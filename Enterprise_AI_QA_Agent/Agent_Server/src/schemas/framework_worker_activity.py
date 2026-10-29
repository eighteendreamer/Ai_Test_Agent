"""Observed Worker task boundaries; neither precise CPU time nor external stops."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.agent_task import AgentTaskStatus, TERMINAL_AGENT_TASK_STATUSES
from src.schemas.framework_model_call import union_activity_intervals


class WorkerActivityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: AgentTaskStatus
    result_status: str | None = None
    execution_returned: bool
    observed_at: datetime

    @model_validator(mode="after")
    def validate_boundary(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Worker activity observation must include a timezone")
        if self.execution_returned and self.status not in TERMINAL_AGENT_TASK_STATUSES:
            raise ValueError("Worker return requires a terminal task state")
        return self


class WorkerActivityIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["worker_task_state_transitions"] = "worker_task_state_transitions"
    binding_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    worker_id: str = Field(min_length=1)


class WorkerActivityBoundary(WorkerActivityIdentity):
    event: WorkerActivityEvent


class FrameworkWorkerActivity(WorkerActivityIdentity):
    events: list[WorkerActivityEvent] = Field(min_length=1)

    @property
    def origin_observed(self) -> bool:
        # Seeing claim is not proof of complete writer or nested-wait coverage.
        return self.events[0].status == "claimed"

    @model_validator(mode="after")
    def validate_event_order(self):
        for previous, current in zip(self.events, self.events[1:]):
            if current.observed_at < previous.observed_at:
                raise ValueError("Worker activity observations are out of order")
            if previous.execution_returned:
                raise ValueError("Worker activity cannot continue after its returned execution")
        return self

    def activity_intervals(self, *, as_of: datetime | None = None) -> list[tuple[datetime, datetime]]:
        """Exclude claim/admission wait; retain unconfirmed dispatch tails.

        Task completion alone is not a return: control/reaper writes can settle
        a row while its Worker is still alive. A return closes only the local
        dispatch span; model/tool operations retain their independent tails.
        Nested waits not represented by task transitions are not reconstructed.
        """
        intervals = []
        started_at = None
        for event in self.events:
            if event.status == "running" and started_at is None:
                started_at = event.observed_at
            if started_at is not None and event.execution_returned:
                intervals.append((started_at, event.observed_at))
                started_at = None
        if started_at is not None:
            intervals.append((started_at, None))
        return union_activity_intervals(intervals, as_of=as_of)
