"""`POST /v1/pairing/dish-photo`, `POST /v1/pairing/dish` — "Что подать" по
фото блюда (contracts/post-scan.md v1.1 §4, contracts/openapi.yaml 0.3.4,
ратифицировано architect 22.09 — сверено построчно с этим кодом, схема
ответа проверена тестом tests/test_pairing_contract_schema.py). Два
задокументированных расхождения с буквальным текстом контракта (пул
кандидатов §4.3, fuzzy-порог §4.2 в app/dish_recognition.py) — оба фиксы
багов, найденных ПОСЛЕ снимка кода, по которому писался контракт; цифры —
reports/backend-dish-photo.md, "Расхождения с контрактом".

`dish-photo`: multipart, поле `image` — лимиты/авторизация КАК У
`/v1/scan/photo` (`app/routers/scan.py`: принципал ОПЦИОНАЛЕН, тот же
`settings.max_upload_bytes`, 400 `validation_error` на пустом/слишком большом
файле). В архив сканов НЕ пишет (бриф тимлида) — `archive_scan_safe` здесь
не вызывается вовсе, это отдельная фича, не скан этикетки вина.

`dish`: JSON `{category, dish?}` — ручной выбор/исправление категории,
`dish.source` всегда `"user"`. В отличие от `dish-photo` (сознательно
зеркалит опциональную авторизацию `/scan/photo` — сюда может бить тот же
неавторизованный клиент, что и на фото), этот путь НЕ участвует в приватной
проверке кейсодержателя и не мультипартит фото — общее правило
`app/security.py` ("scan/chat/wines/analogs требуют принципала") применено
как обычно, принципал ОБЯЗАТЕЛЕН."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, File, UploadFile

from ..config import Settings, get_settings_dep
from ..cv.interface import ImageIndex
from ..deps import get_image_index_dep, get_retriever_dep
from ..dish_pairing import select_wines_for_dish
from ..dish_recognition import recognize_dish_photo, resolve_category
from ..errors import ApiError
from ..food_pairing import portal_tags
from ..rag.interface import Retriever
from ..schemas import DishManualRequest, DishPairingResponse
from ..security import Principal, get_current_principal, get_current_principal_optional

router = APIRouter(prefix="/pairing", tags=["pairing"])

_MESSAGE_NOT_FOOD = "Это не похоже на еду — подбор вин к этому фото недоступен."
_MESSAGE_BOTTLE = "Это похоже на бутылку вина, а не на блюдо — используйте сканер вина."
_MESSAGE_UNSURE = "Не удалось точно распознать блюдо — выберите категорию вручную."
_MESSAGE_NO_WINES = "Не нашлось подходящих вин под эту категорию."


def _build_response(recognition: dict, retriever: Retriever, settings: Settings, t0: float) -> DishPairingResponse:
    status = recognition["status"]
    dish = recognition["dish"]
    wines: list[dict] = []
    message: str | None = None

    if status == "food" and dish.get("category"):
        wines = select_wines_for_dish(retriever, dish["category"], settings)
        if not wines:
            message = _MESSAGE_NO_WINES
    elif status == "not_food":
        message = _MESSAGE_NOT_FOOD
    elif status == "bottle":
        message = _MESSAGE_BOTTLE
    elif status == "unsure":
        message = _MESSAGE_UNSURE

    return DishPairingResponse(
        status=status, dish=dish, wines=wines, message=message,
        timing_ms=int((time.monotonic() - t0) * 1000),
    )


@router.post("/dish-photo", response_model=DishPairingResponse)
async def pairing_dish_photo(
    image: UploadFile | None = File(default=None),
    principal: Principal | None = Depends(get_current_principal_optional),
    image_index: ImageIndex = Depends(get_image_index_dep),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
) -> DishPairingResponse:
    t0 = time.monotonic()
    data = await image.read() if image is not None else b""
    if image is None or not data:
        raise ApiError(400, "validation_error", "Пустой файл изображения")
    if len(data) > settings.max_upload_bytes:
        raise ApiError(400, "validation_error", f"Файл больше {settings.max_upload_bytes} байт")

    try:
        recognition = recognize_dish_photo(data, image_index, settings)
    except ValueError as exc:
        raise ApiError(400, "validation_error", f"Не удалось обработать изображение: {exc}") from exc

    return _build_response(recognition, retriever, settings, t0)


@router.post("/dish", response_model=DishPairingResponse)
def pairing_dish_manual(
    body: DishManualRequest,
    principal: Principal = Depends(get_current_principal),
    retriever: Retriever = Depends(get_retriever_dep),
    settings: Settings = Depends(get_settings_dep),
) -> DishPairingResponse:
    t0 = time.monotonic()
    tags = portal_tags(settings)
    category = resolve_category(body.category, tags)
    if category is None:
        raise ApiError(400, "validation_error", f"Неизвестная категория блюда: {body.category!r}")

    recognition = {
        "status": "food",
        "dish": {
            "name": (body.dish or "").strip(), "category": category, "alternatives": [],
            "ingredients": [], "source": "user",
        },
    }
    return _build_response(recognition, retriever, settings, t0)
