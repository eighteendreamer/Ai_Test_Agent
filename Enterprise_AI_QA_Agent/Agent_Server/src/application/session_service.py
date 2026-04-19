from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Awaitable, Callable
from uuid import uuid4

from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.observation_runtime_service import ObservationRuntimeService
from src.application.input_orchestrator_service import InputOrchestratorService
from src.application.runtime_service import RuntimeService
from src.application.transcript_hygiene_service import TranscriptHygieneService
from src.application.verification_service import VerificationService
from src.domain.models import SessionRecord
from src.runtime.execution_logging import truncate_text
from src.runtime.store import SessionStore
from src.schemas.observation import SessionObservationResponse
from src.schemas.session import (
    ApprovalDecisionRequest,
    ChatMessage,
    ConversationResponse,
    CreateSessionRequest,
    ExecutionEvent,
    HeadlessExecutionRequest,
    InterruptSessionRequest,
    MessageRole,
    PendingInputQueueEntry,
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
        input_orchestrator_service: InputOrchestratorService,
        runtime_service: RuntimeService,
        memory_runtime_service: MemoryRuntimeService | None = None,
        observation_runtime_service: ObservationRuntimeService | None = None,
        transcript_hygiene_service: TranscriptHygieneService | None = None,
        verification_service: VerificationService | None = None,
    ) -> None:
        self._store = store
        self._input_orchestrator_service = input_orchestrator_service
        self._runtime_service = runtime_service
        self._memory_runtime_service = memory_runtime_service
        self._observation_runtime_service = observation_runtime_service
        self._transcript_hygiene_service = transcript_hygiene_service or TranscriptHygieneService()
        self._verification_service = verification_service or VerificationService()
        self._session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._queue_drain_tasks: dict[str, asyncio.Task[None]] = {}

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

    async def list_observations(self, session_id: str) -> SessionObservationResponse:
        await self._require_session(session_id)
        if self._memory_runtime_service is None:
            return SessionObservationResponse(
                session_id=session_id,
                observations=[],
                metadata={"observation_count": 0, "memory_backend": "disabled"},
            )
        observations = await self._memory_runtime_service.list_session_observations(session_id=session_id)
        return SessionObservationResponse(
            session_id=session_id,
            observations=observations,
            metadata={
                "observation_count": len(observations),
                "memory_backend": self._memory_runtime_service.backend,
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
                "transcript_summary": self._transcript_hygiene_service.summarize_messages(session.messages),
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
        session_lock = self._session_locks[session_id]
        if session_lock.locked():
            session = await self._require_session(session_id)
            execution_request = self._input_orchestrator_service.orchestrate(session, payload)
            busy_response = await self._handle_busy_submission(
                session=session,
                payload=payload,
                execution_request=execution_request,
            )
            if busy_response is not None:
                return busy_response

        async with session_lock:
            session = await self._require_session(session_id)
            execution_request = self._input_orchestrator_service.orchestrate(session, payload)
            busy_response = await self._handle_busy_submission(
                session=session,
                payload=payload,
                execution_request=execution_request,
            )
            if busy_response is not None:
                return busy_response
            return await self._run_submission_locked(
                session=session,
                payload=payload,
                execution_request=execution_request,
                allow_interrupted=session.status == SessionStatus.interrupted,
            )

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

    async def _run_submission_locked(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
        execution_request,
        *,
        allow_interrupted: bool = False,
    ) -> ConversationResponse:
        session_id = session.id
        failure_message = "Runtime execution failed before the assistant response was produced."
        superseded_turn_id = ""

        if session.status == SessionStatus.interrupted and allow_interrupted:
            superseded_turn_id = str((session.metadata.get("pending_turn") or {}).get("turn_id") or "")
            session.metadata["pending_turn"] = {}
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "queue.interrupted_turn_superseded",
                    {
                        "message": "A queued input superseded the interrupted turn and started a new turn.",
                        "superseded_turn_id": superseded_turn_id,
                        "next_turn_id": execution_request.turn_id,
                    },
                ),
            )

        session.status = SessionStatus.running
        control = self._ensure_control_metadata(session)
        queued_entries = self._get_pending_input_queue(session)
        control.update(
            {
                "control_state": "active_turn",
                "is_interrupted": False,
                "is_resumable": False,
                "last_turn_id": execution_request.turn_id,
                "queued_input_count": len(queued_entries),
                "last_superseded_turn_id": superseded_turn_id or str(control.get("last_superseded_turn_id") or ""),
            }
        )
        session.metadata["control"] = control
        session.metadata["pending_turn"] = {}
        if payload.agent_key:
            session.selected_agent = payload.agent_key
        if payload.model_key:
            session.preferred_model = payload.model_key

        user_message = ChatMessage(
            id=str(uuid4()),
            role=MessageRole.user,
            content=payload.content.strip(),
            created_at=datetime.utcnow(),
            metadata=self._transcript_hygiene_service.user_message_metadata(
                {
                    "turn_id": execution_request.turn_id,
                    "requested_agent": payload.agent_key,
                    "requested_model": payload.model_key,
                    "message_kind": execution_request.message_kind.value,
                    "submit_mode": execution_request.submit_mode,
                    "command_name": execution_request.command_name,
                    "attachment_count": len(execution_request.attachments),
                    "execution_lane": (
                        execution_request.routing_decision.execution_lane
                        if execution_request.routing_decision
                        else ""
                    ),
                    "queue_behavior": (
                        execution_request.routing_decision.queue_behavior
                        if execution_request.routing_decision
                        else ""
                    ),
                    "input_summary": execution_request.input_summary,
                    **payload.metadata,
                }
            ),
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
                    "execution_lane": (
                        execution_request.routing_decision.execution_lane
                        if execution_request.routing_decision
                        else ""
                    ),
                    "queue_behavior": (
                        execution_request.routing_decision.queue_behavior
                        if execution_request.routing_decision
                        else ""
                    ),
                    "interrupt_policy": (
                        execution_request.routing_decision.interrupt_policy
                        if execution_request.routing_decision
                        else ""
                    ),
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
                    "execution_lane": (
                        execution_request.routing_decision.execution_lane
                        if execution_request.routing_decision
                        else ""
                    ),
                    "queue_behavior": (
                        execution_request.routing_decision.queue_behavior
                        if execution_request.routing_decision
                        else ""
                    ),
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
                    "execution_lane": (
                        execution_request.routing_decision.execution_lane
                        if execution_request.routing_decision
                        else ""
                    ),
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
            session = await self._require_session(session_id)
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
            self._maybe_schedule_pending_input_drain(session_id)
            raise

    async def _handle_busy_submission(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
        execution_request,
    ) -> ConversationResponse | None:
        busy_status = session.status
        if busy_status not in {
            SessionStatus.running,
            SessionStatus.waiting_approval,
            SessionStatus.interrupted,
        }:
            return None

        routing = execution_request.routing_decision
        queue_behavior = routing.queue_behavior if routing else "reject_when_busy"
        interrupt_policy = routing.interrupt_policy if routing else "wait_for_active_turn"
        if queue_behavior == "reject_when_busy":
            if busy_status == SessionStatus.waiting_approval:
                raise ValueError("Session is waiting for approval. Resolve the pending approval before sending a new message.")
            if busy_status == SessionStatus.running:
                raise ValueError("Session is still running. Wait for the current turn to finish before sending a new message.")
            raise ValueError("Session is interrupted. Resume the pending turn before sending a new message.")

        request_interrupt = (
            busy_status == SessionStatus.running
            and queue_behavior == "interrupt_then_retry"
            and interrupt_policy == "interrupt_active_turn"
        )
        return await self._enqueue_pending_input(
            session=session,
            payload=payload,
            execution_request=execution_request,
            busy_status=busy_status,
            request_interrupt=request_interrupt,
        )

    async def _enqueue_pending_input(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
        execution_request,
        *,
        busy_status: SessionStatus,
        request_interrupt: bool,
    ) -> ConversationResponse:
        session_id = session.id
        queue_entries = self._get_pending_input_queue(session)
        queue_entry = PendingInputQueueEntry(
            id=str(uuid4()),
            created_at=datetime.utcnow(),
            busy_status=busy_status.value,
            queue_behavior=(
                execution_request.routing_decision.queue_behavior
                if execution_request.routing_decision
                else "enqueue_if_busy"
            ),
            interrupt_policy=(
                execution_request.routing_decision.interrupt_policy
                if execution_request.routing_decision
                else "wait_for_active_turn"
            ),
            reason=(
                "Current turn is running; the new input was queued and will run after the active turn settles."
                if busy_status == SessionStatus.running
                else (
                    "A pending approval is blocking execution; the new input was queued for later processing."
                    if busy_status == SessionStatus.waiting_approval
                    else "The interrupted turn will be superseded and the queued input will run next."
                )
            ),
            payload=payload,
            metadata={
                "turn_id": execution_request.turn_id,
                "message_kind": execution_request.message_kind.value,
                "submit_mode": execution_request.submit_mode,
                "command_name": execution_request.command_name,
                "execution_lane": (
                    execution_request.routing_decision.execution_lane
                    if execution_request.routing_decision
                    else ""
                ),
                "interrupt_requested": request_interrupt,
            },
        )
        queue_entries.append(queue_entry)
        self._set_pending_input_queue(session, queue_entries)
        control = self._ensure_control_metadata(session)
        control["queued_input_count"] = len(queue_entries)
        if request_interrupt:
            control["control_state"] = "interrupt_requested"
            control["last_interrupt_reason"] = "Queued retry requested by a follow-up input."
        session.metadata["control"] = control
        await self._store.save_session(session)
        await self._store.append_event(
            session_id,
            self._make_event(
                session_id,
                "input.queued",
                {
                    "queue_entry_id": queue_entry.id,
                    "busy_status": busy_status.value,
                    "queue_behavior": queue_entry.queue_behavior,
                    "interrupt_policy": queue_entry.interrupt_policy,
                    "queue_depth": len(queue_entries),
                    "queued_turn_id": execution_request.turn_id,
                    "message_kind": execution_request.message_kind.value,
                    "command_name": execution_request.command_name or "",
                    "interrupt_requested": request_interrupt,
                    "message": queue_entry.reason,
                },
            ),
        )
        if request_interrupt:
            self._runtime_service.request_interrupt(
                session_id,
                "Queued retry requested by a follow-up input.",
            )
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "runtime.interrupt_requested",
                    {
                        "message": "Interrupt requested so the queued input can run next.",
                        "reason": "Queued retry requested by a follow-up input.",
                        "source": "queued_input",
                        "queue_entry_id": queue_entry.id,
                    },
                ),
            )

        if busy_status != SessionStatus.running and busy_status != SessionStatus.waiting_approval:
            self._maybe_schedule_pending_input_drain(session_id)
        return await self._build_queued_response(session, queue_entry)

    async def _build_queued_response(
        self,
        session: SessionRecord,
        queue_entry: PendingInputQueueEntry,
    ) -> ConversationResponse:
        message = ChatMessage(
            id=str(uuid4()),
            role=MessageRole.system,
            content=queue_entry.reason,
            created_at=datetime.utcnow(),
            metadata={
                "persist_transcript": False,
                "context_eligible": False,
                "transcript_bucket": "event",
                "queue_entry_id": queue_entry.id,
                "queue_behavior": queue_entry.queue_behavior,
                "busy_status": queue_entry.busy_status,
            },
        )
        events = await self._store.list_events(session.id)
        refreshed_session = await self._require_session(session.id)
        return ConversationResponse(
            session=await self._to_detail(refreshed_session),
            output=message,
            events=events[-10:],
        )

    def _get_pending_input_queue(self, session: SessionRecord) -> list[PendingInputQueueEntry]:
        raw_items = session.metadata.get("pending_input_queue", [])
        if not isinstance(raw_items, list):
            return []
        items: list[PendingInputQueueEntry] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                items.append(PendingInputQueueEntry.model_validate(item))
            except Exception:
                continue
        return items

    def _set_pending_input_queue(
        self,
        session: SessionRecord,
        queue_entries: list[PendingInputQueueEntry],
    ) -> None:
        session.metadata["pending_input_queue"] = [
            item.model_dump(mode="python") for item in queue_entries
        ]
        self._ensure_control_metadata(session)["queued_input_count"] = len(queue_entries)

    def _maybe_schedule_pending_input_drain(self, session_id: str) -> None:
        existing_task = self._queue_drain_tasks.get(session_id)
        if existing_task is not None and not existing_task.done():
            return
        task = asyncio.create_task(self._drain_pending_inputs(session_id))
        self._queue_drain_tasks[session_id] = task

        def _cleanup(_: asyncio.Task[None]) -> None:
            current = self._queue_drain_tasks.get(session_id)
            if current is task:
                self._queue_drain_tasks.pop(session_id, None)

        task.add_done_callback(_cleanup)

    async def _drain_pending_inputs(self, session_id: str) -> None:
        while True:
            async with self._session_locks[session_id]:
                session = await self._require_session(session_id)
                queue_entries = self._get_pending_input_queue(session)
                if not queue_entries:
                    self._ensure_control_metadata(session)["queued_input_count"] = 0
                    await self._store.save_session(session)
                    return
                if session.status in {SessionStatus.running, SessionStatus.waiting_approval}:
                    return

                next_entry = queue_entries.pop(0)
                self._set_pending_input_queue(session, queue_entries)
                await self._store.save_session(session)
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "input.dequeued",
                        {
                            "queue_entry_id": next_entry.id,
                            "remaining_queue_depth": len(queue_entries),
                            "busy_status": next_entry.busy_status,
                            "queue_behavior": next_entry.queue_behavior,
                        },
                    ),
                )
                await self._run_submission_locked(
                    session=session,
                    payload=next_entry.payload,
                    execution_request=self._input_orchestrator_service.orchestrate(session, next_entry.payload),
                    allow_interrupted=session.status == SessionStatus.interrupted,
                )

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
        await self._persist_tool_observations(
            session=session,
            turn_id=str(runtime_result.state["turn_id"]),
            trace_id=str(runtime_result.state.get("trace_id", "")),
            tool_results=list(runtime_result.state.get("tool_results", [])),
            context_bundle=dict(runtime_result.state.get("context_bundle", {})),
        )

        assistant_message = ChatMessage(
            id=assistant_message_id,
            role=MessageRole.assistant,
            content=runtime_result.output_text,
            created_at=datetime.utcnow(),
            metadata=self._transcript_hygiene_service.assistant_message_metadata(
                response_mode=response_mode,
                metadata={
                    "turn_id": runtime_result.state["turn_id"],
                    "agent_key": runtime_result.state["selected_agent_key"],
                    "agent_name": runtime_result.state["selected_agent_name"],
                    "model_key": runtime_result.state["selected_model_key"],
                    "model_name": runtime_result.state["selected_model_name"],
                },
            ),
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

        latest_session = await self._require_session(session_id)
        latest_queue_entries = self._get_pending_input_queue(latest_session)
        latest_control = self._ensure_control_metadata(latest_session)
        self._set_pending_input_queue(session, latest_queue_entries)
        session.status = self._session_status_from_runtime_result(runtime_result)
        control = self._ensure_control_metadata(session)
        if latest_control.get("last_interrupt_reason") and not control.get("last_interrupt_reason"):
            control["last_interrupt_reason"] = latest_control["last_interrupt_reason"]
        if latest_control.get("last_control_source") and not control.get("last_control_source"):
            control["last_control_source"] = latest_control["last_control_source"]
        session.metadata["control"] = control
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

        latest_session = await self._require_session(session_id)
        if (
            self._get_pending_input_queue(latest_session)
            and latest_session.status not in {SessionStatus.running, SessionStatus.waiting_approval}
        ):
            self._maybe_schedule_pending_input_drain(session_id)
        events = await self._store.list_events(session_id)
        return ConversationResponse(
            session=await self._to_detail(latest_session),
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

    async def _persist_tool_observations(
        self,
        session: SessionRecord,
        turn_id: str,
        trace_id: str,
        tool_results: list[dict],
        context_bundle: dict,
    ) -> None:
        if self._memory_runtime_service is None or self._observation_runtime_service is None:
            return
        observations = self._observation_runtime_service.build_tool_observations(
            session_id=session.id,
            turn_id=turn_id,
            trace_id=trace_id,
            tool_results=tool_results,
            context_bundle=context_bundle,
        )
        if not observations:
            return
        observation_ids = await self._memory_runtime_service.write_observations(observations)
        await self._store.append_event(
            session.id,
            self._make_event(
                session.id,
                "observations.persisted",
                {
                    "turn_id": turn_id,
                    "trace_id": trace_id,
                    "observation_count": len(observation_ids),
                    "observation_ids": observation_ids,
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
        control.setdefault("queued_input_count", 0)
        session.metadata["control"] = control
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
