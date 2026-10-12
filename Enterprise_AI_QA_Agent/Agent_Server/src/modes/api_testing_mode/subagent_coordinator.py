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
from src.modes.api_testing_mode.input_binding import bind_path_parameters, substitute_path_parameter
from src.modes.api_testing_mode.auth_propagation import propagate_auth_ref
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
    ) -> None:
        self._pool = pool
        self._policy = policy
        self._coordinator_runtime_service = coordinator_runtime_service
        self._session_store = session_store
        self._parent_context = parent_context
        self._credential_manager = credential_manager
        self._credential_references = credential_reference_service
        self._dispatch_refs: dict[str, str] = {}
        self._auth_token_field = auth_token_field
        self._worker_agent_key = worker_agent_key
        self._worker_model_key = worker_model_key
        self._poll_interval_seconds = max(0.1, poll_interval_seconds)
        self._checkpoint_callback = checkpoint_callback
        self._active_resource_locks: set[str] = set()
        self._write_running: bool = False
        self._task_outputs: dict[str, Any] = {}
        self._unsettled_child_session_ids: set[str] = set()
        self._unsettled_task_ids: set[str] = set()

    @property
    def has_unsettled_workers(self) -> bool:
        return bool(self._unsettled_child_session_ids or self._unsettled_task_ids)

    async def run_all(self) -> list[ApiTestTask]:
        restored_running = self._pool.running_tasks()
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
            self._pool.resolve_blocked()
            batch = self._select_batch()
            if not batch:
                if self._pool.has_running:
                    await asyncio.sleep(self._poll_interval_seconds)
                    continue
                break
            await self._dispatch_batch(batch)
            if self.has_unsettled_workers:
                break
            self._pool.resolve_blocked()

        if self._policy.max_retries > 0 and not self.has_unsettled_workers:
            await self._retry_failed()

        return self._pool.all_tasks

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
                if self.has_unsettled_workers:
                    return
                self._pool.resolve_blocked()

    def _is_retryable(self, task: ApiTestTask) -> bool:
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
                self._pool.mark_running(task.task_id)
                task.attempts += 1
                task.worker_status = "dispatching"
                self._emit_checkpoint("task_running", task)
                for lock in task.resource_locks:
                    self._active_resource_locks.add(lock)
                if task.execution_mode == EXECUTION_MODE_WRITE:
                    self._write_running = True
                self._apply_input_bindings(task)
                launched_tasks.append(task)

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
                task.worker_status = session.status.value
                task.worker_summary = self._extract_assistant_summary(session.messages)
                tool_output = self._extract_api_test_runner_output(session.messages)
                if not tool_output:
                    self._fail_task(task, task.worker_summary or f"Worker finished without api-test-runner output ({session.status.value}).")
                    continue
                if self._credential_references is not None:
                    result_payload = tool_output.get("task_result") or {}
                    if isinstance(result_payload, dict) and result_payload.get("auth_ref"):
                        restored = await self._credential_references.load_parent_credential(
                            result_payload["auth_ref"], self._parent_context)
                        self._credential_manager.restore_session(restored)
                self._apply_worker_output(task, tool_output)
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

        if task.status == TASK_COMPLETED:
            self._pool.mark_completed(task.task_id)
            self._emit_checkpoint("task_completed", task)
            self._task_outputs[task.task_id] = task.response_body
            if task.execution_mode == EXECUTION_MODE_AUTH and task.auth_ref:
                self._propagate_auth_ref(task.auth_ref, source_task_id=task.task_id)
            return

        self._pool.mark_failed(task.task_id, task.last_error or str(tool_output.get("summary") or "execution_failed"))
        self._emit_checkpoint("task_failed", task)

    def _fail_task(self, task: ApiTestTask, error: str) -> None:
        task.status = TASK_FAILED
        task.last_error = str(error or "execution_failed")
        self._pool.mark_failed(task.task_id, task.last_error)
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

    def _apply_input_bindings(self, task: ApiTestTask) -> None:
        if not task.input_bindings and not task.depends_on:
            return

        for binding in task.input_bindings:
            if not binding.source_task_id:
                continue
            source_output = self._task_outputs.get(binding.source_task_id)
            if source_output is None:
                continue
            value = self._extract_path_value(source_output, binding.source_path)
            if value is None and binding.literal_value is not None:
                value = binding.literal_value
            if value is None:
                continue
            self._inject_binding_value(task, binding.target_path, value)

        bind_path_parameters(task, self._task_outputs)

    def _extract_path_value(self, obj: Any, path: str):
        if not path:
            return obj
        parts = path.replace("[", ".").replace("]", "").split(".")
        current = obj
        for part in parts:
            if not part:
                continue
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
            if current is None:
                return None
        return current

    def _inject_binding_value(self, task: ApiTestTask, target_path: str, value: Any) -> None:
        if not target_path:
            return
        if target_path.startswith("url.path.") or target_path.startswith("path."):
            param_name = target_path.split(".")[-1]
            substitute_path_parameter(task, param_name, value)
            return
        if target_path.startswith("body.") or target_path.startswith("request_body."):
            field_path = target_path.split(".", 1)[1] if "." in target_path else ""
            if field_path:
                if task.request_body is None:
                    task.request_body = {}
                self._inject_nested(task.request_body, field_path, value)
            return
        if target_path.startswith("query."):
            param_name = target_path.split(".", 1)[-1]
            if param_name:
                task.request_query[param_name] = value
            return
        if target_path.startswith("request_body."):
            self._inject_nested(task.request_body, target_path[len("request_body."):], value)
            return
        if target_path.startswith("request_query."):
            self._inject_nested(task.request_query, target_path[len("request_query."):], value)
            return
        if target_path.startswith("request_headers."):
            key = target_path[len("request_headers."):]
            if key:
                task.request_headers[key] = value

    def _inject_nested(self, root: dict[str, Any], path: str, value: Any) -> None:
        current = root
        parts = [part for part in path.split(".") if part]
        if not parts:
            return
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        current[parts[-1]] = value

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
