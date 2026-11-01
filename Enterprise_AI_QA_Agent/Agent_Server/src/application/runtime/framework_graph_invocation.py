"""One invocation/native-audit boundary for the two framework graph builders.

Extracted from DeepAgentRuntimeAdapter: this owns no admission, retry,
reconciliation or application finalization. Outcomes are read material only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.application.deep_agents.native_terminal import NativeTerminalRead
from src.application.deep_agents.result_projection import DeepAgentRuntimeError
from src.schemas.framework_approval import NativeToolInterrupt


@dataclass(frozen=True)
class FrameworkGraphOutcome:
    values: dict[str, Any]
    subagent_events: list[dict[str, Any]]
    native_interrupts: list[NativeToolInterrupt]
    native_terminal: NativeTerminalRead | None
    root_model_budget: dict[str, Any] | None


async def invoke_and_audit_framework_graph(*, agent, invoke_input, invoke_config,
        checkpointer, execution_owner, model_call_policy, execution_deadline,
        invoke_with_events=None) -> FrameworkGraphOutcome:
    """Keep original sync durability, cancellation and strict native checks.

    ``invoke_with_events`` is the existing Deep Agents event transport, not a
    replacement scheduler. The default directly invokes the supplied graph.
    Preserve the old boundary exception type for existing installed callers.
    """
    persistence_options = {"durability": "sync"} if checkpointer is not None else {}
    if invoke_with_events is not None:
        values, events = await invoke_with_events(persistence_options)
    else:
        values = await agent.ainvoke(invoke_input, config=invoke_config, **persistence_options)
        events = []
    # A committed completion/approval wait survives a later clock tick. A
    # graph that swallowed cancellation cannot disarm an already expired timer.
    if execution_deadline is not None and not execution_deadline.expired():
        execution_deadline.reschedule(None)
    native_interrupts, terminal = [], None
    if execution_owner is not None:
        from src.application.deep_agents.native_interrupts import collect_pending_tool_interrupts

        native_interrupts = await collect_pending_tool_interrupts(agent, checkpointer, invoke_config)
        reported_ids = {item.id for item in values.get("__interrupt__", [])}
        if {item.interrupt_id for item in native_interrupts} != reported_ids:
            raise DeepAgentRuntimeError("Returned interrupts disagree with pending native tasks.")
        if not native_interrupts:
            from src.application.deep_agents.native_terminal import collect_native_terminal

            terminal = await collect_native_terminal(agent, checkpointer, invoke_config, returned_values=values)
    root_model_budget = (await model_call_policy.capture_root_budget(execution_owner.binding)
        if model_call_policy is not None else None)
    return FrameworkGraphOutcome(values=values, subagent_events=events, native_interrupts=native_interrupts,
        native_terminal=terminal, root_model_budget=root_model_budget)
