#!/usr/bin/env python3
"""qa/run_cv_index_baseline.py — синтетический baseline сканера напрямую поверх
packages/cv.ImageIndex (агент G), В ОБХОД apps/api (агент F2, см. reports/f-report.md,
раздел «Кейс-сканер: синтетический baseline»).

ПОЧЕМУ в обход apps/api — зафиксированное ограничение задания, не забывчивость:
На момент этого прогона `git log` не содержит ни одного коммита "B: ..." после
`4fdb445` (G сдал packages/cv) — `apps/api/app/cv/factory.py` и соседние файлы ЕСТЬ
изменены под IMAGE_PROVIDER=real (видно в `git status`), но это НЕЗАКОММИЧЕННОЕ, ещё
активно редактируемое рабочее дерево параллельного агента B. Протокол ORCHESTRATION.md
("Пиши только в своей зоне", коммиты ТОЛЬКО с pathspec — b85223b — прямое предупреждение
о цене работы поверх чужого недописанного состояния) и буквальное задание оркестратора
("следи git log по «B:»; если коммит уже лёг...") трактуют "лёг" как ЗАКОММИЧЕН — не
"лежит правкой в общем рабочем дереве прямо сейчас". Поэтому этот раннер идёт НАПРЯМУЮ к
packages/cv.ImageIndex, минуя apps/api целиком:

  - сборка индекса — субпроцессом настоящего `cv build-index` CLI (packages/cv/cv/cli.py),
    как явно указано заданием, по packages/cv/devfix; ЛЮБЫЕ пути записи индекса/кэша
    перенаправлены в qa/synthetic/cv-index/ (CV_DATA_DIR/CV_EMBED_CACHE_DIR) — ни один
    байт не пишется в packages/cv/ (зона G, read-only для агента F/F2);
  - измерение — лёгкий адаптер `CvIndexPredictor` ниже, реализующий Predictor-протокол
    qa/scan_eval.py (`predict(image_bytes, filename) -> Prediction`), вызывающий
    `ImageIndex.search()` напрямую в процессе — без HTTP, без apps/api.

Что это НЕ измеряет (ограничение, зафиксировано, не молчаливый пробел):
  - HTTP/multipart-накладные расходы настоящего `/scan/photo` (на локальном хосте обычно
    единицы мс — не определяющие бюджет ≤3с, но формально другое число, не измеренное здесь);
  - OCR-верификатор near-dup (contracts/image-scan.md, «Пайплайн /scan/photo») — его нет
    ни у кого на эту дату; near-dup пара `aligote-barrel-2024`/`aligote-barrel-2025` в
    eval-сете оценена СТРОГО (`true_slug == top1_slug`), БЕЗ снисхождения вроде
    `cv.selfcheck.is_acceptable_match` — ожидаемое расхождение по этой паре в
    top_confusions отчёта — не баг ImageIndex, см. `contracts/image-scan.md` v0.4.2 и
    `packages/cv/cv/selfcheck.py::NEAR_DUP_GROUPS`;
  - калибровку confident vs not_in_catalog (CV_CONFIDENT_SCORE_THRESHOLD и т.п. — настройки
    apps/api/app/config.py, не packages/cv) — здесь выдаются сырые top-K ANN-матчи.

Запускать ТОЛЬКО через venv пакета cv (torch/opencv/qdrant-client — их нет в qa/.venv):

    packages/cv/.venv/bin/python qa/run_cv_index_baseline.py

Модуль умышленно НЕ импортирует `cv.*` на уровне модуля (только внутри `main()`) — так
`CvIndexPredictor` (чистый duck-typed адаптер, не зависящий от конкретного класса
ImageIndex) и `_parse_build_summary` юнит-тестируются под обычным qa/.venv без torch/opencv
(см. qa/test_synthetic_baseline.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
_CV_PKG_DIR = _REPO_ROOT / "packages" / "cv"
_CV_CLI = _CV_PKG_DIR / ".venv" / "bin" / "cv"
_DEVFIX_DIR = _CV_PKG_DIR / "devfix"

_INDEX_ROOT = _QA_DIR / "synthetic" / "cv-index"
_CV_DATA_DIR = _INDEX_ROOT / "data"
_CV_EMBED_CACHE_DIR = _INDEX_ROOT / "embed_cache"

_PHOTOS_DIR = _QA_DIR / "synthetic" / "photos"
_LABELS_CSV = _QA_DIR / "synthetic" / "labels.csv"

_OUT_DIR = _QA_DIR / "scan-eval-runs" / "synthetic-cv-index-baseline"

BUILD_SEED = 0  # cv/config.py::AUGMENT_SEED_DEFAULT — конвенция G, оставлена как есть
BUILD_VIEWS = 24  # DoD "≥20" (contracts/image-scan.md)
INDEX_VERSION = "f2-synthetic-baseline"

# ВАЖНО: должны попасть в окружение ДО импорта cv.config (его константы читают os.environ
# на момент импорта модуля, не лениво) — выставляем их здесь модульно и передаём тем же
# os.environ и в subprocess build-шага, и (тем же процессом) в eval-шаг ниже.
os.environ.setdefault("CV_DATA_DIR", str(_CV_DATA_DIR))
os.environ.setdefault("CV_EMBED_CACHE_DIR", str(_CV_EMBED_CACHE_DIR))

# HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE: чекпойнт google/siglip2-base-patch16-224 уже полностью
# в локальном кэше (~/.cache/huggingface/hub) — без этого флага `AutoProcessor/AutoModel.
# from_pretrained()` всё равно ходят в сеть за метаданными (etag/redirect) на КАЖДЫЙ холодный
# запуск процесса, и при первом прогоне поймали живой обрыв сети (httpx.RemoteProtocolError:
# "Server disconnected without sending a response" — см. reports/f-report.md, раздел
# «Кейс-сканер: синтетический baseline»). Офлайн-режим убирает сетевой поход целиком: и
# быстрее, и не зависит от сети вообще, раз веса уже на диске (ORCHESTRATION.md намеренно НЕ
# запрещает читать уже скачанное — запрещает "деплоить/публиковать/регистрировать во внешних
# сервисах", это про другое).
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.path.insert(0, str(_QA_DIR))
import scan_eval as se  # noqa: E402 — путь добавлен строкой выше; scan_eval сам чисто stdlib


def _parse_build_summary(stdout: str, fallback_version: str) -> dict[str, Any]:
    """`cv build-index` печатает ровно один JSON-объект в stdout (cli.py::cmd_build_index).
    Если сторонняя библиотека всё же засорит stdout (напр. warning не в stderr) —
    best-effort фолбэк вместо падения всего прогона на некритичной метаданной."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"version": fallback_version, "parse_error": True, "raw_stdout_tail": stdout[-500:]}


def build_index() -> dict[str, Any]:
    """`cv build-index` НАПРЯМУЮ через CLI пакета (packages/cv/cv/cli.py::cmd_build_index) —
    как явно указано заданием оркестратора, не реимплементация build() своими руками."""
    if not _CV_CLI.is_file():
        raise RuntimeError(f"не найден CLI пакета cv: {_CV_CLI} (нужен `uv pip install -e .` в packages/cv)")
    cmd = [
        str(_CV_CLI), "build-index",
        "--refs", str(_DEVFIX_DIR),
        "--version", INDEX_VERSION,
        "--views", str(BUILD_VIEWS),
        "--seed", str(BUILD_SEED),
    ]
    print(f"[baseline] сборка индекса: {' '.join(cmd)}")
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(_CV_PKG_DIR), env=os.environ.copy(), capture_output=True, text=True)
    dt = time.perf_counter() - t0
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        raise RuntimeError(f"cv build-index упал (код {proc.returncode})")
    print(f"[baseline] build-index занял {dt:.1f}с (включая холодный старт модели)")
    summary = _parse_build_summary(proc.stdout, INDEX_VERSION)
    summary["wall_s"] = round(dt, 2)
    return summary


class CvIndexPredictor:
    """Лёгкий адаптер: Predictor-протокол qa/scan_eval.py поверх ImageIndex.search()
    напрямую — задание оркестратора ("... прогони eval ... через лёгкий адаптер").
    `index` — duck-typed (нужен только метод `.search(bytes, top_k=int) -> list[Match]`,
    где Match даёт `.slug/.score/.gap`), НЕ импортируем cv.index здесь, чтобы этот класс
    оставался тестируемым под qa/.venv без torch/opencv (см. тесты с фейковым индексом)."""

    def __init__(self, index: Any, top_k: int = 5):
        self.index = index
        self.top_k = top_k

    def predict(self, image_bytes: bytes, filename: str) -> se.Prediction:  # noqa: ARG002 — сигнатура Predictor
        t0 = time.perf_counter()
        try:
            matches = self.index.search(image_bytes, top_k=self.top_k)
        except ValueError as exc:
            # Контракт (packages/cv/cv/interface зеркало в apps/api): битый/пустой файл
            # -> ValueError, не 500-полуфабрикат. Здесь это честная ошибка транспорта уровня
            # раннера (n_errors), а не промах "не нашли slug".
            return se.Prediction(
                top1_slug=None, top5_slugs=[], latency_ms=(time.perf_counter() - t0) * 1000, error=str(exc)
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        top5 = [m.slug for m in matches]
        return se.Prediction(
            top1_slug=top5[0] if top5 else None,
            top5_slugs=top5,
            latency_ms=elapsed_ms,
            top1_score=matches[0].score if matches else None,
            gap=matches[0].gap if matches else None,
        )


def main() -> int:
    if not _LABELS_CSV.is_file():
        print(f"нет разметки {_LABELS_CSV} — сначала запустить qa/gen_synthetic_scan_photos.py", file=sys.stderr)
        return 2

    build_summary = build_index()

    from cv.index import ImageIndex  # лениво: тяжёлые зависимости нужны только здесь

    items, load_warnings = se.load_eval_set(_PHOTOS_DIR, _LABELS_CSV)
    if not items:
        print("пустой eval-сет", file=sys.stderr)
        return 2

    index = ImageIndex()
    # Прогрев: первый encode() лениво грузит веса модели (секунды на MPS/CPU при холодном
    # HF-кэше) — не должен исказить p50/p95 первого же фото (тот же приём, что
    # cv/encoder.py::SiglipEncoder.benchmark применяет к самому энкодеру).
    t_warm = time.perf_counter()
    _ = index.encoder.dim
    print(f"[baseline] прогрев энкодера: {time.perf_counter() - t_warm:.1f}с")

    predictor = CvIndexPredictor(index)
    records = se.run_eval(items, predictor)

    run_warnings = [
        "Прогон НАПРЯМУЮ поверх packages/cv.ImageIndex, в обход apps/api "
        "(IMAGE_PROVIDER=real на момент прогона не закоммичен — см. докстринг модуля): "
        "нет HTTP-слоя, нет OCR-верификатора near-dup, нет калибровки confident/"
        "not_in_catalog. Near-dup пара aligote-barrel-2024/2025 оценена СТРОГО "
        "(true_slug == top1_slug), без снисхождения cv.selfcheck.is_acceptable_match.",
    ]
    n_errors = sum(1 for r in records if r.error is not None)
    if n_errors:
        run_warnings.append(f"{n_errors} из {len(records)} фото дали ValueError при search() (см. records[].error)")

    report = se.build_report(
        mode="cv-index-direct",
        api_url=None,
        split="all",
        seed=BUILD_SEED,
        holdout_frac=0.0,
        photos_dir=_PHOTOS_DIR,
        items=items,
        records=records,
        load_warnings=load_warnings,
        run_warnings=run_warnings,
        index_version=build_summary.get("version", INDEX_VERSION),
    )
    report["cv_index_build"] = build_summary
    report["dataset_note"] = "синтетика (аугментатор G), НЕ полевые фото кейсодержателя"

    json_path, md_path = se.write_report(report, _OUT_DIR)
    print(f"[baseline] отчёт -> {json_path}\n[baseline]        -> {md_path}")

    m = report["metrics"]
    print(
        f"[baseline] n={m['n']}/{m['n_total']} match_rate={m['match_rate']:.3f} "
        f"match_rate_top5={m['match_rate_top5']:.3f} f1_top1={m['f1_top1']:.3f} "
        f"f1_top5={m['f1_top5']:.3f} p50={m['p50_ms']:.0f}мс p95={m['p95_ms']:.0f}мс"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
