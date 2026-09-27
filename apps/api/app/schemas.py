"""Pydantic v2 модели запросов/ответов — по contracts/openapi.yaml v0.2."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, get_args

from pydantic import BaseModel, EmailStr, Field

ConsentScope = Literal["base", "profiling", "geo", "marketing"]
# v0.3: /consents POST валидирует scopes вручную в роутере (не через pydantic
# Literal) — контракт требует именно 400 validation_error на неизвестном
# скоупе, а автоматический pydantic-422 даёт другой код ответа. Список тот
# же словарь, что и ConsentScope выше — единственный источник.
CONSENT_SCOPES: frozenset[str] = frozenset(get_args(ConsentScope))


class TokenPair(BaseModel):
    access_token: str
    expires_in: int | None = None


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    birth_date: date
    consent_version: str
    consent_scopes: list[ConsentScope]


class LoginRequest(BaseModel):
    email: str
    password: str


class GuestRequest(BaseModel):
    age_confirmed: bool
    consent_version: str


class ConsentPostRequest(BaseModel):
    consent_version: str
    grant: bool
    scopes: list[str]


class ConsentStateItem(BaseModel):
    scope: str
    consent_version: str
    granted: bool
    at: datetime


class ScanHints(BaseModel):
    color: str | None = None
    winery: str | None = None


class ScanResolveRequest(BaseModel):
    text: str = Field(max_length=2000)
    hints: ScanHints | None = None


class ScanMatch(BaseModel):
    wine_id: str
    name: str
    winery_name: str
    confidence: float = Field(ge=0, le=1)


class ScanResolveResponse(BaseModel):
    matches: list[ScanMatch] = Field(max_length=5)
    low_confidence: bool
    # agents/B7-foreign-analogs.md: фолбэк на пустых matches — узнанный по
    # pipeline/ref (сорт/стиль) токен уходит в тот же резолвер стиля, что и
    # /v1/analogs (routers/analogs.py), и возвращает российские аналоги.
    # Пусто/None, когда токен не распознан — старое поведение не меняется.
    # НЕ в contracts/openapi.yaml — контракты правит только оркестратор (см.
    # reports/b7-foreign-analogs.md, "Предложения к контрактам"); задокумен-
    # тированное исключение — tests/test_openapi_contract.py,
    # _KNOWN_UNDOCUMENTED_RESPONSE_FIELDS.
    analogs: list[AnalogsWineItem] = Field(default_factory=list)
    analog_reason: str | None = None


class SimilarWineItem(BaseModel):
    """contracts/openapi.yaml v0.3.6 — item формы `similar_wines` (см. ниже):
    те же слаги/порядок, что `similar`, обогащённые именем/винодельней/фото.
    `winery` — отображаемое имя (source.winery_name, фолбэк — слаг
    source.winery), тот же приём, что `PairingWineItem.winery`."""
    wine_id: str
    name: str
    winery: str | None = None
    image_url: str | None = None


class WineResponse(BaseModel):
    wine_id: str
    source: dict
    derived: dict
    source_url: str
    # DEPRECATED (contracts/openapi.yaml v0.3.6) — голые слаги, без имени/
    # винодельни (дефект жюри, reports/qa-manual-hack-v16.md п.5.1). Оставлено
    # для обратной совместимости — новые клиенты читают similar_wines ниже.
    similar: list[str] = Field(default_factory=list)
    # v0.3.6 — те же слаги и тот же порядок, что similar; слаг без пригодного
    # имени в каталоге в similar_wines не попадает, но остаётся в similar
    # (app/rag/cards.py::build_wine_card, "similar_wines" может быть короче).
    similar_wines: list[SimilarWineItem] = Field(default_factory=list)


# --- GET /v1/wines/{wine_id}/pairings (contracts/post-scan.md v1.0, 22.09) --

class TriggeredRule(BaseModel):
    id: str
    explain: str  # дословно rules[].explain из pipeline/ref/food_pairing_rules.yaml


class WinePairingItem(BaseModel):
    tag: str
    # null при basis=catalog (contracts/openapi.yaml 0.3.3) — свой текст
    # портала, не скорингованный нашим движком.
    score: float | None = Field(default=None, ge=0, le=1)
    triggered_rules: list[TriggeredRule] = Field(default_factory=list)


class WinePairingsResponse(BaseModel):
    wine_id: str
    basis: Literal["catalog", "sensory", "heuristic", "unavailable"]
    pairings: list[WinePairingItem] = Field(max_length=3)
    message: str | None = None  # непусто ⟺ pairings=[]


# --- POST /v1/pairing/dish-photo, POST /v1/pairing/dish --------------------
# "Что подать" по фото блюда (contracts/post-scan.md v1.1 §4, contracts/
# openapi.yaml 0.3.4, 22.09, architect — ратифицировано; имена схем/полей
# ниже сверены построчно с openapi.yaml::components.schemas.{DishInfo,
# PairingWineItem,DishPairingResponse}, см. reports/backend-dish-photo.md,
# "Расхождения с контрактом" — два предложения к контракту зафиксированы там
# (пул кандидатов, порог нечёткого совпадения), схема ответа ниже совпадает
# с openapi.yaml дословно).

class DishInfo(BaseModel):
    # openapi.yaml::DishInfo.name: "НЕ null — пустая строка, когда нечего
    # показать" — поле держим ненуллабельным сознательно (не str | None).
    name: str = ""
    # category: null, если status != food (не резолвлен/не применим).
    category: str | None = None
    alternatives: list[str] = Field(default_factory=list)
    ingredients: list[str] = Field(default_factory=list)
    source: Literal["vlm", "vlm_local", "zero_shot", "user", "none"]


class PairingWineItem(BaseModel):
    wine_id: str
    name: str
    winery: str | None = None
    color: str | None = None
    sugar: str | None = None
    image_url: str | None = None
    reason: str  # детерминированный текст — из шаблона (catalog) либо rules[].explain (rules)
    basis: Literal["catalog", "rules"]


class DishPairingResponse(BaseModel):
    status: Literal["food", "not_food", "bottle", "unsure"]
    dish: DishInfo
    wines: list[PairingWineItem] = Field(default_factory=list, max_length=6)
    message: str | None = None
    timing_ms: int


class DishManualRequest(BaseModel):
    """`POST /v1/pairing/dish` — ручной выбор/исправление категории.
    `category` — свободный текст (валидируется/приводится к одному из 9
    тегов `app/dish_recognition.py::resolve_category`, 400 validation_error
    на нераспознанном)."""
    category: str = Field(max_length=100)
    dish: str | None = Field(default=None, max_length=200)


class ChatFilters(BaseModel):
    color: str | None = None
    sugar: str | None = None
    region: str | None = None
    # budget_rub_max убран в v0.2 (ревью 01, блокер 4): цен в каталоге нет.


class ChatRequest(BaseModel):
    message: str = Field(max_length=1000)
    filters: ChatFilters | None = None
    # openapi 0.3.5 (architect, 22.09, задача тимлида по reports/backend-rag-rebuild.md
    # п.3-4): слаг вина, которое фронт уже показывает (скан/карточка) — опционален,
    # пустая строка трактуется как отсутствие (app/chat/service.py::stream_chat_events).
    wine_id: str | None = Field(default=None, max_length=200)


class ChatFeedbackRequest(BaseModel):
    answer_id: str
    verdict: Literal["up", "down"]


class AnalogsFilters(BaseModel):
    region: str | None = None
    sugar: str | None = None


class AnalogsRequest(BaseModel):
    query: str = Field(max_length=200)
    filters: AnalogsFilters | None = None


class AnalogsStyle(BaseModel):
    slug: str
    name: str
    country: str


class AnalogsWineItem(BaseModel):
    wine_id: str
    name: str
    # Хотфикс (оркестратор, полный прогон реального RAG, 92/1982 rich-ответов
    # 500-ли): ~92 вина боевого каталога не несут винодельню — было `str`
    # (обязательное), pydantic ронял ValidationError на живом None ->
    # HTTP 500 в rich-ответе /scan/photo (routers/scan.py, AnalogsWineItem(
    # **item) для similar/analogs). Честный Optional, не пустая строка —
    # пустая строка тоже была бы "фиксом", но исказила бы факт "винодельня
    # неизвестна" под "винодельня — пустая строка".
    winery_name: str | None = None
    # Тот же класс дыры, соседние поля (финальный прогон 17.09: пара
    # chateau-de-talu «Уроки французского» 500-ила rich двумя запросами из 1982):
    # у единичных вин каталога пусты name/region_name. region — честный Optional;
    # безымянный аналог отбрасывается строителем ДО схемы (карточку без имени
    # не отрендерить), поэтому name остаётся обязательным.
    region_name: str | None = None


class AnalogsResponse(BaseModel):
    style: AnalogsStyle
    wines: list[AnalogsWineItem] = Field(max_length=12)


class EventRequest(BaseModel):
    name: str
    props: dict = Field(default_factory=dict)


class SwipeRequest(BaseModel):
    wine_id: str
    verdict: Literal["like", "dislike", "skip"]


class TasteCandidateItem(BaseModel):
    wine_id: str
    name: str
    winery_name: str
    region_name: str | None = None
    color: str | None = None
    image_url: str | None = None


class TasteCandidatesResponse(BaseModel):
    wines: list[TasteCandidateItem]


class TasteProfileResponse(BaseModel):
    vector: dict[str, float]
    # DEPRECATED (contracts/openapi.yaml v0.3.6) — голые слаги эталонных
    # стилей, без имени/страны (тот же класс дефекта, что WineResponse.similar
    # — найдено аудитом architect по жалобе жюри, не отдельной жалобой на этот
    # эндпоинт). Оставлено для обратной совместимости — новые клиенты читают
    # top_styles_named ниже.
    top_styles: list[str]
    # v0.3.6 — те же слаги и тот же порядок, что top_styles, форма как у
    # list_reference_styles()/AnalogsStyle (contracts/rag-interface.md);
    # переиспользуем AnalogsStyle 1:1 — та же сущность "эталонный стиль".
    # Слаг вне справочника стилей в top_styles_named не попадает, но
    # остаётся в top_styles (routers/taste.py::_resolve_style_names).
    top_styles_named: list[AnalogsStyle] = Field(default_factory=list)
    swipes_count: int


# --- GET /v1/catalog (задача тимлида 27.09, "Каталог вин" по макету Figma) -

class CatalogItem(BaseModel):
    """Карточка плитки каталога — та же форма полей, что `PairingWineItem`
    (winery/color/sugar/image_url), без reason/basis (тут не подбор, а
    список): один источник имени полей на оба места."""
    wine_id: str
    name: str
    winery: str | None = None
    color: str | None = None
    sugar: str | None = None
    # null, когда у слага нет реального файла превью в CASE_DATA_DIR/thumbs
    # (app/rag/case_catalog.py::has_thumb) — вино не выбрасывается из
    # выдачи, фронт рисует заглушку (задание тимлида 27.09).
    image_url: str | None = None


class CatalogResponse(BaseModel):
    wines: list[CatalogItem]
    # Кол-во позиций ПОСЛЕ q/color/sugar, ДО limit/offset — фронту посчитать
    # число страниц/показать "N вин найдено", не требуя отдельного запроса.
    total: int
    limit: int
    offset: int


class WaitlistRequest(BaseModel):
    email: EmailStr
    consent_version: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    # v0.4.7 (контракт §6, TODO-3 ревью 05): index_version — DEPRECATED-алиас
    # rag_index_version (до v0.5, contracts/openapi.yaml). Поле было
    # неоднозначным при двух провайдерах версий (RAG-ретривер и CV-индекс
    # кейса) — ревью 05 поймало это ложной тревогой на прогоне B3 ("index_version
    # смотрит на RAG, а не на CV — не значит, что CV на моке", reports/
    # 05-dataset-wave.md). Оставлен как есть (не удалён) — только ради обратной
    # совместимости существующих клиентов до v0.5.
    index_version: str
    rag_index_version: str
    cv_index_version: str
    # v0.4.4 (ревью 04, блокер 2): готовность CV-энкодера И (v0.4.7, TODO-1)
    # OCR-верификатора — AND обоих прогревов (app/cv/factory.py::
    # warm_up_image_index/warm_up_label_verifier). True сразу, если
    # соответствующий провайдер — mock (нечего греть); на real — True только
    # после успешного прогрева при старте процесса, False если прогрев не
    # удался или ещё идёт.
    warm: bool = True


# --- Кейс ЛЦТ: сканер по фото (contracts/image-scan.md v0.4) --------------

class ScanPhotoFlatResponse(BaseModel):
    """Режим скрипта оценки: РОВНО {"slug": "..."}, ничего лишнего."""
    slug: str


class ScanConfidence(BaseModel):
    top1_score: float | None = None
    gap: float | None = None
    f1_top1: float | None = None
    f1_top5: float | None = None
    # Честное поле по заданию оркестратора: пока нет eval-отчёта (датасет
    # кейса не приехал) — f1_* приходят null, а не притворяются нулевым F1.
    eval_missing: bool = False


class PhotoMatchItem(BaseModel):
    """v0.4.3 (пробел нашёл F): сырая ANN-позиция для eval-раннера, НЕ карточка
    вина (в отличие от `ScanMatch` текстового /scan/resolve выше) — только то,
    что нужно посчитать F1-top5 сравнением с закрытой таблицей: slug и score."""
    slug: str
    score: float


class ScanCandidateItem(BaseModel):
    """v0.4.11 (агент B8): top-5 схлопнутых позиций при неуверенности —
    "возможно, это одно из" (та же капа top-5, что и у `PhotoMatchItem`, но
    это уже КАРТОЧКА-СВОДКА, не голый {slug, score} для eval). Данные — из
    нашей карточки (RAG), иначе из каталога кейса (`app/rag/case_catalog.py`)
    — та же деградация, что и у `card`/`GET /wines/{id}`
    (см. app/rag/cards.py::build_wine_card, app/cv/service.py::_candidate_item).
    winery_name/region_name/image_url — Optional по тому же прецеденту, что
    AnalogsWineItem выше (реальный каталог не всегда знает винодельню/регион)."""
    wine_id: str
    name: str
    winery_name: str | None = None
    region_name: str | None = None
    image_url: str | None = None
    source_url: str
    score: float


class ScanPhotoRichResponse(BaseModel):
    slug: str | None
    card: dict | None = None
    confidence: ScanConfidence
    ocr_verified: bool
    timing_ms: int
    not_in_catalog: bool
    similar: list[AnalogsWineItem] = Field(default_factory=list)
    analogs: list[AnalogsWineItem] = Field(default_factory=list)
    # v0.4.3: top-5 схлопнутых позиций по убыванию — UI не рендерит, нужно
    # исключительно eval-раннеру (без этого поля F1-top5 через живой API
    # вырождается в F1-top1). flat-режим (ScanPhotoFlatResponse) не меняется.
    matches: list[PhotoMatchItem] = Field(default_factory=list, max_length=5)
    # v0.4.11: top-5 кандидатов при неуверенности, обогащённых карточкой.
    # Поле есть ВСЕГДА (не только при not_in_catalog=true) — тот же принцип,
    # что и у matches: UI решает, когда его показывать ("Возможно, это одно
    # из:" при not_in_catalog), бэкенд не скрывает данные заранее.
    candidates: list[ScanCandidateItem] = Field(default_factory=list, max_length=5)


class ScanMetricsResponse(BaseModel):
    index_version: str | None = None
    f1_top1: float | None = None
    f1_top5: float | None = None
    match_rate: float | None = None
    eval_set: str | None = None
    measured_at: str | None = None
