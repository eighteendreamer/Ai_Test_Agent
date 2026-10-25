"""An explicitly selected Chat transport must survive SDK model-name inference."""
import pytest

from test_model_completion_http import Registry, provider
from src.application.model_adapters.langchain_model_adapter import LangChainModelAdapter
from src.application.models.model_runtime_service import ModelRuntimeService
from src.core.config import get_settings
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", ["fixture-model", "codex-fixture-model"])
@pytest.mark.parametrize("langchain_enabled", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
async def test_chat_transport_keeps_actual_http_endpoint(model_id, langchain_enabled, streaming):
    with provider("openai_chat_completions", False) as (url, sends, _):
        settings = get_settings().model_copy(deep=True)
        settings.model.langchain_model_adapter_enabled = langchain_enabled
        config = ModelConfigRecord(
            key="fixture", name="fixture", provider="openai",
            transport="openai_chat_completions", model_id=model_id,
            api_base_url=url, api_key="local-fixture-marker", max_output_tokens=512,
        )
        runtime = ModelRuntimeService(Registry(config), settings)
        deltas = []

        async def receive(delta):
            deltas.append(delta)

        async with runtime.stream_handler(receive if streaming else None):
            result = await runtime.invoke("fixture", ModelInvocationRequest(
                system_prompt="Return the fixture response.",
                messages=[{"role": "user", "content": "ping"}],
            ))

        assert len(sends) == 1
        assert sends[0]["path"] == "/v1/chat/completions"
        assert "messages" in sends[0]["body"]
        assert "input" not in sends[0]["body"]
        assert result.response_summary["mode"] == "ok"
        assert result.response_summary["transport"] == "openai_chat_completions"
        if streaming:
            assert "".join(deltas).strip() == result.text


def test_model_exposed_to_other_harnesses_also_pins_chat_transport():
    config = ModelConfigRecord(
        key="fixture", name="fixture", provider="openai",
        transport="openai_chat_completions", model_id="codex-fixture-model",
        api_base_url="http://127.0.0.1:1/v1",
    )
    model = LangChainModelAdapter().build_model(config, "local-fixture-marker")
    assert model.use_responses_api is False
