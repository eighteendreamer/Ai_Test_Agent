from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.application.performance.http_probe_runner import HttpProbeResult, HttpProbeRunner
from src.application.performance.mock_target_runner import MockTargetResult, MockTargetRunner
from src.application.performance.performance_engine_runner import (
    PerformanceEngineRunner,
    PerfEngineRunnerResult,
)


class _Settings:
    k6_docker_image = "grafana/k6:latest"
    jmeter_docker_image = "alpine/jmeter:5.6.3"
    performance_runner_docker_container_prefix = "qa-perf"


def test_http_probe_result_model_dump():
    result = HttpProbeResult(
        ok=True,
        status_code=200,
        response_time_ms=38.5,
        target_url="http://example.com/health",
        selected_image_key="helper_http_probe",
        selected_image="curlimages/curl:8.8.0",
        reason="HTTP 200 from target in 38ms",
        next_recommendation="Target is reachable, proceed to smoke validation.",
    )
    payload = result.model_dump()
    assert payload["ok"] is True
    assert payload["status_code"] == 200
    assert payload["selected_image_key"] == "helper_http_probe"


def test_http_probe_classifies_errors():
    assert HttpProbeRunner._classify_probe_error("Could not resolve host", "") == "dns_resolve"
    assert HttpProbeRunner._classify_probe_error("Connection refused", "") == "connection_refused"
    assert HttpProbeRunner._classify_probe_error("Operation timed out", "") == "timeout"
    assert HttpProbeRunner._classify_probe_error("ssl certificate problem", "") == "tls_error"
    assert HttpProbeRunner._classify_probe_error("random error", "") == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code,timed_out,stdout,expected", [
    (0, False, "200 0.0385", True),
    (0, False, "503 0.002", False),
    (28, False, "000 1.001", False),
    (-1, True, "000", False),
])
async def test_http_probe_uses_managed_execution(exit_code, timed_out, stdout, expected):
    from src.core.config import Settings
    from src.runtime.docker_resource_manager import DockerExecutionResult

    class Docker:
        async def execute(self, args, *, name_prefix, timeout_seconds):
            assert args[:3] == ["--entrypoint=", "--add-host", "host.docker.internal:host-gateway"]
            assert "--rm" not in args and "--name" not in args
            assert "--show-error" in args
            assert name_prefix == "qa-perf-probe" and timeout_seconds == 6
            return DockerExecutionResult("a" * 64, "probe", exit_code, stdout, "detail", timed_out)

    result = await HttpProbeRunner(Settings(), docker_resources=Docker()).probe(
        target_url="http://example.test", timeout_seconds=1)
    assert result.ok is expected
    assert result.stdout == stdout and result.stderr == "detail"
    if timed_out or exit_code == 28:
        assert result.reason.startswith("timeout:")


@pytest.mark.asyncio
async def test_http_probe_tool_waits_for_capacity_with_context(monkeypatch):
    from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
    from src.application.runtime.tool_job_service import ToolJobService
    from src.core.request_context import bind_request_context, get_request_context
    from src.runtime.resource_lease_manager import ResourceUnavailable
    from src.runtime.tool_job_store import InMemoryToolJobStore
    from src.schemas.agent import ToolDescriptor
    from src.schemas.tool_runtime import ModelToolCall

    async def unavailable(self, **kwargs):
        context = get_request_context()
        assert context.session_id == "probe-session" and context.turn_id == "probe-turn"
        assert context.project_id == "probe-project" and context.attempt_id == "attempt-probe"
        raise ResourceUnavailable("docker_capacity")

    monkeypatch.setattr(HttpProbeRunner, "probe", unavailable)
    jobs = ToolJobService(InMemoryToolJobStore())
    runtime = ToolRuntimeService(tool_job_service=jobs)
    tool = ToolDescriptor(key="http-probe-runner", name="probe", description="probe", category="test")
    context = ToolExecutionContext(session_id="probe-session", turn_id="probe-turn", trace_id="probe-trace",
        user_message="probe", normalized_input="probe", context_bundle={"project_id": "probe-project"})
    with bind_request_context(session_id="parent-session", attempt_id="attempt-probe"):
        record = await runtime.execute(tool, ModelToolCall(id="probe-call", name=tool.key,
            arguments={"target_url": "http://example.test"}), context)
        assert get_request_context().session_id == "parent-session"
    assert record.status == "waiting_resource"
    assert (await jobs.get_job(record.job_id)).status.value == "waiting_resource"
    assert record.output["reason"] == "docker_capacity"


def test_mock_target_result_model_dump():
    result = MockTargetResult(
        ok=True,
        action="start",
        container_name="qa-perf-mock-abc123",
        port=18080,
        response_text="ok",
        selected_image_key="helper_mock_target",
        selected_image="hashicorp/http-echo:1.0.0",
        reason="mock target started on port 18080",
    )
    payload = result.model_dump()
    assert payload["ok"] is True
    assert payload["action"] == "start"
    assert payload["port"] == 18080


def test_performance_engine_runner_select_k6():
    runner = PerformanceEngineRunner(_Settings())
    result = runner.select_engine(engine="k6", scenario="api_baseline")
    assert result.ok is True
    assert result.engine == "k6"
    assert result.selected_image_key == "perf_k6_default"
    assert result.selected_image == "grafana/k6:latest"


def test_performance_engine_runner_select_jmeter():
    runner = PerformanceEngineRunner(_Settings())
    result = runner.select_engine(engine="jmeter", scenario="complex_flow")
    assert result.ok is True
    assert result.engine == "jmeter"
    assert result.selected_image_key == "perf_jmeter_default"
    assert result.selected_image == "alpine/jmeter:5.6.3"


def test_performance_engine_runner_result_model_dump():
    result = PerfEngineRunnerResult(
        ok=True,
        engine="k6",
        selected_image_key="perf_k6_default",
        selected_image="grafana/k6:latest",
        reason="selected perf_k6_default for engine=k6",
    )
    payload = result.model_dump()
    assert payload["ok"] is True
    assert payload["engine"] == "k6"
    assert payload["selected_image_key"] == "perf_k6_default"
