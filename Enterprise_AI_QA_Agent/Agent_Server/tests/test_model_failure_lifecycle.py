from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.application.context.context_compaction_service import ContextCompactionService
from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.application.model_clients.base import ProviderClientError
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.runtime.runtime_service import RuntimeService
from src.application.sessions.session_service import SessionService
from src.domain.models import SessionRecord
from src.graph.nodes.finalizer import build_finalizer_node
from src.graph.nodes.model_invoker import build_model_invoker_node
from src.graph.nodes.responder import responder
from src.registry.tools import ToolRegistry
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.session import (
    ChatMessage,
    MessageRole,
    RuntimeMode,
    SessionMode,
    SessionStatus,
)


def _model_config(**changes) -> ModelConfigRecord:
    return ModelConfigRecord(
        key="test-model",
        name="test-model",
        provider="openai",
        transport="openai_chat_completions",
        model_id="test-model",
        api_base_url="https://model.example.test/v1",
        **changes,
    )


async def _invoke_failure(mode: str, monkeypatch: pytest.MonkeyPatch):
    class MissingRegistry:
        def get_runtime_config(self, key):
            raise KeyError(key)

    class ConfiguredRegistry:
        def __init__(self, config):
            self.config = config

        def get_runtime_config(self, key):
            return self.config

    class FailingAuth:
        async def get_token(self, config):
            raise RuntimeError("PRIVATE_AUTH_ERROR")

    class FailingProvider:
        async def invoke(self, config, api_key, request):
            raise ProviderClientError(
                "PRIVATE_PROVIDER_ERROR",
                status_code=503,
                response_body="PRIVATE_PROVIDER_BODY",
            )

    if mode == "missing_active_model":
        registry = MissingRegistry()
        auth = None
    elif mode == "auth_error":
        registry = ConfiguredRegistry(_model_config(auth_type="oauth2"))
        auth = FailingAuth()
    elif mode == "missing_api_key":
        registry = ConfiguredRegistry(_model_config())
        auth = None
    else:
        registry = ConfiguredRegistry(_model_config(api_key="test-key"))
        auth = None
        monkeypatch.setattr(
            "src.application.models.model_runtime_service.resolve_client",
            lambda config, timeout_seconds: FailingProvider(),
        )
    runtime = ModelRuntimeService(
        registry,
        SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=30)),
        oauth_token_service=auth,
    )
    return await runtime.invoke(
        "test-model",
        ModelInvocationRequest(system_prompt="Test", messages=[{"role": "user", "content": "Run"}]),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_active_model", "auth_error", "missing_api_key", "http_error"])
async def test_model_failure_does_not_complete_assistant_turn(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await _invoke_failure(mode, monkeypatch)
    assert result.response_summary["mode"] == mode
    assert "PRIVATE_AUTH_ERROR" not in result.text
    assert "PRIVATE_PROVIDER_ERROR" not in result.text
    assert "PRIVATE_PROVIDER_BODY" not in result.text

    state = {
        "session_id": "session-model-failure",
        "turn_id": "turn-model-failure",
        "trace_id": "trace-model-failure",
        "mode_key": "default",
        "selected_agent_key": "test-agent",
        "selected_model_key": "test-model",
        "selected_model_name": "test-model",
        "selected_model_provider": "openai",
        "available_tool_keys": [],
        "model_visible_tool_keys": [],
        "allowed_tool_keys": [],
        "approval_required_tool_keys": [],
        "system_prompt": "Test",
        "runtime_messages": [{"role": "user", "content": "Run"}],
        "system_prompt_sections": [],
        "runtime_message_sections": [],
        "context_bundle": {},
        "turn_token_usage": {},
        "loop_iteration": 0,
        "final_response": "",
        "pending_turn": {},
        "continue_loop": False,
        "termination_reason": "",
        "control_state": "running",
        "event_log": [],
    }

    class RuntimeStub:
        async def invoke(self, model_key, request):
            return result

    state = await build_model_invoker_node(RuntimeStub(), ToolRegistry())(state)
    assert any(event["type"] == "model.invocation_failed" for event in state["event_log"])
    assert not any(event["type"] == "model.response_received" for event in state["event_log"])
    state = await build_finalizer_node(model_runtime_service=None)(state)
    state = responder(state)
    assert state["termination_reason"] == "interrupted"
    assert not any(event["type"] == "graph.execution_completed" for event in state["event_log"])

    runtime_service = RuntimeService.__new__(RuntimeService)
    runtime_service._build_pending_turn = lambda state, stage: {"latest_execution_stage": stage}
    runtime_service._convert_model_interruption_to_resumable(state)
    assert state["control_state"] == "interrupted"
    assert state["pending_turn"]["latest_execution_stage"] == "interrupted"
    assert state["interrupt_reason"] == mode
    assert any(event["type"] == "model.invocation_interrupted" for event in state["event_log"])

    session_service = SessionService.__new__(SessionService)
    turn = SimpleNamespace(
        state=state,
        approvals=[],
        snapshot=SimpleNamespace(stage="interrupted"),
        pending_turn=state["pending_turn"],
    )
    assert session_service._session_status_from_runtime_result(turn) == SessionStatus.interrupted
    assert session_service._control_state_from_runtime_result(turn) == "interrupted"


@pytest.mark.asyncio
async def test_failed_model_result_is_not_saved_as_context_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _invoke_failure("missing_api_key", monkeypatch)

    class RuntimeStub:
        def get_default_model_config(self):
            return _model_config()

        async def invoke(self, model_key, request):
            return result

    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-model-failure",
        title="Failure",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        created_at=now,
        updated_at=now,
        messages=[
            ChatMessage(id=str(index), role=MessageRole.user, content=f"message {index}", created_at=now)
            for index in range(60)
        ],
        metadata={"context_usage": {"prompt_tokens": 100000, "context_window": 128000}},
    )
    service = ContextCompactionService(RuntimeStub(), max_tail_messages=24)
    assert await service.maybe_compact(session) is None
    assert "context_summary" not in session.metadata


@pytest.mark.parametrize("mode", ["missing_active_model", "auth_error", "missing_api_key", "http_error"])
def test_old_model_failure_message_is_not_replayed_to_model(mode: str) -> None:
    message = ChatMessage(
        id="old-error",
        role=MessageRole.assistant,
        content="Model request failed",
        created_at=datetime.now(UTC),
        metadata={"response_mode": mode},
    )
    assert TranscriptHygieneService().build_runtime_messages([message]) == []
