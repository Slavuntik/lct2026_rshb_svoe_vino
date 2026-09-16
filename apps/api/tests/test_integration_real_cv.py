"""Smoke-тест реальной связки B+G (contracts/image-scan.md v0.4.2): поднимает
API с IMAGE_PROVIDER=real поверх МАЛЕНЬКОГО реального индекса, собранного
через `cv` CLI из packages/cv/devfix (дев-фикстуры G, в git их нет — тест
честно скипается, если devfix отсутствует), и проходит /scan/photo (flat и
rich) настоящим фото из devfix.

Скип по умолчанию, включается ТРЕМЯ условиями одновременно (тот же паттерн,
что test_integration_real_rag.py):
  1. env RUN_CV_INTEGRATION=1 (тяжёлые модели — torch/transformers/SigLIP2 —
     не CI-дефолт);
  2. пакет cv импортируется (uv sync --extra dev --extra integration в
     apps/api);
  3. packages/cv/devfix/ существует и содержит фото (агент G скачал вежливым
     скриптом, не в git).

LabelVerifier остаётся mock — эта волна интегрирует ТОЛЬКО ImageIndex
(задание оркестратора), OCR-верификатор всё ещё "придёт позже" вне зон
B и G.

Индекс строится в tmp_path (CV_DATA_DIR) — НЕ в packages/cv/data, чтобы не
затирать собственный индекс агента G. Embed-кэш (CV_EMBED_CACHE_DIR) НЕ
переопределён специально: он общий с G (ключ — sha256 пиксельных байт +
модель, packages/cv/cv/encoder.py) — синтетические ракурсы детерминированы
по seed, поэтому те же исходники дают те же байты и переиспользуют уже
посчитанные G эмбеддинги. Реальный SigLIP2 нужен и здесь, и в build-index —
первый прогон "холодный" (загрузка модели), см. warm-up ниже и находку G
про cold-start (~27 с) в её собственном bench.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

# packages/cv (encoder.py) лениво грузит SigLIP2 через transformers
# .from_pretrained() — и в сабпроцессе `cv build-index`, и здесь же в
# процессе pytest (real_cv_client). Модель уже должна лежать в локальном
# HF-кэше (см. README агента G) — форсируем офлайн-режим, чтобы смоук не
# зависел от сети до HF Hub вообще: один прогон поймал транзитный ECONNRESET
# посреди ревалидации кэша, хотя сами веса на диске были целы и валидны.
# setdefault — не переопределяем, если кто-то явно попросил обратное через env.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

DEVFIX_DIR = Path(__file__).resolve().parents[3] / "packages" / "cv" / "devfix"
# Небольшой, но содержательный поднабор: near-dup пара (OCR-сценарий не в
# этой волне, но gap на ней стоит увидеть) + три обычные позиции — достаточно
# для "маленького реального индекса", не всех 57 фото devfix.
SUBSET_FILENAMES = [
    "aligote-barrel-2024.webp",
    "aligote-barrel-2025.webp",
    "abrau-dyurso-victor-dravigny-bryut.webp",
    "ballet-blanc.webp",
    "belbek-aligote-beloe-suhoe-125.webp",
]

_RUN_FLAG = os.environ.get("RUN_CV_INTEGRATION") == "1"

try:
    import cv.index  # noqa: F401 — наличие проверяем самим импортом, не только флагом
    _CV_IMPORTABLE = True
except ImportError:
    _CV_IMPORTABLE = False

_DEVFIX_FILES = (
    [DEVFIX_DIR / name for name in SUBSET_FILENAMES if (DEVFIX_DIR / name).exists()]
    if DEVFIX_DIR.exists() else []
)
_DEVFIX_READY = len(_DEVFIX_FILES) >= 3  # хотя бы часть поднабора — довольно для смоука

_READY = _RUN_FLAG and _CV_IMPORTABLE and _DEVFIX_READY

_SKIP_REASON = (
    "integration выключен по умолчанию — нужны все три: RUN_CV_INTEGRATION=1 "
    f"({'ok' if _RUN_FLAG else 'нет'}), пакет cv установлен "
    f"({'ok' if _CV_IMPORTABLE else 'нет — uv sync --extra integration'}), "
    f"devfix с фото {DEVFIX_DIR} ({'ok' if _DEVFIX_READY else 'нет — не датасет кейса, дев-фикстуры G'})"
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_cv_index_dir(tmp_path_factory) -> Path:
    """Собирает маленький реальный индекс через `cv` CLI (subprocess, как
    настоящий пользователь) в изолированный tmp_path — не трогает
    packages/cv/data агента G. scope="module": строим индекс ОДИН раз на
    все тесты файла (реальный build — не бесплатный)."""
    if not _READY:
        pytest.skip(_SKIP_REASON)

    base = tmp_path_factory.mktemp("cv_integration")
    refs_dir = base / "refs"
    refs_dir.mkdir()
    for f in _DEVFIX_FILES:
        shutil.copy(f, refs_dir / f.name)

    data_dir = base / "data"
    env = {**os.environ, "CV_DATA_DIR": str(data_dir)}

    result = subprocess.run(
        [sys.executable, "-m", "cv.cli", "build-index", "--refs", str(refs_dir), "--version", "b-integration-test"],
        cwd=str(Path(__file__).resolve().parents[3] / "packages" / "cv"),
        env=env, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, f"cv build-index упал:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"

    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["positions"] == len(_DEVFIX_FILES)

    return data_dir


@pytest.fixture(scope="module")
def real_cv_client(real_cv_index_dir):
    from app.main import create_app

    old_env = dict(os.environ)
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["JWT_SECRET"] = "test-secret-key-at-least-32-bytes-long"
    os.environ["RATE_LIMIT_MAX_REQUESTS"] = "1000"
    os.environ["IMAGE_PROVIDER"] = "real"
    os.environ["CV_DATA_DIR"] = str(real_cv_index_dir)
    os.environ.pop("LLM_PROVIDER", None)  # остаётся mock
    try:
        client = TestClient(create_app())
        # Warm-up: первый вызов ImageIndex.search() грузит модель + первую
        # MPS-компиляцию (найдено G: cold-start до ~27с в её собственном
        # bench) — прогоняем ЗАРАНЕЕ, чтобы не мерить это как timing_ms
        # обычного запроса ниже.
        warm = _DEVFIX_FILES[0].read_bytes()
        r = client.post("/v1/scan/photo", files={"image": ("warm.jpg", warm, "image/jpeg")})
        assert r.status_code == 200, f"warm-up запрос упал: {r.text}"
        yield client
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def _slug_for(filename: str) -> str:
    return Path(filename).stem


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_healthz_reports_warm_true_after_startup_warmup(real_cv_client: TestClient):
    """v0.4.4 (ревью 04, блокер 2): create_app() прогревает реальный энкодер
    ОДНИМ embed() заглушки при старте (app/cv/factory.py::warm_up_image_index,
    вызывается из app/main.py) — real_cv_client уже прошёл этот путь целиком
    (плюс собственный warm-up POST фикстуры). /healthz обязан честно отразить
    успешный прогрев, а не врать True по дефолту схемы."""
    r = real_cv_client.get("/v1/healthz")
    assert r.status_code == 200
    assert r.json()["warm"] is True


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_metrics_index_version_matches_live_image_index_property(real_cv_client: TestClient):
    """v0.4.4 (ревью 04, блокер 3): index_version обязан идти ТОЛЬКО из живого
    ImageIndex.index_version, никогда из env-плейсхолдера.

    НАХОДКА этого теста: на момент проверки G ДОБАВИЛА `index_version` в
    cv.index.ImageIndex (свойство читает манифест) параллельно, в той же
    рабочей копии, некоммиченно — контракта "TODO/null до её коммита" уже
    недостаточно, свойство реально есть. НО его значение здесь — НЕ версия
    из тестового tmp-индекса этого файла ("b-integration-test"): `cv/config.py`
    вычисляет MANIFEST_PATH как модульную константу ОДИН РАЗ при первом
    импорте cv.config (а он импортируется уже при коллекции этого файла —
    `try: import cv.index` в шапке, — до того как фикстура здесь успевает
    выставить CV_DATA_DIR), поэтому property читает манифест ПО СТАРОМУ
    дефолтному пути (packages/cv/data/manifest.json), не из изолированной
    tmp-директории теста — межагентский шов, не мой баг и не тестовый
    артефакт (см. reports/b-report.md, предложение к packages/cv/cv/config.py:
    MANIFEST_PATH/DATA_DIR стоит резолвить лениво, а не при импорте модуля).
    Поэтому здесь НЕ проверяем конкретное значение — только то, что моя
    сторона (routers/metrics.py) честно ретранслирует РОВНО то, что говорит
    живой объект, что бы там ни было, а не какой-то свой env-плейсхолдер."""
    r = real_cv_client.get("/v1/metrics/scan")
    assert r.status_code == 200
    assert r.json()["index_version"] == real_cv_client.app.state.image_index.index_version


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_photo_flat_mode_returns_correct_slug(real_cv_client: TestClient):
    photo = next(f for f in _DEVFIX_FILES if f.name == "ballet-blanc.webp")
    r = real_cv_client.post(
        "/v1/scan/photo?flat=1", files={"image": (photo.name, photo.read_bytes(), "image/webp")},
    )
    assert r.status_code == 200
    assert r.json() == {"slug": _slug_for(photo.name)}


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_photo_rich_mode_correct_slug_and_timing_budget(real_cv_client: TestClient):
    photo = next(f for f in _DEVFIX_FILES if f.name == "abrau-dyurso-victor-dravigny-bryut.webp")
    r = real_cv_client.post(
        "/v1/scan/photo", files={"image": (photo.name, photo.read_bytes(), "image/webp")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == _slug_for(photo.name)
    assert body["not_in_catalog"] is False
    assert body["confidence"]["top1_score"] is not None
    # case.md: SLA ответа <= 3 с. Пост-warm-up — без cold-start модели.
    assert body["timing_ms"] <= 3000, f"timing_ms={body['timing_ms']} вне бюджета SLA"


class _RecordingVerifier:
    """LabelVerifier остаётся мок в этой волне (реализация OCR — не задача
    ни B, ни G), но мок из app/cv/mock.py не узнаёт НАСТОЯЩИЕ слаги датасета
    (его конвенция MOCKPHOTO:near-dup:<slug> заточена под фикстуры
    app/cv/fixtures.py) — значит он честно вернёт None на aligote-barrel-*,
    и тест не увидел бы, дошёл ли пайплайн до вызова verify() вообще.
    Подменяем принципиально другим стабом-шпионом: сам факт вызова с
    правильными кандидатами и есть то, что тут проверяется, а не то, что
    ответит верификатор.

    v0.4.4: candidates — list[VerifyCandidate] ({slug, name, vintage}), не
    голые строки (app/cv/service.py::_verify_candidates() строит их через
    retriever.get_by_id()). RAG в этом тесте остаётся mock (фикстура не
    выставляет RAG_PROVIDER=real) и не знает настоящие слаги датасета —
    name/vintage у обоих кандидатов честно деградируют до slug/None, это не
    баг, а тот же fallback, что и для card=None."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def verify(self, image_bytes: bytes, candidates: list[dict]) -> str | None:
        self.calls.append(candidates)
        return candidates[0]["slug"]


@pytest.mark.skipif(not _READY, reason=_SKIP_REASON)
def test_real_near_dup_pair_triggers_ocr_verifier_routing(real_cv_client: TestClient):
    """aligote-barrel-2024/2025 — настоящая near-dup пара датасета (одна
    этикетка в каталоге, буквально совпадающие эталонные фото, см.
    packages/cv/cv/selfcheck.py::NEAR_DUP_GROUPS и reports/g-report.md).

    НАХОДКА №1 этого теста (см. reports/b-report.md): исходный
    CV_NEAR_DUP_GAP_THRESHOLD=0.05 был угадан по шкале мок-скоров
    (app/cv/mock.py) и на РЕАЛЬНОМ индексе для этой пары дал gap=0.245 —
    порог никогда бы не сработал. Пересчитан на 0.3 (app/config.py).

    НАХОДКА №2 (второй прогон после фикса №1): первая версия теста жёстко
    ждала slug=="aligote-barrel-2024" (мол, spy возвращает candidates[0], а
    topID-1 ANN — это всегда сам запрошенный слаг). Реальный прогон это
    опроверг: топ-1 ANN на фото "2024" оказался "2025". Это не баг пайплайна,
    а ровно то, зачем нужен near-dup: сама G заявляла self-match 93.9%, а не
    100% — то есть ANN-порядок ВНУТРИ пары недостоверен сам по себе в ~6%
    случаев, и это один из них. Поэтому здесь проверяем не голое число и не
    то, какой из пары "победил" в ANN, а ПОВЕДЕНИЕ: (a) near-dup routing
    реально вызывает LabelVerifier.verify() с обоими членами пары как
    кандидатами, и (b) итоговый slug — РОВНО ответ verify(), а не тихо
    исходный топ-1 ANN."""
    only_2024 = [f for f in _DEVFIX_FILES if f.name == "aligote-barrel-2024.webp"]
    if not only_2024:
        pytest.skip("aligote-barrel-2024.webp не попал в собранный поднабор devfix")
    photo = only_2024[0]

    spy = _RecordingVerifier()
    real_cv_client.app.state.label_verifier = spy
    try:
        r = real_cv_client.post(
            "/v1/scan/photo", files={"image": (photo.name, photo.read_bytes(), "image/webp")},
        )
    finally:
        from app.cv.mock import MockLabelVerifier
        real_cv_client.app.state.label_verifier = MockLabelVerifier()  # не протекаем в другие тесты модуля

    assert r.status_code == 200
    body = r.json()
    assert body["ocr_verified"] is True, "near-dup routing должен был позвать верификатор и принять его ответ"

    assert spy.calls, "LabelVerifier.verify() ни разу не вызван — near-dup routing не сработал на реальном gap"
    candidate_slugs = {c["slug"] for c in spy.calls[0]}
    assert candidate_slugs == {"aligote-barrel-2024", "aligote-barrel-2025"}, (
        f"ожидали ровно near-dup пару среди кандидатов, получили {candidate_slugs}"
    )
    # v0.4.4: каждый кандидат несёт метаданные каталога (пусть и деградировавшие
    # до slug/None здесь, см. докстринг _RecordingVerifier) — форма, не голые строки.
    assert all(set(c.keys()) == {"slug", "name", "vintage"} for c in spy.calls[0])
    # НЕ проверяем, какой из пары "победил" в ANN (см. НАХОДКА №2 выше) —
    # проверяем, что пайплайн ПРИМЕНИЛ ответ верификатора: итоговый slug
    # обязан РОВНО совпасть с тем, что вернул verify() (spy возвращает
    # candidates[0]["slug"]), иначе near-dup routing вызывает OCR, но
    # игнорирует его.
    assert body["slug"] == spy.calls[0][0]["slug"], (
        "итоговый slug обязан совпасть с ответом verify(), а не остаться "
        "на исходном топ-1 ANN — иначе OCR-решение ни на что не влияет"
    )

    # Документируем реальное число (не проверяем жёстко — следующая
    # перекалибровка на датасете кейса вправе его сдвинуть).
    gap = body["confidence"]["gap"]
    assert gap is not None and 0.05 < gap < 1.0, (
        f"gap={gap} — вне разумного диапазона; если это снова изменилось, порог в "
        f"app/config.py::cv_near_dup_gap_threshold нужно пересчитать ещё раз"
    )
