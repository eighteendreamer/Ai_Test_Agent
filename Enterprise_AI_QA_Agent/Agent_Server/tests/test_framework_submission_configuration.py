"""Explicit server policy composes admission without changing default routes."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.application.orchestration.framework_submission_service import FrameworkReleaseSubmissionLoader
from src.core.config import DeepAgentsConfig
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.framework_release import FrameworkReleaseReference


POLICY = {"checkpoint_namespace": "configured:v2", "max_calls": 8, "max_tokens": 100_000,
    "deadline_seconds": 300}
REFERENCE = {"schema_version": 1, "archive_sha256": "b" * 64,
    "size_bytes": 1234, "source_revision": "c" * 40}


def configured(tmp_path, **changes):
    values = dict(enabled=True, checkpoint_enabled=True, release_store=tmp_path,
        release_reference=tmp_path / "reference.json", message_submission_policy=dict(POLICY))
    values.update(changes)
    return DeepAgentsConfig(**values)


def test_default_policy_is_unconfigured_and_never_creates_a_loader():
    config = DeepAgentsConfig()
    assert config.message_submission_policy is None
    assert FrameworkReleaseSubmissionLoader.from_configuration(config, None,
        agent_registry=None, model_registry=None) is None
    assert not config.enabled and not config.checkpoint_enabled


@pytest.mark.parametrize("changes", [{"checkpoint_namespace": ""}, {"checkpoint_namespace": " "},
    {"max_calls": 0}, {"max_calls": 100_001}, {"max_tokens": 0}, {"max_tokens": 1_000_000_000_001},
    {"deadline_seconds": 0}, {"deadline_seconds": float("inf")}, {"deadline_seconds": float("nan")},
    {"future_policy": "controlled-private-input"}])
def test_policy_rejects_invalid_or_unknown_values(tmp_path, changes):
    with pytest.raises(ValueError):
        configured(tmp_path, message_submission_policy={**POLICY, **changes})


@pytest.mark.parametrize("missing", list(POLICY))
def test_policy_requires_all_limits_and_namespace_without_defaults(tmp_path, missing):
    with pytest.raises(ValueError):
        configured(tmp_path, message_submission_policy={key: value for key, value in POLICY.items() if key != missing})


@pytest.mark.parametrize("changes", [{"enabled": False}, {"checkpoint_enabled": False},
    {"release_store": None, "release_reference": None}])
def test_configured_policy_requires_bound_runtime_and_release_capability(tmp_path, changes):
    with pytest.raises(ValueError):
        configured(tmp_path, **changes)


@pytest.mark.parametrize("new_turns", [False, True])
def test_factory_captures_explicit_policy_and_registries_without_reading_materials(tmp_path, new_turns):
    config = configured(tmp_path, new_turns_enabled=new_turns)
    caps = FrameworkWorkerCapabilities("a" * 64, True,
        release_reference=FrameworkReleaseReference.model_validate(REFERENCE))
    agent_registry, model_registry = object(), object()
    loader = FrameworkReleaseSubmissionLoader.from_configuration(config, caps,
        agent_registry=agent_registry, model_registry=model_registry)
    assert loader.release_store == tmp_path and loader.release_reference == caps.release_reference
    assert loader.agent_registry is agent_registry and loader.model_registry is model_registry
    assert loader.mode_keys == frozenset({"code_review"})
    assert {key: getattr(loader, key) for key in POLICY} == POLICY
    config.pilot_mode_keys.append("api_testing")
    assert loader.mode_keys == frozenset({"code_review"})
    assert config.new_turns_enabled is new_turns


@pytest.mark.parametrize("kind", ["missing", "unbound", "unavailable"])
def test_factory_never_infers_a_release_or_capability(tmp_path, kind, caplog):
    config = configured(tmp_path)
    caps = None if kind == "missing" else FrameworkWorkerCapabilities("a" * 64,
        kind != "unavailable", release_reference=None if kind == "unbound" else
        FrameworkReleaseReference.model_validate(REFERENCE))
    with pytest.raises(ValueError, match="Configured framework message submission failed"):
        FrameworkReleaseSubmissionLoader.from_configuration(config, caps, agent_registry=None, model_registry=None)
    records = [row for row in caplog.records if row.message == "framework_submission_configuration_failed"]
    assert len(records) == 1 and records[0].error_type == "ValueError" and records[0].exc_info is None
    assert str(tmp_path) not in caplog.text


def test_nested_environment_loads_explicit_policy_without_reading_the_real_env(monkeypatch, tmp_path):
    from pydantic_settings import BaseSettings
    from src.core.config import Settings

    class Probe(BaseSettings):
        deep_agents: DeepAgentsConfig
        model_config = Settings.model_config

    for key, value in {"ENABLED": "true", "CHECKPOINT_ENABLED": "true", "NEW_TURNS_ENABLED": "false",
        "RELEASE_STORE": str(tmp_path), "RELEASE_REFERENCE": str(tmp_path / "reference.json"),
        **{"MESSAGE_SUBMISSION_POLICY__" + key.upper(): str(value) for key, value in POLICY.items()}}.items():
        monkeypatch.setenv("DEEP_AGENTS__" + key, value)
    config = Probe(_env_file=None, deep_agents={}).deep_agents
    assert config.message_submission_policy.model_dump() == POLICY
    assert not config.new_turns_enabled


def test_real_initializer_composes_the_loader_at_the_original_dispatcher():
    path = Path(__file__).resolve().parents[1] / "src/main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "AgentTaskDispatcher"]
    assert len(calls) == 1
    keywords = {item.arg: ast.unparse(item.value) for item in calls[0].keywords}
    assert keywords.get("framework_submission_loader") == (
        "FrameworkReleaseSubmissionLoader.from_configuration(settings.deep_agents, "
        "app.state.framework_worker_capabilities, agent_registry=agent_registry, model_registry=model_registry)")
