"""Explicit literal evidence declarations, independent of tools and modes."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class EffectEvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    expected_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    operator: Literal["equals", "not_equals", "contains", "not_contains", "lt", "lte", "gt", "gte"]


class EffectEvidenceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["produced", "cleanup"]
    object_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    collection: bool = False
    checks: list[EffectEvidenceCheck] = Field(min_length=1, max_length=200)
