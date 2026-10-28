"""Validated cognitive guidance, never a Runner/test verdict or recovery proof.

No upstream counterpart (checked: Deep Agents 0.7.13 response_format,
LangChain 1.4.6 ToolStrategy, platform ReviewReportSkeleton, v4 section 9.3):
the SDK validates a supplied type, but the platform owns its result contract.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


STRUCTURED_OUTPUT_VERSION_KEY = "framework_structured_output_version"


class FrameworkCognitiveResult(BaseModel):
    """Advisory model output; evidence references are claims, not permissions."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    scope: Literal["cognitive_guidance_only"]
    summary: str = Field(min_length=1)
    evidence_refs: list[str]
    limitations: list[str]

    @field_validator("schema_version", mode="before")
    @classmethod
    def require_integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Cognitive result requires its explicit integer version")
        return value

    @field_validator("summary")
    @classmethod
    def require_summary(cls, value):
        if not value.strip():
            raise ValueError("Cognitive result summary cannot be blank")
        return value


def require_structured_output_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError("Unsupported cognitive structured output version")
    return value


def validate_cognitive_completion(context, summary):
    """Validate the selected result, without authenticating its model claims."""
    version = require_structured_output_version(context.get(STRUCTURED_OUTPUT_VERSION_KEY))
    if (version is not None or "structured_output_version" in summary or "structured_result" in summary):
        if (version is None or type(summary.get("structured_output_version")) is not int
            or summary["structured_output_version"] != version):
            raise ValueError("Cognitive completion differs from its frozen output contract")
        return FrameworkCognitiveResult.model_validate(summary.get("structured_result"))
    return None
