"""pytest для qa/gen_case_synthetic_baseline_photos.py (агент F4, задача 1 —
reports/f4-data-hygiene.md): регресс на найденную и починенную системную аномалию
генератора (reviews/06-gate-and-analogs.md п.3 — raw top-1 44,0% на seed 314159 vs
69,4% на seed 20260917, "не шум выборки, 6σ").

Причина (см. докстринг "ПРАВКА F4" в самом gen_case_synthetic_baseline_photos.py):
render_synthetic_views(image, n=1, seed=SEED) раньше вызывался с ОДНИМ И ТЕМ ЖЕ
буквальным SEED на каждый слог цикла. render_synthetic_views создаёт свежий
np.random.default_rng(seed) на каждый вызов и тянет ровно n=1 набор параметров
ракурса/фона/блика/блюра/шума/экспозиции — ни один из них не зависит от изображения
(cv/augment.py::_sample_view_params(rng) не принимает картинку вовсе). Значит ВСЕ фото
одного прогона получали побайтово ОДИН И ТОТ ЖЕ ракурс — смена SEED между прогонами не
сэмплирует типичный разброс сложности, а целиком переносит прогон в другую случайную
точку пространства параметров. Починено: _stable_seed_for(slug) — тот же sha256-приём,
что уже был в qa/gen_synthetic_scan_photos.py::_stable_seed_for.

Тесты разбиты по тяжести зависимостей:
- _stable_seed_for(...) сам по себе — чистый hashlib, работает под обычным qa/.venv.
- "инвариантность распределения по seed" — нужен настоящий cv.augment._sample_view_params
  (numpy) — весь модуль пропускается (importorskip), если numpy недоступен (qa/.venv его
  не ставит, см. qa/requirements.txt), так что сбор тестов там не падает, просто
  "skipped"; реальный прогон — под packages/cv/.venv (см. reports/f4-data-hygiene.md).
"""
from __future__ import annotations

import statistics

import pytest

import gen_case_synthetic_baseline_photos as gen_baseline

np = pytest.importorskip("numpy")
augment = pytest.importorskip("cv.augment")


# --------------------------------------------------------------------------------------
# _stable_seed_for — чистая логика, без numpy/cv2
# --------------------------------------------------------------------------------------


def test_stable_seed_is_deterministic():
    assert gen_baseline._stable_seed_for("aligote-barrel-2024") == gen_baseline._stable_seed_for("aligote-barrel-2024")


def test_stable_seed_differs_by_slug():
    seeds = {gen_baseline._stable_seed_for(f"wine-{i}") for i in range(50)}
    assert len(seeds) == 50  # ни одной коллизии на 50 слагах


def test_stable_seed_differs_by_base_seed_too():
    a = gen_baseline._stable_seed_for("wine-1", base_seed=20260917)
    b = gen_baseline._stable_seed_for("wine-1", base_seed=314159)
    assert a != b


# --------------------------------------------------------------------------------------
# Регресс на саму аномалию: разброс параметров ракурса по слагам внутри ОДНОГО прогона
# (base_seed фиксирован) — до фикса он был равен НУЛЮ (все слаги -> один и тот же ракурс).
# --------------------------------------------------------------------------------------

_FAKE_SLUGS = [f"wine-{i:04d}" for i in range(200)]


def _yaws_for_base_seed(base_seed: int) -> list[float]:
    return [
        augment._sample_view_params(np.random.default_rng(gen_baseline._stable_seed_for(slug, base_seed))).yaw_deg
        for slug in _FAKE_SLUGS
    ]


@pytest.mark.parametrize("base_seed", [20260917, 314159, 1])
def test_view_params_do_not_collapse_to_a_single_point(base_seed):
    """До фикса: render_synthetic_views(img, n=1, seed=SEED) с ОДНИМ SEED на все слаги ->
    _sample_view_params вызывался бы с rng одинакового состояния для КАЖДОГО слага ->
    все yaw_deg были бы побайтово идентичны (разброс=0). Прямой регресс-тест на баг:
    200 разных слагов должны получать 200 (практически) разных ракурсов."""
    yaws = _yaws_for_base_seed(base_seed)
    assert len(set(round(y, 9) for y in yaws)) > 190
    assert (max(yaws) - min(yaws)) > 30.0  # диапазон yaw в _sample_view_params — [-24, 24]


def test_view_param_spread_is_stable_regardless_of_which_base_seed_is_picked():
    """"Тест на инвариантность распределения по сиду" (agents/F4-data-hygiene.md, задача
    1): смена SEED прогона не должна сама по себе решать, насколько ТРУДНЫМ окажется
    весь прогон — разброс (std) параметров ракурса на большой выборке слагов должен быть
    похожим для ЛЮБОГО base_seed, а не точкой (std=0, старый баг) и не скакать на
    порядок. Теоретическое std равномерного [-24,24] = 48/sqrt(12) ~= 13.86; допуск ниже
    — щедрый (цель — доказать "не вырождается и не скачет на порядок", не пиновать
    точное распределение)."""
    stds = {seed: statistics.pstdev(_yaws_for_base_seed(seed)) for seed in (20260917, 314159, 1, 999983)}
    for seed, std in stds.items():
        assert 8.0 < std < 20.0, f"base_seed={seed}: std(yaw_deg)={std:.2f} — подозрительно вырожденное/разбросанное"


def test_old_bug_reproduced_on_purpose_as_a_negative_control():
    """Негативный контроль: буквально СТАРЫЙ баганый вызов (один литеральный seed на
    каждый слаг, без _stable_seed_for) ДЕЙСТВИТЕЛЬНО даёт нулевой разброс — подтверждает,
    что тесты выше ловят именно эту регрессию, а не что-то ещё."""
    yaws = [augment._sample_view_params(np.random.default_rng(20260917)).yaw_deg for _slug in _FAKE_SLUGS]
    assert len(set(round(y, 9) for y in yaws)) == 1  # старый баг: буквально одна точка
