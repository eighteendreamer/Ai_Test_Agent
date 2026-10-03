from __future__ import annotations

import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.core.config import get_settings
from src.runtime import planning_worker_runtime as runtime_module


def test_planning_cli_does_not_boot_api_session_or_tool_runtime():
    result = subprocess.run([sys.executable, "-X", "utf8", "-c",
        "import sys; import src.cli.run_case_planning_worker; "
        "assert 'src.main' not in sys.modules, 'planning CLI imports full API startup'"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_stage", ["vector", "model", "close"])
async def test_partial_worker_startup_closes_acquired_resources(monkeypatch, fail_stage):
    events = []
    settings = get_settings().model_copy(deep=True)
    settings.orchestration.redis_vector_enabled = True
    settings.orchestration.redis_task_outbox_streams += ",fixture:case_planning"

    class Resource:
        def __init__(self, name): self.name = name
        async def close(self):
            events.append("close:" + self.name)
            if fail_stage == "close" and self.name == "graphs": raise RuntimeError("graph cleanup failed")
        async def connect(self):
            events.append("connect:" + self.name)
            if fail_stage == "vector": raise RuntimeError("vector startup failed")

    def fail_model():
        events.append("initialize:model")
        raise ValueError("model startup failed")

    @asynccontextmanager
    async def pools(_):
        events.append("pools:enter")
        try: yield
        finally: events.append("pools:close")

    resources = {key: Resource(name) for key, name in [
        ("artifact_storage_service", "objects"), ("embedding_runtime_service", "embedding"),
        ("recording_graph_store", "graphs")]}
    container = SimpleNamespace(settings=SimpleNamespace(override=lambda _: None),
        model_config_store=lambda: SimpleNamespace(initialize=fail_model),
        **{key: (lambda value=value: value) for key, value in resources.items()})
    monkeypatch.setattr(runtime_module, "AppContainer", lambda: container)
    monkeypatch.setattr(runtime_module, "RedisVectorStore", lambda *a, **k: Resource("vector"))
    monkeypatch.setattr(runtime_module, "sqlalchemy_engine_lifespan", pools)
    expected = RuntimeError if fail_stage in {"vector", "close"} else ValueError
    with pytest.raises(expected):
        async with runtime_module.planning_worker_runtime(settings):
            pytest.fail("failed startup yielded a usable runtime")
    assert {event for event in events if event.startswith("close:")} == {
        "close:vector", "close:graphs", "close:embedding", "close:objects"}
    assert events[-1] == "pools:close"
