from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"
    event = "event"


class SessionStatus(str, Enum):
    idle = "idle"
    running = "running"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"


class SessionMode(str, Enum):
    normal = "normal"
    coordinator = "coordinator"
    resumed = "resumed"
    direct_connect = "direct_connect"
    remote = "remote"
    assistant_viewer = "assistant_viewer"
    background_task = "background_task"


class RuntimeMode(str, Enum):
    interactive = "interactive"
    headless = "headless"
    background = "background"


class ToolApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"


class ChatMessage(BaseModel):
    id: str
    role: MessageRole
    content: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionEvent(BaseModel):
    type: str
    session_id: str
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionSnapshot(BaseModel):
    id: str
    session_id: str
    version: int
    stage: str
    created_at: datetime
    graph_state: dict[str, Any] = Field(default_factory=dict)


class ToolApprovalRequest(BaseModel):
    id: str
    session_id: str
    tool_key: str
    tool_name: str
    reason: str
    status: ToolApprovalStatus = ToolApprovalStatus.pending
    created_at: datetime
    resolved_at: datetime | None = None
    decision_note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    id: str
    title: str
    status: SessionStatus
    session_mode: SessionMode
    runtime_mode: RuntimeMode
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionSummary):
    messages: list[ChatMessage] = Field(default_factory=list)
    event_count: int = 0
    snapshot_count: int = 0
    preferred_model: str | None = None
    selected_agent: str | None = None
    pending_approvals: list[ToolApprovalRequest] = Field(default_factory=list)
    last_snapshot: SessionSnapshot | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    title: str = "New Intelligent QA Session"
    session_mode: SessionMode = SessionMode.normal
    runtime_mode: RuntimeMode = RuntimeMode.interactive
    preferred_model: str | None = None
    selected_agent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    content: str
    agent_key: str | None = None
    model_key: str | None = None
    skill_keys: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionRequest(BaseModel):
    turn_id: str
    session_id: str
    user_message: str
    normalized_input: str
    agent_key: str | None = None
    model_key: str | None = None
    skill_keys: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    decision: ToolApprovalStatus
    reason: str | None = None


class ConversationResponse(BaseModel):
    session: SessionDetail
    output: ChatMessage
    events: list[ExecutionEvent] = Field(default_factory=list)


class HeadlessExecutionRequest(BaseModel):
    title: str = "Headless Agent Task"
    content: str
    session_mode: SessionMode = SessionMode.background_task
    agent_key: str | None = None
    model_key: str | None = None
    skill_keys: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
