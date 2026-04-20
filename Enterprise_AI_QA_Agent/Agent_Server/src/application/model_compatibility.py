from __future__ import annotations

from typing import Any

from src.application.model_adapters.openai_chat import OpenAIChatCompletionsAdapter
from src.application.model_adapters.provider_profiles import normalize_provider, resolve_provider_profile
from src.application.model_adapters.registry import AdapterRegistry, build_default_adapter_registry
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.tool_runtime import ModelToolCall


class ModelCompatibilityLayer:
    """
    Transitional compatibility facade.

    The runtime now resolves concrete provider adapters through AdapterRegistry,
    but existing services still depend on the previous ModelCompatibilityLayer
    API. Keep this facade thin so we can migrate call sites incrementally
    without breaking the current session/runtime harness.
    """

    def __init__(self, adapter_registry: AdapterRegistry | None = None) -> None:
        self._adapter_registry = adapter_registry or build_default_adapter_registry()
        self._tool_adapter = OpenAIChatCompletionsAdapter()

    def resolve_adapter(self, config: ModelConfigRecord):
        normalized = normalize_provider(config.provider)
        profile = resolve_provider_profile(normalized)
        resolved = config.model_copy(update={"provider": normalized, "transport": profile.transport})
        return self._adapter_registry.resolve(resolved)

    def resolve_profile(self, config: ModelConfigRecord):
        return self.resolve_adapter(config).describe(config)

    def build_request(
        self,
        config: ModelConfigRecord,
        request: ModelInvocationRequest,
        tool_name_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.resolve_adapter(config).build_request(
            config,
            request,
            tool_name_map=tool_name_map,
        )

    def build_headers(self, config: ModelConfigRecord, api_key: str) -> dict[str, str]:
        return self.resolve_adapter(config).build_headers(config, api_key)

    def build_url(self, config: ModelConfigRecord) -> str:
        return self.resolve_adapter(config).build_url(config)

    def parse_response(self, config: ModelConfigRecord, data: dict[str, Any]) -> dict[str, Any]:
        return self.resolve_adapter(config).parse_response(config, data)

    def build_tool_name_map(self, tools: list[dict[str, Any]]) -> dict[str, str]:
        return self._tool_adapter.build_tool_name_map(tools)

    def remap_tool_calls(
        self,
        tool_calls: list[ModelToolCall],
        tool_name_map: dict[str, str] | None,
    ) -> list[ModelToolCall]:
        return self._tool_adapter.remap_tool_calls(tool_calls, tool_name_map)

    def _parse_tool_arguments(self, value: Any) -> dict[str, Any]:
        return self._tool_adapter.parse_tool_arguments(value)
