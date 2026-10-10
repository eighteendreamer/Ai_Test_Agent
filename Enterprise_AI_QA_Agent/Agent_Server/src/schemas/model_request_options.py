from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class ModelOutputFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["json_object", "json_schema"]
    schema_definition: dict[str, Any] | None = None
    schema_name: str = Field(default="response", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    strict: bool | None = None
    required: bool = True

    @model_validator(mode="after")
    def validate_schema(self):
        if self.kind == "json_schema" and not self.schema_definition:
            raise ValueError("json_schema requires a nonempty schema_definition")
        if self.kind == "json_object" and (self.schema_definition is not None or self.strict is not None):
            raise ValueError("json_object does not accept schema_definition or strict")
        return self


class ModelReasoningOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effort: ReasoningEffort | None = None
    budget_tokens: int | None = Field(default=None, ge=0)
    required: bool = True

    @model_validator(mode="after")
    def validate_kind(self):
        if (self.effort is None) == (self.budget_tokens is None):
            raise ValueError("Set exactly one of effort and budget_tokens")
        return self


class ModelRequestProfile(BaseModel):
    """Explicit compatibility declarations, independent of legacy capability defaults."""
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    revision: int = Field(default=1, ge=1)
    output_formats: list[Literal["json_object", "json_schema"]] = Field(default_factory=list)
    chat_token_parameter: Literal["max_tokens", "max_completion_tokens"] | None = None
    reasoning_style: Literal["none", "effort", "budget"] = "none"
    reasoning_efforts: list[ReasoningEffort] = Field(default_factory=list)
    min_reasoning_budget: int = Field(default=0, ge=0)
    max_reasoning_budget: int | None = Field(default=None, ge=0)
    default_reasoning: ModelReasoningOptions | None = None

    @model_validator(mode="after")
    def validate_declarations(self):
        if self.max_reasoning_budget is not None and self.max_reasoning_budget < self.min_reasoning_budget:
            raise ValueError("max_reasoning_budget is smaller than min_reasoning_budget")
        if self.reasoning_style != "effort" and self.reasoning_efforts:
            raise ValueError("reasoning_efforts requires reasoning_style=effort")
        if self.default_reasoning:
            reason = self.default_reasoning
            if reason.effort is not None and (self.reasoning_style != "effort" or reason.effort not in self.reasoning_efforts):
                raise ValueError("default reasoning effort is not declared")
            if reason.budget_tokens is not None and (self.reasoning_style != "budget" or
                reason.budget_tokens < self.min_reasoning_budget or
                (self.max_reasoning_budget is not None and reason.budget_tokens > self.max_reasoning_budget)):
                raise ValueError("default reasoning budget is not declared")
        return self


def validate_profile_transport(profile: ModelRequestProfile | None, transport: str,
    *, max_output_tokens: int | None = None, temperature: float | None = None) -> None:
    if profile is None:
        return
    if profile.chat_token_parameter and transport != "openai_chat_completions":
        raise ValueError("chat_token_parameter requires openai_chat_completions")
    if transport == "anthropic_messages" and "json_object" in profile.output_formats:
        raise ValueError("Anthropic request profile supports json_schema, not json_object")
    allowed_style = "effort" if transport in {"openai_chat_completions", "openai_responses"} else "budget"
    if profile.reasoning_style not in {"none", allowed_style}:
        raise ValueError(f"reasoning_style={profile.reasoning_style} is not mapped for transport={transport}")
    if transport == "anthropic_messages" and profile.default_reasoning is not None:
        budget = profile.default_reasoning.budget_tokens
        if budget and (budget < 1024 or (max_output_tokens is not None and budget >= max_output_tokens)):
            raise ValueError("Anthropic default reasoning budget must be at least 1024 and smaller than max_output_tokens")
        if budget and temperature not in {None, 1.0}:
            raise ValueError("Anthropic manual thinking requires temperature=1 or omitted")
