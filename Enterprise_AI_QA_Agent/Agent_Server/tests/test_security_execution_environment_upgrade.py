from __future__ import annotations

import asyncio
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.security.execution_environment_service import (
    SecurityExecutionEnvironmentService,
)
from src.application.security.tool_bootstrap_service import ToolBootstrapService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.modes.security_testing_mode.attack_session import SecurityShellSession
from src.modes.security_testing_mode.callback_broker import SecurityCallbackBroker
from src.modes.security_testing_mode.campaign_state import (
    AccessProof,
    AssetNode,
    AssetRelation,
    CredentialReference,
    SecurityCampaign,
)
from src.modes.security_testing_mode.exploit_workspace import ExploitWorkspace
from src.modes.security_testing_mode.runtime import SecurityTestingModeRuntime
from src.modes.security_testing_mode.security_graph_store import SecurityGraphStore
from src.runtime.tool_job_store import InMemoryToolJobStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall


class _Settings:
    def __init__(self):
        self.security_runner_backend = "docker"
        self.security_runner_docker_image = "vxcontrol/kali-linux"
        self.security_runner_docker_container_prefix = "qa-security-test"
        self.security_runner_docker_workdir = "/work"
        self.security_runner_docker_network = ""
        self.security_runner_docker_net_raw = False
        self.security_runner_docker_net_admin = False
        self.security_runner_docker_pull_policy = "never"
        self.security_runner_container_reuse = False
        self.security_runner_docker_cleanup_after_run = True
        self.security_runner_wrap_timeout = True
        self.security_target_allowlist = "127.0.0.1"
        self.security = SimpleNamespace(
            security_runner_backend=self.security_runner_backend,
            security_runner_docker_image=self.security_runner_docker_image,
            security_runner_docker_container_prefix=self.security_runner_docker_container_prefix,
            security_runner_docker_workdir=self.security_runner_docker_workdir,
            security_runner_docker_network=self.security_runner_docker_network,
            security_runner_docker_net_raw=self.security_runner_docker_net_raw,
            security_runner_docker_net_admin=self.security_runner_docker_net_admin,
            security_runner_docker_pull_policy=self.security_runner_docker_pull_policy,
            security_runner_container_reuse=self.security_runner_container_reuse,
            security_runner_docker_cleanup_after_run=self.security_runner_docker_cleanup_after_run,
            security_runner_wrap_timeout=self.security_runner_wrap_timeout,
            security_target_allowlist=self.security_target_allowlist,
        )


class _Context:
    session_id = "session-1"
    turn_id = "turn-1"


class _BootstrapSettings:
    def __init__(self):
        self.orchestration = SimpleNamespace(resource_docker_command_timeout_seconds=60)
        self.security_tool_bootstrap_enabled = True
        self.security_tool_bootstrap_package_allowlist = "tcpdump"
        self.security_tool_bootstrap_image_allowlist = "vxcontrol/kali-linux@sha256:trusted"
        self.security_tool_bootstrap_repository_allowlist = "kali-rolling"
        self.security_tool_bootstrap_timeout_seconds = 60
        self.security_tool_bootstrap_cleanup_required = True
        self.security_runner_docker_image = "vxcontrol/kali-linux"
        self.security_runner_docker_network = ""
        self.security_runner_docker_container_prefix = "qa-security-test"
        self.security = SimpleNamespace(
            security_tool_bootstrap_enabled=self.security_tool_bootstrap_enabled,
            security_tool_bootstrap_package_allowlist=self.security_tool_bootstrap_package_allowlist,
            security_tool_bootstrap_image_allowlist=self.security_tool_bootstrap_image_allowlist,
            security_tool_bootstrap_repository_allowlist=self.security_tool_bootstrap_repository_allowlist,
            security_tool_bootstrap_timeout_seconds=self.security_tool_bootstrap_timeout_seconds,
            security_tool_bootstrap_cleanup_required=self.security_tool_bootstrap_cleanup_required,
            security_runner_docker_image=self.security_runner_docker_image,
            security_runner_docker_network=self.security_runner_docker_network,
            security_runner_docker_container_prefix=self.security_runner_docker_container_prefix,
        )


class _BootstrapDocker:
    def __init__(self, results):
        self.results = iter(results)
        self.created = []
        self.removed = []
        self.commands = []

    async def run(self, args, *, name_prefix, operation):
        assert "vxcontrol/kali-linux@sha256:trusted" in args
        assert "--cap-drop" in args and "no-new-privileges:true" in args
        self.created.append(name_prefix)
        try:
            return await operation(SimpleNamespace(container_id="a" * 64, container_name=name_prefix))
        finally:
            self.removed.append(name_prefix)

    async def _command(self, args, *, timeout):
        if args[0] == "exec":
            assert args[3] == "a" * 64
            self.commands.append(args[-1])
            result = next(self.results)
            return result.returncode, result.stdout, result.stderr
        if args[0] == "ps":
            assert "id=" + "a" * 64 in args
            assert self.removed
        else:
            assert args == ["start", "a" * 64]
        return 0, "", ""


class _PersistentEnvironment:
    def __init__(self):
        self.created = []
        self.executed = []
        self.cleaned = []

    async def create_persistent_container(self, *, campaign_id, artifact_dir, context=None):
        self.created.append((campaign_id, artifact_dir))
        return f"qa-security-runner-attack-{campaign_id}"

    async def execute_in_container(self, *, container_name, command, command_args, timeout_seconds, artifact_dir, context):
        self.executed.append((container_name, command, timeout_seconds))
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        return type(
            "Result",
            (),
            {
                "started_at": now,
                "completed_at": now,
                "exit_code": 0,
                "timed_out": False,
                "stdout": "state=ok",
                "stderr": "",
            },
        )()

    async def container_heartbeat(self, container_name, *, context=None):
        return True

    async def cleanup_container(self, container_name, *, context=None):
        self.cleaned.append(container_name)

    async def put_file(self, **kwargs):
        return kwargs["container_name"]

    async def get_file(self, **kwargs):
        return Path(kwargs["local_path"])


class _ExploitEnvironment(_PersistentEnvironment):
    async def execute_in_container(
        self,
        *,
        container_name,
        command,
        command_args,
        timeout_seconds,
        artifact_dir,
        context,
    ):
        self.executed.append((container_name, command, timeout_seconds))
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        if "py_compile" in command:
            source_path = next(Path(artifact_dir).rglob("verifier.py"))
            pycache = source_path.parent / "pycache"
            pycache.mkdir(parents=True, exist_ok=True)
            (pycache / "verifier.cpython-311.pyc").write_bytes(b"auditable-bytecode")
            stdout = ""
        else:
            stdout = '{"status_code": 302, "observed": "missing header confirmed", "impact": "none"}'
        return type(
            "Result",
            (),
            {
                "started_at": now,
                "completed_at": now,
                "exit_code": 0,
                "timed_out": False,
                "stdout": stdout,
                "stderr": "",
            },
        )()


def _completed(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_parallel_security_calls_have_distinct_artifact_mounts(monkeypatch, tmp_path):
    from datetime import datetime
    from src.core.config import Settings

    class FixedClock:
        @staticmethod
        def utcnow(): return datetime(2026, 9, 17, 12, 0, 0)

    monkeypatch.setattr("src.application.runtime.tool_runtime_service.datetime", FixedClock)
    runtime = object.__new__(ToolRuntimeService)
    runtime._settings = Settings()
    runtime._settings.storage.artifact_root_dir = str(tmp_path)
    first = runtime._prepare_local_artifact_dir(_Context(), "web-scan-runner")
    second = runtime._prepare_local_artifact_dir(_Context(), "web-scan-runner")
    assert first != second
    (first / "evidence.txt").write_text("first", encoding="utf-8")
    (second / "evidence.txt").write_text("second", encoding="utf-8")
    assert (first / "evidence.txt").read_text(encoding="utf-8") == "first"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _GraphProvider:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.writes: list[tuple[str, dict]] = []

    def initialize(self):
        if self.error:
            raise self.error

    def execute_write(self, query, parameters):
        self.writes.append((query, parameters))
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["cancelled", "resume_requested", "completed"])
async def test_background_heartbeat_and_settlement_preserve_concurrent_status(terminal):
    from src.schemas.tool_job import ToolJobStatus

    jobs = ToolJobService(InMemoryToolJobStore())
    job = await jobs.create_job(ToolDescriptor(key="test", name="test", description="test", category="test"),
        call_id="test", session_id="test", turn_id="test", trace_id="test", input_payload={})
    await jobs.mark_running(job)
    assert await jobs.heartbeat_execution(job.id) is not None
    if terminal == "cancelled":
        await jobs.cancel_job(job.id)
    elif terminal == "resume_requested":
        await jobs.request_resume(job.id)
    else:
        await jobs.mark_completed(job.id, summary="first result", output_payload={"original": True})
    assert await jobs.heartbeat_execution(job.id) is None
    assert await jobs.finish_execution(job.id, status=ToolJobStatus.completed,
        output_payload={"late": True}, summary="late result") is None
    assert (await jobs.get_job(job.id)).status.value == terminal


@pytest.mark.asyncio
async def test_detached_job_returns_partial_output_then_completes_and_cleans_up(tmp_path):
    release, executing, closed = (asyncio.Event() for _ in range(3))

    class Docker:
        async def run(self, args, *, name_prefix, operation):
            self.mount = Path(args[args.index("-v") + 1].rsplit(":", 1)[0])
            try:
                return await operation(SimpleNamespace(container_id="a" * 64, container_name="qa-security-session-test"))
            finally:
                closed.set()

        async def _command(self, args, *, timeout):
            if args[0] == "wait":
                await asyncio.Event().wait()
            if args[0] == "exec":
                log = next((self.mount / ".jobs").glob("*.log"))
                log.write_text("starting\n", encoding="utf-8")
                executing.set()
                await release.wait()
                log.write_text("starting\ndone\n", encoding="utf-8")
            return 0, "", ""

    settings = _Settings()
    settings.orchestration = SimpleNamespace(resource_docker_command_timeout_seconds=5)
    jobs = ToolJobService(InMemoryToolJobStore())
    service = SecurityExecutionEnvironmentService(settings=settings, docker_resources=Docker(), tool_jobs=jobs)
    try:
        started = await service.execute_detached(command="sleep 1 && echo done",
            command_args=["sh", "-lc", "sleep 1 && echo done"], timeout_seconds=30,
            artifact_dir=tmp_path / "artifacts", context=_Context())
        assert started.detached and started.is_running
        await asyncio.wait_for(executing.wait(), 1)
        running = await service.poll_execution(started.job_id, context=_Context())
        assert running.is_running and running.stdout == "starting\n"
        assert not closed.is_set()
        with pytest.raises(ValueError, match="does not belong"):
            await service.poll_execution(started.job_id, context=SimpleNamespace(session_id="foreign"))
        release.set()
        await asyncio.wait_for(service._detached_jobs[started.job_id].task, 1)
        assert closed.is_set()
        completed = await service.poll_execution(started.job_id, context=_Context())
        assert completed.is_running is False and completed.exit_code == 0
        assert completed.execution_observation == "command_returned"
        assert completed.stdout == "starting\ndone\n"
        assert (await jobs.get_job(started.job_id)).status.value == "completed"
        remote = SecurityExecutionEnvironmentService(settings=settings, tool_jobs=jobs)
        restored = await remote.poll_execution(started.job_id, context=_Context())
        assert restored.stdout == completed.stdout and restored.exit_code == 0
        assert restored.is_running is False and restored.execution_observation == "command_returned"
    finally:
        release.set()
        await service.close()


def test_container_file_paths_cannot_escape_workdir(tmp_path: Path):
    service = SecurityExecutionEnvironmentService(settings=_Settings(), workspace_root=tmp_path,
        tool_jobs=ToolJobService(InMemoryToolJobStore()))
    assert service._validated_container_path("inputs/words.txt", "/work") == "/work/inputs/words.txt"
    assert service._validated_container_path("/work/results/out.json", "/work") == "/work/results/out.json"

    with pytest.raises(ValueError, match="traversal"):
        service._validated_container_path("../etc/passwd", "/work")
    with pytest.raises(ValueError, match="stay inside"):
        service._validated_container_path("/etc/passwd", "/work")


def test_get_file_rejects_host_destination_outside_artifact_dir(
    tmp_path: Path,
):
    service = SecurityExecutionEnvironmentService(settings=_Settings(), workspace_root=tmp_path,
        tool_jobs=ToolJobService(InMemoryToolJobStore()))

    with pytest.raises(ValueError, match="inside artifact_dir"):
        asyncio.run(
            service.get_file(
                container_path="results/out.json",
                local_path=tmp_path.parent / "escaped.json",
                artifact_dir=tmp_path / "artifacts",
                container_name="qa-security-test-container",
            )
        )


def test_detached_execution_rejects_local_backend(tmp_path: Path):
    settings = _Settings()
    settings.security.security_runner_backend = "local"
    service = SecurityExecutionEnvironmentService(settings=settings, workspace_root=tmp_path,
        tool_jobs=ToolJobService(InMemoryToolJobStore()))
    with pytest.raises(ValueError, match="requires the Docker runner"):
        asyncio.run(
            service.execute_detached(
                command="echo no",
                command_args=["echo", "no"],
                timeout_seconds=5,
                artifact_dir=tmp_path / "artifacts",
            )
        )


def test_persistent_security_session_reuses_campaign_container_and_closes(tmp_path: Path):
    environment = _PersistentEnvironment()
    session = SecurityShellSession(
        environment=environment,
        campaign_id="campaign-p1",
        target_allowlist=["http://localhost:8089"],
        approval_scope_hash="scope-p1",
        artifact_dir=tmp_path / "artifacts",
        timeout_seconds=60,
        command_timeout_seconds=10,
    )

    asyncio.run(session.create())
    asyncio.run(session.exec(command="printf state", target="http://localhost:8089"))
    asyncio.run(session.exec(command="printf result", target="http://localhost:8089"))
    assert len(environment.created) == 1
    assert len(environment.executed) == 2
    assert environment.executed[0][0] == environment.executed[1][0]
    assert asyncio.run(session.heartbeat()) is True
    assert session.read_output()["cursor"] == 2
    asyncio.run(session.close("completed"))
    assert session.status == "closed"
    assert environment.cleaned == ["qa-security-runner-attack-campaign-p1"]
    assert asyncio.run(session.heartbeat()) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["container_exit", "explicit_close"])
async def test_docker_session_repeated_close_does_not_interrupt_cleanup(trigger):
    from src.runtime.docker_resource_session import DockerResourceSession

    exited, cleanup_started, finish_cleanup, retired = (asyncio.Event() for _ in range(4))

    class Manager:
        async def _command(self, args, *, timeout):
            if args[0] == "wait":
                await exited.wait()
            return 0, "0", ""

        async def run(self, args, *, name_prefix, operation):
            try:
                return await operation(SimpleNamespace(container_id="a" * 64, container_name="test"))
            finally:
                cleanup_started.set()
                await finish_cleanup.wait()
                retired.set()

    session = DockerResourceSession(Manager(), command_timeout=1)
    closers = []
    try:
        await session.start([], name_prefix="test", lifetime_seconds=10)
        if trigger == "container_exit":
            exited.set()
        else:
            closers.append(asyncio.create_task(session.close()))
        await asyncio.wait_for(cleanup_started.wait(), 1)
        closers.append(asyncio.create_task(session.close()))
        await asyncio.sleep(0)
        finish_cleanup.set()
        await asyncio.wait_for(asyncio.gather(*closers), 1)
        assert retired.is_set()
    finally:
        finish_cleanup.set()
        await session.close()


@pytest.mark.asyncio
async def test_campaign_resource_wait_preserves_resume_stage_without_settlement(monkeypatch):
    from src.modes.security_testing_mode.campaign_state import SecurityTestingState
    from src.runtime.resource_lease_manager import ResourceUnavailable

    runtime = SecurityTestingModeRuntime()
    state = SecurityTestingState(session_id="resource-wait", campaign=SecurityCampaign(campaign_id="campaign-wait"),
        execution_checkpoint={"post_recon_stage": 0})
    calls = []
    attempts = 0

    async def persistent(current, context):
        calls.append("persistent")
        return current

    async def chain(current, context):
        nonlocal attempts
        attempts += 1
        calls.append("chain")
        runtime._checkpoint_execution_state(state=current, context=context, event_type="chain_running")
        if attempts == 1:
            raise ResourceUnavailable("global:all")
        return current

    async def coder(current, context):
        calls.append("coder")
        return current

    async def stop_after_stages(current, context):
        current.phase = "interrupted"
        return current

    async def must_not_finalize(*args, **kwargs):
        raise AssertionError("Resource waiting cannot produce terminal delivery")

    monkeypatch.setattr(runtime, "_restore_state", lambda _: state)
    monkeypatch.setattr(runtime, "_attach_runtime_context", lambda *_: None)
    monkeypatch.setattr(runtime, "_persist_state", lambda *_: None)
    monkeypatch.setattr(runtime, "_run_persistent_attack_session", persistent)
    monkeypatch.setattr(runtime, "_run_attack_chain", chain)
    monkeypatch.setattr(runtime, "_run_exploit_coder", coder)
    monkeypatch.setattr(runtime, "_run_callback_broker", stop_after_stages)
    monkeypatch.setattr(runtime, "_ensure_terminal_delivery", must_not_finalize)
    context = ToolExecutionContext(session_id="resource-wait", turn_id="turn", trace_id="trace",
        user_message="test", normalized_input="test", context_bundle={})
    result = await runtime.handle({"objective": "test"}, context)
    assert result["status"] == "waiting_resource" and result["waiting_reason"] == "global:all"
    assert state.execution_checkpoint["post_recon_stage"] == 1
    assert state.campaign.settlement is None or not state.campaign.settlement.finalized_at
    await runtime._advance(state, context)
    assert calls == ["persistent", "chain", "chain", "coder"]
    assert "waiting_reason" not in state.execution_checkpoint


@pytest.mark.asyncio
async def test_exploit_workspace_capacity_is_not_a_verifier_failure(tmp_path):
    from src.runtime.resource_lease_manager import ResourceUnavailable

    class BusyEnvironment(_ExploitEnvironment):
        async def create_persistent_container(self, **kwargs):
            raise ResourceUnavailable("docker_capacity")

    workspace = ExploitWorkspace(environment=BusyEnvironment(), campaign_id="campaign-wait", hypothesis_id="hyp-wait",
        target="http://localhost:8089", artifact_dir=tmp_path)
    with pytest.raises(ResourceUnavailable, match="docker_capacity"):
        await workspace.run(source="import json, sys, urllib.request\n"
            "response = urllib.request.urlopen(sys.argv[1], timeout=1)\n"
            "print(json.dumps({'observed': 'fixture'}))\n")
    assert not workspace.workspace_dir.exists()


def test_persistent_security_session_rejects_out_of_scope_target_and_destructive_command(tmp_path: Path):
    environment = _PersistentEnvironment()
    session = SecurityShellSession(
        environment=environment,
        campaign_id="campaign-p1",
        target_allowlist=["http://localhost:8089"],
        approval_scope_hash="scope-p1",
        artifact_dir=tmp_path / "artifacts",
    )
    asyncio.run(session.create())
    with pytest.raises(ValueError, match="outside the authorized allowlist"):
        asyncio.run(session.exec(command="printf state", target="http://localhost:3000"))
    with pytest.raises(ValueError, match="denied pattern"):
        asyncio.run(session.exec(command="rm -rf /work", target="http://localhost:8089"))
    asyncio.run(session.close("test"))


def test_exploit_workspace_rejects_dangerous_generated_source_before_container(tmp_path: Path):
    environment = _ExploitEnvironment()
    workspace = ExploitWorkspace(
        environment=environment,
        campaign_id="campaign-p2",
        hypothesis_id="hyp-p2",
        target="http://localhost:8089",
        artifact_dir=tmp_path / "artifacts",
    )
    source = "import subprocess\nsubprocess.run(['id'])\n"
    result = asyncio.run(workspace.run(source=source))

    assert result.status == "failed"
    assert result.failure_category == "static_check_failed"
    assert result.cleanup_complete is True
    assert environment.created == []
    assert environment.executed == []


def test_exploit_workspace_compiles_executes_hashes_and_cleans(tmp_path: Path):
    environment = _ExploitEnvironment()
    workspace = ExploitWorkspace(
        environment=environment,
        campaign_id="campaign-p2",
        hypothesis_id="hyp-p2",
        target="http://localhost:8089",
        artifact_dir=tmp_path / "artifacts",
    )
    source = """import json
import sys
import urllib.error
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

target = sys.argv[1]
request = urllib.request.Request(target, method=\"GET\")
opener = urllib.request.build_opener(NoRedirect)
try:
    response = opener.open(request, timeout=10)
except urllib.error.HTTPError as exc:
    response = exc
print(json.dumps({\"status_code\": response.code, \"observed\": \"headers collected\", \"impact\": \"none\"}))
"""
    result = asyncio.run(workspace.run(source=source))

    assert result.status == "completed"
    assert result.static_check_status == "passed"
    assert result.compile_exit_code == 0
    assert result.execute_exit_code == 0
    assert len(result.source_hash) == 64
    assert len(result.artifact_hash) == 64
    assert result.impact_verdict == "missing header confirmed"
    assert result.cleanup_complete is True
    assert Path(result.workspace_path).exists() is False
    assert len(environment.created) == 1
    assert len(environment.executed) == 2
    assert environment.cleaned == [result.container_name]


def test_exploit_workspace_uses_short_single_level_mount_path(tmp_path: Path):
    environment = _ExploitEnvironment()
    artifact_dir = tmp_path / "p2" / "campaign1234"
    workspace = ExploitWorkspace(
        environment=environment,
        campaign_id="campaign-p2",
        hypothesis_id="hyp-p2",
        target="http://localhost:8089",
        artifact_dir=artifact_dir,
    )

    relative = workspace.workspace_dir.relative_to(artifact_dir)
    assert workspace.workspace_id.startswith("ew-")
    assert len(workspace.workspace_id) == len("ew-") + 12
    assert relative.parts == ("_security_attack_session_work", workspace.workspace_id)
    assert len(str(workspace.workspace_dir)) < 180


def test_callback_broker_records_only_loopback_metadata_and_releases_port():
    port = _free_loopback_port()

    async def exercise():
        broker = SecurityCallbackBroker(
            port_range=f"{port}-{port}",
            lease_timeout_seconds=30,
        )
        lease = await broker.lease(
            campaign_id="campaign-p3",
            target="http://localhost:8089",
            approval_scope_hash="scope-p3",
        )
        assert lease.callback_url == f"http://127.0.0.1:{port}/callback/{lease.lease_id}"
        reader, writer = await asyncio.open_connection("127.0.0.1", lease.port)
        writer.write(
            (
                f"GET /callback/{lease.lease_id} HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "X-Do-Not-Store: secret-value\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert b"204 No Content" in response

        observed = broker.get(lease.lease_id)
        assert observed is not None
        assert observed.callback_count == 1
        assert observed.callback_sources == ["127.0.0.1"]
        assert "secret-value" not in observed.model_dump_json()

        released = await broker.release(lease.lease_id, reason="test_complete")
        assert released.status == "released"
        assert released.cleanup_complete is True
        await broker.close()

    asyncio.run(exercise())


def test_security_graph_store_persists_relationships_without_secret_values():
    provider = _GraphProvider()
    store = SecurityGraphStore(object(), provider=provider)
    campaign = SecurityCampaign(
        campaign_id="campaign-p3",
        target_fingerprint="fingerprint-p3",
        objective="Authorized P3 graph test",
        assets=[
            AssetNode(asset_id="asset-web", asset_type="web_app", address="http://localhost:8089"),
            AssetNode(asset_id="asset-api", asset_type="api_endpoint", address="http://localhost:8089/api"),
        ],
        asset_relations=[
            AssetRelation(
                relation_id="rel-1",
                source_asset_id="asset-web",
                relation="reaches",
                target_asset_id="asset-api",
                confidence=0.9,
                evidence_ids=["ev-1"],
            )
        ],
        credential_references=[
            CredentialReference(
                credential_ref_id="cred-1",
                auth_type="bearer",
                principal_hint="admin",
                secret_present=True,
            )
        ],
        access_proofs=[
            AccessProof(
                proof_id="proof-1",
                target="http://localhost:8089",
                principal="admin",
                privilege="read_only",
                credential_ref_id="cred-1",
                evidence_ids=["ev-2"],
            )
        ],
    )

    result = asyncio.run(store.persist_campaign(campaign))

    assert result.status == "completed"
    assert result.backend == "memgraph"
    assert result.node_count >= 5
    assert result.relation_count >= 3
    serialized = "\n".join(str(item) for item in provider.writes)
    assert "REACHES" in serialized
    assert "secret-value" not in serialized
    assert "token" not in serialized.lower()
    assert "password" not in serialized.lower()


def test_security_graph_store_degrades_when_memgraph_is_unavailable():
    store = SecurityGraphStore(object(), provider=_GraphProvider(error=RuntimeError("offline")))
    result = asyncio.run(store.persist_campaign(SecurityCampaign(campaign_id="campaign-p3")))

    assert result.status == "unavailable"
    assert result.backend == "memgraph"
    assert "offline" in result.detail


def test_p3_callback_runtime_is_explicit_and_releases_before_settlement():
    port = _free_loopback_port()
    broker = SecurityCallbackBroker(port_range=f"{port}-{port}")
    state = __import__(
        "src.modes.security_testing_mode.campaign_state",
        fromlist=["SecurityTestingRequestState", "SecurityTestingState", "TargetCandidate"],
    )
    request = state.SecurityTestingRequestState(
        objective="P3 callback broker acceptance",
        raw_message="Run P3 callback broker acceptance",
    )
    campaign = SecurityCampaign(
        campaign_id="campaign-p3-runtime",
        targets=[state.TargetCandidate(target_id="target-1", value="http://localhost:8089")],
    )
    testing_state = state.SecurityTestingState(request=request, campaign=campaign)
    runtime = SecurityTestingModeRuntime(
        settings=type(
            "Settings",
            (),
            {
                "security": SimpleNamespace(
                    security_callback_broker_enabled=True,
                    security_callback_port_range=f"{port}-{port}",
                    security_callback_lease_timeout_seconds=30,
                    security_runner_output_summary_threshold_bytes=16384,
                    security_target_allowlist="127.0.0.1",
                ),
                "security_callback_broker_enabled": True,
                "security_callback_port_range": f"{port}-{port}",
                "security_callback_lease_timeout_seconds": 30,
                "security_runner_output_summary_threshold_bytes": 16384,
            },
        )(),
        callback_broker=broker,
    )
    context = type(
        "Context",
        (),
        {"context_bundle": {"trusted_security_authorization": {"approval_scope_hash": "scope-p3"}}},
    )()

    async def exercise():
        updated = await runtime._run_callback_broker(testing_state, context)
        assert len(updated.campaign.callback_leases) == 1
        assert updated.campaign.callback_leases[0].status == "active"
        await runtime._cleanup_callback_leases(updated)
        assert updated.campaign.callback_leases[0].status == "released"
        assert updated.campaign.callback_leases[0].cleanup_complete is True

    asyncio.run(exercise())


def test_docker_localhost_targets_are_rewritten_after_authorization(tmp_path: Path):
    service = SecurityExecutionEnvironmentService(settings=_Settings(), workspace_root=tmp_path,
        tool_jobs=ToolJobService(InMemoryToolJobStore()))
    command, args = service._rewrite_localhost_for_docker(
        "httpx -u http://localhost:8089 && curl http://127.0.0.1:3000/",
        ["httpx", "-u", "http://localhost:8089", "http://127.0.0.1:3000/"],
    )
    assert "localhost" not in command
    assert "127.0.0.1" not in command
    assert command.count("host.docker.internal") == 2
    assert args == [
        "httpx",
        "-u",
        "http://host.docker.internal:8089",
        "http://host.docker.internal:3000/",
    ]


def test_p4_bootstrap_rejects_version_when_only_unversioned_package_is_allowlisted(
    tmp_path: Path,
):
    service = ToolBootstrapService(settings=_BootstrapSettings(), workspace_root=tmp_path)

    manifest = asyncio.run(
        service.run(
            campaign_id="campaign-p4",
            target_allowlist=["http://localhost:8089"],
            profile_key="tcpdump_timed_capture",
            tool_name="tcpdump",
            requested_version="4.99.5",
            approval_scope_hash="scope-p4",
            approval_granted=True,
            artifact_dir=tmp_path / "artifacts",
        )
    )

    assert manifest.status == "failed"
    assert manifest.failure_category == "package_not_allowlisted"
    assert manifest.container_name == ""
    assert manifest.cleanup_complete is False


def test_p4_bootstrap_uses_temporary_container_for_preinstalled_tool_and_cleans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    docker = _BootstrapDocker([_completed(stdout="/usr/sbin/tcpdump\n4.99.5\n")])
    service = ToolBootstrapService(settings=_BootstrapSettings(), workspace_root=tmp_path, docker_resources=docker)
    monkeypatch.setattr(
        "src.application.security.tool_bootstrap_service.shutil.which",
        lambda _: "docker",
    )
    monkeypatch.setattr(
        service,
        "_image_digest",
        lambda _docker, _image: "vxcontrol/kali-linux@sha256:trusted",
    )

    manifest = asyncio.run(
        service.run(
            campaign_id="campaign-p4",
            target_allowlist=["http://localhost:8089"],
            profile_key="tcpdump_timed_capture",
            tool_name="tcpdump",
            approval_scope_hash="scope-p4",
            approval_granted=True,
            artifact_dir=tmp_path / "artifacts",
        )
    )

    assert manifest.status == "already_available"
    assert manifest.cleanup_complete is True
    assert docker.created == [manifest.container_name]
    assert docker.removed == [manifest.container_name]
    assert len(docker.commands) == 1
    assert manifest.install_command == ""
    assert manifest.resolved_version == "4.99.5"
    assert Path(manifest.manifest_path).is_file()


def test_p4_bootstrap_preserves_install_failure_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    settings = _BootstrapSettings()
    settings.security.security_tool_bootstrap_package_allowlist = "tcpdump@9.9.9"
    docker = _BootstrapDocker([
        _completed(returncode=1, stderr="tcpdump is missing"),
        _completed(returncode=100, stderr="E: Version '9.9.9' for 'tcpdump' was not found"),
    ])
    service = ToolBootstrapService(settings=settings, workspace_root=tmp_path, docker_resources=docker)
    monkeypatch.setattr(
        "src.application.security.tool_bootstrap_service.shutil.which",
        lambda _: "docker",
    )
    monkeypatch.setattr(
        service,
        "_image_digest",
        lambda _docker, _image: "vxcontrol/kali-linux@sha256:trusted",
    )

    manifest = asyncio.run(
        service.run(
            campaign_id="campaign-p4",
            target_allowlist=["http://localhost:8089"],
            profile_key="tcpdump_timed_capture",
            tool_name="tcpdump",
            requested_version="9.9.9",
            approval_scope_hash="scope-p4",
            approval_granted=True,
            artifact_dir=tmp_path / "artifacts",
        )
    )

    assert manifest.status == "failed"
    assert manifest.failure_category == "tool_bootstrap_failed"
    assert manifest.install_exit_code == 100
    assert "APT::Sandbox::User=root" in manifest.install_command
    assert "Dir::Etc::sourceparts=-" in manifest.install_command
    assert "https://kali.download/kali" in manifest.install_command
    assert "download.docker.com" not in manifest.install_command
    assert "Version '9.9.9'" in manifest.stderr
    assert manifest.cleanup_complete is True
    assert docker.removed == [manifest.container_name]


@pytest.mark.asyncio
async def test_p4_bootstrap_capacity_wait_does_not_finalize_campaign(monkeypatch, tmp_path):
    from src.core.config import Settings
    from src.core.request_context import get_request_context
    from src.runtime.resource_lease_manager import ResourceUnavailable

    class BusyDocker:
        async def run(self, *args, **kwargs):
            context = get_request_context()
            assert context.session_id == "bootstrap-session" and context.project_id == "bootstrap-project"
            raise ResourceUnavailable("docker_capacity")

    service = ToolBootstrapService(settings=_BootstrapSettings(), docker_resources=BusyDocker())
    monkeypatch.setattr(service, "_image_digest", lambda *_: "vxcontrol/kali-linux@sha256:trusted")
    monkeypatch.setattr("src.application.security.tool_bootstrap_service.shutil.which", lambda _: "docker")
    jobs = ToolJobService(InMemoryToolJobStore())
    runtime = ToolRuntimeService(settings=Settings(), tool_job_service=jobs)
    runtime._security_tool_bootstrap_service = service
    monkeypatch.setattr(runtime, "_prepare_local_artifact_dir", lambda *_: tmp_path)
    def unexpected_projection(**kwargs):
        raise AssertionError("Resource waiting must not finalize Campaign")
    monkeypatch.setattr(runtime, "record_security_tool_bootstrap_campaign", unexpected_projection)
    tool = ToolDescriptor(key="security-tool-bootstrap", name="bootstrap", description="test", category="test")
    context = ToolExecutionContext(session_id="bootstrap-session", turn_id="bootstrap-turn", trace_id="bootstrap-trace",
        user_message="bootstrap", normalized_input="bootstrap", context_bundle={"project_id": "bootstrap-project"},
        server_approval_granted=True, approval_scope_hash="approved-fixture")
    result = await runtime.execute(tool, ModelToolCall(id="bootstrap-call", name=tool.key, arguments={
        "campaign_id": "bootstrap-campaign", "target_allowlist": ["127.0.0.1"], "tool_name": "tcpdump",
        "profile_key": "tcpdump_timed_capture",
    }), context)
    assert result.status == "waiting_resource"
    assert (await jobs.get_job(result.job_id)).status.value == "waiting_resource"
    assert result.output["tool_bootstrap"]["completed_at"] == ""
    assert result.output["error"] == "docker_capacity"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["still_present", "daemon_unavailable", "timeout"])
async def test_p4_bootstrap_cleanup_requires_confirmed_absence(monkeypatch, tmp_path, failure):
    docker = _BootstrapDocker([_completed(stdout="/usr/sbin/tcpdump\n4.99.5\n")])
    original_command = docker._command

    async def command(args, *, timeout):
        if args[0] == "ps":
            if failure == "timeout":
                raise TimeoutError("Docker inventory timed out")
            if failure == "daemon_unavailable":
                return 1, "", "Cannot connect to the Docker daemon"
            return 0, "a" * 64, ""
        return await original_command(args, timeout=timeout)

    docker._command = command
    service = ToolBootstrapService(settings=_BootstrapSettings(), docker_resources=docker)
    monkeypatch.setattr(service, "_image_digest", lambda *_: "vxcontrol/kali-linux@sha256:trusted")
    monkeypatch.setattr("src.application.security.tool_bootstrap_service.shutil.which", lambda _: "docker")
    manifest = await service.run(campaign_id="cleanup-check", target_allowlist=["127.0.0.1"],
        profile_key="tcpdump_timed_capture", tool_name="tcpdump", approval_scope_hash="test-scope",
        approval_granted=True, artifact_dir=tmp_path)
    assert not manifest.cleanup_complete
    assert manifest.status == "failed" and manifest.failure_category == "cleanup_failed"
    assert "pending verification" in manifest.failure_reason


@pytest.mark.asyncio
async def test_p4_bootstrap_allocation_database_failure_persists_failed_manifest(monkeypatch, tmp_path):
    import json
    from psycopg import OperationalError

    class UnavailableAllocation:
        async def run(self, *args, **kwargs):
            raise OperationalError("allocation database unavailable")

    service = ToolBootstrapService(settings=_BootstrapSettings(), docker_resources=UnavailableAllocation())
    monkeypatch.setattr(service, "_image_digest", lambda *_: "vxcontrol/kali-linux@sha256:trusted")
    monkeypatch.setattr("src.application.security.tool_bootstrap_service.shutil.which", lambda _: "docker")
    manifest = await service.run(campaign_id="allocation-failure", target_allowlist=["127.0.0.1"],
        profile_key="tcpdump_timed_capture", tool_name="tcpdump", approval_scope_hash="test-scope",
        approval_granted=True, artifact_dir=tmp_path)
    assert manifest.status == "failed" and manifest.completed_at
    assert manifest.failure_category == "execution_environment_error"
    assert not manifest.cleanup_complete and not manifest.container_name
    assert json.loads(Path(manifest.manifest_path).read_text(encoding="utf-8"))["status"] == "failed"


def test_p4_bootstrap_rejects_configuration_that_disables_mandatory_cleanup(
    tmp_path: Path,
):
    settings = _BootstrapSettings()
    settings.security.security_tool_bootstrap_cleanup_required = False
    service = ToolBootstrapService(settings=settings, workspace_root=tmp_path)

    manifest = asyncio.run(
        service.run(
            campaign_id="campaign-p4",
            target_allowlist=["http://localhost:8089"],
            profile_key="tcpdump_timed_capture",
            tool_name="tcpdump",
            approval_scope_hash="scope-p4",
            approval_granted=True,
            artifact_dir=tmp_path / "artifacts",
        )
    )

    assert manifest.status == "failed"
    assert manifest.failure_category == "cleanup_policy_required"
    assert manifest.container_name == ""


def test_p4_manifest_reference_is_rewritten_to_durable_artifact_uri(
    tmp_path: Path,
):
    class _ArtifactStore:
        async def store_output_artifacts(self, output, **_kwargs):
            artifact = output["artifacts"][0]
            return {
                "artifacts": [
                    {
                        **artifact,
                        "path": "rustfs://qa-agent/p4/manifest.json",
                        "uri": "rustfs://qa-agent/p4/manifest.json",
                        "storage_backend": "rustfs",
                    }
                ]
            }

    manifest_path = tmp_path / "bootstrap.json"
    manifest_path.write_text("{}", encoding="utf-8")
    manifest = type(
        "Manifest",
        (),
        {
            "manifest_path": str(manifest_path),
            "to_dict": lambda self: {
                "bootstrap_id": "bootstrap-p4",
                "manifest_path": self.manifest_path,
            },
        },
    )()
    runtime = object.__new__(ToolRuntimeService)
    runtime._artifact_storage_service = _ArtifactStore()
    result = {
        "tool_bootstrap": {"bootstrap_id": "bootstrap-p4", "manifest_path": str(manifest_path)},
        "artifacts": [{"type": "security_tool_bootstrap_manifest", "path": str(manifest_path)}],
    }
    context = ToolExecutionContext(
        session_id="session-p4",
        turn_id="turn-p4",
        trace_id="trace-p4",
        user_message="",
        normalized_input="",
        context_bundle={},
    )

    asyncio.run(
        runtime._promote_security_bootstrap_manifest_uri(
            result=result,
            manifest=manifest,
            context=context,
        )
    )

    persisted = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.manifest_path == "rustfs://qa-agent/p4/manifest.json"
    assert result["tool_bootstrap"]["manifest_path"] == "rustfs://qa-agent/p4/manifest.json"
    assert result["artifacts"][0]["path"] == "rustfs://qa-agent/p4/manifest.json"
    assert persisted["manifest_path"] == "rustfs://qa-agent/p4/manifest.json"


def test_failed_tool_result_persists_manifest_artifact_and_exposes_download(tmp_path: Path):
    tool_jobs = ToolJobService(InMemoryToolJobStore())
    runtime = ToolRuntimeService(tool_job_service=tool_jobs)
    manifest_path = tmp_path / "bootstrap-failure.json"
    manifest_path.write_text('{"status":"failed"}', encoding="utf-8")
    tool = ToolDescriptor(
        key="security-tool-bootstrap",
        name="Security Tool Bootstrap",
        description="test",
        category="security",
        permission_level="ask",
    )

    async def failed_bootstrap(*_args, **_kwargs):
        return {
            "status": "failed",
            "ok": False,
            "summary": "Fixed installation template returned exit code 100.",
            "error": "tool_bootstrap_failed",
            "artifacts": [
                {
                    "type": "security_tool_bootstrap_manifest",
                    "label": "bootstrap-failure.json",
                    "path": str(manifest_path),
                }
            ],
        }

    runtime._handlers["security-tool-bootstrap"] = failed_bootstrap
    result = asyncio.run(
        runtime.execute(
            tool=tool,
            call=ModelToolCall(
                id="call-p4-failure-artifact",
                name=tool.key,
                arguments={},
            ),
            context=ToolExecutionContext(
                session_id="session-p4-failure-artifact",
                turn_id="turn-p4-failure-artifact",
                trace_id="trace-p4-failure-artifact",
                user_message="",
                normalized_input="",
                context_bundle={},
            ),
        )
    )

    assert result.status == "failed"
    assert result.job_id
    saved = asyncio.run(tool_jobs.list_artifacts(tool_job_id=result.job_id))
    assert len(saved) == 1
    assert saved[0].artifact_type == "security_tool_bootstrap_manifest"
    assert saved[0].path == str(manifest_path)
    assert result.output["download_urls"][0]["artifact_id"] == saved[0].id
