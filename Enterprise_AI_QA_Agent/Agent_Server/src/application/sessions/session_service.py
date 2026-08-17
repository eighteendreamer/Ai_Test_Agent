from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Awaitable, Callable
from uuid import uuid4

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.observation_runtime_service import ObservationRuntimeService
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.runtime.runtime_service import RuntimeService
from src.application.resources.session_resource_service import SessionResourceService
from src.application.projects.project_service import ProjectService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.testing.verification_service import VerificationService
from src.domain.models import SessionRecord
from src.runtime.execution_logging import truncate_text
from src.runtime.store import SessionStore
from src.registry.modes import ModeRegistry
from src.schemas.observation import SessionObservationResponse
from src.schemas.session import (
    ApprovalDecisionRequest,
    ChatMessage,
    ConversationResponse,
    CreateSessionRequest,
    ExecutionEvent,
    HeadlessExecutionRequest,
    InterruptSessionRequest,
    MessageKind,
    MessageRole,
    PendingInputQueueEntry,
    ResumeSessionRequest,
    RuntimeMode,
    SendMessageRequest,
    SessionDetail,
    SessionReplayResponse,
    SessionSnapshot,
    SessionStatus,
    SessionSummary,
    SessionSummaryPage,
    SessionVerificationResponse,
    ToolApprovalRequest,
    ToolApprovalStatus,
    UpdateSessionRequest,
)


DEFAULT_EVENT_HISTORY_LIMIT = 500
DEFAULT_REPLAY_EVENT_LIMIT = 500
DEFAULT_SNAPSHOT_HISTORY_LIMIT = 10
RECENT_CONVERSATION_EVENT_LIMIT = 10
SNAPSHOT_DETAIL_GRAPH_KEYS = {
    "turn_id",
    "trace_id",
    "control_state",
    "loop_iteration",
    "max_iterations",
    "termination_reason",
    "pending_approvals",
    "tool_results",
    "worker_dispatches",
    "code_review_debate_progress",
}


class SessionService:
    def __init__(
        self,
        store: SessionStore,
        input_orchestrator_service: InputOrchestratorService,
        runtime_service: RuntimeService,
        mode_registry: ModeRegistry,
        memory_runtime_service: MemoryRuntimeService | None = None,
        observation_runtime_service: ObservationRuntimeService | None = None,
        transcript_hygiene_service: TranscriptHygieneService | None = None,
        verification_service: VerificationService | None = None,
        session_resource_service: SessionResourceService | None = None,
        project_service: ProjectService | None = None,
    ) -> None:
        self._store = store
        self._input_orchestrator_service = input_orchestrator_service
        self._runtime_service = runtime_service
        self._mode_registry = mode_registry
        self._memory_runtime_service = memory_runtime_service
        self._observation_runtime_service = observation_runtime_service
        self._transcript_hygiene_service = transcript_hygiene_service or TranscriptHygieneService()
        self._verification_service = verification_service or VerificationService()
        self._session_resource_service = session_resource_service
        self._project_service = project_service
        self._session_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._queue_drain_tasks: dict[str, asyncio.Task[None]] = {}
        self._approval_forward_tasks: dict[str, asyncio.Task[None]] = {}
        self._approval_resume_tasks: dict[str, asyncio.Task[None]] = {}

    async def list_sessions(self) -> list[SessionSummary]:
        sessions = await self._store.list_sessions(limit=200)
        return [await self._to_summary(item) for item in sessions]

    async def list_sessions_page(
        self,
        limit: int = 10,
        offset: int = 0,
        mode_key: str | None = None,
    ) -> SessionSummaryPage:
        normalized_limit = max(1, min(int(limit or 10), 100))
        normalized_offset = max(int(offset or 0), 0)
        normalized_mode_key = str(mode_key or "").strip() or None
        sessions = await self._store.list_sessions(
            limit=normalized_limit + 1,
            offset=normalized_offset,
            mode_key=normalized_mode_key,
        )
        has_more = len(sessions) > normalized_limit
        visible = sessions[:normalized_limit]
        return SessionSummaryPage(
            items=[await self._to_summary(item) for item in visible],
            limit=normalized_limit,
            offset=normalized_offset,
            has_more=has_more,
        )

    async def create_session(self, payload: CreateSessionRequest) -> SessionDetail:
        if payload.project_id:
            await self._require_active_project(payload.project_id)
        now = datetime.utcnow()
        mode = self._mode_registry.resolve(payload.mode_key)
        session = SessionRecord(
            id=str(uuid4()),
            title=payload.title,
            status=SessionStatus.idle,
            session_mode=payload.session_mode,
            runtime_mode=payload.runtime_mode,
            mode_key=mode.key,
            created_at=now,
            updated_at=now,
            preferred_model=payload.preferred_model,
            selected_agent=payload.selected_agent or mode.default_agent_key,
            project_id=payload.project_id,
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
                    "mode_key": session.mode_key,
                    "project_id": session.project_id,
                },
            ),
        )
        return await self._to_detail(session)

    async def get_session(self, session_id: str) -> SessionDetail:
        session = await self._store.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        return await self._to_detail(session)

    async def update_session(self, session_id: str, payload: UpdateSessionRequest) -> SessionDetail:
        session = await self._require_session(session_id)
        mode_changed = False
        metadata_changed = False

        if "project_id" in payload.model_fields_set:
            if payload.project_id:
                await self._require_active_project(payload.project_id)
            session.project_id = payload.project_id

        if payload.mode_key is not None:
            mode = self._mode_registry.resolve(payload.mode_key)
            if session.mode_key != mode.key:
                session.mode_key = mode.key
                mode_changed = True
            if payload.selected_agent is None:
                session.selected_agent = mode.default_agent_key

        if payload.selected_agent is not None:
            session.selected_agent = payload.selected_agent

        if payload.preferred_model is not None:
            session.preferred_model = payload.preferred_model

        if payload.metadata is not None:
            session.metadata.update(payload.metadata)
            metadata_changed = True

        await self._store.save_session(session)
        await self._store.append_event(
            session_id,
            self._make_event(
                session_id,
                "session.preferences_updated",
                {
                    "message": "Session execution preferences were updated.",
                    "mode_key": session.mode_key,
                    "selected_agent": session.selected_agent or "",
                    "preferred_model": session.preferred_model or "",
                    "mode_changed": mode_changed,
                    "metadata_changed": metadata_changed,
                },
            ),
        )
        refreshed = await self._require_session(session_id)
        return await self._to_detail(refreshed)

    async def list_events(
        self,
        session_id: str,
        limit: int | None = DEFAULT_EVENT_HISTORY_LIMIT,
        after_event_id: str | None = None,
    ) -> list[ExecutionEvent]:
        await self._require_session(session_id)
        return await self._store.list_events(
            session_id,
            limit=limit,
            after_event_id=after_event_id,
        )

    async def list_snapshots(
        self,
        session_id: str,
        limit: int | None = DEFAULT_SNAPSHOT_HISTORY_LIMIT,
        include_graph_state: bool = False,
    ) -> list[SessionSnapshot]:
        await self._require_session(session_id)
        snapshots = await self._store.list_snapshots(
            session_id,
            limit=limit,
            include_graph_state=include_graph_state,
        )
        if include_graph_state:
            return snapshots
        return [self._snapshot_metadata(snapshot) for snapshot in snapshots]

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
                event_queue=self._store.get_queue(session_id),
            )
            return await self._finalize_runtime_result(
                session=session,
                runtime_result=runtime_result,
                assistant_message_id=assistant_message_id,
                user_message_override=str(runtime_result.state.get("user_message", "")),
            )

    async def replay_session(
        self,
        session_id: str,
        limit: int | None = DEFAULT_REPLAY_EVENT_LIMIT,
    ) -> SessionReplayResponse:
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
        events = await self._store.list_events(session_id, limit=limit)
        detail = await self._to_detail(session)
        return SessionReplayResponse(
            session_id=session_id,
            control_state=detail.control_state,
            latest_snapshot=self._snapshot_for_detail(latest_snapshot),
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

            session = await self._require_session(session_id)
            existing_approvals = await self._store.list_approvals(session_id)
            proxy_approval = next((item for item in existing_approvals if item.id == approval_id), None)
            proxy_metadata = proxy_approval.metadata if proxy_approval is not None else {}
            proxy_child_session_id = str(proxy_metadata.get("proxy_child_session_id") or "").strip()
            proxy_child_approval_id = str(proxy_metadata.get("proxy_child_approval_id") or "").strip()

            approval = await self._store.resolve_approval(
                session_id=session_id,
                approval_id=approval_id,
                status=payload.decision,
                reason=payload.reason,
            )
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
            if proxy_child_session_id and proxy_child_approval_id:
                proxy_execution_status = (
                    SessionStatus.running.value
                    if payload.decision == ToolApprovalStatus.approved
                    else ToolApprovalStatus.denied.value
                )
                await self._mark_proxy_worker_decision_applied(
                    session=session,
                    approval=approval,
                    child_session_id=proxy_child_session_id,
                    execution_status=proxy_execution_status,
                )
                task = asyncio.create_task(
                    self._forward_proxy_approval_decision(
                        parent_session_id=session_id,
                        parent_approval_id=approval_id,
                        child_session_id=proxy_child_session_id,
                        child_approval_id=proxy_child_approval_id,
                        payload=ApprovalDecisionRequest(decision=payload.decision, reason=payload.reason),
                    )
                )
                self._approval_forward_tasks[approval_id] = task
                task.add_done_callback(
                    lambda _finished, proxy_id=approval_id: self._approval_forward_tasks.pop(proxy_id, None)
                )
                return approval
            session.status = SessionStatus.running
            control = self._ensure_control_metadata(session)
            control.update(
                {
                    "control_state": "resuming_after_approval",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_approval_id": approval.id,
                }
            )
            session.metadata["control"] = control
            await self._store.save_session(session)
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "approval.continuation_scheduled",
                    {
                        "approval_id": approval.id,
                        "tool_key": approval.tool_key,
                        "decision": approval.status.value,
                        "message": "Approval continuation has been scheduled in the background.",
                    },
                ),
            )
            task = asyncio.create_task(
                self._resume_after_approval_async(
                    session_id=session_id,
                    approval=approval,
                )
            )
            self._approval_resume_tasks[approval.id] = task
            task.add_done_callback(
                lambda _finished, task_id=approval.id: self._approval_resume_tasks.pop(task_id, None)
            )
            return approval

    async def _resume_after_approval_async(
        self,
        session_id: str,
        approval: ToolApprovalRequest,
    ) -> None:
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            assistant_message_id = str(uuid4())
            try:
                stream_chunk_handler = self._build_stream_chunk_handler(
                    session_id=session_id,
                    turn_id=str(session.metadata.get("pending_turn", {}).get("turn_id", "")),
                    assistant_message_id=assistant_message_id,
                )
                continuation = await self._runtime_service.resume_after_approval(
                    session,
                    approval.model_dump(mode="python"),
                    on_model_chunk=stream_chunk_handler,
                    event_queue=self._store.get_queue(session_id),
                )
                if continuation is None:
                    return
                await self._finalize_runtime_result(
                    session=session,
                    runtime_result=continuation,
                    assistant_message_id=assistant_message_id,
                    user_message_override=str(continuation.state.get("user_message", "")),
                )
            except Exception as exc:
                session.status = SessionStatus.interrupted
                control = self._ensure_control_metadata(session)
                control.update(
                    {
                        "control_state": "interrupted",
                        "is_interrupted": True,
                        "is_resumable": True,
                        "preserve_resources": True,
                        "last_interrupt_reason": truncate_text(str(exc), 240),
                        "last_approval_id": approval.id,
                    }
                )
                session.metadata["control"] = control
                await self._store.save_session(session)
                await self._store.append_event(
                    session_id,
                    self._make_event(
                        session_id,
                        "approval.continuation_interrupted",
                        {
                            "approval_id": approval.id,
                            "tool_key": approval.tool_key,
                            "message": "Approval continuation was interrupted and preserved for resume.",
                            "error": str(exc),
                        },
                    ),
                )

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
            execution_request = await self._input_orchestrator_service.orchestrate_async(session, payload)
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
                mode_key=payload.mode_key,
                preferred_model=payload.model_key,
                selected_agent=payload.agent_key,
                metadata={"launch_mode": "headless"},
            )
        )
        return await self.send_message(
            session.id,
            SendMessageRequest(
                content=payload.content,
                mode_key=payload.mode_key,
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
        if payload.mode_key:
            mode = self._mode_registry.resolve(payload.mode_key)
            session.mode_key = mode.key
            if not payload.agent_key:
                session.selected_agent = mode.default_agent_key
        if payload.agent_key:
            session.selected_agent = payload.agent_key
        if payload.model_key:
            session.preferred_model = payload.model_key

        user_message_metadata = self._transcript_hygiene_service.user_message_metadata(
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
                "has_attachments": bool(execution_request.attachments),
                "attachment_names": [item.name for item in execution_request.attachments[:5]],
                "attachments": [
                    item.model_dump(mode="python") for item in execution_request.attachments
                ],
                "input_summary": execution_request.input_summary,
                **payload.metadata,
            }
        )
        if execution_request.message_kind in {MessageKind.task_notification, MessageKind.coordinator_assignment}:
            user_message_metadata.update(
                {
                    "persist_transcript": False,
                    "context_eligible": False,
                    "transcript_bucket": "event",
                }
            )

        user_message = ChatMessage(
            id=str(uuid4()),
            role=MessageRole.user,
            content=payload.content.strip(),
            created_at=datetime.utcnow(),
            metadata=user_message_metadata,
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
            event_queue = self._store.get_queue(session_id)
            runtime_result = await self._runtime_service.execute_turn(
                session,
                execution_request,
                on_model_chunk=stream_chunk_handler,
                event_queue=event_queue,
            )
            return await self._finalize_runtime_result(
                session=session,
                runtime_result=runtime_result,
                assistant_message_id=assistant_message_id,
                user_message_override=payload.content,
            )
        except Exception as exc:
            session = await self._require_session(session_id)
            latest_snapshot = await self._store.get_latest_snapshot(session_id)
            is_resumable = latest_snapshot is not None and latest_snapshot.stage in {
                "waiting_approval",
                "interrupted",
                "resumable",
            }
            session.status = SessionStatus.interrupted
            self._ensure_control_metadata(session).update(
                {
                    "control_state": "interrupted",
                    "is_interrupted": True,
                    "is_resumable": is_resumable,
                    "preserve_resources": True,
                    "last_interrupt_reason": truncate_text(str(exc), 240),
                }
            )
            await self._store.save_session(session)
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "turn.interrupted",
                    {
                        "turn_id": execution_request.turn_id,
                        "message": "Runtime execution was interrupted before the assistant response was produced.",
                        "error_type": exc.__class__.__name__,
                        "error": truncate_text(str(exc), 240),
                        "is_resumable": is_resumable,
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
        events = await self._store.list_events(session.id, limit=RECENT_CONVERSATION_EVENT_LIMIT)
        refreshed_session = await self._require_session(session.id)
        return ConversationResponse(
            session=await self._to_detail(refreshed_session),
            output=message,
            events=events,
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
                    execution_request=await self._input_orchestrator_service.orchestrate_async(
                        session,
                        next_entry.payload,
                    ),
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
        # Graph events may already have been sent to SSE, but they still need to
        # be persisted for audit/replay. Avoid a second queue publish for the
        # streamed prefix instead of dropping those events from storage.
        streamed_count = runtime_result.state.get("_streamed_event_count", 0)
        for index, event in enumerate(runtime_result.events):
            await self._store.append_event(
                session_id,
                event,
                publish=index >= streamed_count,
            )

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
                "preserve_resources": control_state in {"waiting_approval", "interrupted", "resumable"},
                "replay_available": True,
                "last_snapshot_stage": runtime_result.snapshot.stage,
                "last_turn_id": runtime_result.state["turn_id"],
            }
        )
        session.metadata["control"] = control
        turn_usage = dict(runtime_result.state.get("turn_token_usage") or {})
        if int(turn_usage.get("prompt_tokens") or 0) > 0:
            session.metadata["context_usage"] = {
                "prompt_tokens": int(turn_usage.get("prompt_tokens") or 0),
                "completion_tokens": int(turn_usage.get("completion_tokens") or 0),
                "total_tokens": int(turn_usage.get("total_tokens") or 0),
                "context_window": int(runtime_result.state.get("model_context_window") or 0),
                "turn_id": str(runtime_result.state.get("turn_id") or ""),
                "updated_at": datetime.utcnow().isoformat(),
            }
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
        session.metadata = {
            **dict(latest_session.metadata),
            **dict(session.metadata),
        }
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
        if session.status == SessionStatus.completed:
            await self._cleanup_session_resources_if_terminal(session)

        latest_session = await self._require_session(session_id)
        if (
            self._get_pending_input_queue(latest_session)
            and latest_session.status not in {SessionStatus.running, SessionStatus.waiting_approval}
        ):
            self._maybe_schedule_pending_input_drain(session_id)
        events = await self._store.list_events(session_id, limit=RECENT_CONVERSATION_EVENT_LIMIT)
        return ConversationResponse(
            session=await self._to_detail(latest_session),
            output=assistant_message,
            events=events,
        )

    async def _cleanup_session_resources_if_terminal(self, session: SessionRecord) -> None:
        if self._session_resource_service is None:
            return
        control = self._ensure_control_metadata(session)
        if bool(control.get("preserve_resources")):
            return
        if session.status != SessionStatus.completed:
            return
        cleaned = await self._session_resource_service.cleanup_session(
            session.id,
            reason=f"session_{session.status.value}",
        )
        if not cleaned:
            return
        await self._store.append_event(
            session.id,
            self._make_event(
                session.id,
                "session.resources_cleaned",
                {
                    "session_status": session.status.value,
                    "resource_count": len(cleaned),
                    "resources": [
                        {
                            "kind": item.kind.value,
                            "resource_key": item.resource_key,
                            "status": item.status.value,
                        }
                        for item in cleaned
                    ],
                },
            ),
        )

    async def _forward_proxy_approval_decision(
        self,
        parent_session_id: str,
        parent_approval_id: str,
        child_session_id: str,
        child_approval_id: str,
        payload: ApprovalDecisionRequest,
    ) -> None:
        try:
            await self.resolve_approval(child_session_id, child_approval_id, payload)
        except Exception as exc:
            await self._store.append_event(
                parent_session_id,
                self._make_event(
                    parent_session_id,
                    "worker.approval_forward_failed",
                    {
                        "approval_id": parent_approval_id,
                        "child_session_id": child_session_id,
                        "child_approval_id": child_approval_id,
                        "message": "Failed to forward approval decision to the child worker session.",
                        "error": str(exc),
                    },
                ),
            )

    async def _mark_proxy_worker_decision_applied(
        self,
        *,
        session: SessionRecord,
        approval: ToolApprovalRequest,
        child_session_id: str,
        execution_status: str,
    ) -> None:
        metadata = approval.metadata if isinstance(approval.metadata, dict) else {}
        task_id = str(metadata.get("proxy_task_id") or "").strip()
        worker_agent_key = str(metadata.get("proxy_worker_agent_key") or "").strip()
        parent_turn_id = str(metadata.get("proxy_parent_turn_id") or "").strip()
        is_approved = approval.status == ToolApprovalStatus.approved

        changed = False
        worker_dispatches: list[dict] = []
        for record in session.metadata.get("worker_dispatches", []):
            if not isinstance(record, dict):
                continue
            record_task_id = str(record.get("task_id") or "").strip()
            record_child_session_id = str(record.get("child_session_id") or "").strip()
            if (task_id and record_task_id == task_id) or (child_session_id and record_child_session_id == child_session_id):
                next_record = {
                    **record,
                    "status": execution_status,
                    "child_session_id": child_session_id or record_child_session_id,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                worker_dispatches.append(next_record)
                changed = True
            else:
                worker_dispatches.append(record)

        if not changed and task_id:
            worker_dispatches.append(
                {
                    "task_id": task_id,
                    "child_session_id": child_session_id,
                    "agent_key": worker_agent_key,
                    "description": str(metadata.get("tool_name") or metadata.get("tool_key") or "Worker"),
                    "status": execution_status,
                    "updated_at": datetime.utcnow().isoformat(),
                }
            )
            changed = True

        if changed:
            session.metadata["worker_dispatches"] = worker_dispatches

        pending_approvals = [
            item for item in await self._store.list_approvals(session.id)
            if item.status == ToolApprovalStatus.pending
        ]
        if not pending_approvals and session.status == SessionStatus.waiting_approval:
            session.status = SessionStatus.running
            control = self._ensure_control_metadata(session)
            control.update(
                {
                    "control_state": "running",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_approval_id": approval.id,
                }
            )
            session.metadata["control"] = control

        session.updated_at = datetime.utcnow()
        await self._store.save_session(session)
        await self._store.append_event(
            session.id,
            self._make_event(
                session.id,
                "worker.task_notification_received",
                {
                    "turn_id": parent_turn_id,
                    "task_id": task_id,
                    "child_session_id": child_session_id,
                    "worker_agent_key": worker_agent_key,
                    "worker_status": execution_status,
                    "source": "proxy_approval_forwarder",
                },
            ),
        )
        event_type = "tool.execution_started" if is_approved else "tool.execution_denied"
        summary = (
            f"Approved tool '{approval.tool_name}' is now executing."
            if is_approved
            else f"Approval denied for tool '{approval.tool_name}'."
        )
        await self._store.append_event(
            session.id,
            self._make_event(
                session.id,
                event_type,
                {
                    "turn_id": parent_turn_id,
                    "approval_id": approval.id,
                    "tool_key": approval.tool_key,
                    "tool_name": approval.tool_name,
                    "child_session_id": child_session_id,
                    "status": execution_status,
                    "summary": summary,
                    "source": "proxy_approval_forwarder",
                },
            ),
        )

    async def _require_session(self, session_id: str) -> SessionRecord:
        session = await self._store.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    async def _require_active_project(self, project_id: str) -> None:
        if self._project_service is None:
            raise RuntimeError("Project service is required to bind a session")
        await self._project_service.require_active(project_id)

    async def _to_summary(self, session: SessionRecord) -> SessionSummary:
        return SessionSummary(
            id=session.id,
            title=session.title,
            status=session.status,
            session_mode=session.session_mode,
            runtime_mode=session.runtime_mode,
            mode_key=session.mode_key,
            project_id=session.project_id,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    async def _to_detail(self, session: SessionRecord) -> SessionDetail:
        approvals, last_snapshot = await asyncio.gather(
            self._store.list_approvals(session.id),
            self._store.get_latest_snapshot(session.id),
        )
        control = self._ensure_control_metadata(session)
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
            mode_key=session.mode_key,
            project_id=session.project_id,
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
            last_snapshot=self._snapshot_for_detail(last_snapshot),
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

    def _snapshot_for_detail(self, snapshot: SessionSnapshot | None) -> SessionSnapshot | None:
        if snapshot is None:
            return None
        graph_state = snapshot.graph_state if isinstance(snapshot.graph_state, dict) else {}
        compact_graph_state = {
            key: self._compact_snapshot_graph_value(key, graph_state[key])
            for key in SNAPSHOT_DETAIL_GRAPH_KEYS
            if key in graph_state
        }
        return snapshot.model_copy(update={"graph_state": compact_graph_state})

    def _snapshot_metadata(self, snapshot: SessionSnapshot) -> SessionSnapshot:
        return snapshot.model_copy(update={"graph_state": {}})

    def _compact_snapshot_graph_value(self, key: str, value):
        if key != "tool_results" or not isinstance(value, list):
            return value
        return [self._compact_tool_result(item) for item in value[-20:] if isinstance(item, dict)]

    def _compact_tool_result(self, item: dict) -> dict:
        output = item.get("output") if isinstance(item.get("output"), dict) else {}
        artifacts = output.get("artifacts") if isinstance(output, dict) else []
        metrics = output.get("metrics") if isinstance(output, dict) else {}
        artifact_count = len(artifacts) if isinstance(artifacts, list) else 0
        compact_metrics = {str(key): None for key in metrics.keys()} if isinstance(metrics, dict) else {}
        return {
            "call_id": str(item.get("call_id") or ""),
            "job_id": item.get("job_id"),
            "tool_key": str(item.get("tool_key") or ""),
            "tool_name": str(item.get("tool_name") or item.get("tool_key") or ""),
            "status": str(item.get("status") or ""),
            "summary": truncate_text(str(item.get("summary") or ""), 240),
            "trace_id": item.get("trace_id"),
            "approval_id": item.get("approval_id"),
            "started_at": item.get("started_at"),
            "completed_at": item.get("completed_at"),
            "input": {},
            "output": {
                "artifacts": [{} for _ in range(min(artifact_count, 50))],
                "metrics": compact_metrics,
            },
        }

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
            return SessionStatus.interrupted
        if runtime_result.state.get("termination_reason") == "interrupted":
            return SessionStatus.interrupted
        if runtime_result.approvals or runtime_result.snapshot.stage == "waiting_approval":
            return SessionStatus.waiting_approval
        return SessionStatus.completed

    def _control_state_from_runtime_result(self, runtime_result) -> str:
        model_response_summary = runtime_result.state.get("model_response_summary", {})
        if str(model_response_summary.get("mode") or "") == "http_error":
            return "interrupted"
        if runtime_result.state.get("termination_reason") == "interrupted":
            return "interrupted"
        if runtime_result.approvals or runtime_result.snapshot.stage == "waiting_approval":
            return "waiting_approval"
        if runtime_result.pending_turn:
            return "resumable"
        return "completed"
