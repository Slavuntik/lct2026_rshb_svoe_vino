"""Pydantic v2 модели запросов/ответов — по contracts/openapi.yaml v0.2."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

ConsentScope = Literal["base", "profiling", "geo", "marketing"]


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


class WineResponse(BaseModel):
    wine_id: str
    source: dict
    derived: dict
    source_url: str
    similar: list[str] = Field(default_factory=list)


class ChatFilters(BaseModel):
    color: str | None = None
    sugar: str | None = None
    region: str | None = None
    # budget_rub_max убран в v0.2 (ревью 01, блокер 4): цен в каталоге нет.


class ChatRequest(BaseModel):
    message: str = Field(max_length=1000)
    filters: ChatFilters | None = None


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
    winery_name: str
    region_name: str


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
    top_styles: list[str]
    swipes_count: int


class WaitlistRequest(BaseModel):
    email: EmailStr
    consent_version: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    index_version: str
