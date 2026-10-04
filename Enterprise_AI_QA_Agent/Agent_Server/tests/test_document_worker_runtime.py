from __future__ import annotations

import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.core.config import get_settings
from src.runtime import document_operation_worker_runtime as runtime_module


@pytest.mark.parametrize("cli", ["run_document_parse_worker", "run_document_difference_worker"])
def test_document_cli_import_does_not_boot_production_api(cli):
    result = subprocess.run([sys.executable, "-X", "utf8", "-c",
        f"import sys; import src.cli.{cli}; assert 'src.main' not in sys.modules"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_stage", ["service", "queue", "cancel"])
async def test_document_startup_failure_closes_acquired_resources(monkeypatch, fail_stage):
    import asyncio
    events = []
    settings = get_settings().model_copy(deep=True)
    stream = "fixture:document-worker"
    settings.orchestration.redis_task_outbox_streams += "," + stream

    class Resource:
        def __init__(self, name): self.name = name
        async def close(self): events.append("close:" + self.name)
        async def initialize(self): events.append("initialize:" + self.name)
        async def connect(self):
            events.append("connect:" + self.name)
            raise RuntimeError("queue unavailable")

    @asynccontextmanager
    async def pools(_):
        events.append("pools:enter")
        try: yield
        finally: events.append("pools:close")

    container = SimpleNamespace(settings=SimpleNamespace(override=lambda _: None),
        artifact_storage_service=lambda: Resource("objects"), project_service=lambda: Resource("projects"),
        api_docs_service=lambda: Resource("documents"),
        application_config_store=lambda: SimpleNamespace(initialize=lambda: events.append("initialize:config")))
    monkeypatch.setattr(runtime_module, "AppContainer", lambda: container)
    monkeypatch.setattr(runtime_module, "sqlalchemy_engine_lifespan", pools)
    monkeypatch.setattr(runtime_module, "PostgresTaskOutbox", lambda _: Resource("outbox"))
    monkeypatch.setattr(runtime_module, "RedisTaskQueue", lambda *args, **kwargs: Resource("queue"))

    async def build_service(_, initialize):
        async def operation():
            if fail_stage == "service": raise ValueError("service unavailable")
            if fail_stage == "cancel": raise asyncio.CancelledError()
        await initialize("domain", operation())
        return object(), object()

    expected = asyncio.CancelledError if fail_stage == "cancel" else (ValueError if fail_stage == "service" else RuntimeError)
    with pytest.raises(expected):
        async with runtime_module.document_operation_worker_runtime(settings, stream=stream,
            task_type="document_difference", log_prefix="document_difference_worker", label="document difference", build_service=build_service):
            pytest.fail("startup failed but yielded a runtime")
    assert "close:objects" in events and events[-1] == "pools:close"
    assert ("close:queue" in events) == (fail_stage == "queue")
