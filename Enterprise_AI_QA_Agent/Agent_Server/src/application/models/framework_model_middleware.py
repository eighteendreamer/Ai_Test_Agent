"""One official-model request wrapper shared by framework graph builders."""
from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import AutoStrategy, ProviderStrategy, ToolStrategy
from langchain_core.utils.function_calling import convert_to_openai_tool

from src.application.model_clients.http_observation import (
    current_provider_call_observation, create_observed_http_client, observe_provider_call,
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
