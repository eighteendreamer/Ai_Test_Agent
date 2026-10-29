"""Read-only evidence for an external resource cleanup operation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


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
