"""Durable, project-scoped planning inputs; no synthetic Agent session."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from src.schemas.case_planning import (
    CasePlanRecord, CasePlanningRequest, PlanningDecision, PlanningModel,
)
from src.schemas.model_config import ModelConfigRecord


TERMINAL_PLANNING_TASK_STATUSES = frozenset({"completed", "failed"})


def planning_model_parameters(config: ModelConfigRecord) -> dict[str, Any]:
    # Explicit allowlist: never persist keys, OAuth tokens or header values.
    parameters = config.model_dump(mode="json", include={
        "key", "provider", "transport", "model_id", "temperature", "max_tokens",
        "max_output_tokens", "context_window", "capabilities", "auth_type", "oauth_provider",
    })
    for name in ("api_base_url", "extra_headers"):
        encoded = json.dumps(getattr(config, name), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        parameters[f"{name}_hash"] = hashlib.sha256(encoded.encode()).hexdigest()
    return parameters


class CasePlanningTaskRequest(CasePlanningRequest):
    creation_key: str = Field(min_length=1, max_length=160)

    def planning_request(self) -> CasePlanningRequest:
        return CasePlanningRequest.model_validate(self.model_dump(exclude={"creation_key"}))


class PlanningTaskSeed(PlanningModel):
    plan: CasePlanRecord
    project_context: dict[str, Any]
    system_prompt: str
    model_parameters: dict[str, Any] = Field(default_factory=dict)


class PlanningTaskCheckpoint(PlanningModel):
    plan: CasePlanRecord
    phase: Literal["retrieval", "ready", "invoking", "response", "query", "finished"] = "retrieval"
    catalog: dict[str, Any] = Field(default_factory=dict)
    query_results: list[dict[str, Any]] = Field(default_factory=list)
    validation_feedback: dict[str, Any] | None = None
    next_round: int = Field(default=0, ge=0)
    query_index: int = Field(default=0, ge=0)
    pending_decision: PlanningDecision | None = None
    response_text: str | None = None
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    response_has_tools: bool = False


class CasePlanningTaskRecord(PlanningModel):
    id: str
    project_id: str
    creation_key: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: str
    message: str
    attempt_no: int
    round_count: int
    plan_id: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    deadline_at: datetime | None = None
    completed_at: datetime | None = None


class CasePlanningTaskPage(PlanningModel):
    items: list[CasePlanningTaskRecord]
    has_more: bool
    limit: int
    offset: int


class CasePlanningTaskClaim(PlanningModel):
    task: CasePlanningTaskRecord
    worker_id: str
    attempt_id: str
    lease_token: str
    remaining_seconds: float
    seed: PlanningTaskSeed
    checkpoint: PlanningTaskCheckpoint


class CasePlanningTaskCommand(PlanningModel):
    schema_version: Literal[1] = 1
    task_type: Literal["case_planning"] = "case_planning"
    task_id: str
    project_id: str
