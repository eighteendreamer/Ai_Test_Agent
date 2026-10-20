"""The real SDK HTTP attempt for intent enrichment carries the user turn identity."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from test_model_completion_http import Registry, provider
from src.application.intent.semantic_intent_service import SemanticIntentService
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.orchestration.input_orchestrator_service import InputOrchestratorService
from src.core.config import get_settings
from src.core.request_context import reset_request_context, set_request_context
from src.domain.models import SessionRecord
from src.registry.modes import ModeRegistry
from src.schemas.model_config import ModelConfigRecord
from src.schemas.session import RuntimeMode, SendMessageRequest, SessionMode, SessionStatus


@pytest.mark.asyncio
async def test_semantic_intent_trace_reaches_actual_sdk_http_attempt(caplog: pytest.LogCaptureFixture):
    candidate = {
        "target_kind": "ui",
        "objectives": ["ui_automation"],
        "requested_actions": ["read"],
        "required_capabilities": ["ui.automation"],
        "candidate_mode_key": "ui_automation",
        "confidence": 0.91,
        "needs_clarification": False,
        "evidence": ["local HTTP trace fixture"],
    }
    settings = get_settings().model_copy(deep=True)
    settings.model.langchain_model_adapter_enabled = False
    config = ModelConfigRecord(
        key="trace-fixture",
        name="Trace fixture",
        provider="openai",
        transport="openai_chat_completions",
        model_id="trace-fixture-model",
        api_base_url="",
        api_key="local-fixture-marker",
        max_output_tokens=512,
    )
    session = SessionRecord(
        id="session-semantic-trace",
        title="Semantic trace",
        status=SessionStatus.idle,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="default",
        project_id="project-semantic-trace",
        preferred_model="trace-fixture",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    caplog.set_level(logging.INFO)

    with provider(
        "openai_chat_completions",
        False,
        content_supplier=lambda: candidate,
    ) as (url, sends, _):
        config.api_base_url = url
        runtime = ModelRuntimeService(Registry(config), settings)
        orchestrator = InputOrchestratorService(
            ModeRegistry(), SemanticIntentService(runtime)
        )
        token = set_request_context(
            request_id="request-semantic-trace-http",
            trace_id="trace-semantic-trace-http",
        )
        try:
            execution = await orchestrator.orchestrate_async(
                session,
                SendMessageRequest(
                    content="检查这个应用在不同手机环境中的表现",
                    model_key="trace-fixture",
                ),
            )
        finally:
            reset_request_context(token)

    assert execution.trace_id == "trace-semantic-trace-http"
    assert len(sends) == 1
    assert sends[0]["path"] == "/v1/chat/completions"

    physical = [
        record for record in caplog.records
        if record.message == "model_http_request_attempt_started"
    ]
    started = [
        record for record in caplog.records
        if record.message == "semantic_intent_enrichment_started"
    ]
    completed = [
        record for record in caplog.records
        if record.message == "semantic_intent_enrichment_completed"
    ]
    classified = [
        record for record in caplog.records
        if record.message == "input_intent_classified"
    ]
    assert len(physical) == len(started) == len(completed) == len(classified) == 1
    call_id = started[0].logical_call_id
    assert physical[0].logical_call_id == completed[0].logical_call_id == call_id
    assert all(record.trace_id == "trace-semantic-trace-http" for record in (
        physical[0], started[0], completed[0], classified[0]
    ))
    assert started[0].turn_id == completed[0].turn_id == classified[0].turn_id == execution.turn_id
    assert classified[0].semantic_enriched is True
