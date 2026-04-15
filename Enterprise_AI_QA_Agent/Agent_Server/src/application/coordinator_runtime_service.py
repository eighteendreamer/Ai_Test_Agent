from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

from src.application.session_service import SessionService
from src.core.config import Settings
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
    ) -> None:
        self._settings = settings
        self._store = store
        self._session_service = session_service
        self._parent_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

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

        workers = self._normalize_workers(payload)
        if not workers:
            raise ValueError("subagent-dispatch requires at least one worker specification.")

        launch_records: list[dict[str, Any]] = []
        for worker in workers[: self._settings.coordinator_max_workers]:
            child_session = await self._session_service.create_session(
                CreateSessionRequest(
                    title=f"Worker: {worker.description}",
                    session_mode=SessionMode.background_task,
                    runtime_mode=RuntimeMode.background,
                    preferred_model=worker.model_key,
                    selected_agent=worker.agent_key,
                    metadata={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "parent_trace_id": parent_trace_id,
                        "task_id": worker.task_id,
                        "worker_description": worker.description,
                        "dispatch_role": "worker",
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

        await self._register_worker_dispatches(parent_session_id, launch_records)

        return {
            "ok": True,
            "trace_id": parent_trace_id,
            "summary": f"Launched {len(launch_records)} worker session(s) for coordinator orchestration.",
            "workers": launch_records,
            "artifacts": [],
            "metrics": {"worker_count": len(launch_records)},
            "error": None,
        }

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
            response = await self._session_service.send_message(
                child_session_id,
                SendMessageRequest(
                    content=worker.prompt,
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
            summary = (
                f'Worker "{worker.description}" finished with status {child_session.status.value}.'
            )
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
        )

    async def _deliver_notification(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
        content: str,
        status: str,
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
            parent_session = await self._store.get_session(parent_session_id)
            if parent_session is None:
                return

            auto_resume = (
                parent_session.session_mode == SessionMode.coordinator
                or (parent_session.selected_agent or "") == "coordinator"
            ) and parent_session.status not in {
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
                },
            )
            parent_session.messages.append(notification_message)
            parent_session.updated_at = datetime.utcnow()
            await self._store.save_session(parent_session)

    async def _register_worker_dispatches(
        self,
        parent_session_id: str,
        launch_records: list[dict[str, Any]],
    ) -> None:
        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return
        worker_dispatches = list(parent_session.metadata.get("worker_dispatches", []))
        worker_dispatches.extend(launch_records)
        parent_session.metadata["worker_dispatches"] = worker_dispatches
        await self._store.save_session(parent_session)

    async def _mark_worker_status(
        self,
        parent_session_id: str,
        task_id: str,
        status: str,
        child_session_id: str,
    ) -> None:
        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return
        worker_dispatches = []
        for record in parent_session.metadata.get("worker_dispatches", []):
            if not isinstance(record, dict):
                continue
            if record.get("task_id") == task_id:
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
                    task_id=str(uuid4()),
                    description=description,
                    prompt=prompt,
                    agent_key=agent_key,
                    model_key=str(item.get("model_key") or "").strip() or None,
                    skill_keys=skill_keys,
                    context=dict(item.get("context", {})) if isinstance(item.get("context"), dict) else {},
                )
            )
        return workers

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
