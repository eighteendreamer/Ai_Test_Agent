from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Awaitable, Callable
from uuid import uuid4

from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.prompt_service import PromptSubmissionService
from src.application.runtime_service import RuntimeService
from src.application.verification_service import VerificationService
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
    InterruptSessionRequest,
    MessageRole,
    ResumeSessionRequest,
    RuntimeMode,
    SendMessageRequest,
    SessionDetail,
    SessionReplayResponse,
    SessionStatus,
    SessionSummary,
    SessionVerificationResponse,
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
        verification_service: VerificationService | None = None,
    ) -> None:
        self._store = store
        self._prompt_service = prompt_service
        self._runtime_service = runtime_service
        self._memory_runtime_service = memory_runtime_service
        self._verification_service = verification_service or VerificationService()
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

    async def list_verifications(self, session_id: str) -> SessionVerificationResponse:
        session = await self._require_session(session_id)
        results = session.metadata.get("verification_results", [])
        if not isinstance(results, list):
            results = []
        return SessionVerificationResponse(
            session_id=session_id,
            verification_results=[
                item if isinstance(item, dict) else {}
                for item in results
            ],
            metadata={
                "verification_count": len(results),
            },
        )

    async def interrupt_session(
        self,
        session_id: str,
        payload: InterruptSessionRequest,
    ) -> SessionDetail:
        session = await self._require_session(session_id)
        if session.status != SessionStatus.running:
            raise ValueError("Only running sessions can be interrupted.")

        reason = (payload.reason or "Interrupt requested from session control API.").strip()
        self._runtime_service.request_interrupt(session_id, reason)
        control = self._ensure_control_metadata(session)
        control.update(
            {
                "control_state": "interrupt_requested",
                "is_interrupted": False,
                "is_resumable": False,
                "last_interrupt_reason": reason,
                "last_control_source": payload.source,
            }
        )
        session.metadata["control"] = control
        await self._store.save_session(session)
        await self._store.append_event(
            session_id,
            self._make_event(
                session_id,
                "runtime.interrupt_requested",
                {
                    "message": "Interrupt has been requested and will stop at the next safe boundary.",
                    "reason": reason,
                    "source": payload.source,
                },
            ),
        )
        return await self._to_detail(session)

    async def resume_session(
        self,
        session_id: str,
        payload: ResumeSessionRequest,
    ) -> ConversationResponse:
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            snapshot = await self._store.get_latest_snapshot(session_id)
            if snapshot is None:
                raise ValueError("No snapshot is available for resume.")
            if snapshot.stage not in {"waiting_approval", "interrupted", "resumable"}:
                raise ValueError("Latest snapshot is not resumable.")

            session.status = SessionStatus.running
            control = self._ensure_control_metadata(session)
            control.update(
                {
                    "control_state": "resuming",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_resume_reason": (payload.reason or "Manual resume requested.").strip(),
                    "last_control_source": payload.source,
                }
            )
            session.metadata["control"] = control
            await self._store.save_session(session)

            assistant_message_id = str(uuid4())
            stream_chunk_handler = self._build_stream_chunk_handler(
                session_id=session_id,
                turn_id=str(snapshot.graph_state.get("turn_id", "")),
                assistant_message_id=assistant_message_id,
            )
            runtime_result = await self._runtime_service.resume_turn(
                session,
                snapshot,
                resume_reason=payload.reason or "manual_resume",
                on_model_chunk=stream_chunk_handler,
            )
            return await self._finalize_runtime_result(
                session=session,
                runtime_result=runtime_result,
                assistant_message_id=assistant_message_id,
                user_message_override=str(runtime_result.state.get("user_message", "")),
            )

    async def replay_session(self, session_id: str) -> SessionReplayResponse:
        session = await self._require_session(session_id)
        latest_snapshot = await self._store.get_latest_snapshot(session_id)
        control = self._ensure_control_metadata(session)
        replay_count = session.metadata.get("replay_requests", 0)
        session.metadata["replay_requests"] = int(replay_count) + 1
        session.metadata["control"] = {
            **control,
            "last_replay_requested_at": datetime.utcnow().isoformat(),
            "replay_available": True,
        }
        await self._store.save_session(session)
        await self._store.append_event(
            session_id,
            self._make_event(
                session_id,
                "session.replay_requested",
                {
                    "message": "A read-only replay of the session history was requested.",
                    "replay_count": session.metadata["replay_requests"],
                },
            ),
        )
        events = await self._store.list_events(session_id)
        detail = await self._to_detail(session)
        return SessionReplayResponse(
            session_id=session_id,
            control_state=detail.control_state,
            latest_snapshot=latest_snapshot,
            events=events,
            metadata={
                "replay_count": session.metadata.get("replay_requests", 0),
                "snapshot_stage": latest_snapshot.stage if latest_snapshot else "",
            },
        )

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
                await self._finalize_runtime_result(
                    session=session,
                    runtime_result=continuation,
                    assistant_message_id=assistant_message_id,
                    user_message_override=str(continuation.state.get("user_message", "")),
                )
            return approval

    async def send_message(self, session_id: str, payload: SendMessageRequest) -> ConversationResponse:
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            if session.status == SessionStatus.waiting_approval:
                raise ValueError("Session is waiting for approval. Resolve the pending approval before sending a new message.")
            if session.status == SessionStatus.running:
                raise ValueError("Session is still running. Wait for the current turn to finish before sending a new message.")
            if session.status == SessionStatus.interrupted:
                raise ValueError("Session is interrupted. Resume the pending turn before sending a new message.")

            execution_request = self._prompt_service.prepare(session, payload)
            failure_message = "Runtime execution failed before the assistant response was produced."

            session.status = SessionStatus.running
            control = self._ensure_control_metadata(session)
            control.update(
                {
                    "control_state": "active_turn",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_turn_id": execution_request.turn_id,
                }
            )
            session.metadata["control"] = control
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
                    "message_kind": execution_request.message_kind.value,
                    "submit_mode": execution_request.submit_mode,
                    "command_name": execution_request.command_name,
                    "attachment_count": len(execution_request.attachments),
                    "input_summary": execution_request.input_summary,
                    **payload.metadata,
                },
            )
            session.messages.append(user_message)
            await self._store.save_session(session)
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "input.orchestrated",
                    {
                        "turn_id": execution_request.turn_id,
                        "message": "Input orchestrator normalized the submission and produced an execution request.",
                        "message_kind": execution_request.message_kind.value,
                        "submit_mode": execution_request.submit_mode,
                        "command_name": execution_request.command_name or "",
                        "attachment_count": len(execution_request.attachments),
                        "hook_count": len(execution_request.hook_results),
                        "input_summary": execution_request.input_summary,
                    },
                ),
            )
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
                        "message_kind": execution_request.message_kind.value,
                        "attachment_count": len(execution_request.attachments),
                        "command_name": execution_request.command_name or "",
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
                        "message_kind": execution_request.message_kind.value,
                        "submit_mode": execution_request.submit_mode,
                        "attachment_count": len(execution_request.attachments),
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
                return await self._finalize_runtime_result(
                    session=session,
                    runtime_result=runtime_result,
                    assistant_message_id=assistant_message_id,
                    user_message_override=payload.content,
                )
            except Exception as exc:
                session.status = SessionStatus.failed
                self._ensure_control_metadata(session).update(
                    {
                        "control_state": "failed",
                        "is_interrupted": False,
                        "is_resumable": False,
                    }
                )
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

    async def _finalize_runtime_result(
        self,
        session: SessionRecord,
        runtime_result,
        assistant_message_id: str,
        user_message_override: str,
    ) -> ConversationResponse:
        session_id = session.id
        model_response_summary = runtime_result.state.get("model_response_summary", {})
        response_mode = str(model_response_summary.get("mode") or "ok")
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
                        "turn_id": runtime_result.state["turn_id"],
                        "message": "A tool execution approval request has been created.",
                        "approval_id": approval.id,
                        "tool_key": approval.tool_key,
                        "tool_name": approval.tool_name,
                        "reason": approval.reason,
                    },
                ),
            )

        await self._store.save_snapshot(session_id, runtime_result.snapshot)
        session.metadata["pending_turn"] = runtime_result.pending_turn
        await self._store.append_event(
            session_id,
            self._make_event(
                session_id,
                "snapshot.saved",
                {
                    "turn_id": runtime_result.state["turn_id"],
                    "message": "Execution snapshot has been saved for replay and resume.",
                    "snapshot_id": runtime_result.snapshot.id,
                    "snapshot_version": runtime_result.snapshot.version,
                    "snapshot_stage": runtime_result.snapshot.stage,
                },
            ),
        )

        control = self._ensure_control_metadata(session)
        control_state = self._control_state_from_runtime_result(runtime_result)
        control.update(
            {
                "control_state": control_state,
                "is_interrupted": control_state == "interrupted",
                "is_resumable": control_state in {"waiting_approval", "interrupted", "resumable"},
                "replay_available": True,
                "last_snapshot_stage": runtime_result.snapshot.stage,
                "last_turn_id": runtime_result.state["turn_id"],
            }
        )
        session.metadata["control"] = control
        verification_results = self._verification_service.build_results(
            session_id=session_id,
            turn_id=str(runtime_result.state["turn_id"]),
            trace_id=str(runtime_result.state.get("trace_id", "")),
            tool_results=list(runtime_result.state.get("tool_results", [])),
            context_bundle=dict(runtime_result.state.get("context_bundle", {})),
        )
        if verification_results:
            existing_results = session.metadata.get("verification_results", [])
            if not isinstance(existing_results, list):
                existing_results = []
            serialized_results = [item.model_dump(mode="python") for item in verification_results]
            session.metadata["verification_results"] = [*existing_results, *serialized_results]
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "verification.completed",
                    {
                        "turn_id": runtime_result.state["turn_id"],
                        "message": "Verification results were derived from runtime evidence.",
                        "verification_count": len(serialized_results),
                        "passed_count": sum(1 for item in verification_results if item.status.value == "passed"),
                        "failed_count": sum(1 for item in verification_results if item.status.value == "failed"),
                    },
                ),
            )

        assistant_message = ChatMessage(
            id=assistant_message_id,
            role=MessageRole.assistant,
            content=runtime_result.output_text,
            created_at=datetime.utcnow(),
            metadata={
                "turn_id": runtime_result.state["turn_id"],
                "agent_key": runtime_result.state["selected_agent_key"],
                "agent_name": runtime_result.state["selected_agent_name"],
                "model_key": runtime_result.state["selected_model_key"],
                "model_name": runtime_result.state["selected_model_name"],
                "response_mode": response_mode,
            },
        )
        if runtime_result.output_text:
            session.messages.append(assistant_message)
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "assistant.stream.completed",
                    {
                        "turn_id": runtime_result.state["turn_id"],
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
                        "turn_id": runtime_result.state["turn_id"],
                        "message_id": assistant_message_id,
                        "message": "Assistant response has been added to the transcript.",
                        "agent_key": runtime_result.state["selected_agent_key"],
                        "model_key": runtime_result.state["selected_model_key"],
                        "response_mode": response_mode,
                        "response_preview": truncate_text(runtime_result.output_text, 180),
                        "response_length": len(runtime_result.output_text),
                    },
                ),
            )
            if response_mode == "ok":
                await self._persist_turn_memory(
                    session=session,
                    turn_id=runtime_result.state["turn_id"],
                    trace_id=runtime_result.state["trace_id"],
                    user_message=user_message_override,
                    assistant_message=runtime_result.output_text,
                    tool_results=list(runtime_result.state.get("tool_results", [])),
                    context_bundle=dict(runtime_result.state.get("context_bundle", {})),
                )

        session.status = self._session_status_from_runtime_result(runtime_result)
        await self._store.save_session(session)
        await self._store.append_event(
            session_id,
            self._make_event(
                session_id,
                "turn.completed" if session.status != SessionStatus.interrupted else "turn.interrupted",
                {
                    "turn_id": runtime_result.state["turn_id"],
                    "message": "User turn artifacts were persisted.",
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
        control = self._ensure_control_metadata(session)
        last_snapshot = await self._store.get_latest_snapshot(session.id)
        derived_resumable = bool(control.get("is_resumable")) or (
            last_snapshot is not None and last_snapshot.stage in {"waiting_approval", "interrupted", "resumable"}
        )
        derived_interrupted = bool(control.get("is_interrupted")) or session.status == SessionStatus.interrupted
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
            last_snapshot=last_snapshot,
            control_state=str(control.get("control_state") or (last_snapshot.stage if last_snapshot else session.status.value)),
            is_resumable=derived_resumable,
            is_interrupted=derived_interrupted,
            replay_available=bool(control.get("replay_available")) or last_snapshot is not None,
            verification_results=[
                item if hasattr(item, "model_dump") else item
                for item in session.metadata.get("verification_results", [])
            ],
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

    def _ensure_control_metadata(self, session: SessionRecord) -> dict:
        control = session.metadata.get("control", {})
        if not isinstance(control, dict):
            control = {}
        control.setdefault("control_state", session.status.value)
        control.setdefault("is_resumable", False)
        control.setdefault("is_interrupted", False)
        control.setdefault("replay_available", session.snapshot_count > 0)
        return control

    def _session_status_from_runtime_result(self, runtime_result) -> SessionStatus:
        model_response_summary = runtime_result.state.get("model_response_summary", {})
        if str(model_response_summary.get("mode") or "") == "http_error":
            return SessionStatus.failed
        if runtime_result.state.get("termination_reason") == "interrupted":
            return SessionStatus.interrupted
        if runtime_result.approvals or runtime_result.snapshot.stage == "waiting_approval":
            return SessionStatus.waiting_approval
        return SessionStatus.completed

    def _control_state_from_runtime_result(self, runtime_result) -> str:
        model_response_summary = runtime_result.state.get("model_response_summary", {})
        if str(model_response_summary.get("mode") or "") == "http_error":
            return "failed"
        if runtime_result.state.get("termination_reason") == "interrupted":
            return "interrupted"
        if runtime_result.approvals or runtime_result.snapshot.stage == "waiting_approval":
            return "waiting_approval"
        if runtime_result.pending_turn:
            return "resumable"
        return "completed"
