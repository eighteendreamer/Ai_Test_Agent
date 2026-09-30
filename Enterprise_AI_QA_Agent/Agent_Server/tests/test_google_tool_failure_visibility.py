from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.application.model_clients.base import ProviderClientError
from src.application.models.model_runtime_service import ModelRuntimeService
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("response_body", ["invalid generation config", "function calling unsupported"])
async def test_gemini_bad_request_keeps_tool_contract_and_reports_failure(
    response_body: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[ModelInvocationRequest] = []

    class Client:
        async def invoke(self, config, api_key, request):
            requests.append(request)
            if len(requests) == 1:
                raise ProviderClientError("bad request", status_code=400, response_body=response_body)
            return {
                "text": "Unqualified answer without tools",
                "tool_calls": [],
                "response_id": "fallback",
                "finish_reason": "STOP",
                "stop_reason": "STOP",
                "usage": {},
                "raw_response": {},
            }

    client = Client()
    monkeypatch.setattr(
        "src.application.models.model_runtime_service.resolve_client",
        lambda config, timeout_seconds: client,
    )
    config = ModelConfigRecord(
        key="google-model",
        name="Google model",
        provider="google",
        transport="google_gemini_generate_content",
        model_id="gemini-test",
        api_base_url="https://generativelanguage.googleapis.com",
        api_key="test-key",
    )
    service = ModelRuntimeService(
        model_registry=SimpleNamespace(get_runtime_config=lambda key: config),
        settings=SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=30)),
    )
    request = ModelInvocationRequest(
        system_prompt="Use the registered tool to complete this request.",
        messages=[{"role": "user", "content": "Run the check"}],
        tools=[{
            "name": "run_check",
            "description": "Run the check",
            "input_schema": {"type": "object", "properties": {}},
        }],
    )

    result = await service.invoke("google-model", request)

    assert len(requests) == 1
    assert requests[0].tools == request.tools
    assert result.response_summary["mode"] == "http_error"
    assert result.response_summary["status_code"] == 400
    assert result.request_payload["tool_count"] == 1
    assert result.request_payload["tool_names"] == ["run_check"]
    assert "Unqualified answer" not in result.text
