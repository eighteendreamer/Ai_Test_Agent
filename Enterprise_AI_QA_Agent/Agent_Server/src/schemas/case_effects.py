"""Explicit literal evidence declarations, independent of tools and modes."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer


class EffectEvidenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    expected_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    operator: Literal["equals", "not_equals", "contains", "not_contains", "lt", "lte", "gt", "gte"]


class EffectOperationSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    expected_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)


class EffectEvidenceDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["produced", "cleanup"]
    object_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    collection: bool = False
    checks: list[EffectEvidenceCheck] = Field(min_length=1, max_length=200)
    select_when: list[EffectOperationSelector] | None = Field(default=None, min_length=1, max_length=20)

    @model_serializer(mode="wrap")
    def preserve_original_declaration(self, handler):
        data = handler(self)
        if self.select_when is None:
            data.pop("select_when", None)
        return data
