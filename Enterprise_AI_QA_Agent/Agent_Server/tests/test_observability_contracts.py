from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.application.observability import TraceContext
from src.application.observability.langsmith_adapter import LangSmithObservabilityAdapter, TraceScope
from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.core.config import LangSmithConfig


def test_langsmith_config_is_safe_by_default() -> None:
    config = LangSmithConfig()

    assert config.enabled is False
    assert config.tracing_mode == "off"
    assert config.capture_inputs is False
    assert config.capture_outputs is False
    assert config.api_key_env == "LANGSMITH_API_KEY"


def test_langsmith_config_validates_mode_sampling_and_timeout() -> None:
    config = LangSmithConfig(
        tracing_mode=" SAMPLED ",
        sample_rate=0.25,
        timeout_ms=1,
    )

    assert config.tracing_mode == "sampled"
    assert config.sample_rate == 0.25
    assert config.timeout_ms == 100

    with pytest.raises(ValidationError):
        LangSmithConfig(tracing_mode="always")
    with pytest.raises(ValidationError):
        LangSmithConfig(sample_rate=1.1)


def test_trace_context_projects_stable_business_metadata() -> None:
    context = TraceContext.from_graph_state(
        {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "trace_id": "trace-1",
            "mode_key": "code_review",
            "selected_agent_key": "reviewer",
            "context_bundle": {
                "parent_trace_id": "parent-1",
                "project_id": "project-1",
                "case_id": "case-1",
                "case_version_id": "case-version-1",
                "suite_version_id": "suite-version-1",
                "test_run_id": "run-1",
            },
        },
        environment="test",
        runtime_version="0.1.0",
    )

    assert context.tags == ["code_review", "reviewer"]
    assert context.metadata() == {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "trace_id": "trace-1",
        "parent_trace_id": "parent-1",
        "project_id": "project-1",
        "mode_key": "code_review",
        "agent_key": "reviewer",
        "case_id": "case-1",
        "case_version_id": "case-version-1",
        "suite_version_id": "suite-version-1",
        "test_run_id": "run-1",
        "environment": "test",
        "runtime_version": "0.1.0",
    }


def test_trace_context_requires_local_correlation_ids() -> None:
    with pytest.raises(ValidationError):
        TraceContext(session_id="", turn_id="turn-1", trace_id="trace-1")


def test_trace_context_deduplicates_tags_without_reordering() -> None:
    context = TraceContext(
        session_id="session-1",
        turn_id="turn-1",
        trace_id="trace-1",
        tags=["runtime", "runtime", "code_review", ""],
    )

    assert context.tags == ["runtime", "code_review"]


def _context() -> TraceContext:
    return TraceContext(session_id="session-1", turn_id="turn-1", trace_id="trace-1")


def test_langsmith_adapter_is_noop_when_disabled() -> None:
    adapter = LangSmithObservabilityAdapter(LangSmithConfig())

    with adapter.trace_turn(_context()) as scope:
        assert scope is None


def test_langsmith_adapter_degrades_when_api_key_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="full")
    )

    with adapter.trace_turn(_context()) as scope:
        assert scope is None


class _FakeRun:
    def __init__(self) -> None:
        self.outputs: list[dict[str, object]] = []
        self.id = "run-1"
        self.trace_id = "trace-1"
        self.dotted_order = "trace-1.0001"

    def get_url(self) -> str:
        return "https://smith.langchain.com/r/trace-1"

    def add_outputs(self, outputs: dict[str, object]) -> None:
        self.outputs.append(outputs)


def test_trace_scope_sanitizes_outputs_without_ending_the_parent_run() -> None:
    fake = _FakeRun()
    TraceScope(fake, OutputSafetyPolicy(), True).set_outputs(
        {"summary": "ok", "api_key": "secret-value"}
    )

    assert fake.outputs == [{"summary": "ok", "api_key": "[REDACTED]"}]


def test_trace_scope_does_not_capture_outputs_by_default() -> None:
    fake = _FakeRun()
    TraceScope(fake, OutputSafetyPolicy()).set_outputs({"summary": "private"})

    assert fake.outputs == []


def test_trace_scope_exposes_only_external_run_reference_fields() -> None:
    fake = _FakeRun()

    assert TraceScope(fake, OutputSafetyPolicy()).reference() == {
        "run_id": "run-1",
        "trace_id": "trace-1",
        "dotted_order": "trace-1.0001",
        "url": "https://smith.langchain.com/r/trace-1",
    }
