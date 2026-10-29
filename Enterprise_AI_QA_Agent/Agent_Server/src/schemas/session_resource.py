from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field


class SessionResourceKind(str, Enum):
    docker_container = "docker_container"
    browser_session = "browser_session"
    browser_profile = "browser_profile"


class SessionResourceStatus(str, Enum):
    active = "active"
    released = "released"
    missing = "missing"
    error = "error"


class SessionResourceCleanupPolicy(str, Enum):
    auto = "auto"
    manual = "manual"


def session_resource_cleanup_operation_id(session_id: str, resource_id: str, reason: str) -> str:
    """Derive one stable cleanup operation identity for a resource and reason."""
    return str(uuid5(
        NAMESPACE_URL,
        f"enterprise-ai-qa-agent:session-resource-cleanup:{session_id}:{resource_id}:{reason or ''}",
    ))


class SessionResourceCleanupReconciliation(BaseModel):
    """Read-only resource cleanup evidence; it grants no retry authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1] = 1
    scope: Literal["session_resource_cleanup_reconciliation_only"] = (
        "session_resource_cleanup_reconciliation_only"
    )
    session_id: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    resource_key: str = Field(min_length=1)
    kind: SessionResourceKind
    cleanup_policy: SessionResourceCleanupPolicy
    operation_id: str = Field(min_length=1)
    observed_operation_id: str | None = None
    resource_status: SessionResourceStatus
    cleanup_state: Literal["unknown", "completed"] | None = None
    status: Literal["completed", "unknown", "drifted"]
    reason_code: Literal[
        "cleanup_completed",
        "cleanup_not_started",
        "cleanup_unknown",
        "cleanup_other_operation",
        "cleanup_operation_drift",
        "cleanup_state_drift",
    ]


class SessionResourceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    kind: SessionResourceKind
    resource_key: str
    status: SessionResourceStatus = SessionResourceStatus.active
    cleanup_policy: SessionResourceCleanupPolicy = SessionResourceCleanupPolicy.auto
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    released_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
