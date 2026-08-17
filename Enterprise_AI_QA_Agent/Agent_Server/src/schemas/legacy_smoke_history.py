from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


LegacySmokeMappedStatus = Literal["passed", "failed", "blocked", "skipped"]


class LegacySmokeScopeBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_scope: str = Field(min_length=1, max_length=160)

    @field_validator("project_scope")
    @classmethod
    def normalize_scope(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("project_scope cannot be empty")
        return normalized


class LegacySmokeScopeBinding(BaseModel):
    source_system: Literal["legacy_smoke_catalog"] = "legacy_smoke_catalog"
    project_id: str
    project_scope: str
    created_at: datetime


class LegacySmokeCaseSummary(BaseModel):
    source_system: Literal["legacy_smoke_catalog"] = "legacy_smoke_catalog"
    read_only: Literal[True] = True
    legacy_case_id: str
    title: str = ""
    case_type: str = ""
    legacy_status: str = ""
    mapped_status: LegacySmokeMappedStatus
    summary: str = ""
    assertion_count: int = Field(default=0, ge=0)
    passed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    evidence_count: int = Field(default=0, ge=0)


class LegacySmokeRunSummary(BaseModel):
    source_system: Literal["legacy_smoke_catalog"] = "legacy_smoke_catalog"
    read_only: Literal[True] = True
    legacy_run_id: str
    legacy_plan_id: str
    legacy_plan_version: int = Field(ge=1)
    project_scope: str
    legacy_status: str = ""
    legacy_verdict: str = ""
    total_cases: int = Field(default=0, ge=0)
    passed_cases: int = Field(default=0, ge=0)
    failed_cases: int = Field(default=0, ge=0)
    blocked_cases: int = Field(default=0, ge=0)
    started_at: datetime
    completed_at: datetime | None = None
    summary: str = ""
    case_results: list[LegacySmokeCaseSummary] = Field(default_factory=list)


class LegacySmokeRunPage(BaseModel):
    project_id: str
    bindings: list[LegacySmokeScopeBinding] = Field(default_factory=list)
    items: list[LegacySmokeRunSummary] = Field(default_factory=list)
    limit: int
    next_cursor: str | None = None
    has_more: bool = False
