"""Persist API submissions and execute them only in an Agent Worker."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.core.request_context import bind_request_context, get_request_context, new_id
from src.runtime.resource_lease_manager import ResourceUnavailable
from src.runtime.store import ContinuationLeaseLostError
from src.runtime.task_deferred import TaskDeferred
from src.schemas.agent_task import (
    AgentTaskAccepted,
    AgentTaskCommand,
    AgentTaskRecord,
    TERMINAL_AGENT_TASK_STATUSES,
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


class AgentTaskDispatcher:
    def __init__(self, settings, tasks, sessions, resources):
        self._settings = settings
        self._tasks = tasks
        self._sessions = sessions
        self._resources = resources

    async def submit(self, session_id, payload):
        session = await self._sessions._require_session(session_id)
        if session.project_id:
            await self._sessions._require_active_project(session.project_id)
        # Pure normalization validates modes, attachments and empty input. Model
        # intent enrichment runs later, under Worker admission and ownership.
        request = self._sessions._input_orchestrator_service.orchestrate(session, payload)
        context = get_request_context()
        command = AgentTaskCommand(
            task_id=new_id("task"),
            session_id=session_id,
            turn_id=request.turn_id,
            project_id=session.project_id,
            request_id=context.request_id if context else new_id("req"),
            trace_id=context.trace_id if context else new_id("trace"),
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
        task = await self._tasks.submit(command, payload, message, idempotency_key=key)
        LOGGER.info(
            "agent_task_submitted",
            extra={
                "task_id": task.id,
                "session_id": session_id,
                "turn_id": task.turn_id,
                "request_id": task.request_id,
                "trace_id": task.trace_id,
                "queue": self._settings.orchestration.agent_task_stream,
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
        if await self._tasks.interrupt(session_id):
            return await self._sessions.get_session(session_id)
        return await self._sessions.interrupt_session(session_id, payload)

    async def handle(self, delivery, *, worker_id):
        command = AgentTaskCommand.model_validate(delivery.payload)
        stored = await self._tasks.get(command.task_id)
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
        task = await self._tasks.claim(command.task_id, worker_id=worker_id)
        if task.status in TERMINAL_AGENT_TASK_STATUSES:
            return
        LOGGER.info(
            "agent_task_claimed",
            extra={
                "task_id": task.id,
                "session_id": task.session_id,
                "turn_id": task.turn_id,
                "attempt_id": task.attempt_id,
                "attempt_no": task.attempt_no,
                "worker_id": worker_id,
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
        ):
            await self._execute(task)

    async def _execute(self, task):
        lease, monitor = None, None
        stop = asyncio.Event()
        started = False
        config = self._settings.orchestration
        try:
            if await self._tasks.interrupt_requested(task.id):
                await self._tasks.finish(
                    task,
                    status="cancelled",
                    error_code="user_cancelled_before_admission",
                )
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
                if task.operation == "message":
                    response = await self._sessions.execute_claimed_message(
                        task,
                        ownership_monitor=monitor,
                    )
                elif task.operation == "manual_resume":
                    response = await self._sessions.execute_claimed_resume(
                        task,
                        ownership_monitor=monitor,
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
                await self._tasks.finish(task, status=status, result_status=outcome)
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
                await self._tasks.finish(task, status="failed", error_code=type(exc).__name__)
            else:
                await self._tasks.defer(task, "admission_unavailable")
                raise
        finally:
            async def cleanup():
                stop.set()
                if monitor is not None:
                    result = (await asyncio.gather(monitor, return_exceptions=True))[0]
                    if isinstance(result, BaseException):
                        LOGGER.warning(
                            "agent_task_monitor_stopped",
                            extra={
                                "task_id": task.id,
                                "error_type": type(result).__name__,
                            },
                        )
                if lease is not None:
                    try:
                        await self._resources.retire(lease)
                        LOGGER.info(
                            "agent_task_slot_released",
                            extra={
                                "task_id": task.id,
                                "attempt_id": task.attempt_id,
                                "worker_id": task.worker_id,
                                "resource_id": lease.resource_id,
                            },
                        )
                    except Exception as exc:
                        LOGGER.error(
                            "agent_task_slot_release_pending",
                            extra={
                                "task_id": task.id,
                                "error_type": type(exc).__name__,
                            },
                        )

            await self._sessions._settle_execution_tasks(asyncio.create_task(cleanup()))

    async def _monitor(self, task, lease, stop):
        config = self._settings.orchestration
        interval = min(
            config.agent_task_control_poll_seconds,
            config.resource_default_lease_seconds / 3,
        )
        interrupt_sent = False
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), interval)
                return
            except TimeoutError:
                pass
            if not await self._resources.renew(
                lease,
                lease_seconds=config.resource_default_lease_seconds,
            ):
                raise ContinuationLeaseLostError("Agent resource admission lease was lost")
            if not interrupt_sent and await self._tasks.interrupt_requested(task.id):
                self._sessions._runtime_service.request_interrupt(
                    task.session_id,
                    "User requested task interruption",
                )
                interrupt_sent = True
                LOGGER.info(
                    "agent_task_interrupt_applied",
                    extra={"task_id": task.id, "worker_id": task.worker_id},
                )
