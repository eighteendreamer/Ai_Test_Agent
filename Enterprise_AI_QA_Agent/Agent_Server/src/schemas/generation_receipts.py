"""Generation receipts shared with requirement coverage, without scope imports."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.generation_coverage import GenerationCoverageReview


class GenerationObject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)


class GenerationVersionReference(BaseModel):
    case_id: str
    version_id: str
    version: int
    content_hash: str


class GenerationReceipt(BaseModel):
    module_id: str
    kind: Literal["update", "create", "retirement_candidate"]
    scope_sha256: str
    response: GenerationObject | None
    versions: list[GenerationVersionReference]
    retirement_target: dict | None
    generation_review: GenerationCoverageReview | None = None
