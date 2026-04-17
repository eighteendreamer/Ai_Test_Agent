from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Awaitable, Callable
from uuid import uuid4

from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.prompt_service import PromptSubmissionService
from src.application.runtime_service import RuntimeService
from src.domain.models import SessionRecord
from src.runtime.execution_logging import truncate_text
from src.runtime.store import SessionStore
from src.schemas.session import (
    ApprovalDecisionRequest,
    ChatMessage,
    ConversationResponse,
    CreateSessionRequest,
    ExecutionEvent,
    HeadlessExecutionRequest,
    MessageRole,
    RuntimeMode,
    SendMessageRequest,
    SessionDetail,
    SessionStatus,
    SessionSummary,
    ToolApprovalRequest,
    ToolApprovalStatus,
)


class SessionService:
    def __init__(
        self,
        store: SessionStore,
        prompt_service: PromptSubmissionService,
        runtime_service: RuntimeService,
        memory_runtime_service: MemoryRuntimeService | None = None,
    ) -> None:
        self._store = store
        self._prompt_service = prompt_service
        self._runtime_service = runtime_service
        self._memory_runtime_service = memory_runtime_service
        self._session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def list_sessions(self) -> list[SessionSummary]:
        sessions = await self._store.list_sessions()
        return [await self._to_summary(item) for item in sessions]

    async def create_session(self, payload: CreateSessionRequest) -> SessionDetail:
        now = datetime.utcnow()
        session = SessionRecord(
            id=str(uuid4()),
            title=payload.title,
            status=SessionStatus.idle,
            session_mode=payload.session_mode,
            runtime_mode=payload.runtime_mode,
            created_at=now,
            updated_at=now,
            preferred_model=payload.preferred_model,
            selected_agent=payload.selected_agent,
            metadata=payload.metadata,
        )
        await self._store.save_session(session)
        await self._store.append_event(
            session.id,
            self._make_event(
                session.id,
                "session.created",
                {
                    "title": session.title,
                    "session_mode": session.session_mode.value,
                    "runtime_mode": session.runtime_mode.value,
                },
            ),
        )
        return await self._to_detail(session)

    async def get_session(self, session_id: str) -> SessionDetail:
        session = await self._store.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        return await self._to_detail(session)

    async def list_events(self, session_id: str) -> list[ExecutionEvent]:
        await self._require_session(session_id)
        return await self._store.list_events(session_id)

    async def list_snapshots(self, session_id: str):
        await self._require_session(session_id)
        return await self._store.list_snapshots(session_id)

    async def list_approvals(self, session_id: str) -> list[ToolApprovalRequest]:
        await self._require_session(session_id)
        return await self._store.list_approvals(session_id)

    async def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        payload: ApprovalDecisionRequest,
    ) -> ToolApprovalRequest:
        async with self._session_locks[session_id]:
            if payload.decision not in {ToolApprovalStatus.approved, ToolApprovalStatus.denied}:
                raise ValueError("Approval decision must be approved or denied.")

            approval = await self._store.resolve_approval(
                session_id=session_id,
                approval_id=approval_id,
                status=payload.decision,
                reason=payload.reason,
            )
            session = await self._require_session(session_id)
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "approval.resolved",
                    {
                        "approval_id": approval.id,
                        "tool_key": approval.tool_key,
                        "decision": approval.status.value,
                    },
                ),
            )
            assistant_message_id = str(uuid4())
            stream_chunk_handler = self._build_stream_chunk_handler(
                session_id=session_id,
                turn_id=str(session.metadata.get("pending_turn", {}).get("turn_id", "")),
                assistant_message_id=assistant_message_id,
            )
            continuation = await self._runtime_service.resume_after_approval(
                session,
                approval.model_dump(mode="python"),
                on_model_chunk=stream_chunk_handler,
            )

            if continuation is not None:
                for event in continuation.events:
                    await self._store.append_event(session_id, event)

                for tool_message in continuation.tool_messages:
                    session.messages.append(tool_message)

                await self._store.save_snapshot(session_id, continuation.snapshot)
                session.metadata["pending_turn"] = continuation.pending_turn

                if continuation.output_text:
                    assistant_message = ChatMessage(
                        id=assistant_message_id,
                        role=MessageRole.assistant,
                        content=continuation.output_text,
                        created_at=datetime.utcnow(),
                        metadata={
                            "turn_id": continuation.state["turn_id"],
                            "agent_key": continuation.state["selected_agent_key"],
                            "agent_name": continuation.state["selected_agent_name"],
                            "model_key": continuation.state["selected_model_key"],
                            "model_name": continuation.state["selected_model_name"],
                        },
                    )
                    session.messages.append(assistant_message)
                    await self._store.append_event(
                        session_id,
                        self._make_event(
                            session_id,
                            "assistant.stream.completed",
                            {
                                "turn_id": continuation.state["turn_id"],
                                "message_id": assistant_message_id,
                                "response_length": len(continuation.output_text),
                            },
                        ),
                    )
                    await self._store.append_event(
                        session_id,
                        self._make_event(
                            session_id,
                            "assistant.response_generated",
                            {
                                "turn_id": continuation.state["turn_id"],
                                "message_id": assistant_message_id,
                                "message": "Assistant response has been added after approval resolution.",
                                "agent_key": continuation.state["selected_agent_key"],
                                "model_key": continuation.state["selected_model_key"],
                                "response_preview": truncate_text(continuation.output_text, 180),
                                "response_length": len(continuation.output_text),
                            },
                        ),
                    )
                    await self._persist_turn_memory(
                        session=session,
                        turn_id=continuation.state["turn_id"],
                        trace_id=continuation.state["trace_id"],
                        user_message=str(continuation.state.get("user_message", "")),
                        assistant_message=continuation.output_text,
                        tool_results=list(continuation.state.get("tool_results", [])),
                        context_bundle=dict(continuation.state.get("context_bundle", {})),
                    )

            pending_approvals = [
                item for item in await self._store.list_approvals(session_id) if item.status == ToolApprovalStatus.pending
            ]
            session.status = SessionStatus.waiting_approval if pending_approvals else SessionStatus.completed
            await self._store.save_session(session)
            return approval

    async def send_message(self, session_id: str, payload: SendMessageRequest) -> ConversationResponse:
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            if session.status == SessionStatus.waiting_approval:
                raise ValueError("Session is waiting for approval. Resolve the pending approval before sending a new message.")
            if session.status == SessionStatus.running:
                raise ValueError("Session is still running. Wait for the current turn to finish before sending a new message.")

            execution_request = self._prompt_service.prepare(session, payload)
            failure_message = "Runtime execution failed before the assistant response was produced."

            session.status = SessionStatus.running
            if payload.agent_key:
                session.selected_agent = payload.agent_key
            if payload.model_key:
                session.preferred_model = payload.model_key

            user_message = ChatMessage(
                id=str(uuid4()),
                role=MessageRole.user,
                content=payload.content.strip(),
                created_at=datetime.utcnow(),
                metadata={
                    "turn_id": execution_request.turn_id,
                    "requested_agent": payload.agent_key,
                    "requested_model": payload.model_key,
                    **payload.metadata,
                },
            )
            session.messages.append(user_message)
            await self._store.save_session(session)
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "turn.started",
                    {
                        "turn_id": execution_request.turn_id,
                        "message": "User turn has been accepted by the backend runtime.",
                        "content_preview": truncate_text(payload.content, 180),
                        "normalized_preview": truncate_text(execution_request.normalized_input, 180),
                        "agent_key": payload.agent_key or "",
                        "model_key": payload.model_key or "",
                        "skill_count": len(execution_request.skill_keys),
                        "context_keys": ",".join(sorted(execution_request.context.keys())) or "none",
                        "message_kind": str(payload.metadata.get("message_kind", "user_input")),
                    },
                ),
            )
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "message.received",
                    {
                        "turn_id": execution_request.turn_id,
                        "message": "User input has been persisted to the session transcript.",
                        "role": "user",
                        "content_preview": truncate_text(payload.content, 180),
                        "requested_agent": payload.agent_key or session.selected_agent or "auto",
                        "requested_model": payload.model_key or session.preferred_model or "auto",
                        "message_kind": str(payload.metadata.get("message_kind", "user_input")),
                    },
                ),
            )

            assistant_message_id = str(uuid4())
            stream_chunk_handler = self._build_stream_chunk_handler(
                session_id=session_id,
                turn_id=execution_request.turn_id,
                assistant_message_id=assistant_message_id,
            )
            try:
                runtime_result = await self._runtime_service.execute_turn(
                    session,
                    execution_request,
                    on_model_chunk=stream_chunk_handler,
                )

                for event in runtime_result.events:
                    await self._store.append_event(session_id, event)

                for tool_message in runtime_result.tool_messages:
                    session.messages.append(tool_message)

                for approval_data in runtime_result.approvals:
                    approval = ToolApprovalRequest.model_validate(approval_data)
                    await self._store.save_approval(session_id, approval)
                    await self._store.append_event(
                        session_id,
                        self._make_event(
                            session_id,
                            "approval.created",
                            {
                                "turn_id": execution_request.turn_id,
                                "message": "A tool execution approval request has been created.",
                                "approval_id": approval.id,
                                "tool_key": approval.tool_key,
                                "tool_name": approval.tool_name,
                                "reason": approval.reason,
                            },
                        ),
                    )

                failure_message = "Runtime execution finished, but saving the session artifacts failed."
                await self._store.save_snapshot(session_id, runtime_result.snapshot)
                session.metadata["pending_turn"] = runtime_result.pending_turn
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "snapshot.saved",
                        {
                            "turn_id": execution_request.turn_id,
                            "message": "Execution snapshot has been saved for replay and resume.",
                            "snapshot_id": runtime_result.snapshot.id,
                            "snapshot_version": runtime_result.snapshot.version,
                            "snapshot_stage": runtime_result.snapshot.stage,
                        },
                    ),
                )

                assistant_message = ChatMessage(
                    id=assistant_message_id,
                    role=MessageRole.assistant,
                    content=runtime_result.output_text,
                    created_at=datetime.utcnow(),
                    metadata={
                        "turn_id": execution_request.turn_id,
                        "agent_key": runtime_result.state["selected_agent_key"],
                        "agent_name": runtime_result.state["selected_agent_name"],
                        "model_key": runtime_result.state["selected_model_key"],
                        "model_name": runtime_result.state["selected_model_name"],
                    },
                )
                session.messages.append(assistant_message)
                session.status = (
                    SessionStatus.waiting_approval
                    if runtime_result.approvals
                    else SessionStatus.completed
                )
                await self._store.save_session(session)
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "assistant.stream.completed",
                        {
                            "turn_id": execution_request.turn_id,
                            "message_id": assistant_message_id,
                            "response_length": len(runtime_result.output_text),
                        },
                    ),
                )
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "assistant.response_generated",
                        {
                            "turn_id": execution_request.turn_id,
                            "message_id": assistant_message_id,
                            "message": "Assistant response has been added to the transcript.",
                            "agent_key": runtime_result.state["selected_agent_key"],
                            "model_key": runtime_result.state["selected_model_key"],
                            "response_preview": truncate_text(runtime_result.output_text, 180),
                            "response_length": len(runtime_result.output_text),
                        },
                    ),
                )
                await self._persist_turn_memory(
                    session=session,
                    turn_id=execution_request.turn_id,
                    trace_id=runtime_result.state["trace_id"],
                    user_message=payload.content,
                    assistant_message=runtime_result.output_text,
                    tool_results=list(runtime_result.state.get("tool_results", [])),
                    context_bundle=execution_request.context,
                )
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "turn.completed",
                        {
                            "turn_id": execution_request.turn_id,
                            "message": "User turn has completed and all runtime artifacts were persisted.",
                            "session_status": session.status.value,
                            "event_count": session.event_count,
                            "snapshot_count": session.snapshot_count,
                            "approval_count": len(runtime_result.approvals),
                        },
                    ),
                )

                events = await self._store.list_events(session_id)
                return ConversationResponse(
                    session=await self._to_detail(session),
                    output=assistant_message,
                    events=events[-10:],
                )
            except Exception as exc:
                session.status = SessionStatus.failed
                await self._store.save_session(session)
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "turn.failed",
                        {
                            "turn_id": execution_request.turn_id,
                            "message": failure_message,
                            "error_type": exc.__class__.__name__,
                            "error": truncate_text(str(exc), 240),
                        },
                    ),
                )
                raise

    async def execute_headless(self, payload: HeadlessExecutionRequest) -> ConversationResponse:
        session = await self.create_session(
            CreateSessionRequest(
                title=payload.title,
                session_mode=payload.session_mode,
                runtime_mode=RuntimeMode.headless,
                preferred_model=payload.model_key,
                selected_agent=payload.agent_key,
                metadata={"launch_mode": "headless"},
            )
        )
        return await self.send_message(
            session.id,
            SendMessageRequest(
                content=payload.content,
                agent_key=payload.agent_key,
                model_key=payload.model_key,
                skill_keys=payload.skill_keys,
                context=payload.context,
                metadata=payload.metadata,
            ),
        )

    def get_event_queue(self, session_id: str):
        return self._store.get_queue(session_id)

    async def _require_session(self, session_id: str) -> SessionRecord:
        session = await self._store.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    async def _to_summary(self, session: SessionRecord) -> SessionSummary:
        return SessionSummary(
            id=session.id,
            title=session.title,
            status=session.status,
            session_mode=session.session_mode,
            runtime_mode=session.runtime_mode,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    async def _to_detail(self, session: SessionRecord) -> SessionDetail:
        approvals = await self._store.list_approvals(session.id)
        return SessionDetail(
            id=session.id,
            title=session.title,
            status=session.status,
            session_mode=session.session_mode,
            runtime_mode=session.runtime_mode,
            created_at=session.created_at,
            updated_at=session.updated_at,
            messages=session.messages,
            event_count=session.event_count,
            snapshot_count=session.snapshot_count,
            preferred_model=session.preferred_model,
            selected_agent=session.selected_agent,
            pending_approvals=[
                item for item in approvals if item.status == ToolApprovalStatus.pending
            ],
            last_snapshot=await self._store.get_latest_snapshot(session.id),
            metadata=session.metadata,
        )

    def _make_event(self, session_id: str, event_type: str, payload: dict[str, object]) -> ExecutionEvent:
        return ExecutionEvent(
            type=event_type,
            session_id=session_id,
            timestamp=datetime.utcnow(),
            payload=payload,
        )

    def _build_stream_chunk_handler(
        self,
        session_id: str,
        turn_id: str,
        assistant_message_id: str,
    ) -> Callable[[str], Awaitable[None]]:
        started = False

        async def emit_chunk(chunk: str) -> None:
            nonlocal started
            if not chunk:
                return
            if not started:
                started = True
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "assistant.stream.started",
                        {
                            "turn_id": turn_id,
                            "message_id": assistant_message_id,
                        },
                    ),
                )
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "assistant.stream.delta",
                    {
                        "turn_id": turn_id,
                        "message_id": assistant_message_id,
                        "delta": chunk,
                    },
                ),
            )

        return emit_chunk

    async def _persist_turn_memory(
        self,
        session: SessionRecord,
        turn_id: str,
        trace_id: str,
        user_message: str,
        assistant_message: str,
        tool_results: list[dict],
        context_bundle: dict,
    ) -> None:
        if self._memory_runtime_service is None:
            return
        if not assistant_message.strip():
            return
        memory_ids = await self._memory_runtime_service.write_turn_memory(
            session_id=session.id,
            turn_id=turn_id,
            trace_id=trace_id,
            user_message=user_message,
            assistant_message=assistant_message,
            tool_results=tool_results,
            context_bundle=context_bundle,
        )
        await self._store.append_event(
            session.id,
            self._make_event(
                session.id,
                "memory.persisted",
                {
                    "turn_id": turn_id,
                    "trace_id": trace_id,
                    "memory_backend": self._memory_runtime_service.backend,
                    "memory_count": len(memory_ids),
                    "memory_ids": memory_ids,
                },
            ),
        )
