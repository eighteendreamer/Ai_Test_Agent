"""Extract pending identities from the pinned official per-call tool graph.

No upstream counterpart (checked: LangGraph 1.2.12 StateSnapshot/PregelTask,
LangChain 1.4.6 per-call HITL, PostgresSaver 3.1.2): business approvals need
both the source AI identity and the persisted task_path, which the public
interrupt payload alone does not contain. Never rebuild either from display text.
"""
from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import StateSnapshot

from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.schemas.framework_approval import FrameworkToolCallIdentity, NativeToolInterrupt, approval_value_hash


logger = logging.getLogger(__name__)


def _source_call(messages, tool_call_id, tool_name, arguments):
    source = next((item for item in reversed(messages) if isinstance(item, AIMessage)), None)
    calls = [call for call in (source.tool_calls if source is not None else []) if call.get("id") == tool_call_id]
    if (source is None or not source.id or len(calls) != 1 or calls[0].get("name") != tool_name
        or approval_value_hash(calls[0].get("args")) != approval_value_hash(arguments)):
        raise FrameworkApprovalRecoveryRequired("Tool task has no unambiguous source AI call")
    return source, calls[0]


def executing_tool_identity(binding, runtime, tool_name, arguments) -> FrameworkToolCallIdentity:
    """Read the injected current call, keeping its parent graph namespace.

    Fixed 1.2.12 ExecutionInfo.checkpoint_ns is task-scoped (see _algo.py).
    Strip only the final tools:<task_id> component; ancestor task IDs identify
    registered child graphs and must remain intact. Never use display history.
    """
    info = runtime.execution_info
    if (info is None or info.thread_id != binding.thread_id or not runtime.tool_call_id
        or info.checkpoint_ns.rsplit("|", 1)[-1] != f"tools:{info.task_id}"):
        raise FrameworkApprovalRecoveryRequired("Executing tool has no matching native runtime identity")
    messages = runtime.state.get("messages", []) if isinstance(runtime.state, dict) else []
    source, call = _source_call(messages, runtime.tool_call_id, tool_name, arguments)
    return FrameworkToolCallIdentity(checkpoint_namespace=info.checkpoint_ns.rpartition("|")[0],
        source_message_id=source.id, tool_call_id=call["id"], tool_name=tool_name, arguments=arguments)


def _pending_tool_tasks(state):
    for task in state.tasks:
        if isinstance(task.state, StateSnapshot):
            yield from _pending_tool_tasks(task.state)
            continue
        if not task.interrupts:
            continue
        if task.name != "tools" or task.state is not None or len(task.interrupts) != 1:
            raise FrameworkApprovalRecoveryRequired("Unsupported pending tool interrupt task")
        if not state.config["configurable"].get("checkpoint_id"):
            raise FrameworkApprovalRecoveryRequired("Pending tool task has no saved checkpoint")
        yield state, task


def _pending_tool_identity(state, task, rows):
    if task.error is not None:
        raise FrameworkApprovalRecoveryRequired("Pending native tool task has an unresolved error; reconcile before projection")
    pending = rows.get("__interrupt__")
    resumes = rows.get("__resume__", {}).get("value", [])
    interrupt = task.interrupts[0]
    matches = [item for item in (pending or {}).get("value", []) if item.id == interrupt.id]
    if (len(matches) != 1 or approval_value_hash(matches[0].value) != approval_value_hash(interrupt.value)
        or not isinstance(resumes, list)):
        raise FrameworkApprovalRecoveryRequired("Pending tool interrupt has no matching durable fact")
    action = interrupt.value
    if not isinstance(action, dict) or action.get("type") != "tool_approval":
        raise FrameworkApprovalRecoveryRequired("Pending interrupt is not a native per-call tool approval")
    # 1.2.12 StateSnapshot.interrupts includes old INTERRUPT writes of
    # completed siblings. A matching native task result is the evidence
    # that this sibling has settled; resume writes alone are not.
    results = task.result.get("messages", []) if isinstance(task.result, dict) else []
    if task.error is None and resumes and any(isinstance(item, ToolMessage)
        and item.tool_call_id == action.get("tool_call_id")
        and item.name == action.get("name") for item in results):
        return None
    if resumes:
        raise FrameworkApprovalRecoveryRequired(
            "Native per-call decision has no settled tool result; reconcile before projection")
    # Official per-call tools fan out from the current model response.
    # Searching older AI messages would alias reused provider call IDs.
    messages = state.values.get("messages", []) if isinstance(state.values, dict) else []
    source, call = _source_call(messages, action.get("tool_call_id"), action.get("name"), action.get("args"))
    location = state.config["configurable"]
    return NativeToolInterrupt(
        checkpoint_namespace=location.get("checkpoint_ns", ""), checkpoint_id=location["checkpoint_id"],
        graph_task_id=task.id, task_path=pending["task_path"], interrupt_id=interrupt.id,
        resume_index=len(resumes), source_message_id=source.id, tool_call_id=call["id"],
        tool_name=call["name"], arguments=call["args"])


def _complete_pending_batch(snapshot, observed):
    identities = [identity for _, identity in observed if identity is not None]
    settled = {task.interrupts[0].id for task, identity in observed if identity is None}
    expected = {item.id for item in snapshot.interrupts} - settled
    found = [item.interrupt_id for item in identities]
    if len(found) != len(set(found)) or set(found) != expected:
        raise FrameworkApprovalRecoveryRequired("Pending native interrupts cannot be mapped uniquely")
    return identities


def collect_pending_tool_interrupts_from_snapshot(snapshot, *, read_task) -> list[NativeToolInterrupt]:
    """Same identity kernel, with caller-owned synchronous durable task reads."""
    return _complete_pending_batch(snapshot, [(task, _pending_tool_identity(state, task,
        read_task(state.config, task.id))) for state, task in _pending_tool_tasks(snapshot)])


async def collect_pending_tool_interrupts(agent, saver, config) -> list[NativeToolInterrupt]:
    snapshot = await agent.aget_state(config, subgraphs=True)
    try:
        observed = [(task, _pending_tool_identity(state, task, await saver.aget_interrupt_task(state.config, task.id)))
            for state, task in _pending_tool_tasks(snapshot)]
        identities = _complete_pending_batch(snapshot, observed)
    except FrameworkApprovalRecoveryRequired:
        logger.warning("framework_interrupt_identity_rejected", extra={
            "binding_id": saver.runtime_owner.binding.binding_id})
        raise
    logger.info("framework_interrupt_identities_read", extra={
        "binding_id": saver.runtime_owner.binding.binding_id, "interrupt_count": len(identities)})
    return identities
