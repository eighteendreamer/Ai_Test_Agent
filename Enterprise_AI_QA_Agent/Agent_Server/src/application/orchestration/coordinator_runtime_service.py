from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

from src.application.sessions.session_service import SessionService
from src.core.config import Settings
from src.registry.agents import AgentRegistry
from src.runtime.store import SessionStore
from src.schemas.session import (
    ChatMessage,
    CreateSessionRequest,
    ExecutionEvent,
    MessageRole,
    RuntimeMode,
    SendMessageRequest,
    SessionMode,
    SessionStatus,
    ToolApprovalRequest,
    ToolApprovalStatus,
)


@dataclass
class WorkerDispatchSpec:
    task_id: str
    description: str
    prompt: str
    agent_key: str
    model_key: str | None = None
    skill_keys: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


class CoordinatorRuntimeService:
    def __init__(
        self,
        settings: Settings,
        store: SessionStore,
        session_service: SessionService,
        agent_registry: AgentRegistry,
    ) -> None:
        self._settings = settings
        self._store = store
        self._session_service = session_service
        self._agent_registry = agent_registry
        self._parent_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._watch_tasks: dict[str, asyncio.Task[None]] = {}
        self._max_consecutive_failures = 3

    async def cancel_workers(
        self,
        *,
        task_ids: list[str],
        child_session_ids: list[str],
        reason: str,
    ) -> None:
        """Cancel coordinator-owned coroutines and settle their child sessions."""
        tasks = [
            task
            for task_id in task_ids
            for task in [self._active_tasks.pop(task_id, None)]
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for child_session_id in child_session_ids:
            session = await self._store.get_session(child_session_id)
            if session is None or session.status in {
                SessionStatus.completed,
                SessionStatus.failed,
                SessionStatus.interrupted,
            }:
                continue
            session.status = SessionStatus.interrupted
            session.updated_at = datetime.utcnow()
            control = dict(session.metadata.get("control") or {})
            control.update(
                {
                    "control_state": "interrupted",
                    "is_interrupted": True,
                    "is_resumable": False,
                    "preserve_resources": False,
                    "last_interrupt_reason": reason,
                    "last_control_source": "security_coordinator",
                }
            )
            session.metadata["control"] = control
            await self._store.save_session(session)
            await self._store.append_event(
                child_session_id,
                ExecutionEvent(
                    type="worker.interrupted",
                    session_id=child_session_id,
                    timestamp=datetime.utcnow(),
                    payload={"reason": reason, "source": "security_coordinator"},
                ),
            )

    async def dispatch(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        parent_session_id = str(context.get("session_id") or "")
        parent_turn_id = str(context.get("turn_id") or "")
        parent_trace_id = str(context.get("trace_id") or "")
        if not parent_session_id:
            raise ValueError("subagent-dispatch requires a parent session context.")

        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            raise KeyError(f"Parent session not found: {parent_session_id}")

        if self._is_dispatch_blocked(parent_session, parent_turn_id):
            guard = self._get_failure_guard(parent_session)
            return {
                "ok": False,
                "trace_id": parent_trace_id,
                "summary": "Automatic worker dispatch is blocked for the current turn after repeated failures.",
                "workers": [],
                "artifacts": [],
                "metrics": {
                    "worker_count": 0,
                    "consecutive_failures": int(guard.get("count", 0)),
                },
                "error": str(guard.get("last_error") or "dispatch_blocked"),
            }

        workers = self._normalize_workers(payload)
        if not workers:
            raise ValueError("subagent-dispatch requires at least one worker specification.")
        followup_workers = self._normalize_followup_workers(payload)
        completion_worker = self._normalize_completion_worker(payload)

        launch_records: list[dict[str, Any]] = []
        immediate_failures: list[tuple[WorkerDispatchSpec, str]] = []

        for worker in workers[: self._settings.coordinator_max_workers]:
            if not self._is_agent_available(worker.agent_key):
                launch_records.append(
                    {
                        "task_id": worker.task_id,
                        "child_session_id": "",
                        "agent_key": worker.agent_key,
                        "model_key": worker.model_key or "",
                        "description": worker.description,
                        "status": "failed",
                    }
                )
                immediate_failures.append((worker, f"Unknown agent: {worker.agent_key}"))
                continue

            debate_stage = str(worker.context.get("debate_stage") or "").strip()
            debate_round_index = int(worker.context.get("debate_round_index") or 0)
            dispatch_role = str(worker.context.get("dispatch_role") or "worker").strip() or "worker"
            mode_key = self._resolve_worker_mode_key(worker)
            child_session = await self._session_service.create_session(
                CreateSessionRequest(
                    title=f"Worker: {worker.description}",
                    session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background,
                    mode_key=mode_key,
                    preferred_model=worker.model_key,
                    selected_agent=worker.agent_key,
                    project_id=parent_session.project_id,
                    metadata={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "parent_trace_id": parent_trace_id,
                        "task_id": worker.task_id,
                        "worker_description": worker.description,
                        "dispatch_role": dispatch_role,
                        "notification_mode": "task-notification",
                        **self._trusted_worker_metadata(parent_session, mode_key),
                    },
                )
            )

            launch_record = {
                "task_id": worker.task_id,
                "child_session_id": child_session.id,
                "agent_key": worker.agent_key,
                "model_key": worker.model_key or "",
                "description": worker.description,
                "status": "running",
                "debate_stage": debate_stage,
                "debate_round_index": debate_round_index,
                "debate_total_round_count": int(worker.context.get("debate_total_round_count") or 0),
                "dispatch_role": dispatch_role,
                "source_stage": str(worker.context.get("source_stage") or "").strip(),
                "source_round_index": int(worker.context.get("source_round_index") or 0),
            }
            launch_records.append(launch_record)

            task = asyncio.create_task(
                self._run_child_session(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=child_session.id,
                    worker=worker,
                )
            )
            self._active_tasks[worker.task_id] = task
            task.add_done_callback(lambda _finished, task_id=worker.task_id: self._active_tasks.pop(task_id, None))

        await self._register_worker_dispatches(parent_session_id, parent_turn_id, launch_records)
        if followup_workers:
            await self._register_followup_workers(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                parent_trace_id=parent_trace_id,
                workers=followup_workers,
            )
        if completion_worker is not None:
            await self._register_completion_worker(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                parent_trace_id=parent_trace_id,
                worker=completion_worker,
            )

        for worker, failure_reason in immediate_failures:
            await self._deliver_notification(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                child_session_id="",
                worker=worker,
                content=self._build_task_notification(
                    task_id=worker.task_id,
                    child_session_id="",
                    agent_key=worker.agent_key,
                    trace_id=parent_trace_id,
                    status="failed",
                    summary=f'Worker "{worker.description}" failed: {failure_reason}',
                    result=failure_reason,
                    usage={},
                ),
                status="failed",
                failure_reason=failure_reason,
            )

        successful_launches = [item for item in launch_records if item.get("status") == "running"]
        failed_launches = [item for item in launch_records if item.get("status") == "failed"]
        immediate_failure_errors = [failure_reason for _, failure_reason in immediate_failures]
        dispatch_status = (
            "failed"
            if not successful_launches
            else "partial"
            if failed_launches
            else "completed"
        )
        return {
            "ok": bool(successful_launches),
            "status": dispatch_status,
            "trace_id": parent_trace_id,
            "summary": (
                f"Launched {len(successful_launches)} worker session(s) for coordinator orchestration."
                if dispatch_status == "completed"
                else f"Launched {len(successful_launches)} worker session(s); {len(failed_launches)} worker dispatch(es) failed immediately."
                if dispatch_status == "partial"
                else "No worker session was launched successfully."
            ),
            "workers": launch_records,
            "artifacts": [],
            "metrics": {
                "worker_count": len(successful_launches),
                "failed_worker_count": len(launch_records) - len(successful_launches),
            },
            "error": "; ".join(immediate_failure_errors) or None,
        }

    def _trusted_worker_metadata(self, parent_session: Any, mode_key: str) -> dict[str, Any]:
        """Propagate server-trusted security scope into security child sessions.

        Worker prompts are active security requests, so their safety assessment
        must validate the same verified grant as the parent campaign. Only
        server-persisted parent metadata is copied; arbitrary worker context is
        deliberately excluded. The child still passes normal permission and
        approval policy evaluation for its assigned runner.
        """
        if str(mode_key or "").strip() != "security_testing":
            return {}
        parent_metadata = parent_session.metadata if isinstance(parent_session.metadata, dict) else {}
        grant = parent_metadata.get("security_authorization")
        if not isinstance(grant, dict) or str(grant.get("status") or "").lower() != "verified":
            return {}
        trusted: dict[str, Any] = {"security_authorization": dict(grant)}
        resource_scope = parent_metadata.get("resource_scope")
        if isinstance(resource_scope, dict):
            trusted["resource_scope"] = dict(resource_scope)
        environment = str(parent_metadata.get("environment") or "").strip()
        if environment:
            trusted["environment"] = environment
        return trusted

    async def _run_child_session(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
    ) -> None:
        started_at = datetime.utcnow()
        notification_status = "completed"
        summary = f'Worker "{worker.description}" completed.'
        result_text = ""
        usage: dict[str, Any] = {}

        try:
            mode_key = self._resolve_worker_mode_key(worker)
            response = await self._session_service.send_message(
                child_session_id,
                SendMessageRequest(
                    content=worker.prompt,
                    mode_key=mode_key,
                    agent_key=worker.agent_key,
                    model_key=worker.model_key,
                    skill_keys=worker.skill_keys,
                    context={
                        **worker.context,
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "parent_trace_id": parent_trace_id,
                        "task_id": worker.task_id,
                        "dispatch_description": worker.description,
                    },
                    metadata={
                        "message_kind": "coordinator_assignment",
                        "task_id": worker.task_id,
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                    },
                ),
            )
            child_session = response.session
            result_text = response.output.content
            notification_status = child_session.status.value
            summary = f'Worker "{worker.description}" finished with status {child_session.status.value}.'
            usage = {
                "total_messages": len(child_session.messages),
                "tool_uses": sum(1 for message in child_session.messages if message.role == MessageRole.tool),
                "duration_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
                "event_count": child_session.event_count,
                "snapshot_count": child_session.snapshot_count,
            }
        except Exception as exc:
            notification_status = "failed"
            summary = f'Worker "{worker.description}" failed: {exc}'
            result_text = str(exc)
            usage = {
                "total_messages": 0,
                "tool_uses": 0,
                "duration_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
            }

        notification_xml = self._build_task_notification(
            task_id=worker.task_id,
            child_session_id=child_session_id,
            agent_key=worker.agent_key,
            trace_id=parent_trace_id,
            status=notification_status,
            summary=summary,
            result=result_text,
            usage=usage,
        )
        await self._deliver_notification(
            parent_session_id=parent_session_id,
            parent_turn_id=parent_turn_id,
            child_session_id=child_session_id,
            worker=worker,
            content=notification_xml,
            status=notification_status,
            failure_reason=result_text if notification_status == "failed" else "",
        )
        if notification_status == SessionStatus.waiting_approval.value:
            watch_task = asyncio.create_task(
                self._watch_child_session_until_settled(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=child_session_id,
                    worker=worker,
                )
            )
            self._watch_tasks[worker.task_id] = watch_task
            watch_task.add_done_callback(
                lambda _finished, task_id=worker.task_id: self._watch_tasks.pop(task_id, None)
            )

    async def _deliver_notification(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
        content: str,
        status: str,
        failure_reason: str = "",
    ) -> None:
        async with self._parent_locks[parent_session_id]:
            parent_session = await self._store.get_session(parent_session_id)
            if parent_session is None:
                return

            await self._mark_worker_status(
                parent_session_id=parent_session_id,
                task_id=worker.task_id,
                status=status,
                child_session_id=child_session_id,
                worker=worker,
            )
            await self._store.append_event(
                parent_session_id,
                ExecutionEvent(
                    type="worker.task_notification_received",
                    session_id=parent_session_id,
                    timestamp=datetime.utcnow(),
                    payload={
                        "turn_id": parent_turn_id,
                        "task_id": worker.task_id,
                        "child_session_id": child_session_id,
                        "worker_agent_key": worker.agent_key,
                        "worker_status": status,
                    },
                ),
            )
            await self._sync_child_approvals_to_parent(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                child_session_id=child_session_id,
                worker=worker,
            )

            parent_session = await self._store.get_session(parent_session_id)
            if parent_session is None:
                return

            should_stop, stop_message = self._update_failure_guard(
                session=parent_session,
                parent_turn_id=parent_turn_id,
                status=status,
                worker=worker,
                failure_reason=failure_reason or self._extract_failure_reason(content),
            )
            self._update_code_review_report_metadata(
                session=parent_session,
                worker=worker,
                status=status,
                child_session_id=child_session_id,
                content=content,
            )
            await self._store.save_session(parent_session)

            if should_stop:
                parent_session.messages.append(
                    ChatMessage(
                        id=str(uuid4()),
                        role=MessageRole.assistant,
                        content=stop_message,
                        created_at=datetime.utcnow(),
                        metadata={
                            "message_kind": "automatic_stop",
                            "turn_id": parent_turn_id,
                            "worker_agent_key": worker.agent_key,
                            "worker_status": status,
                        },
                    )
                )
                parent_session.status = SessionStatus.failed
                parent_session.updated_at = datetime.utcnow()
                await self._store.save_session(parent_session)
                await self._store.append_event(
                    parent_session_id,
                    ExecutionEvent(
                        type="worker.auto_stopped",
                        session_id=parent_session_id,
                        timestamp=datetime.utcnow(),
                        payload={
                            "turn_id": parent_turn_id,
                            "task_id": worker.task_id,
                            "reason": stop_message,
                            "worker_agent_key": worker.agent_key,
                        },
                    ),
                )
                return

            followup_launches = await self._maybe_launch_followup_workers(
                parent_session=parent_session,
                parent_turn_id=parent_turn_id,
            )
            if followup_launches:
                parent_session = await self._store.get_session(parent_session_id)
                if parent_session is None:
                    return

            completion_launch = await self._maybe_launch_completion_worker(
                parent_session=parent_session,
                parent_turn_id=parent_turn_id,
            )
            if completion_launch is not None:
                parent_session = await self._store.get_session(parent_session_id)
                if parent_session is None:
                    return

            auto_resume = (
                parent_session.session_mode == SessionMode.coordinator
                or (parent_session.selected_agent or "") == "coordinator"
            ) and status != SessionStatus.waiting_approval.value and parent_session.status not in {
                SessionStatus.running,
                SessionStatus.waiting_approval,
            }

            if auto_resume:
                await self._session_service.send_message(
                    parent_session_id,
                    SendMessageRequest(
                        content=content,
                        agent_key=parent_session.selected_agent or "coordinator",
                        skill_keys=[],
                        context={
                            "message_source": "task_notification",
                            "parent_turn_id": parent_turn_id,
                            "child_session_id": child_session_id,
                            "task_id": worker.task_id,
                            "worker_agent_key": worker.agent_key,
                            "worker_status": status,
                        },
                        metadata={
                            "message_kind": "task_notification",
                            "task_id": worker.task_id,
                            "child_session_id": child_session_id,
                            "worker_agent_key": worker.agent_key,
                            "worker_status": status,
                        },
                    ),
                )
                return

            notification_message = ChatMessage(
                id=str(uuid4()),
                role=MessageRole.user,
                content=content,
                created_at=datetime.utcnow(),
                metadata={
                    "message_kind": "task_notification",
                    "task_id": worker.task_id,
                    "child_session_id": child_session_id,
                    "worker_agent_key": worker.agent_key,
                    "worker_status": status,
                    "persist_transcript": False,
                    "context_eligible": False,
                    "transcript_bucket": "event",
                },
            )
            parent_session.messages.append(notification_message)
            parent_session.updated_at = datetime.utcnow()
            await self._store.save_session(parent_session)

    async def _watch_child_session_until_settled(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
    ) -> None:
        running_marked = False
        while True:
            await asyncio.sleep(1)
            child_session = await self._store.get_session(child_session_id)
            if child_session is None:
                return

            status = child_session.status.value
            if status == SessionStatus.waiting_approval.value:
                # Child workers can create fresh approvals after a prior approval
                # continuation. Keep mirroring them to the parent while blocked so
                # the workbench approval panel stays truthful.
                await self._sync_child_approvals_to_parent(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    child_session_id=child_session_id,
                    worker=worker,
                )
                continue

            if status == SessionStatus.running.value:
                if not running_marked:
                    await self._mark_worker_status(
                        parent_session_id=parent_session_id,
                        task_id=worker.task_id,
                        status=status,
                        child_session_id=child_session_id,
                    )
                    running_marked = True
                continue

            child_detail = await self._session_service.get_session(child_session_id)
            result_text = self._extract_worker_result(child_detail.messages)
            usage = {
                "total_messages": len(child_detail.messages),
                "tool_uses": sum(1 for message in child_detail.messages if message.role == MessageRole.tool),
                "duration_ms": 0,
                "event_count": child_detail.event_count,
                "snapshot_count": child_detail.snapshot_count,
            }
            notification_xml = self._build_task_notification(
                task_id=worker.task_id,
                child_session_id=child_session_id,
                agent_key=worker.agent_key,
                trace_id=parent_trace_id,
                status=status,
                summary=f'Worker "{worker.description}" finished with status {status}.',
                result=result_text,
                usage=usage,
            )
            await self._deliver_notification(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                child_session_id=child_session_id,
                worker=worker,
                content=notification_xml,
                status=status,
                failure_reason=result_text if status == SessionStatus.failed.value else "",
            )
            return

    async def _sync_child_approvals_to_parent(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
    ) -> None:
        if not child_session_id:
            return

        child_approvals = await self._store.list_approvals(child_session_id)
        parent_approvals = await self._store.list_approvals(parent_session_id)
        existing_proxy_by_child_id: dict[str, ToolApprovalRequest] = {}
        for approval in parent_approvals:
            metadata = approval.metadata if isinstance(approval.metadata, dict) else {}
            if str(metadata.get("proxy_child_session_id") or "") != child_session_id:
                continue
            child_approval_id = str(metadata.get("proxy_child_approval_id") or "").strip()
            if child_approval_id:
                existing_proxy_by_child_id[child_approval_id] = approval

        for child_approval in child_approvals:
            proxy = existing_proxy_by_child_id.get(child_approval.id)
            proxy_id = proxy.id if proxy is not None else f"worker-approval-{worker.task_id}-{child_approval.id}"
            metadata = {
                **(child_approval.metadata if isinstance(child_approval.metadata, dict) else {}),
                "proxy_child_session_id": child_session_id,
                "proxy_child_approval_id": child_approval.id,
                "proxy_task_id": worker.task_id,
                "proxy_parent_turn_id": parent_turn_id,
                "proxy_worker_agent_key": worker.agent_key,
                "selected_agent_key": (
                    child_approval.metadata.get("selected_agent_key")
                    if isinstance(child_approval.metadata, dict) and child_approval.metadata.get("selected_agent_key")
                    else worker.agent_key
                ),
            }
            mirrored = ToolApprovalRequest(
                id=proxy_id,
                session_id=parent_session_id,
                tool_key=child_approval.tool_key,
                tool_name=child_approval.tool_name,
                reason=child_approval.reason,
                status=child_approval.status,
                created_at=child_approval.created_at,
                resolved_at=child_approval.resolved_at,
                decision_note=child_approval.decision_note,
                metadata=metadata,
            )
            previous_status = proxy.status if proxy is not None else None

            if proxy is not None and proxy.status != ToolApprovalStatus.pending:
                continue

            await self._store.save_approval(parent_session_id, mirrored)
            if proxy is None and mirrored.status == ToolApprovalStatus.pending:
                await self._store.append_event(
                    parent_session_id,
                    ExecutionEvent(
                        type="approval.created",
                        session_id=parent_session_id,
                        timestamp=datetime.utcnow(),
                        payload={
                            "turn_id": parent_turn_id,
                            "message": "A worker approval request has been synchronized from a child session.",
                            "approval_id": mirrored.id,
                            "tool_key": mirrored.tool_key,
                            "tool_name": mirrored.tool_name,
                            "reason": mirrored.reason,
                            "child_session_id": child_session_id,
                        },
                    ),
                )
            elif proxy is not None and previous_status != mirrored.status and mirrored.status != ToolApprovalStatus.pending:
                await self._store.append_event(
                    parent_session_id,
                    ExecutionEvent(
                        type="approval.resolved",
                        session_id=parent_session_id,
                        timestamp=datetime.utcnow(),
                        payload={
                            "approval_id": mirrored.id,
                            "tool_key": mirrored.tool_key,
                            "decision": mirrored.status.value,
                            "child_session_id": child_session_id,
                        },
                    ),
                )

    async def _register_worker_dispatches(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        launch_records: list[dict[str, Any]],
    ) -> None:
        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return

        guard = self._get_failure_guard(parent_session)
        if str(guard.get("turn_id") or "") != parent_turn_id:
            self._reset_failure_guard(parent_session, parent_turn_id)

        existing = parent_session.metadata.get("worker_dispatches", [])
        merged_by_task_id: dict[str, dict[str, Any]] = {}
        ordered_task_ids: list[str] = []
        anonymous: list[dict[str, Any]] = []
        for record in existing:
            if not isinstance(record, dict):
                continue
            task_id = str(record.get("task_id") or "").strip()
            if not task_id:
                anonymous.append(record)
                continue
            if task_id not in merged_by_task_id:
                ordered_task_ids.append(task_id)
            merged_by_task_id[task_id] = record

        for record in launch_records:
            if not isinstance(record, dict):
                continue
            task_id = str(record.get("task_id") or "").strip()
            if not task_id:
                anonymous.append(record)
                continue
            existing_record = merged_by_task_id.get(task_id)
            if existing_record is None:
                merged_by_task_id[task_id] = dict(record)
                ordered_task_ids.append(task_id)
                continue
            # Merge into the existing record. Preserve the first non-empty
            # child_session_id so downstream consumers can still correlate to
            # the original launch even if the same task is re-registered.
            merged = {**existing_record, **record}
            if not str(merged.get("child_session_id") or "").strip():
                merged["child_session_id"] = existing_record.get("child_session_id") or ""
            merged_by_task_id[task_id] = merged

        rebuilt = [merged_by_task_id[task_id] for task_id in ordered_task_ids]
        rebuilt.extend(anonymous)
        parent_session.metadata["worker_dispatches"] = rebuilt
        await self._store.save_session(parent_session)

    async def _register_followup_workers(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        workers: list[WorkerDispatchSpec],
    ) -> None:
        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return
        total_round_count = 0
        if workers:
            total_round_count = int(workers[0].context.get("debate_total_round_count") or 0)
        parent_session.metadata["pending_followup_workers"] = [
            {
                "task_id": worker.task_id,
                "description": worker.description,
                "prompt": worker.prompt,
                "agent_key": worker.agent_key,
                "model_key": worker.model_key,
                "skill_keys": list(worker.skill_keys),
                "context": dict(worker.context),
            }
            for worker in workers
        ]
        parent_session.metadata["followup_workers_parent_turn_id"] = parent_turn_id
        parent_session.metadata["followup_workers_parent_trace_id"] = parent_trace_id
        parent_session.metadata["followup_workers_dispatched"] = False
        parent_session.metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
            stage="independent_findings",
            status="running",
            current_round_index=1,
            total_round_count=total_round_count,
        )
        await self._store.save_session(parent_session)

    async def _register_completion_worker(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        worker: WorkerDispatchSpec,
    ) -> None:
        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return
        parent_session.metadata["pending_completion_worker"] = {
            "task_id": worker.task_id,
            "description": worker.description,
            "prompt": worker.prompt,
            "agent_key": worker.agent_key,
            "model_key": worker.model_key,
            "skill_keys": list(worker.skill_keys),
            "context": dict(worker.context),
            "parent_turn_id": parent_turn_id,
            "parent_trace_id": parent_trace_id,
        }
        parent_session.metadata["completion_worker_dispatched"] = False
        parent_session.metadata["code_review_report"] = {
            "task_id": worker.task_id,
            "agent_key": worker.agent_key,
            "description": worker.description,
            "status": "pending",
            "updated_at": datetime.utcnow().isoformat(),
        }
        parent_session.metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
            stage="independent_findings",
            status="running",
            current_round_index=1,
            total_round_count=int(worker.context.get("debate_total_round_count") or 0),
        )
        await self._store.save_session(parent_session)

    async def _mark_worker_status(
        self,
        parent_session_id: str,
        task_id: str,
        status: str,
        child_session_id: str,
        worker: WorkerDispatchSpec | None = None,
    ) -> None:
        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return

        worker_dispatches = []
        matched = False
        completion_task_id = str(parent_session.metadata.get("completion_worker_task_id") or "")
        for record in parent_session.metadata.get("worker_dispatches", []):
            if not isinstance(record, dict):
                continue
            if record.get("task_id") == task_id:
                matched = True
                worker_dispatches.append(
                    {
                        **record,
                        "status": status,
                        "child_session_id": child_session_id,
                        "completed_at": datetime.utcnow().isoformat(),
                    }
                )
            else:
                worker_dispatches.append(record)

        if not matched and worker is not None:
            worker_dispatches.append(
                {
                    "task_id": task_id,
                    "child_session_id": child_session_id,
                    "agent_key": worker.agent_key,
                    "model_key": worker.model_key or "",
                    "description": worker.description,
                    "status": status,
                    "completed_at": datetime.utcnow().isoformat(),
                    "is_completion_worker": bool(completion_task_id and completion_task_id == task_id),
                    "debate_stage": str(worker.context.get("debate_stage") or "").strip(),
                    "debate_round_index": int(worker.context.get("debate_round_index") or 0),
                    "debate_total_round_count": int(worker.context.get("debate_total_round_count") or 0),
                    "dispatch_role": str(worker.context.get("dispatch_role") or "").strip(),
                    "source_stage": str(worker.context.get("source_stage") or "").strip(),
                    "source_round_index": int(worker.context.get("source_round_index") or 0),
                }
            )

        parent_session.metadata["worker_dispatches"] = worker_dispatches
        await self._store.save_session(parent_session)

    def _normalize_workers(self, payload: dict[str, Any]) -> list[WorkerDispatchSpec]:
        if isinstance(payload.get("workers"), list):
            raw_workers = payload["workers"]
        else:
            raw_workers = [payload]

        workers: list[WorkerDispatchSpec] = []
        for item in raw_workers:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            agent_key = str(item.get("agent_key") or "qa-planner").strip()
            if not description or not prompt:
                continue
            skill_keys = [
                str(skill_key).strip()
                for skill_key in item.get("skill_keys", [])
                if str(skill_key).strip()
            ]
            workers.append(
                WorkerDispatchSpec(
                    task_id=str(item.get("task_id") or uuid4()),
                    description=description,
                    prompt=prompt,
                    agent_key=agent_key,
                    model_key=str(item.get("model_key") or "").strip() or None,
                    skill_keys=skill_keys,
                    context=dict(item.get("context", {})) if isinstance(item.get("context"), dict) else {},
                )
            )
        return workers

    def _normalize_followup_workers(self, payload: dict[str, Any]) -> list[WorkerDispatchSpec]:
        raw_workers = payload.get("followup_workers")
        if not isinstance(raw_workers, list):
            return []

        workers: list[WorkerDispatchSpec] = []
        for item in raw_workers:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            agent_key = str(item.get("agent_key") or "").strip()
            if not description or not prompt or not agent_key:
                continue
            skill_keys = [
                str(skill_key).strip()
                for skill_key in item.get("skill_keys", [])
                if str(skill_key).strip()
            ]
            workers.append(
                WorkerDispatchSpec(
                    task_id=str(item.get("task_id") or uuid4()),
                    description=description,
                    prompt=prompt,
                    agent_key=agent_key,
                    model_key=str(item.get("model_key") or "").strip() or None,
                    skill_keys=skill_keys,
                    context=dict(item.get("context", {})) if isinstance(item.get("context"), dict) else {},
                )
            )
        return workers

    def _normalize_completion_worker(self, payload: dict[str, Any]) -> WorkerDispatchSpec | None:
        raw_item = payload.get("completion_worker")
        if not isinstance(raw_item, dict):
            return None
        description = str(raw_item.get("description") or "").strip()
        prompt = str(raw_item.get("prompt") or "").strip()
        agent_key = str(raw_item.get("agent_key") or "").strip()
        if not description or not prompt or not agent_key:
            return None
        skill_keys = [
            str(skill_key).strip()
            for skill_key in raw_item.get("skill_keys", [])
            if str(skill_key).strip()
        ]
        return WorkerDispatchSpec(
            task_id=str(raw_item.get("task_id") or uuid4()),
            description=description,
            prompt=prompt,
            agent_key=agent_key,
            model_key=str(raw_item.get("model_key") or "").strip() or None,
            skill_keys=skill_keys,
            context=dict(raw_item.get("context", {})) if isinstance(raw_item.get("context"), dict) else {},
        )

    async def _maybe_launch_completion_worker(
        self,
        parent_session,
        parent_turn_id: str,
    ) -> dict[str, Any] | None:
        pending = parent_session.metadata.get("pending_completion_worker")
        if not isinstance(pending, dict):
            return None
        if bool(parent_session.metadata.get("completion_worker_dispatched")):
            return None
        if str(pending.get("parent_turn_id") or "") != parent_turn_id:
            return None

        pending_followup_workers = parent_session.metadata.get("pending_followup_workers")
        if isinstance(pending_followup_workers, list) and pending_followup_workers:
            return None

        reviewer_dispatches = [
            record
            for record in parent_session.metadata.get("worker_dispatches", [])
            if isinstance(record, dict) and not record.get("is_completion_worker")
        ]
        if not reviewer_dispatches:
            return None
        if any(
            str(record.get("status") or "") in {"running", "waiting_approval", "idle", ""}
            for record in reviewer_dispatches
        ):
            return None

        worker = WorkerDispatchSpec(
            task_id=str(pending.get("task_id") or uuid4()),
            description=str(pending.get("description") or "Completion Worker"),
            prompt=str(pending.get("prompt") or "").strip(),
            agent_key=str(pending.get("agent_key") or "").strip(),
            model_key=str(pending.get("model_key") or "").strip() or None,
            skill_keys=[
                str(skill_key).strip()
                for skill_key in pending.get("skill_keys", [])
                if str(skill_key).strip()
            ],
            context=dict(pending.get("context", {})) if isinstance(pending.get("context"), dict) else {},
        )
        if not worker.prompt or not worker.agent_key:
            return None

        parent_session_id = parent_session.id
        parent_trace_id = str(pending.get("parent_trace_id") or "")
        worker.context = {
            **worker.context,
            "completion_trigger": "all_reviewers_completed",
            "reviewer_dispatches": reviewer_dispatches,
        }

        child_session = await self._session_service.create_session(
            CreateSessionRequest(
                title=f"Worker: {worker.description}",
                session_mode=SessionMode.background_task,
                runtime_mode=RuntimeMode.background,
                mode_key=self._resolve_worker_mode_key(worker),
                preferred_model=worker.model_key,
                selected_agent=worker.agent_key,
                project_id=parent_session.project_id,
                metadata={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "parent_trace_id": parent_trace_id,
                    "task_id": worker.task_id,
                    "worker_description": worker.description,
                    "dispatch_role": "completion_worker",
                    "notification_mode": "task-notification",
                },
            )
        )

        launch_record = {
            "task_id": worker.task_id,
            "child_session_id": child_session.id,
            "agent_key": worker.agent_key,
            "model_key": worker.model_key or "",
            "description": worker.description,
            "status": "running",
            "is_completion_worker": True,
            "debate_stage": "summary_resolution",
            "debate_round_index": int(worker.context.get("debate_round_index") or 0),
            "debate_total_round_count": int(worker.context.get("debate_total_round_count") or 0),
            "dispatch_role": "completion_worker",
        }
        worker_dispatches = list(parent_session.metadata.get("worker_dispatches", []))
        worker_dispatches.append(launch_record)
        parent_session.metadata["worker_dispatches"] = worker_dispatches
        parent_session.metadata["completion_worker_dispatched"] = True
        parent_session.metadata["completion_worker_task_id"] = worker.task_id
        parent_session.metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
            stage="summary_resolution",
            status="running",
            current_round_index=int(worker.context.get("debate_round_index") or 0),
            total_round_count=int(worker.context.get("debate_total_round_count") or 0),
        )
        await self._store.save_session(parent_session)

        task = asyncio.create_task(
            self._run_child_session(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                parent_trace_id=parent_trace_id,
                child_session_id=child_session.id,
                worker=worker,
            )
        )
        self._active_tasks[worker.task_id] = task
        task.add_done_callback(lambda _finished, task_id=worker.task_id: self._active_tasks.pop(task_id, None))
        return launch_record

    async def _maybe_launch_followup_workers(
        self,
        parent_session,
        parent_turn_id: str,
    ) -> list[dict[str, Any]] | None:
        pending_raw = parent_session.metadata.get("pending_followup_workers")
        if not isinstance(pending_raw, list) or not pending_raw:
            return None
        if str(parent_session.metadata.get("followup_workers_parent_turn_id") or "") != parent_turn_id:
            return None

        normalized_pending = [item for item in pending_raw if isinstance(item, dict)]
        if not normalized_pending:
            return None

        next_round_index = min(self._pending_worker_round_index(item) for item in normalized_pending)
        round_batch = [
            item for item in normalized_pending if self._pending_worker_round_index(item) == next_round_index
        ]
        if not round_batch:
            return None

        source_round_index = self._pending_worker_source_round_index(round_batch[0])
        source_stage = self._pending_worker_source_stage(round_batch[0])
        source_dispatches = [
            record
            for record in parent_session.metadata.get("worker_dispatches", [])
            if isinstance(record, dict)
            and not record.get("is_completion_worker")
            and int(record.get("debate_round_index") or 0) == source_round_index
            and str(record.get("debate_stage") or "").strip() == source_stage
        ]
        if not source_dispatches:
            return None
        if any(
            str(record.get("status") or "") in {"running", "waiting_approval", "idle", "", "pending"}
            for record in source_dispatches
        ):
            return None

        peer_bundle = await self._build_peer_review_bundle(source_dispatches)
        launch_records: list[dict[str, Any]] = []
        parent_session_id = parent_session.id
        parent_trace_id = str(parent_session.metadata.get("followup_workers_parent_trace_id") or "")
        current_round_total = int(self._pending_worker_total_round_count(round_batch[0]) or 0)

        for item in round_batch[: self._settings.coordinator_max_workers]:
            worker = WorkerDispatchSpec(
                task_id=str(item.get("task_id") or uuid4()),
                description=str(item.get("description") or "Cross Review Worker"),
                prompt=str(item.get("prompt") or "").strip(),
                agent_key=str(item.get("agent_key") or "").strip(),
                model_key=str(item.get("model_key") or "").strip() or None,
                skill_keys=[
                    str(skill_key).strip()
                    for skill_key in item.get("skill_keys", [])
                    if str(skill_key).strip()
                ],
                context=dict(item.get("context", {})) if isinstance(item.get("context"), dict) else {},
            )
            if not worker.prompt or not worker.agent_key:
                continue
            if not self._is_agent_available(worker.agent_key):
                continue

            debate_stage = str(worker.context.get("debate_stage") or "cross_review").strip() or "cross_review"
            debate_round_index = int(worker.context.get("debate_round_index") or next_round_index)
            source_stage = str(worker.context.get("source_stage") or source_stage).strip() or "independent_findings"
            source_round_index = int(worker.context.get("source_round_index") or source_round_index)
            worker.context = {
                **worker.context,
                "debate_stage": debate_stage,
                "debate_round_index": debate_round_index,
                "peer_review_bundle": peer_bundle,
            }
            worker.prompt = (
                f"{worker.prompt}\n\n"
                f"第 {source_round_index} 轮同伴结论汇总：\n"
                f"{peer_bundle}\n\n"
                "执行约束：\n"
                "- 先围绕这份同伴结论汇总展开攻防，再决定是否需要补充读取文件。\n"
                "- 只有在你需要一条精确证据来支持或反驳某条结论时，才允许额外读取文件。\n"
                "- 在反驳或支持时，请明确引用 peer finding id 或评审官名称。\n"
            )

            child_session = await self._session_service.create_session(
                CreateSessionRequest(
                    title=f"Worker: {worker.description}",
                    session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background,
                    mode_key=self._resolve_worker_mode_key(worker),
                    preferred_model=worker.model_key,
                    selected_agent=worker.agent_key,
                    project_id=parent_session.project_id,
                    metadata={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "parent_trace_id": parent_trace_id,
                        "task_id": worker.task_id,
                        "worker_description": worker.description,
                        "dispatch_role": "debate_followup",
                        "notification_mode": "task-notification",
                    },
                )
            )

            launch_record = {
                "task_id": worker.task_id,
                "child_session_id": child_session.id,
                "agent_key": worker.agent_key,
                "model_key": worker.model_key or "",
                "description": worker.description,
                "status": "running",
                "debate_stage": debate_stage,
                "debate_round_index": debate_round_index,
                "debate_total_round_count": int(worker.context.get("debate_total_round_count") or current_round_total),
                "dispatch_role": "debate_followup",
                "source_stage": source_stage,
                "source_round_index": source_round_index,
            }
            launch_records.append(launch_record)

            task = asyncio.create_task(
                self._run_child_session(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=child_session.id,
                    worker=worker,
                )
            )
            self._active_tasks[worker.task_id] = task
            task.add_done_callback(lambda _finished, task_id=worker.task_id: self._active_tasks.pop(task_id, None))

        remaining_pending = [
            item for item in normalized_pending if self._pending_worker_round_index(item) != next_round_index
        ]
        if not launch_records:
            parent_session.metadata["pending_followup_workers"] = remaining_pending
            parent_session.metadata["followup_workers_dispatched"] = not bool(remaining_pending)
            parent_session.metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
                stage="summary_resolution" if not remaining_pending else "cross_review",
                status="running",
                current_round_index=next_round_index if remaining_pending else current_round_total,
                total_round_count=current_round_total,
                peer_review_count=len(source_dispatches),
            )
            await self._store.save_session(parent_session)
            return None

        worker_dispatches = list(parent_session.metadata.get("worker_dispatches", []))
        worker_dispatches.extend(launch_records)
        parent_session.metadata["worker_dispatches"] = worker_dispatches
        parent_session.metadata["pending_followup_workers"] = remaining_pending
        parent_session.metadata["followup_workers_dispatched"] = not bool(remaining_pending)
        parent_session.metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
            stage="cross_review",
            status="running",
            current_round_index=next_round_index,
            total_round_count=current_round_total,
            peer_review_count=len(source_dispatches),
        )
        await self._store.save_session(parent_session)
        return launch_records

    def _update_code_review_report_metadata(
        self,
        session,
        worker: WorkerDispatchSpec,
        status: str,
        child_session_id: str,
        content: str,
    ) -> None:
        completion_task_id = str(session.metadata.get("completion_worker_task_id") or "")
        if not completion_task_id:
            pending_worker = session.metadata.get("pending_completion_worker")
            if isinstance(pending_worker, dict):
                completion_task_id = str(pending_worker.get("task_id") or "")
        if not completion_task_id or completion_task_id != worker.task_id:
            return

        report_meta = session.metadata.get("code_review_report", {})
        if not isinstance(report_meta, dict):
            report_meta = {}
        result_excerpt = self._extract_failure_reason(content) if content else ""
        session.metadata["code_review_report"] = {
            **report_meta,
            "task_id": worker.task_id,
            "agent_key": worker.agent_key,
            "description": worker.description,
            "status": status,
            "report_session_id": child_session_id or str(report_meta.get("report_session_id") or ""),
            "summary": result_excerpt[:4000],
            "updated_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat()
            if status not in {SessionStatus.running.value, SessionStatus.waiting_approval.value}
            else report_meta.get("completed_at"),
        }
        session.metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
            stage="completed" if status == SessionStatus.completed.value else "summary_resolution",
            status=status,
            current_round_index=int(worker.context.get("debate_round_index") or 0),
            total_round_count=int(worker.context.get("debate_total_round_count") or 0),
        )

    def _build_task_notification(
        self,
        task_id: str,
        child_session_id: str,
        agent_key: str,
        trace_id: str,
        status: str,
        summary: str,
        result: str,
        usage: dict[str, Any],
    ) -> str:
        summary_text = escape(summary)
        result_text = escape(result or "")
        usage_block = (
            "<usage>"
            f"<total_messages>{int(usage.get('total_messages', 0))}</total_messages>"
            f"<tool_uses>{int(usage.get('tool_uses', 0))}</tool_uses>"
            f"<duration_ms>{int(usage.get('duration_ms', 0))}</duration_ms>"
            f"<event_count>{int(usage.get('event_count', 0))}</event_count>"
            f"<snapshot_count>{int(usage.get('snapshot_count', 0))}</snapshot_count>"
            "</usage>"
        )
        return (
            "<task-notification>\n"
            f"<task-id>{escape(task_id)}</task-id>\n"
            f"<session-id>{escape(child_session_id)}</session-id>\n"
            f"<agent-key>{escape(agent_key)}</agent-key>\n"
            f"<trace-id>{escape(trace_id)}</trace-id>\n"
            f"<status>{escape(status)}</status>\n"
            f"<summary>{summary_text}</summary>\n"
            f"<result>{result_text}</result>\n"
            f"{usage_block}\n"
            "</task-notification>"
        )

    def _extract_worker_result(self, messages: list[ChatMessage]) -> str:
        for message in reversed(messages):
            if message.role == MessageRole.assistant and message.content.strip():
                return message.content
        for message in reversed(messages):
            if message.role != MessageRole.user and message.content.strip():
                return message.content
        return ""

    async def _build_peer_review_bundle(self, dispatches: list[dict[str, Any]]) -> str:
        bundle_parts: list[str] = []
        for index, record in enumerate(dispatches, start=1):
            child_session_id = str(record.get("child_session_id") or "").strip()
            if not child_session_id:
                continue
            try:
                child_detail = await self._session_service.get_session(child_session_id)
            except Exception:
                continue
            result_text = self._extract_worker_result(child_detail.messages).strip()
            if not result_text:
                continue
            agent_key = str(record.get("agent_key") or "unknown-agent").strip()
            debate_stage = str(record.get("debate_stage") or "independent_findings").strip()
            task_id = str(record.get("task_id") or "").strip()
            trimmed_result = result_text[:6000]
            bundle_parts.append(
                "\n".join(
                    [
                        f"## Peer Reviewer {index}",
                        f"- agent_key: {agent_key}",
                        f"- task_id: {task_id}",
                        f"- debate_stage: {debate_stage}",
                        "",
                        trimmed_result,
                    ]
                )
            )
        return "\n\n".join(bundle_parts).strip() or "No peer findings were captured."

    def _pending_worker_round_index(self, item: dict[str, Any]) -> int:
        context = item.get("context")
        if isinstance(context, dict):
            try:
                return int(context.get("debate_round_index") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    def _pending_worker_source_round_index(self, item: dict[str, Any]) -> int:
        context = item.get("context")
        if isinstance(context, dict):
            try:
                return int(context.get("source_round_index") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    def _pending_worker_source_stage(self, item: dict[str, Any]) -> str:
        context = item.get("context")
        if isinstance(context, dict):
            return str(context.get("source_stage") or "").strip()
        return ""

    def _pending_worker_total_round_count(self, item: dict[str, Any]) -> int:
        context = item.get("context")
        if isinstance(context, dict):
            try:
                return int(context.get("debate_total_round_count") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    def _build_debate_progress_meta(
        self,
        *,
        stage: str,
        status: str,
        current_round_index: int = 0,
        total_round_count: int = 0,
        peer_review_count: int = 0,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": stage,
            "status": status,
            "stage_label": self._debate_stage_label(stage, current_round_index, total_round_count),
            "current_round_index": current_round_index,
            "total_round_count": total_round_count,
            "updated_at": datetime.utcnow().isoformat(),
        }
        if peer_review_count > 0:
            payload["peer_review_count"] = peer_review_count
        return payload

    def _debate_stage_label(self, stage: str, current_round_index: int, total_round_count: int) -> str:
        if stage == "independent_findings":
            return "第1轮立论"
        if stage == "cross_review":
            round_label = current_round_index or 2
            return f"第{round_label}轮攻防"
        if stage == "summary_resolution":
            round_label = current_round_index or total_round_count or 0
            return f"第{round_label}轮裁决" if round_label else "总结裁决"
        if stage == "completed":
            return "辩论完成"
        return "待启动"

    def _is_agent_available(self, agent_key: str) -> bool:
        try:
            self._agent_registry.get(agent_key)
        except KeyError:
            return False
        return True

    def _resolve_worker_mode_key(self, worker: WorkerDispatchSpec) -> str:
        mode_key = str(worker.context.get("mode_key") or "").strip()
        return mode_key or "default"

    def _get_failure_guard(self, session) -> dict[str, Any]:
        guard = session.metadata.get("worker_failure_guard", {})
        return guard if isinstance(guard, dict) else {}

    def _reset_failure_guard(self, session, turn_id: str = "") -> None:
        session.metadata["worker_failure_guard"] = {
            "turn_id": turn_id,
            "count": 0,
            "last_error": "",
            "recent_errors": [],
            "blocked": False,
        }

    def _is_dispatch_blocked(self, session, turn_id: str) -> bool:
        guard = self._get_failure_guard(session)
        return bool(guard.get("blocked")) and str(guard.get("turn_id") or "") == turn_id

    def _update_failure_guard(
        self,
        session,
        parent_turn_id: str,
        status: str,
        worker: WorkerDispatchSpec,
        failure_reason: str,
    ) -> tuple[bool, str]:
        guard = self._get_failure_guard(session)
        if str(guard.get("turn_id") or "") != parent_turn_id:
            guard = {
                "turn_id": parent_turn_id,
                "count": 0,
                "last_error": "",
                "recent_errors": [],
                "blocked": False,
            }

        if status == "failed":
            recent_errors = list(guard.get("recent_errors", []))
            recent_errors.append(
                {
                    "agent_key": worker.agent_key,
                    "description": worker.description,
                    "reason": failure_reason or "unknown_failure",
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )
            guard["count"] = int(guard.get("count", 0)) + 1
            guard["last_error"] = failure_reason or "unknown_failure"
            guard["recent_errors"] = recent_errors[-self._max_consecutive_failures :]
            failure_limit = 2 if (failure_reason or "").startswith("Unknown agent:") else self._max_consecutive_failures
            if int(guard["count"]) >= failure_limit:
                guard["blocked"] = True
                session.metadata["worker_failure_guard"] = guard
                reasons = [
                    f"{index}. {item.get('agent_key', 'worker')}: {item.get('reason', 'unknown_failure')}"
                    for index, item in enumerate(guard["recent_errors"], start=1)
                ]
                message = (
                    "Background execution has been stopped automatically after repeated failures.\n\n"
                    f"Recent failure count: {guard['count']}\n"
                    f"Failure limit: {failure_limit}\n"
                    "Failure reasons:\n"
                    + "\n".join(reasons)
                )
                return True, message
        else:
            guard = {
                "turn_id": parent_turn_id,
                "count": 0,
                "last_error": "",
                "recent_errors": [],
                "blocked": False,
            }

        session.metadata["worker_failure_guard"] = guard
        return False, ""

    def _extract_failure_reason(self, content: str) -> str:
        start_tag = "<result>"
        end_tag = "</result>"
        if start_tag in content and end_tag in content:
            return content.split(start_tag, 1)[1].split(end_tag, 1)[0].strip()
        return "unknown_failure"
