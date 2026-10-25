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
from src.schemas.framework_approval import NativeToolInterrupt, approval_value_hash


logger = logging.getLogger(__name__)


async def collect_pending_tool_interrupts(agent, saver, config) -> list[NativeToolInterrupt]:
    snapshot = await agent.aget_state(config, subgraphs=True)
    identities: list[NativeToolInterrupt] = []
    settled: set[str] = set()

    async def visit(state):
        for task in state.tasks:
            if isinstance(task.state, StateSnapshot):
                await visit(task.state)
                continue
            if not task.interrupts:
                continue
            if task.name != "tools" or task.state is not None or len(task.interrupts) != 1:
                raise FrameworkApprovalRecoveryRequired("Unsupported pending tool interrupt task")
            location = state.config["configurable"]
            checkpoint_id = location.get("checkpoint_id")
            if not checkpoint_id:
                raise FrameworkApprovalRecoveryRequired("Pending tool task has no saved checkpoint")
            rows = await saver.aget_interrupt_task(state.config, task.id)
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
                settled.add(interrupt.id)
                continue
            if resumes:
                raise FrameworkApprovalRecoveryRequired(
                    "Native per-call decision has no settled tool result; reconcile before projection")
            # Official per-call tools fan out from the current model response.
            # Searching older AI messages would alias reused provider call IDs.
            messages = state.values.get("messages", []) if isinstance(state.values, dict) else []
            source = next((item for item in reversed(messages) if isinstance(item, AIMessage)), None)
            calls = [call for call in (source.tool_calls if source is not None else [])
                if call.get("id") == action.get("tool_call_id")]
            if (source is None or not source.id or len(calls) != 1
                or calls[0].get("name") != action.get("name")
                or approval_value_hash(calls[0].get("args")) != approval_value_hash(action.get("args"))):
                raise FrameworkApprovalRecoveryRequired("Pending tool interrupt has no unambiguous source AI call")
            identities.append(NativeToolInterrupt(
                checkpoint_namespace=location.get("checkpoint_ns", ""), checkpoint_id=checkpoint_id,
                graph_task_id=task.id, task_path=pending["task_path"], interrupt_id=interrupt.id,
                resume_index=len(resumes), source_message_id=source.id, tool_call_id=calls[0]["id"],
                tool_name=calls[0]["name"], arguments=calls[0]["args"]))

    try:
        await visit(snapshot)
        expected = {item.id for item in snapshot.interrupts} - settled
        found = [item.interrupt_id for item in identities]
        if len(found) != len(set(found)) or set(found) != expected:
            raise FrameworkApprovalRecoveryRequired("Pending native interrupts cannot be mapped uniquely")
    except FrameworkApprovalRecoveryRequired:
        logger.warning("framework_interrupt_identity_rejected", extra={
            "binding_id": saver.runtime_owner.binding.binding_id})
        raise
    logger.info("framework_interrupt_identities_read", extra={
        "binding_id": saver.runtime_owner.binding.binding_id, "interrupt_count": len(identities)})
    return identities
