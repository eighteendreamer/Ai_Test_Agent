"""Explicit, read-only 401 recovery shared by both API coordinators."""
from __future__ import annotations

from copy import deepcopy
from typing import Iterable
from urllib.parse import urlsplit

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.capability_mapper import CAP_REFRESH_TOKEN
from src.modes.api_testing_mode.contracts import (
    EXECUTION_MODE_AUTH,
    TASK_COMPLETED,
    TASK_DEFERRED,
    TASK_FAILED,
    TASK_PENDING,
    TASK_READY,
    TASK_RUNNING,
    TASK_SKIPPED,
)

READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
AUTH_REFRESH_CHECKPOINT = "auth_refresh"


class AuthRefreshContractError(ValueError):
    """A frozen campaign contains an unsafe or ambiguous refresh relation."""


def validate_auth_refresh_contract(tasks: Iterable[ApiTestTask]) -> None:
    by_id = {task.task_id: task for task in tasks}
    references: dict[str, str] = {}
    dependencies = {task.task_id: list(task.depends_on) for task in tasks}
    for target in by_id.values():
        if bool(target.auth_refresh_task_id) != bool(target.allow_401_replay):
            raise AuthRefreshContractError(
                f"401 refresh relation must set both auth_refresh_task_id and allow_401_replay ({target.task_id})"
            )
        if not target.auth_refresh_task_id:
            continue
        refresh = by_id.get(target.auth_refresh_task_id)
        if refresh is None or refresh.task_id == target.task_id:
            raise AuthRefreshContractError(f"401 refresh task is missing or self-referencing ({target.task_id})")
        if refresh.task_id in references:
            raise AuthRefreshContractError(
                f"one refresh task cannot serve multiple replay targets ({refresh.task_id})"
            )
        references[refresh.task_id] = target.task_id
        if target.method.upper() not in READ_ONLY_METHODS:
            raise AuthRefreshContractError(f"401 replay requires a read-only target ({target.task_id})")
        if _expects_401(target):
            raise AuthRefreshContractError(f"expected 401 cannot also enable automatic replay ({target.task_id})")
        if refresh.execution_mode != EXECUTION_MODE_AUTH or refresh.capability != CAP_REFRESH_TOKEN:
            raise AuthRefreshContractError(f"refresh task is not an explicit refresh capability ({refresh.task_id})")
        if refresh.task_id in target.depends_on or target.task_id in refresh.depends_on:
            raise AuthRefreshContractError(f"401 refresh relation cannot be a normal dependency edge ({target.task_id})")
        target_origin = _origin(target)
        if target_origin != _origin(refresh):
            raise AuthRefreshContractError(f"401 refresh target and task have different URL origins ({target.task_id})")
        if not _auth_ancestors(target.task_id, dependencies, by_id) & _auth_ancestors(refresh.task_id, dependencies, by_id):
            raise AuthRefreshContractError(f"401 refresh has no shared declared authentication source ({target.task_id})")


def prepare_auth_refresh_tasks(tasks: Iterable[ApiTestTask]) -> None:
    task_list = list(tasks)
    validate_auth_refresh_contract(task_list)
    for target in task_list:
        refresh_id = target.auth_refresh_task_id
        if not refresh_id:
            continue
        refresh = next(task for task in task_list if task.task_id == refresh_id)
        if refresh.status not in {TASK_COMPLETED, TASK_FAILED, TASK_RUNNING}:
            refresh.status = TASK_DEFERRED


def refresh_targets(tasks: Iterable[ApiTestTask]) -> dict[str, ApiTestTask]:
    return {task.task_id: task for task in tasks if task.auth_refresh_task_id and task.allow_401_replay}


def refresh_is_eligible(task: ApiTestTask) -> bool:
    if task.response_status != 401 or task.request_outcome != "completed":
        return False
    state = task.execution_checkpoint.get(AUTH_REFRESH_CHECKPOINT, {})
    return isinstance(state, dict) and state.get("state") not in {"refresh_completed", "replay_completed", "replay_failed"}


def record_401_pending(task: ApiTestTask) -> None:
    # The first Worker attempt has its own immutable private result. A replay
    # creates a new result reference; carrying the old top-level reference into
    # the next frozen input would make the strict reader reject the new Job.
    previous_private_result_ref = task.execution_checkpoint.pop("private_result_ref", "")
    task.execution_checkpoint[AUTH_REFRESH_CHECKPOINT] = {
        "version": 1,
        "state": "pending",
        "target_task_id": task.task_id,
        "refresh_task_id": task.auth_refresh_task_id,
        "target_attempt": task.attempts,
        "original_401": {
            "response_status": task.response_status,
            "response_headers": deepcopy(task.response_headers),
            "response_body": deepcopy(task.response_body),
            "check_results": deepcopy(task.check_results),
            "request_outcome": task.request_outcome,
            "duration_ms": task.duration_ms,
            "completed_at": task.completed_at,
            "worker_session_id": task.worker_session_id,
        },
        "original_private_result_ref": previous_private_result_ref,
        "refresh_attempt": 0,
        "replay_attempt": 0,
    }


def checkpoint(task: ApiTestTask) -> dict:
    state = task.execution_checkpoint.get(AUTH_REFRESH_CHECKPOINT)
    return state if isinstance(state, dict) else {}


def mark_refresh_started(task: ApiTestTask) -> None:
    state = checkpoint(task)
    state["state"] = "refresh_running"
    state["refresh_attempt"] = int(state.get("refresh_attempt") or 0) + 1
    task.execution_checkpoint[AUTH_REFRESH_CHECKPOINT] = state


def mark_refresh_completed(task: ApiTestTask, refresh: ApiTestTask) -> None:
    state = checkpoint(task)
    state["state"] = "refresh_completed"
    state["refresh_task_id"] = refresh.task_id
    state["refresh_attempt"] = refresh.attempts
    state["refresh_worker_session_id"] = refresh.worker_session_id
    state["credential_ref"] = refresh.auth_ref
    task.execution_checkpoint[AUTH_REFRESH_CHECKPOINT] = state


def mark_replay_started(task: ApiTestTask) -> None:
    state = checkpoint(task)
    state["state"] = "replay_running"
    state["replay_attempt"] = int(state.get("replay_attempt") or 0) + 1
    task.execution_checkpoint[AUTH_REFRESH_CHECKPOINT] = state


def mark_replay_finished(task: ApiTestTask) -> None:
    state = checkpoint(task)
    state["state"] = "replay_completed" if task.status == TASK_COMPLETED else "replay_failed"
    state["replay_response_status"] = task.response_status
    state["replay_worker_session_id"] = task.worker_session_id
    state["replay_attempt"] = task.attempts
    task.execution_checkpoint[AUTH_REFRESH_CHECKPOINT] = state


def _expects_401(task: ApiTestTask) -> bool:
    for assertion in task.assertions:
        kind = assertion.kind if hasattr(assertion, "kind") else assertion.get("kind")
        expected = assertion.expected if hasattr(assertion, "expected") else assertion.get("expected")
        if kind == "status_code" and expected == 401:
            return True
        if kind == "status_code_range" and isinstance(expected, (list, tuple)) and 401 in expected:
            return True
    return False


def _origin(task: ApiTestTask) -> tuple[str, str]:
    parsed = urlsplit(task.full_url or task.path)
    return parsed.scheme.casefold(), parsed.netloc.casefold()


def _auth_ancestors(task_id: str, dependencies: dict[str, list[str]], by_id: dict[str, ApiTestTask]) -> set[str]:
    pending = list(dependencies.get(task_id, []))
    visited: set[str] = set()
    sources: set[str] = set()
    while pending:
        identity = pending.pop()
        if identity in visited:
            continue
        visited.add(identity)
        parent = by_id.get(identity)
        if parent is None:
            continue
        if parent.execution_mode == EXECUTION_MODE_AUTH:
            sources.add(identity)
        pending.extend(dependencies.get(identity, []))
    return sources


__all__ = [
    "AUTH_REFRESH_CHECKPOINT",
    "AuthRefreshContractError",
    "READ_ONLY_METHODS",
    "checkpoint",
    "mark_refresh_completed",
    "mark_refresh_started",
    "mark_replay_finished",
    "mark_replay_started",
    "prepare_auth_refresh_tasks",
    "record_401_pending",
    "refresh_is_eligible",
    "refresh_targets",
    "validate_auth_refresh_contract",
]
