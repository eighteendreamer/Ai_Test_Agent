"""Reader-first formal case input, not a RunItem lease or execution permit.

No upstream counterpart (checked: AgentCaseExecutionContext, RunPlanSnapshotIdentity,
ControlledCaseGraphInputAsset, v4 §9/§12): framework checkpoints do not own formal
case versions or the RunItem's original execution attempt. Keep that identity
separate from the AgentTask attempt created by its own claim.
"""
from __future__ import annotations

import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.case_execution import AgentCaseExecutionContext
from src.schemas.controlled_case_graph_input import ControlledCaseGraphInputAsset, parse_controlled_graph_input
from src.schemas.framework_approval import approval_value_hash
from src.schemas.framework_graph_input import FrameworkRuntimePreparation
from src.schemas.framework_transcript import CONTROLLED_CASE_CONTEXT_VERSION_KEY
from src.schemas.run_management import RunPlanSnapshotIdentity


class ControlledCaseTaskInputReference(BaseModel):
    """Delivery identity only; the full original remains in PostgreSQL."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_item_execution_attempt_id: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ControlledCaseTaskInputAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    context: AgentCaseExecutionContext
    run_item_execution_attempt_id: str = Field(min_length=1)
    plan_snapshot_identity: RunPlanSnapshotIdentity
    graph_input: ControlledCaseGraphInputAsset
    asset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled case input requires its explicit integer version")
        return value

    @field_validator("graph_input", mode="before")
    @classmethod
    def read_graph_input(cls, value):
        return parse_controlled_graph_input(value)

    @model_validator(mode="after")
    def verify_original(self):
        if self.asset_sha256 != approval_value_hash(self.model_dump(mode="json", exclude={"asset_sha256"})):
            raise ValueError("Controlled case input digest differs from its frozen contents")
        context = self.context
        if (not all((context.project_id, context.run_id, context.run_item_id, context.plan_id, context.target_key))
            or context.plan_id != self.plan_snapshot_identity.plan_id):
            raise ValueError("Controlled case input differs from its claimed plan identity")
        expected_turn = str(uuid5(NAMESPACE_URL,
            f"case-run:{context.run_id}:{context.run_item_id}:{self.run_item_execution_attempt_id}"))
        if self.graph_input.binding.turn_id != expected_turn:
            raise ValueError("Controlled case input differs from its original RunItem execution turn")
        targets = [step for step in context.steps if step.item.key == context.target_key]
        if (len(targets) != 1 or len({step.item.key for step in context.steps}) != len(context.steps)
            or targets[0].phase != "case" or targets[0].item.kind == "prepare"
            or not targets[0].item.case_id or not targets[0].item.case_version_id or targets[0].content_json is None):
            raise ValueError("Controlled case input requires its unique complete formal target version")
        return self

    @property
    def case_version_id(self) -> str:
        return next(step.item.case_version_id for step in self.context.steps if step.item.key == self.context.target_key)


class ControlledCasePreparedTaskInputAsset(ControlledCaseTaskInputAsset):
    """Original application preparation, not a result or recovery permit."""
    schema_version: Literal[2]
    runtime_preparation: FrameworkRuntimePreparation

    @model_validator(mode="after")
    def verify_preparation(self):
        graph = self.graph_input
        state = self.runtime_preparation.initial_state
        context = state.get("context_bundle") or {}
        if (self.runtime_preparation.identity.binding != graph.binding
            or graph.binding.runtime_version != "v2.0.0"
            or (state.get("session_id"), state.get("turn_id"), state.get("trace_id"), state.get("selected_model_key"))
            != (graph.binding.session_id, graph.binding.turn_id, graph.trace_id, graph.model_key)
            or context.get("runtime_binding") != graph.binding.model_dump(mode="json")
            or state.get("system_prompt") != graph.system_prompt
            or state.get("framework_messages") != graph.framework_messages
            or any(context.get(key) != value for key, value in self.context.runtime_context().items())
            or context.get("attempt_id") != self.run_item_execution_attempt_id
            or context.get("case_execution_managed") is not True or context.get("case_assessment_requested") is not True
            or context.get("trusted_security_runtime_direct_execution") is not False
            or type(context.get(CONTROLLED_CASE_CONTEXT_VERSION_KEY)) is not int
            or context[CONTROLLED_CASE_CONTEXT_VERSION_KEY] != 1
            or context.get("framework_tool_projection_version") != 3
            or context.get("framework_model_protocol") != {
                "provider": graph.binding.model_provider, "transport": graph.binding.model_transport}
            or (state.get("selected_model_provider"), state.get("selected_model_transport"))
                != (graph.binding.model_provider, graph.binding.model_transport)
            or state.get("user_message") != self.context.objective):
            raise ValueError("Controlled runtime preparation differs from its formal input or original graph")
        native = controlled_case_native_input(self.context, self.run_item_execution_attempt_id, graph.binding.turn_id)
        if graph.framework_messages != native or state.get("runtime_messages") != [
            {"role": "user", "content": self.context.objective}]:
            raise ValueError("Controlled runtime preparation requires its formal native source, not Session history")
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from src.schemas.session import ExecutionEvent

        if approval_value_hash([convert_to_openai_tool(tool) for tool in controlled_case_schema_tools(state)]) \
                != approval_value_hash(graph.tool_schemas):
            raise ValueError("Controlled runtime tool descriptors differ from their frozen graph schemas")
        expected = {key: {"allowed_decisions": ["approve", "reject"]}
            for key in state.get("approval_required_tool_keys", [])}
        if graph.model_dump(mode="json")["interrupt_on"] != expected:
            raise ValueError("Controlled runtime approval surface differs from its frozen permissions")
        raw_events = state.get("event_log", [])
        if (not isinstance(raw_events, list) or any(not isinstance(event, dict)
            or set(event) != {"id", "timestamp", "type", "payload"}
            or not event["id"] or not event["timestamp"] for event in raw_events)):
            raise ValueError("Controlled runtime preparation requires complete original event identities and timestamps")
        events = [ExecutionEvent(session_id=graph.binding.session_id, **event) for event in raw_events]
        if (not events or len({event.id for event in events}) != len(events)
            or any(event.session_id != graph.binding.session_id
                or event.payload.get("turn_id") != graph.binding.turn_id
                or event.payload.get("trace_id") != graph.trace_id or event.payload.get("step") != index
                for index, event in enumerate(events, 1))):
            raise ValueError("Controlled runtime preparation requires its original ordered event intents")
        return self


class ControlledCaseResultPreparation(BaseModel):
    """Explicit pre-execution rules, never a completed-result/recovery receipt."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    timestamp_source: Literal["native_checkpoint.ts"]
    output_contract: Literal["current_final_ai_text"]

    @model_validator(mode="before")
    @classmethod
    def require_version(cls, value):
        if isinstance(value, dict) and type(value.get("schema_version")) is not int:
            raise ValueError("Controlled result preparation requires its explicit integer version")
        return value


class ControlledCaseResultTaskInputAsset(ControlledCasePreparedTaskInputAsset):
    """v3 selects deterministic result projection; v1/v2 keep their old meaning."""
    schema_version: Literal[3]
    result_preparation: ControlledCaseResultPreparation

    @model_validator(mode="after")
    def require_initial_result_gap(self):
        state = self.runtime_preparation.initial_state
        if (any(state.get(key) for key in ("tool_results", "tool_messages", "pending_turn", "pending_approvals",
                "final_response", "model_response_text", "model_response_summary"))
            or any(event["type"] == "runtime.turn_completed" for event in state["event_log"])):
            raise ValueError("Controlled result preparation cannot replace prior result or pending execution facts")
        return self


def controlled_case_native_input(context, attempt_id, turn_id):
    """Protocol user role denotes formal input, NOT a persisted business user row."""
    from langchain_core.messages import HumanMessage
    from src.application.deep_agents.framework_transcript import serialize_messages

    return serialize_messages([HumanMessage(id=f"controlled-case:{turn_id}", content=context.objective,
        additional_kwargs={"source": "test_run.agent_execution", "run_id": context.run_id,
            "run_item_id": context.run_item_id, "run_item_execution_attempt_id": attempt_id})])


def controlled_case_schema_tools(state):
    """Use the original governed adapter, including formal action schema metadata."""
    from src.application.model_adapters.tool_adapter import LangChainToolAdapter
    from src.schemas.agent import ToolDescriptor

    context = state.get("context_bundle") or {}
    raw = context.get("framework_tool_descriptors")
    if not isinstance(raw, list):
        raise ValueError("Controlled runtime preparation has no frozen tool descriptors")
    descriptors = [ToolDescriptor.model_validate(item) for item in raw]
    visible = state.get("model_visible_tool_keys")
    if [item.key for item in descriptors] != visible or len(set(visible)) != len(visible):
        raise ValueError("Controlled runtime tool descriptors differ from their visible surface")

    async def schema_only(*args):
        raise RuntimeError("Controlled schema-only tool is not executable")

    return LangChainToolAdapter().to_governed_tools(descriptors, invoke=schema_only, context_bundle=context)


def parse_controlled_case_task_input(value) -> ControlledCaseTaskInputAsset:
    # Frozen models retain mutable nested context. Never trust a prebuilt
    # object or retain caller JSON aliases across a later awaited boundary.
    if isinstance(value, ControlledCaseTaskInputAsset):
        value = value.model_dump(mode="json")
    try:
        value = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Controlled case input contains non-persistable execution data") from exc
    reader = {2: ControlledCasePreparedTaskInputAsset, 3: ControlledCaseResultTaskInputAsset}.get(
        value.get("schema_version") if isinstance(value, dict) else None, ControlledCaseTaskInputAsset)
    return reader.model_validate(value)
