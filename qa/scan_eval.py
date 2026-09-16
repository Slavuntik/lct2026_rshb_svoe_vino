#!/usr/bin/env python3
"""qa/scan_eval.py — eval-раннер сканера кейса ЛЦТ (агент F, см. agents/F-qa-demo.md).

Кейс (case.md) и контракт (contracts/image-scan.md, v0.4, заморожен) описывают конвейер
«фото этикетки → одна карточка вина» и обязывают: (а) достоверность на контрольной выборке
90-100% совпадений — фундамент оценки (50 из 100 баллов кейса); (б) метрика F1 топ-1/топ-5,
виден отрыв лидера от конкурентов; (в) SLA ответа ≤3с; (г) совместимость со скриптом
кейсодержателя — он шлёт фото по одному и ждёт ПЛОСКИЙ `{"slug": "wine-slug"}`.

Этот раннер меряет то же самое локально, ДО приезда датасета кейса и реального скрипта
оценки (оба — «завтра», см. contracts/image-scan.md, CASE_DATA_DIR): вход — каталог фото с
разметкой true-slug (CSV или терпимый разбор имени файла), выход — match-rate, F1 топ-1/топ-5,
p50/p95 времени ответа, отчёт JSON (машине, тот же вид полей, что и `GET /v1/metrics/scan`
контракта) + Markdown (человеку).

Режимы (`--mode`):
  flat  — POST /v1/scan/photo?flat=1 против живого apps/api — РОВНО то, что делает скрипт
          кейсодержателя (case.md, п.6): один slug на фото, ничего больше. Основной режим
          для match-rate/SLA — критерий 90-100% и ≤3с считаются кейсодержателем именно так.
  rich  — POST /v1/scan/photo (без flat) — полный ответ с `confidence`, `matches` и т.д.
          Топ-5 — из официального поля `matches: [{slug, score}]` (contracts/image-scan.md
          v0.4.3, apps/api `a2bc591`; пробел, который раньше был здесь задокументирован,
          закрыт по этому же предложению). Заполняется независимо от confident/not_in_catalog
          — честный топ-5 кандидатов не зависит от решения порога. Фолбэк на `similar` (старое
          поведение) — только если `matches` нет вовсе (API старее v0.4.3) или пуст; раннер
          честно помечает деградацию предупреждением, не молчит.
  mock  — без сети: `MockPredictor` в процессе (по умолчанию — «идеальный оракул», предсказывает
          true_slug; конкретные фото можно переопределить `--mock-map`, включая намеренно
          неверные ответы — для прогона метрик на управляемых кейсах и для pytest).

Сплит dev/holdout (`--split {dev,holdout,all}`, `--seed`, `--holdout-frac`) — ПРЕДОХРАНИТЕЛЬ
от переобучения на публичном датасете кейса: holdout не должен участвовать в подборе настроек
(нормализация, порог уверенности, веса ракурсов и т.п.) — только в финальной проверке перед
сдачей. Разбиение — по стабильному хэшу `photo_id` (не перемешивание списка): если каталог
дорастёт новыми фото (case.md: ~50 позиций/день), УЖЕ размеченные фото не поменяют сплит.

Коды возврата: 0 — прогон состоялся, отчёт записан (метрики могут быть любыми — это измерение,
не проверка на соответствие, если явно не заданы `--fail-under-*`/`--fail-over-*`);
2 — ошибка использования (каталог не найден, пустой eval-сет после фильтра сплита и т.п.);
1 — прогон состоялся, но нарушен явно заданный порог (`--fail-under-match-rate`,
`--fail-over-p95-ms`) — для будущего использования в CI/Makefile-гейте, по умолчанию не задан.

Использование:
    python scan_eval.py --photos-dir case-data/public --mode flat --split holdout
    python scan_eval.py --photos-dir qa/tests/fixtures/scan_mini --mode mock --split all
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import secrets
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlsplit

GENERATOR_NAME = "svoy-somelye-scan-eval"
GENERATOR_VERSION = "0.1.0"  # интерим до приезда датасета/скрипта кейса — см. докстринг модуля

_SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = _SCRIPT_DIR / "scan-eval-runs"

DEFAULT_API_URL = "http://localhost:8000"
SCAN_PHOTO_PATH = "/v1/scan/photo"  # contracts/image-scan.md
IMAGE_FIELD_NAME = "image"          # contracts/image-scan.md: "multipart: image"

DEFAULT_TOP_K = 5
DEFAULT_SEED = 1337
DEFAULT_HOLDOUT_FRAC = 0.2
DEFAULT_TIMEOUT_S = 10.0

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Алиасы заголовков CSV-разметки — "формат загрузчика сделай терпимым" (задача оркестратора).
_FILENAME_HEADER_ALIASES = {"filename", "file", "photo", "photo_id", "image", "img"}
_SLUG_HEADER_ALIASES = {"slug", "wine_slug", "true_slug", "label", "answer"}


# --------------------------------------------------------------------------------------
# Загрузка eval-сета: каталог фото + разметка (CSV или терпимый разбор имени файла)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalItem:
    photo_id: str  # имя файла (уникально в пределах каталога) — ключ сплита и mock-карты
    path: Path
    true_slug: str


def infer_slug_from_filename(filename: str) -> str:
    """slug по умолчанию = имя файла без расширения; `__` режет доп.суффикс (несколько
    фото на один slug, например `abrau-...-105__blik.jpg` -> `abrau-...-105`). Слаги сами
    состоят из дефисов (см. каталог), поэтому делить по первому дефису нельзя — только по
    выделенному разделителю `__`, который в реальных slug'ах не встречается.
    """
    stem = Path(filename).stem
    return stem.split("__", 1)[0]


def _resolve_csv_columns(fieldnames: Iterable[str]) -> tuple[str, str]:
    lowered = {name.strip().lower(): name for name in fieldnames if name}
    filename_col = next((lowered[a] for a in _FILENAME_HEADER_ALIASES if a in lowered), None)
    slug_col = next((lowered[a] for a in _SLUG_HEADER_ALIASES if a in lowered), None)
    if filename_col is None or slug_col is None:
        raise ValueError(
            f"CSV-разметка: не нашёл колонку имени файла и/или slug среди {sorted(lowered)} — "
            f"ожидаю один из {sorted(_FILENAME_HEADER_ALIASES)} и один из {sorted(_SLUG_HEADER_ALIASES)}"
        )
    return filename_col, slug_col


def load_eval_set(
    photos_dir: Path,
    labels_csv: Path | None = None,
) -> tuple[list[EvalItem], list[str]]:
    """Терпимый загрузчик: CSV-разметка, если есть (явно передана или auto `labels.csv` в
    `photos_dir`), иначе — slug выводится из имени файла (`infer_slug_from_filename`).

    Возвращает (items, warnings) — предупреждения (файл из CSV не найден на диске, файл на
    диске не упомянут в CSV, файл без разметки и т.п.) не валят загрузку, просто исключают
    конкретный файл и остаются в отчёте, а не проглатываются молча.
    """
    warnings: list[str] = []
    if not photos_dir.is_dir():
        raise FileNotFoundError(f"каталог фото не найден: {photos_dir}")

    files_on_disk = sorted(
        p for p in photos_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    by_name = {p.name: p for p in files_on_disk}

    csv_path = labels_csv or (photos_dir / "labels.csv" if (photos_dir / "labels.csv").is_file() else None)

    items: list[EvalItem] = []
    if csv_path is not None:
        if not csv_path.is_file():
            raise FileNotFoundError(f"labels-csv не найден: {csv_path}")
        with csv_path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError(f"CSV-разметка пуста: {csv_path}")
            filename_col, slug_col = _resolve_csv_columns(reader.fieldnames)
            seen_names: set[str] = set()
            for row in reader:
                name = (row.get(filename_col) or "").strip()
                slug = (row.get(slug_col) or "").strip()
                if not name or not slug:
                    warnings.append(f"CSV: пропущена строка без имени файла или slug: {row!r}")
                    continue
                seen_names.add(name)
                path = by_name.get(name)
                if path is None:
                    warnings.append(f"CSV: файл '{name}' размечен, но не найден в {photos_dir}")
                    continue
                items.append(EvalItem(photo_id=name, path=path, true_slug=slug))
        unlisted = sorted(set(by_name) - seen_names)
        for name in unlisted:
            warnings.append(f"CSV: файл '{name}' есть в каталоге, но не размечен в {csv_path.name} — пропущен")
    else:
        for path in files_on_disk:
            slug = infer_slug_from_filename(path.name)
            if not slug:
                warnings.append(f"не смог вывести slug из имени файла: {path.name} — пропущен")
                continue
            items.append(EvalItem(photo_id=path.name, path=path, true_slug=slug))
        if not files_on_disk:
            warnings.append(f"в {photos_dir} не найдено изображений ({sorted(IMAGE_EXTENSIONS)})")

    items.sort(key=lambda it: it.photo_id)  # детерминированный порядок — вход сплита и отчёта
    return items, warnings


# --------------------------------------------------------------------------------------
# Сплит dev/holdout — стабильный хэш photo_id, не перемешивание списка (устойчив к росту каталога)
# --------------------------------------------------------------------------------------


def assign_split(photo_id: str, seed: int, holdout_frac: float) -> str:
    """"dev" | "holdout", детерминированно по (seed, photo_id). Хэш, а не shuffle+slice: при
    добавлении новых фото в каталог (case.md — рост ~50/день) уже размеченные фото НЕ меняют
    сплит — иначе предохранитель от переобучения тёк бы при каждом новом фото.
    """
    if not 0.0 <= holdout_frac <= 1.0:
        raise ValueError(f"holdout_frac должен быть в [0, 1], получено {holdout_frac}")
    digest = hashlib.sha256(f"{seed}:{photo_id}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF  # -> [0, 1)
    return "holdout" if bucket < holdout_frac else "dev"


def filter_by_split(
    items: list[EvalItem], split: str, seed: int, holdout_frac: float
) -> list[EvalItem]:
    if split == "all":
        return items
    if split not in ("dev", "holdout"):
        raise ValueError(f"неизвестный split: {split!r} (ожидаю dev|holdout|all)")
    return [it for it in items if assign_split(it.photo_id, seed, holdout_frac) == split]


# --------------------------------------------------------------------------------------
# Предикторы: живой API (flat/rich) и mock (в процессе, без сети)
# --------------------------------------------------------------------------------------


@dataclass
class Prediction:
    top1_slug: str | None
    top5_slugs: list[str]
    latency_ms: float
    top1_score: float | None = None
    gap: float | None = None
    degraded_top5: bool = False  # rich-режим: нет поля 'matches' (API < v0.4.3) и 'similar' пуст
    error: str | None = None


class Predictor(Protocol):
    def predict(self, image_bytes: bytes, filename: str) -> Prediction: ...


def _build_multipart(field_name: str, filename: str, content: bytes) -> tuple[bytes, str]:
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = f"----svoysomelyeScanEval{secrets.token_hex(12)}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return body, boundary


def _post_photo(api_url: str, path_and_query: str, filename: str, content: bytes, timeout: float) -> tuple[int, bytes, float]:
    body, boundary = _build_multipart(IMAGE_FIELD_NAME, filename, content)
    req = urllib.request.Request(api_url.rstrip("/") + path_and_query, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — свой хост, локальный прогон
            status = resp.status
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read()
    elapsed_ms = (time.perf_counter() - start) * 1000
    return status, payload, elapsed_ms


class FlatApiPredictor:
    """POST /v1/scan/photo?flat=1 — ровно поведение скрипта кейсодержателя (case.md, п.6):
    один плоский `{"slug": "..."}` на фото. НЕ несёт top-5 (по контракту flat ничего, кроме
    slug, не отдаёт) — top5_slugs всегда однослотовый `[slug]` (или `[]`, если slug пуст).
    """

    def __init__(self, api_url: str = DEFAULT_API_URL, timeout: float = DEFAULT_TIMEOUT_S):
        self.api_url = api_url
        self.timeout = timeout

    def predict(self, image_bytes: bytes, filename: str) -> Prediction:
        try:
            status, payload, elapsed_ms = _post_photo(
                self.api_url, f"{SCAN_PHOTO_PATH}?flat=1", filename, image_bytes, self.timeout
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return Prediction(top1_slug=None, top5_slugs=[], latency_ms=0.0, error=str(exc))
        if status != 200:
            return Prediction(
                top1_slug=None, top5_slugs=[], latency_ms=elapsed_ms, error=f"HTTP {status}: {payload[:200]!r}"
            )
        try:
            data = json.loads(payload)
            slug = data.get("slug")
        except (json.JSONDecodeError, AttributeError) as exc:
            return Prediction(top1_slug=None, top5_slugs=[], latency_ms=elapsed_ms, error=f"flat-ответ не парсится: {exc}")
        if slug is not None and not isinstance(slug, str):
            return Prediction(top1_slug=None, top5_slugs=[], latency_ms=elapsed_ms, error=f"flat-ответ: поле slug не строка: {data!r}")
        if not slug:
            # ВАЖНО (найдено живой проверкой против настоящего apps/api — B, contracts/
            # image-scan.md): {"slug": ""} — ВАЛИДНЫЙ ответ "нет уверенного матча", а не
            # сбой транспорта. Контракт прямо это требует: "flat ВСЕГДА отдаёт лучший
            # доступный slug" — при полном отсутствии кандидатов лучший доступныйslug
            # пуст, это осмысленный, честный ответ. top1_slug=None здесь — обычный промах
            # в счёте match-rate/F1 (compute_metrics), НЕ error/исключение из знаменателя —
            # раньше это ошибочно считалось error, что тихо убирало "честно не нашли" из
            # метрик (систематически завышая match_rate).
            return Prediction(top1_slug=None, top5_slugs=[], latency_ms=elapsed_ms)
        return Prediction(top1_slug=slug, top5_slugs=[slug], latency_ms=elapsed_ms)


def _extract_matches_top5(data: dict[str, Any]) -> list[str]:
    """contracts/image-scan.md v0.4.3 (apps/api commit `a2bc591`, пробел нашёл F, задание
    оркестратора `ba0ab1e`): `matches: [{slug, score}]` — top-5 схлопнутых ANN-позиций по
    убыванию, заполняется НЕЗАВИСИМО от confident/not_in_catalog решения. Официальный
    источник top-5 для eval — не костыль из `similar` (тот семантически про ветку "не в
    каталоге", см. `_RichApiPredictorFallback` ниже). Терпим к мусору внутри списка (не
    роняем весь прогон из-за одного кривого элемента) — сохраняем порядок, дедуп, срез до
    `DEFAULT_TOP_K`."""
    raw = data.get("matches")
    if not isinstance(raw, list):
        return []
    slugs = [item["slug"] for item in raw if isinstance(item, dict) and isinstance(item.get("slug"), str) and item["slug"]]
    seen: set[str] = set()
    deduped = []
    for s in slugs:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped[:DEFAULT_TOP_K]


class RichApiPredictor:
    """POST /v1/scan/photo (rich) — топ-5 берётся из ОФИЦИАЛЬНОГО поля `matches:
    [{slug, score}]` (contracts/image-scan.md v0.4.3, пробел нашёл этот же раннер в baseline
    F2 — без него F1-top5 против живого API вырождался в F1-top1; B закрыл в `a2bc591`).
    `matches` заполняется НЕЗАВИСИМО от того, confident ответ или not_in_catalog — честный
    top-5 кандидатов измеряет КАЧЕСТВО РАНЖИРОВАНИЯ отдельно от калибровки порога confident/
    not_in_catalog (полезно как раз для калибровки этого порога, см. qa/acceptance.md,
    «Порядок дня датасета»). Если `matches` отсутствует или пуст (API старее v0.4.3, или сам
    ImageIndex не нашёл вовсе ничего) — деградация на старый источник (`similar`, семантически
    про ветку "не в каталоге" — костыль, не полноценный топ-5), `degraded_top5=True`
    подсвечивает это в отчёте, не молчит.
    """

    def __init__(self, api_url: str = DEFAULT_API_URL, timeout: float = DEFAULT_TIMEOUT_S):
        self.api_url = api_url
        self.timeout = timeout

    def predict(self, image_bytes: bytes, filename: str) -> Prediction:
        try:
            status, payload, elapsed_ms = _post_photo(self.api_url, SCAN_PHOTO_PATH, filename, image_bytes, self.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return Prediction(top1_slug=None, top5_slugs=[], latency_ms=0.0, error=str(exc))
        if status != 200:
            return Prediction(
                top1_slug=None, top5_slugs=[], latency_ms=elapsed_ms, error=f"HTTP {status}: {payload[:200]!r}"
            )
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            return Prediction(top1_slug=None, top5_slugs=[], latency_ms=elapsed_ms, error=f"rich-ответ не парсится: {exc}")
        slug = data.get("slug")
        if slug is not None and not isinstance(slug, str):
            return Prediction(top1_slug=None, top5_slugs=[], latency_ms=elapsed_ms, error=f"rich-ответ: поле slug неожиданного типа: {data!r}")
        confidence = data.get("confidence") or {}
        top5_from_matches = _extract_matches_top5(data)

        if not slug:
            # Как и в FlatApiPredictor (см. его комментарий) — slug=null/"" с
            # not_in_catalog=true валиден по контракту ("вина нет в каталоге -> похожие/
            # аналоги либо честное 'не найдено'", case.md, оценивается ОСОБО высоко, не
            # штрафуется) — не error. top1_slug=None корректно засчитается как промах в
            # match-rate top-1. top5_slugs — из `matches`, если API его прислал (v0.4.3+):
            # not_in_catalog НЕ обнуляет candidate-пул ANN, интересно знать, был ли
            # true_slug где-то в топ-5, даже когда порог confident решил перестраховаться —
            # это ровно то, что нужно для калибровки CV_CONFIDENT_SCORE_THRESHOLD.
            return Prediction(
                top1_slug=None, top5_slugs=top5_from_matches, latency_ms=elapsed_ms,
                top1_score=confidence.get("top1_score"), gap=confidence.get("gap"),
                degraded_top5=not top5_from_matches,
            )

        if top5_from_matches:
            return Prediction(
                top1_slug=slug, top5_slugs=top5_from_matches, latency_ms=elapsed_ms,
                top1_score=confidence.get("top1_score"), gap=confidence.get("gap"),
                degraded_top5=False,
            )

        # Фолбэк — API старее v0.4.3 (нет поля `matches`) или оно пусто: старое поведение из
        # `similar` (apps/api/app/schemas.py::AnalogsWineItem — идентификатор `wine_id`, не
        # `slug`, найдено живой проверкой против настоящего apps/api, не предположением).
        similar = data.get("similar") or []
        extra_slugs = [s.get("wine_id") for s in similar if isinstance(s, dict) and isinstance(s.get("wine_id"), str)]
        top5 = [slug] + [s for s in extra_slugs if s and s != slug]
        seen: set[str] = set()
        deduped = []
        for s in top5:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        deduped = deduped[:DEFAULT_TOP_K]
        return Prediction(
            top1_slug=slug,
            top5_slugs=deduped,
            latency_ms=elapsed_ms,
            top1_score=confidence.get("top1_score"),
            gap=confidence.get("gap"),
            degraded_top5=(len(deduped) == 1),
        )


class MockPredictor:
    """Без сети — для dry-run и pytest. По умолчанию «идеальный оракул»: возвращает
    true_slug top-1 (нужен для честного end-to-end прогона механики раннера/метрик без
    зависимости от ещё не написанного CV-ядра). Конкретные photo_id можно переопределить
    через `overrides` — в т.ч. намеренно неверными ответами, для управляемой проверки
    метрик (см. --mock-map в CLI и test_scan_eval.py).
    """

    def __init__(
        self,
        overrides: dict[str, list[str] | str] | None = None,
        latency_ms: float = 50.0,
        default_perfect: bool = True,
        rng_seed: int = DEFAULT_SEED,
    ):
        self.overrides: dict[str, list[str]] = {}
        for photo_id, value in (overrides or {}).items():
            self.overrides[photo_id] = [value] if isinstance(value, str) else list(value)
        self.latency_ms = latency_ms
        self.default_perfect = default_perfect
        self._true_slug_by_photo_id: dict[str, str] = {}

    def bind_true_slugs(self, items: list[EvalItem]) -> None:
        """Нужно для default_perfect — «оракул» подсматривает правильный ответ намеренно
        (это MOCK для рехёрсала механики, не для измерения качества модели, которой ещё нет).
        """
        self._true_slug_by_photo_id = {it.photo_id: it.true_slug for it in items}

    def predict(self, image_bytes: bytes, filename: str) -> Prediction:  # noqa: ARG002 — сигнатура Predictor
        top5 = self.overrides.get(filename)
        if top5 is None:
            if self.default_perfect and filename in self._true_slug_by_photo_id:
                top5 = [self._true_slug_by_photo_id[filename]]
            else:
                top5 = ["__mock_unknown__"]
        return Prediction(top1_slug=top5[0] if top5 else None, top5_slugs=top5, latency_ms=self.latency_ms)


# --------------------------------------------------------------------------------------
# Прогон + метрики
# --------------------------------------------------------------------------------------


@dataclass
class RunRecord:
    photo_id: str
    true_slug: str
    top1_slug: str | None
    top5_slugs: list[str]
    latency_ms: float
    top1_score: float | None = None
    gap: float | None = None
    degraded_top5: bool = False
    error: str | None = None

    @property
    def hit_top1(self) -> bool:
        return self.error is None and self.top1_slug == self.true_slug

    @property
    def hit_topk(self) -> bool:
        return self.error is None and self.true_slug in self.top5_slugs


def run_eval(items: list[EvalItem], predictor: Predictor) -> list[RunRecord]:
    records: list[RunRecord] = []
    for item in items:
        image_bytes = item.path.read_bytes()
        pred = predictor.predict(image_bytes, item.photo_id)
        records.append(
            RunRecord(
                photo_id=item.photo_id,
                true_slug=item.true_slug,
                top1_slug=pred.top1_slug,
                top5_slugs=pred.top5_slugs,
                latency_ms=pred.latency_ms,
                top1_score=pred.top1_score,
                gap=pred.gap,
                degraded_top5=pred.degraded_top5,
                error=pred.error,
            )
        )
    return records


def _percentile(values: list[float], pct: float) -> float:
    """Линейная интерполяция между соседними рангами (как numpy.percentile по умолчанию,
    default 'linear') — без зависимости от numpy (её нет в qa/requirements.txt)."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100)
    f, c = int(k // 1), int(-(-k // 1))  # floor, ceil без math.floor/ceil ради читаемости знаков
    if f == c:
        return s[f]
    d0 = s[f] * (c - k)
    d1 = s[c] * (k - f)
    return d0 + d1


def _macro_f1(true_slugs: list[str], hit_check: Callable[[int, str], bool], predicted_positive: Callable[[int, str], bool]) -> float:
    """Макро-F1, класс = только slug'и, реально встретившиеся как true_slug в eval-сете
    (см. докстринг ниже про near-duplicates) — НЕ произвольные slug'и, засветившиеся в
    top-5 (иначе средняя тонет в шуме дистракторов, никогда не бывших правильным ответом).

    Для класса s (индексы i — все записи):
      TP(s) = |{i: true[i]==s и hit_check(i, s)}|
      predicted_positive(i, s) — «запись i засчитывает slug s как один из своих
        предсказаний» (top1==s для топ-1; s∈top5 для топ-5)
      FP(s) = |{i: predicted_positive(i, s)}| − TP(s)
      FN(s) = |{i: true[i]==s}| − TP(s)
      precision/recall/f1(s) — 0, если знаменатель 0 (класс никогда не предсказан и т.п.)
    Итог — среднее f1(s) по классам. Эта конкретная формула — наша интерпретация "F1" кейса
    (в ТЗ формула не дана, только цель "виден отрыв лидера"); при получении реального скрипта
    оценки — сверить и при расхождении задокументировать/заменить (см. докстринг модуля).
    """
    classes = sorted(set(true_slugs))
    if not classes:
        return 0.0
    f1s = []
    n = len(true_slugs)
    for s in classes:
        tp = sum(1 for i in range(n) if true_slugs[i] == s and hit_check(i, s))
        pred_pos = sum(1 for i in range(n) if predicted_positive(i, s))
        fp = pred_pos - tp
        fn = sum(1 for i in range(n) if true_slugs[i] == s) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1s.append(f1)
    return sum(f1s) / len(f1s)


def compute_metrics(records: list[RunRecord]) -> dict[str, Any]:
    """Метрики только по записям без ошибки транспорта/парсинга (`error is None`) — ошибочные
    запросы считаются отдельно (`n_errors`/`error_rate`), не портят точность/F1/задержку
    статистику "ответивших", но и не должны быть невидимы — error_rate идёт в отчёт отдельной
    строкой и предупреждением при error_rate > 0.
    """
    n_total = len(records)
    ok = [r for r in records if r.error is None]
    n = len(ok)
    n_errors = n_total - n

    if n == 0:
        return {
            "n_total": n_total,
            "n": 0,
            "n_errors": n_errors,
            "error_rate": 1.0 if n_total else 0.0,
            "match_rate": 0.0,
            "match_rate_top5": 0.0,
            "f1_top1": 0.0,
            "f1_top5": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "mean_ms": 0.0,
            "mean_top1_score": None,
            "mean_gap": None,
        }

    true_slugs = [r.true_slug for r in ok]
    hits_top1 = sum(1 for r in ok if r.hit_top1)
    hits_top5 = sum(1 for r in ok if r.hit_topk)

    f1_top1 = _macro_f1(
        true_slugs,
        hit_check=lambda i, s: ok[i].top1_slug == s,
        predicted_positive=lambda i, s: ok[i].top1_slug == s,
    )
    f1_top5 = _macro_f1(
        true_slugs,
        hit_check=lambda i, s: s in ok[i].top5_slugs,
        predicted_positive=lambda i, s: s in ok[i].top5_slugs,
    )

    latencies = [r.latency_ms for r in ok]
    scores = [r.top1_score for r in ok if r.top1_score is not None]
    gaps = [r.gap for r in ok if r.gap is not None]

    return {
        "n_total": n_total,
        "n": n,
        "n_errors": n_errors,
        "error_rate": n_errors / n_total if n_total else 0.0,
        "match_rate": hits_top1 / n,
        "match_rate_top5": hits_top5 / n,
        "f1_top1": f1_top1,
        "f1_top5": f1_top5,
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "mean_ms": statistics.fmean(latencies),
        "mean_top1_score": statistics.fmean(scores) if scores else None,
        "mean_gap": statistics.fmean(gaps) if gaps else None,
    }


def top_confusions(records: list[RunRecord], limit: int = 10) -> list[tuple[str, str, int]]:
    """(true_slug, predicted_top1_slug, count) для промахов топ-1, самые частые сначала —
    диагностика near-duplicates (case.md: "главный источник ошибок"), не автоматическая
    их детекция (slug сам по себе не гарантирует признак near-dup группы)."""
    counts: dict[tuple[str, str], int] = {}
    for r in records:
        if r.error is None and not r.hit_top1 and r.top1_slug is not None:
            key = (r.true_slug, r.top1_slug)
            counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(true_s, pred_s, cnt) for (true_s, pred_s), cnt in ranked[:limit]]


# --------------------------------------------------------------------------------------
# Отчёт: JSON (машине, поля — как GET /v1/metrics/scan контракта, где применимо) + Markdown
# --------------------------------------------------------------------------------------


def build_report(
    *,
    mode: str,
    api_url: str | None,
    split: str,
    seed: int,
    holdout_frac: float,
    photos_dir: Path,
    items: list[EvalItem],
    records: list[RunRecord],
    load_warnings: list[str],
    run_warnings: list[str],
    index_version: str | None = None,
) -> dict[str, Any]:
    metrics = compute_metrics(records)
    return {
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "api_url": api_url,
        "index_version": index_version,
        "split": split,
        "seed": seed,
        "holdout_frac": holdout_frac,
        "photos_dir": str(photos_dir),
        "n_items_loaded": len(items),
        "metrics": metrics,
        "top_confusions": [{"true_slug": t, "predicted_slug": p, "count": c} for t, p, c in top_confusions(records)],
        "warnings": [*load_warnings, *run_warnings],
        "records": [asdict(r) for r in records],
    }


def render_report_md(report: dict[str, Any]) -> str:
    m = report["metrics"]
    lines = [
        "# Отчёт scan_eval — кейс-сканер ЛЦТ",
        "",
        f"Сгенерировано: {report['generated_at']} · режим: `{report['mode']}`"
        + (f" · API: `{report['api_url']}`" if report.get("api_url") else ""),
        f"Сплит: `{report['split']}` (seed={report['seed']}, holdout_frac={report['holdout_frac']}) · "
        f"каталог: `{report['photos_dir']}` · фото загружено: {report['n_items_loaded']}",
        "",
        "## Метрики",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| match-rate (top-1) | {m['match_rate']:.4f} ({m['match_rate']*100:.1f}%) |",
        f"| match-rate (top-5) | {m['match_rate_top5']:.4f} ({m['match_rate_top5']*100:.1f}%) |",
        f"| F1 top-1 (macro) | {m['f1_top1']:.4f} |",
        f"| F1 top-5 (macro) | {m['f1_top5']:.4f} |",
        f"| p50 времени ответа | {m['p50_ms']:.0f} мс |",
        f"| p95 времени ответа | {m['p95_ms']:.0f} мс |",
        f"| среднее время ответа | {m['mean_ms']:.0f} мс |",
        f"| n (успешных ответов) | {m['n']} из {m['n_total']} |",
        f"| error-rate (нет валидного ответа) | {m['error_rate']*100:.1f}% |",
    ]
    if m.get("mean_top1_score") is not None:
        lines.append(f"| средний top1_score (rich) | {m['mean_top1_score']:.4f} |")
    if m.get("mean_gap") is not None:
        lines.append(f"| средний gap (rich) | {m['mean_gap']:.4f} |")
    lines += [
        "",
        f"Критерии кейса (case.md): match-rate top-1 90-100%, SLA p95 ≤3000 мс. "
        f"Сейчас: match-rate={m['match_rate']*100:.1f}%, p95={m['p95_ms']:.0f} мс "
        f"({'в рамках SLA' if m['p95_ms'] <= 3000 else 'SLA НАРУШЕН'}).",
        "",
    ]
    if report["warnings"]:
        lines.append("## Предупреждения")
        lines.append("")
        for w in report["warnings"]:
            lines.append(f"- {w}")
        lines.append("")
    if report["top_confusions"]:
        lines.append("## Топ промахов top-1 (диагностика near-duplicates, case.md)")
        lines.append("")
        lines.append("| true_slug | предсказано | раз |")
        lines.append("|---|---|---|")
        for c in report["top_confusions"]:
            lines.append(f"| {c['true_slug']} | {c['predicted_slug']} | {c['count']} |")
        lines.append("")
    lines.append(
        "Интерпретация F1 — macro-F1 по slug'ам, реально встретившимся как true_slug в этом "
        "eval-сете (см. `scan_eval.py::_macro_f1`); ТЗ кейса формулу не даёт, только цель "
        "«виден отрыв лидера от конкурентов» — сверить с реальным скриптом оценки по приезду "
        "(qa/mock_case_script.sh — рехёрсал их описанного поведения, не их код)."
    )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_report_md(report), encoding="utf-8")
    return json_path, md_path


def _fetch_index_version(api_url: str, timeout: float) -> str | None:
    """GET /v1/metrics/scan, не /v1/healthz — найдено живой проверкой: /v1/healthz.index_version
    называет версию RAG/текстового индекса (agents/A-rag.md), а не CV/image-индекса — две
    разные подсистемы делят одно имя поля. /v1/metrics/scan правильно резолвит именно CV-версию
    (`apps/api/app/routers/metrics.py`: `image_index.index_version` или settings-дефолт) даже
    ДО первого eval-прогона, когда файла отчёта ещё нет. Лучшее усилие: недоступность/
    некорректный ответ -> None, не ошибка прогона."""
    try:
        req = urllib.request.Request(api_url.rstrip("/") + "/v1/metrics/scan", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — свой хост, локальный прогон
            data = json.loads(resp.read())
        version = data.get("index_version")
        return version if isinstance(version, str) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, AttributeError):
        return None


def eval_report_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    """Ровно схема, которую `apps/api/app/cv/eval_report.py::read_eval_report` ожидает по
    пути `CV_EVAL_REPORT_PATH` — общий источник для `GET /v1/metrics/scan` и
    `confidence.f1_*` rich-режима `/scan/photo` (contracts/image-scan.md: "F1-цифры — с
    последнего eval-прогона"). Docstring B (apps/api/app/cv/eval_report.py): `{index_version,
    f1_top1, f1_top5, match_rate, eval_set, measured_at}` — этот файл сегодня пишет `cv bench`/
    `cv eval` агента G (когда появится); scan_eval.py может писать ЭТОТ ЖЕ формат уже сейчас
    (`--write-eval-report`), потому что метрики совпадают дословно — единственный источник
    цифр, не две расходящиеся копии, пока у G нет своего писателя."""
    m = report["metrics"]
    return {
        "index_version": report.get("index_version"),
        "f1_top1": m["f1_top1"],
        "f1_top5": m["f1_top5"],
        "match_rate": m["match_rate"],
        "eval_set": f"{report['photos_dir']} ({report['split']}, n={m['n']})",
        "measured_at": report["generated_at"],
    }


def write_eval_report_snapshot(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(eval_report_snapshot(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eval-раннер сканера кейса ЛЦТ")
    parser.add_argument("--photos-dir", required=True, type=Path, help="каталог фото с разметкой")
    parser.add_argument("--labels-csv", type=Path, default=None, help="CSV-разметка (по умолчанию — <photos-dir>/labels.csv, иначе имя файла)")
    parser.add_argument("--mode", choices=["flat", "rich", "mock"], required=True)
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="для --mode flat|rich")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--mock-map", type=Path, default=None, help="JSON {photo_id: slug|[slug,...]} — переопределения MockPredictor")
    parser.add_argument("--mock-latency-ms", type=float, default=50.0)
    parser.add_argument("--mock-imperfect", action="store_true", help="без --mock-map по умолчанию НЕ подсматривать true_slug (для честного 'пустого' рехёрсала)")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--holdout-frac", type=float, default=DEFAULT_HOLDOUT_FRAC)
    parser.add_argument("--limit", type=int, default=None, help="ограничить число фото (быстрый смоук)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--fail-under-match-rate", type=float, default=None)
    parser.add_argument("--fail-over-p95-ms", type=float, default=None)
    parser.add_argument(
        "--write-eval-report", type=Path, default=None,
        help="дополнительно записать снимок метрик в схеме apps/api/app/cv/eval_report.py "
        "(CV_EVAL_REPORT_PATH) — GET /v1/metrics/scan и confidence.f1_* rich-режима смогут "
        "прочитать этот файл напрямую",
    )
    return parser


def run(argv: list[str]) -> int:
    args = _build_arg_parser().parse_args(argv)

    try:
        items, load_warnings = load_eval_set(args.photos_dir, args.labels_csv)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[scan_eval] ОШИБКА загрузки: {exc}", file=sys.stderr)
        return 2

    items = filter_by_split(items, args.split, args.seed, args.holdout_frac)
    if args.limit is not None:
        items = items[: args.limit]

    if not items:
        print(f"[scan_eval] ОШИБКА: пуст eval-сет после загрузки/фильтра сплита ({args.split})", file=sys.stderr)
        return 2

    if args.split == "holdout":
        print(
            "[scan_eval] ВНИМАНИЕ: сплит holdout — не использовать этот прогон для подбора "
            "настроек (нормализация, пороги, веса ракурсов); только финальная проверка.",
            file=sys.stderr,
        )

    run_warnings: list[str] = []
    if args.mode == "mock":
        overrides = {}
        if args.mock_map:
            overrides = json.loads(args.mock_map.read_text(encoding="utf-8"))
        predictor: Predictor = MockPredictor(
            overrides=overrides,
            latency_ms=args.mock_latency_ms,
            default_perfect=not args.mock_imperfect,
        )
        if isinstance(predictor, MockPredictor):
            predictor.bind_true_slugs(items)
        api_url_for_report = None
        index_version = None
    else:
        predictor = FlatApiPredictor(args.api_url, args.timeout) if args.mode == "flat" else RichApiPredictor(args.api_url, args.timeout)
        api_url_for_report = args.api_url
        index_version = _fetch_index_version(args.api_url, args.timeout)

    records = run_eval(items, predictor)

    n_transport_errors = sum(1 for r in records if r.error is not None)
    if n_transport_errors:
        run_warnings.append(f"{n_transport_errors} из {len(records)} запросов не дали валидный ответ (см. records[].error)")
    if args.mode == "rich":
        n_degraded = sum(1 for r in records if r.degraded_top5)
        if n_degraded:
            run_warnings.append(
                f"rich: у {n_degraded} из {len(records)} ответов нет поля 'matches' (API старее "
                f"v0.4.3) и 'similar' пусто — топ-5 деградировал к топ-1"
            )

    report = build_report(
        mode=args.mode,
        api_url=api_url_for_report,
        split=args.split,
        seed=args.seed,
        holdout_frac=args.holdout_frac,
        photos_dir=args.photos_dir,
        items=items,
        records=records,
        load_warnings=load_warnings,
        run_warnings=run_warnings,
        index_version=index_version,
    )

    json_path, md_path = write_report(report, args.out_dir)
    if args.write_eval_report:
        eval_report_path = write_eval_report_snapshot(report, args.write_eval_report)
        print(f"[scan_eval]           {eval_report_path} (снимок для CV_EVAL_REPORT_PATH)")

    m = report["metrics"]
    print(
        f"[scan_eval] mode={args.mode} split={args.split} n={m['n']}/{m['n_total']} "
        f"match_rate={m['match_rate']:.3f} f1_top1={m['f1_top1']:.3f} f1_top5={m['f1_top5']:.3f} "
        f"p50={m['p50_ms']:.0f}мс p95={m['p95_ms']:.0f}мс"
    )
    print(f"[scan_eval] Записано: {md_path}\n[scan_eval]           {json_path}")
    for w in report["warnings"]:
        print(f"[scan_eval]   WARNING {w}")

    exit_code = 0
    if args.fail_under_match_rate is not None and m["match_rate"] < args.fail_under_match_rate:
        print(f"[scan_eval] ПОРОГ НАРУШЕН: match_rate {m['match_rate']:.3f} < {args.fail_under_match_rate}", file=sys.stderr)
        exit_code = 1
    if args.fail_over_p95_ms is not None and m["p95_ms"] > args.fail_over_p95_ms:
        print(f"[scan_eval] ПОРОГ НАРУШЕН: p95 {m['p95_ms']:.0f}мс > {args.fail_over_p95_ms}", file=sys.stderr)
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
