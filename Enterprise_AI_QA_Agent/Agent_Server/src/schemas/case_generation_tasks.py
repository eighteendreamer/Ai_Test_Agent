"""Leased module tasks over immutable confirmed generation scope objects."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.case_generation import GenerationSnapshot, IncrementalGenerationRequest
from src.schemas.document_differences import UUID_PATTERN
from src.schemas.model_config import ModelTransportOptions
from src.schemas.generation_receipts import GenerationObject, GenerationReceipt, GenerationVersionReference


class GenerationTaskPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: float = Field(default=600, gt=0, le=86400)
    call_timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_input_bytes: int = Field(default=256000, ge=1, le=10000000)
    max_response_bytes: int = Field(default=8000000, ge=1, le=100000000)
    framing_allowance_tokens: int = Field(default=1024, ge=0, le=100000)
    transport_policy: Literal["provider_defaults", "single_attempt"] = "provider_defaults"

    @property
    def transport_options(self):
        return ModelTransportOptions(timeout_seconds=self.call_timeout_seconds, max_retries=0) if self.transport_policy == "single_attempt" else None


class GenerationTaskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_key: str
    revision: int = Field(ge=1)
    payload: GenerationTaskPolicy


class GenerationTaskRequest(IncrementalGenerationRequest):
    creation_key: str = Field(min_length=1, max_length=160)


class GenerationRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    creation_key: str = Field(min_length=1, max_length=160)


class GenerationRetryReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=UUID_PATTERN)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationGroup(GenerationSnapshot):
    id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    version_id: str = Field(pattern=UUID_PATTERN)
    creation_key: str
    request_hash: str
    scope: GenerationObject
    scope_sha256: str
    module_ids: list[str] = Field(min_length=1)
    config: GenerationTaskSettings
    created_at: datetime
    sha256: str

    @model_validator(mode="after")
    def validate_fixed(self):
        return self.check_hash("sha256")


class GenerationTaskSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    document_id: str
    version_id: str
    group_id: str
    group_sha256: str
    module_id: str
    module_sha256: str
    config: GenerationTaskSettings
    retry: GenerationRetryReference | None = None


class GenerationRetry(GenerationSnapshot):
    implementation_version: Literal["generation-retry-v1"] = "generation-retry-v1"
    id: str = Field(pattern=UUID_PATTERN)
    task_id: str = Field(pattern=UUID_PATTERN)
    parent_task_id: str = Field(pattern=UUID_PATTERN)
    parent_task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    group_id: str = Field(pattern=UUID_PATTERN)
    group_sha256: str
    scope_sha256: str
    module_id: str = Field(pattern=UUID_PATTERN)
    module_sha256: str
    creation_key: str
    config: GenerationTaskSettings
    strategy: Literal["frozen_request", "saved_response"]
    response: GenerationObject | None
    created_at: datetime
    sha256: str

    @model_validator(mode="after")
    def validate_fixed(self):
        if (self.strategy == "saved_response") != (self.response is not None):
            raise ValueError("重试策略与原持久响应不同")
        return self.check_hash("sha256")


class GenerationModuleCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["invoking", "response_saved", "result_saved"]
    invocation_attempt_id: str | None = None
    budget: dict = Field(default_factory=dict)
    response: GenerationObject | None = None
    receipt: dict | None = None


class GenerationTaskRecord(BaseModel):
    id: str
    project_id: str
    document_id: str
    version_id: str
    group_id: str
    module_id: str
    creation_key: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: Literal["queued", "generating", "invoking", "response_saved", "result_saved", "finished"]
    message: str
    attempt_no: int
    checkpoint: GenerationModuleCheckpoint | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None = None
    completed_at: datetime | None = None


class GenerationTaskPage(BaseModel):
    items: list[GenerationTaskRecord]
    has_more: bool
    limit: int
    offset: int


class GenerationTaskClaim(BaseModel):
    task: GenerationTaskRecord
    seed: GenerationTaskSeed
    attempt_id: str
    worker_id: str
    lease_token: str
    remaining_seconds: float


class GenerationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_type: Literal["case_generation"] = "case_generation"
    project_id: str
    task_id: str


class GenerationCheckpointView(GenerationModuleCheckpoint):
    receipt: GenerationReceipt | None = None


class GenerationTaskView(GenerationTaskRecord):
    checkpoint: GenerationCheckpointView | None = None


class GenerationGroupView(BaseModel):
    group: GenerationGroup
    modules: list[GenerationTaskView]


class GenerationGroupPage(BaseModel):
    items: list[GenerationGroup]
    has_more: bool
    limit: int
    offset: int


class GenerationRetryView(BaseModel):
    retry: GenerationRetry
    task: GenerationTaskView


class GenerationRetryPage(BaseModel):
    items: list[GenerationRetryView]
    has_more: bool
    limit: int
    offset: int
