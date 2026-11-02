"""Persist API submissions and execute them only in an Agent Worker."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from time import monotonic
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError

from src.application.observability.business_metrics import (
    agent_task_execution_seconds,
    agent_task_retries_total,
    agent_task_wait_seconds,
    agent_tasks_finished_total,
    agent_tasks_submitted_total,
)
from src.core.request_context import bind_request_context, get_request_context, new_id
from src.runtime.resource_lease_manager import ResourceUnavailable
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.runtime.framework_message_stages import FrameworkMessageStageRecoveryRequired
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.controlled_case_approval_wait import ControlledCaseApprovalWaitReceipt
from src.schemas.agent_task import (
    AgentTaskAccepted,
    AgentTaskCommand,
    AgentTaskCommandV2,
    AgentTaskRecord,
    TERMINAL_AGENT_TASK_STATUSES,
    parse_agent_task_command,
)
from src.schemas.session import (
    ChatMessage,
    CreateSessionRequest,
    ExecutionEvent,
    HeadlessExecutionRequest,
    MessageRole,
    ResumeSessionRequest,
    RuntimeMode,
    SendMessageRequest,
)

LOGGER = logging.getLogger(__name__)


def _case_receipt_fields(receipt):
    if isinstance(receipt, ControlledCaseApprovalWaitReceipt):
        return {"result_status": "waiting_approval", "wait_sha256": receipt.receipt_sha256}
    return {"result_status": "completed", "result_id": receipt.result_id}


def _task_type(value) -> str:
    """Resolve the task_type label from a command or a durable task record."""
    declared = getattr(value, "task_type", None)
    if declared:
        return str(declared)
    operation = str(getattr(value, "operation", "") or "")
    return "agent_turn" if operation in {"", "message"} else operation


def _seconds_since(value: datetime | None) -> float:
    """Elapsed whole seconds since a persisted timestamp, never negative."""
    if value is None:
        return 0.0
    moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())


class AgentTaskDispatcher:
    def __init__(self, settings, tasks, sessions, resources, *,
                 framework_capabilities: FrameworkWorkerCapabilities | None = None,
                 framework_model_calls=None, framework_native_reader=None,
                 framework_submission_preparer=None, framework_submission_loader=None,
                 controlled_case_settlements=None, controlled_case_runtime=None):
        if framework_submission_preparer is not None and framework_submission_loader is not None:
            raise ValueError("Configure either a captured preparer or an async material loader, not both")
        self._settings = settings
        self._tasks = tasks
        self._sessions = sessions
        self._resources = resources
        self._framework_capabilities = framework_capabilities
        self._framework_model_calls = framework_model_calls
        self._framework_native_reader = framework_native_reader
        # Explicit composition only. No production caller installs this port
        # until compatible readers and the mode's release gates are deployed.
        self._framework_submission_preparer = framework_submission_preparer
        self._framework_submission_loader = framework_submission_loader
        self._controlled_case_settlements = controlled_case_settlements
        self._controlled_case_runtime = controlled_case_runtime
        self._coordinator = None
        self._child_reaper_task: asyncio.Task[None] | None = None
        self._child_reaper_stop: asyncio.Event | None = None

    def set_coordinator_service(self, coordinator) -> None:
        """Inject the coordinator that turns finished child tasks into parent resumes."""
        self._coordinator = coordinator

    def set_framework_capabilities(self, capabilities: FrameworkWorkerCapabilities) -> None:
        """Install capabilities for subsequent deliveries, not an emergency stop."""
        self._framework_capabilities = capabilities

    def _record_submission(self, command) -> None:
        agent_tasks_submitted_total().labels(
            task_type=_task_type(command),
            queue=self._submission_queue(command),
        ).inc()

    def _submission_queue(self, task) -> str:
        return (self._settings.orchestration.agent_task_v2_stream
            if getattr(task, "runtime_binding", None) is not None
            else self._settings.orchestration.agent_task_stream)

    async def submit(self, session_id, payload, *, execution_request=None):
        session = await self._sessions._require_session(session_id)
        if session.project_id:
            await self._sessions._require_active_project(session.project_id)
        # Pure normalization validates modes, attachments and empty input. Model
        # intent enrichment runs later, under Worker admission and ownership.
        request = execution_request or self._sessions._input_orchestrator_service.orchestrate(
            session, payload
        )
        context = get_request_context()
        command = AgentTaskCommand(
            task_id=new_id("task"),
            session_id=session_id,
            turn_id=request.turn_id,
            project_id=session.project_id,
            request_id=context.request_id if context else new_id("req"),
            trace_id=getattr(request, "trace_id", "") or (context.trace_id if context else new_id("trace")),
            run_id=context.run_id if context else None,
            run_item_id=context.run_item_id if context else None,
        )
        key = payload.metadata.get("client_message_id") or command.request_id
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise ValueError("client_message_id must be a nonempty string of at most 256 characters")
        message = ChatMessage(
            id=str(uuid4()),
            role=MessageRole.user,
            content=payload.content.strip(),
            created_at=datetime.now(timezone.utc),
            metadata=self._sessions._transcript_hygiene_service.user_message_metadata(
                {
                    **payload.metadata,
                    "turn_id": request.turn_id,
                    "delivery_status": "queued",
                    "context_eligible": False,
                    "attachments": [
                        item.model_dump(mode="json") for item in payload.attachments
                    ],
                }
            ),
        )
        message.metadata["context_eligible"] = False
        preparer = self._framework_submission_preparer
        loader = self._framework_submission_loader
        capabilities = self._framework_capabilities
        loaded, task = None, None
        if loader is not None:
            task = await self._tasks.find_message_submission(session_id, payload, idempotency_key=key)
            if task is None and self._settings.deep_agents.new_turns_enabled:
                try:
                    # External read-only configuration/release acquisition must
                    # finish before the PG Session admission transaction opens.
                    loaded = await loader(session, request, command)
                    if loaded is not None:
                        root = loaded.model_budget.root_configuration(loaded.binding.model_config_fingerprint)
                        # The normalized request may inherit a Session preference.
                        # Only the original payload expresses an explicit choice;
                        # do not admit a candidate the Worker must later reject.
                        if root is not None and str(payload.model_key or "").strip() not in {"", "auto", root.key}:
                            raise ValueError("Message model selection differs from its prepared root model")
                except Exception as exc:
                    # A concurrent request may have won while acquisition was
                    # in flight. Its committed receipt outranks current source
                    # availability; a miss never authorizes a legacy fallback.
                    task = await self._tasks.find_message_submission(session_id, payload, idempotency_key=key)
                    if task is None:
                        LOGGER.warning("agent_message_material_loading_failed", extra={"session_id": session_id,
                            "task_id": command.task_id, "error_type": type(exc).__name__})
                        raise ValueError("Framework message material loading failed") from None
            if task is not None:
                LOGGER.info("agent_message_loaded_admission_reused", extra={"session_id": session_id,
                    "task_id": task.id, "turn_id": task.turn_id})
        if task is None and preparer is None and loader is None:
            task = await self._tasks.submit(command, payload, message, idempotency_key=key)
        elif task is None:
            def prepare():
                # Called only for a new API intent, under the existing Session
                # transaction lock. Must be synchronous and preparation-only:
                # no graph, model, queue, DB or other execution side effects.
                if not self._settings.deep_agents.new_turns_enabled:
                    LOGGER.info("agent_message_new_turn_admission_disabled", extra={"session_id": session_id,
                        "task_id": command.task_id, "recovery_reason": "new_turn_admission_disabled"})
                    return None
                try:
                    prepared = preparer(session, request, command) if preparer is not None else loaded
                    if prepared is not None:
                        if capabilities is None:
                            raise ValueError("Prepared admission requires explicit reader capabilities")
                        capabilities.require(prepared.binding, operation="message")
                        capabilities.require_model_ledger_format("budget",
                            prepared.model_budget.model_dump(mode="json", warnings="error"))
                    return prepared
                except Exception as exc:
                    LOGGER.warning("agent_message_preparation_failed", extra={
                        "session_id": session_id, "task_id": command.task_id, "error_type": type(exc).__name__})
                    raise ValueError("Framework message preparation failed") from None

            task = await self._tasks.submit_prepared_message(command, payload, message, idempotency_key=key,
                prepare_framework_submission=prepare)
        self._record_submission(task)
        LOGGER.info(
            "agent_task_submitted",
            extra={
                "task_id": task.id,
                "session_id": session_id,
                "turn_id": task.turn_id,
                "request_id": task.request_id,
                "trace_id": task.trace_id,
                "queue": self._submission_queue(task),
                "source": payload.source,
            },
        )
        return await self._accepted(
            task,
            "任务已接收，执行状态请查看编排轨迹。",
        )

    async def submit_resume(
        self,
        session_id: str,
        payload: ResumeSessionRequest,
    ) -> AgentTaskAccepted:
        session = await self._sessions._require_session(session_id)
        snapshot = await self._sessions._require_resume_snapshot(session_id)
        context = get_request_context()
        command = AgentTaskCommand(
            task_id=new_id("task"),
            operation="manual_resume",
            session_id=session_id,
            turn_id=self._sessions._resume_turn_id(snapshot),
            project_id=session.project_id,
            request_id=context.request_id if context else new_id("req"),
            trace_id=context.trace_id if context else new_id("trace"),
            run_id=context.run_id if context else None,
            run_item_id=context.run_item_id if context else None,
            reference_id=snapshot.id,
        )
        key = payload.client_request_id or command.request_id
        task = await self._tasks.submit_resume(
            command,
            payload,
            idempotency_key=key,
        )
        self._record_submission(command)
        LOGGER.info(
            "agent_resume_task_submitted",
            extra={
                "task_id": task.id,
                "session_id": task.session_id,
                "turn_id": task.turn_id,
                "request_id": task.request_id,
                "trace_id": task.trace_id,
                "snapshot_id": task.reference_id,
                "queue": self._settings.orchestration.agent_task_stream,
                "source": payload.source,
            },
        )
        return await self._accepted(
            task,
            "恢复任务已排队，执行状态请查看编排轨迹。",
        )

    async def submit_headless(
        self,
        payload: HeadlessExecutionRequest,
    ) -> AgentTaskAccepted:
        context = get_request_context()
        request_id = context.request_id if context else new_id("req")
        trace_id = context.trace_id if context else new_id("trace")
        key = payload.client_request_id or request_id
        session = await self._sessions._new_session_record(
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
        session.id = str(
            uuid5(
                NAMESPACE_URL,
                f"enterprise-ai-qa-agent:headless:{key}",
            )
        )
        execution_payload = SendMessageRequest(
            content=payload.content,
            mode_key=payload.mode_key,
            agent_key=payload.agent_key,
            model_key=payload.model_key,
            skill_keys=payload.skill_keys,
            submit_mode="background",
            source="headless.execute",
            context=payload.context,
            metadata={**payload.metadata, "client_message_id": key},
        )
        execution_request = self._sessions._input_orchestrator_service.orchestrate(
            session,
            execution_payload,
        )
        command = AgentTaskCommand(
            task_id=new_id("task"),
            session_id=session.id,
            turn_id=execution_request.turn_id,
            request_id=request_id,
            trace_id=trace_id,
            run_id=context.run_id if context else None,
            run_item_id=context.run_item_id if context else None,
        )
        message = ChatMessage(
            id=str(uuid4()),
            role=MessageRole.user,
            content=payload.content.strip(),
            created_at=datetime.now(timezone.utc),
            metadata=self._sessions._transcript_hygiene_service.user_message_metadata(
                {
                    **execution_payload.metadata,
                    "turn_id": execution_request.turn_id,
                    "delivery_status": "queued",
                    "context_eligible": False,
                }
            ),
        )
        message.metadata["context_eligible"] = False
        created_event = ExecutionEvent(
            type="session.created",
            session_id=session.id,
            timestamp=datetime.now(timezone.utc),
            request_id=request_id,
            trace_id=trace_id,
            turn_id=execution_request.turn_id,
            payload={
                "title": session.title,
                "session_mode": session.session_mode.value,
                "runtime_mode": session.runtime_mode.value,
                "mode_key": session.mode_key,
                "project_id": session.project_id,
            },
        )
        task = await self._tasks.submit_new_session(
            session,
            created_event,
            command,
            execution_payload,
            message,
            idempotency_key=key,
            submission_payload=payload.model_dump(mode="json"),
        )
        self._record_submission(command)
        LOGGER.info(
            "headless_agent_task_submitted",
            extra={
                "task_id": task.id,
                "session_id": task.session_id,
                "turn_id": task.turn_id,
                "request_id": task.request_id,
                "trace_id": task.trace_id,
                "queue": self._settings.orchestration.agent_task_stream,
            },
        )
        return await self._accepted(
            task,
            "Headless 任务已接收，执行状态请查看编排轨迹。",
        )

    async def submit_approval(
        self,
        session_id: str,
        approval_id: str,
        payload,
    ) -> AgentTaskAccepted:
        session = await self._sessions._require_session(session_id)
        pending_turn = session.metadata.get("pending_turn") or {}
        binding = await self._tasks.get_approval_runtime_binding(session_id, approval_id)
        context = get_request_context()
        command_type = AgentTaskCommandV2 if binding is not None else AgentTaskCommand
        command = command_type(
            task_id=new_id("task"),
            operation="approval_resume",
            session_id=session_id,
            turn_id=binding.turn_id if binding else str(pending_turn.get("turn_id") or f"approval_{approval_id}"),
            project_id=session.project_id,
            request_id=context.request_id if context else new_id("req"),
            trace_id=context.trace_id if context else new_id("trace"),
            run_id=context.run_id if context else None,
            run_item_id=context.run_item_id if context else None,
            reference_id=approval_id,
            **({"runtime_binding": binding} if binding is not None else {}),
        )
        key = payload.client_request_id or f"{approval_id}:{payload.decision.value}"
        if binding is not None:
            task = await self._tasks.submit_approval_v2(command, approval_id, payload)
        else:
            task = await self._tasks.submit_approval(command, approval_id, payload, idempotency_key=key)
        self._record_submission(command)
        LOGGER.info(
            "agent_approval_task_submitted",
            extra={
                "task_id": task.id,
                "session_id": task.session_id,
                "turn_id": task.turn_id,
                "approval_id": approval_id,
                "request_id": task.request_id,
                "trace_id": task.trace_id,
                "queue": (self._settings.orchestration.agent_task_v2_stream if binding is not None
                    else self._settings.orchestration.agent_task_stream),
                "decision": payload.decision.value,
            },
        )
        return await self._accepted(
            task,
            "审批已记录，续跑任务已排队。",
        )

    async def submit_coordinator_child(
        self,
        *,
        parent_task_id: str,
        root_trace_id: str,
        child_session_id: str,
        turn_id: str,
        project_id: str | None,
        request_id: str,
        trace_id: str,
        run_id: str | None,
        worker,
        mode_key: str = "",
        parent_session_id: str = "",
        parent_turn_id: str = "",
    ) -> AgentTaskRecord:
        """Persist one coordinator worker assignment on its pre-created child session.

        Coordinator dispatch already normalized the parent turn, so the child
        payload is stored verbatim and normalized again only inside the Worker.
        """
        await self._sessions._require_session(child_session_id)
        command = AgentTaskCommand(
            task_type="coordinator_child",
            operation="coordinator_child",
            task_id=new_id("task"),
            session_id=child_session_id,
            turn_id=turn_id,
            project_id=project_id,
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            parent_task_id=parent_task_id,
            root_trace_id=root_trace_id,
        )
        payload = SendMessageRequest(
            content=worker.prompt,
            mode_key=mode_key or None,
            agent_key=worker.agent_key,
            model_key=worker.model_key,
            skill_keys=list(worker.skill_keys),
            submit_mode="background",
            source="coordinator.dispatch",
            context={
                **worker.context,
                "parent_session_id": parent_session_id,
                "parent_turn_id": parent_turn_id,
                "parent_trace_id": trace_id,
                "parent_task_id": parent_task_id,
                "root_trace_id": root_trace_id,
                "task_id": worker.task_id,
                "dispatch_description": worker.description,
            },
            metadata={
                "message_kind": "coordinator_assignment",
                "task_id": worker.task_id,
                "parent_session_id": parent_session_id,
                "parent_turn_id": parent_turn_id,
            },
        )
        message = ChatMessage(
            id=str(uuid4()),
            role=MessageRole.user,
            content=worker.prompt.strip(),
            created_at=datetime.now(timezone.utc),
            metadata=self._sessions._transcript_hygiene_service.user_message_metadata(
                {
                    **payload.metadata,
                    "turn_id": turn_id,
                    "delivery_status": "queued",
                    "context_eligible": False,
                }
            ),
        )
        message.metadata["context_eligible"] = False
        key = f"coordinator_child:{worker.task_id}"
        task = await self._tasks.submit(command, payload, message, idempotency_key=key)
        self._record_submission(command)
        LOGGER.info(
            "coordinator_child_task_submitted",
            extra={
                "task_id": task.id,
                "parent_task_id": parent_task_id,
                "child_session_id": child_session_id,
                "child_turn_id": task.turn_id,
                "worker_task_id": worker.task_id,
                "root_trace_id": root_trace_id,
                "queue": self._settings.orchestration.agent_task_stream,
            },
        )
        return task

    async def submit_coordinator_resume(
        self,
        *,
        parent_session_id: str,
        parent_turn_id: str,
        project_id: str | None,
        request_id: str,
        trace_id: str,
        run_id: str | None,
        run_item_id: str | None,
        parent_task_id: str,
        notification_task_id: str,
        content: str,
        context: dict,
        metadata: dict,
        agent_key: str | None = None,
        mode_key: str | None = None,
        skill_keys: list[str] | None = None,
    ) -> AgentTaskRecord:
        """Queue the parent continuation for one settled coordinator child."""
        await self._sessions._require_session(parent_session_id)
        parent = None
        try:
            parent = await self._tasks.get(parent_task_id)
        except KeyError:
            LOGGER.warning(
                "coordinator_resume_parent_task_missing",
                extra={
                    "parent_task_id": parent_task_id,
                    "parent_session_id": parent_session_id,
                    "notification_task_id": notification_task_id,
                },
            )
        command = AgentTaskCommand(
            task_type="coordinator_resume",
            operation="coordinator_resume",
            task_id=new_id("task"),
            session_id=parent_session_id,
            turn_id=parent_turn_id,
            project_id=project_id,
            request_id=request_id,
            trace_id=trace_id,
            run_id=run_id,
            run_item_id=run_item_id,
            parent_task_id=parent_task_id,
            root_trace_id=(
                (parent.root_trace_id or parent.trace_id) if parent else None
            ) or trace_id,
        )
        payload = SendMessageRequest(
            content=content,
            mode_key=mode_key,
            agent_key=agent_key,
            skill_keys=list(skill_keys or []),
            submit_mode="background",
            source="coordinator.notification",
            context=dict(context),
            metadata=dict(metadata),
        )
        turn_key = str(notification_task_id)
        message = ChatMessage(
            id=str(uuid4()),
            role=MessageRole.user,
            content=content.strip(),
            created_at=datetime.now(timezone.utc),
            metadata=self._sessions._transcript_hygiene_service.user_message_metadata(
                {
                    **payload.metadata,
                    "turn_id": parent_turn_id,
                    "delivery_status": "queued",
                    "context_eligible": False,
                }
            ),
        )
        message.metadata["context_eligible"] = False
        key = f"coordinator_resume:{turn_key}"
        task = await self._tasks.submit(command, payload, message, idempotency_key=key)
        self._record_submission(command)
        LOGGER.info(
            "coordinator_resume_task_submitted",
            extra={
                "task_id": task.id,
                "parent_task_id": parent_task_id,
                "parent_session_id": parent_session_id,
                "parent_turn_id": parent_turn_id,
                "notification_task_id": turn_key,
                "root_trace_id": task.root_trace_id,
                "queue": self._settings.orchestration.agent_task_stream,
            },
        )
        return task

    async def _accepted(self, task, content: str) -> AgentTaskAccepted:
        return AgentTaskAccepted(
            task=task,
            session=await self._sessions.get_session(task.session_id),
            output=ChatMessage(
                id=f"receipt_{task.id}",
                role=MessageRole.system,
                content=content,
                created_at=task.created_at,
                metadata={"persist_transcript": False, "context_eligible": False},
            ),
            events=await self._sessions.list_events(task.session_id, limit=10),
        )

    async def list_tasks(self, session_id):
        await self._sessions._require_session(session_id)
        tasks = await self._tasks.list_session(session_id)
        return [AgentTaskRecord.model_validate(task.model_dump()) for task in tasks]

    async def interrupt(self, session_id, payload):
        if await self._tasks.interrupt(session_id,
            reason=payload.reason or "User requested task interruption", source=payload.source):
            return await self._sessions.get_session(session_id)
        return await self._sessions.interrupt_session(session_id, payload)

    async def handle(self, delivery, *, worker_id):
        command = parse_agent_task_command(delivery.payload)
        if isinstance(command, AgentTaskCommandV2):
            # Legacy workers must never consume a v2 envelope. A dedicated v2
            # worker owns binding validation and its separate Redis stream.
            raise ValueError("v2 agent command requires the v2 worker stream")
        await self._handle_command(
            command,
            worker_id=worker_id,
            queue=self._settings.orchestration.agent_task_stream,
        )

    async def handle_v2(self, delivery, *, worker_id):
        """Consume one v2 envelope after rechecking its durable binding.

        The Redis stream is only a delivery mechanism.  PostgreSQL remains the
        source of truth for both the task identity and the immutable runtime
        binding, so a stale or manually altered envelope is rejected before a
        task lease can be claimed.
        """
        try:
            command = FrameworkWorkerCapabilities.require_command_format(delivery.payload)
        except TaskDeferred as exc:
            LOGGER.warning("agent_v2_command_reader_unavailable", extra={
                "message_id": delivery.message_id, "worker_id": worker_id, "reason": str(exc)})
            raise
        await self._handle_command(
            command,
            worker_id=worker_id,
            queue=self._settings.orchestration.agent_task_v2_stream,
            runtime_binding=command.runtime_binding,
        )

    async def _handle_command(
        self,
        command,
        *,
        worker_id: str,
        queue: str,
        runtime_binding: RuntimeBinding | None = None,
    ) -> None:
        capabilities = self._framework_capabilities
        if runtime_binding is not None and capabilities is None:
            # Without a reader, even a lookup can turn an unavailable durable
            # format into a delivery failure. Do not decode or claim it.
            reason = "framework_worker_capabilities_unavailable"
            LOGGER.warning("agent_v2_capability_unavailable", extra={
                "task_id": command.task_id, "binding_id": runtime_binding.binding_id,
                "runtime_kind": runtime_binding.runtime_kind, "runtime_version": runtime_binding.runtime_version,
                "worker_id": worker_id, "reason": reason})
            raise TaskDeferred(reason)
        if runtime_binding is None:
            stored = await self._tasks.get(command.task_id)
        else:
            try:
                stored = await self._tasks.get(command.task_id, framework_capabilities=capabilities)
            except TaskDeferred as exc:
                LOGGER.warning("agent_v2_binding_reader_unavailable", extra={
                    "task_id": command.task_id, "binding_id": runtime_binding.binding_id,
                    "worker_id": worker_id, "reason": str(exc)})
                raise
        for name in (
            "operation",
            "session_id",
            "turn_id",
            "project_id",
            "request_id",
            "trace_id",
            "run_id",
            "run_item_id",
            "reference_id",
        ):
            if getattr(command, name) != getattr(stored, name):
                raise ValueError("Agent command identity does not match the durable task")
        if runtime_binding is not None:
            if stored.runtime_binding is None:
                raise ValueError("v2 task is missing its persisted runtime binding")
            if stored.runtime_binding != runtime_binding:
                raise ValueError("Agent command runtime binding does not match the durable task")
            if command.operation == "case_settle":
                # A settlement lease is not graph execution authority. Never
                # send it through ordinary capability/claim/begin/finish.
                await self._handle_case_settlement(command, stored, capabilities, worker_id, queue)
                return
            if command.operation == "case_execute":
                await self._handle_controlled_case(command, stored, capabilities, worker_id, queue)
                return
            try:
                capabilities.require(runtime_binding, operation=command.operation)
                stage_version = await self._tasks.get_framework_message_stage_version(
                    command.task_id, session_id=command.session_id)
                capabilities.require_message_stage(stage_version)
                if command.operation == "message" and stored.status in {"queued", "waiting_resource"}:
                    try:
                        stage = await self._tasks.get_framework_message_stage(command.task_id, session_id=command.session_id)
                    except ValidationError as exc:
                        capabilities.defer_unsupported_format(
                            exc, reason="framework_message_stage_format_unavailable")
                        raise
                    if stage is None or stage.phase in {"submitted", "orchestrating", "orchestrated"}:
                        session = await self._sessions._require_session(command.session_id)
                        capabilities.require_transcript_format(session, runtime_binding)
            except TaskDeferred as exc:
                LOGGER.warning("agent_v2_capability_unavailable", extra={
                    "task_id": command.task_id, "binding_id": runtime_binding.binding_id,
                    "runtime_kind": runtime_binding.runtime_kind,
                    "runtime_version": runtime_binding.runtime_version,
                    "worker_id": worker_id, "reason": str(exc),
                })
                raise
            task = await self._tasks.claim(
                command.task_id, worker_id=worker_id, expected_binding=runtime_binding,
                framework_capabilities=capabilities,
                check_model_ledger=self._framework_model_calls is not None,
            )
        else:
            if stored.runtime_binding is not None:
                raise ValueError("Bound framework task requires a v2 agent command")
            task = await self._tasks.claim(command.task_id, worker_id=worker_id)
        if task.status in TERMINAL_AGENT_TASK_STATUSES:
            if task.runtime_binding is not None and task.operation in {"message", "message_recover"}:
                await self._tasks.ensure_framework_message_recovery(task.id, native_reader=self._framework_native_reader)
            if task.runtime_binding is not None and task.operation in {"approval_resume", "approval_recover", "framework_reconcile"}:
                await self._tasks.ensure_framework_reconciliation(task.id)
            return
        task_type = _task_type(command)
        agent_task_wait_seconds().labels(task_type=task_type).observe(
            _seconds_since(task.created_at)
        )
        if task.attempt_no > 1:
            agent_task_retries_total().labels(task_type=task_type).inc()
        LOGGER.info(
            "agent_task_claimed",
            extra={
                "task_id": task.id,
                "session_id": task.session_id,
                "turn_id": task.turn_id,
                "attempt_id": task.attempt_id,
                "attempt_no": task.attempt_no,
                "worker_id": worker_id,
                **({"binding_id": task.runtime_binding.binding_id,
                    "owner_epoch": task.runtime_owner.owner_epoch} if task.runtime_owner is not None else {}),
            },
        )
        with bind_request_context(
            request_id=task.request_id,
            trace_id=task.trace_id,
            project_id=task.project_id,
            session_id=task.session_id,
            turn_id=task.turn_id,
            run_id=task.run_id,
            run_item_id=task.run_item_id,
            attempt_id=task.attempt_id,
            worker_id=worker_id,
            task_type=task_type,
            queue=queue,
        ):
            await self._execute(task)
            if task.runtime_binding is not None and task.operation in {"message", "message_recover"}:
                await self._tasks.ensure_framework_message_recovery(task.id, native_reader=self._framework_native_reader)
            if task.runtime_binding is not None and task.operation in {"approval_resume", "approval_recover", "framework_reconcile"}:
                await self._tasks.ensure_framework_reconciliation(task.id)

    async def _handle_controlled_case(self, command, stored, capabilities, worker_id, queue):
        # No upstream counterpart (checked: original _execute, case executor,
        # atomic settlements, v4 §12.1): generic finish cannot settle both
        # owners. Keep one-shot execution and receipt-only ACK separate.
        runtime = self._controlled_case_runtime
        lease, monitor, execution = None, None, None
        task, stop = stored, asyncio.Event()
        clock, completed = monotonic(), False
        config = self._settings.orchestration
        try:
            if runtime is None:
                raise TaskDeferred("framework_case_execution_port_unavailable")
            delivery = command.model_dump(mode="json")
            capabilities.require_runtime_artifact(command.runtime_binding)
            if stored.status in TERMINAL_AGENT_TASK_STATUSES:
                receipt = await runtime.acknowledge_delivery(delivery, capabilities=capabilities)
                LOGGER.info("agent_case_delivery_acknowledged", extra={"task_id": task.id,
                    "worker_id": worker_id, **_case_receipt_fields(receipt), "automatic_replay": False})
                return
            capabilities.require(command.runtime_binding, operation=command.operation)
            runtime.require_dispatch_ready(stored, capabilities=capabilities)
            task = await self._tasks.claim_controlled_case(delivery, worker_id=worker_id,
                framework_capabilities=capabilities)
            if task.status in TERMINAL_AGENT_TASK_STATUSES:
                await runtime.acknowledge_delivery(delivery, capabilities=capabilities)
                return
            with bind_request_context(request_id=task.request_id, trace_id=task.trace_id,
                project_id=task.project_id, session_id=task.session_id, turn_id=task.turn_id,
                run_id=task.run_id, run_item_id=task.run_item_id, attempt_id=task.attempt_id,
                worker_id=worker_id, task_type="case_execute", queue=queue):
                agent_task_wait_seconds().labels(task_type="case_execute").observe(_seconds_since(task.created_at))
                if task.attempt_no > 1:
                    agent_task_retries_total().labels(task_type="case_execute").inc()
                LOGGER.info("agent_case_execution_claimed", extra={"task_id": task.id,
                    "attempt_id": task.attempt_id, "attempt_no": task.attempt_no, "worker_id": worker_id,
                    "binding_id": task.runtime_binding.binding_id,
                    "execution_id": task.runtime_owner.controlled_case_lease.run_item_execution_attempt_id})
                try:
                    lease = await self._resources.acquire(resource_type="agent", resource_id=f"turn:{task.id}",
                        worker_id=worker_id, lease_seconds=config.resource_default_lease_seconds,
                        session_id=task.session_id, project_id=task.project_id, run_id=task.run_id,
                        run_item_id=task.run_item_id, attempt_id=task.attempt_id)
                except ResourceUnavailable as exc:
                    await self._tasks.defer(task, exc.reason)
                    raise TaskDeferred(exc.reason) from exc

                async def renew():
                    if await self._tasks.renew_framework_execution(task, config.turn_execution_lease_seconds):
                        return
                    # The atomic return deliberately closes both running
                    # leases before the Python waiter receives its receipt.
                    # End monitoring only after the existing scoped reader
                    # proves THIS attempt's commit; never renew a terminal
                    # owner or turn an unknown/foreign result into success.
                    returned = await self._tasks.get(task.id, framework_capabilities=capabilities)
                    if (returned.status != "completed" or returned.attempt_id != task.attempt_id
                            or returned.attempt_no != task.attempt_no):
                        raise ContinuationLeaseLostError("Controlled execution lost its original dual owner")
                    receipt = await runtime.acknowledge_delivery(delivery, capabilities=capabilities)
                    if (receipt.task_id != task.id or receipt.attempt_id != task.attempt_id
                            or receipt.owner_epoch != task.runtime_owner.owner_epoch
                            or receipt.binding_fingerprint != task.runtime_binding.fingerprint):
                        raise ValueError("Controlled monitor return differs from its captured attempt")
                    stop.set()
                    LOGGER.info("agent_case_execution_monitor_completed", extra={"task_id": task.id,
                        "attempt_id": task.attempt_id, "worker_id": worker_id,
                        **_case_receipt_fields(receipt), "automatic_replay": False})

                if not await self._resources.renew(lease, lease_seconds=config.resource_default_lease_seconds):
                    raise ContinuationLeaseLostError("Controlled admission resource was lost before execution")
                await renew()
                monitor = asyncio.create_task(self._monitor(task, lease, stop, renew_owner=renew))
                execution = asyncio.create_task(runtime.execute(task, resource_id=lease.resource_id,
                    fencing_token=lease.fencing_token))
                done, _ = await asyncio.wait({execution, monitor}, return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:
                    await monitor
                outcome = await execution
                receipt = outcome.settlement_receipt
                if outcome.approval_wait_receipt is not None:
                    if receipt is not None:
                        raise ValueError("Controlled execution cannot both park and settle")
                    receipt = await runtime.acknowledge_delivery(delivery, capabilities=capabilities)
                    if receipt != outcome.approval_wait_receipt:
                        raise ValueError("Controlled execution wait differs from its acknowledged original")
                if receipt is None:
                    reason = ("framework_case_approval_projection_unavailable"
                        if outcome.graph_outcome.native_interrupts else "framework_case_result_unsettled")
                    raise TaskDeferred(reason)
                completed = True
                LOGGER.info("agent_case_execution_committed", extra={"task_id": task.id,
                    "attempt_id": task.attempt_id, "worker_id": worker_id,
                    **_case_receipt_fields(receipt), "automatic_replay": False})
        except asyncio.CancelledError:
            LOGGER.warning("agent_case_execution_receipt_unknown", extra={"task_id": task.id,
                "attempt_id": task.attempt_id, "worker_id": worker_id, "automatic_replay": False})
            raise
        except Exception as exc:
            LOGGER.warning("agent_case_execution_deferred", extra={"task_id": task.id,
                "attempt_id": task.attempt_id, "worker_id": worker_id, "error_type": type(exc).__name__,
                "defer_reason": str(exc) if isinstance(exc, TaskDeferred) else "execution_requires_reconciliation",
                "automatic_replay": False})
            if isinstance(exc, TaskDeferred):
                raise
            raise TaskDeferred("framework_case_execution_requires_reconciliation") from None
        finally:
            if completed:
                agent_tasks_finished_total().labels(task_type="case_execute", status="completed").inc()
                agent_task_execution_seconds().labels(task_type="case_execute").observe(max(0, monotonic()-clock))

            async def cleanup():
                if execution is not None:
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                await self._cleanup_execution(task, lease, monitor, stop)
            await self._sessions._settle_execution_tasks(asyncio.create_task(cleanup()))

    async def _handle_case_settlement(self, command, stored, capabilities, worker_id, queue):
        service = self._controlled_case_settlements
        if service is None:
            LOGGER.warning("agent_case_settlement_port_unavailable", extra={"task_id": command.task_id,
                "worker_id": worker_id, "automatic_replay": False})
            raise TaskDeferred("framework_case_settlement_port_unavailable")
        lease, monitor, execution = None, None, None
        task, stop = stored, asyncio.Event()
        clock, completed = monotonic(), False
        config = self._settings.orchestration
        try:
            capabilities.require_runtime_artifact(command.runtime_binding)
            delivery = command.model_dump(mode="json")
            if stored.status == "completed":
                await service.acknowledge(delivery, capabilities=capabilities)
                LOGGER.info("agent_case_settlement_acknowledged", extra={"task_id": command.task_id,
                    "worker_id": worker_id, "automatic_replay": False})
                return
            scoped = await service.claim(delivery, worker_id=worker_id, capabilities=capabilities)
            # These are logging/resource-admission fields from the captured
            # lease, not a reconstructed RuntimeExecutionOwner.
            task = stored.model_copy(update={"attempt_id": scoped.attempt_id, "attempt_no": scoped.attempt_no,
                "worker_id": scoped.worker_id, "lease_token": scoped.lease_token, "status": "claimed"})
            with bind_request_context(request_id=task.request_id, trace_id=task.trace_id,
                project_id=task.project_id, session_id=task.session_id, turn_id=task.turn_id,
                run_id=task.run_id, run_item_id=task.run_item_id, attempt_id=task.attempt_id,
                worker_id=worker_id, task_type="case_settle", queue=queue):
                agent_task_wait_seconds().labels(task_type="case_settle").observe(_seconds_since(task.created_at))
                if scoped.attempt_no > 1:
                    agent_task_retries_total().labels(task_type="case_settle").inc()
                try:
                    lease = await self._resources.acquire(resource_type="agent", resource_id=f"turn:{task.id}",
                        worker_id=worker_id, lease_seconds=config.resource_default_lease_seconds,
                        session_id=task.session_id, project_id=task.project_id, run_id=task.run_id,
                        run_item_id=task.run_item_id, attempt_id=task.attempt_id)
                except ResourceUnavailable as exc:
                    await service.defer(scoped, reason=exc.reason, capabilities=capabilities)
                    raise TaskDeferred(exc.reason) from exc

                async def renew():
                    return await service.renew(scoped, lease_seconds=config.turn_execution_lease_seconds,
                        capabilities=capabilities)

                if not await self._resources.renew(lease, lease_seconds=config.resource_default_lease_seconds):
                    raise ContinuationLeaseLostError("Settlement admission resource was lost before commit")
                await renew()
                monitor = asyncio.create_task(self._monitor(task, lease, stop, renew_owner=renew))
                execution = asyncio.create_task(service.complete(scoped, capabilities=capabilities))
                done, _ = await asyncio.wait({execution, monitor}, return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:
                    await monitor
                receipt = await execution
                completed = True
                LOGGER.info("agent_case_settlement_committed", extra={"task_id": task.id,
                    "attempt_id": task.attempt_id, "worker_id": worker_id, "result_id": receipt.result_id,
                    "automatic_replay": False})
        except asyncio.CancelledError:
            LOGGER.warning("agent_case_settlement_receipt_unknown", extra={"task_id": task.id,
                "attempt_id": task.attempt_id, "worker_id": worker_id, "automatic_replay": False})
            raise
        except Exception as exc:
            LOGGER.warning("agent_case_settlement_deferred", extra={"task_id": task.id,
                "attempt_id": task.attempt_id, "worker_id": worker_id, "error_type": type(exc).__name__,
                "defer_reason": str(exc) if isinstance(exc, TaskDeferred) else "settlement_requires_reconciliation",
                "automatic_replay": False})
            if isinstance(exc, TaskDeferred):
                raise
            # Keep the original receipt/attempt intact. A failed reply, source
            # drift or lost monitor is not permission to fail/reset/replay it.
            raise TaskDeferred("framework_case_settlement_requires_reconciliation") from None
        finally:
            if completed:
                agent_tasks_finished_total().labels(task_type="case_settle", status="completed").inc()
                agent_task_execution_seconds().labels(task_type="case_settle").observe(max(0, monotonic()-clock))

            async def cleanup():
                if execution is not None:
                    execution.cancel()
                    await asyncio.gather(execution, return_exceptions=True)
                await self._cleanup_execution(task, lease, monitor, stop)
            await self._sessions._settle_execution_tasks(asyncio.create_task(cleanup()))

    async def _execute(self, task):
        lease, monitor = None, None
        stop = asyncio.Event()
        started = False
        notification = None
        config = self._settings.orchestration
        execution_clock = monotonic()
        finished_status: str | None = None
        try:
            if await self._tasks.interrupt_requested(task.id):
                await self._tasks.finish(
                    task,
                    status="cancelled",
                    error_code="user_cancelled_before_admission",
                )
                finished_status = "cancelled"
                return
            try:
                lease = await self._resources.acquire(
                    resource_type="agent",
                    resource_id=f"turn:{task.id}",
                    worker_id=task.worker_id,
                    lease_seconds=config.resource_default_lease_seconds,
                    session_id=task.session_id,
                    project_id=task.project_id,
                    run_id=task.run_id,
                    run_item_id=task.run_item_id,
                    attempt_id=task.attempt_id,
                )
            except ResourceUnavailable as exc:
                await self._tasks.defer(task, exc.reason)
                LOGGER.info(
                    "agent_task_waiting_resource",
                    extra={
                        "task_id": task.id,
                        "attempt_id": task.attempt_id,
                        "worker_id": task.worker_id,
                        "waiting_reason": exc.reason,
                    },
                )
                raise TaskDeferred(exc.reason) from exc
            # Once this transaction may have committed, recovery must assume
            # execution could have started, even if the database reply is lost.
            started = True
            await self._tasks.begin(
                task,
                resource_id=lease.resource_id,
                fencing_token=lease.fencing_token,
            )
            if await self._tasks.interrupt_requested(task.id):
                await self._tasks.finish(
                    task,
                    status="cancelled",
                    error_code="user_cancelled_before_execution",
                )
                finished_status = "cancelled"
                return
            monitor = asyncio.create_task(self._monitor(task, lease, stop))
            LOGGER.info(
                "agent_task_execution_started",
                extra={
                    "task_id": task.id,
                    "attempt_id": task.attempt_id,
                    "worker_id": task.worker_id,
                    "resource_id": lease.resource_id,
                },
            )
            with bind_request_context(resource_id=lease.resource_id):
                clock = monotonic()
                if task.operation == "framework_reconcile":
                    if not await self._resources.renew(lease, lease_seconds=config.resource_default_lease_seconds):
                        raise ContinuationLeaseLostError("Reconciliation admission lease lost before commit")
                    await self._sessions.execute_claimed_reconciliation(task)
                    finished_status = "completed"
                    return
                if task.runtime_binding is not None and task.operation in {"message_recover", "approval_recover"}:
                    from src.runtime.framework_approval_settlement import is_settlement_task

                    settling = is_settlement_task({"input_payload": task.input}) if task.operation == "approval_recover" else False
                    if task.operation == "message_recover":
                        stage = await self._tasks.get_framework_message_stage(task.id, session_id=task.session_id)
                        settling = bool(stage is not None and stage.recoveries and stage.recoveries[-1].resume_phase == "settling")
                    if settling:
                        if not await self._resources.renew(lease, lease_seconds=config.resource_default_lease_seconds):
                            raise ContinuationLeaseLostError("Projected settlement admission lease lost before commit")
                        # The finish transaction rechecks owner, commit, stage
                        # reference and lineage. No Session/finalizer is entered.
                        await self._tasks.finish(task, status="completed", result_status="completed")
                        finished_status = "completed"
                        return
                if task.operation in {"message", "message_recover"}:
                    response = await self._sessions.execute_claimed_message(
                        task,
                        ownership_monitor=monitor,
                    )
                elif task.operation == "manual_resume":
                    response = await self._sessions.execute_claimed_resume(
                        task,
                        ownership_monitor=monitor,
                    )
                elif task.operation in {"approval_resume", "approval_recover"}:
                    response = await self._sessions.execute_claimed_approval(
                        task,
                        ownership_monitor=monitor,
                    )
                elif task.operation == "coordinator_resume":
                    response = await self._sessions.execute_claimed_message(
                        task,
                        ownership_monitor=monitor,
                    )
                elif task.operation == "coordinator_child":
                    response = await self._sessions.execute_claimed_message(
                        task,
                        ownership_monitor=monitor,
                    )
                    notification = self._child_notification(
                        task,
                        status=response.session.status.value,
                        summary=self._child_summary(task, response.session.status.value),
                        result_text=response.output.content if response.output else "",
                        usage=self._notification_usage(
                            response.session,
                            int((monotonic() - clock) * 1000),
                        ),
                    )
                else:  # pragma: no cover - schema validation rejects this first
                    raise ValueError(f"Unsupported Agent task operation: {task.operation}")
                if not await self._resources.renew(lease, lease_seconds=config.resource_default_lease_seconds):
                    raise ContinuationLeaseLostError(
                        "Agent admission lease lost before result commit"
                    )
                outcome = response.session.status.value
                status = {
                    "interrupted": "interrupted",
                    "failed": "failed",
                }.get(outcome, "completed")
                if notification is not None:
                    if not await self._tasks.set_result_payload(
                        task.id,
                        attempt_id=task.attempt_id,
                        fencing_token=task.fencing_token,
                        payload=notification,
                    ):
                        LOGGER.warning(
                            "coordinator_child_result_payload_fencing_rejected",
                            extra={
                                "task_id": task.id,
                                "attempt_id": task.attempt_id,
                                "fencing_token": task.fencing_token,
                            },
                        )
                await self._tasks.finish(task, status=status, result_status=outcome)
                finished_status = status
                LOGGER.info(
                    "agent_task_execution_finished",
                    extra={
                        "task_id": task.id,
                        "attempt_id": task.attempt_id,
                        "worker_id": task.worker_id,
                        "status": status,
                        "result_status": outcome,
                    },
                )
                if notification is not None:
                    await self._notify_parent_child_done(task, notification)
                if (response.session.metadata.get("pending_turn") or {}).get("waiting_tool") and self._coordinator is not None:
                    try:
                        await self._coordinator.on_parent_tool_wait(task.session_id)
                    except Exception:
                        # The wait snapshot/task outcome is already committed.
                        # The durable dispatch reaper will retry reconciliation.
                        LOGGER.exception("tool_child_wait_post_commit_reconcile_failed",
                            extra={"session_id": task.session_id, "task_id": task.id})
        except TaskDeferred:
            raise
        except ContinuationLeaseLostError as exc:
            LOGGER.warning(
                "agent_task_ownership_lost",
                extra={"task_id": task.id, "attempt_id": task.attempt_id},
            )
            raise TaskDeferred("Agent ownership requires recovery") from exc
        except asyncio.CancelledError:
            # Keep the running fact and lease for recovery after a Worker kill or
            # timeout. A cancelled delivery must never silently replay a tool.
            raise
        except Exception as exc:
            LOGGER.exception(
                "agent_task_execution_failed",
                extra={"task_id": task.id, "error_type": type(exc).__name__},
            )
            if started:
                if notification is None:
                    notification = self._child_notification(
                        task,
                        status="failed",
                        summary=self._child_summary(task, "failed"),
                        result_text=f"{type(exc).__name__}: {exc}",
                    )
                    if notification is not None:
                        await self._tasks.set_result_payload(
                            task.id,
                            attempt_id=task.attempt_id,
                            fencing_token=task.fencing_token,
                            payload=notification,
                        )
                requires_reconciliation = task.operation == "framework_reconcile" or (
                    task.runtime_binding is not None
                    and isinstance(exc, FrameworkMessageStageRecoveryRequired)
                )
                failure_status = "recovery_required" if requires_reconciliation else "failed"
                await self._tasks.finish(task, status=failure_status, error_code=type(exc).__name__)
                finished_status = failure_status
                if notification is not None:
                    await self._notify_parent_child_done(task, notification)
            else:
                await self._tasks.defer(task, "admission_unavailable")
                raise
        finally:
            if finished_status is not None:
                task_type = _task_type(task)
                agent_tasks_finished_total().labels(
                    task_type=task_type,
                    status=finished_status,
                ).inc()
                agent_task_execution_seconds().labels(task_type=task_type).observe(
                    max(0.0, monotonic() - execution_clock)
                )

            await self._sessions._settle_execution_tasks(asyncio.create_task(
                self._cleanup_execution(task, lease, monitor, stop)))

    async def _cleanup_execution(self, task, lease, monitor, stop):
        stop.set()
        if monitor is not None:
            result = (await asyncio.gather(monitor, return_exceptions=True))[0]
            if isinstance(result, BaseException):
                LOGGER.warning("agent_task_monitor_stopped", extra={"task_id": task.id,
                    "error_type": type(result).__name__})
        if lease is not None:
            try:
                await self._resources.retire(lease)
                LOGGER.info("agent_task_slot_released", extra={"task_id": task.id,
                    "attempt_id": task.attempt_id, "worker_id": task.worker_id, "resource_id": lease.resource_id})
            except Exception as exc:
                LOGGER.error("agent_task_slot_release_pending", extra={"task_id": task.id,
                    "error_type": type(exc).__name__})

    async def _monitor(self, task, lease, stop, *, renew_owner=None):
        config = self._settings.orchestration
        interval = min(
            config.agent_task_control_poll_seconds,
            config.resource_default_lease_seconds / 3,
        )
        if renew_owner is not None:
            interval = min(interval, config.turn_execution_lease_seconds / 3)
        interrupt_sent = False
        monitor_clock = monotonic()
        last_resource_renewal = None
        last_interrupt_poll_seconds = None
        operation, operation_clock = "wait", monitor_clock
        try:
            while not stop.is_set():
                operation, operation_clock = "wait", monotonic()
                try:
                    await asyncio.wait_for(stop.wait(), interval)
                    return
                except TimeoutError:
                    pass
                operation, operation_clock = "resource_renew", monotonic()
                if not await self._resources.renew(
                    lease,
                    lease_seconds=config.resource_default_lease_seconds,
                ):
                    raise ContinuationLeaseLostError("Agent resource admission lease was lost")
                last_resource_renewal = monotonic()
                if renew_owner is not None:
                    operation = "settlement_renew" if task.operation == "case_settle" else "case_execution_renew"
                    operation_clock = monotonic()
                    await renew_owner()
                    continue  # Scoped renewal also checks cancellation; no graph interrupt.
                operation, operation_clock = "interrupt_poll", monotonic()
                if not interrupt_sent:
                    requested = await self._tasks.interrupt_requested(task.id)
                    last_interrupt_poll_seconds = monotonic() - operation_clock
                    if requested:
                        operation, operation_clock = "interrupt_apply", monotonic()
                        self._sessions._runtime_service.request_interrupt(
                            task.session_id,
                            "User requested task interruption",
                        )
                        interrupt_sent = True
                        LOGGER.info(
                            "agent_task_interrupt_applied",
                            extra={"task_id": task.id, "worker_id": task.worker_id},
                        )
        except Exception as exc:
            observed_at = monotonic()
            # Fixed event names remain useful in a spawned Worker's default
            # formatter. Never log raw exception text, input or lease objects.
            event = {"wait": "agent_task_monitor_wait_failed",
                "resource_renew": "agent_task_resource_renew_failed",
                "interrupt_poll": "agent_task_interrupt_poll_failed",
                "interrupt_apply": "agent_task_interrupt_apply_failed",
                "settlement_renew": "agent_case_settlement_renew_failed",
                "case_execution_renew": "agent_case_execution_renew_failed"}[operation]
            LOGGER.log(logging.WARNING if isinstance(exc, ContinuationLeaseLostError) else logging.ERROR,
                event, extra={"task_id": task.id, "attempt_id": task.attempt_id, "worker_id": task.worker_id,
                    "resource_id": lease.resource_id, "monitor_operation": operation,
                    "operation_seconds": observed_at - operation_clock,
                    "monitor_seconds": observed_at - monitor_clock, "error_type": type(exc).__name__,
                    "resource_lease_seconds": config.resource_default_lease_seconds,
                    "since_resource_renew_seconds": (observed_at - last_resource_renewal
                        if last_resource_renewal is not None else None),
                    "last_interrupt_poll_seconds": last_interrupt_poll_seconds})
            raise

    @staticmethod
    def _child_input(task) -> dict:
        payload = getattr(task, "input", None)
        return payload if isinstance(payload, dict) else {}

    def _child_summary(self, task, status: str) -> str:
        context = self._child_input(task).get("context") or {}
        description = str(context.get("dispatch_description") or "").strip()
        label = f'"{description}"' if description else str(task.id)
        if status == "completed":
            return f"Worker {label} completed."
        return f"Worker {label} finished with status {status}."

    @staticmethod
    def _notification_usage(session, duration_ms: int) -> dict:
        messages = list(getattr(session, "messages", None) or [])
        return {
            "total_messages": len(messages),
            "tool_uses": sum(
                1 for message in messages if message.role == MessageRole.tool
            ),
            "duration_ms": int(duration_ms),
            "event_count": int(getattr(session, "event_count", 0) or 0),
            "snapshot_count": int(getattr(session, "snapshot_count", 0) or 0),
        }

    def _child_notification(
        self,
        task,
        *,
        status: str,
        summary: str,
        result_text: str,
        usage: dict | None = None,
    ) -> dict | None:
        """Build the durable child outcome the parent coordinator resumes on."""
        if task.operation != "coordinator_child" or not task.parent_task_id:
            return None
        payload = self._child_input(task)
        metadata = payload.get("metadata") or {}
        context = payload.get("context") or {}
        return {
            "child_task_id": task.id,
            "parent_task_id": task.parent_task_id,
            "root_trace_id": task.root_trace_id,
            "child_session_id": task.session_id,
            "child_turn_id": task.turn_id,
            "child_request_id": task.request_id,
            "child_trace_id": task.trace_id,
            "attempt_id": task.attempt_id,
            "fencing_token": task.fencing_token,
            "worker_task_id": str(
                metadata.get("task_id") or context.get("task_id") or ""
            ),
            "worker_agent_key": str(
                payload.get("agent_key") or metadata.get("worker_agent_key") or ""
            ),
            "worker_parent_turn_id": str(metadata.get("parent_turn_id") or ""),
            "status": status,
            "summary": summary,
            "result_text": result_text,
            "usage": dict(usage or {}),
        }

    async def _notify_parent_child_done(self, task, notification: dict) -> bool:
        if self._coordinator is None:
            LOGGER.warning(
                "coordinator_child_notification_skipped",
                extra={
                    "task_id": task.id,
                    "parent_task_id": task.parent_task_id,
                    "reason": "coordinator_service_not_injected",
                },
            )
            return False
        try:
            await self._coordinator.on_child_task_finished(
                parent_task_id=str(task.parent_task_id),
                child_task_id=task.id,
                child_session_id=task.session_id,
                notification=notification,
            )
            return True
        except Exception as exc:
            # The child fact is already committed; losing only the parent
            # continuation must never rewrite the child outcome.
            LOGGER.exception(
                "coordinator_child_notification_failed",
                extra={
                    "task_id": task.id,
                    "parent_task_id": task.parent_task_id,
                    "error_type": type(exc).__name__,
                },
            )
            return False

    async def recover_settled_coordinator_children(self) -> int:
        """Retry a committed child result whose parent resume has not been queued."""
        retried = 0
        for task in await self._tasks.list_settled_children_needing_resume():
            payload = task.result_payload or {}
            timed_out = task.error_code == "coordinator_child_timeout"
            status = str(payload.get("status") or task.status)
            summary = (
                self._child_summary(task, "failed")
                + f" Timeout after {self._settings.orchestration.coordinator_child_timeout_seconds}s"
                " without execution progress or a live lease."
                if timed_out else str(payload.get("summary") or self._child_summary(task, status))
            )
            notification = self._child_notification(
                task,
                status=status,
                summary=summary,
                result_text=str(payload.get("result_text") or task.error_code or ""),
            )
            if notification is None:
                continue
            notification.update(payload)
            if timed_out:
                notification["summary"] = summary
            if await self._notify_parent_child_done(task, notification):
                retried += 1
        if retried:
            LOGGER.info("coordinator_settled_children_retried", extra={"retried": retried})
        return retried

    async def reap_coordinator_children(self) -> int:
        """Fail stalled unowned children and notify their parents."""
        config = self._settings.orchestration
        rows = await self._tasks.reap_expired_children(
            now=datetime.now(timezone.utc),
            timeout_seconds=config.coordinator_child_timeout_seconds,
        )
        for row in rows:
            try:
                task = await self._tasks.get(row["id"])
            except KeyError:
                LOGGER.warning(
                    "coordinator_child_reap_record_missing",
                    extra={"child_task_id": row["id"], "parent_task_id": row.get("parent_task_id")},
                )
                continue
            notification = self._child_notification(
                task,
                status="failed",
                summary=self._child_summary(task, "failed")
                + f" Timeout after {config.coordinator_child_timeout_seconds}s"
                " without execution progress or a live lease.",
                result_text="coordinator_child_timeout",
            )
            if notification is None:
                continue
            await self._notify_parent_child_done(task, notification)
        if rows:
            LOGGER.info(
                "coordinator_children_reaped",
                extra={"reaped": len(rows), "timeout_seconds": config.coordinator_child_timeout_seconds},
            )
        return len(rows)

    async def start_child_reaper(self) -> None:
        """Start the overdue-child reaper when durable children are enabled."""
        if not self._settings.orchestration.coordinator_durable_children:
            LOGGER.info("coordinator_child_reaper_disabled")
            return
        if self._child_reaper_task is not None and not self._child_reaper_task.done():
            return
        self._child_reaper_stop = asyncio.Event()
        self._child_reaper_task = asyncio.create_task(
            self._child_reaper_loop(),
            name="coordinator-child-reaper",
        )
        LOGGER.info(
            "coordinator_child_reaper_started",
            extra={
                "interval_seconds": self._child_reaper_interval_seconds(),
                "timeout_seconds": self._settings.orchestration.coordinator_child_timeout_seconds,
            },
        )

    async def stop_child_reaper(self) -> None:
        """Stop the reaper without leaving a background task behind."""
        task = self._child_reaper_task
        stop = self._child_reaper_stop
        self._child_reaper_task = None
        self._child_reaper_stop = None
        if task is None:
            return
        if stop is not None:
            stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        LOGGER.info("coordinator_child_reaper_stopped")

    def _child_reaper_interval_seconds(self) -> float:
        return max(self._settings.orchestration.agent_task_control_poll_seconds * 10, .05)

    async def _child_reaper_loop(self) -> None:
        stop = self._child_reaper_stop
        interval = self._child_reaper_interval_seconds()
        while stop is not None and not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), interval)
                return
            except TimeoutError:
                pass
            try:
                await self.reap_coordinator_children()
                await self.recover_settled_coordinator_children()
            except Exception as exc:
                LOGGER.exception(
                    "coordinator_child_reaper_pass_failed",
                    extra={"error_type": type(exc).__name__},
                )
