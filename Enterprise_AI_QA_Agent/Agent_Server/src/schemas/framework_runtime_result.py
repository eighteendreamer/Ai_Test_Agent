"""Original application result and finalization identities, not a graph cursor."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash
from src.schemas.model_config import is_model_failure_summary
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.session import ChatMessage, ExecutionEvent, SessionSnapshot, VerificationResult


class FrameworkRuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output_text: str
    events: list[ExecutionEvent]
    snapshot: SessionSnapshot
    approvals: list[dict[str, Any]]
    state: dict[str, Any]
    tool_messages: list[ChatMessage]
    pending_turn: dict[str, Any]

    @classmethod
    def from_result(cls, result):
        return cls(**{name: getattr(result, name) for name in cls.model_fields})

    def restore(self):
        from src.application.runtime.runtime_service import RuntimeTurnResult

        value = type(self).model_validate_json(self.model_dump_json())
        return RuntimeTurnResult(**{name: getattr(value, name) for name in type(self).model_fields})


class FrameworkRuntimeResultAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    scope: Literal["completed_message_finalization_input"] = "completed_message_finalization_input"
    binding: RuntimeBinding
    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_calls_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_jobs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_result: FrameworkRuntimeResult
    assistant_message_id: str = Field(min_length=1)
    user_message: str
    verification_results: list[VerificationResult]
    projection_at: datetime
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify(self):
        result, binding = self.runtime_result, self.binding
        if (result.snapshot.session_id != binding.session_id
            or result.snapshot.stage != "completed" or result.approvals or result.pending_turn
            or result.state.get("turn_id") != binding.turn_id
            or result.snapshot.graph_state.get("turn_id") != binding.turn_id
            or (result.state.get("context_bundle") or {}).get("runtime_binding") != binding.model_dump(mode="json")
            or (result.snapshot.graph_state.get("context_bundle") or {}).get("runtime_binding") != binding.model_dump(mode="json")
            or result.state.get("termination_reason") == "interrupted"
            or is_model_failure_summary(result.state.get("model_response_summary") or {})
            or result.state.get("control_state") not in {None, "completed"}):
            raise ValueError("Runtime result is not a completed application result for its binding")
        if any(item.session_id != binding.session_id or item.turn_id != binding.turn_id
            for item in self.verification_results):
            raise ValueError("Runtime result verification belongs to another Turn")
        if any(item.session_id != binding.session_id for item in result.events):
            raise ValueError("Runtime result event belongs to another Session")
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Runtime result asset digest does not match its frozen contents")
        return self

    def event_id(self, event_type: str) -> str:
        return str(uuid5(NAMESPACE_URL,
            f"framework-finalization:{self.binding.binding_id}:{self.assistant_message_id}:{event_type}"))
