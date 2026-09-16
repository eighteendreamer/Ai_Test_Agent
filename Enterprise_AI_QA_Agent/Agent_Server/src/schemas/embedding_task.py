"""Versioned queue contract for durable Embedding work."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TestCaseEmbeddingTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task_id: str = Field(min_length=1, max_length=160)
    task_type: Literal["embedding_task"] = "embedding_task"
    source_type: Literal["test_case_version"] = "test_case_version"
    source_id: str = Field(min_length=1, max_length=160)
    source_version: str = Field(min_length=1, max_length=160)
    content_hash: str = Field(min_length=64, max_length=64)
    request_id: str = Field(min_length=1, max_length=256)
    trace_id: str = Field(min_length=1, max_length=256)
    session_id: str | None = None
    turn_id: str | None = None
    project_id: str = Field(min_length=1, max_length=160)
    run_id: str | None = None
    run_item_id: str | None = None
    attempt_id: str | None = None
    worker_id: str | None = None
    resource_id: str | None = None
    case_id: str = Field(min_length=1, max_length=160)
    case_version_id: str = Field(min_length=1, max_length=160)
    priority: int = Field(default=10, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
