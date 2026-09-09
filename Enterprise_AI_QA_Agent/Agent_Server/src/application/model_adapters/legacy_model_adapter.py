from __future__ import annotations

from typing import Any

from src.application.model_adapters.base import ModelPort
from src.application.model_clients.base import ProviderClient
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest, ModelInvocationResult


class LegacyProviderAdapter(ModelPort):
    """Expose the existing ProviderClient contract through ModelPort.

    This is an identity boundary, not a second provider implementation.  The
    current ModelRuntimeService remains responsible for auth, retry/fallback,
    sanitization and observability until the dual-run gate is complete.
    """

    def __init__(self, client: ProviderClient) -> None:
        self._client = client

    async def invoke(
        self,
        config: ModelConfigRecord,
        api_key: str,
        request: ModelInvocationRequest,
    ) -> ModelInvocationResult:
        parsed = await self._client.invoke(config, api_key, request)
        tool_calls = parsed.get("tool_calls") or []
        return ModelInvocationResult(
            text=str(parsed.get("text") or ""),
            tool_calls=tool_calls,
            request_payload={
                "model_key": config.key,
                "model_id": config.model_id,
                "provider": config.provider,
                "transport": config.transport,
                "api_base_url": config.api_base_url,
                "tool_count": len(request.tools),
                "tool_names": [str(item.get("name") or "") for item in request.tools],
            },
            response_summary={
                "mode": "ok",
                "provider": config.provider,
                "transport": config.transport,
                "response_id": parsed.get("response_id"),
                "finish_reason": parsed.get("finish_reason"),
                "stop_reason": parsed.get("stop_reason"),
                "usage": parsed.get("usage") or {},
                "tool_call_count": len(tool_calls),
                "tool_call_names": [getattr(call, "name", "") for call in tool_calls],
            },
            raw_response=parsed.get("raw_response") or {},
        )
