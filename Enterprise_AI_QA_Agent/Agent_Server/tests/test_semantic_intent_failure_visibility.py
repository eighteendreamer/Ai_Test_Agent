from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from src.application.intent.semantic_intent_service import SemanticIntentService
from src.application.model_clients.base import ProviderClientError
from src.schemas.intent import IntentDecision
from src.schemas.model_config import ModelInvocationResult


class _FailingRuntime:
    async def invoke(self, model_key, request):
        raise ProviderClientError("provider unavailable", status_code=503)


class _MalformedRuntime:
    async def invoke(self, model_key, request):
        return SimpleNamespace(text="PRIVATE_RESPONSE not valid JSON")


class _ErrorResultRuntime:
    async def invoke(self, model_key, request):
        return ModelInvocationResult(
            text="PRIVATE_RESPONSE provider failed",
            response_summary={"mode": "http_error", "status_code": 503},
        )


@pytest.mark.parametrize(
    ("runtime", "event_name", "error_type"),
    [
        (_FailingRuntime(), "semantic_intent_enrichment_failed", "ProviderClientError"),
        (_ErrorResultRuntime(), "semantic_intent_enrichment_failed", "http_error"),
        (_MalformedRuntime(), "semantic_intent_response_invalid", "invalid_response"),
    ],
)
def test_semantic_intent_fallback_records_failure_without_input_text(
    runtime,
    event_name: str,
    error_type: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    baseline = IntentDecision(candidate_mode_key=None, confidence=0.2)
    service = SemanticIntentService(runtime)

    with caplog.at_level(
        logging.WARNING,
        logger="src.application.intent.semantic_intent_service",
    ):
        result = asyncio.run(
            service.enrich(
                message="PRIVATE_REQUEST run a review",
                baseline=baseline,
                model_key="model-a",
            )
        )

    assert result is baseline
    matching = [record for record in caplog.records if record.message == event_name]
    assert len(matching) == 1
    assert matching[0].model_key == "model-a"
    assert matching[0].error_type == error_type
    assert "PRIVATE_REQUEST" not in caplog.text
    assert "PRIVATE_RESPONSE" not in caplog.text
