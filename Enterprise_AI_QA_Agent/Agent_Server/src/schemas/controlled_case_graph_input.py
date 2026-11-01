"""Versioned input consumed by the controlled graph, not RunItem authority.

No upstream counterpart (checked: FrameworkGraphInputAsset, pinned MessagesState
and model middleware, v4 §9/§12): native checkpoints do not retain the original
system prompt and tool/approval configuration. The internal frozen invocation
links its digest through official checkpoint metadata, not a business stage.
Formal case admission and application result preparation remain separate.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from langchain.agents.middleware.human_in_the_loop import DecisionType
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from src.application.deep_agents.framework_transcript import deserialize_messages, serialize_messages
from src.schemas.framework_approval import approval_value_hash
from src.schemas.runtime_binding import RuntimeBinding


CONTROLLED_GRAPH_INPUT_KEY = "controlled_graph_input_sha256"


class ControlledToolApprovalConfig(BaseModel):
    """Persistable subset of the pinned InterruptOnConfig; no callable text."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_decisions: list[DecisionType] = Field(min_length=1)
    description: str | None = Field(default=None, exclude_if=lambda value: value is None)


class ControlledCaseGraphInputAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    runtime_kind: Literal["langgraph"]
    binding: RuntimeBinding
    trace_id: str = Field(min_length=1)
    model_key: str = Field(min_length=1)
    system_prompt: str
    framework_messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
    interrupt_on: dict[str, StrictBool | ControlledToolApprovalConfig]
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled graph input requires its explicit integer version")
        return value

    @model_validator(mode="after")
    def verify_original(self):
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Controlled graph input digest differs from its frozen contents")
        if self.binding.runtime_kind != self.runtime_kind:
            raise ValueError("Controlled graph input differs from its runtime binding kind")
        native = deserialize_messages(self.framework_messages)
        if not native or serialize_messages(native) != self.framework_messages:
            raise ValueError("Controlled graph input requires its complete original native messages")
        identities = [message.id for message in native]
        # Same add_messages collision boundary as the controlled model node.
        # Do not generate new IDs on each load or let a reducer erase input.
        if any(not identity for identity in identities) or len(set(identities)) != len(identities):
            raise ValueError("Controlled graph input requires stable distinct message identities")
        names = []
        for schema in self.tool_schemas:
            function = schema.get("function")
            if (schema.get("type") != "function" or not isinstance(function, dict)
                or not isinstance(function.get("name"), str) or not function["name"]):
                raise ValueError("Controlled graph input requires callable tool schemas")
            names.append(function["name"])
        if len(set(names)) != len(names) or not set(self.interrupt_on).issubset(names):
            raise ValueError("Controlled graph input tool names or approval targets are inconsistent")
        return self

    def request(self, *, business_tools, resume=None, continue_from_checkpoint=False):
        """Rebuild values only; a matching schema is not permission to run tools."""
        from src.application.runtime.controlled_case_adapter import ControlledCaseGraphRequest

        tools = list(business_tools)
        # Python equality conflates JSON booleans and numbers (False == 0).
        # Reuse the asset's canonical JSON comparison, including list order.
        if approval_value_hash([convert_to_openai_tool(tool) for tool in tools]) != approval_value_hash(self.tool_schemas):
            raise ValueError("Controlled graph tools differ from their frozen schemas or order")
        original = self.model_dump(mode="json")
        return ControlledCaseGraphRequest(session_id=self.binding.session_id, turn_id=self.binding.turn_id,
            checkpoint_namespace=self.binding.checkpoint_namespace, trace_id=self.trace_id, model_key=self.model_key,
            system_prompt=self.system_prompt, framework_messages=original["framework_messages"],
            business_tools=tools, interrupt_on=original["interrupt_on"],
            resume=resume, continue_from_checkpoint=continue_from_checkpoint)

    def require_checkpoint(self, checkpoint):
        """A content reference, not a signature, owner lease or recovery permit."""
        if checkpoint is None or checkpoint.metadata.get(CONTROLLED_GRAPH_INPUT_KEY) != self.asset_sha256:
            raise ValueError("Controlled checkpoint has no matching original frozen input reference")


def parse_controlled_graph_input(value) -> ControlledCaseGraphInputAsset:
    # frozen=True does not freeze nested lists/dicts. Revalidate at EVERY read
    # and execution boundary instead of trusting an already constructed object.
    if isinstance(value, ControlledCaseGraphInputAsset):
        value = value.model_dump(mode="json")
    try:
        # Any-typed nested values must not retain aliases into mutable caller
        # JSON across provider/model awaits. Never stringify arbitrary objects.
        value = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Controlled graph input contains non-persistable configuration") from exc
    return ControlledCaseGraphInputAsset.model_validate(value)


def freeze_controlled_graph_input(*, binding, request) -> ControlledCaseGraphInputAsset:
    from src.application.runtime.controlled_case_adapter import ControlledCaseGraphRequest

    binding = RuntimeBinding.model_validate(binding)
    if (not isinstance(request, ControlledCaseGraphRequest) or request.resume is not None or request.continue_from_checkpoint
        or (request.session_id, request.turn_id, request.checkpoint_namespace)
        != (binding.session_id, binding.turn_id, binding.checkpoint_namespace)):
        raise ValueError("Controlled graph input requires its original invocation and matching binding")
    payload = {"schema_version": 1, "runtime_kind": "langgraph", "binding": binding.model_dump(mode="json"),
        "trace_id": request.trace_id, "model_key": request.model_key, "system_prompt": request.system_prompt,
        "framework_messages": request.framework_messages,
        "tool_schemas": [convert_to_openai_tool(tool) for tool in request.business_tools], "interrupt_on": request.interrupt_on}
    try:
        # Same explicit JSON/canonical digest as existing graph-input assets.
        payload = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Controlled graph input contains non-persistable configuration") from exc
    return parse_controlled_graph_input({**payload, "asset_sha256": approval_value_hash(payload)})
