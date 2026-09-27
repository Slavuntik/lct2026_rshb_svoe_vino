"""Подбор вин к распознанному/выбранному блюду — `POST /v1/pairing/dish-photo`,
`POST /v1/pairing/dish` (contracts/post-scan.md v1.1 §4.3, ратифицировано
architect 22.09). Пул кандидатов — ВЕСЬ каталог (все слаги `case_catalog.json`,
2103 на снимке 22.09), не выборка — тимлид 22.09 отклонил первую версию (пул
`candidates_for_taste()`/`search()`, 30 из 2103): "colода свайп-дегустации —
разнообразие без учёта блюда... к стейку предложат лучшее из колоды, а не из
каталога". Контракт §4.3 всё ещё пишет "пул top-30 `Retriever.search()`" —
architect обещал поправить на "весь каталог" после этого отчёта (см. reports/
backend-dish-photo.md, "Расхождения с контрактом").

Два яруса:
  1. `basis="catalog"` — ВСЕ вина, у которых `category` буквально есть в
     `source.food_pairings` (то же поле, что уровень 1
     `app/food_pairing.py::compute_pairings` читает для ДРУГОГО направления).
     Включение — ТОЛЬКО по тегу (хард-блок/score движка правил тег не
     отменяют — портал уже сказал "сочетается"); сортировка — по score
     ДВИЖКА правил (тот же скоринг, что ярус rules, для ранжирования, не
     фильтрации), тег-вина без score (нет usable-вектора) — в конец,
     tie-break по слагу.
  2. `basis="rules"` — остаток (не попавшие в catalog по тегу): движок
     `pipeline/ref/food_pairing_rules.yaml` в направлении блюдо → вино
     (`app/food_pairing.py::score_wine_for_dish`): sensory-вектор, если
     надёжен (`_is_usable_sensory`), иначе heuristic по цвету/ключевым
     словам — обе функции переиспользованы 1:1. `score<=0`/хард-блок → вино
     не участвует (в отличие от яруса catalog — здесь score это единственное
     основание быть в выдаче вовсе, не только порядок).

Диверсификация — не больше `_MAX_PER_WINERY` вин одной винодельни, всего
`_MAX_TOTAL` (числа из брифа тимлида, НЕ из
`food_pairing_rules.yaml::output_contract.top_n` — тот про ДРУГОЕ направление).

Карточки/векторы вин кэшируются ОДИН РАЗ НА ПРОЦЕСС (`_build_catalog_cards`,
`@lru_cache` по объекту `retriever` — тот живёт один на процесс,
`app.state.retriever`; разные `retriever` — разные записи кэша, поэтому
изоляция тестов автоматическая) — на запрос только скоринг по уже готовым
векторам (см. её докстринг и замер в reports/backend-dish-photo.md,
"десятки миллисекунд на тёплом кэше"). Тесты подменяют `_iter_catalog_cards`
целиком (не обходят через search/candidates_for_taste) — см.
tests/test_dish_pairing.py."""
from __future__ import annotations

from functools import lru_cache

from .config import Settings
from .food_pairing import (
    _is_usable_sensory,
    _load_rules_data,
    _rules_path,
    build_heuristic_wine_vector,
    build_sensory_wine_vector,
    score_wine_for_dish,
)
from .rag import case_catalog
from .rag.cards import build_wine_card
from .rag.interface import Retriever

# _is_usable_sensory/_load_rules_data/_rules_path — приватные по соглашению
# (leading underscore), но этот модуль в той же зоне (backend) и том же
# файле владения (apps/api/app/), что food_pairing.py — переиспользуем как
# есть, вторая реализация загрузки YAML/гейта надёжности sensory была бы
# чистым дублированием уже протестированной логики.

_MAX_PER_WINERY = 2  # бриф тимлида: "не больше 2 вин одной винодельни"
_MAX_TOTAL = 6  # бриф тимлида: "всего 6"

_CATALOG_REASON_TEMPLATE = "Портал рекомендует это вино к категории «{category}»."
_FALLBACK_RULES_REASON = "Хорошо сочетается по вкусовому профилю блюда."

CatalogCard = tuple[str, dict, "dict | None"]  # (wine_id, source, wine_vector)


def _wine_vector(source: dict, derived: dict) -> dict | None:
    """Тот же каскад sensory→heuristic, что уровни 2/3 `compute_pairings()`
    (без уровня 1 — тег проверяется отдельно каталожным ярусом). `None` — ни
    надёжной sensory, ни пригодного `color` (вызывающий код просто не может
    посчитать score для этого вина — не роняет подбор)."""
    sensory = derived.get("sensory") or {}
    if _is_usable_sensory(sensory):
        return build_sensory_wine_vector(source, sensory)
    return build_heuristic_wine_vector(source)


@lru_cache(maxsize=16)
def _build_catalog_cards(retriever: Retriever) -> tuple[CatalogCard, ...]:
    """`(wine_id, source, wine_vector)` для КАЖДОГО слага `case_catalog.json`
    (2103 на снимке 22.09, `CASE_DATA_DIR/case_catalog.json`) — карточка
    через `build_wine_card()`, тот же RAG-первым/case-catalog-фолбэк
    источник, что `GET /wines/{id}` (несёт `source.food_pairings`, когда
    слаг резолвится через наш RAG-каталог; чистый case-catalog-фолбэк его не
    несёт вовсе, contracts/post-scan.md §1 "Находка" — тогда вино участвует
    только в ярусе rules, если у него есть `color`). `wine_vector` — тот же
    sensory→heuristic каскад, что уровни 2/3 `GET /wines/{id}/pairings`,
    посчитан ЗДЕСЬ, один раз (не на каждый запрос дальше по стеку).

    `@lru_cache` ключуется САМИМ объектом `retriever` (не строкой пути) —
    тот создаётся один раз на процесс (`create_app()` -> `app.state.
    retriever`), так что кэш естественно "once per process" в проде; разные
    `retriever` (каждый тест — свежий `MockRetriever()`/стаб) получают
    разные записи, изоляция тестов автоматическая — но тесты этого модуля
    всё равно подменяют `_iter_catalog_cards()` целиком (см. её докстринг),
    эта функция реальным кодом тестов не вызывается вовсе.

    `similar` карточки (`build_wine_card()` несёт его для `GET /wines/{id}`)
    отбрасывается — здесь не нужен, не тратим память на 2103 списка."""
    result: list[CatalogCard] = []
    for slug in case_catalog.all_slugs():
        card = build_wine_card(retriever, slug)
        if card is None:
            continue
        source = card.get("source") or {}
        derived = card.get("derived") or {}
        result.append((slug, source, _wine_vector(source, derived)))
    return tuple(result)


def _iter_catalog_cards(retriever: Retriever, settings: Settings) -> tuple[CatalogCard, ...]:
    """Тонкая обёртка над `_build_catalog_cards()` — единственная причина
    существования: тесты `monkeypatch.setattr(dish_pairing,
    "_iter_catalog_cards", ...)` ровно эту функцию, минуя `case_catalog.json`/
    `build_wine_card`/`retriever` целиком (бриф тимлида 22.09, п.4:
    "источник каталога подменяемым, а не обход через search"). `settings`
    сейчас не используется (case_catalog читает `CASE_DATA_DIR` из окружения
    напрямую) — параметр оставлен для единообразия сигнатуры с остальными
    функциями модуля и на случай будущей параметризации источника."""
    return _build_catalog_cards(retriever)


def get_catalog_cards(retriever: Retriever, settings: Settings) -> tuple[CatalogCard, ...]:
    """Публичная точка входа в ТОТ ЖЕ кэш карточек каталога — для
    переиспользования вне подбора к блюду: `GET /v1/catalog`
    (`app/routers/catalog.py`, задача тимлида 27.09, "Кэш карточек у нас уже
    есть, ты его делал для подбора к блюду, переиспользуй"). Тонкая,
    СТАБИЛЬНАЯ (не приватная по соглашению, в отличие от
    `_iter_catalog_cards`) обёртка над ней же — тот же прогретый в фоне при
    старте (`warm_up_catalog_cache`, `app/main.py`) кэш, без второго
    холодного построения (12 с на 2103 карточки, см. докстринг
    `_build_catalog_cards`). Тесты подменяют `_iter_catalog_cards()` (см.
    tests/test_dish_pairing.py) — эта функция вызывает её по имени модуля,
    поэтому подмена действует и здесь."""
    return _iter_catalog_cards(retriever, settings)


def _reset_catalog_cache() -> None:
    """Только для тестов (tests/conftest.py, autouse) — тот же приём, что
    `app/cv/service.py::_reset_model_breakers()`: если тест всё же коснулся
    реального `_build_catalog_cards()` без подмены `_iter_catalog_cards()`,
    следующий тест не наследует его кэш (см. докстринг `_build_catalog_cards`
    про то, почему это обычно и не требуется — но дёшево подстраховаться)."""
    _build_catalog_cards.cache_clear()


def warm_up_catalog_cache(retriever: Retriever) -> bool:
    """Прогрев `_build_catalog_cards()` — тимлид 22.09, реакция на замер
    "холодный кэш 12 с на 2103 карточки" (reports/backend-dish-photo.md):
    "не должен доставаться первому пользователю — на демо это выглядит как
    зависший запрос". Вызывается из `app/main.py` В ФОНОВОМ ПОТОКЕ (12 с —
    заметно дольше, чем синхронные `warm_up_image_index`/`warm_up_retriever`
    там же, задерживать старт процесса ради этого не стоит: `create_app()`
    обязан вернуться сразу, готовность сервиса не должна ждать 12 с).

    Сбой (любое исключение — битый `CASE_DATA_DIR`, сбой RAG-резолюции и
    т.п.) -> `False`, лог WARNING/INFO — забота вызывающего кода в
    `app/main.py`, не эта функция. Кэш тогда просто не прогрет заранее и
    соберётся ЛЕНИВО на первом реальном запросе `/v1/pairing/*` — то же
    поведение, что и без прогрева вовсе (не хуже, просто без выигрыша по
    времени для первого пользователя)."""
    try:
        _build_catalog_cards(retriever)
        return True
    except Exception:  # noqa: BLE001 — прогрев в фоновом потоке не должен ронять его молча с трейсбеком
        return False


def _winery_key(wine_id: str, source: dict) -> str:
    return source.get("winery") or source.get("winery_name") or wine_id


def _wine_item(wine_id: str, source: dict, *, reason: str, basis: str) -> dict:
    return {
        "wine_id": wine_id,
        "name": source.get("name") or wine_id,
        "winery": source.get("winery_name") or source.get("winery") or None,
        "color": source.get("color") or None,
        "sugar": source.get("sugar_category") or None,
        "image_url": source.get("image_url"),
        "reason": reason,
        "basis": basis,
    }


def select_wines_for_dish(retriever: Retriever, category: str, settings: Settings) -> list[dict]:
    """До `_MAX_TOTAL` вин, не больше `_MAX_PER_WINERY` одной винодельни;
    каталожный ярус первым, движок правил — вторым, добирает остаток (см.
    докстринг модуля). Детерминировано при фиксированном каталоге/
    `food_pairing_rules.yaml`: сортировки ниже — единственный источник
    порядка, никакой случайности."""
    catalog_cards = _iter_catalog_cards(retriever, settings)
    rules_data = _load_rules_data(_rules_path(settings))
    dish_vector = (rules_data.get("portal_tag_defaults") or {}).get(category) or {}
    reason_catalog = _CATALOG_REASON_TEMPLATE.format(category=category)

    catalog_tier: list[tuple[float, str, dict]] = []  # (sort_score, wine_id, source)
    rules_tier: list[tuple[float, str, dict, str]] = []  # (score, wine_id, source, explain)

    for wine_id, source, wine_vector in catalog_cards:
        scored = score_wine_for_dish(dish_vector, wine_vector, rules_data) if wine_vector is not None else None
        tagged = category in (source.get("food_pairings") or [])
        if tagged:
            # Включение — ТОЛЬКО по тегу; score (если посчитался) — лишь для
            # сортировки внутри яруса, не для фильтрации (contracts/post-scan.md
            # §4.3, п.1 по брифу тимлида 22.09: "портал уже сказал — доверяем").
            sort_score = scored[0] if scored is not None else 0.0
            catalog_tier.append((sort_score, wine_id, source))
            continue
        if scored is None:
            continue
        score, explain = scored
        rules_tier.append((score, wine_id, source, explain or _FALLBACK_RULES_REASON))

    catalog_tier.sort(key=lambda t: (-t[0], t[1]))  # score убыв., wine_id возр. (tie-break)
    rules_tier.sort(key=lambda t: (-t[0], t[1]))

    selected: list[dict] = []
    winery_counts: dict[str, int] = {}

    def _try_add(wine_id: str, source: dict, reason: str, basis: str) -> None:
        if len(selected) >= _MAX_TOTAL:
            return
        key = _winery_key(wine_id, source)
        if winery_counts.get(key, 0) >= _MAX_PER_WINERY:
            return
        winery_counts[key] = winery_counts.get(key, 0) + 1
        selected.append(_wine_item(wine_id, source, reason=reason, basis=basis))

    for _score, wine_id, source in catalog_tier:
        _try_add(wine_id, source, reason_catalog, "catalog")
    for _score, wine_id, source, explain in rules_tier:
        _try_add(wine_id, source, explain, "rules")

    return selected
