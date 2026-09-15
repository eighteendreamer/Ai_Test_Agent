"""Versioned queue command for test-run dispatch."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RunDispatchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_type: Literal["test_run"] = "test_run"
    task_id: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=256)
    trace_id: str = Field(min_length=1, max_length=256)
    project_id: str = Field(min_length=1, max_length=160)
    session_id: str | None = None
    run_id: str = Field(min_length=1, max_length=160)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    priority: Literal[0] = 0


class RunDispatchAccepted(BaseModel):
    status: Literal["queued"] = "queued"
    task_id: str
    run_id: str
    message_id: str
