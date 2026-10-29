"""Explicit policy wiring for main, named subagent and summary model calls."""
from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.internal_call_transformer import internal_call_metadata
from langchain.agents.structured_output import AutoStrategy, ProviderStrategy, ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import get_buffer_string
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.runtime import get_runtime
from deepagents.backends import StateBackend
from src.application.deep_agents.result_projection import DeepAgentRuntimeError
from src.application.model_clients.http_observation import (
    current_provider_call_observation, create_observed_http_client, observe_provider_call,
)
from deepagents.middleware.summarization import (
    DEEPAGENTS_DEFAULT_SUMMARY_PROMPT, SummarizationMiddleware, compute_summarization_defaults,
)


logger = logging.getLogger(__name__)


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
                elif (
                    http_client is not None
                    and request.model.__class__.__module__.startswith("langchain_google_genai")
                    and hasattr(request.model, "client")
                ):
                    model = request.model
                    request = request.override(
                        model=model.model_copy(
                            update={"client": _build_observed_google_client(model, http_client)}
                        )
                    )
                return await self._policy.invoke(owner=self._owner, model=request.model, model_key=self._model_key,
                    execution_info=execution_info, branch_name=self._branch_name,
                    purpose="subagent" if self._branch_name is not None else "agent", messages=messages,
                    tools=[convert_to_openai_tool(tool) for tool in request.tools], options=options,
                    send=lambda: handler(request))
            finally:
                if http_client is not None:
                    await http_client.aclose()


def _build_observed_google_client(model, http_client):
    """Build the documented google-genai client with its async HTTP client."""
    from google.genai import Client
    from google.genai.types import HttpOptions

    api_key = model.google_api_key
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()
    base_url = model.base_url
    if isinstance(base_url, dict):
        base_url = base_url.get("api_endpoint")
    return Client(
        vertexai=model.vertexai,
        api_key=api_key if not model.credentials else None,
        credentials=model.credentials,
        project=model.project,
        location=model.location,
        http_options=HttpOptions(
            base_url=base_url,
            api_version=model.api_version,
            headers=dict(model.additional_headers or {}),
            client_args=model.client_args,
            async_client_args=model.client_args,
            httpx_async_client=http_client,
        ),
    )


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
        summary = result.text.strip()
        if not summary:
            # The upstream custom-summarizer contract accepts empty strings.
            # Do not let one replace a real history range with an empty body.
            logger.warning("framework_summary_empty", extra={"binding_id": owner.binding.binding_id,
                "model_key": model_key, "branch_name": branch_name, "source_message_count": len(messages)})
            raise DeepAgentRuntimeError("Deep Agents summarization returned an empty summary.")
        return summary

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
