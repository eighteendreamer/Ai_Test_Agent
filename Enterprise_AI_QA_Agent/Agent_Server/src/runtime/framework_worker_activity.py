"""Worker facts piggyback on existing transactional Agent task events.

No upstream counterpart (checked: Agent task begin/defer/finish and ordered
Session events, Codex rmcp-client active_time_timeout, v4 section 6.3): the
reference timer is process-local. Reuse durable task boundaries, not a new
timer/scheduler or lease-expiry inference. Missing history stays missing.
"""
from __future__ import annotations

from src.schemas.framework_worker_activity import (
    FrameworkWorkerActivity, WorkerActivityBoundary, WorkerActivityEvent,
)
from src.schemas.runtime_binding import RuntimeBinding


ACTIVITY_KEY = "framework_worker_activity"


def _identity(task, attempt):
    binding = RuntimeBinding.model_validate(task["runtime_binding"])
    if binding.session_id != task["session_id"] or binding.turn_id != task["turn_id"]:
        raise ValueError("Worker activity differs from its durable task binding")
    return dict(binding_id=binding.binding_id, session_id=task["session_id"],
        turn_id=task["turn_id"], task_id=task["id"], attempt_id=attempt["id"], worker_id=attempt["worker_id"])


def capture_worker_activity(task, *, status, result_status, execution_returned, observed_at):
    return WorkerActivityBoundary(**_identity(task, {"id": task["attempt_id"], "worker_id": task["worker_id"]}),
        event=WorkerActivityEvent(status=status, result_status=result_status,
            execution_returned=execution_returned, observed_at=observed_at)).model_dump(mode="json")


def read_worker_activity(task, attempts, events):
    """Validate raw facts against task/attempt/event identities, without writes.

    Missing old events yield None, not fabricated zero activity. A visible
    prefix cannot establish complete coverage, even if its claim was observed.
    """
    journals = {attempt["id"]: None for attempt in attempts}
    identities = {attempt["id"]: _identity(task, attempt) for attempt in attempts}
    points = {attempt["id"]: [] for attempt in attempts}
    for row in events:
        raw = row["payload"].get(ACTIVITY_KEY)
        if raw is None:
            continue
        boundary = WorkerActivityBoundary.model_validate(raw)
        identity = boundary.model_dump(exclude={"event", "schema_version", "scope"})
        point = boundary.event
        if (identities.get(boundary.attempt_id) != identity
            or row["session_id"] != boundary.session_id
            or row["payload"].get("task_id") != boundary.task_id
            or row["payload"].get("turn_id") != boundary.turn_id
            or row["type"] != f"agent_task.{point.status}"
            or row["timestamp"] != point.observed_at
            or row["payload"].get("result_status") != point.result_status
            or row["payload"].get("execution_returned", False) != point.execution_returned
            or row["payload"].get("status", point.status) != point.status
            or (row["payload"].get("_harness_context") or {}).get("attempt_id") != boundary.attempt_id):
            raise ValueError("Worker activity differs from its durable event or attempt identity")
        points[boundary.attempt_id].append(point)
    for attempt in attempts:
        observed = points[attempt["id"]]
        if attempt["id"] == task["attempt_id"] and attempt["status"] != task["status"]:
            raise ValueError("Worker activity task and current attempt states disagree")
        if observed:
            journal = FrameworkWorkerActivity(**identities[attempt["id"]], events=observed)
            if journal.events[-1].status != attempt["status"]:
                raise ValueError("Worker activity differs from its durable attempt state")
            journals[attempt["id"]] = journal
    return journals
