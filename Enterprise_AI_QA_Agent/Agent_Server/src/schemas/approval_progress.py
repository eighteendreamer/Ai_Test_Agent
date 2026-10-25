"""Public approval progress; contains no execution authority or resume payload."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.schemas.tool_job import ToolJobStatus


class FrameworkApprovalProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    approval_id: str
    turn_id: str
    tool_job_id: str | None = None
    tool_key: str
    tool_name: str
    decision: Literal["pending", "approved", "denied"]
    phase: Literal["awaiting_decision", "decision_submitted", "executing", "applying_result", "applied", "recovery_required"]
    observed_at: datetime
    delivery_state: Literal["queued", "sending", "delivered"] | None = None
    tool_status: ToolJobStatus | None = None
    task_status: str | None = None
    outcome: Literal["rejected", "governance_denied", "tool_result"] | None = None
    decided_at: datetime | None = None
    delivered_at: datetime | None = None
    applied_at: datetime | None = None
