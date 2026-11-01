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
        # A completed framework result may return only the current invocation
        # suffix (approval continuation), but that suffix must remain an exact
        # tail of the complete event log captured in state. Without this check,
        # a caller could remove formal events, recompute the asset digest, and
        # still present the asset as a valid recovery input.
        event_log = result.state.get("event_log")
        if event_log is not None:
            if not isinstance(event_log, list) or not event_log or not result.events:
                raise ValueError("Runtime result has no complete event suffix")
            required = {"id", "timestamp", "type", "payload"}
            original = []
            for raw in event_log:
                if not isinstance(raw, dict) or not required.issubset(raw):
                    raise ValueError("Runtime result event log has incomplete original material")
                payload = raw["payload"]
                if not isinstance(payload, dict) or payload.get("turn_id") != binding.turn_id:
                    raise ValueError("Runtime result event log differs from its binding Turn")
                try:
                    item = ExecutionEvent(session_id=binding.session_id, id=raw["id"],
                        timestamp=raw["timestamp"], type=raw["type"], payload=raw["payload"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("Runtime result event log has invalid original material") from exc
                original.append(item.model_dump(mode="json", include={"id", "timestamp", "type", "payload"}))
            returned = [item.model_dump(mode="json", include={"id", "timestamp", "type", "payload"})
                for item in result.events]
            if (original[-1]["type"] != "runtime.turn_completed"
                or len(returned) > len(original) or original[-len(returned):] != returned):
                raise ValueError("Runtime result events differ from its complete original event suffix")
        from src.schemas.framework_cognitive_result import validate_cognitive_completion

        cognitive = [validate_cognitive_completion(state.get("context_bundle") or {}, state.get("model_response_summary") or {})
            for state in (result.state, result.snapshot.graph_state)]
        if cognitive[0] != cognitive[1]:
            raise ValueError("Runtime cognitive result differs from its original snapshot")
        from src.schemas.framework_summary import validate_summary_completion

        summaries = [validate_summary_completion(state.get("context_bundle") or {},
            state.get("model_response_summary") or {}, state.get("framework_messages") or [])
            for state in (result.state, result.snapshot.graph_state)]
        if summaries[0] != summaries[1]:
            raise ValueError("Runtime native summary coverage differs from its original snapshot")
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Runtime result asset digest does not match its frozen contents")
        return self

    def event_id(self, event_type: str) -> str:
        return str(uuid5(NAMESPACE_URL,
            f"framework-finalization:{self.binding.binding_id}:{self.assistant_message_id}:{event_type}"))


class ControlledCaseRuntimeResultAsset(FrameworkRuntimeResultAsset):
    """Same finalization material, formal source instead of a business user row.

    No upstream counterpart (checked: message result asset, original RunItem
    checkpoint, v4 §9/§12.1): a formal case has no message-stage or user row.
    This asset does not authorize finalization, recovery or a test verdict.
    """
    schema_version: Literal[2] = 2
    scope: Literal["completed_controlled_case_finalization_input"] = "completed_controlled_case_finalization_input"
    run_id: str = Field(min_length=1)
    run_item_id: str = Field(min_length=1)
    run_item_execution_attempt_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_formal_source(self):
        context = self.runtime_result.state.get("context_bundle") or {}
        if (self.binding.runtime_kind != "langgraph" or not context.get("case_execution_managed")
            or (context.get("run_id"), context.get("run_item_id"), context.get("attempt_id"))
                != (self.run_id, self.run_item_id, self.run_item_execution_attempt_id)):
            raise ValueError("Controlled result asset differs from its original formal execution")
        return self
