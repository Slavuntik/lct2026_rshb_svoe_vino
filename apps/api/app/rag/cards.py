"""Единая сборка карточки вина — используется И `GET /wines/{id}`, И
`card` в rich-режиме `/scan/photo` (contracts/image-scan.md v0.4.1: "card —
ровно тело ответа GET /wines/{id}"). Один построитель — чтобы эти два места
не могли снова разойтись по форме (ровно тот баг, который нашёл C и
зафиксировал v0.4.1: card в CV-пайплайне не нёс `similar`).

v0.4.11 (п.2, агент B8): если наш RAG-каталог не резолвит `wine_id` в вино —
фолбэк на каталог кейса (`app/rag/case_catalog.py`,
`CASE_DATA_DIR/case_catalog.json`): 122 из 2054 usable-слагов кейса (на
снимке 21.09) отсутствуют в нашем каталоге вовсе, а приватная проверка
организаторов содержит ТОЛЬКО вина из каталога кейса — без фолбэка тап по
такому кандидату/слагу упирался бы в `card=None`/404. Форма ответа
(wine_id/source/derived/source_url/similar) не меняется ни на йоту — только
источник данных внутри `source`; `derived`/`similar` у фолбэка пусты (в
каталоге кейса нет сенсорики и связей "похожих вин", это отдельный слой).

v0.3.6 (22.09, architect, contracts/openapi.yaml — дефект жюри,
reports/qa-manual-hack-v16.md п.5.1): `similar` — голые слаги, карточка
«Похожие вина» показывала их жюри напрямую (временная подпись фронта
«Похожее вино N», reports/frontend-jury-pass-fixes.md). +`similar_wines` —
{wine_id,name,winery,image_url} в ТОМ ЖЕ порядке, что `similar`. Дёшево:
`retriever.similar()` уже отдаёт `Candidate` с полным `meta` (source/derived)
— раньше код брал только `c.id` и выбрасывал остальное; теперь source
переиспользуется на месте, БЕЗ дополнительных обращений к ретриверу (см.
`_similar_wine_items` ниже). `similar` оставлен как есть (DEPRECATED в
контракте, не удалён) — обратная совместимость с клиентами, читающими голые
слаги. Слаг без пригодного `name` (тот же класс пробела в данных каталога,
что хотфикс `AnalogsWineItem` в `routers/scan.py`) — пропускается в
similar_wines, остаётся в similar.
"""
from __future__ import annotations

from . import case_catalog
from .interface import Candidate, Retriever


def _similar_wine_items(candidates: list[Candidate]) -> list[dict]:
    """{wine_id, name, winery, image_url} по `Candidate.meta["source"]` —
    ТЕ ЖЕ кандидаты, что уже вернул `retriever.similar()` для слагов
    `similar` (см. докстринг модуля) — ни одного нового похода в ретривер.
    Кандидат без пригодного `name` пропускается (остаётся только слагом в
    `similar`), порядок остальных сохраняется 1:1 с порядком `candidates`."""
    items: list[dict] = []
    for c in candidates:
        source = c.meta.get("source") or {}
        name = source.get("name")
        if not name:
            continue
        items.append({
            "wine_id": c.id,
            "name": name,
            "winery": source.get("winery_name") or source.get("winery") or None,
            "image_url": source.get("image_url"),
        })
    return items


def build_wine_card(retriever: Retriever, wine_id: str, *, similar_top_k: int = 6) -> dict | None:
    """{wine_id, source, derived, source_url, similar, similar_wines} — как
    GET /wines/{id}, буквально. None, если wine_id не резолвится НИ нашим
    RAG, НИ каталогом кейса (или вовсе неизвестен обоим) — вызывающая
    сторона решает, что это значит у себя (404 у /wines/{id}, card=None у
    /scan/photo)."""
    candidate = retriever.get_by_id(wine_id)
    if candidate is not None and candidate.kind == "wine":
        similar_candidates = retriever.similar(wine_id, top_k=similar_top_k)
        return {
            "wine_id": wine_id,
            "source": candidate.meta["source"],
            "derived": candidate.meta["derived"],
            "source_url": candidate.url,
            "similar": [c.id for c in similar_candidates],
            "similar_wines": _similar_wine_items(similar_candidates),
        }

    case_wine = case_catalog.lookup(wine_id)
    if case_wine is None:
        return None
    return {
        "wine_id": wine_id,
        "source": {
            "name": case_wine.name,
            "winery_name": case_wine.winery_name,
            "region_name": case_wine.region_name,
            "grapes": case_wine.grapes,
            "color": case_wine.color,
            "category": case_wine.category,
            "description": case_wine.description,
            "image_url": case_catalog.thumb_url(wine_id),
        },
        "derived": {},
        "source_url": case_catalog.source_url(wine_id),
        "similar": [],
        "similar_wines": [],
    }
