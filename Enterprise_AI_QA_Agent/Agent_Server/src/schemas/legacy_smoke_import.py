from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


LegacySmokeImportDecision = Literal[
    "importable",
    "unmapped",
    "already_projected",
    "invalid",
]


class LegacySmokeImportPreflightEntry(BaseModel):
    legacy_run_id: str
    legacy_plan_id: str = ""
    project_scope: str = ""
    project_id: str | None = None
    decision: LegacySmokeImportDecision
    reason: str = ""
    case_count: int = Field(default=0, ge=0)
    mapped_case_statuses: dict[str, int] = Field(default_factory=dict)


class LegacySmokeImportPreflightReport(BaseModel):
    dry_run: Literal[True] = True
    source_system: Literal["legacy_smoke_catalog"] = "legacy_smoke_catalog"
    read_count: int = Field(default=0, ge=0)
    importable_count: int = Field(default=0, ge=0)
    unmapped_count: int = Field(default=0, ge=0)
    already_projected_count: int = Field(default=0, ge=0)
    invalid_count: int = Field(default=0, ge=0)
    unmapped_scopes: list[str] = Field(default_factory=list)
    entries: list[LegacySmokeImportPreflightEntry] = Field(default_factory=list)
