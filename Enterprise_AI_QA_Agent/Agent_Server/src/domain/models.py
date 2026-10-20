from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.schemas.session import (
    ChatMessage,
    ExecutionEvent,
    RuntimeMode,
    SessionMode,
    SessionSnapshot,
    SessionStatus,
    ToolApprovalRequest,
)


@dataclass
class SessionRecord:
    id: str
    title: str
    status: SessionStatus
    session_mode: SessionMode
    runtime_mode: RuntimeMode
    mode_key: str
    created_at: datetime
    updated_at: datetime
    preferred_model: str | None = None
    selected_agent: str | None = None
    project_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[ChatMessage] = field(default_factory=list)
    event_count: int = 0
    snapshot_count: int = 0
    # Internal optimistic-write token; deliberately omitted from API DTOs and exports.
    state_revision: int = field(default=0, repr=False, compare=False)


@dataclass
class SessionImportBundle:
    """A complete session and its persisted child records for backup restore."""

    session: SessionRecord
    events: list[ExecutionEvent] = field(default_factory=list)
    snapshots: list[SessionSnapshot] = field(default_factory=list)
    approvals: list[ToolApprovalRequest] = field(default_factory=list)
