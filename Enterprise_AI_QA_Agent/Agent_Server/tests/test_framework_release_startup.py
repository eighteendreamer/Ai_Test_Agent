"""Configured source identity is checked before application resources start."""
from __future__ import annotations

import ast
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

from fastapi import FastAPI
import pytest

from src.core.config import DeepAgentsConfig
from src.runtime.framework_worker_capabilities import FrameworkWorkerCapabilities
from src.schemas.framework_release import FrameworkReleaseReference
from tests.test_framework_release_binding import current_source_release


REFERENCE = {"schema_version": 1, "archive_sha256": "b" * 64,
    "size_bytes": 1234, "source_revision": "c" * 40}


def test_release_configuration_preserves_paths_without_enabling_runtime_or_writers(tmp_path):
    config = DeepAgentsConfig(release_store=tmp_path, release_reference=tmp_path / "reference.json")
    assert config.model_dump().get("release_store") == tmp_path
    assert config.model_dump().get("release_reference") == tmp_path / "reference.json"
    assert not config.enabled and not config.checkpoint_enabled
    blank = DeepAgentsConfig(release_store="", release_reference="  ")
    assert blank.release_store is blank.release_reference is None
    assert DeepAgentsConfig().release_store is DeepAgentsConfig().release_reference is None


@pytest.mark.parametrize("values", [{"release_store": "store"}, {"release_reference": "reference.json"}])
def test_release_configuration_requires_a_complete_pair(values):
    with pytest.raises(ValueError, match="together"):
        DeepAgentsConfig(**values)


@pytest.mark.parametrize("value", [0, False])
def test_release_paths_do_not_silently_convert_non_path_values_to_unconfigured(value):
    with pytest.raises(ValueError):
        DeepAgentsConfig(release_store=value, release_reference=value)


@pytest.mark.parametrize("enabled,checkpoint", [(False, False), (True, False), (True, True)])
def test_configured_factory_uses_the_existing_installation_verifier(tmp_path, monkeypatch, caplog, enabled, checkpoint):
    from src.core.log_format import ContextFilter, ContextFormatter, LOG_FORMAT

    caplog.set_level("INFO", logger="src.runtime.framework_worker_capabilities")
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(REFERENCE), encoding="utf-8-sig")
    config = DeepAgentsConfig(enabled=enabled, checkpoint_enabled=checkpoint, new_turns_enabled=False,
        release_store=tmp_path, release_reference=path)
    checked = []

    def installed(store, reference, *, deepagents_available):
        checked.append((store, reference, deepagents_available))
        return FrameworkWorkerCapabilities("a" * 64, deepagents_available,
            release_reference=FrameworkReleaseReference.model_validate(reference))

    monkeypatch.setattr(FrameworkWorkerCapabilities, "from_release", installed)
    caps = FrameworkWorkerCapabilities.from_configuration(config)
    assert checked == [(tmp_path, REFERENCE, enabled and checkpoint)]
    assert caps.release_reference.model_dump(mode="json") == REFERENCE
    assert caps.deepagents_available is (enabled and checkpoint) and not config.new_turns_enabled
    record, = [row for row in caplog.records if row.message == "framework_release_preflight_verified"]
    ContextFilter().filter(record)
    rendered = ContextFormatter(LOG_FORMAT).format(record)
    assert json.loads(rendered.split(" diagnostics=", 1)[1]) == {
        "framework_release_revision": REFERENCE["source_revision"],
        "framework_release_sha256": REFERENCE["archive_sha256"]}


def test_unconfigured_factory_does_not_probe_or_guess_an_application_release(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Unconfigured application startup must retain its original optional capability")

    monkeypatch.setattr(FrameworkWorkerCapabilities, "from_artifact", unexpected)
    config = DeepAgentsConfig(enabled=True, checkpoint_enabled=True)
    assert FrameworkWorkerCapabilities.from_configuration(config) is None


@pytest.mark.parametrize("failure", ["missing", "malformed", "reference", "installation"])
def test_configured_preflight_reports_only_safe_error_type(tmp_path, monkeypatch, caplog, failure):
    path = tmp_path / "private-release-reference.json"
    if failure == "malformed":
        path.write_text("private-reference-probe{", encoding="utf-8")
    elif failure == "reference":
        path.write_text(json.dumps({**REFERENCE, "private-reference-probe": "private-input"}), encoding="utf-8")
    elif failure == "installation":
        path.write_text(json.dumps(REFERENCE), encoding="utf-8")

        def reject(*args, **kwargs):
            raise ValueError("private-installation-probe")

        monkeypatch.setattr(FrameworkWorkerCapabilities, "from_release", reject)
    config = DeepAgentsConfig(release_store=tmp_path, release_reference=path)
    with pytest.raises(ValueError, match="Configured framework release preflight failed") as caught:
        FrameworkWorkerCapabilities.from_configuration(config)
    assert "private-" not in str(caught.value) + caplog.text
    records = [row for row in caplog.records if row.message == "framework_release_preflight_failed"]
    assert len(records) == 1
    assert records[0].error_type == {"missing": "FileNotFoundError", "malformed": "JSONDecodeError",
        "reference": "ValidationError", "installation": "ValueError"}[failure]
    assert not any("private-" in str(value) for value in records[0].__dict__.values())


@pytest.mark.parametrize("blank", [False, True])
def test_nested_settings_environment_parses_release_pair_without_changing_route(monkeypatch, tmp_path, blank):
    from pydantic_settings import BaseSettings
    from src.core.config import Settings

    class Probe(BaseSettings):
        deep_agents: DeepAgentsConfig
        model_config = Settings.model_config

    monkeypatch.setenv("DEEP_AGENTS__RELEASE_STORE", "" if blank else str(tmp_path))
    monkeypatch.setenv("DEEP_AGENTS__RELEASE_REFERENCE", "" if blank else str(tmp_path / "reference.json"))
    monkeypatch.setenv("DEEP_AGENTS__ENABLED", "false")
    monkeypatch.setenv("DEEP_AGENTS__NEW_TURNS_ENABLED", "false")
    config = Probe(_env_file=None, deep_agents={}).deep_agents
    assert config.release_store == (None if blank else tmp_path)
    assert config.release_reference == (None if blank else tmp_path / "reference.json")
    assert not config.enabled and not config.new_turns_enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", [True, False])
async def test_real_lifespan_checks_source_before_sql_or_other_resources(monkeypatch, rejected):
    from src import main as module

    events = []
    caps = FrameworkWorkerCapabilities("a" * 64, False,
        release_reference=FrameworkReleaseReference.model_validate(REFERENCE))

    def preflight(config):
        events.append("preflight")
        if rejected:
            raise ValueError("controlled release rejection")
        return caps

    @asynccontextmanager
    async def engines(settings):
        events.append("engines")
        assert not rejected, "SQL resource ownership started before source verification"
        try:
            yield
        finally:
            events.append("engines_closed")

    async def initialize(app, cleanup, final_cleanup):
        events.append("initialize")
        assert app.state.framework_worker_capabilities is caps

    monkeypatch.setattr(FrameworkWorkerCapabilities, "from_configuration", preflight, raising=False)
    monkeypatch.setattr(module, "sqlalchemy_engine_lifespan", engines)
    monkeypatch.setattr(module, "_initialize_lifespan_resources", initialize)
    app = FastAPI()
    app.state.framework_worker_capabilities = caps  # Never reuse a previous lifespan's identity after rejection.
    if rejected:
        with pytest.raises(ValueError, match="controlled release rejection"):
            async with module.lifespan(app):
                pytest.fail("Rejected release must not start the application")
        assert events == ["preflight"] and app.state.framework_worker_capabilities is None
    else:
        async with module.lifespan(app):
            events.append("running")
            assert app.state.framework_worker_capabilities is caps
        assert events == ["preflight", "engines", "initialize", "running", "engines_closed"]


@pytest.mark.asyncio
async def test_cancelled_source_preflight_never_installs_late_capability_or_starts_resources(monkeypatch):
    from src import main as module

    entered, release, finished = Event(), Event(), Event()

    def preflight(config):
        entered.set()
        try:
            assert release.wait(5)
            return FrameworkWorkerCapabilities("a" * 64, True)
        finally:
            finished.set()

    def unexpected(*args, **kwargs):
        pytest.fail("Cancelled preflight must not start application resources")

    monkeypatch.setattr(FrameworkWorkerCapabilities, "from_configuration", preflight)
    monkeypatch.setattr(module, "sqlalchemy_engine_lifespan", unexpected)
    monkeypatch.setattr(module, "_initialize_lifespan_resources", unexpected)
    app = FastAPI()
    lifecycle = module.lifespan(app)
    starting = asyncio.create_task(lifecycle.__aenter__())
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert not finished.is_set() and app.state.framework_worker_capabilities is None
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
        assert app.state.framework_worker_capabilities is None
    finally:
        release.set()
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 2)


def test_initializer_wires_preflight_capability_into_the_original_dispatcher():
    # Wiring audit, paired with real lifespan ordering tests; not full E4.
    source = Path(__file__).resolve().parents[1] / "src/main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "AgentTaskDispatcher"]
    assert len(calls) == 1
    keywords = {item.arg: ast.unparse(item.value) for item in calls[0].keywords}
    assert keywords.get("framework_capabilities") == "app.state.framework_worker_capabilities"


@pytest.mark.asyncio
async def test_v2_cli_runs_configured_preflight_before_lifespan_or_queue(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from src.cli import run_agent_v2_worker as cli

    settings = SimpleNamespace(deep_agents=DeepAgentsConfig(
        release_store=tmp_path, release_reference=tmp_path / "reference.json"))
    checked = []

    def reject(config):
        checked.append(config)
        raise ValueError("controlled configuration rejection")

    def unexpected(*args, **kwargs):
        pytest.fail("Configured source must be checked before lifespan or delivery")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(FrameworkWorkerCapabilities, "from_configuration", reject)
    monkeypatch.setattr(cli, "app", SimpleNamespace(router=SimpleNamespace(lifespan_context=unexpected)))
    monkeypatch.setattr(cli, "RedisTaskQueue", unexpected)
    with pytest.raises(ValueError, match="controlled configuration rejection"):
        await cli._run(once=True, worker_id="configuration-probe")
    assert checked == [settings.deep_agents]


STARTUP = r'''
import asyncio, json, pathlib, sys
from contextlib import asynccontextmanager
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "Agent_Server"))

async def probe():
    from src.core import config
    data = json.loads(sys.stdin.buffer.read())
    settings = config.Settings(_env_file=None, database={"redis_url": "redis://unused"},
        storage={}, security={}, model={}, orchestration={}, frontend={},
        docker={name: "unused-preflight-probe" for name in config.DockerConfig.model_fields},
        mail={"agently_cli_config_root": str(root / "unused-mail"), "agently_auth_lock_ttl_seconds": 5,
            "agently_auth_lock_wait_seconds": .1, "agently_auth_check_interval_seconds": .1}, deep_agents={
            "enabled": data["enabled"], "checkpoint_enabled": data["enabled"], "new_turns_enabled": False,
            "release_store": data["store"], "release_reference": data["reference_path"]})
    config.get_settings = lambda: settings
    from src import main
    events = []

    @asynccontextmanager
    async def engines(settings):
        events.append("engines")
        try:
            yield
        finally:
            events.append("engines_closed")

    async def initialize(app, cleanup, final_cleanup):
        # Only source preflight is real here. Do not initialize application
        # stores, domain Runners, a graph or model/tool/network providers.
        events.append("initialize")
        caps = app.state.framework_worker_capabilities
        assert caps.release_reference.model_dump(mode="json") == data["reference"]
        assert caps.deepagents_available is data["enabled"]

    main.sqlalchemy_engine_lifespan = engines
    main._initialize_lifespan_resources = initialize
    rejected = False
    try:
        async with main.lifespan(main.app):
            events.append("running")
    except ValueError as exc:
        assert str(exc) == "Configured framework release preflight failed"
        assert main.app.state.framework_worker_capabilities is None
        rejected = True
    assert rejected is data["drift"]
    assert events == ([] if rejected else ["engines", "initialize", "running", "engines_closed"])
    assert not settings.deep_agents.new_turns_enabled
    loaded = [pathlib.Path(module.__file__).resolve() for name, module in sys.modules.items()
        if name.startswith("src.") and getattr(module, "__file__", None)]
    assert loaded and all(path.is_relative_to(root) for path in loaded)
    return {"preflight_rejected": rejected, "source_modules_inside_release": True,
        "application_yielded": "running" in events, "source_module_count": len(loaded)}

try:
    print(json.dumps(asyncio.run(probe()), sort_keys=True))
except BaseException as exc:
    import traceback
    print("source_startup_probe_failed " + json.dumps({"error_type": type(exc).__name__,
        "frames": [{"file": pathlib.Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)]}), file=sys.stderr)
    raise SystemExit(1) from None
'''


@pytest.mark.parametrize("enabled,drift", [(False, False), (True, False), (True, True)])
def test_restored_current_source_runs_real_application_preflight_without_starting_backends(
        tmp_path, current_source_release, enabled, drift):
    from src.runtime.framework_release_archive import restore_release

    store, reference = current_source_release
    source = tmp_path / "installed-application"
    restore_release(store, reference, source)
    if drift:
        path = source / "Agent_Server/src/core/config.py"
        path.write_bytes(path.read_bytes() + b"\n# controlled source drift\n")
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(reference.model_dump_json(), encoding="utf-8")
    data = {"enabled": enabled, "drift": drift, "store": str(store), "reference_path": str(reference_path),
        "reference": reference.model_dump(mode="json")}
    environment = {name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")
        if name in os.environ}
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([sys.executable, "-I", "-B", "-c", STARTUP, str(source)],
        input=json.dumps(data).encode("utf-8"), cwd=source, env=environment, capture_output=True, timeout=180)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    observed = json.loads(result.stdout)
    assert observed["preflight_rejected"] is drift and observed["application_yielded"] is (not drift)
    assert observed["source_modules_inside_release"] and observed["source_module_count"] > 100
