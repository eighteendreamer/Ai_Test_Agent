"""Immutable input supplied to a persisted Deep Agents graph invocation.

No upstream counterpart (checked: LangGraph checkpoints, Deep Agents runtime
adapter, plan v3 §3/§5.1): a checkpoint records graph state after admission,
while the application must retain the exact initial input it authorized.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.framework_approval import approval_value_hash


class FrameworkGraphInputAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
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
        return self


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
) -> FrameworkGraphInputAsset:
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
    # Fail before persistence if an application value cannot be represented in
    # the durable JSON contract. Do not stringify arbitrary objects silently.
    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return FrameworkGraphInputAsset(**payload, asset_sha256=approval_value_hash(payload))
