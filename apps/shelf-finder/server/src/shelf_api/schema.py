"""Public API types; catalog embeddings and image contents are never returned."""

from typing import Literal
from pydantic import BaseModel, Field, FiniteFloat


class ImageSize(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ShelfMatch(BaseModel):
    box: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    wineId: str
    name: str
    alternativeWineIds: list[str] = Field(default_factory=list)
    identificationLevel: Literal["product", "shared-reference"] = "product"


class ScanResponse(BaseModel):
    requestId: str
    pipelineVersion: str
    catalogVersion: str
    image: ImageSize
    detectedCount: int = Field(ge=0, le=80)
    matches: list[ShelfMatch]
    timingsMs: dict[str, FiniteFloat]
    warnings: list[str]


class HealthResponse(BaseModel):
    ready: bool
    busy: bool
    state: Literal["ready", "warming", "failed"]
    catalogSize: int

    device: str = "auto"
    profile: str = "baseline"
    scanTimeoutSeconds: int = Field(default=300, ge=15, le=600)
