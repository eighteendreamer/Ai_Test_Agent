"""ToolJob activity facts recorded by the same owner-fenced write transaction.

No upstream counterpart (checked: existing ToolJob CAS/output observations,
Codex rmcp-client active_time_timeout, v4 section 6.3): the reference pause
timer is process-local; this platform needs durable, server-observed boundaries
across waits and Worker/store rebuilds. These facts grant no execution rights.
"""
from __future__ import annotations

from src.schemas.framework_tool_activity import FrameworkToolActivity, ToolActivityEvent
from src.schemas.tool_job import ToolJobStatus, ToolOutputObservation


ACTIVITY_KEY = "framework_tool_activity"


def _identity(job):
    return {"job_id": job["id"], "binding_id": job["metadata"].get("framework_binding_id"),
        "session_id": job["session_id"], "turn_id": job["turn_id"]}


def _read(value):
    record = value["metadata"].get(ACTIVITY_KEY)
    if record is None:
        return None
    journal = FrameworkToolActivity.model_validate(record)
    if any(getattr(journal, key) != field for key, field in _identity(value).items()):
        raise ValueError("Tool activity differs from its durable job identity")
    if journal.events[-1].status != value["status"]:
        raise ValueError("Tool activity differs from its durable job state")
    if journal.events[-1].output_observed:
        ToolOutputObservation.model_validate(value["metadata"].get("tool_output_observation"))
    return journal


def read_tool_activity(job):
    """Read only; absent/legacy history remains absent, invalid history rejects."""
    if hasattr(job, "model_dump"):
        value = job.model_dump(mode="python")
    elif hasattr(job, "_mapping"):
        value = dict(job._mapping)
    else:
        value = dict(job)
    return _read(value)


def record_tool_activity(previous, current, *, observed_at, output_observed=False):
    """Return server-owned JSON, ignoring any candidate-supplied journal.

    Existing rows without history start with an explicitly incomplete observed
    prefix; neither client timestamps nor started_at can backfill old waits.
    """
    journal = _read(previous) if previous is not None else None
    if output_observed:
        ToolOutputObservation.model_validate(current["metadata"].get("tool_output_observation"))
    if journal is None:
        first = previous if previous is not None else current
        journal = FrameworkToolActivity(**_identity(current),
            origin_observed=previous is None and first["started_at"] is None
                and first["status"] in {ToolJobStatus.queued, ToolJobStatus.waiting_approval},
            events=[ToolActivityEvent(status=first["status"], output_observed=False, observed_at=observed_at)])
    returned = journal.events[-1].output_observed or output_observed
    if (journal.events[-1].status, journal.events[-1].output_observed) != (current["status"], returned):
        journal = FrameworkToolActivity(**journal.model_dump(exclude={"events"}),
            events=[*journal.events, ToolActivityEvent(status=current["status"],
                output_observed=returned, observed_at=observed_at)])
    return journal.model_dump(mode="json")
