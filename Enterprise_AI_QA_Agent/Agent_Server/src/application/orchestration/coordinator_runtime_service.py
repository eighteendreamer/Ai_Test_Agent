from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5
from xml.sax.saxutils import escape

from src.application.observability import LangSmithObservabilityAdapter
from src.application.sessions.session_service import SessionService
from src.core.agent_communication import AgentMessageBus, ChildSessionWatcher
from src.core.agent_control import AgentControlService
from src.core.config import Settings
from src.core.request_context import get_request_context, new_id
from src.core.worker_pool import WorkerPool
from src.domain.models import SessionRecord
from src.registry.agents import AgentRegistry
from src.runtime.session_lock_map import SessionLockMap
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

logger = logging.getLogger(__name__)


@dataclass
class WorkerDispatchSpec:
    task_id: str
    description: str
    prompt: str
    agent_key: str
    model_key: str | None = None
    skill_keys: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


# A settled worker must never be re-settled by a duplicate child completion.
TERMINAL_WORKER_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
# Launch-time registration may not rewind an already projected outcome; a
# recovery_required record still accepts a genuine later settlement.
NON_REWINDABLE_WORKER_STATUSES = TERMINAL_WORKER_STATUSES | {"recovery_required"}
ACTIVE_WORKER_STATUSES = frozenset(
    {"running", "waiting_approval", "idle", "pending", "launching", "recovery_required", ""}
)


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
        self._parent_locks = SessionLockMap()
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._watch_tasks: dict[str, asyncio.Task[None]] = {}
        self._max_consecutive_failures = 3
        self._agent_control = AgentControlService(
            max_concurrent=settings.orchestration.coordinator_max_workers,
        )
        self._worker_pool = WorkerPool()
        self._message_bus = AgentMessageBus()
        self._watchers: dict[str, ChildSessionWatcher] = {}
        self._observability_service: LangSmithObservabilityAdapter | None = None
        self._agent_task_dispatcher = None
        self._agent_task_store = None
        self._owner_id = f"coordinator:{uuid4()}"
        self._dispatch_reaper_interval_seconds = float(
            settings.orchestration.turn_reaper_interval_seconds
        )
        self._dispatch_reaper_task: asyncio.Task[None] | None = None
        self._dispatch_reaper_stop: asyncio.Event | None = None

    async def start_dispatch_reaper(self) -> None:
        """Periodically reconcile claims after the session turn reaper settles turns."""
        if self._dispatch_reaper_task is not None and not self._dispatch_reaper_task.done():
            return
        self._dispatch_reaper_stop = asyncio.Event()
        self._dispatch_reaper_task = asyncio.create_task(
            self._dispatch_reaper_loop(),
            name="coordinator-dispatch-reaper",
        )
        logger.info(
            "coordinator_dispatch_reaper_started",
            extra={"interval_seconds": self._dispatch_reaper_interval_seconds},
        )

    async def stop_dispatch_reaper(self) -> None:
        """Stop the dispatch reconciler and wait for its current pass to finish."""
        task = self._dispatch_reaper_task
        stop = self._dispatch_reaper_stop
        self._dispatch_reaper_task = None
        self._dispatch_reaper_stop = None
        if task is None:
            return
        if stop is not None:
            stop.set()
        await task
        logger.info("coordinator_dispatch_reaper_stopped")

    async def stop_child_watchers(self) -> None:
        """Stop approval watchers before their session store is closed."""
        tasks = list(set(self._watch_tasks.values()))
        self._watch_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("coordinator_child_watchers_stopped", extra={"watcher_count": len(tasks)})

    async def _dispatch_reaper_loop(self) -> None:
        stop = self._dispatch_reaper_stop
        if stop is None:
            return
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self._dispatch_reaper_interval_seconds
                )
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                await self.recover_orphaned_dispatches()
            except Exception:
                logger.exception("coordinator_dispatch_reaper_pass_failed")

    def set_observability_service(self, service: LangSmithObservabilityAdapter | None) -> None:
        self._observability_service = service

    def set_agent_task_dispatcher(self, dispatcher) -> None:
        """Inject the durable task dispatcher; built after the dispatcher itself."""
        self._agent_task_dispatcher = dispatcher

    def set_agent_task_store(self, store) -> None:
        """Inject the Agent task store used to resolve parent/child task rows."""
        self._agent_task_store = store

    def _on_worker_task_done(self, session_id: str, task_id: str) -> None:
        watcher = self._watchers.pop(session_id, None)
        if watcher and not watcher.is_settled:
            watcher.notify_completed()
        self._worker_pool.complete_worker(task_id, {})

    async def cancel_workers(
        self,
        *,
        task_ids: list[str],
        child_session_ids: list[str],
        reason: str,
    ) -> None:
        """Cancel coordinator-owned coroutines and settle their child sessions."""
        watch_tasks = [
            task
            for child_session_id in child_session_ids
            for task in [self._watch_tasks.pop(child_session_id, None)]
            if task is not None
        ]
        for task in watch_tasks:
            task.cancel()
        if watch_tasks:
            await asyncio.gather(*watch_tasks, return_exceptions=True)
            logger.info(
                "coordinator_child_watchers_cancelled",
                extra={"child_session_ids": child_session_ids, "reason": reason},
            )
        await self._cancel_durable_children(child_session_ids, reason=reason)
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

    async def _cancel_durable_children(self, child_session_ids: list[str], *, reason: str) -> None:
        """Cancel not-yet-started durable children before the in-process teardown."""
        if self._agent_task_store is None or not child_session_ids:
            return
        for child_session_id in child_session_ids:
            try:
                child = await self._store.get_session(child_session_id)
            except Exception as exc:
                logger.warning(
                    "coordinator_child_parent_context_lookup_failed",
                    extra={
                        "child_session_id": child_session_id,
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            metadata = child.metadata if child is not None and isinstance(child.metadata, dict) else {}
            parent_session_id = str(metadata.get("parent_session_id") or "")
            parent_turn_id = str(metadata.get("parent_turn_id") or "")
            if not parent_session_id or not parent_turn_id:
                continue
            await self.cancel_parent_children(parent_session_id, parent_turn_id, reason=reason)
            return

    async def cancel_parent_children(self, parent_session_id: str, parent_turn_id: str, *, reason: str = "") -> int:
        """Cancel queued coordinator children owned by one parent turn."""
        if self._agent_task_store is None or not parent_session_id or not parent_turn_id:
            return 0
        parent_task = await self._resolve_parent_task(parent_session_id, parent_turn_id)
        if parent_task is None:
            return 0
        cancelled = await self._agent_task_store.cancel_unstarted_children(parent_task.id)
        logger.info(
            "coordinator_children_cancel_requested",
            extra={
                "parent_task_id": parent_task.id,
                "parent_session_id": parent_session_id,
                "parent_turn_id": parent_turn_id,
                "cancelled": cancelled,
                "reason": reason or "parent_cancelled",
            },
        )
        return cancelled

    async def recover_orphaned_dispatches(self) -> int:
        """Reconcile persisted worker receipts after a coordinator restart.

        Child execution ownership is already governed by the normal SessionStore
        turn lease. This scan records materialized children whose parent receipt
        was not saved, but never replays a child with unknown external side effects.
        """
        recovered = 0
        sessions = await self._store.list_sessions(limit=None)
        children_by_dispatch: dict[tuple[str, str, str], list[SessionRecord]] = defaultdict(list)
        children_by_parent_turn_task: dict[tuple[str, str, str], list[SessionRecord]] = defaultdict(list)
        for child in sessions:
            metadata = child.metadata if isinstance(child.metadata, dict) else {}
            parent_session_id = str(metadata.get("parent_session_id") or "").strip()
            parent_turn_id = str(metadata.get("parent_turn_id") or "").strip()
            dispatch_key = str(metadata.get("coordinator_dispatch_key") or "").strip()
            task_id = str(metadata.get("task_id") or "").strip()
            if parent_session_id and parent_turn_id and task_id:
                children_by_parent_turn_task[(parent_session_id, parent_turn_id, task_id)].append(child)
            if parent_session_id and parent_turn_id and dispatch_key:
                children_by_dispatch[(parent_session_id, parent_turn_id, dispatch_key)].append(child)

        for parent in sessions:
            records = parent.metadata.get("worker_dispatches", [])
            if not isinstance(records, list):
                records = []
            claims = parent.metadata.get("coordinator_dispatch_claims", [])
            if not isinstance(claims, list):
                claims = []
            can_reconcile_missing_receipts = self._parent_turn_is_settled(parent)
            changed = False
            reconciled: list[dict[str, Any]] = []
            known_worker_keys: set[tuple[str, str]] = set()
            known_unscoped_task_ids: set[str] = set()
            recovered_for_parent = 0
            for record in records:
                if not isinstance(record, dict):
                    reconciled.append(record)
                    continue
                task_id = str(record.get("task_id") or "").strip()
                record_turn_id = str(record.get("parent_turn_id") or "").strip()
                if task_id:
                    if record_turn_id:
                        known_worker_keys.add((record_turn_id, task_id))
                    else:
                        known_unscoped_task_ids.add(task_id)
                record_status = str(record.get("status") or "")
                if record_status not in {"running", "recovery_required"}:
                    reconciled.append(record)
                    continue
                child_id = str(record.get("child_session_id") or "").strip()
                if not child_id:
                    reconciled.append(record)
                    continue
                child = await self._store.get_session(child_id)
                if child is None:
                    if record_status == "recovery_required":
                        reconciled.append(record)
                        continue
                    reconciled.append({
                        **record,
                        "status": "recovery_required",
                        "recovery_reason": "child_session_missing",
                        "recovered_at": datetime.utcnow().isoformat(),
                    })
                    changed = True
                    recovered += 1
                    recovered_for_parent += 1
                    continue
                child_status = child.status.value
                if child_status in {SessionStatus.completed.value, SessionStatus.failed.value}:
                    recovered_record = {
                        **record,
                        "status": child_status,
                        "completed_at": child.updated_at.isoformat(),
                        "recovered_at": datetime.utcnow().isoformat(),
                    }
                    if record_status == "recovery_required" and not self._has_fingerprinted_child_claim(
                        claims,
                        parent_turn_id=(record_turn_id or str(
                            (child.metadata or {}).get("parent_turn_id") or ""
                        ).strip()),
                        task_id=task_id,
                        child_dispatch_key=str(
                            (child.metadata or {}).get("coordinator_dispatch_key") or ""
                        ).strip(),
                    ):
                        reconciled.append(record)
                        continue
                    recovered_record.pop("recovery_reason", None)
                    recovered_record.pop("child_session_status", None)
                    reconciled.append(recovered_record)
                    changed = True
                    recovered += 1
                    recovered_for_parent += 1
                elif child_status == SessionStatus.interrupted.value:
                    if record_status == "recovery_required":
                        reconciled.append(record)
                    else:
                        reconciled.append({
                            **record,
                            "status": "recovery_required",
                            "recovery_reason": "child_session_interrupted_without_safe_replay",
                            "recovered_at": datetime.utcnow().isoformat(),
                        })
                        changed = True
                        recovered += 1
                        recovered_for_parent += 1
                else:
                    reconciled.append(record)

            if can_reconcile_missing_receipts:
                for claim in claims:
                    if not isinstance(claim, dict):
                        continue
                    parent_turn_id = str(claim.get("turn_id") or "").strip()
                    dispatch_key = str(claim.get("dispatch_key") or "").strip()
                    request_fingerprint = str(claim.get("request_fingerprint") or "").strip()
                    worker_task_ids = [
                        str(item).strip()
                        for item in claim.get("worker_task_ids", [])
                        if str(item).strip()
                    ] if isinstance(claim.get("worker_task_ids"), list) else []
                    if not parent_turn_id or not dispatch_key:
                        continue
                    pending_task_ids = self._pending_worker_task_ids(
                        parent.metadata, parent_turn_id
                    )
                    if worker_task_ids:
                        claim_children: list[SessionRecord] = []
                        ambiguous_task_ids: set[str] = set()
                        for task_id in worker_task_ids:
                            candidates = children_by_parent_turn_task.get(
                                (parent.id, parent_turn_id, task_id), []
                            )
                            matching = [
                                child
                                for child in candidates
                                if not str(
                                    (child.metadata or {}).get("coordinator_dispatch_key") or ""
                                ).strip()
                                or str(
                                    (child.metadata or {}).get("coordinator_dispatch_key") or ""
                                ).strip() == dispatch_key
                            ]
                            if candidates and not matching:
                                ambiguous_task_ids.add(task_id)
                            claim_children.extend(matching)
                    else:
                        claim_children = list(
                            children_by_dispatch.get(
                                (parent.id, parent_turn_id, dispatch_key), []
                            )
                        )
                        ambiguous_task_ids = set()

                    if worker_task_ids and not claim_children:
                        claim_has_receipt = any(
                            (parent_turn_id, task_id) in known_worker_keys
                            or task_id in known_unscoped_task_ids
                            for task_id in worker_task_ids
                        )
                        claim_has_pending = any(
                            task_id in pending_task_ids for task_id in worker_task_ids
                        )
                        if (
                            request_fingerprint
                            and not claim_has_receipt
                            and not claim_has_pending
                            and not ambiguous_task_ids
                        ):
                            released = await self._store.release_coordinator_dispatch_claim_if_no_child(
                                parent.id,
                                parent_turn_id,
                                dispatch_key,
                                str(claim.get("owner_id") or ""),
                                request_fingerprint,
                                worker_task_ids,
                                require_settled_parent=True,
                            )
                            if released:
                                changed = True
                                recovered += 1
                                recovered_for_parent += 1
                                logger.warning(
                                    "coordinator_dispatch_unmaterialized_claim_released_after_restart",
                                    extra={
                                        "parent_session_id": parent.id,
                                        "parent_turn_id": parent_turn_id,
                                        "dispatch_key": dispatch_key,
                                        "worker_task_count": len(worker_task_ids),
                                    },
                                )
                                continue

                    materialized_task_ids: set[str] = set()
                    for child in claim_children:
                        child_metadata = (
                            child.metadata if isinstance(child.metadata, dict) else {}
                        )
                        task_id = str(child_metadata.get("task_id") or "").strip()
                        if task_id:
                            materialized_task_ids.add(task_id)
                        if not task_id or (parent_turn_id, task_id) in known_worker_keys:
                            continue
                        if task_id in known_unscoped_task_ids:
                            logger.warning(
                                "coordinator_dispatch_child_receipt_task_id_conflict",
                                extra={
                                    "parent_session_id": parent.id,
                                    "parent_turn_id": parent_turn_id,
                                    "dispatch_key": dispatch_key,
                                    "task_id": task_id,
                                },
                            )
                            continue
                        recovery_record = {
                            "task_id": task_id,
                            "child_session_id": child.id,
                            "agent_key": str(child.selected_agent or ""),
                            "model_key": str(child.preferred_model or ""),
                            "description": str(
                                child_metadata.get("worker_description") or child.title
                            ),
                            "dispatch_role": str(child_metadata.get("dispatch_role") or "worker"),
                            "status": (
                                child.status.value
                                if request_fingerprint
                                and child.status.value in {
                                    SessionStatus.completed.value,
                                    SessionStatus.failed.value,
                                }
                                else "recovery_required"
                            ),
                            "parent_turn_id": parent_turn_id,
                            "recovered_at": datetime.utcnow().isoformat(),
                        }
                        if recovery_record["status"] in {
                            SessionStatus.completed.value,
                            SessionStatus.failed.value,
                        }:
                            recovery_record["completed_at"] = child.updated_at.isoformat()
                        else:
                            recovery_record["child_session_status"] = child.status.value
                            recovery_record["recovery_reason"] = (
                                "child_session_persisted_before_parent_receipt"
                            )
                        reconciled.append(recovery_record)
                        known_worker_keys.add((parent_turn_id, task_id))
                        changed = True
                        recovered += 1
                        recovered_for_parent += 1
                        logger.warning(
                            "coordinator_dispatch_child_receipt_reconstructed",
                            extra={
                                "parent_session_id": parent.id,
                                "parent_turn_id": parent_turn_id,
                                "dispatch_key": dispatch_key,
                                "task_id": task_id,
                                "child_session_id": child.id,
                                "child_session_status": child.status.value,
                                "request_fingerprint_available": bool(request_fingerprint),
                                "receipt_status": recovery_record["status"],
                            },
                        )

                    for task_id in worker_task_ids:
                        if (
                            not task_id
                            or task_id in materialized_task_ids
                            or task_id in pending_task_ids
                            or (parent_turn_id, task_id) in known_worker_keys
                            or task_id in ambiguous_task_ids
                            or task_id in known_unscoped_task_ids
                        ):
                            continue
                        reconciled.append(
                            {
                                "task_id": task_id,
                                "child_session_id": "",
                                "status": "recovery_required",
                                "parent_turn_id": parent_turn_id,
                                "recovery_reason": "child_session_missing_after_partial_dispatch",
                                "recovered_at": datetime.utcnow().isoformat(),
                            }
                        )
                        known_worker_keys.add((parent_turn_id, task_id))
                        changed = True
                        recovered += 1
                        recovered_for_parent += 1
            if changed:
                await self._reconcile_worker_dispatches(parent, reconciled)
                await self._store.append_event(
                    parent.id,
                    ExecutionEvent(
                        type="worker.dispatches_reconciled",
                        session_id=parent.id,
                        timestamp=datetime.utcnow(),
                        payload={"recovered_count": recovered_for_parent},
                    ),
                )
        if recovered:
            logger.warning("coordinator_orphaned_dispatches_reconciled", extra={"recovered_count": recovered})
        return recovered

    @staticmethod
    def _has_fingerprinted_child_claim(
        claims: list[Any],
        *,
        parent_turn_id: str,
        task_id: str,
        child_dispatch_key: str,
    ) -> bool:
        """Return true only when one persisted claim identifies this child.

        A terminal child can safely settle a previously unresolved receipt only
        when the parent has a fingerprinted claim for the same task and turn.
        Older claims without enough identity remain recovery_required.
        """
        if not parent_turn_id or not task_id:
            return False
        candidates = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            if str(claim.get("turn_id") or "").strip() != parent_turn_id:
                continue
            if not str(claim.get("request_fingerprint") or "").strip():
                continue
            dispatch_key = str(claim.get("dispatch_key") or "").strip()
            worker_task_ids = claim.get("worker_task_ids")
            claimed_task_ids = (
                {str(item).strip() for item in worker_task_ids}
                if isinstance(worker_task_ids, list)
                else set()
            )
            if claimed_task_ids and task_id not in claimed_task_ids:
                continue
            if child_dispatch_key:
                if dispatch_key != child_dispatch_key:
                    continue
            else:
                if not claimed_task_ids or task_id not in claimed_task_ids:
                    continue
            candidates.append(claim)
        return len(candidates) == 1

    @staticmethod
    def _parent_turn_is_settled(parent: SessionRecord) -> bool:
        status = getattr(parent.status, "value", str(parent.status))
        if status not in {
            SessionStatus.interrupted.value,
            SessionStatus.completed.value,
            SessionStatus.failed.value,
        }:
            return False
        metadata = parent.metadata if isinstance(parent.metadata, dict) else {}
        return not str(metadata.get("turn_lease_token") or "").strip()

    @staticmethod
    def _pending_worker_task_ids(
        metadata: dict[str, Any], parent_turn_id: str
    ) -> set[str]:
        task_ids: set[str] = set()
        queues = (
            ("pending_initial_workers", "pending_initial_workers_parent_turn_id"),
            ("pending_followup_workers", "followup_workers_parent_turn_id"),
        )
        for key, turn_key in queues:
            queued_turn_id = str(metadata.get(turn_key) or "").strip()
            if queued_turn_id and queued_turn_id != parent_turn_id:
                continue
            pending = metadata.get(key)
            if not isinstance(pending, list):
                continue
            task_ids.update(
                str(item.get("task_id") or "").strip()
                for item in pending
                if isinstance(item, dict) and str(item.get("task_id") or "").strip()
            )
        completion = metadata.get("pending_completion_worker")
        if isinstance(completion, dict):
            completion_turn_id = str(completion.get("parent_turn_id") or "").strip()
            task_id = str(completion.get("task_id") or "").strip()
            if task_id and (not completion_turn_id or completion_turn_id == parent_turn_id):
                task_ids.add(task_id)
        return task_ids

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

        parent_depth = int(parent_session.metadata.get("agent_depth") or 0)
        if parent_depth >= self._agent_control.MAX_DEPTH:
            return {
                "ok": False,
                "trace_id": parent_trace_id,
                "summary": f"Agent 嵌套深度 {parent_depth} 已达上限 {self._agent_control.MAX_DEPTH}，拒绝创建子 Agent。",
                "workers": [],
                "artifacts": [],
                "metrics": {"worker_count": 0, "depth": parent_depth, "max_depth": self._agent_control.MAX_DEPTH},
                "error": "agent_depth_exceeded",
            }

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

        if self._settings.orchestration.coordinator_max_workers < 1:
            raise ValueError("coordinator_max_workers must be at least 1")
        workers = self._normalize_workers(payload, parent_session_id, parent_turn_id)
        if not workers:
            raise ValueError("subagent-dispatch requires at least one worker specification.")
        followup_workers = self._normalize_followup_workers(payload, parent_session_id, parent_turn_id)
        completion_worker = self._normalize_completion_worker(payload, parent_session_id, parent_turn_id)
        dispatch_key = "|".join(
            [
                *(sorted(worker.task_id for worker in workers)),
                *(sorted(worker.task_id for worker in followup_workers)),
                f"completion:{completion_worker.task_id}" if completion_worker else "",
            ]
        )
        request_fingerprint = self._dispatch_request_fingerprint(
            workers, followup_workers, completion_worker
        )
        dispatch_worker_task_ids = [
            worker.task_id
            for worker in [
                *workers,
                *followup_workers,
                *([completion_worker] if completion_worker is not None else []),
            ]
        ]
        claimed = await self._store.claim_coordinator_dispatch(
            parent_session_id,
            parent_turn_id,
            dispatch_key,
            self._owner_id,
            request_fingerprint,
            dispatch_worker_task_ids,
        )
        if not claimed:
            return await self._duplicate_dispatch_receipt(
                parent_session_id,
                parent_turn_id,
                parent_trace_id,
                dispatch_key,
                request_fingerprint,
                workers,
            )

        launch_records: list[dict[str, Any]] = []
        immediate_failures: list[tuple[WorkerDispatchSpec, str]] = []
        pending_launches: list[tuple[WorkerDispatchSpec, dict[str, Any], str]] = []
        max_workers = self._settings.orchestration.coordinator_max_workers
        deferred_workers = workers[max_workers:]
        if deferred_workers:
            await self._register_initial_worker_queue(
                parent_session_id, parent_turn_id, parent_trace_id, deferred_workers
            )
            logger.info(
                "coordinator_initial_workers_queued",
                extra={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "queued_count": len(deferred_workers),
                    "max_concurrent": max_workers,
                },
            )

        for worker in workers[:max_workers]:
            if not self._is_agent_available(worker.agent_key):
                launch_records.append(
                    {
                        "task_id": worker.task_id,
                        "child_session_id": "",
                        "agent_key": worker.agent_key,
                        "model_key": worker.model_key or "",
                        "description": worker.description,
                        "status": "failed",
                        "error": f"Unknown agent: {worker.agent_key}",
                    }
                )
                immediate_failures.append((worker, f"Unknown agent: {worker.agent_key}"))
                continue

            dispatch_role = str(worker.context.get("dispatch_role") or "worker").strip() or "worker"
            mode_key = self._resolve_worker_mode_key(worker)
            try:
                child_session = await self._session_service.create_internal_session(
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
                            "coordinator_dispatch_key": dispatch_key,
                            "task_id": worker.task_id,
                            "worker_description": worker.description,
                            "dispatch_role": dispatch_role,
                            "notification_mode": "task-notification",
                            "agent_depth": parent_depth + 1,
                            **self._trusted_worker_metadata(parent_session, mode_key),
                        },
                    )
                )
            except Exception as exc:
                await self._release_dispatch_claim_after_child_create_failure(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    dispatch_key=dispatch_key,
                    request_fingerprint=request_fingerprint,
                    worker_task_ids=dispatch_worker_task_ids,
                    error=exc,
                )
                raise

            launch_record = self._initial_worker_record(
                worker, status="running", child_session_id=child_session.id
            )
            launch_records.append(launch_record)
            pending_launches.append((worker, launch_record, child_session.id))

        # Register before launching: another process' Worker can settle a durable
        # child faster than this turn reaches its registration call, and the
        # parent metadata is written as a whole document.
        queued_records = [
            self._initial_worker_record(worker, status="queued")
            for worker in deferred_workers
        ]
        await self._register_worker_dispatches(
            parent_session_id,
            parent_turn_id,
            [
                {**record, "status": "launching"}
                if record.get("status") == "failed" else record
                for record in launch_records
            ] + queued_records,
        )
        for worker, launch_record, child_session_id in pending_launches:
            try:
                child_task_id = await self._start_child_worker(
                    parent_session=parent_session,
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=child_session_id,
                    worker=worker,
                    mode_key=self._resolve_worker_mode_key(worker),
                    register_pool=True,
                )
            except ValueError as exc:
                launch_record["status"] = "failed"
                launch_record["error"] = str(exc)
                immediate_failures.append((worker, f"Coordinator child dispatch rejected: {exc}"))
                continue
            if child_task_id:
                launch_record["child_task_id"] = child_task_id
        if pending_launches:
            await self._register_worker_dispatches(
                parent_session_id,
                parent_turn_id,
                [
                    {**record, "status": "launching"}
                    if record.get("status") == "failed" else record
                    for _worker, record, _child in pending_launches
                ],
            )
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

        latest_parent = await self._store.get_session(parent_session_id)
        latest_records = {
            str(item.get("task_id") or ""): item
            for item in (latest_parent.metadata.get("worker_dispatches", []) if latest_parent else [])
            if isinstance(item, dict)
            and str(item.get("parent_turn_id") or "") == parent_turn_id
        }
        worker_records = [
            latest_records.get(worker.task_id, record)
            for worker, record in zip(workers, launch_records + queued_records)
        ]
        successful_launches = [
            item for item in worker_records
            if str(item.get("status") or "") in {"running", "completed", "waiting_approval", "idle"}
        ]
        queued_launches = [
            item for item in worker_records if str(item.get("status") or "") == "queued"
        ]
        failed_launches = [
            item for item in worker_records if str(item.get("status") or "") == "failed"
        ]
        unresolved_launches = [
            item for item in worker_records
            if str(item.get("status") or "") in {"launching", "recovery_required"}
        ]
        immediate_failure_errors = [failure_reason for _, failure_reason in immediate_failures]
        dispatch_status = (
            "failed"
            if not successful_launches and not queued_launches
            else "partial"
            if failed_launches or unresolved_launches
            else "completed"
        )
        return {
            "ok": bool(successful_launches or queued_launches),
            "status": dispatch_status,
            "trace_id": parent_trace_id,
            "summary": (
                f"Launched {len(successful_launches)} worker session(s); "
                f"queued {len(queued_launches)} worker(s); "
                f"failed {len(failed_launches)} and unresolved {len(unresolved_launches)}."
            ),
            "workers": worker_records,
            "artifacts": [],
            "metrics": {
                "worker_count": len(successful_launches),
                "queued_worker_count": len(queued_launches),
                "failed_worker_count": len(failed_launches),
                "unresolved_worker_count": len(unresolved_launches),
            },
            "error": "; ".join(immediate_failure_errors) or None,
        }

    async def _release_dispatch_claim_after_child_create_failure(
        self,
        *,
        parent_session_id: str,
        parent_turn_id: str,
        dispatch_key: str,
        request_fingerprint: str,
        worker_task_ids: list[str],
        error: Exception,
    ) -> None:
        try:
            released = await self._store.release_coordinator_dispatch_claim_if_no_child(
                parent_session_id,
                parent_turn_id,
                dispatch_key,
                self._owner_id,
                request_fingerprint,
                worker_task_ids,
            )
        except Exception:
            logger.exception(
                "coordinator_dispatch_claim_release_check_failed",
                extra={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "dispatch_key": dispatch_key,
                    "failure_type": type(error).__name__,
                },
            )
            return
        if released:
            logger.warning(
                "coordinator_dispatch_claim_released_after_child_create_failure",
                extra={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "dispatch_key": dispatch_key,
                    "failure_type": type(error).__name__,
                },
            )
            return
        logger.warning(
            "coordinator_dispatch_claim_retained_after_child_create_failure",
            extra={
                "parent_session_id": parent_session_id,
                "parent_turn_id": parent_turn_id,
                "dispatch_key": dispatch_key,
                "failure_type": type(error).__name__,
            },
        )

    async def _duplicate_dispatch_receipt(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        dispatch_key: str,
        request_fingerprint: str,
        workers: list[WorkerDispatchSpec],
    ) -> dict[str, Any]:
        requested_ids = [worker.task_id for worker in workers]
        worker_records: list[dict[str, Any]] = []
        missing_ids = requested_ids
        # The winning caller may still be creating child sessions. Wait briefly
        # for its persisted receipt; an unresolved claim is never safe to replay.
        for attempt in range(21):
            parent = await self._store.get_session(parent_session_id)
            if parent is None:
                raise KeyError(f"Parent session not found: {parent_session_id}")
            claims = parent.metadata.get("coordinator_dispatch_claims", [])
            matching_claim = (
                next(
                    (
                        claim
                        for claim in claims
                        if isinstance(claim, dict)
                        and str(claim.get("turn_id") or "") == parent_turn_id
                        and str(claim.get("dispatch_key") or "") == dispatch_key
                    ),
                    None,
                )
                if isinstance(claims, list)
                else None
            )
            stored_fingerprint = (
                str(matching_claim.get("request_fingerprint") or "")
                if isinstance(matching_claim, dict)
                else ""
            )
            if not stored_fingerprint:
                logger.warning(
                    "coordinator_dispatch_claim_fingerprint_missing",
                    extra={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "dispatch_key": dispatch_key,
                    },
                )
                return {
                    "ok": False,
                    "status": "recovery_required",
                    "trace_id": parent_trace_id,
                    "summary": (
                        "Coordinator dispatch claim predates request fingerprints; "
                        "reconcile before retry."
                    ),
                    "workers": [],
                    "artifacts": [],
                    "metrics": {
                        "worker_count": 0,
                        "duplicate": True,
                    },
                    "error": "coordinator_dispatch_claim_unresolved",
                }
            if stored_fingerprint != request_fingerprint:
                logger.warning(
                    "coordinator_dispatch_identity_conflict",
                    extra={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "dispatch_key": dispatch_key,
                    },
                )
                return {
                    "ok": False,
                    "status": "conflict",
                    "trace_id": parent_trace_id,
                    "summary": (
                        "Coordinator dispatch identity was reused with different "
                        "worker content."
                    ),
                    "workers": [],
                    "artifacts": [],
                    "metrics": {
                        "worker_count": 0,
                        "duplicate": True,
                        "identity_conflict": True,
                    },
                    "error": "coordinator_dispatch_identity_conflict",
                }
            records = {
                str(item.get("task_id") or ""): item
                for item in parent.metadata.get("worker_dispatches", [])
                if isinstance(item, dict)
                and str(item.get("parent_turn_id") or "") == parent_turn_id
            }
            worker_records = [records[task_id] for task_id in requested_ids if task_id in records]
            missing_ids = [task_id for task_id in requested_ids if task_id not in records]
            if not missing_ids:
                break
            if attempt < 20:
                await asyncio.sleep(0.05)

        unresolved = [
            record for record in worker_records
            if str(record.get("status") or "") in {"", "launching", "recovery_required"}
            or (
                str(record.get("status") or "") in {"running", "waiting_approval", "idle"}
                and not str(record.get("child_session_id") or "")
            )
        ]
        if missing_ids or unresolved:
            logger.warning(
                "coordinator_dispatch_claim_unresolved",
                extra={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "missing_task_ids": missing_ids,
                    "unresolved_task_ids": [str(record.get("task_id") or "") for record in unresolved],
                },
            )
            return {
                "ok": False,
                "status": "recovery_required",
                "trace_id": parent_trace_id,
                "summary": "Coordinator dispatch claim has no complete worker receipt; reconcile before retry.",
                "workers": worker_records,
                "artifacts": [],
                "metrics": {"worker_count": len(worker_records), "duplicate": True,
                            "missing_worker_count": len(missing_ids)},
                "error": "coordinator_dispatch_claim_unresolved",
            }

        accepted = [
            record for record in worker_records
            if str(record.get("status") or "") not in TERMINAL_WORKER_STATUSES - {"completed"}
        ]
        return {
            "ok": bool(accepted),
            "status": "duplicate",
            "trace_id": parent_trace_id,
            "summary": "Coordinator dispatch was already recorded for this parent turn.",
            "workers": worker_records,
            "artifacts": [],
            "metrics": {"worker_count": len(accepted), "duplicate": True},
            "error": None if accepted else "coordinator_dispatch_failed",
        }

    def _durable_children_enabled(self) -> bool:
        return bool(
            self._settings.orchestration.coordinator_durable_children
            and self._agent_task_dispatcher is not None
        )

    async def _resolve_parent_task(self, parent_session_id: str, parent_turn_id: str):
        """Find the durable task owning the parent turn that is dispatching now.

        No upstream counterpart (checked: ToolExecutionContext fields, security
        mode `_build_dispatch_context`, coordinator `dispatch` callers): the tool
        dispatch context never carries a durable task id, so the running task is
        resolved from the pending Agent task rows of the parent session.
        """
        if self._agent_task_store is None or not parent_session_id or not parent_turn_id:
            return None
        try:
            pending = await self._agent_task_store.list_session(parent_session_id, pending_only=True)
        except Exception as exc:
            logger.warning(
                "coordinator_parent_task_lookup_failed",
                extra={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "error_type": type(exc).__name__,
                },
            )
            return None
        for record in pending:
            if record.turn_id != parent_turn_id:
                continue
            if record.operation not in {"message", "coordinator_resume"}:
                continue
            if record.status not in {"claimed", "running"}:
                continue
            return record
        return None

    async def _start_child_worker(
        self,
        *,
        parent_session,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
        mode_key: str,
        register_pool: bool = True,
    ) -> str | None:
        """Launch one worker durably when the feature flag is on, else in-process.

        Returns the durable child task id, or None for the in-process path.
        Raises ValueError when the store rejects the assignment (depth, fan-out
        or cycle guard) so the caller can settle the dispatch record.
        """
        parent_task = await self._resolve_parent_task(parent_session_id, parent_turn_id)
        if self._durable_children_enabled() and parent_task is not None:
            context = get_request_context()
            receipt = await self._agent_task_dispatcher.submit_coordinator_child(
                parent_task_id=parent_task.id,
                root_trace_id=parent_task.root_trace_id or parent_trace_id,
                child_session_id=child_session_id,
                turn_id=str(uuid4()),
                project_id=getattr(parent_session, "project_id", None),
                request_id=context.request_id if context else new_id("req"),
                trace_id=parent_trace_id or (context.trace_id if context else "") or new_id("trace"),
                run_id=context.run_id if context else None,
                worker=worker,
                mode_key=mode_key,
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
            )
            logger.info(
                "coordinator_child_dispatched_durable",
                extra={
                    "parent_task_id": parent_task.id,
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "child_task_id": receipt.id,
                    "child_session_id": child_session_id,
                    "task_id": worker.task_id,
                },
            )
            return receipt.id
        if self._settings.orchestration.coordinator_durable_children and parent_task is None:
            # The parent turn is not running under a durable Agent task (for
            # example the worker pipeline is disabled). Falling back keeps the
            # dispatch observable instead of silently dropping the worker.
            logger.warning(
                "coordinator_durable_child_parent_task_missing",
                extra={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "task_id": worker.task_id,
                    "reason": "in_process_fallback",
                },
            )
        task = asyncio.create_task(
            self._run_child_session(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                parent_trace_id=parent_trace_id,
                child_session_id=child_session_id,
                worker=worker,
            )
        )
        self._active_tasks[worker.task_id] = task
        task.add_done_callback(
            lambda _finished, task_id=worker.task_id: self._active_tasks.pop(task_id, None)
        )
        if register_pool:
            self._worker_pool.register_worker(
                worker.task_id,
                child_session_id,
                worker.agent_key,
            )
            self._watchers[child_session_id] = ChildSessionWatcher(child_session_id)
            task.add_done_callback(
                lambda _finished, sid=child_session_id, tid=worker.task_id: self._on_worker_task_done(sid, tid)
            )
        return None

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
            trace_context = None
            observability_service = getattr(self, "_observability_service", None)
            if observability_service is not None and parent_trace_id and parent_turn_id:
                trace_context = observability_service.context_from_mapping(
                    {
                        "session_id": child_session_id,
                        "turn_id": parent_turn_id,
                        "trace_id": parent_trace_id,
                        "parent_trace_id": parent_trace_id,
                        "mode_key": mode_key,
                        "agent_key": worker.agent_key,
                    }
                )
            trace_manager = (
                observability_service.trace_node(
                    trace_context,
                    node_name=f"worker.{worker.agent_key}",
                    inputs={
                        "task_id": worker.task_id,
                        "child_session_id": child_session_id,
                        "agent_key": worker.agent_key,
                    },
                )
                if trace_context is not None
                else nullcontext()
            )
            with trace_manager:
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
            self._start_child_watch(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                parent_trace_id=parent_trace_id,
                child_session_id=child_session_id,
                worker=worker,
            )

    async def _project_worker_notification(
        self,
        *,
        parent_session_id: str,
        parent_turn_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
        content: str,
        status: str,
        failure_reason: str = "",
    ) -> tuple[Any, bool] | None:
        """Apply the parent-side projection of one child notification.

        The caller must hold ``self._parent_locks[parent_session_id]``. Returns
        ``(parent_session, auto_stopped)`` or None when the parent vanished.
        """
        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return None

        def claim_projection(metadata: dict[str, Any]) -> bool:
            if any(
                isinstance(record, dict)
                and str(record.get("task_id") or "") == worker.task_id
                and (
                    str(record.get("parent_turn_id") or "") == parent_turn_id
                    or (
                        not str(record.get("parent_turn_id") or "")
                        and str(record.get("child_session_id") or "") == child_session_id
                    )
                )
                and str(record.get("status") or "") in TERMINAL_WORKER_STATUSES
                for record in metadata.get("worker_dispatches", [])
            ):
                return False
            self._apply_worker_status(
                metadata,
                worker.task_id,
                status,
                child_session_id,
                worker,
                parent_turn_id=parent_turn_id,
            )
            return True

        projected = await self._store.update_metadata_and_append_event(
            parent_session_id,
            claim_projection,
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
        if not projected:
            return None
        await self._sync_child_approvals_to_parent(
            parent_session_id=parent_session_id,
            parent_turn_id=parent_turn_id,
            child_session_id=child_session_id,
            worker=worker,
        )

        parent_session = await self._store.get_session(parent_session_id)
        if parent_session is None:
            return None

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
            return parent_session, True
        return parent_session, False

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
            projected = await self._project_worker_notification(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                child_session_id=child_session_id,
                worker=worker,
                content=content,
                status=status,
                failure_reason=failure_reason,
            )
            if projected is None:
                return
            parent_session, should_stop = projected
            if should_stop:
                return

            initial_launches = await self._maybe_launch_initial_workers(
                parent_session=parent_session,
                parent_turn_id=parent_turn_id,
            )
            if initial_launches:
                parent_session = await self._store.get_session(parent_session_id)
                if parent_session is None:
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

    def _worker_from_dispatch(
        self,
        record: dict[str, Any] | None,
        notification: dict[str, Any],
        worker_task_id: str,
    ) -> WorkerDispatchSpec:
        """Rebuild the worker identity a projection needs from its dispatch record."""
        item = record if isinstance(record, dict) else {}
        return WorkerDispatchSpec(
            task_id=str(item.get("task_id") or worker_task_id),
            description=str(item.get("description") or ""),
            prompt="",
            agent_key=str(item.get("agent_key") or notification.get("worker_agent_key") or ""),
            model_key=str(item.get("model_key") or "").strip() or None,
            context={
                "debate_stage": str(item.get("debate_stage") or "").strip(),
                "debate_round_index": int(item.get("debate_round_index") or 0),
                "debate_total_round_count": int(item.get("debate_total_round_count") or 0),
                "dispatch_role": str(item.get("dispatch_role") or "").strip(),
                "source_stage": str(item.get("source_stage") or "").strip(),
                "source_round_index": int(item.get("source_round_index") or 0),
            },
        )

    async def on_child_task_finished(
        self,
        *,
        parent_task_id: str,
        child_task_id: str,
        child_session_id: str,
        notification: dict[str, Any],
    ) -> None:
        """Project one settled durable child and queue its parent continuation."""
        if self._agent_task_store is None or self._agent_task_dispatcher is None:
            logger.warning(
                "coordinator_child_finished_without_durable_wiring",
                extra={"child_task_id": child_task_id, "parent_task_id": parent_task_id},
            )
            return
        try:
            parent_task = await self._agent_task_store.get(parent_task_id)
        except KeyError:
            logger.warning(
                "coordinator_child_parent_task_missing",
                extra={"child_task_id": child_task_id, "parent_task_id": parent_task_id},
            )
            return
        parent_session_id = parent_task.session_id
        parent_turn_id = parent_task.turn_id
        worker_task_id = str(notification.get("worker_task_id") or child_task_id)
        status = str(notification.get("status") or "completed")
        result_text = str(notification.get("result_text") or "")
        usage = notification.get("usage")
        resume_content = ""
        resume_agent_key = ""
        async with self._parent_locks[parent_session_id]:
            parent_session = await self._store.get_session(parent_session_id)
            if parent_session is None:
                logger.warning(
                    "coordinator_child_parent_session_missing",
                    extra={
                        "child_task_id": child_task_id,
                        "parent_task_id": parent_task_id,
                        "parent_session_id": parent_session_id,
                    },
                )
                return
            record = next(
                (
                    item
                    for item in parent_session.metadata.get("worker_dispatches", [])
                    if isinstance(item, dict)
                    and (
                        str(item.get("parent_turn_id") or "") == parent_turn_id
                        or (
                            not str(item.get("parent_turn_id") or "")
                            and str(item.get("child_session_id") or "") == child_session_id
                        )
                    )
                    and str(item.get("task_id") or "") == worker_task_id
                ),
                None,
            )
            if record is not None and str(record.get("status") or "") in TERMINAL_WORKER_STATUSES:
                auto_resume = (
                    parent_session.session_mode == SessionMode.coordinator
                    or (parent_session.selected_agent or "") == "coordinator"
                ) and parent_session.status not in {
                    SessionStatus.running,
                    SessionStatus.waiting_approval,
                }
                if auto_resume and not self._is_dispatch_blocked(parent_session, parent_turn_id):
                    worker = self._worker_from_dispatch(record, notification, worker_task_id)
                    content = self._build_task_notification(
                        task_id=worker.task_id,
                        child_session_id=child_session_id,
                        agent_key=worker.agent_key,
                        trace_id=str(notification.get("root_trace_id") or parent_task.trace_id),
                        status=status,
                        summary=str(notification.get("summary") or f'Worker "{worker.description}" finished with status {status}.'),
                        result=result_text,
                        usage=usage if isinstance(usage, dict) else {},
                    )
                    await self._queue_coordinator_resume(
                        parent_task=parent_task,
                        parent_session_id=parent_session_id,
                        parent_turn_id=parent_turn_id,
                        child_task_id=child_task_id,
                        child_session_id=child_session_id,
                        notification=notification,
                        worker_task_id=worker_task_id,
                        status=status,
                        content=content,
                        agent_key=parent_session.selected_agent or "coordinator",
                    )
                    return
                logger.info(
                    "coordinator_child_duplicate_finished_ignored",
                    extra={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "task_id": worker_task_id,
                        "child_task_id": child_task_id,
                        "worker_status": record.get("status"),
                    },
                )
                return
            worker = self._worker_from_dispatch(record, notification, worker_task_id)
            content = self._build_task_notification(
                task_id=worker.task_id,
                child_session_id=child_session_id,
                agent_key=worker.agent_key,
                trace_id=str(notification.get("root_trace_id") or parent_task.trace_id),
                status=status,
                summary=str(
                    notification.get("summary")
                    or f'Worker "{worker.description}" finished with status {status}.'
                ),
                result=result_text,
                usage=usage if isinstance(usage, dict) else {},
            )
            logger.info(
                "coordinator_child_finished",
                extra={
                    "parent_task_id": parent_task_id,
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "child_task_id": child_task_id,
                    "child_session_id": child_session_id,
                    "task_id": worker.task_id,
                    "worker_status": status,
                },
            )
            projected = await self._project_worker_notification(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                child_session_id=child_session_id,
                worker=worker,
                content=content,
                status=status,
                failure_reason=result_text if status == SessionStatus.failed.value else "",
            )
            if projected is None:
                return
            parent_session, should_stop = projected
            if should_stop:
                return
            if status == SessionStatus.waiting_approval.value:
                # An approval continuation is its own task type and carries no
                # child result; keep watching in-process until it truly settles.
                self._start_child_watch(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=str(notification.get("child_trace_id") or parent_task.trace_id),
                    child_session_id=child_session_id,
                    worker=worker,
                )
                return
            # Debate rounds stay registered here: both helpers only append
            # dispatch records and submit further child tasks when due, so the
            # durable path keeps the previous in-process semantics.
            initial_launches = await self._maybe_launch_initial_workers(
                parent_session=parent_session,
                parent_turn_id=parent_turn_id,
            )
            if initial_launches:
                parent_session = await self._store.get_session(parent_session_id)
                if parent_session is None:
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
            ) and parent_session.status not in {
                SessionStatus.running,
                SessionStatus.waiting_approval,
            }
            if not auto_resume:
                parent_session.messages.append(
                    ChatMessage(
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
                )
                parent_session.updated_at = datetime.utcnow()
                await self._store.save_session(parent_session)
                return
            resume_content = content
            resume_agent_key = parent_session.selected_agent or "coordinator"

        # Released the parent lock before writing the parent continuation so a
        # concurrent notification cannot serialize behind this database submit.
        await self._queue_coordinator_resume(
            parent_task=parent_task,
            parent_session_id=parent_session_id,
            parent_turn_id=parent_turn_id,
            child_task_id=child_task_id,
            child_session_id=child_session_id,
            notification=notification,
            worker_task_id=worker_task_id,
            status=status,
            content=resume_content,
            agent_key=resume_agent_key,
        )

    async def _queue_coordinator_resume(
        self,
        *,
        parent_task,
        parent_session_id: str,
        parent_turn_id: str,
        child_task_id: str,
        child_session_id: str,
        notification: dict[str, Any],
        worker_task_id: str,
        status: str,
        content: str,
        agent_key: str,
    ) -> None:
        resume = await self._agent_task_dispatcher.submit_coordinator_resume(
            parent_session_id=parent_session_id,
            parent_turn_id=parent_turn_id,
            project_id=parent_task.project_id,
            request_id=new_id("req"),
            trace_id=str(notification.get("child_trace_id") or "") or parent_task.trace_id or new_id("trace"),
            run_id=parent_task.run_id,
            run_item_id=parent_task.run_item_id,
            parent_task_id=parent_task.id,
            notification_task_id=f"{child_task_id}:{notification.get('attempt_id') or 'na'}",
            content=content,
            context={
                "message_source": "task_notification",
                "parent_turn_id": parent_turn_id,
                "parent_task_id": parent_task.id,
                "child_session_id": child_session_id,
                "child_task_id": child_task_id,
                "task_id": worker_task_id,
                "worker_agent_key": str(notification.get("worker_agent_key") or ""),
                "worker_status": status,
                "root_trace_id": str(notification.get("root_trace_id") or ""),
            },
            metadata={
                "message_kind": "task_notification",
                "task_id": worker_task_id,
                "child_session_id": child_session_id,
                "child_task_id": child_task_id,
                "worker_agent_key": str(notification.get("worker_agent_key") or ""),
                "worker_status": status,
            },
            agent_key=agent_key,
        )
        logger.info(
            "coordinator_resume_queued",
            extra={
                "task_id": resume.id,
                "parent_task_id": parent_task.id,
                "parent_session_id": parent_session_id,
                "parent_turn_id": parent_turn_id,
                "child_task_id": child_task_id,
                "worker_status": status,
            },
        )

    def _start_child_watch(
        self,
        *,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
    ) -> None:
        existing = self._watch_tasks.get(child_session_id)
        if existing is not None and not existing.done():
            return
        watch_task = asyncio.create_task(
            self._watch_child_session_until_settled(
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                parent_trace_id=parent_trace_id,
                child_session_id=child_session_id,
                worker=worker,
            )
        )
        self._watch_tasks[child_session_id] = watch_task

        def completed(task: asyncio.Task[None]) -> None:
            if self._watch_tasks.get(child_session_id) is task:
                self._watch_tasks.pop(child_session_id, None)
            if task.cancelled():
                return
            try:
                task.result()
            except Exception:
                logger.exception(
                    "coordinator_child_watch_failed",
                    extra={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "child_session_id": child_session_id,
                        "task_id": worker.task_id,
                    },
                )

        watch_task.add_done_callback(completed)

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
            parent_session = await self._store.get_session(parent_session_id)
            if parent_session is None or parent_session.status in {
                SessionStatus.failed, SessionStatus.interrupted,
            }:
                logger.info(
                    "coordinator_child_watch_parent_settled",
                    extra={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "child_session_id": child_session_id,
                        "parent_status": parent_session.status.value if parent_session else "missing",
                    },
                )
                return
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
                        parent_turn_id=parent_turn_id,
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

    def _merge_worker_dispatches(
        self,
        metadata: dict[str, Any],
        parent_turn_id: str,
        launch_records: list[dict[str, Any]],
    ) -> None:
        guard_host = SimpleNamespace(metadata=metadata)
        guard = self._get_failure_guard(guard_host)
        if str(guard.get("turn_id") or "") != parent_turn_id:
            self._reset_failure_guard(guard_host, parent_turn_id)

        existing = metadata.get("worker_dispatches", [])
        merged_by_worker_key: dict[tuple[str, str], dict[str, Any]] = {}
        ordered_worker_keys: list[tuple[str, str]] = []
        anonymous: list[dict[str, Any]] = []
        for record in existing if isinstance(existing, list) else []:
            if not isinstance(record, dict):
                continue
            task_id = str(record.get("task_id") or "").strip()
            if not task_id:
                anonymous.append(record)
                continue
            worker_key = (str(record.get("parent_turn_id") or "").strip(), task_id)
            if worker_key not in merged_by_worker_key:
                ordered_worker_keys.append(worker_key)
            merged_by_worker_key[worker_key] = record

        for record in launch_records:
            if not isinstance(record, dict):
                continue
            record = {**record, "parent_turn_id": parent_turn_id}
            task_id = str(record.get("task_id") or "").strip()
            if not task_id:
                anonymous.append(record)
                continue
            worker_key = (parent_turn_id, task_id)
            existing_record = merged_by_worker_key.get(worker_key)
            if existing_record is None:
                merged_by_worker_key[worker_key] = dict(record)
                ordered_worker_keys.append(worker_key)
                continue
            # Merge into the existing record. Preserve the first non-empty
            # child_session_id so downstream consumers can still correlate to
            # the original launch even if the same task is re-registered.
            merged = {**existing_record, **record}
            if not str(merged.get("child_session_id") or "").strip():
                merged["child_session_id"] = existing_record.get("child_session_id") or ""
            # A child can settle before this registration runs, so a terminal
            # projection must never be rewound to a launch-time status.
            prior_status = str(existing_record.get("status") or "")
            if (
                str(existing_record.get("parent_turn_id") or "") in {"", parent_turn_id}
                and prior_status in NON_REWINDABLE_WORKER_STATUSES
            ) and str(
                record.get("status") or ""
            ) not in TERMINAL_WORKER_STATUSES:
                merged["status"] = prior_status
                if existing_record.get("completed_at"):
                    merged["completed_at"] = existing_record["completed_at"]
            merged_by_worker_key[worker_key] = merged

        rebuilt = [merged_by_worker_key[key] for key in ordered_worker_keys]
        rebuilt.extend(anonymous)
        metadata["worker_dispatches"] = rebuilt

    async def _mutate_parent_metadata(
        self,
        parent_session_id: str,
        mutate: Callable[[dict[str, Any]], None],
        *,
        operation: str,
    ) -> bool:
        atomic_mutate = getattr(self._store, "mutate_metadata", None)
        if not callable(atomic_mutate):
            logger.error(
                "coordinator_atomic_metadata_store_required",
                extra={
                    "parent_session_id": parent_session_id,
                    "operation": operation,
                    "store_type": type(self._store).__name__,
                },
            )
            raise TypeError(
                f"Coordinator {operation} requires atomic mutate_metadata on "
                f"{type(self._store).__name__}."
            )
        return await atomic_mutate(parent_session_id, mutate)

    async def _register_worker_dispatches(
        self,
        parent_session_id: str,
        parent_turn_id: str,
        launch_records: list[dict[str, Any]],
    ) -> None:
        await self._mutate_parent_metadata(
            parent_session_id,
            lambda metadata: self._merge_worker_dispatches(
                metadata, parent_turn_id, launch_records
            ),
            operation="register_worker_dispatches",
        )

    def _apply_reconciled_dispatches(
        self,
        metadata: dict[str, Any],
        reconciled: list[dict[str, Any]],
    ) -> None:
        updates = {
            (
                str(record.get("parent_turn_id") or "").strip(),
                str(record.get("task_id") or "").strip(),
            ): record
            for record in reconciled
            if isinstance(record, dict) and record.get("recovered_at")
        }
        if not updates:
            return
        merged: list[Any] = []
        seen_worker_keys: set[tuple[str, str]] = set()
        for record in metadata.get("worker_dispatches") or []:
            if isinstance(record, dict):
                identity = (
                    str(record.get("parent_turn_id") or "").strip(),
                    str(record.get("task_id") or "").strip(),
                )
                seen_worker_keys.add(identity)
            else:
                identity = ("", "")
            update = updates.get(identity)
            current_status = str(record.get("status") or "")
            update_status = str(update.get("status") or "") if update is not None else ""
            may_settle_recovery = (
                current_status == "recovery_required"
                and update_status in {SessionStatus.completed.value, SessionStatus.failed.value}
            )
            if update is not None and (current_status == "running" or may_settle_recovery):
                merged.append(update)
            else:
                merged.append(record)
        for identity, update in updates.items():
            if identity not in seen_worker_keys:
                merged.append(update)
        metadata["worker_dispatches"] = merged

    async def _reconcile_worker_dispatches(
        self,
        parent_session: SessionRecord,
        reconciled: list[dict[str, Any]],
    ) -> None:
        """Reap orphaned dispatches without rewinding a concurrently settled child."""
        await self._mutate_parent_metadata(
            parent_session.id,
            lambda metadata: self._apply_reconciled_dispatches(metadata, reconciled),
            operation="reconcile_worker_dispatches",
        )

    @staticmethod
    def _pending_worker_payload(worker: WorkerDispatchSpec) -> dict[str, Any]:
        return {
            "task_id": worker.task_id,
            "description": worker.description,
            "prompt": worker.prompt,
            "agent_key": worker.agent_key,
            "model_key": worker.model_key,
            "skill_keys": list(worker.skill_keys),
            "context": dict(worker.context),
        }

    @classmethod
    def _dispatch_request_fingerprint(
        cls,
        workers: list[WorkerDispatchSpec],
        followup_workers: list[WorkerDispatchSpec],
        completion_worker: WorkerDispatchSpec | None,
    ) -> str:
        payload = {
            "workers": [cls._pending_worker_payload(worker) for worker in workers],
            "followup_workers": [
                cls._pending_worker_payload(worker) for worker in followup_workers
            ],
            "completion_worker": (
                cls._pending_worker_payload(completion_worker)
                if completion_worker is not None
                else None
            ),
        }
        try:
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Coordinator dispatch worker specifications must be JSON serializable."
            ) from exc
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _worker_from_pending(item: dict[str, Any]) -> WorkerDispatchSpec:
        return WorkerDispatchSpec(
            task_id=str(item["task_id"]),
            description=str(item["description"]),
            prompt=str(item["prompt"]),
            agent_key=str(item["agent_key"]),
            model_key=str(item.get("model_key") or "").strip() or None,
            skill_keys=list(item.get("skill_keys") or []),
            context=dict(item.get("context") or {}),
        )

    @staticmethod
    def _initial_worker_record(
        worker: WorkerDispatchSpec, *, status: str, child_session_id: str = ""
    ) -> dict[str, Any]:
        return {
            "task_id": worker.task_id,
            "child_session_id": child_session_id,
            "agent_key": worker.agent_key,
            "model_key": worker.model_key or "",
            "description": worker.description,
            "status": status,
            "debate_stage": str(worker.context.get("debate_stage") or "").strip(),
            "debate_round_index": int(worker.context.get("debate_round_index") or 0),
            "debate_total_round_count": int(worker.context.get("debate_total_round_count") or 0),
            "dispatch_role": str(worker.context.get("dispatch_role") or "worker").strip() or "worker",
            "source_stage": str(worker.context.get("source_stage") or "").strip(),
            "source_round_index": int(worker.context.get("source_round_index") or 0),
        }

    @classmethod
    def _followup_worker_record(
        cls, worker: WorkerDispatchSpec, *, status: str, child_session_id: str = ""
    ) -> dict[str, Any]:
        record = cls._initial_worker_record(
            worker, status=status, child_session_id=child_session_id
        )
        record["dispatch_role"] = "debate_followup"
        record["debate_stage"] = str(worker.context.get("debate_stage") or "cross_review")
        return record

    async def _register_initial_worker_queue(
        self, parent_session_id: str, parent_turn_id: str, parent_trace_id: str,
        workers: list[WorkerDispatchSpec]
    ) -> None:
        def register(metadata: dict[str, Any]) -> None:
            metadata["pending_initial_workers"] = [
                self._pending_worker_payload(worker) for worker in workers
            ]
            metadata["pending_initial_workers_parent_turn_id"] = parent_turn_id
            metadata["pending_initial_workers_parent_trace_id"] = parent_trace_id

        if not await self._mutate_parent_metadata(
            parent_session_id, register, operation="register_initial_worker_queue"
        ):
            raise KeyError(f"Parent session not found: {parent_session_id}")

    async def _claim_initial_worker_slots(
        self, parent_session_id: str, parent_turn_id: str
    ) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []

        def claim(metadata: dict[str, Any]) -> None:
            if str(metadata.get("pending_initial_workers_parent_turn_id") or "") != parent_turn_id:
                return
            pending = metadata.get("pending_initial_workers")
            if not isinstance(pending, list) or not pending:
                return
            active = sum(
                1
                for record in metadata.get("worker_dispatches", [])
                if isinstance(record, dict)
                and str(record.get("dispatch_role") or "worker") not in {"debate_followup", "completion_worker"}
                and str(record.get("status") or "") in ACTIVE_WORKER_STATUSES
            )
            capacity = max(0, self._settings.orchestration.coordinator_max_workers - active)
            if capacity == 0:
                return
            claimed.extend(item for item in pending[:capacity] if isinstance(item, dict))
            metadata["pending_initial_workers"] = pending[capacity:]
            self._merge_worker_dispatches(
                metadata,
                parent_turn_id,
                [
                    self._initial_worker_record(
                        self._worker_from_pending(item), status="launching"
                    )
                    for item in claimed
                ],
            )

        await self._mutate_parent_metadata(
            parent_session_id, claim, operation="claim_initial_worker_slots"
        )
        return claimed

    async def _record_unlaunched_worker_failure(
        self,
        *,
        parent_session_id: str,
        parent_turn_id: str,
        parent_trace_id: str,
        child_session_id: str,
        worker: WorkerDispatchSpec,
        reason: str,
    ) -> None:
        await self._project_worker_notification(
            parent_session_id=parent_session_id,
            parent_turn_id=parent_turn_id,
            child_session_id=child_session_id,
            worker=worker,
            content=self._build_task_notification(
                task_id=worker.task_id,
                child_session_id=child_session_id,
                agent_key=worker.agent_key,
                trace_id=parent_trace_id,
                status="failed",
                summary=f'Worker "{worker.description}" failed: {reason}',
                result=reason,
                usage={},
            ),
            status="failed",
            failure_reason=reason,
        )

    async def _maybe_launch_initial_workers(
        self, parent_session: SessionRecord, parent_turn_id: str
    ) -> list[dict[str, Any]] | None:
        claimed = await self._claim_initial_worker_slots(parent_session.id, parent_turn_id)
        if not claimed:
            return None
        parent = await self._store.get_session(parent_session.id)
        if parent is None:
            raise KeyError(f"Parent session not found: {parent_session.id}")
        parent_trace_id = str(parent.metadata.get("pending_initial_workers_parent_trace_id") or "")
        parent_depth = int(parent.metadata.get("agent_depth") or 0)
        results: list[dict[str, Any]] = []
        for item in claimed:
            worker = self._worker_from_pending(item)
            record = self._initial_worker_record(worker, status="launching")
            try:
                if not self._is_agent_available(worker.agent_key):
                    raise ValueError(f"Unknown agent: {worker.agent_key}")
                mode_key = self._resolve_worker_mode_key(worker)
                child = await self._session_service.create_internal_session(
                    CreateSessionRequest(
                        title=f"Worker: {worker.description}",
                        session_mode=SessionMode.background_task,
                        runtime_mode=RuntimeMode.background,
                        mode_key=mode_key,
                        preferred_model=worker.model_key,
                        selected_agent=worker.agent_key,
                        project_id=parent.project_id,
                        metadata={
                            "parent_session_id": parent.id,
                            "parent_turn_id": parent_turn_id,
                            "parent_trace_id": parent_trace_id,
                            "task_id": worker.task_id,
                            "worker_description": worker.description,
                            "dispatch_role": record["dispatch_role"],
                            "notification_mode": "task-notification",
                            "agent_depth": parent_depth + 1,
                            **self._trusted_worker_metadata(parent, mode_key),
                        },
                    )
                )
                record["child_session_id"] = child.id
                child_task_id = await self._start_child_worker(
                    parent_session=parent,
                    parent_session_id=parent.id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=child.id,
                    worker=worker,
                    mode_key=mode_key,
                    register_pool=True,
                )
                if child_task_id:
                    record["child_task_id"] = child_task_id
                record["status"] = "running"
            except ValueError as exc:
                record["status"] = "failed"
                record["error"] = str(exc)
                await self._record_unlaunched_worker_failure(
                    parent_session_id=parent.id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=record["child_session_id"],
                    worker=worker,
                    reason=str(exc),
                )
                logger.warning(
                    "coordinator_queued_worker_rejected",
                    extra={"parent_session_id": parent.id, "parent_turn_id": parent_turn_id,
                           "task_id": worker.task_id, "reason": str(exc)},
                )
            except Exception:
                # The child may already exist or have reached the task store.
                # Preserve an explicit unresolved record rather than replay it.
                record["status"] = "recovery_required"
                logger.exception(
                    "coordinator_queued_worker_launch_failed",
                    extra={"parent_session_id": parent.id, "parent_turn_id": parent_turn_id,
                           "task_id": worker.task_id},
                )
            await self._register_worker_dispatches(parent.id, parent_turn_id, [record])
            results.append(record)
        return results

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
            self._pending_worker_payload(worker) for worker in workers
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

    def _apply_worker_status(
        self,
        metadata: dict[str, Any],
        task_id: str,
        status: str,
        child_session_id: str,
        worker: WorkerDispatchSpec | None,
        parent_turn_id: str,
    ) -> None:
        worker_dispatches = []
        matched = False
        completion_task_id = str(metadata.get("completion_worker_task_id") or "")
        for record in metadata.get("worker_dispatches", []):
            if not isinstance(record, dict):
                continue
            if (
                record.get("task_id") == task_id
                and (
                    str(record.get("parent_turn_id") or "") == parent_turn_id
                    or (
                        not str(record.get("parent_turn_id") or "")
                        and str(record.get("child_session_id") or "") == child_session_id
                    )
                )
            ):
                matched = True
                worker_dispatches.append(
                    {
                        **record,
                        "parent_turn_id": parent_turn_id,
                        "status": status,
                        "child_session_id": child_session_id,
                        "completed_at": datetime.utcnow().isoformat(),
                    }
                )
            else:
                worker_dispatches.append(record)

        if not matched and worker is not None:
            new_record = {
                "task_id": task_id,
                "child_session_id": child_session_id,
                "parent_turn_id": parent_turn_id,
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
            worker_dispatches.append(new_record)

        metadata["worker_dispatches"] = worker_dispatches

    async def _mark_worker_status(
        self,
        parent_session_id: str,
        task_id: str,
        status: str,
        child_session_id: str,
        worker: WorkerDispatchSpec | None,
        parent_turn_id: str,
    ) -> None:
        await self._mutate_parent_metadata(
            parent_session_id,
            lambda metadata: self._apply_worker_status(
                metadata,
                task_id,
                status,
                child_session_id,
                worker,
                parent_turn_id=parent_turn_id,
            ),
            operation="mark_worker_status",
        )

    @staticmethod
    def _stable_worker_task_id(
        item: dict[str, Any], *, parent_session_id: str, parent_turn_id: str,
        role: str, index: int,
    ) -> str:
        supplied = str(item.get("task_id") or "").strip()
        if supplied:
            return supplied
        identity = json.dumps(
            {"parent_session_id": parent_session_id, "parent_turn_id": parent_turn_id,
             "role": role, "index": index, "worker": item},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        )
        return str(uuid5(NAMESPACE_URL, identity))

    def _normalize_workers(
        self, payload: dict[str, Any], parent_session_id: str, parent_turn_id: str
    ) -> list[WorkerDispatchSpec]:
        if isinstance(payload.get("workers"), list):
            raw_workers = payload["workers"]
        else:
            raw_workers = [payload]

        workers: list[WorkerDispatchSpec] = []
        for index, item in enumerate(raw_workers):
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
                    task_id=self._stable_worker_task_id(
                        item, parent_session_id=parent_session_id,
                        parent_turn_id=parent_turn_id, role="initial", index=index,
                    ),
                    description=description,
                    prompt=prompt,
                    agent_key=agent_key,
                    model_key=str(item.get("model_key") or "").strip() or None,
                    skill_keys=skill_keys,
                    context=dict(item.get("context", {})) if isinstance(item.get("context"), dict) else {},
                )
            )
        return workers

    def _normalize_followup_workers(
        self, payload: dict[str, Any], parent_session_id: str, parent_turn_id: str
    ) -> list[WorkerDispatchSpec]:
        raw_workers = payload.get("followup_workers")
        if not isinstance(raw_workers, list):
            return []

        workers: list[WorkerDispatchSpec] = []
        for index, item in enumerate(raw_workers):
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
                    task_id=self._stable_worker_task_id(
                        item, parent_session_id=parent_session_id,
                        parent_turn_id=parent_turn_id, role="followup", index=index,
                    ),
                    description=description,
                    prompt=prompt,
                    agent_key=agent_key,
                    model_key=str(item.get("model_key") or "").strip() or None,
                    skill_keys=skill_keys,
                    context=dict(item.get("context", {})) if isinstance(item.get("context"), dict) else {},
                )
            )
        return workers

    def _normalize_completion_worker(
        self, payload: dict[str, Any], parent_session_id: str, parent_turn_id: str
    ) -> WorkerDispatchSpec | None:
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
            task_id=self._stable_worker_task_id(
                raw_item, parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id, role="completion", index=0,
            ),
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
        followup_turn_id = str(
            parent_session.metadata.get("followup_workers_parent_turn_id") or ""
        )
        if (
            isinstance(pending_followup_workers, list)
            and pending_followup_workers
            and (not followup_turn_id or followup_turn_id == parent_turn_id)
        ):
            return None
        pending_initial_workers = parent_session.metadata.get("pending_initial_workers")
        initial_turn_id = str(
            parent_session.metadata.get("pending_initial_workers_parent_turn_id") or ""
        )
        if (
            isinstance(pending_initial_workers, list)
            and pending_initial_workers
            and (not initial_turn_id or initial_turn_id == parent_turn_id)
        ):
            return None

        reviewer_dispatches = [
            record
            for record in parent_session.metadata.get("worker_dispatches", [])
            if isinstance(record, dict)
            and str(record.get("parent_turn_id") or "") == parent_turn_id
            and not record.get("is_completion_worker")
        ]
        if not reviewer_dispatches:
            return None
        if any(
            str(record.get("status") or "") in ACTIVE_WORKER_STATUSES | {"queued"}
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
            "parent_turn_id": parent_turn_id,
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
        try:
            child_task_id = await self._start_child_worker(
                parent_session=parent_session,
                parent_session_id=parent_session_id,
                parent_turn_id=parent_turn_id,
                parent_trace_id=parent_trace_id,
                child_session_id=child_session.id,
                worker=worker,
                mode_key=self._resolve_worker_mode_key(worker),
                register_pool=False,
            )
        except ValueError as exc:
            logger.warning(
                "coordinator_completion_child_rejected",
                extra={
                    "parent_session_id": parent_session_id,
                    "parent_turn_id": parent_turn_id,
                    "task_id": worker.task_id,
                    "reason": str(exc),
                },
            )
            return None
        if child_task_id:
            launch_record["child_task_id"] = child_task_id
        self._merge_worker_dispatches(
            parent_session.metadata, parent_turn_id, [launch_record]
        )
        parent_session.metadata["completion_worker_dispatched"] = True
        parent_session.metadata["completion_worker_task_id"] = worker.task_id
        parent_session.metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
            stage="summary_resolution",
            status="running",
            current_round_index=int(worker.context.get("debate_round_index") or 0),
            total_round_count=int(worker.context.get("debate_total_round_count") or 0),
        )
        await self._store.save_session(parent_session)
        return launch_record

    async def _claim_followup_worker_slots(
        self, parent_session_id: str, parent_turn_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        claimed: list[dict[str, Any]] = []
        source_dispatches: list[dict[str, Any]] = []

        def claim(metadata: dict[str, Any]) -> None:
            if str(metadata.get("followup_workers_parent_turn_id") or "") != parent_turn_id:
                return
            initial_turn_id = str(
                metadata.get("pending_initial_workers_parent_turn_id") or ""
            )
            if metadata.get("pending_initial_workers") and (
                not initial_turn_id or initial_turn_id == parent_turn_id
            ):
                return
            pending = metadata.get("pending_followup_workers")
            if not isinstance(pending, list) or not pending:
                return
            pending = [item for item in pending if isinstance(item, dict)]
            if not pending:
                return
            round_index = min(self._pending_worker_round_index(item) for item in pending)
            first = next(item for item in pending if self._pending_worker_round_index(item) == round_index)
            source_index = self._pending_worker_source_round_index(first)
            source_stage = self._pending_worker_source_stage(first)
            prior = [
                record for record in metadata.get("worker_dispatches", [])
                if isinstance(record, dict)
                and str(record.get("parent_turn_id") or "") == parent_turn_id
                and not record.get("is_completion_worker")
                and int(record.get("debate_round_index") or 0) == source_index
                and str(record.get("debate_stage") or "").strip() == source_stage
            ]
            if not prior or any(
                str(record.get("status") or "") in ACTIVE_WORKER_STATUSES | {"queued"}
                for record in prior
            ):
                return
            active = sum(
                1 for record in metadata.get("worker_dispatches", [])
                if isinstance(record, dict)
                and int(record.get("debate_round_index") or 0) == round_index
                and str(record.get("status") or "") in ACTIVE_WORKER_STATUSES
            )
            capacity = max(0, self._settings.orchestration.coordinator_max_workers - active)
            if capacity == 0:
                return
            selected_positions: set[int] = set()
            for position, item in enumerate(pending):
                if self._pending_worker_round_index(item) != round_index:
                    continue
                claimed.append(item)
                selected_positions.add(position)
                if len(claimed) == capacity:
                    break
            if not claimed:
                return
            source_dispatches.extend(prior)
            remaining = [item for position, item in enumerate(pending) if position not in selected_positions]
            metadata["pending_followup_workers"] = remaining
            metadata["followup_workers_dispatched"] = not bool(remaining)
            self._merge_worker_dispatches(
                metadata,
                parent_turn_id,
                [
                    self._followup_worker_record(
                        self._worker_from_pending(item), status="launching"
                    )
                    for item in claimed
                ],
            )
            metadata["code_review_debate_progress"] = self._build_debate_progress_meta(
                stage="cross_review",
                status="running",
                current_round_index=round_index,
                total_round_count=self._pending_worker_total_round_count(first),
                peer_review_count=len(prior),
            )

        await self._mutate_parent_metadata(
            parent_session_id, claim, operation="claim_followup_worker_slots"
        )
        return claimed, source_dispatches

    async def _maybe_launch_followup_workers(
        self,
        parent_session,
        parent_turn_id: str,
    ) -> list[dict[str, Any]] | None:
        selected_batch, source_dispatches = await self._claim_followup_worker_slots(
            parent_session.id, parent_turn_id
        )
        if not selected_batch:
            return None
        parent_session = await self._store.get_session(parent_session.id)
        if parent_session is None:
            raise KeyError("Coordinator parent session disappeared during follow-up dispatch")
        peer_bundle = await self._build_peer_review_bundle(source_dispatches)
        launch_records: list[dict[str, Any]] = []
        parent_session_id = parent_session.id
        parent_trace_id = str(parent_session.metadata.get("followup_workers_parent_trace_id") or "")
        next_round_index = self._pending_worker_round_index(selected_batch[0])
        source_round_index = self._pending_worker_source_round_index(selected_batch[0])
        source_stage = self._pending_worker_source_stage(selected_batch[0])

        for item in selected_batch:
            worker = self._worker_from_pending(item)
            if not worker.prompt or not worker.agent_key or not self._is_agent_available(worker.agent_key):
                reason = "invalid_worker_spec" if not worker.prompt or not worker.agent_key else "unknown_agent"
                record = self._followup_worker_record(worker, status="failed")
                record["error"] = reason
                await self._record_unlaunched_worker_failure(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id="",
                    worker=worker,
                    reason=reason,
                )
                await self._register_worker_dispatches(parent_session_id, parent_turn_id, [record])
                logger.warning(
                    "coordinator_followup_worker_rejected",
                    extra={"parent_session_id": parent_session_id, "parent_turn_id": parent_turn_id,
                           "task_id": worker.task_id, "reason": reason},
                )
                launch_records.append(record)
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

            launch_record = self._followup_worker_record(worker, status="launching")
            try:
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
                launch_record["child_session_id"] = child_session.id
                child_task_id = await self._start_child_worker(
                    parent_session=parent_session,
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=child_session.id,
                    worker=worker,
                    mode_key=self._resolve_worker_mode_key(worker),
                    register_pool=False,
                )
            except ValueError as exc:
                launch_record["status"] = "failed"
                launch_record["error"] = str(exc)
                await self._record_unlaunched_worker_failure(
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    parent_trace_id=parent_trace_id,
                    child_session_id=launch_record["child_session_id"],
                    worker=worker,
                    reason=str(exc),
                )
                logger.warning(
                    "coordinator_followup_child_rejected",
                    extra={
                        "parent_session_id": parent_session_id,
                        "parent_turn_id": parent_turn_id,
                        "task_id": worker.task_id,
                        "reason": str(exc),
                    },
                )
            except Exception:
                launch_record["status"] = "recovery_required"
                logger.exception(
                    "coordinator_followup_child_launch_failed",
                    extra={"parent_session_id": parent_session_id, "parent_turn_id": parent_turn_id,
                           "task_id": worker.task_id},
                )
            else:
                if child_task_id:
                    launch_record["child_task_id"] = child_task_id
                launch_record["status"] = "running"
            await self._register_worker_dispatches(parent_session_id, parent_turn_id, [launch_record])
            launch_records.append(launch_record)

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
