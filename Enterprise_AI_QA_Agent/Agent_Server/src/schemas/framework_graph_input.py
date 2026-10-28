"""Immutable input supplied to a persisted Deep Agents graph invocation.

No upstream counterpart (checked: LangGraph checkpoints, Deep Agents runtime
adapter, plan v3 §3/§5.1): a checkpoint records graph state after admission,
while the application must retain the exact initial input it authorized.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash
from src.schemas.runtime_binding import RuntimeBinding
from src.schemas.framework_tool_projection import TOOL_PROJECTION_VERSION_KEY
from src.schemas.framework_cognitive_result import STRUCTURED_OUTPUT_VERSION_KEY, require_structured_output_version


RESULT_PROJECTION_VERSION_KEY = "framework_result_projection_version"
TERMINAL_EVENT_PREPARATION_KEY = "framework_terminal_event_preparation"


class FrameworkTerminalEventPreparation(BaseModel):
    """Frozen intent rules; checkpoint time is not an application commit time."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    timestamp_source: Literal["native_checkpoint.ts"]
    pilot_stage: Literal["DA-E1", "DA-E2", "DA-E3", "DA-E4"]
    adapter_stage: Literal["DA-E1", "DA-E2", "DA-E3", "DA-E4"]

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Terminal event preparation requires its explicit integer version")
        return value


class FrameworkProjectionIdentity(BaseModel):
    """Reserved before invocation, not a claim about graph completion time."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding: RuntimeBinding
    assistant_message_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_version: int = Field(ge=1)
    snapshot_created_at: datetime


class FrameworkRuntimePreparation(BaseModel):
    """Application inputs absent from native checkpoints; grants no recovery."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: FrameworkProjectionIdentity
    initial_state: dict[str, Any]


class _FrameworkGraphInputFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    runtime_kind: Literal["deepagents"] = "deepagents"
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    model_key: str = Field(min_length=1)
    checkpoint_namespace: str | None = None
    system_prompt: str
    messages: list[dict[str, Any]]
    framework_messages: list[dict[str, Any]]
    context: dict[str, Any]
    business_tool_keys: list[str]
    interrupt_tool_keys: list[str]
    feature_flags: dict[str, Any]
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_digest(self):
        payload = self.model_dump(mode="json", exclude={"asset_sha256"})
        if self.asset_sha256 != approval_value_hash(payload):
            raise ValueError("Graph input asset digest does not match its frozen contents")
        version = self.feature_flags.get("tool_projection_version")
        if "tool_projection_version" in self.feature_flags and (
            type(version) is not int or version not in {1, 2, 3} or self.schema_version != 2):
            raise ValueError("Tool projection material requires its supported prepared graph format")
        if "verification_projection_version" in self.feature_flags and (
            type(self.feature_flags["verification_projection_version"]) is not int
            or self.feature_flags["verification_projection_version"] != 1 or self.schema_version != 2):
            raise ValueError("Verification preparation requires its supported prepared graph format")
        if "result_projection_version" in self.feature_flags and (
            type(self.feature_flags["result_projection_version"]) is not int
            or self.feature_flags["result_projection_version"] != 1 or self.schema_version != 2):
            raise ValueError("Result projection requires its supported prepared graph format")
        if "terminal_event_projection_version" in self.feature_flags and (
            type(self.feature_flags["terminal_event_projection_version"]) is not int
            or self.feature_flags["terminal_event_projection_version"] != 1 or self.schema_version != 2):
            raise ValueError("Terminal event projection requires its supported prepared graph format")
        if "structured_output_version" in self.feature_flags:
            if require_structured_output_version(self.feature_flags["structured_output_version"]) is None:
                raise ValueError("Structured output requires its explicit selected version")
            if self.schema_version != 2:
                raise ValueError("Structured output requires its supported prepared graph format")
        return self

    @property
    def required_message_stage_version(self):
        version = self.feature_flags.get("tool_projection_version")
        if "structured_output_version" in self.feature_flags:
            return 24
        if "terminal_event_projection_version" in self.feature_flags:
            return 21
        if "result_projection_version" in self.feature_flags:
            return 20
        if "verification_projection_version" in self.feature_flags:
            return 19
        return {1: 13, 2: 14, 3: 15}.get(version, 12 if self.schema_version == 2 else 1)


class FrameworkGraphInputAsset(_FrameworkGraphInputFields):
    schema_version: Literal[1] = 1


class FrameworkPreparedGraphInputAsset(_FrameworkGraphInputFields):
    schema_version: Literal[2] = 2
    runtime_preparation: FrameworkRuntimePreparation

    @model_validator(mode="after")
    def verify_preparation(self):
        binding = self.runtime_preparation.identity.binding
        state = self.runtime_preparation.initial_state
        if (binding.session_id != self.session_id or binding.turn_id != self.turn_id
            or binding.checkpoint_namespace != self.checkpoint_namespace
            or state.get("session_id") != self.session_id or state.get("turn_id") != self.turn_id
            or state.get("trace_id") != self.trace_id or state.get("selected_model_key") != self.model_key
            or (state.get("context_bundle") or {}).get("runtime_binding") != binding.model_dump(mode="json")):
            raise ValueError("Runtime preparation differs from its frozen graph binding or initial state")
        version = (state.get("context_bundle") or {}).get(TOOL_PROJECTION_VERSION_KEY)
        if version != self.feature_flags.get("tool_projection_version") or (version is not None and type(version) is not int):
            raise ValueError("Tool projection material format differs from its frozen runtime preparation")
        from src.schemas.framework_verification_projection import FrameworkVerificationPreparation, VERIFICATION_PREPARATION_KEY

        context = state.get("context_bundle") or {}
        if STRUCTURED_OUTPUT_VERSION_KEY in context or "structured_output_version" in self.feature_flags:
            version = require_structured_output_version(context.get(STRUCTURED_OUTPUT_VERSION_KEY))
            if version is None or version != self.feature_flags.get("structured_output_version"):
                raise ValueError("Structured output differs from its original graph format")
        result_version = context.get(RESULT_PROJECTION_VERSION_KEY)
        if RESULT_PROJECTION_VERSION_KEY in context or "result_projection_version" in self.feature_flags:
            if (type(result_version) is not int or result_version != 1
                or self.feature_flags.get("result_projection_version") != result_version):
                raise ValueError("Result projection differs from its original graph format")
        if VERIFICATION_PREPARATION_KEY in context or "verification_projection_version" in self.feature_flags:
            if self.feature_flags.get("verification_projection_version") != 1 or VERIFICATION_PREPARATION_KEY not in context:
                raise ValueError("Verification preparation differs from its original graph format")
            FrameworkVerificationPreparation.model_validate(context[VERIFICATION_PREPARATION_KEY]).require_context(context,
                session_id=self.session_id, turn_id=self.turn_id, trace_id=self.trace_id)
        if TERMINAL_EVENT_PREPARATION_KEY in context or "terminal_event_projection_version" in self.feature_flags:
            if (self.feature_flags.get("terminal_event_projection_version") != 1
                or TERMINAL_EVENT_PREPARATION_KEY not in context or result_version != 1):
                raise ValueError("Terminal event preparation differs from its original graph format")
            FrameworkTerminalEventPreparation.model_validate(context[TERMINAL_EVENT_PREPARATION_KEY])
        return self


class FrameworkApprovalGraphInput(BaseModel):
    """Original per-invocation preparation, not a decision or recovery proof."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    owner_epoch: int = Field(ge=1, strict=True)
    approval_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    continue_from_checkpoint: bool = Field(strict=True)
    graph_input_asset: FrameworkPreparedGraphInputAsset

    @model_validator(mode="after")
    def verify_invocation_boundary(self):
        events = self.graph_input_asset.runtime_preparation.initial_state.get("event_log", [])
        if not isinstance(events, list) or not events or not isinstance(events[-1], dict):
            raise ValueError("Approval graph preparation has no original invocation boundary")
        event = events[-1]
        payload = event.get("payload", {})
        if (not isinstance(payload, dict) or event.get("type") != "turn.resumed" or payload.get("approval_id") != self.approval_id
            or payload.get("decision_id") != self.decision_id
            or payload.get("continue_from_checkpoint") is not self.continue_from_checkpoint):
            raise ValueError("Approval graph preparation differs from its original invocation boundary")
        return self


def parse_graph_input_asset(value) -> FrameworkGraphInputAsset | FrameworkPreparedGraphInputAsset:
    if isinstance(value, _FrameworkGraphInputFields):
        value = value.model_dump(mode="json")
    selected = FrameworkPreparedGraphInputAsset if value.get("schema_version") == 2 else FrameworkGraphInputAsset
    return selected.model_validate(value)


def graph_input_asset(
    *,
    session_id: str,
    turn_id: str,
    trace_id: str,
    model_key: str,
    checkpoint_namespace: str | None,
    system_prompt: str,
    messages: list[dict[str, Any]],
    framework_messages: list[dict[str, Any]],
    context: dict[str, Any],
    business_tool_keys: list[str],
    interrupt_tool_keys: list[str],
    feature_flags: dict[str, Any],
    runtime_preparation: FrameworkRuntimePreparation | None = None,
) -> FrameworkGraphInputAsset | FrameworkPreparedGraphInputAsset:
    payload = {
        "schema_version": 1,
        "runtime_kind": "deepagents",
        "session_id": session_id,
        "turn_id": turn_id,
        "trace_id": trace_id,
        "model_key": model_key,
        "checkpoint_namespace": checkpoint_namespace,
        "system_prompt": system_prompt,
        "messages": messages,
        "framework_messages": framework_messages,
        "context": context,
        "business_tool_keys": business_tool_keys,
        "interrupt_tool_keys": interrupt_tool_keys,
        "feature_flags": feature_flags,
    }
    if runtime_preparation is not None:
        payload.update(schema_version=2, runtime_preparation=runtime_preparation.model_dump(mode="json"))
    # Fail before persistence if an application value cannot be represented in
    # the durable JSON contract. Do not stringify arbitrary objects silently.
    payload = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return parse_graph_input_asset({**payload, "asset_sha256": approval_value_hash(payload)})
