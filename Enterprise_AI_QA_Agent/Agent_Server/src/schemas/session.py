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


class MessageKind(str, Enum):
    user_input = "user_input"
    slash_command = "slash_command"
    system_command = "system_command"
    task_notification = "task_notification"
    coordinator_assignment = "coordinator_assignment"


class SessionStatus(str, Enum):
    idle = "idle"
    running = "running"
    waiting_approval = "waiting_approval"
    interrupted = "interrupted"
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


class VerificationStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    partial = "partial"
    not_run = "not_run"


class VerificationEvidence(BaseModel):
    source_type: str
    source_id: str
    label: str
    detail: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerificationResult(BaseModel):
    id: str
    session_id: str
    turn_id: str
    trace_id: str
    verifier: str
    status: VerificationStatus
    summary: str
    assertion_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    evidence: list[VerificationEvidence] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


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
    control_state: str = "idle"
    is_resumable: bool = False
    is_interrupted: bool = False
    replay_available: bool = False
    verification_results: list[VerificationResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateSessionRequest(BaseModel):
    title: str = "New Intelligent QA Session"
    session_mode: SessionMode = SessionMode.normal
    runtime_mode: RuntimeMode = RuntimeMode.interactive
    preferred_model: str | None = None
    selected_agent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InputAttachment(BaseModel):
    kind: str = "file"
    name: str
    uri: str | None = None
    content_type: str | None = None
    text_excerpt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InputHookResult(BaseModel):
    hook_key: str
    status: str
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    content: str = ""
    agent_key: str | None = None
    model_key: str | None = None
    skill_keys: list[str] = Field(default_factory=list)
    attachments: list[InputAttachment] = Field(default_factory=list)
    message_kind: MessageKind = MessageKind.user_input
    submit_mode: str = "immediate"
    command_name: str | None = None
    interrupt_if_busy: bool = False
    source: str = "session.send_message"
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PendingInputQueueEntry(BaseModel):
    id: str
    created_at: datetime
    busy_status: str
    queue_behavior: str
    interrupt_policy: str
    reason: str = ""
    payload: SendMessageRequest
    metadata: dict[str, Any] = Field(default_factory=dict)


class InputEnvelope(BaseModel):
    raw_content: str = ""
    normalized_content: str = ""
    message_kind: MessageKind = MessageKind.user_input
    submit_mode: str = "immediate"
    command_name: str | None = None
    command_args: str = ""
    attachment_count: int = 0
    attachment_names: list[str] = Field(default_factory=list)
    has_text: bool = False
    has_attachments: bool = False
    source: str = "session.send_message"


class InputRoutingDecision(BaseModel):
    execution_lane: str = "conversation_turn"
    queue_behavior: str = "reject_when_busy"
    interrupt_policy: str = "wait_for_active_turn"
    should_persist_user_message: bool = True
    should_stream_response: bool = True
    expects_model_turn: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionRequest(BaseModel):
    turn_id: str
    session_id: str
    user_message: str
    normalized_input: str
    agent_key: str | None = None
    model_key: str | None = None
    skill_keys: list[str] = Field(default_factory=list)
    attachments: list[InputAttachment] = Field(default_factory=list)
    message_kind: MessageKind = MessageKind.user_input
    submit_mode: str = "immediate"
    command_name: str | None = None
    input_summary: str = ""
    hook_results: list[InputHookResult] = Field(default_factory=list)
    input_envelope: InputEnvelope | None = None
    routing_decision: InputRoutingDecision | None = None
    orchestration_meta: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    decision: ToolApprovalStatus
    reason: str | None = None


class InterruptSessionRequest(BaseModel):
    reason: str | None = None
    source: str = "user"


class ResumeSessionRequest(BaseModel):
    reason: str | None = None
    source: str = "user"


class SessionReplayResponse(BaseModel):
    session_id: str
    control_state: str
    latest_snapshot: SessionSnapshot | None = None
    events: list[ExecutionEvent] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionVerificationResponse(BaseModel):
    session_id: str
    verification_results: list[VerificationResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


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
