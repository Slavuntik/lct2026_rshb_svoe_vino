"""GET /v1/catalog — постраничная плитка каталога кейса (2103 позиции) для
экрана «Каталог вин» (макет Figma, задача тимлида 27.09, "нужен быстро и
просто, фронт делает его следом").

Проверено на переиспользование ДО того, как писать новый метод (см. отчёт
reports/backend-catalog-list.md, "Проверено на переиспользование") — ничего
не подошло без натяжки:
  - `GET /v1/taste/candidates` — колода СВАЙПА (`require_profiling_consent`,
    гостю недоступна вовсе), `retriever.candidates_for_taste()` отдаёт
    РАЗНООБРАЗИЕ по цвету/региону/стилю и исключает уже свайпнутое — нет ни
    курсора, ни детерминированной страницы по всему каталогу; другая задача
    (профиль вкуса, не браузинг каталога).
  - `POST /v1/analogs` — резолвит ОДИН эталонный стиль по текстовому запросу
    и отдаёт top-12 вин ЭТОГО стиля; ни постраничности, ни поиска по
    названию, ни списка каталога вообще.
  - `GET /v1/case-thumbs/{slug}.webp` — статика превью, не список; здесь же
    используется для `image_url` (см. ниже).

Источник данных — ТОТ ЖЕ кэш карточек каталога, что `app/dish_pairing.py`
строит для подбора вин к блюду (`dish_pairing.get_catalog_cards()`,
`@lru_cache` по объекту retriever, прогревается в фоновом потоке при старте
процесса — `warm_up_catalog_cache()` в `app/main.py`). Этот модуль
переиспользует его БЕЗ повторного холодного построения (12 с на 2103
карточки, см. `dish_pairing._build_catalog_cards.__doc__`) и добавляет СВОЙ
кэш `_catalog_items()` поверх: плоские карточки плитки (без веса
sensory/heuristic — он тут не нужен), отсортированные ОДИН раз. На запрос
дальше — только фильтрация/срез уже готового кортежа (тёплый кэш — единицы
миллисекунд на 2103 позициях, замер в reports/backend-catalog-list.md).

`image_url` — НЕ `source.get("image_url")` напрямую. У 1978 из 2103 слагов
каталога (те, что резолвятся настоящим RAG-индексом из vines/catalog/wines/)
это поле несёт внешний CDN организатора (`api.vino-svoe.ru`) — всегда
валидный, реальное фото портала. У остальных ~125 (супплемент
`packages/rag/rag/case_data.py::build_supplemental_wine_records` — слагов
кейса, которых нет в настоящем RAG-индексе вовсе) это НАШ ЖЕ шаблон
`/v1/case-thumbs/{slug}.webp`, но записанный БЕЗУСЛОВНО, без проверки, есть
ли файл на диске. Плитка каталога — единый локальный источник превью для
ВСЕХ 2103 позиций (`case_catalog.thumb_url()`/`has_thumb()`, тот же файл,
что реально отдаёт `GET /v1/case-thumbs/{slug}.webp`): так у КАЖДОЙ карточки
плитки одна и та же гарантия (либо реальный файл, либо честный `null`), а
не смесь "внешний CDN на удачу" и "наш шаблон на удачу" на одном экране.
`image_url: null`, если файла в `CASE_DATA_DIR/thumbs` нет — вино НЕ
выбрасывается из выдачи, фронт рисует заглушку (задание тимлида; см.
`case_catalog.has_thumb()` про фактическое число слагов без файла и
расхождение с ориентиром "44").

Авторизация — `Depends(get_current_principal)`, как у соседних браузинговых
методов `GET /wines/{id}` и `POST /analogs` (`app/security.py`: "Гость
допущен к scan/chat/wines/analogs").

Порядок — детерминированный: имя (casefold) с тай-брейком по `wine_id`
(слаг). Не зависит ни от скора, ни от времени запроса — при неизменном
каталоге постраничная выдача не дублирует и не пропускает позиции между
запросами (в т.ч. при разном `q`/`color`/`sugar` — сортировка ОДНА на весь
кэш, фильтры только сужают срез до неё).
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, Query

from .. import dish_pairing
from ..config import Settings, get_settings_dep
from ..deps import get_retriever_dep
from ..rag import case_catalog
from ..rag.interface import Retriever
from ..schemas import CatalogItem, CatalogResponse
from ..security import Principal, get_current_principal

router = APIRouter(prefix="/catalog", tags=["catalog"])

CatalogRow = dict  # {"wine_id","name","winery","color","sugar","image_url"} — см. CatalogItem


def _catalog_row(wine_id: str, source: dict) -> CatalogRow:
    return {
        "wine_id": wine_id,
        "name": source.get("name") or wine_id,
        "winery": source.get("winery_name") or source.get("winery") or None,
        "color": source.get("color") or None,
        "sugar": source.get("sugar_category") or None,
        "image_url": case_catalog.thumb_url(wine_id) if case_catalog.has_thumb(wine_id) else None,
    }


@lru_cache(maxsize=16)
def _catalog_items(retriever: Retriever, settings: Settings) -> tuple[CatalogRow, ...]:
    """Плоские, ОТСОРТИРОВАННЫЕ ОДИН РАЗ карточки плитки — построены поверх
    `dish_pairing.get_catalog_cards()` (см. докстринг модуля). `@lru_cache`
    ключуется `(retriever, settings)` — тот же приём "один раз на процесс",
    что `dish_pairing._build_catalog_cards` (ключ по объекту retriever,
    живущему один на процесс/тест — см. её докстринг про изоляцию тестов).
    `settings` в самой сборке не участвует (`case_catalog` читает
    `CASE_DATA_DIR` из окружения напрямую, не через Settings) — в ключе
    кэша только ради единой сигнатуры с `get_catalog_cards()`, тесты это не
    задевает (разные объекты retriever уже дают разные записи)."""
    rows = [_catalog_row(wine_id, source) for wine_id, source, _vector in dish_pairing.get_catalog_cards(retriever, settings)]
    rows.sort(key=lambda r: (r["name"].casefold(), r["wine_id"]))
    return tuple(rows)


def _reset_catalog_items_cache() -> None:
    """Только для тестов (tests/conftest.py, autouse) — тот же приём, что
    `dish_pairing._reset_catalog_cache()`: страховка, если какой-то тест
    всё же построил реальный кэш этого модуля без подмены
    `dish_pairing._iter_catalog_cards()`."""
    _catalog_items.cache_clear()


def _text_matches(row: CatalogRow, needle_casefold: str) -> bool:
    if needle_casefold in row["name"].casefold():
        return True
    winery = row["winery"]
    return bool(winery) and needle_casefold in winery.casefold()


@router.get("", response_model=CatalogResponse)
def get_catalog(
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200, description="Поиск по названию и винодельне"),
    color: str | None = Query(default=None, max_length=50),
    sugar: str | None = Query(default=None, max_length=50),
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
) -> CatalogResponse:
    rows = _catalog_items(retriever, settings)

    if color and color.strip():
        needle = color.strip().casefold()
        rows = tuple(r for r in rows if (r["color"] or "").casefold() == needle)
    if sugar and sugar.strip():
        needle = sugar.strip().casefold()
        rows = tuple(r for r in rows if (r["sugar"] or "").casefold() == needle)
    if q and q.strip():
        needle = q.strip().casefold()
        rows = tuple(r for r in rows if _text_matches(r, needle))

    total = len(rows)
    page = rows[offset : offset + limit]
    return CatalogResponse(
        wines=[CatalogItem(**row) for row in page],
        total=total,
        limit=limit,
        offset=offset,
    )
