from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


logger = logging.getLogger(__name__)


def is_reserved_tool_argument(key: str) -> bool:
    return key.startswith("_server_") or key == "_p4_approval_scope_hash"


ToolExecutionStatus = Literal["completed", "partial", "failed", "waiting_approval", "waiting_resource", "waiting_children", "denied"]


class ModelToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def discard_untrusted_server_arguments(self) -> ModelToolCall:
        reserved_keys = [key for key in self.arguments if is_reserved_tool_argument(key)]
        if reserved_keys:
            logger.warning(
                "model_tool_reserved_arguments_ignored",
                extra={"tool_key": self.name, "call_id": self.id, "argument_keys": reserved_keys},
            )
            self.arguments = {
                key: value for key, value in self.arguments.items()
                if key not in reserved_keys
            }
        return self


class ToolExecutionRecord(BaseModel):
    call_id: str
    job_id: str | None = None
    tool_key: str
    tool_name: str
    status: ToolExecutionStatus
    summary: str
    trace_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
