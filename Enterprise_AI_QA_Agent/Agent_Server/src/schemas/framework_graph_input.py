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
            type(version) is not int or version != 1 or self.schema_version != 2):
            raise ValueError("Tool projection material requires its supported prepared graph format")
        return self

    @property
    def required_message_stage_version(self):
        return 13 if "tool_projection_version" in self.feature_flags else (12 if self.schema_version == 2 else 1)


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
