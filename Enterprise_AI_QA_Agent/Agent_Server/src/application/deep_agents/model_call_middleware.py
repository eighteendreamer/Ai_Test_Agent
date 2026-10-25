"""Explicit policy wiring for main, named subagent and summary model calls."""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.internal_call_transformer import internal_call_metadata
from langchain.agents.structured_output import AutoStrategy, ProviderStrategy, ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import get_buffer_string
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.runtime import get_runtime
from deepagents.backends import StateBackend
from src.application.model_clients.http_observation import (
    current_provider_call_observation, create_observed_http_client, observe_provider_call,
)
from deepagents.middleware.summarization import (
    DEEPAGENTS_DEFAULT_SUMMARY_PROMPT, SummarizationMiddleware, compute_summarization_defaults,
)


class FrameworkModelCallMiddleware(AgentMiddleware):
    def __init__(self, *, policy, owner, model_key, branch_name=None):
        self._policy, self._owner, self._model_key, self._branch_name = policy, owner, model_key, branch_name

    async def awrap_model_call(self, request, handler):
        # An application attempt represents one provider request. A global
        # LangChain cache hit must not be accounted as a new physical request.
        request = request.override(model=request.model.model_copy(update={"cache": False}))
        messages = ([request.system_message] if request.system_message is not None else []) + list(request.messages)
        options = {**request.model_settings, "tool_choice": request.tool_choice}
        if request.response_format is not None:
            strategy = request.response_format
            if isinstance(strategy, ProviderStrategy):
                options["structured_output"] = strategy.to_model_kwargs()
            else:
                normalized = (strategy if isinstance(strategy, ToolStrategy) else
                    ToolStrategy(strategy.schema if isinstance(strategy, AutoStrategy) else strategy))
                options["structured_output"] = {"strategy": type(strategy).__name__, "schemas": [
                    {"name": spec.name, "description": spec.description, "schema": spec.json_schema}
                    for spec in normalized.schema_specs]}
        execution_info = request.runtime.execution_info
        active = current_provider_call_observation()
        context = (None if active is not None else
            observe_provider_call(f"graph:{self._owner.task_id}:{execution_info.checkpoint_ns}", self._model_key))
        if context is None:
            return await self._policy.invoke(owner=self._owner, model=request.model, model_key=self._model_key,
                execution_info=execution_info, branch_name=self._branch_name,
                purpose="subagent" if self._branch_name is not None else "agent", messages=messages,
                tools=[convert_to_openai_tool(tool) for tool in request.tools], options=options,
                send=lambda: handler(request))
        with context:
            http_client = create_observed_http_client()
            try:
                # ChatOpenAI is the only pinned graph model with an official
                # per-instance async HTTP client field. Other transports keep
                # their SDK-native construction until their documented client
                # injection is wired and tested.
                if http_client is not None and hasattr(request.model, "http_async_client"):
                    model = request.model
                    updates = {"http_async_client": http_client}
                    from openai import AsyncOpenAI

                    # ChatOpenAI keeps the provider settings under its public
                    # Pydantic fields.  ``timeout``/``base_url`` are not
                    # fields on this version; the corresponding names are
                    # ``request_timeout``/``openai_api_base``.  Rebuilding
                    # both the root client and the chat resource is required:
                    # changing only ``http_async_client`` leaves the already
                    # constructed SDK resource bound to the old transport.
                    api_key = model.openai_api_key
                    if hasattr(api_key, "get_secret_value"):
                        api_key = api_key.get_secret_value()
                    root = AsyncOpenAI(
                        api_key=api_key,
                        organization=model.openai_organization,
                        base_url=model.openai_api_base,
                        timeout=model.request_timeout,
                        default_headers=model.default_headers,
                        default_query=model.default_query,
                        max_retries=model.max_retries or 0,
                        http_client=http_client,
                    )
                    updates.update({"root_async_client": root,
                        "async_client": root.chat.completions})
                    request = request.override(model=model.model_copy(update=updates))
                return await self._policy.invoke(owner=self._owner, model=request.model, model_key=self._model_key,
                    execution_info=execution_info, branch_name=self._branch_name,
                    purpose="subagent" if self._branch_name is not None else "agent", messages=messages,
                    tools=[convert_to_openai_tool(tool) for tool in request.tools], options=options,
                    send=lambda: handler(request))
            finally:
                if http_client is not None:
                    await http_client.aclose()


def governed_summarization(*, policy, owner, model, model_key, backend, branch_name=None):
    """Replace the official same-name slot using its custom summarizer API.

    LangChain 1.4.6 defaults to model.with_retry inside summarization. A custom
    callable receives the officially trimmed history and owns each actual
    request explicitly, avoiding that implicit retry loop.
    """
    async def summarize(messages):
        prompt = DEEPAGENTS_DEFAULT_SUMMARY_PROMPT.format(messages=get_buffer_string(messages, format="xml"))
        request_messages = [HumanMessage(content=prompt)]
        result = await policy.invoke(owner=owner, model=model, model_key=model_key,
            execution_info=get_runtime().execution_info, branch_name=branch_name, purpose="summary",
            messages=request_messages, tools=[], options={},
            send=lambda: model.model_copy(update={"cache": False}).ainvoke(request_messages,
                config={"metadata": {"lc_source": "summarization", **internal_call_metadata()}}))
        return result.text.strip()

    defaults = compute_summarization_defaults(model)
    return SummarizationMiddleware(model=model, backend=backend, trigger=defaults["trigger"],
        keep=defaults["keep"], truncate_args_settings=defaults["truncate_args_settings"], summarizer=summarize)


def configure_model_policy(kwargs, *, policy, owner, model, model_key):
    """Explicitly wire every exposed model branch; do not assume inheritance."""
    backend = kwargs.get("backend") or StateBackend()

    def middleware(existing, branch_name=None, branch_model=model):
        return [*existing, governed_summarization(policy=policy, owner=owner, model=branch_model,
            model_key=model_key, backend=backend, branch_name=branch_name),
            FrameworkModelCallMiddleware(policy=policy, owner=owner, model_key=model_key, branch_name=branch_name)]

    result = {**kwargs, "middleware": middleware(list(kwargs.get("middleware") or []))}
    if kwargs.get("subagents"):
        result["subagents"] = [{**agent, "middleware": middleware(list(agent.get("middleware") or []),
            branch_name=agent["name"], branch_model=agent.get("model", model))} for agent in kwargs["subagents"]]
    return result
