"""Распознавание блюда по фото — `POST /v1/pairing/dish-photo`
(contracts/post-scan.md v1.1 §4.1, ратифицировано architect 22.09 — сверено
построчно С ЭТИМ КОДОМ). Одно задокументированное расхождение с буквальным
текстом §4.2: `_FUZZY_MATCH_THRESHOLD` ниже — 80, не 60 — фикс бага,
найденного ПОСЛЕ снимка кода, по которому писался контракт (детали у самой
константы; цифры — reports/backend-dish-photo.md, "Расхождения с контрактом").

Порядок путей распознавания (первый удачный — используется):
  1. VLM-шлюз (`VISION_LLM_URL`) — приоритетный источник.
  2. Локальная VLM (`VISION_LLM_LOCAL_URL`, практически — Mac) — ТОЛЬКО если
     шлюз не ответил/не настроен/не успел. В отличие от слияния сканера
     (`app/cv/service.py::_fusion_text_and_vectors`, СКЛЕИВАЕТ тексты обеих
     моделей) — здесь простой приоритет по брифу тимлида: "берём ответ
     шлюза, если успел, иначе локальный".
  3. Zero-shot по CV-модели SigLIP2 (`zero_shot_classify()`) — ТОЛЬКО если
     энкодер модели несёт текстовую башню (reflection в приватные
     `encoder._model`/`_processor` пакета packages/cv — эта правка НЕ
     меняет packages/cv ни на строку, только читает уже загруженный объект
     через `image_index.encoder`, см. её докстринг).
  4. `status="unsure"`, `alternatives` — все 9 тегов (для чипов на фронте).

Пул/дедлайн/предохранитель модели — ПЕРЕИСПОЛЬЗОВАНЫ из `app/cv/service.py`
(`_FUSION_MODEL_POOL`, `_MODEL_BREAKERS`, `settings.vision_llm_timeout_s`) —
БУКВАЛЬНО те же объекты, не копия: бриф тимлида "шлюз лёг — и скан, и
блюдо его пропускают" реализуется тем, что оба потребителя делят ОДИН
предохранитель на модель ("vlm"/"vlm_local"). Этот модуль не меняет
`app/cv/service.py` ни на строку (зона `app/cv/service.py` — только по
брифу ML-лида, `.claude/agents/backend.md`).
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import Future

from rapidfuzz import fuzz, process

from .config import Settings
from .cv import vision_llm
from .cv.interface import ImageIndex
from .cv.service import _FUSION_MODEL_POOL, _MODEL_BREAKERS
from .food_pairing import portal_tags

logger = logging.getLogger(__name__)

NOT_FOOD_LABEL = "не еда"
BOTTLE_LABEL = "бутылка вина"

# НЕ тот же порог, что resolve_style (app/rag/mock.py, 60.0) — там fuzzy идёт
# против длинных названий стилей (несколько различающих токенов), здесь —
# против коротких (2-3 слова) тегов, где `token_set_ratio` систематически
# переоценивает совпадение по ОДНОМУ общему слову ("...кухня" эмпирически
# даёт 75-100% почти с любым текстом, содержащим "кухня", независимо от
# смысла — проверено вручную, см. reports/backend-dish-photo.md). 80.0 —
# эмпирический порог: пропускает реальные опечатки ("Азиятская кухня" 93.3%,
# "блюдо из птицы" 85.7%), отсекает подстрочное совпадение по одному общему
# слову ("итальянская кухня"/"марсианская кухня" -> "Азиатская кухня" 75.0%).
# Не герметично (вырожденный ввод вроде голого "кухня" всё ещё даст 100%) —
# известное ограничение эвристики, не калибровано на реальных данных.
_FUZZY_MATCH_THRESHOLD = 80.0

# Частые альтернативные формулировки -> канонический тег portal_tag_defaults.
# НЕ исчерпывающий словарь (v1, эвристика) — см. "Предложения" в
# reports/backend-dish-photo.md; неопознанное уходит на fuzzy, затем unsure.
_SYNONYMS: dict[str, str] = {
    "рыба": "Блюда из рыбы",
    "морепродукты": "Блюда из рыбы",
    "рыба и морепродукты": "Блюда из рыбы",
    "суши": "Блюда из рыбы",
    "птица": "Блюда из птицы",
    "курица": "Блюда из птицы",
    "утка": "Блюда из птицы",
    "индейка": "Блюда из птицы",
    "салат": "Салаты",
    "лёгкие салаты": "Салаты",
    "легкие салаты": "Салаты",
    "брускетта": "Брускетты",
    "тапас": "Брускетты",
    "закуски": "Брускетты",
    "гриль": "BBQ",
    "барбекю": "BBQ",
    "стейк": "BBQ",
    "мясо": "BBQ",
    "мясо и стейки": "BBQ",
    "азия": "Азиатская кухня",
    "азиатская": "Азиатская кухня",
    "тайская кухня": "Азиатская кухня",
    "китайская кухня": "Азиатская кухня",
    "паназиатская кухня": "Азиатская кухня",
    "десерт": "Выпечка и десерты",
    "десерты": "Выпечка и десерты",
    "торт": "Выпечка и десерты",
    "выпечка": "Выпечка и десерты",
    "сладкое": "Выпечка и десерты",
    "сыр": "Сыры",
    "сыры": "Сыры",
    "сырная тарелка": "Сыры",
    "твёрдые сыры": "Сыры",
    "твердые сыры": "Сыры",
    "устрица": "Устрицы",
    "устрицы": "Устрицы",
}


def _prompt(tags: list[str]) -> str:
    tag_list = ", ".join(f'"{t}"' for t in tags)
    return (
        "На фото может быть блюдо (еда) или что-то другое, например бутылка вина. "
        "Ответь строго одним JSON без пояснений и без markdown-разметки: "
        '{"is_food": true, "is_wine_bottle": false, "dish": "", "ingredients": [], '
        '"category": "", "alternatives": []}. '
        "is_food — true, если на фото блюдо/еда. is_wine_bottle — true, если на фото "
        "бутылка вина (этикетка, характерная форма бутылки) — это независимый признак, "
        "может быть true даже при is_food=false. dish — короткое название блюда "
        "по-русски, если это еда, иначе пустая строка. ingredients — до 5 ключевых "
        "ингредиентов по-русски, если различимы, иначе пустой список. category — РОВНО "
        f"один тег из списка ниже, который лучше всего подходит блюду; список: {tag_list}. "
        "Если не уверен, какой тег выбрать — оставь category пустой строкой. "
        "alternatives — до 2 других уместных тегов из того же списка, если сомневаешься "
        "между несколькими вариантами, иначе пустой список. Если на фото не еда и не "
        "бутылка вина — is_food и is_wine_bottle оба false, остальные поля пустые."
    )


def resolve_category(raw: str | None, tags: list[str]) -> str | None:
    """Приводит сырую строку категории (от VLM/zero-shot/пользователя) к
    одному из `tags` — точное совпадение (без учёта регистра) -> синонимы
    (`_SYNONYMS`) -> нечёткое (rapidfuzz, порог `_FUZZY_MATCH_THRESHOLD`) ->
    `None` (вызывающий код тогда честно деградирует в `unsure`)."""
    if not raw or not raw.strip() or not tags:
        return None
    text = raw.strip()
    text_cf = text.casefold()

    for tag in tags:
        if text_cf == tag.casefold():
            return tag

    synonym = _SYNONYMS.get(text_cf)
    if synonym in tags:
        return synonym

    best = process.extractOne(text, tags, scorer=fuzz.token_set_ratio)
    if best is not None and best[1] >= _FUZZY_MATCH_THRESHOLD:
        return best[0]
    return None


def _empty_dish(*, source: str, alternatives: list[str] | None = None) -> dict:
    return {
        "name": "", "category": None, "alternatives": alternatives or [],
        "ingredients": [], "source": source,
    }


def _ask_models(image_bytes: bytes, settings: Settings, prompt: str) -> tuple[dict, str]:
    """(распарсенный JSON ответа модели или `{}`, "vlm"|"vlm_local"|"none").

    Приоритет шлюза: submit ОБОИХ (если настроены и предохранитель
    разрешает) в `_FUSION_MODEL_POOL` (тот же пул, что сканер), ждём каждого
    до ОБЩЕГО дедлайна `t0 + VISION_LLM_TIMEOUT_S` (тот же якорь и то же имя
    настройки, что `app/cv/service.py::_fusion_text_and_vectors` — "дедлайн
    VISION_LLM_TIMEOUT_S" из брифа тимлида), затем — "берём ответ шлюза, если
    успел, иначе локальный" (НЕ склейка, в отличие от сканера)."""
    t0 = time.monotonic()
    deadline = t0 + settings.vision_llm_timeout_s
    cooldown_s = settings.vision_llm_breaker_cooldown_s
    fails_threshold = settings.vision_llm_breaker_fails

    futures: dict[str, Future] = {}
    if (
        settings.vision_llm_url and settings.vision_llm_key
        and _MODEL_BREAKERS["vlm"].allow(cooldown_s=cooldown_s)
    ):
        futures["vlm"] = _FUSION_MODEL_POOL.submit(
            vision_llm.ask_json_or_raise, image_bytes,
            url=settings.vision_llm_url, key=settings.vision_llm_key, model=settings.vision_llm_model,
            timeout_s=settings.vision_llm_timeout_s, prompt=prompt, image_size=settings.vision_llm_image_size,
        )
    if settings.vision_llm_local_url and _MODEL_BREAKERS["vlm_local"].allow(cooldown_s=cooldown_s):
        futures["vlm_local"] = _FUSION_MODEL_POOL.submit(
            vision_llm.ask_json_or_raise, image_bytes,
            url=settings.vision_llm_local_url, key=None, model=settings.vision_llm_local_model,
            timeout_s=settings.vision_llm_timeout_s, prompt=prompt, image_size=settings.vision_llm_image_size,
        )

    results: dict[str, dict] = {}
    for name, fut in futures.items():
        try:
            data = fut.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:  # noqa: BLE001 — не успела к дедлайну/сбой потока/HTTP-ошибка шлюза
            _MODEL_BREAKERS[name].record_failure(name=name, fails_threshold=fails_threshold, cooldown_s=cooldown_s)
            continue
        _MODEL_BREAKERS[name].record_success(name=name)  # пустой, но честный {} — не сбой
        if data:
            results[name] = data

    if "vlm" in results:
        return results["vlm"], "vlm"
    if "vlm_local" in results:
        return results["vlm_local"], "vlm_local"
    return {}, "none"


def _interpret_model_json(data: dict, tags: list[str], source: str) -> dict:
    is_wine_bottle = bool(data.get("is_wine_bottle"))
    is_food = bool(data.get("is_food"))

    if is_wine_bottle:
        return {"status": "bottle", "dish": _empty_dish(source=source)}
    if not is_food:
        return {"status": "not_food", "dish": _empty_dish(source=source)}

    raw_category = data.get("category")
    category = resolve_category(raw_category if isinstance(raw_category, str) else None, tags)
    name = str(data.get("dish") or "").strip()
    # openapi.yaml::DishInfo.ingredients: "до 5 позиций у source=vlm|vlm_local" —
    # промпт просит модель за этим следить, но не полагаемся на её дисциплину:
    # обрезаем сами (модель, вернувшая 8 позиций, не должна пробить контракт).
    ingredients = [str(x).strip() for x in (data.get("ingredients") or []) if str(x).strip()][:5]
    raw_alternatives = data.get("alternatives") or []
    resolved_alternatives = [
        resolved for a in raw_alternatives if isinstance(a, str)
        for resolved in [resolve_category(a, tags)] if resolved and resolved != category
    ]
    # openapi.yaml::DishInfo.alternatives: "0-2 тега... без повтора category" —
    # тот же принцип обрезки, что ingredients выше; dict.fromkeys — дедуп с
    # сохранением порядка (модель может назвать один тег дважды).
    alternatives = list(dict.fromkeys(resolved_alternatives))[:2]

    if category is None:
        # "иначе status=unsure и alternatives — все теги для чипов" (бриф
        # тимлида, раздел про zero-shot) — то же самое честное поведение
        # распространено и на путь VLM: тег вне списка и не восстановлен ни
        # синонимом, ни fuzzy — не выдумываем уверенность.
        return {
            "status": "unsure",
            "dish": {
                "name": name, "category": None, "alternatives": tags,
                "ingredients": ingredients, "source": source,
            },
        }
    return {
        "status": "food",
        "dish": {
            "name": name, "category": category, "alternatives": alternatives,
            "ingredients": ingredients, "source": source,
        },
    }


# --------------------------------------------------------------------------
# Запасной путь без модели — zero-shot по CV-модели SigLIP2 (если ENCODER
# несёт текстовую башню). `ImageIndex.embed()` — часть контракта
# (app/cv/interface.py, "эмбеддинг БЕЗ нормализации" — то, что нужно для
# фото блюда: `search()` нормализует под этикетку вина, это бы искажало фото
# блюда). Текстовая сторона (`_encode_texts`) reflection'ом читает
# `image_index.encoder._model`/`_processor` (packages/cv/cv/encoder.py::
# SiglipEncoder) — ТОЛЬКО чтение уже загруженного объекта, packages/cv не
# меняется ни на строку. `MockImageIndex` (тесты) не несёт атрибута
# `encoder` вовсе -> `getattr(..., None)` -> `None` -> честный `unsure`,
# без единого падения теста.
# --------------------------------------------------------------------------

_ZERO_SHOT_TEXT_CACHE: dict[str, dict[str, list[float]]] = {}


def _encode_texts(encoder: object, texts: list[str]) -> list[list[float]] | None:
    try:
        import numpy as np
        import torch
    except ImportError:
        return None
    try:
        encoder._load()  # тот же ленивый загрузчик, что encoder.encode() (packages/cv)
        model = getattr(encoder, "_model", None)
        processor = getattr(encoder, "_processor", None)
        if model is None or processor is None or not hasattr(model, "get_text_features"):
            return None
        inputs = processor(text=texts, padding="max_length", truncation=True, return_tensors="pt")
        inputs = {k: v.to(encoder.device) for k, v in inputs.items()}
        with torch.no_grad():
            output = model.get_text_features(**inputs)
        feats = output if isinstance(output, torch.Tensor) else output.pooler_output
        vecs = feats.to("cpu", dtype=torch.float32).numpy()
        result = []
        for row in vecs:
            norm = np.linalg.norm(row)
            if norm > 0:
                row = row / norm
            result.append([float(x) for x in row])
        return result
    except Exception:  # noqa: BLE001 — reflection в чужой (packages/cv) объект: любой сбой -> недоступно
        logger.warning("dish_recognition: текстовая башня SigLIP2 недоступна/сбоила — zero-shot пропущен")
        return None


def zero_shot_classify(
    image_index: ImageIndex, image_bytes: bytes, tags: list[str], *, margin: float,
) -> tuple[str, str] | None:
    """`(label, kind)`, kind ∈ {"tag", "not_food", "bottle"}; `None` — zero-shot
    недоступен (нет текстовой башни/сбой) ИЛИ разрыв топ1/топ2 меньше
    `margin` ("строго с порогом отрыва", бриф тимлида) — вызывающий код
    тогда честно деградирует в `unsure`."""
    encoder = getattr(image_index, "encoder", None)
    if encoder is None or not tags:
        return None

    labels = [*tags, NOT_FOOD_LABEL, BOTTLE_LABEL]
    cache_key = getattr(encoder, "model_name", None)
    text_embs = _ZERO_SHOT_TEXT_CACHE.get(cache_key) if cache_key else None
    if text_embs is None:
        encoded = _encode_texts(encoder, [f"фото: {label}" for label in labels])
        if encoded is None:
            return None
        text_embs = dict(zip(labels, encoded))
        if cache_key:
            _ZERO_SHOT_TEXT_CACHE[cache_key] = text_embs

    try:
        image_vec = image_index.embed(image_bytes)
    except Exception:  # noqa: BLE001 — битые байты и т.п. — роутер уже проверил непустой файл выше
        return None

    scored = sorted(
        ((label, sum(a * b for a, b in zip(image_vec, vec))) for label, vec in text_embs.items()),
        key=lambda pair: pair[1], reverse=True,
    )
    if len(scored) < 2:
        return None
    (top_label, top_score), (_, second_score) = scored[0], scored[1]
    if (top_score - second_score) < margin:
        return None

    if top_label == NOT_FOOD_LABEL:
        return top_label, "not_food"
    if top_label == BOTTLE_LABEL:
        return top_label, "bottle"
    return top_label, "tag"


def recognize_dish_photo(image_bytes: bytes, image_index: ImageIndex, settings: Settings) -> dict:
    """`{"status", "dish"}` — status ∈ food|not_food|bottle|unsure. Порядок
    путей — докстринг модуля."""
    tags = portal_tags(settings)
    prompt = _prompt(tags)
    data, source = _ask_models(image_bytes, settings, prompt)

    if data:
        return _interpret_model_json(data, tags, source)

    zero_shot = zero_shot_classify(image_index, image_bytes, tags, margin=settings.dish_zero_shot_margin)
    if zero_shot is not None:
        label, kind = zero_shot
        if kind == "not_food":
            return {"status": "not_food", "dish": _empty_dish(source="zero_shot")}
        if kind == "bottle":
            return {"status": "bottle", "dish": _empty_dish(source="zero_shot")}
        return {
            "status": "food",
            "dish": {
                "name": "", "category": label, "alternatives": [], "ingredients": [],
                "source": "zero_shot",
            },
        }

    return {"status": "unsure", "dish": _empty_dish(source="none", alternatives=tags)}
