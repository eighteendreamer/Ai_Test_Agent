from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from src.schemas.session import SessionSummary
from src.schemas.case_planning_task import CasePlanningTaskRecord
from src.schemas.document_differences import DocumentDifferenceTaskRecord


class TaskPoolSessionSummary(SessionSummary):
    selected_agent: str | None = None
    worker_dispatches: list[dict[str, Any]] = Field(default_factory=list)
    parent_session_id: str = ""


class TaskPoolPage(BaseModel):
    items: list[TaskPoolSessionSummary] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool = False


TaskPoolKind = Literal["all", "session", "case_planning", "document_difference", "document_impact"]
TaskPoolState = Literal["all", "active", "completed", "failed"]


class TaskPoolSessionEntry(BaseModel):
    kind: Literal["session"] = "session"
    project_name: str | None = None
    session: TaskPoolSessionSummary


class TaskPoolPlanningEntry(BaseModel):
    kind: Literal["case_planning"] = "case_planning"
    project_name: str | None = None
    task: CasePlanningTaskRecord


class TaskPoolDocumentDifferenceEntry(BaseModel):
    kind: Literal["document_difference"] = "document_difference"
    project_name: str | None = None
    document_title: str | None = None
    task: DocumentDifferenceTaskRecord


class TaskPoolDocumentImpactSummary(BaseModel):
    """Public catalog fields only; input/result object locations remain private."""
    id: str
    project_id: str
    document_id: str
    version_id: str
    difference_snapshot_id: str
    creation_key: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: Literal["queued", "preparing", "input_saved", "analyzing", "result_saved", "finished"]
    message: str
    attempt_no: int
    snapshot_id: str | None = None
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None = None
    completed_at: datetime | None = None


class TaskPoolDocumentImpactEntry(BaseModel):
    kind: Literal["document_impact"] = "document_impact"
    project_name: str | None = None
    document_title: str | None = None
    task: TaskPoolDocumentImpactSummary


TaskPoolEntry = Annotated[
    TaskPoolSessionEntry | TaskPoolPlanningEntry | TaskPoolDocumentDifferenceEntry | TaskPoolDocumentImpactEntry,
    Field(discriminator="kind"),
]


class TaskPoolEntriesPage(BaseModel):
    items: list[TaskPoolEntry] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool = False
