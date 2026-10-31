"""Read-only evidence for an external resource cleanup operation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class DockerResourceAbsenceObservation(BaseModel):
    """A target query, not proof of command success or reversed side effects."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["docker_container_absence_only"] = "docker_container_absence_only"
    method: Literal["docker_ps_all_full_id"] = "docker_ps_all_full_id"
    container_id: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    allocation_id: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")
    lease_namespace: str = Field(min_length=1)
    observer_worker_id: str = Field(min_length=1)
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Docker absence observation requires a timezone")
        return value


class ResourceCleanupReconciliation(BaseModel):
    """A cleanup receipt view that never grants another execution attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["resource_cleanup_reconciliation_only"] = "resource_cleanup_reconciliation_only"
    task_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    external_resource_id: str | None = None
    allocation_id: str | None = None
    status: Literal["completed", "unknown", "drifted"]
    receipt_status: Literal["running", "completed", "skipped", "failed"] | None = None
    terminal_status: Literal["completed", "skipped"] | None = None
    attempts: StrictInt = Field(ge=0)
    worker_id: str | None = None
    error_type: str | None = None
    completed_at: datetime | None = None
    reason_code: Literal[
        "receipt_missing",
        "receipt_running",
        "receipt_failed",
        "receipt_completed",
        "receipt_skipped",
        "receipt_identity_drift",
        "receipt_status_invalid",
    ]
