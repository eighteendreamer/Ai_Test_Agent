"""Explicit literal evidence declarations, independent of tools and modes."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator


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


class EffectSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_key: str = Field(min_length=1, max_length=80)
    declaration_id: str = Field(min_length=1, max_length=80)


class EffectRelationDeclaration(EffectEvidenceDeclaration):
    declaration_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    select_when: list[EffectOperationSelector] = Field(min_length=1, max_length=20)
    sources: list[EffectSourceReference] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def require_role_sources(self):
        if bool(self.sources) != (self.role == "cleanup"):
            raise ValueError("多效果清理声明需要产生来源；产生声明不能引用清理来源")
        pairs = [(ref.item_key, ref.declaration_id) for ref in self.sources]
        if len(pairs) != len(set(pairs)):
            raise ValueError("多效果来源声明重复")
        return self
