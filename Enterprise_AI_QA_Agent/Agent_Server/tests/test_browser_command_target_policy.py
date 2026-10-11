"""Navigation targets use the same command semantics at admission and execution."""
import json
import os
from pathlib import Path

import pytest

from src.application.security.execution_safety_policy import ExecutionSafetyPolicy
from src.registry.tools import ToolRegistry


def captured_navigation():
    source = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p0-tool-evidence-production/untrusted-target/postgres-execution.json"
    data = json.loads(source.read_bytes())
    records = data["tables"]["postgres_snapshot_table"]["records"][-1]["graph_state"]["tool_results"]
    return next(row["input"] for row in records if row["tool_key"] == "browser-control"
        and row["status"] == "completed" and row["input"].get("command") == "open")


def test_actual_command_navigation_cannot_skip_existing_private_target_policy():
    decision = ExecutionSafetyPolicy().evaluate_tool_call(tool=ToolRegistry().get("browser-control"),
        arguments=captured_navigation(), active_mode_key="ui_automation", context={})
    assert decision.behavior == "deny" and decision.reason_code == "untrusted_private_target"


@pytest.mark.parametrize("payload", [
    {"args": ["open", "http://127.0.0.1:8080/"]},
    {"args": ["--raw", "-s=ignored", "open", "--headed", "http://127.0.0.1:8080/"]},
    {"command_args": ["goto", "http://127.0.0.1:8080/"]},
    {"command": "tab-new http://127.0.0.1:8080/"},
    {"args": "explore http://127.0.0.1:8080/ --max-pages=1"},
])
def test_all_existing_command_representations_preserve_trusted_scope(payload):
    tool = ToolRegistry().get("browser-control")
    policy = ExecutionSafetyPolicy()
    arguments = {"action": "command", **payload}
    assert policy.evaluate_tool_call(tool=tool, arguments=arguments,
        active_mode_key="ui_automation", context={}).reason_code == "untrusted_private_target"
    assert policy.evaluate_tool_call(tool=tool, arguments=arguments,
        active_mode_key="ui_automation", context={"trusted_resource_scope": {
            "project_url": "http://127.0.0.1:8080"}}).behavior == "allow"


@pytest.mark.parametrize("verb", ["click", "fill", "eval", "snapshot"])
def test_non_navigation_arguments_are_not_scanned_as_target_urls(verb):
    decision = ExecutionSafetyPolicy().evaluate_tool_call(tool=ToolRegistry().get("browser-control"),
        arguments={"action": "command", "args": [verb, "http://127.0.0.1:8080/"]},
        active_mode_key="ui_automation", context={})
    assert decision.behavior == "allow"


def test_metadata_target_stays_blocked_even_with_scope_and_other_tools_unchanged():
    policy = ExecutionSafetyPolicy()
    arguments = {"action": "command", "args": ["open", "http://169.254.169.254/"]}
    assert policy.evaluate_tool_call(tool=ToolRegistry().get("browser-control"), arguments=arguments,
        active_mode_key="ui_automation", context={"trusted_resource_scope": {
            "project_url": "http://169.254.169.254"}}).reason_code == "blocked_network_target"
    assert policy.evaluate_tool_call(tool=ToolRegistry().get("report-writer"), arguments=arguments,
        active_mode_key="ui_automation", context={}).behavior == "allow"


def test_malformed_command_is_logged_and_left_to_existing_runtime_validation(caplog):
    from src.application.runtime.browser_commands import browser_command_args
    payload = {"action": "command", "command": 'open "unfinished'}
    decision = ExecutionSafetyPolicy().evaluate_tool_call(tool=ToolRegistry().get("browser-control"),
        arguments=payload, active_mode_key="ui_automation", context={})
    assert decision.behavior == "allow"
    assert "browser_command_target_unparsed" in caplog.text
    with pytest.raises(ValueError):
        browser_command_args(payload)


def test_actual_default_ui_split_command_preserves_verb_and_navigation_admission():
    from src.application.runtime.browser_commands import browser_command_args
    source = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-default-ui-live1/positive/tool-jobs.json"
    captured = next(job["input_payload"] for job in json.loads(source.read_bytes())
        if job["tool_key"] == "browser-control" and job["input_payload"].get("command") == "open")
    assert browser_command_args(captured) == ["open", captured["args"][0]]
    policy = ExecutionSafetyPolicy()
    tool = ToolRegistry().get("browser-control")
    assert policy.evaluate_tool_call(tool=tool, arguments=captured,
        active_mode_key="ui_automation", context={}).reason_code == "untrusted_private_target"
    assert policy.evaluate_tool_call(tool=tool, arguments=captured, active_mode_key="ui_automation",
        context={"trusted_resource_scope": {"project_url": captured["args"][0]}}).behavior == "allow"


@pytest.mark.parametrize("arguments", [
    {"command": "open", "args": ["open", "http://127.0.0.1:8080/"]},
    {"command": "open", "args": ["--raw", "-s=ignored", "open", "http://127.0.0.1:8080/"]},
    {"command": "open", "args": ["http://127.0.0.1:8080/"]},
])
def test_complete_and_split_commands_have_same_navigation_target(arguments):
    from src.application.runtime.browser_commands import browser_command_navigation_target
    assert browser_command_navigation_target({"action": "command", **arguments}) == "http://127.0.0.1:8080/"


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_BROWSER_TESTS") != "1", reason="requires actual Chromium")
async def test_actual_graph_admission_blocks_command_before_http_and_trusted_command_reaches_browser(tmp_path):
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from uuid import uuid4
    import asyncio
    import shutil
    from src.application.context.mcp_runtime_service import MCPRuntimeService
    from src.application.permissions.permission_service import PermissionService
    from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
    from src.application.runtime.tool_job_service import ToolJobService
    from src.core.config import get_settings
    from src.graph.nodes.tool_executor import _resolve_tool_call
    from src.registry.mcp import MCPRegistry
    from src.runtime.tool_job_store import InMemoryToolJobStore
    from src.infrastructure.storage_utils import make_json_safe
    from src.schemas.tool_runtime import ModelToolCall
    from test_tool_executor_safety_gate import _state

    requests = []
    confirmation = "actual-page-" + uuid4().hex
    raw = f"<!doctype html><h1>{confirmation}</h1>".encode()
    (tmp_path / "page.html").write_bytes(raw)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}/"
    settings = get_settings().model_copy(deep=True)
    settings.storage.artifact_root_dir = str(tmp_path / "artifacts")
    settings.orchestration.browser_headless = True
    mcp = MCPRuntimeService(MCPRegistry(), settings)
    jobs = ToolJobService(InMemoryToolJobStore())
    await jobs.initialize()
    runtime = ToolRuntimeService(settings=settings, mcp_runtime_service=mcp, tool_job_service=jobs)
    try:
        arguments = {**captured_navigation(), "command": "open", "args": [origin]}
        state = _state("browser-control", arguments, mode_key="ui_automation")
        context = ToolExecutionContext(session_id=state["session_id"], turn_id=state["turn_id"],
            trace_id=state["trace_id"], user_message="受控页面", normalized_input="受控页面", context_bundle={})
        async def resolve(call, scope):
            state["context_bundle"] = scope
            return await _resolve_tool_call(state=state, tool_call=call, tool_registry=ToolRegistry(),
                permission_service=PermissionService(), tool_runtime_service=runtime,
                tool_job_service=jobs, tool_context=replace(context, context_bundle=scope))
        blocked = await resolve(ModelToolCall(id="blocked-navigation", name="browser-control", arguments=arguments), {})
        assert blocked["tool_result"]["status"] == "denied" and not requests
        assert blocked["tool_result"]["output"]["permission_reason_code"] == "untrusted_private_target"
        assert json.loads(blocked["tool_message"]["content"])["output_reference"] is None
        scope = {"trusted_resource_scope": {"project_url": origin}}
        admitted = await resolve(ModelToolCall(id="trusted-navigation", name="browser-control", arguments=arguments), scope)
        assert admitted["tool_result"]["status"] == "completed" and requests
        snapshot = await resolve(ModelToolCall(id="trusted-snapshot", name="browser-control",
            arguments={"action": "command", "args": ["snapshot"]}), scope)
        assert snapshot["tool_result"]["status"] == "completed"
        assert confirmation in snapshot["tool_result"]["output"]["stdout"]
        saved = await jobs.get_job(snapshot["tool_result"]["job_id"])
        assert confirmation in saved.output_payload["stdout"]
        output = os.getenv("QA_BROWSER_COMMAND_EVIDENCE_DIR")
        if output:
            evidence = Path(output)
            evidence.mkdir(parents=True, exist_ok=False)
            (evidence / "page.html").write_bytes(raw)
            (evidence / "verification.json").write_text(json.dumps(make_json_safe({"target": origin, "requests": requests,
                "blocked": blocked, "admitted": admitted, "snapshot": snapshot,
                "persisted_job": saved.model_dump(mode="json"), "confirmation": confirmation}),
                ensure_ascii=False, indent=2), encoding="utf-8")
            shutil.copytree(tmp_path / "artifacts", evidence / "artifacts")
    finally:
        await mcp.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        await asyncio.to_thread(thread.join, 5)
        assert not thread.is_alive()
