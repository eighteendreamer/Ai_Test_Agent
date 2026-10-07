"""Explicit references to already-frozen document evidence; v2 stays unchanged."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from src.schemas.case_effects import EffectRelationDeclaration

SOURCE_PROTOCOL = "case-effect-document-expected-v1"
SOURCE_PROMPT_VERSION = "case-planning-document-expected-v6"


class DocumentExpectedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1, max_length=512)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    excerpt: str | None = Field(default=None, min_length=1, max_length=8000)
    literal: str | None = Field(default=None, min_length=1, max_length=8000)
    encoding: Literal["text", "json"] | None = None

    @model_validator(mode="after")
    def exact_text_reference(self):
        values = (self.excerpt, self.literal, self.encoding)
        if any(value is not None for value in values) and not all(value is not None for value in values):
            raise ValueError("原文标量引用需要摘录、字面值和解码方式")
        return self


class SourceExpectedValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_path_tokens: list[Annotated[str, Field(max_length=256)]] | None = Field(default=None, max_length=32)
    expected_source: DocumentExpectedReference | None = None

    @model_validator(mode="after")
    def one_expected_source(self):
        if (self.expected_path_tokens is None) == (self.expected_source is None):
            raise ValueError("预期必须引用自身路径或固定文档之一")
        return self

    @model_serializer(mode="wrap")
    def omit_unused_source(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class SourceEffectCheck(SourceExpectedValue):
    output_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)
    operator: Literal["equals", "not_equals", "contains", "not_contains", "lt", "lte", "gt", "gte"]


class SourceOperationSelector(SourceExpectedValue):
    input_path_tokens: list[Annotated[str, Field(max_length=256)]] = Field(max_length=32)


class SourceEffectRelationDeclaration(EffectRelationDeclaration):
    checks: list[SourceEffectCheck] = Field(min_length=1, max_length=200)
    select_when: list[SourceOperationSelector] = Field(min_length=1, max_length=20)
