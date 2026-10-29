from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field


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
