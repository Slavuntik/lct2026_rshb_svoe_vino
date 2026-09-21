"""GET /v1/case-thumbs/{slug}.webp — превью эталона кейса для фолбэк-карточки
(contracts/image-scan.md v0.4.11, п.4: "материалы каталога — только в демо,
как разрешили организаторы"). Файлы генерирует офлайн-скрипт
`apps/api/scripts/build_case_thumbs.py` (агент B8) в
`CASE_DATA_DIR/thumbs/<slug>.webp` — эта ручка только статически раздаёт уже
готовые файлы, без ресайза на лету.

Без авторизации (тот же принцип, что и `/scan/photo`, `/metrics/scan`) — сюда
бьёт `<img src>` фолбэк-карточки, у неё нет и не будет токена.
"""
from __future__ import annotations

import re

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..errors import ApiError
from ..rag.case_catalog import case_data_dir

router = APIRouter(prefix="/case-thumbs", tags=["case-thumbs"])

# Строгий allowlist слага (транслит + дефисы — тот же алфавит, что и у
# реальных слагов кейса, см. case-data/slug_refs.json), а не просто "нет
# ../": слаг обязан СОСТОЯТЬ ЦЕЛИКОМ из lower-case латиницы/цифр/дефисов, "."
# и "/" в нём в принципе невозможны — защита от обхода пути на уровне
# формата, до похода на диск.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Статика на весь срок демо (v0.4.11: "материалы каталога — только в демо") —
# конкретный slug всегда отдаёт один и тот же файл, безопасно кэшировать надолго.
_CACHE_CONTROL = "public, max-age=86400, immutable"


@router.get("/{slug}.webp")
def get_case_thumb(slug: str) -> FileResponse:
    if not _SLUG_RE.match(slug):
        raise ApiError(404, "not_found", "Превью не найдено")

    thumbs_dir = (case_data_dir() / "thumbs").resolve()
    path = (thumbs_dir / f"{slug}.webp").resolve()
    # Второй, не полагающийся на регекс рубеж защиты от обхода пути (DoD
    # contracts/image-scan.md v0.4.11 п.6) — тот же принцип "два независимых
    # слоя", что и в остальной волне (app/cv/service.py CV_VERIFY_PROXIMITY
    # + family-gap; case_catalog.py кэш+честная деградация).
    if not path.is_relative_to(thumbs_dir) or not path.is_file():
        raise ApiError(404, "not_found", "Превью не найдено")

    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": _CACHE_CONTROL})
