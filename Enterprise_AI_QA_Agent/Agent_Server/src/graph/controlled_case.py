"""Direct, budget-governed model/tool graph for the formal-case migration.

This kernel owns the conditional backedge, not RunItem admission, business
approval authority, application finalization or test verdicts. Its caller must
provide already assembled frozen input, governed tools and the captured saver,
and invoke with sync durability inside the existing root execution scope.
No production route or Worker capability is enabled by importing this module.
"""
from __future__ import annotations

import logging

from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Send

from src.application.models.framework_model_middleware import FrameworkModelCallMiddleware


logger = logging.getLogger(__name__)


def build_controlled_case_graph(*, model, model_key, system_prompt, tools,
        model_call_policy, owner, checkpointer, interrupt_on):
    """Reuse pinned SDK reducers, per-call HITL and the original request policy.

    Reference: LangChain 1.4.6 factory's ModelRequest/ModelResponse and Send
    fan-out; LangGraph 1.2.12 ToolNode hydrates ToolRuntime from channels. Do
    not copy its tool execution, message reducer, interrupt or retry engine.
    Registry handlers are never inputs: tools must be application-governed
    LangChain wrappers. The matching saver is a correlation requirement, not
    proof of current authority; model/tool/saver ports still perform fencing.
    """
    if (owner is None or owner.binding.runtime_kind != "langgraph" or model_call_policy is None
        or checkpointer is None or getattr(checkpointer, "runtime_owner", None) != owner):
        logger.warning("controlled_case_graph_composition_rejected", extra={"error_type": "owner_policy_saver_mismatch"})
        raise ValueError("Controlled case graph requires its LangGraph owner, model policy and captured saver")
    tools = list(tools)
    middleware = FrameworkModelCallMiddleware(policy=model_call_policy, owner=owner, model_key=model_key)
    approval = (HumanInTheLoopMiddleware(interrupt_on=interrupt_on, interrupt_mode="per_call")
        if interrupt_on else None)

    async def invoke(request):
        # Same public chat-model binding/invocation contract as the pinned
        # factory. No structured-output parser, implicit retry or second loop.
        options = dict(request.model_settings)
        if request.tool_choice is not None:
            options["tool_choice"] = request.tool_choice
        bound = (request.model.bind_tools(request.tools, **options) if request.tools else
            request.model.bind(**options) if options else request.model)
        messages = ([request.system_message] if request.system_message is not None else []) + list(request.messages)
        response = await bound.ainvoke(messages)
        if not isinstance(response, AIMessage):
            raise ValueError("Controlled case model did not return an AIMessage")
        return ModelResponse(result=[response])

    async def call_model(state: MessagesState, runtime: Runtime):
        request = ModelRequest(model=model, tools=tools, system_message=SystemMessage(content=system_prompt),
            messages=list(state["messages"]), state=state, runtime=runtime)
        try:
            response = await middleware.awrap_model_call(request, invoke)
            message = response.result[-1]
            # No upstream counterpart (checked: pinned factory.py,
            # graph/message.py, v4 sections 8-9): preserve our settled source
            # identity rather than allow a new admission to rewrite history.
            # add_messages intentionally replaces equal IDs. A fresh model
            # admission must not use that update mechanism to erase an earlier
            # call source (and orphan its ToolMessage). Retain the settled
            # response in the original ledger; never resend or invent a new ID.
            if message.id and any(item.id == message.id for item in state["messages"]):
                raise ValueError("Controlled case model reused a message identity from its input")
        except Exception as exc:
            logger.warning("controlled_case_model_failed", extra={"binding_id": owner.binding.binding_id,
                "error_type": type(exc).__name__})
            raise
        logger.info("controlled_case_model_returned", extra={"binding_id": owner.binding.binding_id,
            "tool_call_count": len(response.result[-1].tool_calls)})
        return {"messages": response.result}

    graph = _compile_controlled_case_graph(call_model, ToolNode(tools, handle_tool_errors=False,
        awrap_tool_call=approval.awrap_tool_call if approval is not None else None), checkpointer)
    logger.info("controlled_case_graph_built", extra={"binding_id": owner.binding.binding_id,
        "tool_count": len(tools), "approval_tool_count": len(interrupt_on)})
    return graph


def build_controlled_case_native_schema(*, source, checkpointer):
    """Same official channels/topology, deliberately no executable actions.

    No upstream counterpart (checked: pinned StateGraph/MessagesState,
    DeepAgentRuntimeAdapter.native_schema, v4 §8.2): this fixed graph has no
    Harness-dependent state channels. Prompt, tools and approvals do not alter
    its schema. Do not resolve a current model or reconstruct governed tools.
    Read material grants neither execution nor application recovery permission.
    """
    if (source.binding.runtime_kind != "langgraph" or checkpointer is None
        or getattr(checkpointer, "runtime_source", None) != source
        or getattr(checkpointer, "runtime_owner", None) is not None):
        raise ValueError("Controlled native schema requires its matching source-only saver")
    return _compile_controlled_case_graph(_deny_native_execution, _deny_native_execution, checkpointer)


async def _deny_native_execution(state: MessagesState):
    raise PermissionError("A native checkpoint schema cannot execute model or tool actions")


def after_model(state):
    # Each call gets its own native task/interrupt. ToolNode reads the
    # source AI message from channels; never inline or reconstruct history.
    calls = state["messages"][-1].tool_calls
    return [Send("tools", [call]) for call in calls] if calls else END


def _compile_controlled_case_graph(model_node, tools_node, checkpointer):
    # Both readers use the SAME pinned SDK builder, not a hand-written list of
    # channel names, a custom reducer or a second checkpoint materializer.
    graph = StateGraph(MessagesState)
    graph.add_node("model", model_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, ["tools", END])
    graph.add_edge("tools", "model")
    return graph.compile(checkpointer=checkpointer)
