"""Server-owned inputs and observable outcome of a durable Run replan."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from src.schemas.case_planning import PlanningModel


class RunRevisionPlanningRequest(PlanningModel):
    creation_key: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("重规划原因不能为空")
        return value.strip()


class RunRevisionPlanningInput(PlanningModel):
    run_id: str = Field(min_length=1, max_length=160)
    source_plan_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)
    required_case_versions: dict[str, str] = Field(min_length=1, max_length=200)
    read_case_versions: dict[str, str] = Field(min_length=1, max_length=200)
    context: dict[str, Any]

    def submission(self) -> dict:
        return {"expected_revision": self.expected_revision, "reason": self.reason}


class RunRevisionPlanningOutcome(PlanningModel):
    run_id: str
    status: Literal["pending", "applied", "rejected"]
    revision: int | None = Field(default=None, ge=1)
    message: str
