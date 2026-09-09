from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.application.observability import TraceContext
from src.application.observability.langsmith_adapter import LangSmithObservabilityAdapter, TraceScope
from src.application.security.output_safety_policy import OutputSafetyPolicy
from src.core.config import LangSmithConfig, Settings


def test_langsmith_config_is_safe_by_default() -> None:
    config = LangSmithConfig()

    assert config.enabled is False
    assert config.tracing_mode == "off"
    assert config.capture_inputs is False
    assert config.capture_outputs is False
    assert config.api_key_env == "LANGSMITH_API_KEY"
    assert config.api_key is None


def test_langsmith_config_accepts_secret_from_settings_without_exposing_repr() -> None:
    config = LangSmithConfig(api_key="config-secret")

    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "config-secret"
    assert "config-secret" not in repr(config)


def test_settings_loads_nested_langsmith_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH__ENABLED", "true")
    monkeypatch.setenv("LANGSMITH__TRACING_MODE", "sampled")
    monkeypatch.setenv("LANGSMITH__API_KEY", "nested-secret")
    monkeypatch.setenv("LANGSMITH__WORKSPACE_ID", "workspace-1")

    config = Settings().langsmith

    assert config.enabled is True
    assert config.tracing_mode == "sampled"
    assert config.api_key is not None
    assert config.api_key.get_secret_value() == "nested-secret"
    assert config.workspace_id == "workspace-1"


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


class _RecordingLangSmithClient:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.updated: list[dict[str, object]] = []

    def create_run(self, **kwargs: object) -> None:
        self.created.append(kwargs)

    def update_run(self, **kwargs: object) -> None:
        self.updated.append(kwargs)


class _FailingLangSmithClient:
    def create_run(self, **kwargs: object) -> None:
        raise RuntimeError("observability transport unavailable")

    def update_run(self, **kwargs: object) -> None:
        raise RuntimeError("observability transport unavailable")


class _RootOnlyLangSmithClient(_RecordingLangSmithClient):
    def create_run(self, **kwargs: object) -> None:
        if self.created:
            raise RuntimeError("child observability transport unavailable")
        super().create_run(**kwargs)


def test_trace_transport_failure_does_not_block_business_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="full"),
        client=_FailingLangSmithClient(),
    )
    callback_ran = False

    with adapter.trace_turn(_context()) as scope:
        callback_ran = True
        assert scope is None

    assert callback_ran is True


def test_node_transport_failure_does_not_block_business_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    client = _RootOnlyLangSmithClient()
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="full"),
        client=client,
    )
    callback_ran = False

    with adapter.trace_turn(_context()) as scope:
        assert scope is not None
        with adapter.trace_node(_context(), node_name="router"):
            callback_ran = True

    assert callback_ran is True
    assert [item["name"] for item in client.created] == [
        "enterprise_ai_qa_agent.turn"
    ]


def test_full_trace_posts_root_and_nested_node_with_actual_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The locked LangSmith SDK must emit the root before its child nodes."""
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    client = _RecordingLangSmithClient()
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(
            enabled=True,
            tracing_mode="full",
            capture_outputs=True,
        ),
        client=client,
    )

    with adapter.trace_turn(
        _context(),
        inputs={"api_key": "must-not-send", "message": "safe"},
    ) as scope:
        assert scope is not None
        with adapter.trace_node(
            _context(),
            node_name="router",
            inputs={"password": "must-not-send"},
        ):
            pass
        scope.set_outputs({"summary": "ok", "api_key": "must-not-send"})

    assert [item["name"] for item in client.created] == [
        "enterprise_ai_qa_agent.turn",
        "enterprise_ai_qa_agent.node.router",
    ]
    root_id = client.created[0]["id"]
    assert client.created[1]["parent_run_id"] == root_id
    assert client.updated[-1]["run_id"] == root_id
    assert client.updated[-1]["outputs"] == {
        "summary": "ok",
        "api_key": "[REDACTED]",
    }
    serialized = json.dumps(client.created + client.updated, default=str)
    assert "must-not-send" not in serialized


def test_test_run_item_trace_is_bounded_and_carries_attempt_thread_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    client = _RecordingLangSmithClient()
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="full", capture_outputs=True),
        client=client,
    )

    with adapter.trace_test_run_item(
        _context(),
        run_item_id="item-1",
        attempt_id="attempt-2",
        thread_id="thread-1",
        inputs={"request": "redacted-by-policy"},
    ) as scope:
        assert scope is not None
        scope.set_outputs({"status": "passed", "api_key": "secret"})

    assert client.created[0]["name"] == "enterprise_ai_qa_agent.test_run_item"
    metadata = client.created[0]["extra"]["metadata"]
    assert metadata["run_item_id"] == "item-1"
    assert metadata["attempt_id"] == "attempt-2"
    assert metadata["thread_id"] == "thread-1"
    assert client.updated[-1]["outputs"] == {"status": "passed", "api_key": "[REDACTED]"}


def test_errors_only_posts_failed_root_with_actual_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    client = _RecordingLangSmithClient()
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="errors_only"),
        client=client,
    )

    with pytest.raises(RuntimeError, match="business failure"):
        with adapter.trace_turn(_context()):
            raise RuntimeError("business failure")

    assert [item["name"] for item in client.created] == [
        "enterprise_ai_qa_agent.turn.error"
    ]
    assert client.updated[-1]["run_id"] == client.created[0]["id"]
    assert "business failure" in str(client.updated[-1]["error"])


def test_errors_only_item_trace_keeps_item_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    client = _RecordingLangSmithClient()
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="errors_only"),
        client=client,
    )

    with pytest.raises(RuntimeError, match="item failure"):
        with adapter.trace_test_run_item(_context(), run_item_id="item-1"):
            raise RuntimeError("item failure")

    assert [item["name"] for item in client.created] == [
        "enterprise_ai_qa_agent.test_run_item.error"
    ]


def test_configured_langsmith_api_key_is_used_without_process_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    import langsmith

    captured: dict[str, object] = {}

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setattr(langsmith, "Client", _Client)
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(
            enabled=True,
            tracing_mode="full",
            api_key="config-secret",
            workspace_id="workspace-from-config",
        )
    )

    adapter._get_client(langsmith)

    assert captured["api_key"] == "config-secret"
    assert captured["workspace_id"] == "workspace-from-config"


def test_errors_only_does_not_trace_successful_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    import langsmith

    calls: list[tuple[object, object]] = []

    class _ErrorTrace:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            calls.append((exc_type, exc))
            return False

    monkeypatch.setattr(langsmith, "trace", lambda **kwargs: _ErrorTrace())
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="errors_only"),
        client=object(),
    )

    with adapter.trace_turn(_context()):
        pass

    assert calls == []


def test_errors_only_records_failed_turn_without_replacing_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    import langsmith

    calls: list[tuple[object, object]] = []

    class _ErrorTrace:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            calls.append((exc_type, exc))
            return False

    monkeypatch.setattr(langsmith, "trace", lambda **kwargs: _ErrorTrace())
    adapter = LangSmithObservabilityAdapter(
        LangSmithConfig(enabled=True, tracing_mode="errors_only"),
        client=object(),
    )

    with pytest.raises(RuntimeError, match="boom"):
        with adapter.trace_turn(_context()):
            raise RuntimeError("boom")

    assert len(calls) == 1
    assert calls[0][0] is RuntimeError
    assert isinstance(calls[0][1], RuntimeError)
