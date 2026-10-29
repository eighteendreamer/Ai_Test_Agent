from __future__ import annotations

import hashlib
import json

import pytest
from types import SimpleNamespace
from pydantic import ValidationError

from src.schemas.agent_task import AgentTaskCommand, AgentTaskCommandV2, parse_agent_task_command
from src.schemas.runtime_binding import RuntimeBinding
from src.application.orchestration.agent_task_dispatcher import AgentTaskDispatcher


def binding(**overrides):
    value = {
        "binding_id": "binding-1",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "runtime_kind": "deepagents",
        "runtime_version": "v2.0.0",
        "state_schema_version": 2,
        "thread_id": "turn-1",
        "checkpoint_namespace": "qa:v2",
        "prompt_version": "prompt-2026-10-25",
        "skill_version": "skills-1",
        "tool_schema_version": "tools-1",
        "model_config_fingerprint": "model-sha256",
        "model_provider": "openai",
        "model_transport": "openai_chat_completions",
        "dependency_lock_sha256": "a" * 64,
    }
    value.update(overrides)
    return RuntimeBinding(**value)


def test_runtime_binding_is_immutable_and_thread_id_is_turn_id():
    value = binding()
    with pytest.raises(ValidationError):
        value.runtime_version = "v2.1.0"
    with pytest.raises(ValidationError):
        binding(thread_id="different-thread")


def test_runtime_binding_fingerprint_is_stable_and_excludes_binding_id():
    first = binding()
    second = binding(binding_id="binding-2")
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert first.model_dump(mode="json")["fingerprint"] == first.fingerprint


def test_legacy_binding_fingerprint_omits_new_optional_protocol_fields():
    value = binding(model_provider=None, model_transport=None)
    payload = {name: getattr(value, name) for name in type(value).model_fields
        if name not in {"binding_id", "created_at", "model_provider", "model_transport"}}
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()
    assert value.fingerprint == expected
    assert binding().fingerprint != value.fingerprint


def test_legacy_binding_wire_round_trip_omits_new_optional_protocol_fields():
    value = binding(model_provider=None, model_transport=None)
    historical = value.model_dump(mode="json", exclude={"model_provider", "model_transport"})
    loaded = RuntimeBinding.model_validate(historical)
    assert loaded.model_dump(mode="json") == historical
    assert "model_provider" not in loaded.model_dump(mode="json")
    assert "model_transport" not in loaded.model_dump(mode="json")


def test_v1_command_rejects_runtime_v2_fields_and_v2_command_requires_binding():
    legacy = {
        "task_id": "task-1", "session_id": "session-1", "turn_id": "turn-1",
        "request_id": "request-1", "trace_id": "trace-1",
        "runtime_binding": binding().model_dump(mode="json"),
    }
    with pytest.raises(ValidationError):
        AgentTaskCommand.model_validate(legacy)

    with pytest.raises(ValidationError):
        AgentTaskCommandV2.model_validate({**legacy, "runtime_binding": None})

    command = AgentTaskCommandV2.model_validate({**legacy, "runtime_binding": binding().model_dump(mode="json")})
    assert command.schema_version == 2
    assert command.runtime_binding.thread_id == command.turn_id


def test_command_parser_is_explicit_and_never_downgrades_v2_to_v1():
    legacy = AgentTaskCommand(
        task_id="task-1", session_id="session-1", turn_id="turn-1",
        request_id="request-1", trace_id="trace-1",
    )
    assert isinstance(parse_agent_task_command(legacy.model_dump(mode="json")), AgentTaskCommand)
    with pytest.raises(ValidationError):
        parse_agent_task_command({
            "schema_version": 2,
            "task_id": "task-1", "session_id": "session-1", "turn_id": "turn-1",
            "request_id": "request-1", "trace_id": "trace-1",
        })


@pytest.mark.asyncio
async def test_legacy_dispatcher_refuses_v2_envelope_before_claiming_task():
    command = AgentTaskCommandV2(
        task_id="task-1", session_id="session-1", turn_id="turn-1",
        request_id="request-1", trace_id="trace-1", runtime_binding=binding(),
    )
    dispatcher = object.__new__(AgentTaskDispatcher)
    with pytest.raises(ValueError, match="v2 worker stream"):
        await dispatcher.handle(SimpleNamespace(payload=command.model_dump(mode="json")), worker_id="legacy")
