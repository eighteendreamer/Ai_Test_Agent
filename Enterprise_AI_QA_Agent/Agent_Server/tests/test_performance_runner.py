from __future__ import annotations

from pathlib import Path
import asyncio
import os
import shutil
from types import SimpleNamespace

import pytest

from src.application.performance.engine_adapter import EngineCommand, ScriptArtifact
from src.core.request_context import get_request_context
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
from src.modes.performance_testing_mode.contracts import PHASE_GUARD_PASSED, PHASE_SMOKE_VALIDATED, STATE_METADATA_KEY
from src.modes.performance_testing_mode.plan_state import PerformanceTestingState
from src.runtime.docker_resource_manager import DockerResourceManager
from src.runtime.resource_lease_manager import ResourceUnavailable
from src.application.performance.perf_runner_service import ExecutionResult, PerfRunnerService
from src.modes.performance_testing_mode.plan_state import PerfPlan


class _Settings:
    def __init__(self):
        self.orchestration = SimpleNamespace(
            performance_max_concurrent_runs=1,
            performance_runner_docker_container_prefix="qa-perf",
            performance_runner_docker_workdir="/work",
            performance_runner_ephemeral=True,
            performance_runner_backend="local",
        )


class _AutoSettings(_Settings):
    def __init__(self):
        super().__init__()
        self.orchestration.performance_runner_backend = "auto"


def test_validate_smoke_reports_timeout():
    service = PerfRunnerService(_Settings())

    result = service._validate_smoke(ExecutionResult(-1, "", "timeout", timed_out=True), PerfPlan())

    assert result.passed is False
    assert result.detail == "冒烟验证超时"


def test_validate_smoke_reports_zero_completed_requests():
    service = PerfRunnerService(_Settings())
    summary = '{"metrics":{"http_reqs":{"count":0},"http_req_failed":{"rate":0}}}'

    result = service._validate_smoke(ExecutionResult(0, "", "", summary_json=summary), PerfPlan())

    assert result.passed is False
    assert "无请求完成" in result.detail


def test_validate_smoke_reports_high_failure_rate():
    service = PerfRunnerService(_Settings())
    summary = '{"metrics":{"http_reqs":{"count":3},"http_req_failed":{"rate":0.67}}}'

    result = service._validate_smoke(ExecutionResult(0, "", "", summary_json=summary), PerfPlan())

    assert result.passed is False
    assert "失败率过高" in result.detail


def test_validate_smoke_accepts_jmeter_statistics_json():
    service = PerfRunnerService(_Settings())
    summary = '{"Total":{"sampleCount":3,"errorPct":0.0}}'

    result = service._validate_smoke(ExecutionResult(0, "", "", summary_json=summary), PerfPlan())

    assert result.passed is True


def test_validate_smoke_reports_jmeter_high_failure_rate():
    service = PerfRunnerService(_Settings())
    summary = '{"Total":{"sampleCount":3,"errorPct":67.0}}'

    result = service._validate_smoke(ExecutionResult(0, "", "", summary_json=summary), PerfPlan())

    assert result.passed is False
    assert "失败率过高" in result.detail


def test_validate_smoke_reports_missing_extract():
    service = PerfRunnerService(_Settings())
    plan = PerfPlan()
    plan.smoke.expect_extract = ["token"]
    summary = '{"metrics":{"http_reqs":{"count":1},"http_req_failed":{"rate":0}}}'

    result = service._validate_smoke(ExecutionResult(0, "no auth", "", summary_json=summary), plan)

    assert result.passed is False
    assert result.extracted == {"token": False}


def test_read_summary_artifact_falls_back_to_jmeter_statistics(tmp_path: Path):
    stats = tmp_path / "report" / "statistics.json"
    stats.parent.mkdir()
    stats.write_text('{"Total":{"sampleCount":1}}', encoding="utf-8")

    content = PerfRunnerService._read_summary_artifact(tmp_path, tmp_path / "summary.json")

    assert "sampleCount" in content


def test_materialize_data_files_copies_existing_sources(tmp_path: Path):
    source = tmp_path / "source.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    script = ScriptArtifact(
        engine="jmeter",
        script_content="",
        filename="test.jmx",
        data_files={"data/source.csv": str(source)},
    )

    PerfRunnerService._materialize_data_files(script, work)

    assert (work / "data" / "source.csv").read_text(encoding="utf-8") == "id\n1\n"


def test_detect_backend_checks_jmeter_binary_before_docker(monkeypatch: pytest.MonkeyPatch):
    service = PerfRunnerService(_AutoSettings())

    def fake_which(name: str):
        return "E:/bin/jmeter.exe" if name == "jmeter" else None

    monkeypatch.setattr("src.application.performance.perf_runner_service.shutil.which", fake_which)

    assert service.detect_backend("jmeter") == "local"


def test_detect_backend_falls_back_to_docker_when_engine_binary_missing(monkeypatch: pytest.MonkeyPatch):
    service = PerfRunnerService(_AutoSettings())

    monkeypatch.setattr(
        "src.application.performance.perf_runner_service.shutil.which",
        lambda name: "docker.exe" if name == "docker" else None,
    )

    assert service.detect_backend("jmeter") == "docker"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [PHASE_GUARD_PASSED, PHASE_SMOKE_VALIDATED])
async def test_resource_wait_preserves_resumable_phase_and_trusted_scope(phase):
    runtime = PerformanceTestingModeRuntime()
    state = PerformanceTestingState(phase=phase, plan=PerfPlan(), session_id="s1")
    context = SimpleNamespace(session_id="s1", turn_id="t1", context_bundle={
        STATE_METADATA_KEY: state.model_dump(), "project_id": "p1", "test_run_id": "r1",
        "run_item_id": "i1", "attempt_id": "a1",
    })

    class Runner:
        async def execute(self, *args):
            scope = get_request_context()
            assert scope.project_id == "p1" and scope.run_item_id == "i1"
            assert scope.attempt_id == "a1" and scope.session_id == "s1"
            raise ResourceUnavailable("global:all")
        run_smoke = execute

    runtime._runner = Runner()
    runtime._create_engine_adapter = lambda engine: SimpleNamespace(build_script=lambda plan: object())
    result = await runtime.handle({}, context)
    assert result["status"] == "waiting_resource"
    assert result["waiting_reason"] == "global:all"
    assert result["phase"] == phase
    assert not result["performance_testing_state"]["errors"]
    assert context.context_bundle[STATE_METADATA_KEY]["phase"] == phase
    assert get_request_context() is None


@pytest.mark.asyncio
async def test_docker_never_starts_if_binding_is_rejected():
    calls = []
    lease = SimpleNamespace(resource_id="docker-slot-0", fencing_token=3, worker_id="w1")

    class Manager:
        namespace = "qa:test"
        async def connect(self): pass
        async def acquire_first_available(self, **kwargs): return lease
        async def prepare_allocation(self, *args): return True
        async def bind(self, *args): return False
        async def retire(self, *args): calls.append("retire"); return True

    class Allocations:
        async def prepare(self, *args, **kwargs): calls.append("prepare")
        async def bind(self, *args): calls.append("persist_binding")

    class Docker(DockerResourceManager):
        async def _command(self, args, *, timeout):
            calls.append(args[0])
            return 0, "a" * 64 if args[0] == "create" else "", ""

    settings = SimpleNamespace(orchestration=SimpleNamespace(
        resource_docker_slots=1, resource_default_lease_seconds=30,
        resource_docker_command_timeout_seconds=60,
    ))
    docker = Docker(settings, lease_manager=Manager(), allocation_store=Allocations())
    with pytest.raises(RuntimeError, match="ownership lost"):
        await docker.execute(["busybox", "true"], name_prefix="qa-test", timeout_seconds=10)
    assert calls == ["prepare", "create", "persist_binding", "rm", "retire"]


@pytest.mark.asyncio
async def test_docker_never_creates_without_durable_intent():
    calls = []
    class Manager:
        namespace = "qa:test"
        async def connect(self): pass
        async def acquire_first_available(self, **kwargs): return object()
        async def retire(self, lease): calls.append("retire"); return True
    class Allocations:
        async def prepare(self, *args, **kwargs): raise ConnectionError("intent database unavailable")
    class Docker(DockerResourceManager):
        async def _command(self, *args, **kwargs): raise AssertionError("Docker must not be called")
    settings = SimpleNamespace(orchestration=SimpleNamespace(
        resource_docker_slots=1, resource_default_lease_seconds=30, resource_docker_command_timeout_seconds=60))
    with pytest.raises(ConnectionError, match="intent database"):
        await Docker(settings, lease_manager=Manager(), allocation_store=Allocations()).execute(
            ["busybox", "true"], name_prefix="qa-test", timeout_seconds=10)
    assert calls == ["retire"]


@pytest.mark.asyncio
async def test_auto_backend_uses_resource_manager_and_preserves_summary(monkeypatch):
    calls = []

    class Docker:
        async def execute(self, args, **kwargs):
            calls.append((args, kwargs))
            mount = args[args.index("-v") + 1].rsplit(":", 1)[0]
            (Path(mount) / "summary.json").write_text('{"samples":1}', encoding="utf-8")
            return SimpleNamespace(exit_code=7, stdout="result", stderr="details", timed_out=False, container_id="a" * 64)

    service = PerfRunnerService(_AutoSettings(), docker_resources=Docker())
    monkeypatch.setattr("src.application.performance.perf_runner_service.shutil.which", lambda name: "docker" if name == "docker" else None)
    assert service.detect_backend() == "docker"
    result = await service._run_in_docker(
        EngineCommand(image="busybox", command=["true"]),
        ScriptArtifact(engine="k6", filename="test.js", script_content=""), "qa-test", 10,
    )
    assert result.exit_code == 7 and result.stderr == "details"
    assert result.container_id == "a" * 64 and result.summary_json == '{"samples":1}'
    assert "--rm" not in calls[0][0] and "--name" not in calls[0][0]


@pytest.mark.skipif(os.name != "nt", reason="Windows Uvicorn loop compatibility")
def test_docker_cli_supports_windows_selector_loop():
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is unavailable")
    loop = asyncio.SelectorEventLoop()
    try:
        code, stdout, stderr = loop.run_until_complete(
            DockerResourceManager(object())._command(["--version"], timeout=15))
        assert code == 0 and "Docker version" in stdout and not stderr
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()
