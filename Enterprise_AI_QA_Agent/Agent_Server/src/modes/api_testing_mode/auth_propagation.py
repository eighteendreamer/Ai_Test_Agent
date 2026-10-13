"""Share the verified source-based credential propagation between coordinators."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from urllib.parse import urlsplit

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.capability_mapper import CAP_LOGIN, CAP_REFRESH_TOKEN
from src.modes.api_testing_mode.contracts import EXECUTION_MODE_AUTH, TASK_COMPLETED, TASK_FAILED, TASK_RUNNING, TASK_SKIPPED

logger = logging.getLogger("uvicorn.error.api_testing_mode.coordinator")


def propagate_auth_ref(tasks: Iterable[ApiTestTask], auth_ref: str, *, source_task_id: str) -> None:
    tasks_by_id = {task.task_id: task for task in tasks}
    dependencies = {identity: list(task.depends_on) for identity, task in tasks_by_id.items()}
    # Older saved single-account plans have a refresh capability but no login
    # edge. Project the same unique source without rewriting their frozen graph.
    for task in tasks_by_id.values():
        if task.capability != CAP_REFRESH_TOKEN or _auth_ancestors(task, tasks_by_id, dependencies):
            continue
        candidates = refresh_login_candidates(task, tasks_by_id.values())
        if len(candidates) == 1:
            dependencies[task.task_id].append(candidates[0].task_id)
            logger.info("api_legacy_refresh_source_projected", extra={
                "task_id": task.task_id, "login_task_id": candidates[0].task_id,
                "frozen_dependencies_unchanged": True,
            })
    auth_ancestors = {identity: _auth_ancestors(task, tasks_by_id, dependencies)
        for identity, task in tasks_by_id.items()}
    for task in tasks_by_id.values():
        if task.status in {TASK_COMPLETED, TASK_FAILED, TASK_RUNNING, TASK_SKIPPED}:
            continue
        sources = auth_ancestors[task.task_id]
        # A recorded refresh shadows its own login, but an unrelated auth
        # branch never shadows or replaces another account's credential.
        nearest = sources - set().union(*(auth_ancestors[source] for source in sources))
        if len(nearest) > 1:
            logger.warning("api_auth_source_ambiguous", extra={
                "target_task_id": task.task_id, "auth_source_task_ids": sorted(nearest),
                "configured_credential_preserved": bool(task.auth_ref),
            })
        elif nearest == {source_task_id}:
            task.auth_ref = auth_ref
            logger.info("api_auth_ref_propagated", extra={
                "source_task_id": source_task_id, "target_task_id": task.task_id,
                "dependency_match": source_task_id in task.depends_on})


def refresh_login_candidates(task: ApiTestTask, tasks: Iterable[ApiTestTask]) -> list[ApiTestTask]:
    """Reuse the declared login/refresh capabilities only within one URL origin."""
    address = urlsplit(task.full_url or task.path)
    origin = (address.scheme.lower(), address.netloc.lower())
    candidates = []
    for candidate in tasks:
        if candidate.capability != CAP_LOGIN or candidate.execution_mode != EXECUTION_MODE_AUTH:
            continue
        candidate_address = urlsplit(candidate.full_url or candidate.path)
        if (candidate_address.scheme.lower(), candidate_address.netloc.lower()) == origin:
            candidates.append(candidate)
    return candidates


def _auth_ancestors(task: ApiTestTask, tasks_by_id: dict[str, ApiTestTask], dependencies: dict[str, list[str]]) -> set[str]:
    # No upstream counterpart (checked: existing auth_propagation, formal
    # RunItem dependencies, local auth course): credential lineage must be
    # derived from the recorded graph; never infer identity from task mode.
    pending, visited, sources = list(dependencies[task.task_id]), set(), set()
    while pending:
        identity = pending.pop()
        if identity in visited:
            continue
        visited.add(identity)
        parent = tasks_by_id.get(identity)
        if parent is None:
            continue  # ApiTaskPool validates references before execution.
        if parent.execution_mode == EXECUTION_MODE_AUTH:
            sources.add(identity)
        pending.extend(dependencies[identity])
    return sources
