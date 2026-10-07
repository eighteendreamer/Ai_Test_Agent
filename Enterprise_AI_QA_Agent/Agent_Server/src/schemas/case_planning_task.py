"""Durable, project-scoped planning inputs; no synthetic Agent session."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_serializer

from src.schemas.case_planning import (
    CasePlanRecord, CasePlanningRequest, PlanningDecision, PlanningModel,
)
from src.schemas.model_config import model_invocation_parameters as planning_model_parameters


TERMINAL_PLANNING_TASK_STATUSES = frozenset({"completed", "failed"})
PLANNING_RESPONSE_PROTOCOL = "case-planning-response-completion-v1"


class CasePlanningTaskRequest(CasePlanningRequest):
    creation_key: str = Field(min_length=1, max_length=160)

    def planning_request(self) -> CasePlanningRequest:
        return CasePlanningRequest.model_validate(self.model_dump(exclude={"creation_key"}))


class PlanningTaskSeed(PlanningModel):
    plan: CasePlanRecord
    project_context: dict[str, Any]
    system_prompt: str
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    tool_contracts: dict[str, dict[str, Any]] | None = None
    tool_operations: dict[str, dict[str, Any]] | None = None
    response_protocol: Literal["case-planning-response-completion-v1"] | None = None

    @model_serializer(mode="wrap")
    def preserve_original_seed(self, handler):
        data = handler(self)
        if self.tool_contracts is None:
            data.pop("tool_contracts", None)
        if self.tool_operations is None:
            data.pop("tool_operations", None)
        if self.response_protocol is None:
            data.pop("response_protocol", None)
        return data


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
