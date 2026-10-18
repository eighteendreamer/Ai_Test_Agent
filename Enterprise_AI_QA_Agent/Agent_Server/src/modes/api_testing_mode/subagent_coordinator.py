"""Dispatch API test tasks to real worker sessions via ``subagent-dispatch``."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from src.modes.api_testing_mode.campaign_state import (
    ApiTestTask,
    CredentialSession,
    ExecutionPolicy,
)
from src.modes.api_testing_mode.contracts import (
    EXECUTION_MODE_AUTH,
    EXECUTION_MODE_READ,
    EXECUTION_MODE_WRITE,
    TASK_COMPLETED,
    TASK_FAILED,
)
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.modes.api_testing_mode.input_binding import (
    apply_input_bindings,
    inject_binding_value,
    unresolved_path_parameters,
)
from src.modes.api_testing_mode.auth_propagation import propagate_auth_ref
from src.modes.api_testing_mode.auth_refresh import (
    checkpoint as auth_refresh_checkpoint,
    mark_refresh_completed,
    mark_refresh_started,
    mark_replay_finished,
    mark_replay_started,
    prepare_auth_refresh_tasks,
    record_401_pending,
    refresh_is_eligible,
    refresh_targets,
)
from src.modes.api_testing_mode.worker_result_reader import ApiWorkerResultReader
from src.runtime.store import SessionStore
from src.runtime.postgres_api_credential_store import ApiCredentialReferenceError
from src.schemas.session import MessageRole, SessionStatus

CheckpointCallback = Callable[[str, ApiTestTask, list[ApiTestTask]], None]
logger = logging.getLogger("uvicorn.error.api_testing_mode.coordinator")


class ApiSubagentCoordinator:
    """Orchestrate API task execution through background worker sessions."""

    def __init__(
        self,
        *,
        pool: ApiTaskPool,
        policy: ExecutionPolicy,
        coordinator_runtime_service: Any,
        session_store: SessionStore,
        parent_context: dict[str, Any],
        credential_manager: CredentialManager,
        auth_token_field: str = "access_token",
        worker_agent_key: str = "api-executor-worker",
        worker_model_key: str | None = None,
        poll_interval_seconds: float = 0.25,
        checkpoint_callback: CheckpointCallback | None = None,
        credential_reference_service: Any = None,
        tool_job_service: Any = None,
    ) -> None:
        self._pool = pool
        self._policy = policy
        self._coordinator_runtime_service = coordinator_runtime_service
        self._session_store = session_store
        self._parent_context = parent_context
        self._credential_manager = credential_manager
        self._credential_references = credential_reference_service
        self._tool_jobs = tool_job_service
        self._dispatch_refs: dict[str, str] = {}
        self._auth_token_field = auth_token_field
        self._worker_agent_key = worker_agent_key
        self._worker_model_key = worker_model_key
        self._poll_interval_seconds = max(0.1, poll_interval_seconds)
        self._checkpoint_callback = checkpoint_callback
        self._active_resource_locks: set[str] = set()
        self._write_running: bool = False
        prepare_auth_refresh_tasks(pool.all_tasks)
        self._auth_refresh_targets = refresh_targets(pool.all_tasks)
        self._pending_auth_refresh: list[str] = [
            task.task_id for task in pool.all_tasks
            if auth_refresh_checkpoint(task).get("state") in {
                "pending", "refresh_running", "refresh_completed", "replay_running"
            }
        ]
        self._task_outputs: dict[str, Any] = {
            task.task_id: task.response_body for task in pool.all_tasks if task.status == TASK_COMPLETED
        }
        self._unsettled_child_session_ids: set[str] = set()
        self._unsettled_task_ids: set[str] = set()
        self._waiting_child_session_ids: set[str] = set()

    @property
    def waiting_child_session_ids(self) -> list[str]:
        return sorted(self._waiting_child_session_ids)

    @property
    def has_unsettled_workers(self) -> bool:
        return bool(self._unsettled_child_session_ids or self._unsettled_task_ids)

    async def run_all(self) -> list[ApiTestTask]:
        if not await self._restore_completed_outputs():
            return self._pool.all_tasks
        restored_running = self._pool.running_tasks()
        if restored_running and self._tool_jobs is not None:
            deadline = asyncio.get_running_loop().time() + max(
                self._poll_interval_seconds, self._policy.request_timeout_seconds * 2)
            for task in restored_running:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    continue
                try:
                    settled = await asyncio.wait_for(self._restore_settled_worker(task), timeout=remaining)
                    if settled is False:
                        self._unsettled_task_ids.add(task.task_id)
                except ApiCredentialReferenceError as exc:
                    logger.warning("api_worker_result_recovery_rejected", extra={
                        "parent_session_id": self._parent_context.get("session_id"),
                        "task_id": task.task_id, "child_session_id": task.worker_session_id, "reason": str(exc)})
                    self._fail_task(task, str(exc))
                    self._unsettled_task_ids.add(task.task_id)
                except TimeoutError:
                    logger.warning("api_worker_result_read_timed_out", extra={
                        "parent_session_id": self._parent_context.get("session_id"),
                        "task_id": task.task_id, "child_session_id": task.worker_session_id})
            restored_running = self._pool.running_tasks()
            if self.has_unsettled_workers or self.waiting_child_session_ids:
                return self._pool.all_tasks
        if restored_running:
            logger.warning(
                "api_restored_workers_unsettled",
                extra={
                    "parent_session_id": str(self._parent_context.get("session_id") or ""),
                    "parent_turn_id": str(self._parent_context.get("turn_id") or ""),
                    "task_ids": [task.task_id for task in restored_running],
                    "child_session_ids": [task.worker_session_id for task in restored_running if task.worker_session_id],
                },
            )
            for task in restored_running:
                self._unsettled_task_ids.add(task.task_id)
                if task.worker_session_id:
                    self._unsettled_child_session_ids.add(task.worker_session_id)
                self._fail_task(
                    task,
                    "worker_unsettled: restored running task has no active coordinator wait; "
                    "reconcile its child session before retry",
                )
            return self._pool.all_tasks
        while not self._pool.is_complete:
            # A persisted 401 recovery checkpoint owns the target task until
            # refresh/replay is settled; recover it before dependency
            # resolution can expose the target as an ordinary task.
            if self._pending_auth_refresh:
                await self._recover_pending_auth_refresh()
                if self.has_unsettled_workers or self.waiting_child_session_ids:
                    break
                continue
            self._pool.resolve_blocked()
            batch = self._select_batch()
            if not batch:
                if self._pool.has_running:
                    await asyncio.sleep(self._poll_interval_seconds)
                    continue
                if self._pending_auth_refresh:
                    await self._recover_pending_auth_refresh()
                    continue
                self._skip_unused_refresh_tasks()
                break
            await self._dispatch_batch(batch)
            if self._pending_auth_refresh:
                await self._recover_pending_auth_refresh()
            if self.has_unsettled_workers or self.waiting_child_session_ids:
                break
            self._pool.resolve_blocked()

        if self._policy.max_retries > 0 and not self.has_unsettled_workers and not self.waiting_child_session_ids:
            await self._retry_failed()

        return self._pool.all_tasks

    async def _restore_completed_outputs(self) -> bool:
        # Completed public checkpoints retain the original Job/ref, not usable
        # response credentials. Rehydrate privately without resetting a verdict.
        for task in self._pool.completed_tasks():
            if not task.execution_checkpoint.get("private_result_ref"):
                continue
            try:
                async def read():
                    if self._tool_jobs is None:
                        raise ApiCredentialReferenceError("api_worker_result_reference_service_missing")
                    session = await self._session_store.get_session(task.worker_session_id)
                    if session is None:
                        raise ApiCredentialReferenceError("api_worker_result_session_missing")
                    output = await ApiWorkerResultReader(tool_job_service=self._tool_jobs,
                        credential_reference_service=self._credential_references).read(
                            session=session, task=task, parent_context=self._parent_context)
                    if not output or output["task_result"].get("status") != TASK_COMPLETED:
                        raise ApiCredentialReferenceError("api_worker_result_not_completed")
                    result = output["task_result"]
                    if result.get("auth_ref"):
                        credential = await self._credential_references.load_parent_credential(
                            result["auth_ref"], self._parent_context)
                        self._credential_manager.restore_session(credential)
                    return result
                result = await asyncio.wait_for(read(), timeout=max(
                    self._poll_interval_seconds, self._policy.request_timeout_seconds * 2))
                task.response_body = result.get("response_body")
                self._task_outputs[task.task_id] = task.response_body
                logger.info("api_completed_private_output_restored", extra={
                    "parent_session_id": self._parent_context.get("session_id"), "task_id": task.task_id,
                    "child_session_id": task.worker_session_id})
            except (ApiCredentialReferenceError, TimeoutError) as exc:
                # Preserve the terminal result; stop new sends until its original
                # private evidence is readable rather than using [REDACTED].
                self._unsettled_task_ids.add(task.task_id)
                logger.warning("api_completed_private_output_unavailable", extra={
                    "parent_session_id": self._parent_context.get("session_id"), "task_id": task.task_id,
                    "reason": str(exc) if isinstance(exc, ApiCredentialReferenceError) else "read_timeout"})
                return False
        return True

    async def _retry_failed(self) -> None:
        max_retries = self._policy.max_retries
        for _ in range(max_retries):
            retryable = [
                task for task in self._pool.failed_tasks()
                if task.attempts <= max_retries and self._is_retryable(task)
            ]
            if not retryable:
                break
            for task in retryable:
                task.status = "ready"
                task.last_error = ""
                task.worker_status = ""
                task.worker_summary = ""
                task.worker_session_id = ""
            while not self._pool.is_complete:
                self._pool.resolve_blocked()
                batch = self._select_batch()
                if not batch:
                    if self._pool.has_running:
                        await asyncio.sleep(self._poll_interval_seconds)
                        continue
                    break
                await self._dispatch_batch(batch)
                if self._pending_auth_refresh:
                    await self._recover_pending_auth_refresh()
                if self.has_unsettled_workers or self.waiting_child_session_ids:
                    return
                self._pool.resolve_blocked()

    def _is_retryable(self, task: ApiTestTask) -> bool:
        if (task.last_error or "").startswith("Required input binding(s) unresolved:"):
            return False
        if task.request_outcome == "unknown":
            return False
        error = (task.last_error or "").lower()
        if error.startswith(("worker_timeout:", "worker_unsettled:")):
            return False
        if task.response_status and task.response_status >= 500:
            return True
        if "timeout" in error or "timed out" in error:
            return True
        if "connection" in error:
            return True
        return False

    def _select_batch(self) -> list[ApiTestTask]:
        ready = self._pool.ready_tasks()
        if not ready:
            return []

        auth_tasks = [task for task in ready if task.execution_mode == EXECUTION_MODE_AUTH]
        if auth_tasks:
            return auth_tasks[:1]

        if self._write_running:
            return []

        batch: list[ApiTestTask] = []
        max_workers = max(1, self._policy.max_workers)

        for task in ready:
            if len(batch) >= max_workers:
                break
            if task.execution_mode == EXECUTION_MODE_WRITE:
                if self._policy.write_serial and batch:
                    continue
                if self._has_resource_conflict(task, batch):
                    continue
                batch.append(task)
                break
            if task.execution_mode == EXECUTION_MODE_READ:
                if self._has_resource_conflict(task, batch):
                    continue
                batch.append(task)

        return batch

    def _has_resource_conflict(self, task: ApiTestTask, batch: list[ApiTestTask]) -> bool:
        if not task.resource_locks:
            return False
        task_locks = set(task.resource_locks)
        if task_locks & self._active_resource_locks:
            return True
        for other in batch:
            if set(other.resource_locks) & task_locks:
                return True
        return False

    async def _dispatch_batch(self, batch: list[ApiTestTask]) -> None:
        launched_tasks: list[ApiTestTask] = []
        try:
            for task in batch:
                # Resolve inputs before claiming/dispatching. A remaining
                # placeholder is a missing prerequisite, never a valid URL.
                binding_gaps = self._apply_input_bindings(task)
                if binding_gaps:
                    self._fail_task(task, "Required input binding(s) unresolved: " + "; ".join(binding_gaps))
                    continue
                unresolved = unresolved_path_parameters(task)
                if unresolved:
                    self._fail_task(
                        task,
                        "Required path parameter(s) unresolved: " + ", ".join(unresolved)
                        + "; provide project data or a producer test case before execution.",
                    )
                    continue
                self._pool.mark_running(task.task_id)
                task.attempts += 1
                task.worker_status = "dispatching"
                self._emit_checkpoint("task_running", task)
                for lock in task.resource_locks:
                    self._active_resource_locks.add(lock)
                if task.execution_mode == EXECUTION_MODE_WRITE:
                    self._write_running = True
                launched_tasks.append(task)

            if not launched_tasks:
                return

            if self._credential_references is not None:
                for task in launched_tasks:
                    task_payload = task.model_dump(mode="json")
                    task_payload["attempts"] = max(0, task.attempts - 1)
                    identity = task.task_id if task.attempts <= 1 else f"{task.task_id}:attempt:{task.attempts}"
                    self._dispatch_refs[task.task_id] = await self._credential_references.freeze_dispatch(
                        parent_context=self._parent_context, dispatch_task_id=identity, task=task_payload,
                        token_field=self._auth_token_field,
                        credential=self._credential_manager.get(task.auth_ref) if task.auth_ref else None)
            workers = [self._build_worker_spec(task) for task in launched_tasks]
            dispatch_task_ids = {
                task.task_id: worker["task_id"]
                for task, worker in zip(launched_tasks, workers)
            }
            dispatch_result = await self._coordinator_runtime_service.dispatch(
                payload={"workers": workers},
                context=self._parent_context,
            )
            if dispatch_result.get("status") == "recovery_required":
                for task in launched_tasks:
                    self._unsettled_task_ids.add(task.task_id)
                    self._fail_task(
                        task,
                        dispatch_result.get("error") or "coordinator_dispatch_claim_unresolved",
                    )
                return
            worker_records = {
                str(item.get("task_id") or ""): item
                for item in dispatch_result.get("workers", [])
                if isinstance(item, dict)
            }
            child_session_ids = [
                str(record.get("child_session_id") or "")
                for record in worker_records.values()
                if str(record.get("status") or "") in {
                    "running", "completed", "failed", "interrupted", "cancelled",
                    "waiting_approval",
                }
                and str(record.get("child_session_id") or "")
                and not (
                    str(record.get("status") or "") == "failed"
                    and str(record.get("error") or "")
                )
            ]
            # Capture dispatch identity before waiting. A recreated coordinator
            # reads this same child rather than launching another HTTP request.
            for task in launched_tasks:
                record = worker_records.get(dispatch_task_ids[task.task_id])
                if record and record.get("child_session_id"):
                    task.worker_session_id = str(record["child_session_id"])
                    self._emit_checkpoint("task_dispatched", task)
            settled_sessions, timed_out_ids = await self._wait_for_sessions(child_session_ids)
            self._unsettled_child_session_ids.update(timed_out_ids)
            settled_map = {session.id: session for session in settled_sessions}

            for task in launched_tasks:
                record = worker_records.get(dispatch_task_ids[task.task_id])
                if not record:
                    self._fail_task(task, dispatch_result.get("error") or "worker_dispatch_missing")
                    continue
                child_session_id = str(record.get("child_session_id") or "")
                if str(record.get("status") or "") == "failed" and (
                    str(record.get("error") or "") or not child_session_id
                ):
                    self._fail_task(
                        task,
                        str(record.get("error") or dispatch_result.get("error") or "worker_launch_failed"),
                    )
                    continue
                task.worker_session_id = child_session_id
                if child_session_id in timed_out_ids:
                    task.worker_status = "running"
                    self._fail_task(
                        task,
                        f"worker_timeout: child session {child_session_id} did not settle; "
                        "its execution status remains unknown and must be reconciled before retry",
                    )
                    continue
                session = settled_map.get(child_session_id)
                if session is None:
                    self._fail_task(task, "worker_session_not_found")
                    continue
                if self._tool_jobs is not None and session.status == SessionStatus.waiting_approval:
                    task.worker_status = session.status.value
                    self._waiting_child_session_ids.add(session.id)
                    self._emit_checkpoint("task_waiting_approval", task)
                    continue
                await self._merge_session_result(task, session)
        except ApiCredentialReferenceError as exc:
            logger.warning("api_worker_private_input_unavailable", extra={
                "parent_session_id": str(self._parent_context.get("session_id") or ""), "reason": str(exc)})
            for task in launched_tasks:
                if task.status not in {TASK_COMPLETED, TASK_FAILED}:
                    self._fail_task(task, str(exc))
        finally:
            for task in launched_tasks:
                for lock in task.resource_locks:
                    self._active_resource_locks.discard(lock)
                if task.execution_mode == EXECUTION_MODE_WRITE:
                    self._write_running = False

    async def _restore_settled_worker(self, task: ApiTestTask) -> bool | None:
        session = await self._session_store.get_session(task.worker_session_id) if task.worker_session_id else None
        if session is not None and session.status in {SessionStatus.running, SessionStatus.waiting_approval}:
            self._waiting_child_session_ids.add(session.id)
            return None
        if session is None or session.status not in {SessionStatus.completed, SessionStatus.failed, SessionStatus.interrupted}:
            return None
        return await self._merge_session_result(task, session)

    async def _merge_session_result(self, task: ApiTestTask, session: Any) -> bool:
        task.worker_status = session.status.value
        task.worker_summary = self._extract_assistant_summary(session.messages)
        if self._tool_jobs is not None:
            tool_output = await ApiWorkerResultReader(tool_job_service=self._tool_jobs,
                credential_reference_service=self._credential_references).read(
                    session=session, task=task, parent_context=self._parent_context)
        else:
            tool_output = self._extract_api_test_runner_output(session.messages)
        if not tool_output:
            self._fail_task(task, task.worker_summary or f"Worker finished without api-test-runner output ({session.status.value}).")
            return False
        if self._credential_references is not None:
            result_payload = tool_output.get("task_result") or {}
            if isinstance(result_payload, dict) and result_payload.get("auth_ref"):
                restored = await self._credential_references.load_parent_credential(
                    result_payload["auth_ref"], self._parent_context)
                self._credential_manager.restore_session(restored)
        result_payload = tool_output.get("task_result") or {}
        result_task = ApiTestTask.model_validate(result_payload) if isinstance(result_payload, dict) else None
        if result_task is not None and result_task.status == TASK_FAILED and self._queue_401_recovery(task, result_task):
            self._copy_worker_result(task, result_task, tool_output)
            record_401_pending(task)
            task.status = "pending"
            task.last_error = ""
            self._emit_checkpoint("task_auth_refresh_pending", task)
        else:
            self._apply_worker_output(task, tool_output)
        return True

    def _build_worker_spec(self, task: ApiTestTask) -> dict[str, Any]:
        current_credential = self._credential_manager.get(task.auth_ref) if task.auth_ref else None
        task_payload = task.model_dump(mode="json")
        # The parent reserves this dispatch attempt before launch. The runner
        # increments attempts when it actually executes the HTTP request.
        task_payload["attempts"] = max(0, task.attempts - 1)
        runner_args = {
            "worker_action": "execute_task",
            "task": task_payload,
            "auth_token_field": self._auth_token_field,
        }
        if self._credential_references is not None:
            runner_args = {"worker_action": "execute_task", "dispatch_ref": self._dispatch_refs[task.task_id]}
        elif current_credential is not None:
            runner_args["credential_session"] = current_credential.model_dump(mode="json")

        prompt = (
            "Execute the assigned API test task by calling `api-test-runner` exactly once.\n\n"
            "Tool arguments:\n"
            f"{json.dumps(runner_args, ensure_ascii=False, indent=2)}\n\n"
            "After the tool finishes, reply with a single concise execution summary."
        )
        return {
            "task_id": task.task_id if task.attempts <= 1 else f"{task.task_id}:attempt:{task.attempts}",
            "description": f"{task.method.upper()} {task.path or task.full_url}",
            "prompt": prompt,
            "agent_key": self._worker_agent_key,
            "model_key": self._worker_model_key,
            "context": {
                "mode_key": "api_testing",
                "dispatch_role": "api_execution_worker",
                "api_task_id": task.task_id,
                "api_execution_mode": task.execution_mode,
            },
        }

    async def _wait_for_sessions(self, child_session_ids: list[str]) -> tuple[list[Any], set[str]]:
        pending = {session_id for session_id in child_session_ids if session_id}
        settled: dict[str, Any] = {}
        terminal_statuses = {
            SessionStatus.completed,
            SessionStatus.failed,
            SessionStatus.interrupted,
            SessionStatus.waiting_approval,
        }
        timeout_seconds = max(self._poll_interval_seconds, self._policy.request_timeout_seconds * 2)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while pending:
            completed_ids: list[str] = []
            read_timed_out = False
            for session_id in list(pending):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    read_timed_out = True
                    break
                try:
                    session = await asyncio.wait_for(
                        self._session_store.get_session(session_id), timeout=remaining,
                    )
                except TimeoutError:
                    read_timed_out = True
                    break
                if session is None:
                    completed_ids.append(session_id)
                    continue
                if session.status in terminal_statuses:
                    settled[session_id] = session
                    completed_ids.append(session_id)
            for session_id in completed_ids:
                pending.discard(session_id)
            if not pending:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if read_timed_out or remaining <= 0:
                logger.warning(
                    "api_worker_wait_timed_out",
                    extra={
                        "parent_session_id": str(self._parent_context.get("session_id") or ""),
                        "parent_turn_id": str(self._parent_context.get("turn_id") or ""),
                        "child_session_ids": sorted(pending),
                        "timeout_seconds": timeout_seconds,
                    },
                )
                return list(settled.values()), pending
            await asyncio.sleep(min(self._poll_interval_seconds, remaining))
        return list(settled.values()), set()

    def _apply_worker_output(self, task: ApiTestTask, tool_output: dict[str, Any]) -> None:
        task_payload = tool_output.get("task_result")
        if not isinstance(task_payload, dict):
            self._fail_task(task, "worker_task_result_missing")
            return

        result_task = ApiTestTask.model_validate(task_payload)
        self._copy_worker_result(task, result_task, tool_output)

        if task.status == TASK_COMPLETED:
            self._pool.mark_completed(task.task_id)
            self._pool.record_reconciliation(task)
            self._emit_checkpoint("task_completed", task)
            self._task_outputs[task.task_id] = task.response_body
            if task.execution_mode == EXECUTION_MODE_AUTH and task.auth_ref:
                self._propagate_auth_ref(task.auth_ref, source_task_id=task.task_id)
            return

        self._pool.mark_failed(task.task_id, task.last_error or str(tool_output.get("summary") or "execution_failed"))
        self._pool.record_reconciliation(task)
        self._emit_checkpoint("task_failed", task)

    def _copy_worker_result(self, task: ApiTestTask, result_task: ApiTestTask, tool_output: dict[str, Any]) -> None:
        merged_worker_session_id = task.worker_session_id
        merged_worker_status = task.worker_status
        merged_worker_summary = task.worker_summary or str(tool_output.get("summary") or "")
        for field_name in result_task.model_dump(mode="python"):
            # Keep validated nested models; serializing their values here turns
            # AssertionSpec into dicts and breaks the parent state contract.
            setattr(task, field_name, getattr(result_task, field_name))
        task.worker_session_id = merged_worker_session_id
        task.worker_status = merged_worker_status
        task.worker_summary = merged_worker_summary
        credential_payload = tool_output.get("credential_session")
        if isinstance(credential_payload, dict):
            restored = self._credential_manager.restore_session(
                CredentialSession.model_validate(credential_payload)
            )
            task.auth_ref = restored.credential_session_id

    def _queue_401_recovery(self, task: ApiTestTask, result_task: ApiTestTask) -> bool:
        if task.task_id not in self._auth_refresh_targets or not refresh_is_eligible(result_task):
            return False
        if task.task_id not in self._pending_auth_refresh:
            self._pending_auth_refresh.append(task.task_id)
        logger.info("api_auth_refresh_pending", extra={"task_id": task.task_id,
            "refresh_task_id": task.auth_refresh_task_id, "original_status": 401})
        return True

    async def _recover_pending_auth_refresh(self) -> None:
        pending = list(self._pending_auth_refresh)
        self._pending_auth_refresh.clear()
        for target_id in pending:
            target = self._pool.get(target_id)
            refresh = self._pool.get(target.auth_refresh_task_id) if target else None
            if target is None or refresh is None:
                continue
            state = auth_refresh_checkpoint(target)
            if state.get("state") in {"replay_completed", "replay_failed", "replay_unsettled"}:
                continue
            if state.get("state") == "replay_running":
                if target.status == TASK_COMPLETED:
                    mark_replay_finished(target)
                    self._emit_checkpoint("task_auth_refresh_replayed", target)
                    continue
                if target.status == TASK_RUNNING:
                    self._unsettled_task_ids.add(target.task_id)
                    self._fail_task(target, "auth_refresh_recovery_unsettled")
                    state["state"] = "replay_unsettled"
                    target.execution_checkpoint["auth_refresh"] = state
                    continue
            # Reuse a refresh with a durable completed Worker result. A
            # restarted coordinator must never send the same refresh twice.
            if refresh.status == TASK_COMPLETED:
                if not refresh.auth_ref:
                    self._fail_task(target, "auth_refresh_credential_missing")
                    state["state"] = "refresh_failed"
                    target.execution_checkpoint["auth_refresh"] = state
                    continue
                target.auth_ref = refresh.auth_ref
                if state.get("state") != "refresh_completed":
                    mark_refresh_completed(target, refresh)
                    self._emit_checkpoint("task_auth_refresh_completed", target)
            elif state.get("state") == "pending" and refresh.status in {"deferred", "ready"}:
                if any((self._pool.get(dep) is None or self._pool.get(dep).status != "completed")
                       for dep in refresh.depends_on):
                    self._fail_task(target, "auth_refresh_prerequisite_unavailable")
                    state["state"] = "refresh_prerequisite_failed"
                    target.execution_checkpoint["auth_refresh"] = state
                    continue
                self._pool.mark_ready(refresh.task_id)
                mark_refresh_started(target)
                await self._dispatch_batch([refresh])
                if refresh.status == TASK_COMPLETED and refresh.auth_ref:
                    target.auth_ref = refresh.auth_ref
                    mark_refresh_completed(target, refresh)
                    self._emit_checkpoint("task_auth_refresh_completed", target)
                else:
                    self._fail_task(target, "auth_refresh_failed")
                    state = auth_refresh_checkpoint(target)
                    state["state"] = "refresh_failed"
                    target.execution_checkpoint["auth_refresh"] = state
                    continue
            elif refresh.status == TASK_FAILED:
                self._fail_task(target, "auth_refresh_failed")
                state["state"] = "refresh_failed"
                target.execution_checkpoint["auth_refresh"] = state
                continue
            else:
                # A refresh already marked running but lacking a settled
                # Worker result has unknown outcome and cannot be resent.
                self._fail_task(target, "auth_refresh_recovery_unsettled")
                state["state"] = "refresh_unsettled"
                target.execution_checkpoint["auth_refresh"] = state
                continue

            self._pool.mark_ready(target.task_id)
            mark_replay_started(target)
            self._emit_checkpoint("task_auth_refresh_replay_started", target)
            await self._dispatch_batch([target])
            mark_replay_finished(target)
            if auth_refresh_checkpoint(target).get("state") == "replay_failed":
                self._pool.mark_failed(target.task_id, target.last_error or "401 replay failed")
            self._emit_checkpoint("task_auth_refresh_replayed", target)

    def _skip_unused_refresh_tasks(self) -> None:
        for task in self._pool.all_tasks:
            if task.status == "deferred":
                self._pool.mark_skipped(task.task_id, "Auth refresh was not needed.")
                self._emit_checkpoint("task_skipped", task)

    def _fail_task(self, task: ApiTestTask, error: str) -> None:
        task.status = TASK_FAILED
        task.last_error = str(error or "execution_failed")
        self._pool.mark_failed(task.task_id, task.last_error)
        self._pool.record_reconciliation(task)
        self._emit_checkpoint("task_failed", task)

    def _emit_checkpoint(self, event_type: str, task: ApiTestTask) -> None:
        if self._checkpoint_callback is None:
            return
        try:
            self._checkpoint_callback(event_type, task, self._pool.all_tasks)
        except Exception:
            return

    def _propagate_auth_ref(self, auth_ref: str, *, source_task_id: str) -> None:
        propagate_auth_ref(self._pool.all_tasks, auth_ref, source_task_id=source_task_id)

    def _apply_input_bindings(self, task: ApiTestTask) -> list[str]:
        return apply_input_bindings(task, self._task_outputs)

    def _inject_binding_value(self, task: ApiTestTask, target_path: str, value: Any) -> None:
        # Retained for existing direct callers; execution uses the atomic binder.
        inject_binding_value(task, target_path, value)

    def _extract_api_test_runner_output(self, messages: list[Any]) -> dict[str, Any] | None:
        for message in reversed(messages):
            if message.role != MessageRole.tool:
                continue
            if str(message.metadata.get("tool_key") or "") != "api-test-runner":
                continue
            parsed = self._parse_tool_payload(str(message.content or ""))
            if isinstance(parsed, dict):
                return parsed
        return None

    def _parse_tool_payload(self, content: str) -> dict[str, Any] | None:
        if not content:
            return None
        payload_text = content.split("\n\n", 1)[1] if "\n\n" in content else content
        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _extract_assistant_summary(self, messages: list[Any]) -> str:
        for message in reversed(messages):
            if message.role == MessageRole.assistant:
                return str(message.content or "").strip()
        return ""


__all__ = ["ApiSubagentCoordinator", "CheckpointCallback"]
