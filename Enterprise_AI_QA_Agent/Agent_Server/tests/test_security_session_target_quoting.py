"""Normal OData URL transport through the real persistent-session runner.

Input source: https://learn.microsoft.com/en-us/odata/concepts/queryoptions-overview
Only a synthetic local HTTP service is contacted; no bypass/attack command.
"""
from __future__ import annotations

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.security.execution_environment_service import (
    SecurityExecutionEnvironmentService,
)
from src.core.request_context import bind_request_context
from src.modes.security_testing_mode.campaign_state import (
    SecurityCampaign,
    SecurityTestingRequestState,
    SecurityTestingState,
    TargetCandidate,
)
from src.modes.security_testing_mode.runtime import SecurityTestingModeRuntime
from src.runtime.docker_resource_manager import DockerResourceManager
from tests.test_live_resource_leases import (
    _complete_real_cleanup,
    _security_foreground_settings,
)

pytest_plugins = ["tests.test_live_resource_leases"]


@pytest.mark.skipif(os.environ.get("RUN_LIVE_DOCKER_TESTS") != "1", reason="requires local Kali Docker image/Redis/PG")
@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "?$filter=Name%20eq%20'Milk'&$select=Name"])
async def test_persistent_session_preserves_normal_odata_query(manager, tmp_path, monkeypatch, query):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"value":[{"Name":"Milk"}]}')

        def log_message(self, *_args):
            pass  # Synthetic fixture only; captured request paths are asserted below.

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = f"http://127.0.0.1:{server.server_port}/Products{query}"
    lease_manager, _ = manager
    settings = _security_foreground_settings()
    # The lifecycle-only fixture uses --network none. This HTTP transport test
    # requires the normal Docker Desktop network to reach its local host fixture.
    settings.security.security_runner_docker_network = ""
    settings.security.security_target_allowlist = "127.0.0.1"
    settings.security.security_attack_session_enabled = True
    environment = SecurityExecutionEnvironmentService(
        settings=settings, docker_resources=DockerResourceManager(settings, lease_manager=lease_manager),
    )
    runtime = SecurityTestingModeRuntime(settings=settings, execution_environment_service=environment)
    monkeypatch.setattr(runtime, "_attack_session_artifact_dir", lambda *_args: tmp_path)
    context = ToolExecutionContext(session_id="s-odata-transport", turn_id="t-odata", trace_id="trace-odata",
                                   user_message="persistent session", normalized_input="persistent session",
                                   context_bundle={"project_id": "p-local-odata"})
    state = SecurityTestingState(request=SecurityTestingRequestState(objective="persistent session", target_url=target),
                                 campaign=SecurityCampaign(campaign_id="normal-odata-query",
                                                           targets=[TargetCandidate(value=target)]))
    try:
        with bind_request_context(attempt_id="a-live-perf"):
            await runtime._run_persistent_attack_session(state, context)
        assert state.campaign.shell_session.status == "completed", state.campaign.shell_session.close_reason
        assert len(paths) == 1
        assert parse_qs(urlsplit(paths[0]).query) == parse_qs(urlsplit(target).query)
    finally:
        await environment.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        if await lease_manager.list_cleanup_pending():
            await _complete_real_cleanup(lease_manager)
