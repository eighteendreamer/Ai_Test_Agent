"""Select a native-state schema, never a runnable execution or recovery route."""
from __future__ import annotations

from src.schemas.runtime_binding import RuntimeCheckpointSource


def native_graph_schema(graph_input, *, source, saver):
    """Reuse each original builder inside the caller's source-only read scope.

    A kind is a schema selector, not version/artifact compatibility, RunItem
    ownership, result projection or admission authority. Existing Worker and
    Runtime capability checks remain closed for controlled execution.
    """
    source = RuntimeCheckpointSource.model_validate(source)
    if source.binding.runtime_kind == "deepagents":
        from src.application.deep_agents.runtime_adapter import DeepAgentRuntimeAdapter

        return DeepAgentRuntimeAdapter(model_resolver=None).native_schema(graph_input, source=source, saver=saver)
    if source.binding.runtime_kind == "langgraph":
        from src.application.runtime.controlled_case_adapter import ControlledCaseGraphAdapter

        return ControlledCaseGraphAdapter(model_resolver=None, checkpointer_factory=None,
            model_call_policy=None).native_schema(graph_input, source=source, saver=saver)
    raise ValueError("Native schema reader does not support this runtime kind")
