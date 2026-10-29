"""Server-observed ToolJob transitions, not proof of physical process stops."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_model_call import union_activity_intervals
from src.schemas.tool_job import ToolJobStatus


class ToolActivityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ToolJobStatus
    output_observed: bool
    observed_at: datetime

    @model_validator(mode="after")
    def require_observation_timezone(self):
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Tool activity observation must include a timezone")
        return self


class FrameworkToolActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["tool_job_state_transitions"] = "tool_job_state_transitions"
    job_id: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    # This proves the observed origin, not global writer/handler coverage.
    origin_observed: bool
    events: list[ToolActivityEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_event_order(self):
        first = self.events[0]
        if self.origin_observed and (first.status not in {
            ToolJobStatus.queued, ToolJobStatus.waiting_approval,
        } or first.output_observed):
            raise ValueError("Tool activity origin requires an observed pre-execution state")
        for previous, current in zip(self.events, self.events[1:]):
            if current.observed_at < previous.observed_at:
                raise ValueError("Tool activity observations are out of order")
            if previous.output_observed and not current.output_observed:
                raise ValueError("Tool activity cannot forget a reliable output")
        return self

    def activity_intervals(self, *, as_of: datetime | None = None) -> list[tuple[datetime, datetime]]:
        """Union observed running segments; an unproven stop retains an open tail.

        A returned output or completed/partial settlement closes the local tool
        operation. Cancel, retry/resume requests and failure without a reliable
        return do not prove the sent operation stopped. Missing prehistory is
        separately exposed by origin_observed, never reconstructed here.
        """
        intervals = []
        started_at = None
        for event in self.events:
            if event.status == ToolJobStatus.running and not event.output_observed and started_at is None:
                started_at = event.observed_at
            if started_at is not None and (event.output_observed or event.status in {
                ToolJobStatus.waiting_approval, ToolJobStatus.waiting_resource, ToolJobStatus.waiting_children,
                ToolJobStatus.completed, ToolJobStatus.partial,
            }):
                intervals.append((started_at, event.observed_at))
                started_at = None
        if started_at is not None:
            intervals.append((started_at, None))
        return union_activity_intervals(intervals, as_of=as_of)
