from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TestCaseLifecycleStatus = Literal["draft", "pending_review", "active", "disabled", "archived"]
TestCasePriority = Literal["P0", "P1", "P2", "P3"]


class TestCaseSourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=256)
    version: str | None = Field(default=None, max_length=160)
    label: str = Field(default="", max_length=240)
    uri: str | None = Field(default=None, max_length=2048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TestCaseStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1)
    action: str = Field(min_length=1, max_length=4000)
    expected: str | None = Field(default=None, max_length=4000)
    kind: str = Field(default="action", min_length=1, max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)


class TestCaseAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    target: str = Field(default="", max_length=1000)
    operator: str = Field(default="equals", min_length=1, max_length=80)
    expected: Any = None
    description: str = Field(default="", max_length=2000)


class TestCaseRecord(BaseModel):
    id: str
    project_id: str
    case_key: str
    title: str
    mode_key: str
    case_type: str
    priority: TestCasePriority = "P1"
    lifecycle_status: TestCaseLifecycleStatus = "draft"
    active_version_id: str | None = None
    latest_version: int = 1
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class TestCaseVersionRecord(BaseModel):
    id: str
    case_id: str
    version: int = Field(ge=1)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[TestCaseStep] = Field(min_length=1)
    assertions: list[TestCaseAssertion] = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict)
    cleanup: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=64, max_length=64)
    source_refs: list[TestCaseSourceRef] = Field(min_length=1)
    model_key: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=160)
    skill_versions: dict[str, str] = Field(min_length=1)
    created_at: datetime


class _VersionContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preconditions: list[str] = Field(default_factory=list)
    steps: list[TestCaseStep] = Field(min_length=1)
    assertions: list[TestCaseAssertion] = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict)
    cleanup: list[str] = Field(default_factory=list)
    source_refs: list[TestCaseSourceRef] = Field(min_length=1)
    model_key: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=160)
    skill_versions: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_step_order(self):
        orders = [step.order for step in self.steps]
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("Test case step order must be unique and contiguous from 1")
        return self


class TestCaseDraftCreateRequest(_VersionContentRequest):
    case_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    title: str = Field(min_length=1, max_length=300)
    mode_key: str = Field(min_length=1, max_length=80)
    case_type: str = Field(min_length=1, max_length=80)
    priority: TestCasePriority = "P1"


class TestCaseVersionCreateRequest(_VersionContentRequest):
    pass


class TestCaseActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str | None = None


class TestCaseGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=4000)
    mode_key: str = Field(min_length=1, max_length=80)
    model_key: str | None = Field(default=None, max_length=160)
    api_doc_ids: list[str] = Field(default_factory=list, max_length=20)
    include_knowledge_graph: bool = True
    include_history: bool = True
    max_cases: int = Field(default=30, ge=1, le=200)


class GeneratedTestCaseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_key: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    title: str = Field(min_length=1, max_length=300)
    case_type: str = Field(min_length=1, max_length=80)
    priority: TestCasePriority = "P1"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[TestCaseStep] = Field(min_length=1)
    assertions: list[TestCaseAssertion] = Field(min_length=1)
    test_data: dict[str, Any] = Field(default_factory=dict)
    cleanup: list[str] = Field(default_factory=list)


class GeneratedTestCaseBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_key: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=160)
    skill_versions: dict[str, str] = Field(min_length=1)
    cases: list[GeneratedTestCaseDraft] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class TestCaseBundle(BaseModel):
    case: TestCaseRecord
    version: TestCaseVersionRecord


class TestCaseGenerationResponse(BaseModel):
    items: list[TestCaseBundle]
    warnings: list[str] = Field(default_factory=list)


class TestCasePage(BaseModel):
    items: list[TestCaseRecord]
    limit: int
    offset: int
    has_more: bool
