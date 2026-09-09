from __future__ import annotations

import pytest

from src.application.deep_agents import (
    DeepAgentRuntimeAdapter,
    DeepAgentRuntimeRequest,
)
from src.application.deep_agents.runtime_adapter import _project_root_from_context


class _FakeAgent:
    def __init__(self, calls: list[dict]):
        self._calls = calls

    async def ainvoke(self, payload, *, config):
        self._calls.append({"payload": payload, "config": config})
        return {
            "messages": [
                {"role": "user", "content": "inspect"},
                {"role": "assistant", "content": "DA_E1_OK"},
            ]
        }


@pytest.mark.asyncio
async def test_da_e1_maps_messages_and_keeps_business_tools_empty():
    calls: list[dict] = []
    factory_calls: list[dict] = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return _FakeAgent(calls)

    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _resolved_model(),
        agent_factory=factory,
    )
    result = await adapter.execute(
        DeepAgentRuntimeRequest(
            session_id="session-1",
            turn_id="turn-1",
            trace_id="trace-1",
            model_key="qwen-test",
            system_prompt="pilot",
            messages=[{"role": "user", "content": "inspect"}],
        )
    )

    assert result.output_text == "DA_E1_OK"
    assert result.metadata["stage"] == "DA-E1"
    assert factory_calls[0]["tools"] == []
    assert calls[0]["config"] == {"configurable": {"thread_id": "turn-1"}}
    assert calls[0]["payload"]["messages"] == [{"role": "user", "content": "inspect"}]


@pytest.mark.asyncio
async def test_da_e1_rejects_missing_assistant_output():
    class EmptyAgent:
        async def ainvoke(self, payload, *, config):
            return {"messages": [{"role": "user", "content": "only input"}]}

    adapter = DeepAgentRuntimeAdapter(
        model_resolver=lambda _key: _resolved_model(),
        agent_factory=lambda **_kwargs: EmptyAgent(),
    )

    with pytest.raises(RuntimeError, match="no assistant message"):
        await adapter.execute(
            DeepAgentRuntimeRequest(
                session_id="session-1",
                turn_id="turn-1",
                trace_id="trace-1",
                model_key="qwen-test",
                system_prompt="pilot",
                messages=[],
            )
        )


def test_da_e2_resolves_only_explicit_local_project_root():
    assert _project_root_from_context({"project_root": "C:/workspace"}) == "C:/workspace"
    assert _project_root_from_context(
        {"project_source": {"source_type": "local", "root_path": "C:/repo"}}
    ) == "C:/repo"
    assert _project_root_from_context(
        {"project_source": {"source_type": "ssh", "root_path": "/srv/repo"}}
    ) == ""


async def _resolved_model():
    return object()
