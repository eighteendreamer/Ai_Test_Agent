from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.schemas.session import ChatMessage, RuntimeMode, SessionMode, SessionStatus


@dataclass
class SessionRecord:
    id: str
    title: str
    status: SessionStatus
    session_mode: SessionMode
    runtime_mode: RuntimeMode
    created_at: datetime
    updated_at: datetime
    preferred_model: str | None = None
    selected_agent: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    messages: list[ChatMessage] = field(default_factory=list)
    event_count: int = 0
    snapshot_count: int = 0
