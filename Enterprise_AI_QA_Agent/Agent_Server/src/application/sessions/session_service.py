from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import logging
import os
import socket
from typing import Any, Awaitable, Callable
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.context.observation_runtime_service import ObservationRuntimeService
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.application.runtime.runtime_service import RuntimeService
from src.runtime.session_event_sink import SessionEventSink
from src.runtime.session_assistant_stream import SessionAssistantStream
from src.application.resources.session_resource_service import SessionResourceService
from src.application.projects.project_service import ProjectService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.testing.verification_service import VerificationService
from src.domain.models import SessionRecord
from src.runtime.execution_logging import truncate_text
from src.runtime.execution_context import framework_owner_scope, current_framework_owner
from src.runtime.session_lock_map import SessionLockMap
from src.runtime.store import (
    ContinuationLeaseLostError,
    SessionStore,
    SessionWriteConflictError,
)
from src.registry.modes import ModeRegistry
from src.schemas.model_config import is_model_failure_summary
from src.schemas.observation import SessionObservationResponse
from src.schemas.session import (
    ApprovalDecisionRequest,
    ChatMessage,
    ConversationResponse,
    CreateSessionRequest,
    ExecutionEvent,
    ExecutionRequest,
    InterruptSessionRequest,
    MessageKind,
    MessageRole,
    PendingInputQueueEntry,
    ResumeSessionRequest,
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
from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.agent_task import AgentTaskAccepted
from src.schemas.framework_transcript import framework_transcript_asset
from src.runtime.framework_application_projection import FrameworkProjectionWrite
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_graph_input import FrameworkProjectionIdentity


DEFAULT_EVENT_HISTORY_LIMIT = 500
DEFAULT_REPLAY_EVENT_LIMIT = 500
DEFAULT_SNAPSHOT_HISTORY_LIMIT = 10
RECENT_CONVERSATION_EVENT_LIMIT = 10
logger = logging.getLogger(__name__)
BUSY_SUBMISSION_STATUSES = frozenset({
    SessionStatus.running,
    SessionStatus.waiting_approval,
    SessionStatus.interrupted,
})
SERVER_MANAGED_SESSION_METADATA_KEYS = frozenset(
    {"environment", "resource_scope", "security_authorization"}
)
INTERNAL_SESSION_METADATA_KEYS = frozenset(
    {
        "turn_lease_turn_id",
        "turn_lease_owner",
        "turn_lease_token",
        "turn_lease_expires_at",
        "coordinator_dispatch_claims",
    }
)
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
        approval_continuation_lease_seconds: int = 120,
        approval_continuation_heartbeat_seconds: float = 30.0,
        turn_execution_lease_seconds: int = 120,
        turn_execution_heartbeat_seconds: float = 30.0,
        turn_reaper_interval_seconds: float = 30.0,
        event_buffer_capacity: int = 4096,
    ) -> None:
        self._store = store
        self._event_buffer_capacity = max(1, int(event_buffer_capacity))
        self._input_orchestrator_service = input_orchestrator_service
        self._runtime_service = runtime_service
        self._mode_registry = mode_registry
        self._memory_runtime_service = memory_runtime_service
        self._observation_runtime_service = observation_runtime_service
        self._transcript_hygiene_service = transcript_hygiene_service or TranscriptHygieneService()
        self._verification_service = verification_service or VerificationService()
        self._session_resource_service = session_resource_service
        self._project_service = project_service
        self._approval_continuation_lease_seconds = max(
            1, int(approval_continuation_lease_seconds)
        )
        self._approval_continuation_heartbeat_seconds = max(
            0.1, float(approval_continuation_heartbeat_seconds)
        )
        self._turn_execution_lease_seconds = max(1, int(turn_execution_lease_seconds))
        self._turn_execution_heartbeat_seconds = max(
            0.1, float(turn_execution_heartbeat_seconds)
        )
        self._turn_reaper_interval_seconds = max(0.05, float(turn_reaper_interval_seconds))
        self._turn_reaper_stop: asyncio.Event | None = None
        self._turn_reaper_task: asyncio.Task | None = None
        self._worker_owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
        # UI 录制审批回调（方案 4.2 环节④/⑤，P0-8）：main.py 在 recorder 域初始化后注入
        self._recording_approval_service = None
        self._session_locks = SessionLockMap()
        self._queue_drain_tasks: dict[str, asyncio.Task[None]] = {}
        self._approval_forward_tasks: dict[str, asyncio.Task[None]] = {}
        self._approval_resume_tasks: dict[str, asyncio.Task[None]] = {}
        self._closing = False
        self._agent_task_store = None
        self._agent_task_dispatcher = None

    def set_agent_task_store(self, store) -> None:
        self._agent_task_store = store

    def set_agent_task_dispatcher(self, dispatcher) -> None:
        self._agent_task_dispatcher = dispatcher

    async def execute_claimed_message(
        self,
        task,
        *,
        ownership_monitor: asyncio.Task,
    ) -> ConversationResponse:
        """Worker entry: use the session lease atomically granted with the task.

        The dispatcher commits its outcome and releases this lease; this method
        never creates a second claim or routes the input into the local queue.
        """
        payload = SendMessageRequest.model_validate(task.input)
        if not task.user_message_id:
            raise ValueError("Message task is missing its durable user message")
        if task.runtime_binding is not None and task.runtime_owner is None:
            raise ContinuationLeaseLostError("Framework message is missing its captured execution owner")
        async with self._session_locks[task.session_id]:
            await self._store.assert_turn_execution(task.session_id, task.lease_token)
            stop = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._keep_turn_execution_alive(
                    session_id=task.session_id,
                    lease_token=task.lease_token,
                    stop_event=stop,
                    framework_task=task if task.runtime_binding is not None else None,
                )
            )

            async def execute():
                session = await self._require_session(task.session_id)
                session.messages = [
                    message
                    for message in session.messages
                    if message.metadata.get("delivery_status") != "queued"
                    or message.id == task.user_message_id
                ]
                if session.project_id:
                    await self._require_active_project(session.project_id)
                with framework_owner_scope(task.runtime_owner):
                    stages = None
                    if task.runtime_owner is not None:
                        from src.runtime.framework_message_stages import FrameworkMessageStages, message_source_manifest

                        stages = FrameworkMessageStages(self._agent_task_store)
                    frozen = None
                    prepared_stage = None
                    if stages is not None and task.operation == "message_recover":
                        existing = await self._agent_task_store.get_framework_message_stage(task.id, session_id=task.session_id)
                        if existing is not None and existing.phase == "orchestrated":
                            frozen = await stages.resume_orchestrated(task.runtime_owner, modes=self._mode_registry.list())
                        elif existing is not None and existing.phase == "orchestrating":
                            prepared_stage = await stages.resume_orchestrating(task.runtime_owner, modes=self._mode_registry.list())
                        elif existing is not None and existing.phase == "runtime_submitted":
                            return await self._recover_projected_framework_message(
                                session=session,
                                task=task,
                                stage=existing,
                                stages=stages,
                            )
                    if prepared_stage is not None:
                        from src.runtime.framework_message_recovery import prepared_model_call
                        from src.runtime.postgres_framework_model_calls import PostgresFrameworkModelCalls

                        calls = PostgresFrameworkModelCalls(self._agent_task_store._settings)
                        prepared, call = prepared_model_call(prepared_stage, task.runtime_binding,
                            await calls.snapshot(task.runtime_binding))
                        if call is not None and call.phase == "ready":
                            await calls.transfer_ready(task.runtime_owner, expected=call)
                        request = await self._input_orchestrator_service.orchestrate_prepared(session, payload, prepared,
                            turn_id=str(task.turn_id), trace_id=str(task.trace_id),
                            **({"completed_call": call} if call is not None and call.phase == "completed" else {}))
                        request.runtime_binding = task.runtime_binding
                    elif frozen is None:
                        if stages is not None:
                            await stages.begin(task.runtime_owner,
                                message_source_manifest(session, task.runtime_binding, payload,
                                    modes=self._mode_registry.list(), semantic_input_required=True))
                        async def record_semantic_input(prepared):
                            return await stages.freeze_semantic_input(task.runtime_owner, prepared)

                        request = await self._input_orchestrator_service.orchestrate_async(
                            session,
                            payload,
                            turn_id=str(task.turn_id),
                            trace_id=str(task.trace_id),
                            **({"record_semantic_input": record_semantic_input} if stages is not None else {}),
                        )
                        request.runtime_binding = task.runtime_binding
                    else:
                        request = ExecutionRequest.model_validate(frozen.execution_request)
                    if stages is not None:
                        if frozen is None:
                            frozen = await stages.freeze_execution(task.runtime_owner, request)
                        request = ExecutionRequest.model_validate(frozen.execution_request)
                        await stages.submit_runtime(task.runtime_owner)
                    result = await self._run_owned_submission_locked(
                        session,
                        payload,
                        request,
                        allow_interrupted=True,
                        turn_lease_token=task.lease_token,
                        user_message_id=task.user_message_id,
                        projection_owner=task.runtime_owner,
                        on_graph_input=(
                            (lambda asset: stages.record_graph_input(task.runtime_owner, asset))
                            if stages is not None else None
                        ),
                    )
                    if stages is not None:
                        projection_status = (result.stage if isinstance(result, SessionSnapshot)
                            else result.session.status.value)
                        if projection_status == "completed":
                            await stages.projected(task.runtime_owner,
                                result.id if isinstance(result, SessionSnapshot) else result.output.id)
                        else:
                            # A waiting/interrupted response is a durable partial
                            # projection, not the completed Turn's application ACK.
                            logger.info("framework_message_projection_pending", extra={"task_id": task.id,
                                "binding_id": task.runtime_binding.binding_id, "result_status": projection_status})
                    return result

            try:
                return await self._await_owned_execution(
                    task.session_id,
                    heartbeat,
                    execute,
                    ownership_monitors=(ownership_monitor,),
                )
            finally:
                await self._stop_lease_heartbeat(task.session_id, stop, heartbeat)

    async def _recover_projected_framework_message(self, *, session, task, stage, stages):
        """Finish or acknowledge an original application result, never its graph.

        The receipt proves the Session/messages/completion-events transaction.
        It does not claim Memory or cleanup completed. No graph is invoked.
        """
        from src.runtime.framework_application_projection import COMMIT_KEY, load_committed_projection

        approval_finalization = task.operation == "approval_recover"
        acknowledge = stages.projected_approval if approval_finalization else stages.projected
        if COMMIT_KEY not in (stage.source_manifest or {}):
            from src.runtime.framework_runtime_results import FrameworkRuntimeResults

            if not approval_finalization:
                await stages.resume_finalizing(task.runtime_owner)
            asset = await FrameworkRuntimeResults(self._agent_task_store).load(task.runtime_owner)
            logger.info("framework_message_finalization_resuming", extra={"session_id": session.id,
                "binding_id": task.runtime_binding.binding_id, "task_id": task.id,
                "origin_result_task_id": asset.task_id, "runtime_result_asset_sha256": asset.asset_sha256})
            response = await self._finalize_runtime_result(session=session,
                runtime_result=asset.runtime_result.restore(), assistant_message_id=asset.assistant_message_id,
                user_message_override=asset.user_message, turn_lease_token=task.lease_token,
                projection_owner=task.runtime_owner, projection_asset=asset)
            await acknowledge(task.runtime_owner, response.output.id)
            return response

        commit, snapshot, assistant = await load_committed_projection(self._agent_task_store, task.runtime_owner)
        binding = task.runtime_binding
        if assistant is None:
            # Empty output has no persisted assistant row. Keep the stable
            # snapshot identity without inventing a new transcript message.
            assistant = ChatMessage(
                id=commit.snapshot_id,
                role=MessageRole.assistant,
                content="",
                created_at=snapshot.created_at,
                metadata={"turn_id": binding.turn_id, "recovered_projection": True},
            )
        reference = commit.output_message_id or commit.snapshot_id
        await acknowledge(task.runtime_owner, reference)
        refreshed = await self._require_session(session.id)
        events = await self._store.list_events(session.id, limit=RECENT_CONVERSATION_EVENT_LIMIT)
        return ConversationResponse(session=await self._to_detail(refreshed), output=assistant, events=events)

    async def execute_claimed_resume(
        self,
        task,
        *,
        ownership_monitor: asyncio.Task,
    ) -> ConversationResponse:
        """Worker entry for a resume task already fenced by AgentTaskStore."""
        payload = ResumeSessionRequest.model_validate(task.input)
        if not task.reference_id:
            raise ValueError("Manual resume task is missing its checkpoint reference")
        async with self._session_locks[task.session_id]:
            await self._store.assert_turn_execution(task.session_id, task.lease_token)
            session = await self._require_session(task.session_id)
            snapshot = await self._require_resume_snapshot(
                task.session_id,
                reference_id=task.reference_id,
            )
            return await self._run_owned_resume_locked(
                session=session,
                snapshot=snapshot,
                payload=payload,
                turn_lease_token=task.lease_token,
                ownership_monitors=(ownership_monitor,),
                complete_lease=False,
            )

    async def execute_claimed_reconciliation(self, task):
        """Short deterministic transaction; no graph/model/tool invocation."""
        async with self._session_locks[task.session_id]:
            with framework_owner_scope(task.runtime_owner):
                return await self._runtime_service.reconcile_framework_task(task, tasks=self._agent_task_store)

    async def execute_claimed_approval(
        self,
        task,
        *,
        ownership_monitor: asyncio.Task,
    ) -> ConversationResponse:
        """Worker entry for an approval continuation fenced by the Agent task."""
        approval_payload = (task.input or {}).get("approval")
        if not isinstance(approval_payload, dict):
            raise ValueError("Approval task is missing its durable approval payload")
        approval = ToolApprovalRequest.model_validate(approval_payload)
        if approval.id != task.reference_id:
            raise ValueError("Approval task reference does not match its payload")
        bound = task.runtime_binding is not None
        if bound:
            owner = task.runtime_owner
            if (owner is None or owner.binding != task.runtime_binding or owner.task_id != task.id
                or owner.attempt_id != task.attempt_id or owner.worker_id != task.worker_id
                or owner.owner_token != task.lease_token):
                raise ContinuationLeaseLostError("Framework approval is missing its matching captured owner")
            if not isinstance(task.input.get("decision_id"), str) or not task.input["decision_id"]:
                raise ValueError("Framework approval task is missing its frozen decision_id")
        async with self._session_locks[task.session_id]:
            await self._store.assert_turn_execution(task.session_id, task.lease_token)
            session = await self._require_session(task.session_id)
            with framework_owner_scope(task.runtime_owner):
                from src.runtime.framework_approval_finalization import is_finalization_task

                if bound and task.operation == "approval_recover" and is_finalization_task({"input_payload": task.input}):
                    from src.runtime.framework_message_stages import FrameworkMessageStages

                    stages = FrameworkMessageStages(self._agent_task_store)
                    stage = await asyncio.to_thread(stages.get, task.id, task.session_id, include_continuations=True)
                    stop = asyncio.Event()
                    heartbeat = asyncio.create_task(self._keep_turn_execution_alive(session_id=task.session_id,
                        lease_token=task.lease_token, stop_event=stop, framework_task=task))
                    try:
                        return await self._await_owned_execution(task.session_id, heartbeat,
                            self._recover_projected_framework_message, session=session, task=task, stage=stage, stages=stages,
                            ownership_monitors=(ownership_monitor,))
                    finally:
                        await self._stop_lease_heartbeat(task.session_id, stop, heartbeat)
                return await self._run_owned_approval_locked(
                    session=session,
                    approval=approval,
                    turn_lease_token=task.lease_token,
                    ownership_monitors=(ownership_monitor,),
                    framework_task=task if bound else None,
                )

    def set_recording_approval_service(self, recording_approval_service) -> None:
        """注入 UI 录制审批服务（审批通过 → RecorderSessionService.launch）。"""
        self._recording_approval_service = recording_approval_service

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
        self._reject_server_managed_metadata(payload.metadata)
        return await self._create_session(payload)

    async def create_internal_session(self, payload: CreateSessionRequest) -> SessionDetail:
        """Create a session from metadata assembled by server-owned orchestration."""
        return await self._create_session(payload)

    async def _create_session(self, payload: CreateSessionRequest) -> SessionDetail:
        session = await self._new_session_record(payload)
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
        # append_event updates counters/timestamps in the store, not the detached
        # draft. Project the committed value, as ordinary GET /sessions does.
        detail = await self.get_session(session.id)
        logger.info("session_creation_projection_loaded", extra={
            "session_id": session.id, "event_count": detail.event_count,
        })
        return detail

    async def _new_session_record(self, payload: CreateSessionRequest) -> SessionRecord:
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
        return session

    async def recover_expired_turn_executions(
        self, *, recovery_source: str = "service_startup",
    ) -> list[str]:
        recovered_session_ids = await self._store.recover_expired_turn_executions()
        for session_id in recovered_session_ids:
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "turn.recovery_detected",
                    {
                        "message": "An expired turn lease was detected; the session was preserved for manual resume.",
                        "recovery_source": recovery_source,
                        "is_resumable": True,
                    },
                ),
            )
            logger.warning(
                "expired_turn_recovered",
                extra={"session_id": session_id, "recovery_source": recovery_source},
            )
        return recovered_session_ids

    async def start_turn_reaper(self) -> None:
        if self._closing:
            raise RuntimeError("Cannot start a closed SessionService.")
        if self._turn_reaper_task is not None and not self._turn_reaper_task.done():
            return
        try:
            await self._schedule_pending_input_drains()
        except Exception:
            logger.exception("pending_input_queue_startup_scan_failed")
        self._turn_reaper_stop = asyncio.Event()
        self._turn_reaper_task = asyncio.create_task(
            self._turn_reaper_loop(), name="session-turn-lease-reaper",
        )
        logger.info("session_turn_reaper_started", extra={
            "interval_seconds": self._turn_reaper_interval_seconds,
        })

    async def stop_turn_reaper(self) -> None:
        stop = self._turn_reaper_stop
        task = self._turn_reaper_task
        if task is None:
            return
        if stop is not None:
            stop.set()
        await task
        self._turn_reaper_task = None
        self._turn_reaper_stop = None
        logger.info("session_turn_reaper_stopped")

    async def close(self) -> None:
        self._closing = True
        try:
            await self.stop_turn_reaper()
        finally:
            tasks = {
                task: (kind, key)
                for kind, registry in (
                    ("pending_input", self._queue_drain_tasks),
                    ("approval_forward", self._approval_forward_tasks),
                    ("approval_resume", self._approval_resume_tasks),
                )
                for key, task in list(registry.items())
            }
            logger.info("session_background_tasks_stopping", extra={"task_count": len(tasks)})
            for task in tasks:
                if not task.done():
                    task.cancel()
            results = await self._settle_execution_tasks(*tasks)
            error = None
            for (kind, key), result in zip(tasks.values(), results, strict=True):
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    logger.error("session_background_task_shutdown_failed", extra={
                        "task_kind": kind, "task_id": key, "error_type": type(result).__name__,
                    })
                    if error is None:
                        error = result
            self._queue_drain_tasks.clear()
            self._approval_forward_tasks.clear()
            self._approval_resume_tasks.clear()
            if error is not None:
                raise error
        logger.info("session_background_tasks_stopped")

    async def _turn_reaper_loop(self) -> None:
        stop = self._turn_reaper_stop
        if stop is None:
            return
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._turn_reaper_interval_seconds)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                await self.recover_expired_turn_executions(recovery_source="periodic_reaper")
                await self._schedule_pending_input_drains()
            except Exception:
                logger.exception("session_turn_reaper_failed")

    async def _schedule_pending_input_drains(self) -> None:
        sessions = await self._store.list_sessions_with_pending_input_queue()
        for session in sessions:
            if session.status in {SessionStatus.running, SessionStatus.waiting_approval}:
                continue
            self._maybe_schedule_pending_input_drain(session.id)

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
            self._reject_server_managed_metadata(payload.metadata)
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

    @staticmethod
    def _reject_server_managed_metadata(metadata: dict[str, Any] | None) -> None:
        if not isinstance(metadata, dict):
            return
        forbidden = sorted(SERVER_MANAGED_SESSION_METADATA_KEYS.intersection(metadata))
        if forbidden:
            raise ValueError(
                "Session metadata keys are server-managed and cannot be set by clients: "
                + ", ".join(forbidden)
            )

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
        reason = (payload.reason or "Interrupt requested from session control API.").strip()

        def mark_interrupt_requested(metadata: dict, current_status: SessionStatus) -> None:
            if current_status != SessionStatus.running:
                raise ValueError("Only running sessions can be interrupted.")
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control.update(
                {
                    "control_state": "interrupt_requested",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_interrupt_reason": reason,
                    "last_control_source": payload.source,
                }
            )
            metadata["control"] = control

        if not await self._store.update_status_and_mutate_metadata(
            session_id,
            status=None,
            mutate=mark_interrupt_requested,
        ):
            raise KeyError(session_id)
        self._runtime_service.request_interrupt(session_id, reason)
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
        return await self.get_session(session_id)

    async def resume_session(
        self,
        session_id: str,
        payload: ResumeSessionRequest,
    ) -> ConversationResponse:
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            snapshot = await self._require_resume_snapshot(session_id)
            turn_id = self._resume_turn_id(snapshot)
            turn_lease_token = str(uuid4())
            claimed_session = await self._store.claim_turn_execution(
                session_id=session_id,
                turn_id=turn_id,
                owner_id=self._worker_owner_id,
                lease_token=turn_lease_token,
                lease_seconds=self._turn_execution_lease_seconds,
            )
            if claimed_session is None:
                raise ValueError("Session turn is owned by another worker.")
            return await self._run_owned_resume_locked(
                session=claimed_session,
                snapshot=snapshot,
                payload=payload,
                turn_lease_token=turn_lease_token,
                complete_lease=True,
            )

    async def _require_resume_snapshot(
        self,
        session_id: str,
        *,
        reference_id: str | None = None,
    ) -> SessionSnapshot:
        snapshot = await self._store.get_latest_snapshot(session_id)
        if snapshot is None:
            raise ValueError("No snapshot is available for resume.")
        if reference_id is not None and snapshot.id != reference_id:
            raise ValueError("Resume checkpoint changed before execution.")
        if snapshot.stage == "waiting_approval":
            raise ValueError("Pending approval must be resolved through the approval API.")
        if snapshot.stage not in {"interrupted", "resumable"}:
            raise ValueError("Latest snapshot is not resumable.")
        session = await self._require_session(session_id)
        if snapshot.stage == "interrupted" and not session.metadata.get("pending_turn"):
            raise ValueError(
                "The interrupted runtime has no durable checkpoint and cannot be resumed."
            )
        return snapshot

    @staticmethod
    def _resume_turn_id(snapshot: SessionSnapshot) -> str:
        return str(snapshot.graph_state.get("turn_id") or f"resume_{snapshot.id}")

    async def _run_owned_resume_locked(
        self,
        *,
        session: SessionRecord,
        snapshot: SessionSnapshot,
        payload: ResumeSessionRequest,
        turn_lease_token: str,
        ownership_monitors: tuple[asyncio.Task, ...] = (),
        complete_lease: bool,
    ) -> ConversationResponse:
        session_id = session.id
        turn_id = self._resume_turn_id(snapshot)

        def mark_resuming(metadata: dict, current_status: SessionStatus) -> None:
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control.setdefault("control_state", current_status.value)
            control.update(
                {
                    "control_state": "resuming",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_resume_reason": (payload.reason or "Manual resume requested.").strip(),
                    "last_control_source": payload.source,
                }
            )
            metadata["control"] = control

        if not await self._store.update_status_and_mutate_metadata(
            session_id,
            status=None,
            mutate=mark_resuming,
            turn_lease_token=turn_lease_token,
        ):
            raise KeyError(session_id)
        session = await self._require_session(session_id)

        assistant_message_id = str(uuid4())
        stream_chunk_handler = self._build_stream_chunk_handler(
            session_id=session_id,
            turn_id=turn_id,
            assistant_message_id=assistant_message_id,
            turn_lease_token=turn_lease_token,
        )
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._keep_turn_execution_alive(
                session_id=session_id,
                lease_token=turn_lease_token,
                stop_event=heartbeat_stop,
            )
        )
        settled = False
        try:
            runtime_result = await self._await_owned_execution(
                session_id,
                heartbeat_task,
                stream_chunk_handler.run,
                self._event_sink(session_id, turn_lease_token=turn_lease_token).run,
                self._runtime_service.resume_turn,
                session,
                snapshot,
                resume_reason=payload.reason or "manual_resume",
                on_model_chunk=stream_chunk_handler,
                ownership_monitors=ownership_monitors,
            )
            response = await self._await_owned_execution(
                session_id,
                heartbeat_task,
                self._finalize_runtime_result,
                session=session,
                runtime_result=runtime_result,
                assistant_message_id=assistant_message_id,
                user_message_override=str(runtime_result.state.get("user_message", "")),
                turn_lease_token=turn_lease_token,
                ownership_monitors=ownership_monitors,
            )
            settled = True
            return response
        except asyncio.CancelledError:
            raise
        except ContinuationLeaseLostError:
            logger.warning("turn_resume_fenced_after_lease_loss", extra={"session_id": session_id})
            raise
        except Exception as exc:
            settled = True
            await self._mark_interrupted_after_error(
                session_id,
                truncate_text(str(exc), 240),
                turn_lease_token=turn_lease_token,
            )
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "turn.interrupted",
                    {
                        "turn_id": turn_id,
                        "message": "Manual resume was interrupted before the assistant response was produced.",
                        "error_type": exc.__class__.__name__,
                        "error": truncate_text(str(exc), 240),
                        "is_resumable": True,
                    },
                ),
                turn_lease_token=turn_lease_token,
            )
            raise
        finally:
            healthy = await self._stop_lease_heartbeat(
                session_id,
                heartbeat_stop,
                heartbeat_task,
            )
            if complete_lease and settled and healthy:
                completed = await self._store.complete_turn_execution(
                    session_id,
                    turn_lease_token,
                )
                if not completed:
                    logger.warning(
                        "turn_execution_completion_lease_lost",
                        extra={"session_id": session_id, "turn_id": turn_id},
                    )

    async def replay_session(
        self,
        session_id: str,
        limit: int | None = DEFAULT_REPLAY_EVENT_LIMIT,
    ) -> SessionReplayResponse:
        latest_snapshot = await self._store.get_latest_snapshot(session_id)
        replay_count = 0

        def mark_replay_requested(metadata: dict) -> None:
            nonlocal replay_count
            replay_count = int(metadata.get("replay_requests", 0)) + 1
            metadata["replay_requests"] = replay_count
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control.update(
                {
                    "last_replay_requested_at": datetime.utcnow().isoformat(),
                    "replay_available": True,
                }
            )
            metadata["control"] = control

        if not await self._store.mutate_metadata(session_id, mark_replay_requested):
            raise KeyError(session_id)
        await self._store.append_event(
            session_id,
            self._make_event(
                session_id,
                "session.replay_requested",
                {
                    "message": "A read-only replay of the session history was requested.",
                    "replay_count": replay_count,
                },
            ),
        )
        events = await self._store.list_events(session_id, limit=limit)
        session = await self._require_session(session_id)
        detail = await self._to_detail(session)
        return SessionReplayResponse(
            session_id=session_id,
            control_state=detail.control_state,
            latest_snapshot=self._snapshot_for_detail(latest_snapshot),
            events=events,
            metadata={
                "replay_count": replay_count,
                "snapshot_stage": latest_snapshot.stage if latest_snapshot else "",
                "transcript_summary": self._transcript_hygiene_service.summarize_messages(session.messages),
            },
        )

    async def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        payload: ApprovalDecisionRequest,
    ) -> ToolApprovalRequest | AgentTaskAccepted:
        if payload.decision not in {ToolApprovalStatus.approved, ToolApprovalStatus.denied}:
            raise ValueError("Approval decision must be approved or denied.")
        session = await self._require_session(session_id)
        approvals = await self._store.list_approvals(session_id)
        selected = next((item for item in approvals if item.id == approval_id), None)
        metadata = selected.metadata if selected is not None else {}
        dedicated = (metadata.get("approval_type") == "ui_recording"
            or metadata.get("source") in {"test_run_case_execution", "test_run_agent_execution"}
            or metadata.get("proxy_child_session_id") or metadata.get("proxy_child_approval_id"))
        pending_binding = session.metadata.get("pending_turn", {}).get("context_bundle", {}).get("runtime_binding")
        if not dedicated and (metadata.get("framework_binding_id") is not None or pending_binding is not None):
            if self._agent_task_dispatcher is None:
                raise ValueError("Framework approval requires the durable Agent Worker dispatcher")
            # Admission is a short database transaction. It must remain usable
            # while another decision owns the long Session execution lock.
            return await self._agent_task_dispatcher.submit_approval(session_id, approval_id, payload)
        async with self._session_locks[session_id]:
            if payload.decision not in {ToolApprovalStatus.approved, ToolApprovalStatus.denied}:
                raise ValueError("Approval decision must be approved or denied.")

            session = await self._require_session(session_id)
            existing_approvals = await self._store.list_approvals(session_id)
            proxy_approval = next((item for item in existing_approvals if item.id == approval_id), None)
            proxy_metadata = proxy_approval.metadata if proxy_approval is not None else {}
            if (
                proxy_metadata.get("deep_agent_interrupt_id")
                and proxy_approval.status != ToolApprovalStatus.pending
                and approval_id not in session.metadata.get("pending_turn", {}).get("pending_approval_ids", [])
            ):
                if proxy_approval.status != payload.decision:
                    raise ValueError(f"Approval already resolved: {approval_id}")
                await self._store.append_event(
                    session_id,
                    self._make_event(session_id, "approval.decision_replayed", {
                        "approval_id": approval_id,
                        "decision": payload.decision.value,
                        "message": "Completed HITL decision returned without rescheduling execution.",
                    }),
                )
                return proxy_approval
            if proxy_metadata.get("source") in {"test_run_case_execution", "test_run_agent_execution"}:
                raise ValueError(
                    "Use the test run item approval endpoint for test-run approvals."
                )
            proxy_child_session_id = str(proxy_metadata.get("proxy_child_session_id") or "").strip()
            proxy_child_approval_id = str(proxy_metadata.get("proxy_child_approval_id") or "").strip()

            if (
                self._agent_task_dispatcher is not None
                and not proxy_child_session_id
                and not proxy_child_approval_id
                and proxy_metadata.get("approval_type") != "ui_recording"
                and proxy_metadata.get("source") != "test_run_case_execution"
            ):
                return await self._agent_task_dispatcher.submit_approval(
                    session_id,
                    approval_id,
                    payload,
                )

            approval = await self._store.resolve_approval(
                session_id=session_id,
                approval_id=approval_id,
                status=payload.decision,
                reason=payload.reason,
            )
            if proxy_child_session_id and proxy_child_approval_id:
                await self._append_approval_resolved_event(session_id, approval)
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

            # UI 录制审批（方案 4.2 环节④/⑤，P0-8）：不走 graph 工具重执行链路——
            # approved → RecorderSessionService.launch；denied → 降级事件。
            if approval.metadata.get("approval_type") == "ui_recording":
                await self._append_approval_resolved_event(session_id, approval)
                logger.info(
                    "ui_recording_approval_decision_recorded",
                    extra={
                        "session_id": session_id,
                        "approval_id": approval.id,
                        "decision": approval.status.value,
                        "session_status": session.status.value,
                    },
                )
                if self._recording_approval_service is None:
                    await self._store.append_event(
                        session_id,
                        self._make_event(
                            session_id,
                            "recorder.approval_unavailable",
                            {
                                "approval_id": approval.id,
                                "message": "录制服务未初始化，审批决策无法触发录制启动。",
                            },
                        ),
                    )
                    return approval
                await self._recording_approval_service.apply_decision(
                    approval,
                    decision=payload.decision,
                    reason=payload.reason,
                )
                return approval

            continuation_token = str(uuid4())
            continuation_claimed = await self._store.claim_approval_continuation(
                session_id=session_id,
                approval_id=approval.id,
                owner_id=self._worker_owner_id,
                lease_token=continuation_token,
                lease_seconds=self._approval_continuation_lease_seconds,
            )
            if not continuation_claimed:
                await self._store.append_event(
                    session_id,
                    self._make_event(session_id, "approval.continuation_already_claimed", {
                        "approval_id": approval.id,
                        "tool_key": approval.tool_key,
                        "message": "Another worker owns or completed this approval continuation.",
                    }),
                )
                return approval

            await self._append_approval_resolved_event(session_id, approval)

            def schedule_approval_continuation(
                metadata: dict,
                current_status: SessionStatus,
            ) -> None:
                if current_status not in {SessionStatus.waiting_approval, SessionStatus.running}:
                    raise ValueError("Session is no longer waiting for this approval continuation.")
                control = metadata.get("control")
                control = dict(control) if isinstance(control, dict) else {}
                control.update(
                    {
                        "control_state": "resuming_after_approval",
                        "is_interrupted": False,
                        "is_resumable": False,
                        "last_approval_id": approval.id,
                    }
                )
                metadata["control"] = control

            if not await self._store.update_status_and_mutate_metadata(
                session_id,
                status=SessionStatus.running,
                mutate=schedule_approval_continuation,
                allowed_current_statuses={SessionStatus.waiting_approval},
            ):
                raise KeyError(session_id)
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
                    continuation_token=continuation_token,
                )
            )
            self._approval_resume_tasks[approval.id] = task
            task.add_done_callback(
                lambda _finished, task_id=approval.id: self._approval_resume_tasks.pop(task_id, None)
            )
            return approval

    async def _run_owned_approval_locked(
        self,
        *,
        session: SessionRecord,
        approval: ToolApprovalRequest,
        turn_lease_token: str,
        ownership_monitors: tuple[asyncio.Task, ...] = (),
        capture_snapshot: bool = False,
        framework_task=None,
    ) -> ConversationResponse | SessionSnapshot:
        session_id = session.id
        turn_id = str(session.metadata.get("pending_turn", {}).get("turn_id", ""))
        assistant_message_id = str(uuid4())
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._keep_turn_execution_alive(
                session_id=session_id,
                lease_token=turn_lease_token,
                stop_event=heartbeat_stop,
                framework_task=framework_task,
            )
        )
        def mark_approval_resuming(metadata: dict, current_status: SessionStatus) -> None:
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control.setdefault("control_state", current_status.value)
            control.update(
                {
                    "control_state": "resuming_after_approval",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_approval_id": approval.id,
                }
            )
            metadata["control"] = control

        if not await self._store.update_status_and_mutate_metadata(
            session_id,
            status=None,
            mutate=mark_approval_resuming,
            turn_lease_token=turn_lease_token,
        ):
            raise KeyError(session_id)
        session = await self._require_session(session_id)
        settled = False
        try:
            continuation_options = {}
            projection_asset_holder = {}
            projection_owner = None
            if framework_task is not None:
                from src.runtime.framework_message_stages import FrameworkMessageStages
                from src.schemas.framework_graph_input import parse_graph_input_asset, FrameworkApprovalGraphInput

                stage = await asyncio.to_thread(FrameworkMessageStages(self._agent_task_store).get,
                    framework_task.id, session_id, include_continuations=True)
                source = (stage.source_manifest or {}) if stage is not None else {}
                original_input = source.get("graph_input_asset")
                if original_input is not None and parse_graph_input_asset(original_input).schema_version == 2:
                    projection_owner = framework_task.runtime_owner
                    originals = source.get("approval_graph_inputs", {})
                    previous = originals.get(framework_task.id)
                    if previous is None and framework_task.operation == "approval_recover":
                        previous = originals.get(framework_task.input.get("origin_task_id"))
                    identity = (FrameworkApprovalGraphInput.model_validate(previous).graph_input_asset.runtime_preparation.identity
                        if previous is not None else FrameworkProjectionIdentity(binding=framework_task.runtime_binding,
                            assistant_message_id=assistant_message_id, snapshot_id=str(uuid4()),
                            snapshot_version=session.snapshot_count + 1, snapshot_created_at=datetime.utcnow()))
                    assistant_message_id = identity.assistant_message_id
                    continuation_options["projection_identity"] = identity
                continuation_method = self._runtime_service.resume_framework_approval
                continuation_args = (session, approval.id, framework_task.input["decision_id"])

                async def on_runtime_result(result):
                    asset = await self._prepare_framework_runtime_result(
                        framework_task.runtime_owner, result, assistant_message_id,
                        str(result.state.get("user_message", "")),
                    )
                    if asset is not None:
                        projection_asset_holder["asset"] = asset

                # Historical invocations without original preparation retain
                # their execution contract; they gain no result-recovery right.
                if projection_owner is not None:
                    continuation_options["on_runtime_result"] = on_runtime_result
            else:
                continuation_method = self._runtime_service.resume_after_approval
                continuation_args = (session, approval.model_dump(mode="python"))
            stream_chunk_handler = self._build_stream_chunk_handler(
                session_id=session_id,
                turn_id=turn_id,
                assistant_message_id=assistant_message_id,
                turn_lease_token=turn_lease_token,
            )
            continuation = await self._await_owned_execution(
                session_id,
                heartbeat_task,
                stream_chunk_handler.run,
                self._event_sink(session_id, turn_lease_token=turn_lease_token).run,
                continuation_method,
                *continuation_args,
                on_model_chunk=stream_chunk_handler,
                **continuation_options,
                ownership_monitors=ownership_monitors,
            )
            if continuation is None:
                settled = True
                return ConversationResponse(
                    session=await self._to_detail(session),
                    output=ChatMessage(
                        id=assistant_message_id,
                        role=MessageRole.assistant,
                        content="",
                        created_at=datetime.utcnow(),
                        metadata={"persist_transcript": False, "context_eligible": False},
                    ),
                    events=await self._store.list_events(session_id, limit=RECENT_CONVERSATION_EVENT_LIMIT),
                )
            projection_asset = projection_asset_holder.get("asset")
            if projection_owner is not None and projection_asset is None:
                projection_asset = await self._prepare_framework_runtime_result(
                    framework_task.runtime_owner, continuation, assistant_message_id,
                    str(continuation.state.get("user_message", "")),
                )
            response = await self._await_owned_execution(
                session_id,
                heartbeat_task,
                self._finalize_runtime_result,
                session=session,
                runtime_result=continuation,
                assistant_message_id=assistant_message_id,
                user_message_override=str(continuation.state.get("user_message", "")),
                turn_lease_token=turn_lease_token,
                projection_owner=projection_owner,
                **({"projection_asset": projection_asset} if projection_asset is not None else {}),
                ownership_monitors=ownership_monitors,
            )
            settled = True
            return continuation.snapshot.model_copy(deep=True) if capture_snapshot else response
        except asyncio.CancelledError:
            raise
        except ContinuationLeaseLostError:
            logger.warning(
                "approval_task_fenced_after_lease_loss",
                extra={"session_id": session_id, "approval_id": approval.id},
            )
            raise
        except Exception as exc:
            settled = True
            await self._mark_interrupted_after_error(
                session_id,
                truncate_text(str(exc), 240),
                approval_id=approval.id,
                turn_lease_token=turn_lease_token,
            )
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "approval.continuation_interrupted",
                    {
                        "approval_id": approval.id,
                        "tool_key": approval.tool_key,
                        "message": "Approval continuation was interrupted and preserved for resume.",
                        "error": truncate_text(str(exc), 240),
                    },
                ),
                turn_lease_token=turn_lease_token,
            )
            raise
        finally:
            await self._stop_lease_heartbeat(session_id, heartbeat_stop, heartbeat_task)

    async def _resume_after_approval_async(
        self,
        session_id: str,
        approval: ToolApprovalRequest,
        continuation_token: str,
    ) -> None:
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            assistant_message_id = str(uuid4())
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                self._keep_approval_continuation_alive(
                    session_id=session_id,
                    approval_id=approval.id,
                    continuation_token=continuation_token,
                    stop_event=heartbeat_stop,
                )
            )
            continuation_settled = False
            try:
                stream_chunk_handler = self._build_stream_chunk_handler(
                    session_id=session_id,
                    turn_id=str(session.metadata.get("pending_turn", {}).get("turn_id", "")),
                    assistant_message_id=assistant_message_id,
                    continuation_approval_id=approval.id,
                    continuation_lease_token=continuation_token,
                )
                continuation = await self._await_owned_execution(
                    session_id, heartbeat_task,
                    stream_chunk_handler.run,
                    self._event_sink(session_id, continuation_approval_id=approval.id,
                        continuation_lease_token=continuation_token).run,
                    self._runtime_service.resume_after_approval,
                    session,
                    approval.model_dump(mode="python"),
                    on_model_chunk=stream_chunk_handler,
                )
                if continuation is None:
                    continuation_settled = True
                    return
                await self._await_owned_execution(
                    session_id, heartbeat_task, self._finalize_runtime_result,
                    session=session,
                    runtime_result=continuation,
                    assistant_message_id=assistant_message_id,
                    user_message_override=str(continuation.state.get("user_message", "")),
                    continuation_approval_id=approval.id,
                    continuation_lease_token=continuation_token,
                )
                continuation_settled = True
            except ContinuationLeaseLostError:
                logger.warning(
                    "approval_continuation_fenced_after_lease_loss",
                    extra={"session_id": session_id, "approval_id": approval.id},
                )
            except Exception as exc:
                await self._mark_interrupted_after_error(
                    session_id,
                    truncate_text(str(exc), 240),
                    approval_id=approval.id,
                    continuation_approval_id=approval.id,
                    continuation_lease_token=continuation_token,
                )
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
                    continuation_approval_id=approval.id,
                    continuation_lease_token=continuation_token,
                )
                continuation_settled = True
            finally:
                healthy = await self._stop_lease_heartbeat(session_id, heartbeat_stop, heartbeat_task)
                if continuation_settled and healthy:
                    completed = await self._store.complete_approval_continuation(
                        session_id=session_id,
                        approval_id=approval.id,
                        lease_token=continuation_token,
                    )
                    if not completed:
                        logger.warning(
                            "approval_continuation_completion_lease_lost",
                            extra={"session_id": session_id, "approval_id": approval.id},
                        )

    async def _keep_approval_continuation_alive(
        self,
        *,
        session_id: str,
        approval_id: str,
        continuation_token: str,
        stop_event: asyncio.Event,
    ) -> bool | None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._approval_continuation_heartbeat_seconds,
                )
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                return
            renewed = await self._store.renew_approval_continuation(
                session_id=session_id,
                approval_id=approval_id,
                lease_token=continuation_token,
                lease_seconds=self._approval_continuation_lease_seconds,
            )
            if not renewed:
                logger.error(
                    "approval_continuation_heartbeat_lease_lost",
                    extra={"session_id": session_id, "approval_id": approval_id},
                )
                self._runtime_service.request_interrupt(
                    session_id,
                    "Approval continuation lease was lost to another worker.",
                )
                return False

    async def _keep_turn_execution_alive(
        self,
        *,
        session_id: str,
        lease_token: str,
        stop_event: asyncio.Event,
        framework_task=None,
    ) -> bool | None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._turn_execution_heartbeat_seconds,
                )
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                return
            if framework_task is not None:
                renewed = await self._agent_task_store.renew_framework_execution(
                    framework_task, self._turn_execution_lease_seconds)
            else:
                renewed = await self._store.renew_turn_execution(
                    session_id=session_id,
                    lease_token=lease_token,
                    lease_seconds=self._turn_execution_lease_seconds,
                )
            if not renewed:
                logger.error(
                    "turn_execution_heartbeat_lease_lost",
                    extra={"session_id": session_id},
                )
                self._runtime_service.request_interrupt(
                    session_id,
                    "Turn execution lease was lost to another worker.",
                )
                return False

    async def _append_approval_resolved_event(
        self,
        session_id: str,
        approval: ToolApprovalRequest,
    ) -> None:
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

    async def send_message(self, session_id: str, payload: SendMessageRequest) -> ConversationResponse:
        session_lock = self._session_locks[session_id]
        if session_lock.locked():
            session = await self._require_session(session_id)
            if session.status in BUSY_SUBMISSION_STATUSES:
                execution_request = await self._input_orchestrator_service.orchestrate_async(session, payload)
                busy_response = await self._handle_busy_submission(
                    session=session,
                    payload=payload,
                    execution_request=execution_request,
                )
                if busy_response is not None:
                    return busy_response
            else:
                logger.info("session_input_waiting_for_lock", extra={
                    "session_id": session_id,
                    "session_status": session.status.value,
                    "reason": "lock_held_before_busy_state",
                })

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

    async def execute_case_turn(self, session_id: str, *, context: AgentCaseExecutionContext,
        attempt_id: str, mode_key: str, managed_by_run: bool = False) -> SessionSnapshot:
        """Server-only frozen execution; share normal turn leases, events and snapshots.

        The caller must build context from persisted versions and validate its
        own item lease before calling. No HTTP route accepts this frozen payload.
        """
        if not attempt_id:
            raise ValueError("固定用例执行缺少 attempt 身份")
        turn_id = str(uuid5(NAMESPACE_URL, f"case-run:{context.run_id}:{context.run_item_id}:{attempt_id}"))
        frozen = context.execution_json()
        mode = self._mode_registry.resolve(mode_key)
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            if session.project_id != context.project_id:
                raise ValueError("固定用例执行会话与正式项目不一致")
            for snapshot in reversed(await self._store.list_snapshots(session_id, include_graph_state=True)):
                state = snapshot.graph_state
                if state.get("turn_id") != turn_id:
                    continue
                saved = state.get("context_bundle") or {}
                if (saved.get("case_execution") != frozen or saved.get("attempt_id") != attempt_id
                    or bool(saved.get("case_execution_managed")) != managed_by_run
                    or state.get("mode_key") != mode.key):
                    raise ValueError("相同执行身份的冻结上下文或模式发生变化")
                logger.info("case_session_snapshot_replayed", extra={"session_id": session_id,
                    "run_id": context.run_id, "run_item_id": context.run_item_id,
                    "attempt_id": attempt_id, "snapshot_id": snapshot.id, "stage": snapshot.stage})
                # Pending or unknown outcomes also replay: recovery must inspect
                # the existing snapshot rather than start a new turn and repeat effects.
                return snapshot.model_copy(deep=True)
            payload = SendMessageRequest(content=context.objective, mode_key=mode.key,
                source="test_run.agent_execution")
            # Selection and planning are already frozen. Keep the normal mode and
            # permission context without another semantic-intent model request.
            request = self._input_orchestrator_service.orchestrate(session, payload)
            if managed_by_run and mode.key == "ui_automation":
                # Frozen RunItems have a dedicated execution and evidence contract.
                # The interactive E2E Skill prescribes a separate file/report
                # workflow that duplicates the RunItem's persisted receipt and can
                # consume the fixed turn budget. Keep the CLI command Skill, whose
                # runtime instructions are part of the actual browser tool contract.
                request.skill_keys = [key for key in request.skill_keys if key != "playwright-e2e-testing"]
            request.turn_id = turn_id
            if request.routing_decision is not None:
                request.routing_decision.queue_behavior = "reject_when_busy"
            request.context.update(context.runtime_context(), attempt_id=attempt_id,
                case_assessment_requested=True, case_execution_managed=managed_by_run,
                trusted_security_runtime_direct_execution=False)
            await self._handle_busy_submission(session=session, payload=payload, execution_request=request)
            logger.info("case_session_turn_started", extra={"session_id": session_id,
                "run_id": context.run_id, "run_item_id": context.run_item_id,
                "attempt_id": attempt_id, "plan_id": context.plan_id, "mode_key": mode.key})
            snapshot = await self._run_submission_locked(session=session, payload=payload,
                execution_request=request, capture_snapshot=True)
            if not isinstance(snapshot, SessionSnapshot):
                raise RuntimeError("固定用例执行未返回已持久化快照")
            return snapshot

    async def resume_case_approval(self, session_id: str, *, context: AgentCaseExecutionContext,
        attempt_id: str, mode_key: str, approval_id: str,
        payload: ApprovalDecisionRequest) -> SessionSnapshot:
        """Server-owned synchronous continuation; caller maintains the RunItem lease."""
        if payload.decision not in {ToolApprovalStatus.approved, ToolApprovalStatus.denied}:
            raise ValueError("Approval decision must be approved or denied.")
        turn_id = str(uuid5(NAMESPACE_URL, f"case-run:{context.run_id}:{context.run_item_id}:{attempt_id}"))
        async with self._session_locks[session_id]:
            session = await self._require_session(session_id)
            snapshot = await self._store.get_latest_snapshot(session_id)
            state = snapshot.graph_state if snapshot is not None else {}
            saved = state.get("context_bundle") or {}
            if (session.project_id != context.project_id or state.get("turn_id") != turn_id
                or saved.get("case_execution") != context.execution_json()
                or saved.get("attempt_id") != attempt_id or not saved.get("case_execution_managed")
                or state.get("mode_key") != self._mode_registry.resolve(mode_key).key):
                raise ValueError("审批续执行与固定运行身份或内容不一致")
            approvals = await self._store.list_approvals(session_id)
            approval = next((entry for entry in approvals if entry.id == approval_id), None)
            if (approval is None or approval.metadata.get("source") != "test_run_agent_execution"
                or approval.metadata.get("turn_id") != turn_id):
                raise ValueError("审批不属于本次固定用例执行")
            if approval.status not in {ToolApprovalStatus.pending, payload.decision}:
                raise ValueError("审批已经以不同决定结束")
            if approval_id not in session.metadata.get("pending_turn", {}).get("pending_approval_ids", []):
                if approval.status != payload.decision:
                    raise ValueError("审批已不在当前待执行集合")
                return snapshot.model_copy(deep=True)
            token = str(uuid4())
            claimed = await self._store.claim_turn_execution(session_id=session_id, turn_id=turn_id,
                owner_id=self._worker_owner_id, lease_token=token, lease_seconds=self._turn_execution_lease_seconds,
                approval_id=approval_id)
            if claimed is None:
                raise ValueError("Session turn is owned by another worker.")
            settled = False
            try:
                resolved = await self._store.resolve_approval(session_id=session_id, approval_id=approval_id,
                    status=payload.decision, reason=payload.reason)
                if resolved.status != payload.decision:
                    raise ValueError("审批被并发决议为不同状态")
                await self._append_approval_resolved_event(session_id, resolved)
                result = await self._run_owned_approval_locked(session=claimed, approval=resolved,
                    turn_lease_token=token, capture_snapshot=True)
                if not isinstance(result, SessionSnapshot):
                    raise RuntimeError("固定用例审批续执行未返回持久快照")
                settled = True
                return result
            finally:
                if settled:
                    await self._store.complete_turn_execution(session_id, token)
                else:
                    logger.warning("case_approval_lease_preserved_for_recovery", extra={
                        "session_id": session_id, "approval_id": approval_id, "run_item_id": context.run_item_id})

    def _event_sink(self, session_id: str, **fence) -> SessionEventSink:
        return SessionEventSink(self._store, session_id, capacity=self._event_buffer_capacity,
            fence=fence, runtime_owner=current_framework_owner())

    async def _await_owned_execution(
        self,
        session_id: str,
        heartbeat: asyncio.Task,
        operation,
        *args,
        ownership_monitors: tuple[asyncio.Task, ...] = (),
        **kwargs,
    ):
        """Use the TestRun/Redis worker FIRST_COMPLETED ownership pattern."""
        execution = None
        monitors = (heartbeat, *ownership_monitors)
        try:
            if not any(task.done() for task in monitors):
                execution = asyncio.create_task(operation(*args, **kwargs))
                await asyncio.wait(
                    {execution, *monitors},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            stopped = next((task for task in monitors if task.done()), None)
            if stopped is not None:
                error = None if stopped.cancelled() else stopped.exception()
                logger.error("session_execution_ownership_lost", extra={
                    "session_id": session_id,
                    "error_type": type(error).__name__ if error else "LeaseLost",
                })
                raise ContinuationLeaseLostError(
                    "Session execution heartbeat cannot confirm ownership"
                ) from error
            return await execution
        finally:
            if execution is not None:
                if not execution.done():
                    execution.cancel()
                # External resources and accepted event writes must settle before
                # the caller stops heartbeats or completes the lease.
                await self._settle_execution_tasks(execution)

    @staticmethod
    async def _settle_execution_tasks(*tasks):
        settled = asyncio.gather(*tasks, return_exceptions=True)
        cancelled = False
        while not settled.done():
            try:
                await asyncio.shield(settled)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return settled.result()

    async def _stop_lease_heartbeat(self, session_id, stop, heartbeat) -> bool:
        # Let any in-flight database renewal finish instead of cancelling its
        # to_thread waiter while the transaction is still running.
        stop.set()
        result = (await self._settle_execution_tasks(heartbeat))[0]
        if isinstance(result, BaseException) or result is False:
            logger.warning("session_lease_left_for_recovery", extra={
                "session_id": session_id,
                "error_type": type(result).__name__ if isinstance(result, BaseException) else "LeaseLost",
            })
            return False
        return True

    async def list_event_page(self, session_id: str, *, limit: int, after_event_id: str | None = None):
        return await self._store.read_event_page(session_id, limit=limit, after_event_id=after_event_id)

    async def _run_submission_locked(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
        execution_request,
        *,
        allow_interrupted: bool = False,
        capture_snapshot: bool = False,
    ) -> ConversationResponse | SessionSnapshot:
        turn_lease_token = str(uuid4())
        claimed_session = await self._store.claim_turn_execution(
            session_id=session.id,
            turn_id=execution_request.turn_id,
            owner_id=self._worker_owner_id,
            lease_token=turn_lease_token,
            lease_seconds=self._turn_execution_lease_seconds,
        )
        if claimed_session is None:
            current = await self._require_session(session.id)
            busy_response = await self._handle_busy_submission(
                session=current,
                payload=payload,
                execution_request=execution_request,
            )
            if busy_response is not None:
                return busy_response
            raise ValueError("Session turn is owned by another worker.")
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._keep_turn_execution_alive(
                session_id=session.id,
                lease_token=turn_lease_token,
                stop_event=heartbeat_stop,
            )
        )
        settled = False
        try:
            response = await self._await_owned_execution(
                session.id, heartbeat_task, self._run_owned_submission_locked,
                session=claimed_session,
                payload=payload,
                execution_request=execution_request,
                allow_interrupted=allow_interrupted,
                turn_lease_token=turn_lease_token,
                capture_snapshot=capture_snapshot,
            )
            settled = True
            return response
        except asyncio.CancelledError:
            raise
        except ContinuationLeaseLostError:
            raise
        except Exception:
            settled = True
            raise
        finally:
            healthy = await self._stop_lease_heartbeat(session.id, heartbeat_stop, heartbeat_task)
            if settled and healthy:
                completed = await self._store.complete_turn_execution(
                    session.id, turn_lease_token
                )
                if not completed:
                    logger.warning(
                        "turn_execution_completion_lease_lost",
                        extra={"session_id": session.id},
                    )

    async def _run_owned_submission_locked(
        self,
        session: SessionRecord,
        payload: SendMessageRequest,
        execution_request,
        *,
        allow_interrupted: bool,
        turn_lease_token: str,
        user_message_id: str | None = None,
        capture_snapshot: bool = False,
        on_graph_input: Callable[[dict], Awaitable[None]] | None = None,
        projection_owner=None,
    ) -> ConversationResponse | SessionSnapshot:
        session_id = session.id
        turn_fenced_write = {"turn_lease_token": turn_lease_token}
        superseded_turn_id = ""
        worker_context_messages = list(session.messages) if user_message_id is not None else None

        def restore_worker_context_messages(target: SessionRecord) -> None:
            if worker_context_messages is None:
                return
            latest_messages = {message.id: message for message in target.messages}
            target.messages = [
                deepcopy(latest_messages.get(message.id, message))
                for message in worker_context_messages
            ]

        await self._store.assert_turn_execution(session_id, turn_lease_token)
        # claim_turn_execution returns a detached value; start from the newest
        # row so concurrent queue/control projections are not copied backward.
        session = await self._require_session(session_id)
        restore_worker_context_messages(session)

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
                **turn_fenced_write,
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
        if user_message_id is not None:
            user_message_metadata.update(
                turn_id=execution_request.turn_id,
                delivery_status="delivered",
                context_eligible=True,
            )
        if execution_request.message_kind in {
            MessageKind.task_notification,
            MessageKind.coordinator_assignment,
        }:
            user_message_metadata.update(
                {
                    "persist_transcript": False,
                    "context_eligible": False,
                    "transcript_bucket": "event",
                }
            )

        user_message = ChatMessage(
            id=user_message_id or str(uuid4()),
            role=MessageRole.user,
            content=payload.content.strip(),
            created_at=datetime.utcnow(),
            metadata=user_message_metadata,
        )
        target_mode_key = session.mode_key
        target_selected_agent = session.selected_agent
        target_preferred_model = session.preferred_model
        for attempt in range(3):
            candidate = await self._require_session(session_id)
            restore_worker_context_messages(candidate)
            await self._store.assert_turn_execution(session_id, turn_lease_token)
            candidate.status = SessionStatus.running
            candidate.mode_key = target_mode_key
            candidate.selected_agent = target_selected_agent
            candidate.preferred_model = target_preferred_model
            candidate.metadata["pending_turn"] = {}
            control = self._ensure_control_metadata(candidate)
            queued_entries = self._get_pending_input_queue(candidate)
            control.update(
                {
                    "control_state": "active_turn",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_turn_id": execution_request.turn_id,
                    "queued_input_count": len(queued_entries),
                    "last_superseded_turn_id": superseded_turn_id or str(
                        control.get("last_superseded_turn_id") or ""
                    ),
                }
            )
            candidate.metadata["control"] = control
            candidate.messages = list(candidate.messages)
            existing_message = next(
                (index for index, message in enumerate(candidate.messages)
                 if message.id == user_message.id),
                None,
            )
            if existing_message is None:
                candidate.messages.append(user_message)
            else:
                user_message.created_at = candidate.messages[existing_message].created_at
                candidate.messages[existing_message] = user_message
            try:
                await self._store.save_session(candidate, **turn_fenced_write)
                session = candidate
                break
            except SessionWriteConflictError:
                if attempt == 2:
                    raise
                logger.info(
                    "session_input_commit_rebased_after_conflict",
                    extra={"session_id": session_id, "turn_id": execution_request.turn_id,
                           "attempt": attempt + 1},
                )
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
            **turn_fenced_write,
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
            **turn_fenced_write,
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
            **turn_fenced_write,
        )

        assistant_message_id = str(uuid4())
        stream_chunk_handler = self._build_stream_chunk_handler(
            session_id=session_id,
            turn_id=execution_request.turn_id,
            assistant_message_id=assistant_message_id,
            turn_lease_token=turn_lease_token,
        )
        # Reserve projection identities before invoking the bound graph. This
        # timestamp describes snapshot preparation, not graph completion.
        projection_identity = (
            FrameworkProjectionIdentity(binding=projection_owner.binding,
                assistant_message_id=assistant_message_id, snapshot_id=str(uuid4()),
                snapshot_version=session.snapshot_count + 1, snapshot_created_at=datetime.utcnow())
            if projection_owner is not None else None
        )
        try:
            projection_asset_holder = {}

            async def on_runtime_result(result):
                asset = await self._prepare_framework_runtime_result(
                    projection_owner, result, assistant_message_id, payload.content,
                )
                if asset is not None:
                    projection_asset_holder["asset"] = asset

            runtime_result = await stream_chunk_handler.run(
                self._event_sink(session_id, **turn_fenced_write).run,
                self._runtime_service.execute_turn,
                session,
                execution_request,
                on_model_chunk=stream_chunk_handler,
                on_graph_input=on_graph_input,
                on_runtime_result=on_runtime_result,
                **({"projection_identity": projection_identity} if projection_identity is not None else {}),
            )
            projection_asset = projection_asset_holder.get("asset")
            # Legacy/test RuntimeService implementations may not expose the
            # optional boundary callback yet. Keep their behavior compatible;
            # the production RuntimeService persists before returning.
            if projection_asset is None:
                projection_asset = await self._prepare_framework_runtime_result(
                    projection_owner, runtime_result, assistant_message_id, payload.content,
                )
            response = await self._finalize_runtime_result(
                session=session,
                runtime_result=runtime_result,
                assistant_message_id=assistant_message_id,
                user_message_override=payload.content,
                turn_lease_token=turn_lease_token,
                projection_owner=projection_owner,
                **({"projection_asset": projection_asset} if projection_asset is not None else {}),
            )
            return runtime_result.snapshot.model_copy(deep=True) if capture_snapshot else response
        except ContinuationLeaseLostError:
            logger.warning(
                "turn_execution_fenced_after_lease_loss",
                extra={"session_id": session_id, "turn_id": execution_request.turn_id},
            )
            raise
        except Exception as exc:
            latest_snapshot = await self._store.get_latest_snapshot(session_id)
            is_resumable = latest_snapshot is not None and latest_snapshot.stage in {
                "waiting_approval",
                "interrupted",
                "resumable",
            }
            await self._mark_interrupted_after_error(
                session_id,
                truncate_text(str(exc), 240),
                is_resumable=is_resumable,
                turn_lease_token=turn_lease_token,
            )
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
                **turn_fenced_write,
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
        if busy_status not in BUSY_SUBMISSION_STATUSES:
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
                "trace_id": execution_request.trace_id,
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
        if self._agent_task_dispatcher is not None:
            return await self._submit_durable_pending_input(
                session_id,
                payload,
                idempotency_key=self._durable_pending_input_idempotency_key(
                    session_id, payload
                ),
                request_interrupt=request_interrupt,
                execution_request=execution_request,
            )

        queued_event = self._make_event(
            session_id,
            "input.queued",
            {
                "queue_entry_id": queue_entry.id,
                "busy_status": busy_status.value,
                "queue_behavior": queue_entry.queue_behavior,
                "interrupt_policy": queue_entry.interrupt_policy,
                "queue_depth": 0,
                "queued_turn_id": execution_request.turn_id,
                "message_kind": execution_request.message_kind.value,
                "command_name": execution_request.command_name or "",
                "interrupt_requested": request_interrupt,
                "message": queue_entry.reason,
            },
        )

        def append_queued_input(metadata: dict) -> bool:
            queue_entries = self._parse_pending_input_queue(metadata.get("pending_input_queue", []))
            queue_entries.append(queue_entry)
            metadata["pending_input_queue"] = [item.model_dump(mode="python") for item in queue_entries]
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control["queued_input_count"] = len(queue_entries)
            if request_interrupt:
                control["control_state"] = "interrupt_requested"
                control["last_interrupt_reason"] = "Queued retry requested by a follow-up input."
            metadata["control"] = control
            queued_event.payload["queue_depth"] = len(queue_entries)
            return True

        if not await self._store.update_metadata_and_append_event(
            session_id, append_queued_input, queued_event,
        ):
            raise KeyError(session_id)
        if request_interrupt:
            await self._request_queued_input_interrupt(
                session_id, queue_entry_id=queue_entry.id
            )

        refreshed = await self._require_session(session_id)
        if refreshed.status not in {SessionStatus.running, SessionStatus.waiting_approval}:
            self._maybe_schedule_pending_input_drain(session_id)
        return await self._build_queued_response(session, queue_entry)

    @staticmethod
    def _durable_pending_input_idempotency_key(
        session_id: str,
        payload: SendMessageRequest,
    ) -> str | None:
        if "client_message_id" in payload.metadata:
            return payload.metadata["client_message_id"]
        metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
        context = payload.context if isinstance(payload.context, dict) else {}
        message_kind = str(metadata.get("message_kind") or payload.message_kind.value)
        task_id = str(metadata.get("task_id") or context.get("task_id") or "").strip()
        parent_turn_id = str(
            metadata.get("parent_turn_id") or context.get("parent_turn_id") or ""
        ).strip()
        if (
            message_kind not in {"coordinator_assignment", "task_notification"}
            or not task_id
            or not parent_turn_id
        ):
            return None
        return str(
            uuid5(
                NAMESPACE_URL,
                f"enterprise-ai-qa-agent:queued-message:{session_id}:"
                f"{parent_turn_id}:{message_kind}:{task_id}",
            )
        )

    async def _submit_durable_pending_input(
        self,
        session_id: str,
        payload: SendMessageRequest,
        *,
        idempotency_key: str | None,
        request_interrupt: bool,
        execution_request=None,
    ):
        queued_payload = payload.model_copy(deep=True)
        queued_payload.submit_mode = "queued"
        queued_payload.interrupt_if_busy = (
            queued_payload.interrupt_if_busy or request_interrupt
        )
        metadata = dict(queued_payload.metadata)
        if "client_message_id" not in metadata and idempotency_key:
            metadata["client_message_id"] = idempotency_key
        queued_payload.metadata = metadata
        receipt = await self._agent_task_dispatcher.submit(
            session_id,
            queued_payload,
            execution_request=execution_request,
        )
        if request_interrupt:
            task = getattr(receipt, "task", None)
            await self._request_queued_input_interrupt(
                session_id,
                queue_entry_id=str(getattr(task, "id", "") or idempotency_key or ""),
            )
        return receipt

    async def _request_queued_input_interrupt(
        self, session_id: str, *, queue_entry_id: str
    ) -> None:
        try:
            self._runtime_service.request_interrupt(
                session_id,
                "Queued retry requested by a follow-up input.",
            )
        except Exception:
            logger.exception(
                "queued_input_runtime_interrupt_failed",
                extra={"session_id": session_id, "queue_entry_id": queue_entry_id},
            )
            return
        try:
            await self._store.append_event(
                session_id,
                self._make_event(
                    session_id,
                    "runtime.interrupt_requested",
                    {
                        "message": "Interrupt requested so the queued input can run next.",
                        "reason": "Queued retry requested by a follow-up input.",
                        "source": "queued_input",
                        "queue_entry_id": queue_entry_id,
                    },
                ),
            )
        except Exception:
            logger.exception(
                "queued_input_interrupt_event_failed",
                extra={"session_id": session_id, "queue_entry_id": queue_entry_id},
            )

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
        return self._parse_pending_input_queue(session.metadata.get("pending_input_queue", []))

    @staticmethod
    def _parse_pending_input_queue(raw_items: object) -> list[PendingInputQueueEntry]:
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
        if self._closing:
            return
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

    async def _remove_pending_input_entry(
        self,
        session_id: str,
        entry: PendingInputQueueEntry,
        *,
        agent_task_id: str | None = None,
    ) -> bool:
        event = self._make_event(
            session_id,
            "input.dequeued",
            {
                "queue_entry_id": entry.id,
                "agent_task_id": agent_task_id or "",
                "message": (
                    "Queued input was handed to the durable Agent task queue."
                    if agent_task_id
                    else "Queued input was claimed for local execution."
                ),
            },
        )

        def remove_entry(metadata: dict[str, Any]) -> bool:
            entries = self._parse_pending_input_queue(
                metadata.get("pending_input_queue", [])
            )
            if not entries or entries[0].id != entry.id:
                return False
            remaining = entries[1:]
            metadata["pending_input_queue"] = [
                item.model_dump(mode="python") for item in remaining
            ]
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control["queued_input_count"] = len(remaining)
            metadata["control"] = control
            return True

        return await self._store.update_metadata_and_append_event(
            session_id, remove_entry, event
        )

    async def _drain_pending_inputs(self, session_id: str) -> None:
        while True:
            async with self._session_locks[session_id]:
                session = await self._require_session(session_id)
                queue_entries = self._get_pending_input_queue(session)
                if not queue_entries:
                    def refresh_queued_input_count(metadata: dict[str, Any]) -> None:
                        control = metadata.get("control")
                        control = dict(control) if isinstance(control, dict) else {}
                        control["queued_input_count"] = len(
                            self._parse_pending_input_queue(
                                metadata.get("pending_input_queue", [])
                            )
                        )
                        metadata["control"] = control

                    if not await self._store.mutate_metadata(
                        session_id, refresh_queued_input_count
                    ):
                        logger.warning(
                            "pending_input_queue_session_missing",
                            extra={"session_id": session_id},
                        )
                    return
                if session.status in {SessionStatus.running, SessionStatus.waiting_approval}:
                    return

                next_entry = queue_entries[0]
                if self._agent_task_dispatcher is not None:
                    try:
                        execution_request = (
                            await self._input_orchestrator_service.orchestrate_async(
                                session,
                                next_entry.payload,
                                turn_id=str(next_entry.metadata.get("turn_id") or "") or None,
                                trace_id=str(next_entry.metadata.get("trace_id") or "") or None,
                            )
                        )
                        receipt = await self._submit_durable_pending_input(
                            session_id,
                            next_entry.payload,
                            idempotency_key=next_entry.id,
                            request_interrupt=False,
                            execution_request=execution_request,
                        )
                        task = getattr(receipt, "task", None)
                        task_id = str(getattr(task, "id", "") or "")
                        removed = await self._remove_pending_input_entry(
                            session_id, next_entry, agent_task_id=task_id
                        )
                    except Exception:
                        logger.exception(
                            "pending_input_durable_handoff_failed",
                            extra={
                                "session_id": session_id,
                                "queue_entry_id": next_entry.id,
                            },
                        )
                        return
                    if not removed:
                        continue
                    logger.info(
                        "pending_input_handed_to_durable_agent_task",
                        extra={
                            "session_id": session_id,
                            "queue_entry_id": next_entry.id,
                            "agent_task_id": task_id,
                        },
                    )
                    continue

                try:
                    execution_request = (
                        await self._input_orchestrator_service.orchestrate_async(
                            session,
                            next_entry.payload,
                            turn_id=str(next_entry.metadata.get("turn_id") or "") or None,
                            trace_id=str(next_entry.metadata.get("trace_id") or "") or None,
                        )
                    )
                except Exception:
                    logger.exception(
                        "pending_input_orchestration_failed",
                        extra={
                            "session_id": session_id,
                            "queue_entry_id": next_entry.id,
                            "turn_id": str(next_entry.metadata.get("turn_id") or ""),
                        },
                    )
                    return
                if not await self._remove_pending_input_entry(session_id, next_entry):
                    continue
                current_session = await self._require_session(session_id)
                await self._run_submission_locked(
                    session=current_session,
                    payload=next_entry.payload,
                    execution_request=execution_request,
                    allow_interrupted=current_session.status == SessionStatus.interrupted,
                )

    @staticmethod
    def _build_framework_transcript_asset(session_id: str, state: dict[str, Any]) -> dict[str, Any] | None:
        """Project Deep Agents' native messages for the next independent Turn."""
        framework_messages = list(state.get("framework_messages") or [])
        context = dict(state.get("context_bundle") or {})
        summary = dict(state.get("model_response_summary") or {})
        if not framework_messages or not (
            summary.get("harness") == "deepagents"
            or context.get("deep_agent_harness") == "deepagents"
        ):
            return None
        binding = dict(context.get("runtime_binding") or {})
        asset = framework_transcript_asset(
            session_id=session_id,
            source_turn_id=str(state.get("turn_id") or ""),
            runtime_version=str(binding.get("runtime_version") or "deepagents-unbound"),
            framework_messages=framework_messages,
        )
        return asset.model_dump(mode="json")

    def _runtime_verification_results(self, session_id, runtime_result):
        return self._verification_service.build_results(
            session_id=session_id,
            turn_id=str(runtime_result.state["turn_id"]),
            trace_id=str(runtime_result.state.get("trace_id", "")),
            tool_results=list(runtime_result.state.get("tool_results", [])),
            context_bundle=dict(runtime_result.state.get("context_bundle", {})),
        )

    async def _prepare_framework_runtime_result(self, owner, runtime_result, assistant_message_id, user_message):
        if owner is None or self._control_state_from_runtime_result(runtime_result) != "completed":
            return None
        if owner != current_framework_owner() or self._agent_task_store is None:
            raise ContinuationLeaseLostError("Framework result preparation requires its captured message owner")
        from src.runtime.framework_runtime_results import FrameworkRuntimeResults

        return await FrameworkRuntimeResults(self._agent_task_store).freeze(owner,
            runtime_result=runtime_result, assistant_message_id=assistant_message_id, user_message=user_message,
            verification_results=self._runtime_verification_results(owner.binding.session_id, runtime_result))

    async def _finalize_runtime_result(
        self,
        session: SessionRecord,
        runtime_result,
        assistant_message_id: str,
        user_message_override: str,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
        projection_owner=None,
        projection_asset=None,
    ) -> ConversationResponse:
        session_id = session.id
        if projection_asset is not None:
            if (projection_owner != current_framework_owner() or projection_owner is None
                or projection_asset.binding != projection_owner.binding):
                raise ContinuationLeaseLostError("Frozen finalization requires its captured framework owner")
            from src.runtime.framework_runtime_results import FrameworkRuntimeResults
            from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired

            persisted_asset = await FrameworkRuntimeResults(self._agent_task_store).load(projection_owner)
            if persisted_asset != projection_asset:
                raise FrameworkMessageStageRecoveryRequired("Finalization input differs from its persisted original result")
            projection_asset = persisted_asset
            runtime_result = projection_asset.runtime_result.restore()
            assistant_message_id = projection_asset.assistant_message_id
            user_message_override = projection_asset.user_message
        projection_at = projection_asset.projection_at if projection_asset is not None else None

        def finalization_event(session_id, event_type, payload):
            event = self._make_event(session_id, event_type, payload)
            if projection_asset is not None:
                event.id = projection_asset.event_id(event_type)
                event.timestamp = projection_at
            return event

        fenced_write = {
            "continuation_approval_id": continuation_approval_id,
            "continuation_lease_token": continuation_lease_token,
            "turn_lease_token": turn_lease_token,
        }
        framework_projection = None
        if projection_owner is not None and self._control_state_from_runtime_result(runtime_result) == "completed":
            from src.schemas.runtime_binding import RuntimeBinding
            from src.infrastructure.storage_utils import make_json_safe

            binding_data = (runtime_result.state.get("context_bundle") or {}).get("runtime_binding")
            binding = RuntimeBinding.model_validate(binding_data)
            if projection_owner != current_framework_owner() or projection_owner.binding != binding:
                raise RuntimeError("Framework application projection owner does not match runtime binding")
            framework_projection = FrameworkProjectionWrite(
                owner=projection_owner,
                snapshot_id=runtime_result.snapshot.id,
                snapshot_sha256=approval_value_hash(make_json_safe(runtime_result.snapshot.graph_state)),
                output_message_id=assistant_message_id if runtime_result.output_text else None,
                runtime_result_asset_sha256=(projection_asset.asset_sha256
                    if projection_asset is not None else None),
            )
        event_fenced_write = dict(fenced_write)
        if projection_asset is not None:
            event_fenced_write["framework_projection"] = framework_projection
        model_response_summary = runtime_result.state.get("model_response_summary", {})
        response_mode = str(model_response_summary.get("mode") or "ok")
        framework_asset = self._build_framework_transcript_asset(session_id, runtime_result.state)
        # Live graph events are already durable. Stable IDs make this flush
        # idempotent, including runtimes which only return a final event log.
        for event in runtime_result.events:
            await self._store.append_event(
                session_id,
                event,
                **event_fenced_write,
            )

        for tool_message in runtime_result.tool_messages:
            session.messages.append(tool_message)

        for approval_data in runtime_result.approvals:
            approval = ToolApprovalRequest.model_validate(approval_data)
            binding_data = runtime_result.state.get("context_bundle", {}).get("runtime_binding")
            if binding_data is not None:
                from src.schemas.runtime_binding import RuntimeBinding

                owner = current_framework_owner()
                if owner is None or owner.binding != RuntimeBinding.model_validate(binding_data):
                    raise RuntimeError("Bound approval finalization requires its captured runtime owner")
                # The owner transaction already committed Approval/ToolJob/link
                # and its stable event. A human may since have decided it.
                # Read and validate that projection; never upsert the old copy.
                await self._runtime_service.load_framework_approval(approval.id)
                continue
            await self._store.save_approval(session_id, approval)
            await self._store.append_event(
                session_id,
                finalization_event(
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
                **fenced_write,
            )

        saved_snapshot = await self._store.save_snapshot(
            session_id, runtime_result.snapshot,
            **({"framework_projection": framework_projection} if projection_asset is not None else {}),
            **fenced_write,
        )
        if projection_asset is not None:
            # The store may allocate a different version under its Session
            # lock. Preserve the immutable intent, but publish the actual row.
            runtime_result.snapshot = saved_snapshot
        session.metadata["pending_turn"] = runtime_result.pending_turn
        await self._store.append_event(
            session_id,
            finalization_event(
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
            **event_fenced_write,
        )

        control = self._ensure_control_metadata(session)
        control_state = self._control_state_from_runtime_result(runtime_result)
        is_resumable = bool(runtime_result.pending_turn) or control_state == "waiting_approval"
        control.update(
            {
                "control_state": control_state,
                "is_interrupted": control_state == "interrupted",
                "is_resumable": is_resumable,
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
                "updated_at": (projection_at or datetime.utcnow()).isoformat(),
            }
        verification_results = (list(projection_asset.verification_results) if projection_asset is not None
            else self._runtime_verification_results(session_id, runtime_result))
        if verification_results:
            existing_results = session.metadata.get("verification_results", [])
            if not isinstance(existing_results, list):
                existing_results = []
            serialized_results = [item.model_dump(mode="python") for item in verification_results]
            session.metadata["verification_results"] = [*existing_results, *serialized_results]
            await self._store.append_event(
                session_id,
                finalization_event(
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
                **event_fenced_write,
            )
        await self._persist_tool_observations(
            session=session,
            turn_id=str(runtime_result.state["turn_id"]),
            trace_id=str(runtime_result.state.get("trace_id", "")),
            tool_results=list(runtime_result.state.get("tool_results", [])),
            context_bundle=dict(runtime_result.state.get("context_bundle", {})),
            fenced_write=fenced_write,
            framework_projection=framework_projection if projection_asset is not None else None,
            projection_asset=projection_asset,
        )

        assistant_message = self._transcript_hygiene_service.finalization_assistant_message(
            message_id=(runtime_result.snapshot.id if projection_owner is not None and not runtime_result.output_text
                else assistant_message_id),
            content=runtime_result.output_text,
            created_at=projection_at or datetime.utcnow(),
            state=runtime_result.state,
        )
        completion_events = []
        if runtime_result.output_text:
            session.messages.append(assistant_message)
            completion_events.append(
                finalization_event(
                    session_id,
                    "assistant.stream.completed",
                    {
                        "turn_id": runtime_result.state["turn_id"],
                        "message_id": assistant_message_id,
                        "response_length": len(runtime_result.output_text),
                    },
                ),
            )
            completion_events.append(
                finalization_event(
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

        completion_event = finalization_event(
            session_id,
            "turn.completed" if self._session_status_from_runtime_result(runtime_result) != SessionStatus.interrupted else "turn.interrupted",
            {
                "turn_id": runtime_result.state["turn_id"],
                "message": "User turn artifacts were persisted.",
                "session_status": self._session_status_from_runtime_result(runtime_result).value,
                "event_count": session.event_count,
                "snapshot_count": session.snapshot_count,
                "approval_count": len(runtime_result.approvals),
            },
        )
        completion_events.append(completion_event)

        for attempt in range(3):
            latest_session = await self._require_session(session_id)
            latest_queue_entries = self._get_pending_input_queue(latest_session)

            # Re-apply only fields owned by this completed turn onto the latest
            # detached record. This preserves concurrent preferences, queue
            # updates, delivery metadata, and unrelated worker notifications.
            session.title = latest_session.title
            session.session_mode = latest_session.session_mode
            session.runtime_mode = latest_session.runtime_mode
            session.mode_key = latest_session.mode_key
            session.project_id = latest_session.project_id
            session.preferred_model = latest_session.preferred_model
            session.selected_agent = latest_session.selected_agent
            session.created_at = latest_session.created_at
            session.event_count = latest_session.event_count
            session.snapshot_count = latest_session.snapshot_count
            session.state_revision = latest_session.state_revision

            latest_messages = list(latest_session.messages)
            latest_message_ids = {item.id for item in latest_messages}
            for message in session.messages:
                if message.id not in latest_message_ids:
                    latest_messages.append(message)
                    latest_message_ids.add(message.id)
            session.messages = latest_messages

            metadata = deepcopy(latest_session.metadata)
            metadata["pending_turn"] = runtime_result.pending_turn
            if framework_asset is not None:
                metadata["framework_transcript_asset"] = framework_asset
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control.update(
                {
                    "control_state": control_state,
                    "is_interrupted": control_state == "interrupted",
                    "is_resumable": is_resumable,
                    "preserve_resources": control_state in {"waiting_approval", "interrupted", "resumable"},
                    "replay_available": True,
                    "last_snapshot_stage": runtime_result.snapshot.stage,
                    "last_turn_id": runtime_result.state["turn_id"],
                }
            )
            metadata["control"] = control
            if int(turn_usage.get("prompt_tokens") or 0) > 0:
                metadata["context_usage"] = {
                    "prompt_tokens": int(turn_usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(turn_usage.get("completion_tokens") or 0),
                    "total_tokens": int(turn_usage.get("total_tokens") or 0),
                    "context_window": int(runtime_result.state.get("model_context_window") or 0),
                    "turn_id": str(runtime_result.state.get("turn_id") or ""),
                    "updated_at": (projection_at or datetime.utcnow()).isoformat(),
                }
            if verification_results:
                existing_results = metadata.get("verification_results", [])
                if projection_asset is not None:
                    from src.runtime.framework_finalization_projection import merge_original_verifications

                    metadata["verification_results"] = merge_original_verifications(existing_results, projection_asset)
                else:
                    if not isinstance(existing_results, list):
                        existing_results = []
                    existing_by_id = {
                        str(item.get("id")): item
                        for item in existing_results
                        if isinstance(item, dict) and item.get("id")
                    }
                    for result in verification_results:
                        result_payload = result.model_dump(mode="python")
                        existing_by_id[str(result_payload["id"])] = result_payload
                    metadata["verification_results"] = list(existing_by_id.values())
            session.metadata = metadata
            self._set_pending_input_queue(session, latest_queue_entries)
            session.status = self._session_status_from_runtime_result(runtime_result)
            completion_event.payload["event_count"] = session.event_count
            completion_event.payload["snapshot_count"] = session.snapshot_count

            try:
                await self._store.save_session_with_events(session, completion_events,
                    **({"framework_projection": framework_projection} if framework_projection is not None else {}),
                    **fenced_write)
                break
            except SessionWriteConflictError:
                if attempt == 2:
                    raise
                logger.info(
                    "session_turn_commit_rebased_after_conflict",
                    extra={"session_id": session_id, "turn_id": runtime_result.state["turn_id"],
                           "attempt": attempt + 1},
                )
        if runtime_result.output_text and response_mode == "ok":
            try:
                await self._persist_turn_memory(
                    session=session,
                    turn_id=runtime_result.state["turn_id"],
                    trace_id=runtime_result.state["trace_id"],
                    user_message=user_message_override,
                    assistant_message=runtime_result.output_text,
                    tool_results=list(runtime_result.state.get("tool_results", [])),
                    context_bundle=dict(runtime_result.state.get("context_bundle", {})),
                    fenced_write=fenced_write,
                )
            except Exception:
                # Transcript and completion events have already committed. Memory is
                # derived data and must not turn the committed answer into an API error.
                logger.exception("turn_memory_persist_failed_after_commit", extra={
                    "session_id": session_id,
                    "turn_id": runtime_result.state["turn_id"],
                })
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

        pending_approvals = [
            item for item in await self._store.list_approvals(session.id)
            if item.status == ToolApprovalStatus.pending
        ]
        def apply_proxy_projection(current_metadata: dict, current_status: SessionStatus) -> None:
            worker_dispatches = current_metadata.get("worker_dispatches", [])
            if not isinstance(worker_dispatches, list):
                worker_dispatches = []
            projected_dispatches = []
            changed = False
            for record in worker_dispatches:
                if not isinstance(record, dict):
                    continue
                record_task_id = str(record.get("task_id") or "").strip()
                record_child_session_id = str(record.get("child_session_id") or "").strip()
                if ((task_id and record_task_id == task_id)
                    or (child_session_id and record_child_session_id == child_session_id)):
                    projected_dispatches.append({
                        **record,
                        "status": execution_status,
                        "child_session_id": child_session_id or record_child_session_id,
                        "updated_at": datetime.utcnow().isoformat(),
                    })
                    changed = True
                else:
                    projected_dispatches.append(record)
            if not changed and task_id:
                projected_dispatches.append({
                    "task_id": task_id,
                    "child_session_id": child_session_id,
                    "agent_key": worker_agent_key,
                    "description": str(metadata.get("tool_name") or metadata.get("tool_key") or "Worker"),
                    "status": execution_status,
                    "updated_at": datetime.utcnow().isoformat(),
                })
                changed = True
            if changed:
                current_metadata["worker_dispatches"] = projected_dispatches

            control = current_metadata.get("control")
            if (
                current_status == SessionStatus.waiting_approval
                and not pending_approvals
                and isinstance(control, dict)
                and control.get("control_state") == "waiting_approval"
            ):
                current_metadata["control"] = {
                    **control,
                    "control_state": "running",
                    "is_interrupted": False,
                    "is_resumable": False,
                    "last_approval_id": approval.id,
                }

        status = SessionStatus.running if not pending_approvals else None
        await self._store.update_status_and_mutate_metadata(
            session.id,
            status=status,
            allowed_current_statuses={SessionStatus.waiting_approval},
            mutate=apply_proxy_projection,
        )
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

    async def _mark_interrupted_after_error(
        self,
        session_id: str,
        reason: str,
        *,
        is_resumable: bool = True,
        approval_id: str | None = None,
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> None:
        allowed_statuses = {
            SessionStatus.running,
            SessionStatus.waiting_approval,
            SessionStatus.interrupted,
        }

        def mark_interrupted(metadata: dict, current_status: SessionStatus) -> None:
            if current_status not in allowed_statuses:
                return
            control = metadata.get("control")
            control = dict(control) if isinstance(control, dict) else {}
            control.update(
                {
                    "control_state": "interrupted",
                    "is_interrupted": True,
                    "is_resumable": is_resumable,
                    "preserve_resources": is_resumable,
                    "last_interrupt_reason": reason,
                }
            )
            if approval_id:
                control["last_approval_id"] = approval_id
            metadata["control"] = control

        found = await self._store.update_status_and_mutate_metadata(
            session_id,
            status=SessionStatus.interrupted,
            mutate=mark_interrupted,
            allowed_current_statuses=allowed_statuses,
            continuation_approval_id=continuation_approval_id,
            continuation_lease_token=continuation_lease_token,
            turn_lease_token=turn_lease_token,
        )
        if not found:
            raise KeyError(session_id)
        logger.warning(
            "session_interrupted_after_owned_execution_error",
            extra={"session_id": session_id, "approval_id": approval_id or "",
                   "is_resumable": is_resumable},
        )

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
        # Queue projection is a read model, never persist it into the legacy
        # pending_input_queue consumed by local coordinator continuations.
        session = deepcopy(session)
        approvals, last_snapshot = await asyncio.gather(
            self._store.list_approvals(session.id),
            self._store.get_latest_snapshot(session.id),
        )
        control = self._ensure_control_metadata(session)
        has_pending_turn = bool(session.metadata.get("pending_turn"))
        approval_progress = []
        if self._agent_task_store is not None:
            turn_id = (session.metadata.get("pending_turn") or {}).get("turn_id") or control.get("last_turn_id")
            approval_progress = await self._agent_task_store.list_framework_approval_progress(session.id, turn_id)
            tasks = await self._agent_task_store.list_session(session.id, pending_only=True)
            queued = [
                task
                for task in reversed(tasks)
                if task.status in {"queued", "waiting_resource", "claimed"}
                and task.operation not in {"framework_reconcile", "approval_recover", "message_recover"}
            ]
            resume_queued = any(task.operation == "manual_resume" for task in queued)
            local_queue = session.metadata.get("pending_input_queue", [])
            durable_queue = [
                {
                    "id": task.id,
                    "created_at": task.created_at.isoformat(),
                    "busy_status": session.status.value,
                    "queue_behavior": "enqueue_if_busy",
                    "interrupt_policy": "wait_for_active_turn",
                    "reason": task.waiting_reason or "等待 Worker 执行",
                    "payload": task.input,
                    "metadata": {
                        "turn_id": task.turn_id,
                        "task_status": task.status,
                        "task_operation": task.operation,
                    },
                }
                for task in queued
            ]
            session.metadata["pending_input_queue"] = [*local_queue, *durable_queue]
            control["queued_input_count"] = len(queued) + len(local_queue)
            if queued and session.status not in {
                SessionStatus.running,
                SessionStatus.waiting_approval,
            } and not (control.get("control_state") == "interrupted" and control.get("last_interrupt_binding_id")):
                control["control_state"] = "queued"
        derived_resumable = bool(control.get("is_resumable")) or (
            last_snapshot is not None
            and (
                last_snapshot.stage in {"waiting_approval", "resumable"}
                or (last_snapshot.stage == "interrupted" and has_pending_turn)
            )
        )
        if self._agent_task_store is not None and resume_queued:
            derived_resumable = False
        derived_interrupted = bool(control.get("is_interrupted")) or session.status == SessionStatus.interrupted
        interrupted_binding = control.get("last_interrupt_binding_id") if control.get("control_state") == "interrupted" else None
        if interrupted_binding:
            derived_resumable = False
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
                and (not interrupted_binding or item.metadata.get("framework_binding_id") != interrupted_binding)
            ],
            framework_approval_progress=approval_progress,
            last_snapshot=self._snapshot_for_detail(last_snapshot),
            control_state=str(control.get("control_state") or (last_snapshot.stage if last_snapshot else session.status.value)),
            is_resumable=derived_resumable,
            is_interrupted=derived_interrupted,
            replay_available=bool(control.get("replay_available")) or last_snapshot is not None,
            verification_results=[
                item if hasattr(item, "model_dump") else item
                for item in session.metadata.get("verification_results", [])
            ],
            metadata={
                key: value
                for key, value in session.metadata.items()
                if key not in INTERNAL_SESSION_METADATA_KEYS
            },
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
        continuation_approval_id: str | None = None,
        continuation_lease_token: str | None = None,
        turn_lease_token: str | None = None,
    ) -> SessionAssistantStream:
        fenced_write = {
            "continuation_approval_id": continuation_approval_id,
            "continuation_lease_token": continuation_lease_token,
            "turn_lease_token": turn_lease_token,
        }

        return SessionAssistantStream(
            self._store, session_id, turn_id, assistant_message_id, self._make_event,
            fence=fenced_write,
        )

    async def _persist_turn_memory(
        self,
        session: SessionRecord,
        turn_id: str,
        trace_id: str,
        user_message: str,
        assistant_message: str,
        tool_results: list[dict],
        context_bundle: dict,
        fenced_write: dict[str, str | None] | None = None,
    ) -> None:
        if self._memory_runtime_service is None:
            return
        if not assistant_message.strip():
            return
        turn_lease_token = str((fenced_write or {}).get("turn_lease_token") or "")
        if turn_lease_token:
            await self._store.assert_turn_execution(session.id, turn_lease_token)
        memory_ids = await self._memory_runtime_service.write_turn_memory(
            session_id=session.id,
            turn_id=turn_id,
            trace_id=trace_id,
            user_message=user_message,
            assistant_message=assistant_message,
            tool_results=tool_results,
            context_bundle=context_bundle,
            turn_lease_token=turn_lease_token or None,
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
            **(fenced_write or {}),
        )

    async def _persist_tool_observations(
        self,
        session: SessionRecord,
        turn_id: str,
        trace_id: str,
        tool_results: list[dict],
        context_bundle: dict,
        fenced_write: dict[str, str | None] | None = None,
        framework_projection=None,
        projection_asset=None,
    ) -> None:
        if self._memory_runtime_service is None or self._observation_runtime_service is None:
            return
        write_options = {}
        if framework_projection is not None:
            from src.runtime.framework_observation_projection import FrameworkObservationProjections

            def prepare(original):
                state = original.runtime_result.state
                items = self._observation_runtime_service.build_tool_observations(
                    session_id=original.binding.session_id, turn_id=original.binding.turn_id,
                    trace_id=str(state.get("trace_id", "")), tool_results=list(state.get("tool_results", [])),
                    context_bundle=dict(state.get("context_bundle", {})))
                return items, self._memory_runtime_service.build_observation_requests(items)

            asset = await FrameworkObservationProjections(self._agent_task_store).prepare(framework_projection, prepare)
            observations = asset.observations
            write_options = {"framework_projection": framework_projection, "prepared_requests": asset.write_requests}
        else:
            observations = self._observation_runtime_service.build_tool_observations(
                session_id=session.id,
                turn_id=turn_id,
                trace_id=trace_id,
                tool_results=tool_results,
                context_bundle=context_bundle,
            )
        if not observations:
            return
        turn_lease_token = str((fenced_write or {}).get("turn_lease_token") or "")
        if turn_lease_token:
            await self._store.assert_turn_execution(session.id, turn_lease_token)
        observation_ids = await self._memory_runtime_service.write_observations(
            observations,
            turn_lease_token=turn_lease_token or None,
            **write_options,
        )
        event = self._make_event(
            session.id,
            "observations.persisted",
            {
                "turn_id": turn_id,
                "trace_id": trace_id,
                "observation_count": len(observation_ids),
                "observation_ids": observation_ids,
            },
        )
        if framework_projection is not None:
            event.id = projection_asset.event_id(event.type)
            event.timestamp = projection_asset.projection_at
        await self._store.append_event(
            session.id,
            event,
            **({"framework_projection": framework_projection} if framework_projection is not None else {}),
            **(fenced_write or {}),
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
        if is_model_failure_summary(model_response_summary):
            return SessionStatus.interrupted
        if runtime_result.state.get("termination_reason") == "interrupted":
            return SessionStatus.interrupted
        if runtime_result.approvals or runtime_result.snapshot.stage == "waiting_approval":
            return SessionStatus.waiting_approval
        return SessionStatus.completed

    def _control_state_from_runtime_result(self, runtime_result) -> str:
        model_response_summary = runtime_result.state.get("model_response_summary", {})
        if is_model_failure_summary(model_response_summary):
            return "interrupted"
        if runtime_result.state.get("termination_reason") == "interrupted":
            return "interrupted"
        if runtime_result.approvals or runtime_result.snapshot.stage == "waiting_approval":
            return "waiting_approval"
        if runtime_result.pending_turn:
            return "resumable"
        return "completed"
