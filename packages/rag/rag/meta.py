"""Форма Candidate.meta — зафиксирована контрактом v0.3 (ревью 02, блокер 1).

Внутренний payload (хранится в Qdrant/сайдкарах, используется для
фильтрации/ранжирования — filters/sensory/kind/text/url/id) шире, чем то,
что отдаётся наружу в Candidate.meta. Эта функция — единственное место,
где internal-payload превращается в публичную форму meta по kind:

  kind=wine:   {"source": {...}, "derived": {...}}   — блоки карточки vines целиком
  kind=chunk:  {"article_id", "title", "heading", "rubric"}
  kind=winery: {"source": {...}}

Лист-модуль (без зависимостей на остальной rag/) — импортируется отовсюду,
где строится Candidate (hybrid/styles/taste/base), без риска циклов.
"""
from __future__ import annotations


def public_meta(payload: dict) -> dict:
    kind = payload.get("kind")
    if kind == "wine":
        return {"source": payload.get("source") or {}, "derived": payload.get("derived") or {}}
    if kind == "chunk":
        return {
            "article_id": payload.get("article_id"),
            "title": payload.get("title"),
            "heading": payload.get("heading"),
            "rubric": (payload.get("filters") or {}).get("rubric"),
        }
    if kind == "winery":
        return {"source": payload.get("source") or {}}
    # Защитный запасной путь — в контракте перечислены только три kind;
    # если когда-нибудь появится четвёртый, лучше отдать payload как есть,
    # чем уронить запрос.
    return dict(payload)
