from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, StrictInt, model_validator

from src.schemas.document_impact_batches import ImpactBatchModel
from src.schemas.model_config import ModelInvocationRequest, ModelTransportOptions
from src.schemas.tool_runtime import ModelToolCall


class DocumentImpactInvocationPolicy(ImpactBatchModel):
    max_logical_calls: int = Field(default=200, ge=1, le=10000)
    max_reserved_estimated_tokens: int = Field(default=2_000_000, ge=1, le=100_000_000)
    call_timeout_seconds: float = Field(default=120, gt=0, le=600)
    transport_policy: Literal["provider_defaults", "single_attempt"] = "provider_defaults"
    max_response_bytes: int = Field(default=8_000_000, ge=1, le=100_000_000)
    token_method: Literal["utf8_bytes_estimate", "tiktoken_content"] = "utf8_bytes_estimate"
    encoding_name: Literal["cl100k_base", "o200k_base"] | None = None
    framing_allowance_tokens: int = Field(default=1024, ge=0, le=100000)

    @model_validator(mode="after")
    def validate_encoding(self):
        if (self.token_method == "tiktoken_content") != (self.encoding_name is not None):
            raise ValueError("tiktoken内容计量必须明确编码；字节估算不能声明编码")
        return self

    @property
    def transport_options(self):
        if self.transport_policy == "single_attempt":
            return ModelTransportOptions(timeout_seconds=self.call_timeout_seconds, max_retries=0)
        return None


class DocumentImpactInvocationSettings(ImpactBatchModel):
    scope_key: str
    revision: int
    payload: DocumentImpactInvocationPolicy


class DocumentImpactInvocationSnapshot(ImpactBatchModel):
    config: DocumentImpactInvocationSettings
    tokenizer_version: str | None
    runtime_parameters: dict[str, Any]


class DocumentImpactUnit(ImpactBatchModel):
    kind: Literal["segment", "case", "difference"]
    id: str = Field(min_length=1)


class DocumentImpactRangeRead(ImpactBatchModel):
    source_id: str
    start_char: StrictInt = Field(ge=0)
    end_char: StrictInt = Field(ge=0)


class DocumentImpactCallSelection(ImpactBatchModel):
    unit: DocumentImpactUnit
    segment_ids: list[str] = Field(default_factory=list, max_length=100)
    case_version_ids: list[str] = Field(default_factory=list, max_length=100)
    difference_ids: list[str] = Field(default_factory=list, max_length=100)
    ranges: list[DocumentImpactRangeRead] = Field(default_factory=list, max_length=100)
    previous_call_id: str | None = None
    retry_of_call_id: str | None = None

    @model_validator(mode="after")
    def validate_reads(self):
        for items in (self.segment_ids, self.case_version_ids, self.difference_ids):
            if len(items) != len(set(items)):
                raise ValueError("影响调用固定读取不能重复")
        if self.previous_call_id and self.retry_of_call_id:
            raise ValueError("影响调用不能同时是继续读取和未知结果重试")
        return self


class DocumentImpactTokenEstimate(ImpactBatchModel):
    implementation_version: Literal["impact-content-budget-v1"] = "impact-content-budget-v1"
    method: Literal["utf8_bytes_estimate", "tiktoken_content"]
    encoding_name: str | None
    tokenizer_version: str | None
    content_tokens_or_estimate: int = Field(ge=0)
    framing_allowance_tokens: int = Field(ge=0)
    input_estimated_tokens: int = Field(ge=0)
    output_reserved_tokens: int = Field(ge=1)
    reserved_estimated_tokens: int = Field(ge=1)
    canonical_input_bytes: int = Field(ge=1)
    # Even an exact content encoding does not count provider framing or prove model mapping.
    provider_token_measurement: Literal["estimate"] = "estimate"

    @model_validator(mode="after")
    def validate_totals(self):
        if (self.input_estimated_tokens != self.content_tokens_or_estimate + self.framing_allowance_tokens or
            self.reserved_estimated_tokens != self.input_estimated_tokens + self.output_reserved_tokens):
            raise ValueError("影响调用预算汇总与计量不同")
        return self


class DocumentImpactCallInput(ImpactBatchModel):
    implementation_version: Literal["impact-call-input-v1"] = "impact-call-input-v1"
    input_sha256: str
    manifest_sha256: str
    selection: DocumentImpactCallSelection
    request: ModelInvocationRequest
    budget: DocumentImpactTokenEstimate


class DocumentImpactCallObject(ImpactBatchModel):
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)


class DocumentImpactCallResponse(ImpactBatchModel):
    call_id: str
    request_sha256: str
    text: str
    tool_calls: list[ModelToolCall]
    metadata: dict[str, Any]
    # SDK-produced application content, not HTTP headers or request credentials.
    raw_response: dict[str, Any]


class DocumentImpactCallRecord(ImpactBatchModel):
    id: str
    task_id: str
    call_key: str
    selection_hash: str
    selection: DocumentImpactCallSelection
    request: DocumentImpactCallObject
    budget: DocumentImpactTokenEstimate
    phase: Literal["ready", "invoking", "response_saved", "outcome_unknown", "response_unavailable"]
    invocation_attempt_id: str | None = None
    response: DocumentImpactCallObject | None = None
    response_disposition: Literal["received", "provider_failure", "tool_calls", "truncated", "empty"] | None = None
    outcome_code: str | None = None
    created_at: datetime
    updated_at: datetime
    # Logical reservation is not a transmission count or a billing guarantee.
    transport_attempts: Literal["not_observed"] = "not_observed"

    @model_validator(mode="after")
    def validate_state(self):
        if self.phase != "ready" and not self.invocation_attempt_id:
            raise ValueError("影响调用状态缺少调用attempt")
        if (self.phase == "response_saved") != (self.response is not None):
            raise ValueError("影响调用状态与响应对象不一致")
        return self
