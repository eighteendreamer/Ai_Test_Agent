from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from src.schemas.session import SessionSummary
from src.schemas.case_planning_task import CasePlanningTaskRecord


class TaskPoolSessionSummary(SessionSummary):
    selected_agent: str | None = None
    worker_dispatches: list[dict[str, Any]] = Field(default_factory=list)
    parent_session_id: str = ""


class TaskPoolPage(BaseModel):
    items: list[TaskPoolSessionSummary] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool = False


TaskPoolKind = Literal["all", "session", "case_planning"]
TaskPoolState = Literal["all", "active", "completed", "failed"]


class TaskPoolSessionEntry(BaseModel):
    kind: Literal["session"] = "session"
    project_name: str | None = None
    session: TaskPoolSessionSummary


class TaskPoolPlanningEntry(BaseModel):
    kind: Literal["case_planning"] = "case_planning"
    project_name: str | None = None
    task: CasePlanningTaskRecord


TaskPoolEntry = Annotated[TaskPoolSessionEntry | TaskPoolPlanningEntry, Field(discriminator="kind")]


class TaskPoolEntriesPage(BaseModel):
    items: list[TaskPoolEntry] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool = False
