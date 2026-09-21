"""OCR-верификатор near-dup семей — cv/verify.py (contracts/image-scan.md v0.4.4,
ревью 04, блокер 1: "реальный OCR-верификатор не существует и не имеет владельца —
а кейс проверяет именно его").

Зачем отдельно от ImageIndex: CV-эмбеддинг (SigLIP2) реагирует на общий облик этикетки
(цвет, композиция, иллюстрация) — у near-dup семьи (одна этикетка, разные год/категория/
объём) это визуально ОДНА И ТА ЖЕ картинка, ANN принципиально не может их развести
(cv/index.py — near-dup позиции нарочно попадают в одну "группу" по `gap`). Год/объём/
категория — мелкий печатный текст, который эмбеддинг не разрешает, а OCR — да.

Пайплайн `verify()`: decode (ValueError на битые байты) → `normalize_query()` (та же
нормализация, что видит `ImageIndex.search()` — этикетка уже кадрирована и выпрямлена,
OCR получает не сырое фото целиком, а стабилизированный регион) → PaddleOCR → токены
года/объёма/категории из распознанного текста → сопоставление с `VerifyCandidate.name`/
`vintage` → slug, если ровно один кандидат однозначно совпал, иначе `None`.

`None` — ЗАКОНОМЕРНЫЙ исход (контракт: "воздержался — API оставляет ANN-топ, честная
деградация, не ошибка"), не только на нечитаемом фото, но и когда текст неоднозначен
(два кандидата совпали одинаково хорошо) или кандидаты не несут различающего сигнала.

## Трассировка (TODO-2, ревью 05; agents/G4-family-gap.md)

`CV_VERIFY_DEBUG=1` включает структурированный JSON-лог в stderr на каждый вызов
`verify()`: распознанный OCR-текст, извлечённые токены (год/объём/категория/тип
вина/цвет) по запросу И по каждому кандидату, причина решения/воздержания. Флаг
НЕ меняет поведение/производительность, когда выключен — `verify()` при
`CV_VERIFY_DEBUG` не установлен зовёт ровно `match_candidates()`, как раньше;
трассировка добавляет отдельную (идентичную по арифметике, см. `_score_candidate`)
ветку только при явном включении. Если `verify()` НЕ вызывается вообще (near-dup
routing апстрим в `apps/api` решил не звать OCR) — лога не будет: это тоже
диагностический факт (отсутствие строки `[cv.verify]`), не баг трассировки.

## Тип вина и цвет как различители (найдено TODO-2, q2 — Мускатель Массандра)

Реальный прогон на `case-data/eval/queries/02eef911.webp` показал: OCR корректно
читает "МУСКАТЕЛЬ" и "БЕЛЫЙ" прямо с этикетки, но `_CATEGORY_KEYWORDS` (до этой
правки) знал только уровень сахара (брют/сухое/сладкое...) — ни "мускатель", ни
"мускат", ни "портвейн" не были токенами вообще, так что near-dup соседи Массандры
(Портвейн/Мускат/Мускатель разных цветов) не получали НИКАКОГО категориального
сигнала, при том что OCR текст читает верно. Добавлены два НЕЗАВИСИМЫХ измерения —
`_WINE_TYPE_KEYWORDS` (портвейн/мускат/мускатель/кагор/херес/мадера) и
`_COLOR_KEYWORDS` (белый/красный/чёрный/розовый) — раздельно от старой "категории"
(уровень сахара), иначе "мускатель белый" и "мускатель чёрный" совпадали бы по ОДНОЙ
общей категории "мускатель" и снова получали одинаковый скор (реальная арифметика
задачи — см. reports/g4-family-gap.md, "q2"). Извлечение — по границе слова
(`\\b...\\b`), не по вхождению подстроки: "мускат" НЕ обязан ложно совпадать внутри
"мускатель" (разные, не вложенные категории на реальных этикетках Массандры).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import TypedDict

import cv2
import numpy as np

from cv import imageio
from cv.normalize import normalize_query

# Год — ровно 4 цифры вида 19xx/20xx, не как часть более длинного числа (объём в мл
# вроде "1500" не должен читаться как год; "(?!\d)"/"(?<!\d)" — границы не-цифра).
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

# Объём — частые написания на российских этикетках: "0.75", "0,75", "750", "0.5", "500", "1.5", "1500".
_VOLUME_RE = re.compile(r"(?<!\d)(?:0[.,]75|750|0[.,]5|500|1[.,]5|1500)\s*(?:л|l|мл|ml)?\b", re.IGNORECASE)

# Категории сахара/стиля — ключевые слова рус+лат, сведённые к канонической форме для
# сравнения. Не полный taxonomy.yaml (это справочник пайплайна данных, не моя зона) —
# минимальный набор, достаточный для tie-break внутри near-dup семьи по объявлению кейса.
_CATEGORY_KEYWORDS: dict[str, str] = {
    "брют": "брют",
    "brut": "брют",
    "экстра брют": "экстра брют",
    "extra brut": "экстра брют",
    "сухое": "сухое",
    "сухой": "сухое",
    "dry": "сухое",
    "sec": "сухое",
    "полусухое": "полусухое",
    "demi-sec": "полусухое",
    "полусладкое": "полусладкое",
    "сладкое": "сладкое",
    "sweet": "сладкое",
    "doux": "сладкое",
    "резерв": "резерв",
    "reserve": "резерв",
    "reserva": "резерв",
    "крепленое": "крепленое",
    "креплёное": "крепленое",
}

# Тип вина — НАЙДЕНО TODO-2 (q2, Мускатель Массандра): OCR реально читает "МУСКАТЕЛЬ"
# с этикетки, но до этой правки ни один near-dup сосед Массандры не получал
# категориального сигнала — ни "мускат"/"мускатель"/"портвейн" не было токенами
# ВООБЩЕ. Отдельное от _CATEGORY_KEYWORDS измерение (не один общий "категория" бакет) —
# осознанно: "мускатель белый" и "мускатель чёрный" делят ТИП ("мускатель"), а разница
# только в ЦВЕТЕ (_COLOR_KEYWORDS ниже) — единый бакет дал бы им одинаковый скор,
# смешав два независимых сигнала в один (см. reports/g4-family-gap.md, "q2").
_WINE_TYPE_KEYWORDS: dict[str, str] = {
    "портвейн": "портвейн",
    "portveyn": "портвейн",
    "port": "портвейн",
    "мускатель": "мускатель",
    "мускат": "мускат",
    "muscat": "мускат",
    "кагор": "кагор",
    "херес": "херес",
    "sherry": "херес",
    "мадера": "мадера",
    "madeira": "мадера",
}

# Цвет продукта — второй независимый различитель ("Мускатель БЕЛЫЙ" vs "Мускатель
# ЧЁРНЫЙ" — тот же тип, разный цвет; оба слова читаются OCR прямо с этикетки).
_COLOR_KEYWORDS: dict[str, str] = {
    "белый": "белый",
    "белое": "белый",
    "красный": "красный",
    "красное": "красный",
    "чёрный": "черный",
    "черный": "черный",
    "розовый": "розовый",
    "розовое": "розовый",
}

DEFAULT_SCORE_THRESH = 0.5  # ниже — OCR сам неуверен в строке, в токенизацию не пускаем
DEFAULT_LANG = "ru"  # PaddleOCR: кириллица + латиница (цифры/латинские слова — общий алфавит)
DEFAULT_OCR_SIZE = 320  # даунскейл перед OCR — доминирующий рычаг бюджета 700 мс, см. LabelVerifier.read_text


class VerifyCandidate(TypedDict):
    slug: str
    name: str  # из каталога: несёт год/категорию текстом
    vintage: int | None


def _extract_years(text: str) -> set[int]:
    return {int(m.group(0)) for m in _YEAR_RE.finditer(text)}


def _extract_volumes(text: str) -> set[str]:
    return {m.group(0).strip().rstrip("лlml мМлL").strip() for m in _VOLUME_RE.finditer(text)}


def _keyword_extractor(keywords: dict[str, str]):
    """Компилирует один "найди-любое-из-ключей" regex с границами слова (`\\b`) —
    НЕ вхождение подстроки (`kw in text`, как было в _extract_categories ДО этой
    правки): "мускат" не обязан ложно совпадать внутри "мускатель" только потому,
    что это префикс той же строки символов — на реальных этикетках это два РАЗНЫХ
    типа вина (найдено TODO-2, q2). Ключи отсортированы по убыванию длины, чтобы
    многословные фразы ("экстра брют") были в альтернации раньше своих подстрок."""
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(k) for k in sorted(keywords, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )

    def extract(text_lower: str) -> set[str]:
        return {keywords[m.group(0).lower()] for m in pattern.finditer(text_lower)}

    return extract


_extract_categories = _keyword_extractor(_CATEGORY_KEYWORDS)
_extract_wine_type = _keyword_extractor(_WINE_TYPE_KEYWORDS)
_extract_color = _keyword_extractor(_COLOR_KEYWORDS)


@dataclass(frozen=True)
class _Tokens:
    """Токены, извлечённые из одной строки (запроса OCR ИЛИ candidate.name — одна и
    та же функция `_tokens()` с обеих сторон, чтобы сравнение было симметричным).
    Пять НЕЗАВИСИМЫХ измерений — год/объём/сахарная-категория/тип-вина/цвет — каждое
    даёт свой собственный вклад в скор `_score_candidate()`, ни одно не поглощает
    другое (см. докстринг модуля, "Тип вина и цвет как различители")."""

    years: set[int]
    volumes: set[str]
    categories: set[str]
    wine_types: set[str]
    colors: set[str]


def _tokens(text: str, name: str | None = None) -> _Tokens:
    """Токены года/объёма/категории/типа/цвета из строки (запроса ИЛИ candidate.name)."""
    combined = f"{text} {name}" if name else text
    lower = combined.lower()
    return _Tokens(
        years=_extract_years(combined),
        volumes=_extract_volumes(combined),
        categories=_extract_categories(lower),
        wine_types=_extract_wine_type(lower),
        colors=_extract_color(lower),
    )


def _ocr_has_no_signal(ocr: _Tokens) -> bool:
    return not (ocr.years or ocr.volumes or ocr.categories or ocr.wine_types or ocr.colors)


def _score_candidate(ocr: _Tokens, c: VerifyCandidate) -> tuple[float, dict]:
    """Скор одного кандидата против токенов OCR — общая арифметика для
    `match_candidates()` (решение) и `match_candidates_trace()` (то же решение +
    полная трассировка для CV_VERIFY_DEBUG). ОДНА реализация — трассировка не может
    разойтись с реальным решением просто потому, что была написана отдельно.

    Скоринг: год из vintage — самый надёжный сигнал (+2), год ВНУТРИ name-строки —
    тоже сильный (+2, только если vintage не решил вопрос). Объём/сахарная-категория/
    тип-вина/цвет — по +1 каждый, НЕЗАВИСИМО (см. _Tokens): "мускатель белый" против
    "мускатель чёрный" отличаются именно тем, что первый матчит ДВА измерения (тип
    И цвет) против одного — раздельные бакеты обязательны для этого различения
    (найдено TODO-2, q2 — reports/g4-family-gap.md)."""
    name_tok = _tokens("", c.get("name"))
    vintage = c.get("vintage")

    score = 0.0
    matched_on: list[str] = []
    if vintage is not None and vintage in ocr.years:
        score += 2.0
        matched_on.append(f"vintage:{vintage}")
    elif name_tok.years and (name_tok.years & ocr.years):
        score += 2.0
        matched_on.append(f"name_year:{sorted(name_tok.years & ocr.years)}")
    if name_tok.volumes and (name_tok.volumes & ocr.volumes):
        score += 1.0
        matched_on.append(f"volume:{sorted(name_tok.volumes & ocr.volumes)}")
    if name_tok.categories and (name_tok.categories & ocr.categories):
        score += 1.0
        matched_on.append(f"category:{sorted(name_tok.categories & ocr.categories)}")
    if name_tok.wine_types and (name_tok.wine_types & ocr.wine_types):
        score += 1.0
        matched_on.append(f"wine_type:{sorted(name_tok.wine_types & ocr.wine_types)}")
    if name_tok.colors and (name_tok.colors & ocr.colors):
        score += 1.0
        matched_on.append(f"color:{sorted(name_tok.colors & ocr.colors)}")

    debug = {
        "slug": c["slug"],
        "name": c.get("name"),
        "vintage": vintage,
        "name_tokens": {
            "years": sorted(name_tok.years),
            "volumes": sorted(name_tok.volumes),
            "categories": sorted(name_tok.categories),
            "wine_types": sorted(name_tok.wine_types),
            "colors": sorted(name_tok.colors),
        },
        "score": score,
        "matched_on": matched_on,
    }
    return score, debug


def match_candidates(ocr_text: str, candidates: list[VerifyCandidate]) -> str | None:
    """Чистая функция сопоставления (без OCR) — токены распознанного текста против
    `name`/`vintage` каждого кандидата. Вынесена отдельно от `LabelVerifier.verify()`,
    чтобы логику сопоставления можно было тестировать без реального движка OCR.

    Кандидат побеждает, только если его скор (см. `_score_candidate`) СТРОГО больше
    всех остальных (равенство или нулевой скор -> воздержание — честная неуверенность,
    а не потеря сигнала по невнимательности)."""
    if not candidates:
        return None

    ocr = _tokens(ocr_text)
    if _ocr_has_no_signal(ocr):
        return None  # OCR ничего опознаваемого не нашёл — не из чего сопоставлять

    scored: list[tuple[float, str]] = []
    for c in candidates:
        score, _debug = _score_candidate(ocr, c)
        if score > 0:
            scored.append((score, c["slug"]))

    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # неоднозначно — двое совпали одинаково хорошо
    return scored[0][1]


def match_candidates_trace(ocr_text: str, candidates: list[VerifyCandidate]) -> tuple[str | None, dict]:
    """Как `match_candidates()`, плюс полная структурированная трассировка — ТОЛЬКО
    для `CV_VERIFY_DEBUG=1` (`LabelVerifier.verify()`). Делит `_score_candidate()` с
    `match_candidates()`, так что решение здесь ГАРАНТИРОВАННО совпадает с боевым
    (не отдельная, потенциально расходящаяся копия логики)."""
    trace: dict = {"ocr_tokens": None, "per_candidate": [], "decision": None, "reason": None}
    if not candidates:
        trace["reason"] = "no_candidates"
        return None, trace

    ocr = _tokens(ocr_text)
    trace["ocr_tokens"] = {
        "years": sorted(ocr.years),
        "volumes": sorted(ocr.volumes),
        "categories": sorted(ocr.categories),
        "wine_types": sorted(ocr.wine_types),
        "colors": sorted(ocr.colors),
    }
    if _ocr_has_no_signal(ocr):
        trace["reason"] = "ocr_no_recognizable_tokens"
        return None, trace

    scored: list[tuple[float, str]] = []
    for c in candidates:
        score, debug = _score_candidate(ocr, c)
        trace["per_candidate"].append(debug)
        if score > 0:
            scored.append((score, c["slug"]))

    if not scored:
        trace["reason"] = "no_candidate_scored"
        return None, trace
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        trace["reason"] = "ambiguous_tie"
        trace["tied_slugs"] = [slug for score, slug in scored if score == scored[0][0]]
        return None, trace
    trace["reason"] = "matched"
    trace["decision"] = scored[0][1]
    return scored[0][1], trace


def _verify_debug_enabled() -> bool:
    """Читает `CV_VERIFY_DEBUG` ЖИВЬЁМ при каждом вызове (не замораживает при
    импорте, в отличие от большинства констант `cv.config`) — так тест может
    `monkeypatch.setenv("CV_VERIFY_DEBUG", "1")` прямо перед вызовом `verify()`,
    без манипуляций с уже импортированными модулями."""
    return os.environ.get("CV_VERIFY_DEBUG", "").strip().lower() not in ("", "0", "false", "no")


def _log_verify_trace(*, candidates: list[VerifyCandidate], ocr_text: str | None, trace: dict) -> None:
    payload = {
        "called": True,
        "candidates": [
            {"slug": c["slug"], "name": c.get("name"), "vintage": c.get("vintage")} for c in candidates
        ],
        "ocr_text": ocr_text,
        **trace,
    }
    print("[cv.verify] " + json.dumps(payload, ensure_ascii=False), file=sys.stderr)


class LabelVerifier:
    """PaddleOCR (`lang=`, дефолт "ru" — кириллица; цифры и базовая латиница читаются
    тем же движком) поверх нормализованного региона этикетки запроса.

    Ленивая загрузка модели (как `cv.encoder.SiglipEncoder`) — конструктор и импорт
    модуля не должны тянуть веса/сеть, если верификатор в конкретном запуске не нужен.
    """

    def __init__(
        self,
        lang: str = DEFAULT_LANG,
        score_thresh: float = DEFAULT_SCORE_THRESH,
        ocr_size: int = DEFAULT_OCR_SIZE,
    ):
        self.lang = lang
        self.score_thresh = score_thresh
        # CV_OCR_SIZE — сторона кадра, подаваемого в OCR. Дефолт 320 (замер дев-машины).
        # На слабом CPU это доминирующая статья бюджета: ams3 ставит 256 (2.5 с против
        # 2.9 с при 320, тот же вердикт); 224 уже теряет текст — верификатор воздерживается.
        self.ocr_size = int(os.environ.get("CV_OCR_SIZE", ocr_size))
        self._ocr = None

    def _load(self) -> None:
        if self._ocr is not None:
            return
        from paddleocr import PaddleOCR

        # Детект/развёртка документа и текстовой ориентации нам не нужны — normalize_query()
        # уже кадрировал и выпрямил этикетку; отключение этих стадий — заметная часть бюджета
        # 700 мс (см. reports/g-report.md, замеры).
        # Модели детектора/распознавателя — через env, дефолт оставлен движку (на дев-машине
        # это server-детектор, и он там укладывается в бюджет). На слабом CPU прод-бокса
        # server-детектор стоит секунды: хак-стенд ams3 ставит CV_OCR_DET_MODEL=
        # PP-OCRv5_mobile_det. Имена моделей — те же, что PaddleOCR кэширует в PADDLE_PDX_CACHE_HOME.
        extra: dict[str, str] = {}
        det_model = os.environ.get("CV_OCR_DET_MODEL")
        rec_model = os.environ.get("CV_OCR_REC_MODEL")
        if det_model:
            extra["text_detection_model_name"] = det_model
        if rec_model:
            extra["text_recognition_model_name"] = rec_model
        self._ocr = PaddleOCR(
            lang=self.lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            **extra,
        )

    def read_text(self, image_arr: np.ndarray) -> str:
        """RGB ndarray -> распознанный текст (строки объединены пробелом), только
        куски с confidence >= `score_thresh`. Пустая строка, если OCR ничего не нашёл
        или сам движок упал (деградация, не исключение — см. `verify()`).

        Даунскейл до `ocr_size` ПЕРЕД детекцией — доминирующий рычаг бюджета 700 мс:
        детекция+распознавание PaddleOCR масштабируются с числом пикселей, а нужный
        текст (год/объём/категория) остаётся читаемым и на уменьшенном кадре — замер
        (см. reports/g-report.md): 448px ~800 мс, 320px ~420 мс, тот же текст, тот же
        результат сопоставления."""
        self._load()
        h, w = image_arr.shape[:2]
        if max(h, w) > self.ocr_size:
            scale = self.ocr_size / max(h, w)
            image_arr = cv2.resize(
                image_arr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA
            )
        try:
            results = self._ocr.predict(image_arr)
        except Exception:  # noqa: BLE001 — сбой движка OCR = воздержание, не 500
            return ""
        if not results:
            return ""
        page = results[0]
        texts = page.get("rec_texts") if hasattr(page, "get") else getattr(page, "rec_texts", None)
        scores = page.get("rec_scores") if hasattr(page, "get") else getattr(page, "rec_scores", None)
        if not texts:
            return ""
        scores = scores or [1.0] * len(texts)
        kept = [t for t, s in zip(texts, scores) if s >= self.score_thresh]
        return " ".join(kept)

    def verify(self, image: bytes, candidates: list[VerifyCandidate]) -> str | None:
        """Контракт (image-scan.md v0.4.4). `image` — ЗАПРОС целиком (как в `ImageIndex.
        search()`), не предварительно вырезанный регион — `verify()` сам нормализует.

        `CV_VERIFY_DEBUG=1` (TODO-2, ревью 05) — структурированный лог в stderr на
        каждый вызов (см. докстринг модуля, "Трассировка"). Флаг проверяется В НАЧАЛЕ
        и НЕ меняет ничего в основной ветке: при выключенном флаге код после этой
        проверки идентичен версии до правки (тот же `match_candidates()`, тот же
        порядок вызовов, без дополнительной работы)."""
        arr = imageio.decode_image(image)  # ValueError на битые/пустые байты — до OCR
        debug = _verify_debug_enabled()
        if not candidates:
            if debug:
                _log_verify_trace(candidates=[], ocr_text=None, trace={"reason": "no_candidates", "decision": None})
            return None
        normalized = normalize_query(arr, enabled=True)
        text = self.read_text(normalized)
        if not debug:
            return match_candidates(text, candidates)
        decision, trace = match_candidates_trace(text, candidates)
        _log_verify_trace(candidates=candidates, ocr_text=text, trace=trace)
        return decision


def benchmark(verifier: LabelVerifier, images: list[bytes], candidates: list[VerifyCandidate], n: int | None = None) -> dict:
    """Тайминги `verify()` end-to-end (нормализация + OCR + сопоставление) — для
    отчёта (бюджет контракта: p95 <= 700 мс)."""
    if not images:
        return {"n": 0}
    n = n or len(images)
    imgs = [images[i % len(images)] for i in range(n)]

    timings = []
    for data in imgs:
        t0 = time.perf_counter()
        verifier.verify(data, candidates)
        timings.append((time.perf_counter() - t0) * 1000)

    arr = np.array(timings)
    return {
        "n": len(arr),
        "p50_ms": round(float(np.percentile(arr, 50)), 2),
        "p95_ms": round(float(np.percentile(arr, 95)), 2),
        "max_ms": round(float(arr.max()), 2),
        "mean_ms": round(float(arr.mean()), 2),
    }
