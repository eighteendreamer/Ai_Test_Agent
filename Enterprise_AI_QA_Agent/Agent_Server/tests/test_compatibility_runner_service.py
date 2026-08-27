from __future__ import annotations

import base64
import asyncio
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any
from uuid import uuid4


from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.routes.compatibility import router as compatibility_router
from src.application.compatibility import runner_client as runner_client_module
from src.application.compatibility.runner_client import CompatibilityRunnerClient
from src.application.compatibility.runner_service import CompatibilityRunnerNotFound, CompatibilityRunnerService
from src.modes.compatibility_testing_mode.case_generator import CompatibilityCaseGenerator
from src.modes.compatibility_testing_mode.contracts import (
    AuthProfile,
    CompatibilityDispatchPlan,
    CompatibilityModeCall,
    ProductProfile,
    CompatibilityCase,
    EnvironmentSpec,
    CompatibilityRunnerTask,
    RunnerSelector,
)
from src.modes.compatibility_testing_mode.mode_invoker import CompatibilityModeInvocationPlanner
from src.modes.compatibility_testing_mode.runtime import CompatibilityTestingModeRuntime
from src.registry.tools import ToolRegistry
from src.schemas.compatibility_runner import (
    CompatibilityArtifactUploadRequest,
    CompatibilityRunnerCleanupRequest,
    CompatibilityRunnerHeartbeatRequest,
    CompatibilityRunnerRegistrationRequest,
    CompatibilityRunnerTaskReportRequest,
    CompatibilityTaskRequeueRequest,
)
from src.schemas.tool_runtime import ToolExecutionRecord


class _Settings:
    def __init__(self, root) -> None:
        self.data_dir = str(root / "data")
        self.artifact_root_dir = str(root / "artifacts")
        self.compatibility_runner_heartbeat_timeout_seconds = 120


class _FakeToolRegistry:
    def get(self, key: str):
        if key != "smoke-suite-runner":
            raise KeyError(key)
        return SimpleNamespace(key=key, name="Smoke Suite Runner")


class _FakeToolRuntimeService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, tool, call, context):
        self.calls.append(
            {
                "tool_key": tool.key,
                "arguments": call.arguments,
                "runner_id": context.context_bundle.get("compatibility_runner_id"),
                "task_id": context.context_bundle.get("compatibility_task", {}).get("task_id"),
            }
        )
        return ToolExecutionRecord(
            call_id=call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="completed",
            summary=f"executed {tool.key}",
            trace_id=context.trace_id,
            input=call.arguments,
            output={"ok": True, "runner_id": context.context_bundle.get("compatibility_runner_id")},
        )


class _FailingToolRuntimeService:
    async def execute(self, tool, call, context):
        raise RuntimeError("mode runtime exploded")


class _PartialToolRuntimeService:
    async def execute(self, tool, call, context):
        return ToolExecutionRecord(
            call_id=call.id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="partial",
            summary="mode call requires follow-up approval",
            trace_id=context.trace_id,
            input=call.arguments,
            output={"ok": False, "reason": "approval_required"},
        )


class _ReadableArtifactStorage:
    enabled = True

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    async def store_uploaded_bytes(self, **kwargs):
        filename = kwargs.get("filename") or "artifact.bin"
        object_prefix = kwargs.get("object_prefix") or "compatibility"
        uri = f"rustfs://qa-agent/{object_prefix}/{filename}"
        self.objects[uri] = {
            "content": kwargs.get("content") or b"",
            "content_type": kwargs.get("content_type") or "application/octet-stream",
        }
        return {
            "uri": uri,
            "storage_backend": "rustfs",
            "bucket": "qa-agent",
            "object_name": f"{object_prefix}/{filename}",
        }

    async def read_object_uri(self, uri: str) -> dict[str, Any]:
        return self.objects[uri]


def test_runner_queue_artifact_report_and_requeue():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_service_{uuid4().hex}"
    try:
        asyncio.run(_exercise_runner_service(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runner_restart_requeues_assigned_tasks_without_stale_capacity():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_restart_{uuid4().hex}"
    try:
        asyncio.run(_exercise_runner_restart_recovery(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_offline_runner_does_not_poll_new_tasks():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_offline_poll_{uuid4().hex}"
    try:
        asyncio.run(_exercise_offline_runner_poll_guard(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_offline_runner_cleanup_prunes_only_safe_records():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_cleanup_{uuid4().hex}"
    try:
        asyncio.run(_exercise_offline_runner_cleanup(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runner_with_unknown_os_does_not_match_os_specific_tasks():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_os_guard_{uuid4().hex}"
    try:
        asyncio.run(_exercise_runner_os_match_guard(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runner_without_required_device_does_not_match_device_task():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_device_guard_{uuid4().hex}"
    try:
        asyncio.run(_exercise_runner_device_match_guard(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runner_with_wrong_os_version_does_not_match_versioned_task():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_os_version_guard_{uuid4().hex}"
    try:
        asyncio.run(_exercise_runner_os_version_match_guard(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runner_with_wrong_browser_version_does_not_match_versioned_task():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_browser_version_guard_{uuid4().hex}"
    try:
        asyncio.run(_exercise_runner_browser_version_match_guard(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_runner_browser_selector_requires_matching_browser_capability():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_browser_guard_{uuid4().hex}"
    try:
        asyncio.run(_exercise_runner_browser_match_guard(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cancelled_runner_task_does_not_complete_report():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_cancelled_{uuid4().hex}"
    try:
        asyncio.run(_exercise_cancelled_runner_report(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stale_runner_heartbeat_requeues_assigned_tasks_live():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_stale_{uuid4().hex}"
    try:
        asyncio.run(_exercise_stale_runner_recovery(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_reregistering_stale_runner_requeues_prior_assignment():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_reregister_stale_{uuid4().hex}"
    try:
        asyncio.run(_exercise_stale_runner_reregister_recovery(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_heartbeat_from_stale_runner_requeues_prior_assignment():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_heartbeat_stale_{uuid4().hex}"
    try:
        asyncio.run(_exercise_stale_runner_heartbeat_recovery(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_artifact_storage_does_not_hold_runner_lock():
    root = Path(__file__).resolve().parent / ".tmp" / f"runner_upload_lock_{uuid4().hex}"
    try:
        asyncio.run(_exercise_artifact_storage_lock_boundary(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_auth_product_does_not_generate_login_case():
    cases = CompatibilityCaseGenerator().generate(
        ProductProfile(
            name="Public Web",
            product_type="web",
            entrypoint="https://example.test",
            auth=AuthProfile(strategy="none"),
        )
    )

    assert all("登录" not in case.name for case in cases)


def test_runtime_drafts_dispatches_and_rejects_invalid_case_selection():
    root = Path(__file__).resolve().parent / ".tmp" / f"compat_runtime_{uuid4().hex}"
    try:
        asyncio.run(_exercise_compatibility_runtime(root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_compatibility_tool_schema_covers_dispatch_fields():
    descriptor = ToolRegistry().get("compatibility-test-runner")
    properties = descriptor.input_schema["properties"]

    dispatch_fields = ("plan", "approved_plan", "confirm_risks", "selected_case_ids", "selected_environment_ids")
    intake_fields = (
        "product_access_manifest",
        "access_manifest",
        "product_version",
        "artifact_type",
        "package_name",
        "activity",
        "bundle_id",
        "mini_program_path",
        "command",
        "base_api",
        "proxy",
        "requires_vpn",
        "exclude",
    )
    for field in (*dispatch_fields, *intake_fields):
        assert field in properties
    assert "runner_summary" in descriptor.output_schema
    assert "recoverable_tasks" in descriptor.output_schema


def test_compatibility_mode_calls_use_registered_tools_for_all_product_types():
    registered_tool_keys = {descriptor.key for descriptor in ToolRegistry().list()}
    planner = CompatibilityModeInvocationPlanner()
    test_case = CompatibilityCase(name="启动并验证核心页面", case_id="case_registered_tools")

    for product_type in (
        "web",
        "h5",
        "android_app",
        "ios_app",
        "wechat_mini_program",
        "alipay_mini_program",
        "linux_app",
    ):
        calls = planner.plan_calls(
            product=ProductProfile(
                name=f"{product_type} product",
                product_type=product_type,
                entrypoint="https://example.test" if product_type in {"web", "h5"} else "artifact://build",
            ),
            environment=EnvironmentSpec(
                environment_id=f"env_{product_type}",
                name=f"{product_type} environment",
                provider="local_browser" if product_type in {"web", "h5"} else "external_device_lab",
            ),
            cases=[test_case],
        )

        assert calls, product_type
        assert {call.tool_key for call in calls}.issubset(registered_tool_keys)
        if product_type not in {"web", "h5"}:
            assert all(call.tool_key == "smoke-suite-runner" for call in calls)
            assert calls[0].arguments["product"]["product_type"] == product_type


def test_runner_client_server_mode_calls_executor_requires_completed_bridge():
    client = CompatibilityRunnerClient(
        base_url="http://127.0.0.1:1032",
        runner_id="runner-client-mode-bridge",
        name="Client Mode Bridge",
        capabilities=["local_browser", "playwright", "chrome"],
        devices=[],
        max_parallel=1,
        poll_interval=1,
        executor="server-mode-calls",
    )
    client.execute_mode_calls = lambda task: {
        "status": "partial",
        "ok": True,
        "summary": "Executed 2 compatibility mode call(s): 1 completed, 1 partial, 0 failed.",
        "mode_call_count": 2,
        "mode_call_results": [
            {"tool_key": "smoke-suite-runner", "status": "partial"},
            {"tool_key": "ui-automation-runner", "status": "completed"},
        ],
    }

    payload = client.execute_task(
        {
            "task_id": "compat_task_client_bridge",
            "environment_id": "env_client_bridge",
            "mode_calls": [
                {"tool_key": "smoke-suite-runner", "arguments": {}},
                {"tool_key": "ui-automation-runner", "arguments": {}},
            ],
        }
    )

    assert payload["status"] == "failed"
    assert payload["error"] == "mode_bridge_partial"
    assert payload["result"]["summary"].startswith("Compatibility mode bridge did not complete (partial):")
    assert payload["result"]["mode_call_count"] == 2
    assert payload["metadata"]["executor"] == "server_mode_calls"
    assert payload["metadata"]["mode_bridge_status"] == "partial"

    client.execute_mode_calls = lambda task: {
        "status": "completed",
        "ok": True,
        "summary": "Executed 2 compatibility mode call(s): 2 completed, 0 partial, 0 failed.",
        "mode_call_count": 2,
        "mode_call_results": [
            {"tool_key": "smoke-suite-runner", "status": "completed"},
            {"tool_key": "ui-automation-runner", "status": "completed"},
        ],
    }
    completed_payload = client.execute_task(
        {
            "task_id": "compat_task_client_bridge_completed",
            "environment_id": "env_client_bridge",
            "mode_calls": [
                {"tool_key": "smoke-suite-runner", "arguments": {}},
                {"tool_key": "ui-automation-runner", "arguments": {}},
            ],
        }
    )
    assert completed_payload["status"] == "completed"
    assert completed_payload["error"] is None
    assert completed_payload["metadata"]["mode_bridge_status"] == "completed"


def test_runner_client_server_mode_calls_rejects_non_web_tasks():
    client = CompatibilityRunnerClient(
        base_url="http://127.0.0.1:1032",
        runner_id="runner-client-non-web-bridge",
        name="Client Non Web Bridge",
        capabilities=["android_appium", "appium"],
        devices=["Pixel"],
        max_parallel=1,
        poll_interval=1,
        executor="server-mode-calls",
    )
    client.execute_mode_calls = lambda task: (_ for _ in ()).throw(AssertionError("bridge should not execute"))

    payload = client.execute_task(
        {
            "task_id": "compat_task_client_non_web_bridge",
            "environment_id": "env_android_pixel",
            "mode_calls": [{"tool_key": "smoke-suite-runner", "arguments": {}}],
            "metadata": {
                "product": {
                    "name": "Android App",
                    "product_type": "android_app",
                    "entrypoint": "artifact://android.apk",
                }
            },
        }
    )

    assert payload["status"] == "failed"
    assert payload["error"] == "executor_requires_external_runner"
    assert payload["metadata"]["unsupported_product_type"] == "android_app"
    assert payload["result"]["planned_mode_calls"] == ["smoke-suite-runner"]


def test_runner_client_register_merges_version_metadata():
    captured: dict[str, Any] = {}
    original_json_request = runner_client_module._json_request

    def fake_json_request(base_url: str, path: str, *, method: str = "GET", payload=None):
        captured.update({"base_url": base_url, "path": path, "method": method, "payload": payload})
        return {"runner_id": payload["runner_id"], "status": "online"}

    runner_client_module._json_request = fake_json_request
    try:
        client = CompatibilityRunnerClient(
            base_url="http://127.0.0.1:1032",
            runner_id="runner-client-version-metadata",
            name="Client Version Metadata",
            capabilities=["android_browser", "playwright_or_appium", "chrome"],
            devices=["Pixel 7"],
            max_parallel=1,
            poll_interval=1,
            executor="dry-run",
            metadata={
                "os_version": "14",
                "browser_versions": {"chrome": "120.0"},
                "executor": "spoofed",
                "runner_kind": "spoofed_runner",
            },
        )
        client.register()
    finally:
        runner_client_module._json_request = original_json_request

    payload = captured["payload"]
    assert captured["path"] == "/api/v1/compatibility/runners/register"
    assert captured["method"] == "POST"
    assert payload["devices"] == ["Pixel 7"]
    assert payload["metadata"]["executor"] == "dry-run"
    assert payload["metadata"]["runner_kind"] == "compatibility_runner_client"
    assert payload["metadata"]["os_version"] == "14"
    assert payload["metadata"]["browser_versions"] == {"chrome": "120.0"}


def test_runner_client_default_base_url_matches_local_backend():
    assert runner_client_module.DEFAULT_BASE_URL == "http://127.0.0.1:1032"


def test_runner_client_run_once_uploads_execution_log_and_reports_uploaded_artifact_ids():
    class _CapturingRunnerClient(CompatibilityRunnerClient):
        def __init__(self) -> None:
            super().__init__(
                base_url="http://127.0.0.1:1032",
                runner_id="runner-client-upload-protocol",
                name="Client Upload Protocol",
                capabilities=["local_browser", "playwright", "chrome"],
                devices=[],
                max_parallel=1,
                poll_interval=1,
                executor="dry-run",
            )
            self.uploads: list[dict] = []
            self.reports: list[dict] = []
            self.heartbeats: list[str] = []

        def heartbeat(self, status: str = "online") -> None:
            self.heartbeats.append(status)

        def poll(self) -> list[dict[str, Any]]:
            return [
                {
                    "task_id": "compat_task_client_upload",
                    "environment_id": "env_client_upload",
                    "case_ids": ["case_launch"],
                    "mode_calls": [{"tool_key": "smoke-suite-runner"}],
                    "metadata": {
                        "environment": {"name": "Chrome"},
                        "product": {"name": "Client Upload Web"},
                    },
                }
            ]

        def upload_artifact(self, task_id: str, **kwargs):
            upload = {"task_id": task_id, **kwargs}
            self.uploads.append(upload)
            return {"artifact_id": "artifact_client_upload_log"}

        def report(self, task_id: str, payload: dict[str, Any]) -> None:
            self.reports.append({"task_id": task_id, "payload": payload})

    client = _CapturingRunnerClient()

    processed = client.run_once()

    assert processed == 1
    assert client.active_task_ids == []
    assert client.heartbeats == ["online", "busy", "online"]
    assert len(client.uploads) == 1
    upload = client.uploads[0]
    assert upload["task_id"] == "compat_task_client_upload"
    assert upload["artifact_type"] == "runner_log"
    assert upload["metadata"]["preview"] == "Dry-run compatibility task accepted by runner."
    log_payload = json.loads(upload["content"].decode("utf-8"))
    assert log_payload["task_id"] == "compat_task_client_upload"
    assert log_payload["status"] == "completed"
    assert log_payload["planned_mode_calls"] == ["smoke-suite-runner"]

    assert len(client.reports) == 1
    report = client.reports[0]
    assert report["task_id"] == "compat_task_client_upload"
    payload = report["payload"]
    assert payload["status"] == "completed"
    assert payload["artifacts"] == []
    assert payload["metadata"]["uploaded_artifact_ids"] == ["artifact_client_upload_log"]
    assert payload["metadata"]["executor"] == "dry_run"


def test_compatibility_api_routes_cover_runner_lifecycle():
    root = Path(__file__).resolve().parent / ".tmp" / f"compat_api_{uuid4().hex}"
    try:
        service = CompatibilityRunnerService(settings=_Settings(root))
        fake_tool_runtime = _FakeToolRuntimeService()
        app = FastAPI()
        app.state.compatibility_runner_service = service
        app.state.tool_registry = _FakeToolRegistry()
        app.state.tool_runtime_service = fake_tool_runtime
        app.include_router(compatibility_router, prefix="/api/v1")
        client = TestClient(app)

        runner_payload = {
            "runner_id": "runner-api-1",
            "name": "API Route Runner",
            "os": "Windows 11",
            "capabilities": ["local_browser", "playwright", "chrome"],
            "max_parallel": 1,
        }
        response = client.post("/api/v1/compatibility/runners/register", json=runner_payload)
        assert response.status_code == 200
        assert response.json()["runner_id"] == "runner-api-1"

        dispatch_plan = CompatibilityDispatchPlan(
            plan_id="plan_api",
            tasks=[
                CompatibilityRunnerTask(
                    environment_id="env_api_chrome",
                    case_ids=["case_launch"],
                    runner_selector=RunnerSelector(
                        provider="local_browser",
                        capabilities=["local_browser", "playwright", "chrome"],
                        os="Windows",
                        browser="chrome",
                    ),
                    mode_calls=[
                        CompatibilityModeCall(
                            tool_key="smoke-suite-runner",
                            reason="baseline",
                            arguments={"target_url": "https://example.test"},
                        )
                    ],
                )
            ],
        )
        queued = asyncio.run(service.enqueue_dispatch_plan(dispatch_plan))
        task_id = queued[0].task_id

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/report",
            json={"status": "completed", "result": {"summary": "Should not write before assignment."}},
        )
        assert response.status_code == 409
        assert "not assigned" in response.json()["detail"]

        response = client.post(
            f"/api/v1/compatibility/runners/missing-runner/tasks/{task_id}/report",
            json={"status": "completed", "result": {"summary": "Missing runner."}},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Runner not found"

        unassigned_upload = {
            "filename": "unassigned-log.txt",
            "content_base64": base64.b64encode(b"unassigned").decode("ascii"),
            "type": "runner_log",
            "label": "Unassigned log",
            "mime_type": "text/plain",
        }
        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/artifacts/upload",
            json=unassigned_upload,
        )
        assert response.status_code == 409
        assert "not assigned" in response.json()["detail"]

        invalid_upload = {
            "filename": "invalid-log.txt",
            "content_base64": "not-valid-base64",
            "type": "runner_log",
            "label": "Invalid log",
            "mime_type": "text/plain",
        }
        response = client.post(
            f"/api/v1/compatibility/runners/missing-runner/tasks/{task_id}/artifacts/upload",
            json=invalid_upload,
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "Runner not found"
        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/artifacts/upload",
            json=invalid_upload,
        )
        assert response.status_code == 409
        assert "not assigned" in response.json()["detail"]

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/mode-calls/execute",
        )
        assert response.status_code == 409
        assert "not assigned" in response.json()["detail"]

        response = client.post("/api/v1/compatibility/runners/runner-api-1/tasks/poll")
        assert response.status_code == 200
        assert response.json()["tasks"][0]["task_id"] == task_id

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/mode-calls/execute",
        )
        assert response.status_code == 200
        mode_call_response = response.json()
        assert mode_call_response["status"] == "completed"
        assert mode_call_response["mode_call_count"] == 1
        assert mode_call_response["mode_call_results"][0]["tool_key"] == "smoke-suite-runner"
        assert mode_call_response["mode_call_results"][0]["output"]["runner_id"] == "runner-api-1"
        assert fake_tool_runtime.calls[0]["task_id"] == task_id
        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/mode-calls/execute",
        )
        assert response.status_code == 409
        assert "already been executed" in response.json()["detail"]
        tasks_after_mode_calls = client.get(f"/api/v1/compatibility/tasks?dispatch_id={dispatch_plan.dispatch_id}")
        assert tasks_after_mode_calls.status_code == 200
        mode_call_metadata = tasks_after_mode_calls.json()[0]["metadata"]
        assert mode_call_metadata["mode_calls_execution_status"] == "completed"
        assert mode_call_metadata["mode_calls_execution_result_count"] == 1

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/report",
            json={"status": "queued", "result": {"summary": "Invalid status regression."}},
        )
        assert response.status_code == 400
        assert "completed, failed, or cancelled" in response.json()["detail"]

        upload = {
            "filename": "route-log.txt",
            "content_base64": base64.b64encode(b"route log").decode("ascii"),
            "type": "runner_log",
            "label": "Route log",
            "mime_type": "text/plain",
        }
        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/artifacts/upload",
            json=upload,
        )
        assert response.status_code == 200
        artifact_id = response.json()["artifact_id"]

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/artifacts/upload",
            json=invalid_upload,
        )
        assert response.status_code == 400

        response = client.get(f"/api/v1/compatibility/artifacts/{artifact_id}/content")
        assert response.status_code == 200
        assert response.content == b"route log"

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/report",
            json={"status": "failed", "result": {"summary": "Route lifecycle failed."}, "error": "route_failure"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "failed"
        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-1/tasks/{task_id}/report",
            json={"status": "failed", "result": {"summary": "Duplicate report should not overwrite."}},
        )
        assert response.status_code == 409
        assert "cannot be reported again" in response.json()["detail"]

        response = client.get(f"/api/v1/compatibility/report?dispatch_id={dispatch_plan.dispatch_id}")
        assert response.status_code == 200
        report = response.json()
        assert report["failed_tasks"] == 1
        assert report["artifacts"][0]["artifact_id"] == artifact_id

        response = client.post(
            "/api/v1/compatibility/tasks/requeue",
            json={"task_ids": [task_id], "statuses": ["failed"], "reason": "route_retry"},
        )
        assert response.status_code == 200
        assert response.json()["task_ids"] == [task_id]

        response = client.post(
            "/api/v1/compatibility/tasks/requeue",
            json={"task_ids": [task_id], "statuses": ["failed"], "reason": "route_retry_stale_status"},
        )
        assert response.status_code == 200
        assert response.json()["requeued_count"] == 0
        assert response.json()["skipped_task_ids"] == [task_id]
        assert response.json()["skipped_reasons"][task_id] == "status_queued_not_requeueable"

        response = client.post(
            "/api/v1/compatibility/plans/draft",
            json={
                "product_name": "Route Web",
                "product_type": "web",
                "target_url": "https://example.test",
                "auth_strategy": "none",
                "priority_flows": ["搜索"],
            },
        )
        assert response.status_code == 200
        drafted = response.json()
        assert drafted["ok"] is True
        assert drafted["plan"]["product"]["name"] == "Route Web"
        route_available_environment_ids = [
            environment["environment_id"]
            for environment in drafted["plan"]["environments"]
            if environment["availability"] == "available"
        ]
        route_unavailable_environment_ids = [
            environment["environment_id"]
            for environment in drafted["plan"]["environments"]
            if environment["availability"] != "available"
        ]
        assert route_available_environment_ids
        assert route_unavailable_environment_ids

        response = client.post(
            "/api/v1/compatibility/plans/dispatch",
            json={
                "plan": drafted["plan"],
                "selected_case_ids": [drafted["plan"]["cases"][0]["case_id"]],
                "selected_environment_ids": route_available_environment_ids[:1],
            },
        )
        assert response.status_code == 200
        dispatched = response.json()
        assert dispatched["ok"] is True
        assert dispatched["runner_queue"]["queued_task_count"] == 1
        assert "artifact upload pipeline" not in dispatched["missing_components"]
        assert "mode invoker execution bridge" not in dispatched["missing_components"]

        response = client.post(
            "/api/v1/compatibility/plans/dispatch",
            json={
                "plan": drafted["plan"],
                "selected_case_ids": [drafted["plan"]["cases"][0]["case_id"]],
                "selected_environment_ids": route_unavailable_environment_ids[:1],
            },
        )
        assert response.status_code == 400
        assert "没有可执行 Runner/Provider 环境" in response.json()["detail"]

        response = client.post(
            "/api/v1/compatibility/plans/dispatch",
            json={
                "plan": drafted["plan"],
                "selected_case_ids": ["missing-case"],
                "selected_environment_ids": route_available_environment_ids[:1],
            },
        )
        assert response.status_code == 400
        assert "未知用例 ID" in response.json()["detail"]

        response = client.post(
            "/api/v1/compatibility/plans/dispatch",
            json={
                "plan": drafted["plan"],
                "selected_case_ids": [drafted["plan"]["cases"][0]["case_id"]],
                "selected_environment_ids": ["missing-environment"],
            },
        )
        assert response.status_code == 400
        assert "未知环境 ID" in response.json()["detail"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_artifact_content_response_sanitizes_object_storage_filename_header():
    root = Path(__file__).resolve().parent / ".tmp" / f"compat_api_artifact_header_{uuid4().hex}"
    try:
        storage = _ReadableArtifactStorage()
        service = CompatibilityRunnerService(settings=_Settings(root), artifact_storage_service=storage)
        app = FastAPI()
        app.state.compatibility_runner_service = service
        app.include_router(compatibility_router, prefix="/api/v1")
        client = TestClient(app)

        response = client.post(
            "/api/v1/compatibility/runners/register",
            json={
                "runner_id": "runner-api-header",
                "name": "API Header Runner",
                "os": "Windows 11",
                "capabilities": ["local_browser", "playwright", "chrome"],
                "max_parallel": 1,
            },
        )
        assert response.status_code == 200

        dispatch_plan = CompatibilityDispatchPlan(
            plan_id="plan_header",
            tasks=[
                CompatibilityRunnerTask(
                    environment_id="env_header_chrome",
                    case_ids=["case_launch"],
                    runner_selector=RunnerSelector(
                        provider="local_browser",
                        capabilities=["local_browser", "playwright", "chrome"],
                        os="Windows",
                        browser="chrome",
                    ),
                    mode_calls=[],
                )
            ],
        )
        queued = asyncio.run(service.enqueue_dispatch_plan(dispatch_plan))
        task_id = queued[0].task_id
        response = client.post("/api/v1/compatibility/runners/runner-api-header/tasks/poll")
        assert response.status_code == 200

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-header/tasks/{task_id}/artifacts/upload",
            json={
                "filename": "route-log\r\nbad\"名.txt",
                "content_base64": base64.b64encode(b"object route log").decode("ascii"),
                "type": "runner_log",
                "label": "Object Route Log",
                "mime_type": "text/plain",
            },
        )
        assert response.status_code == 200
        artifact_id = response.json()["artifact_id"]

        response = client.get(f"/api/v1/compatibility/artifacts/{artifact_id}/content")
        assert response.status_code == 200
        assert response.content == b"object route log"
        disposition = response.headers["content-disposition"]
        assert "\r" not in disposition
        assert "\n" not in disposition
        assert "filename*=UTF-8''" in disposition
        assert "route-log%0D%0Abad%22%E5%90%8D.txt" in disposition
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mode_call_execution_exception_marks_bridge_failed_and_requeue_clears_lock():
    root = Path(__file__).resolve().parent / ".tmp" / f"compat_api_mode_exception_{uuid4().hex}"
    try:
        service = CompatibilityRunnerService(settings=_Settings(root))
        app = FastAPI()
        app.state.compatibility_runner_service = service
        app.state.tool_registry = _FakeToolRegistry()
        app.state.tool_runtime_service = _FailingToolRuntimeService()
        app.include_router(compatibility_router, prefix="/api/v1")
        client = TestClient(app)

        response = client.post(
            "/api/v1/compatibility/runners/register",
            json={
                "runner_id": "runner-api-mode-exception",
                "name": "API Mode Exception Runner",
                "os": "Windows 11",
                "capabilities": ["local_browser", "playwright", "chrome"],
                "max_parallel": 1,
            },
        )
        assert response.status_code == 200

        dispatch_plan = CompatibilityDispatchPlan(
            plan_id="plan_api_mode_exception",
            tasks=[
                CompatibilityRunnerTask(
                    environment_id="env_api_exception_chrome",
                    case_ids=["case_launch"],
                    runner_selector=RunnerSelector(
                        provider="local_browser",
                        capabilities=["local_browser", "playwright", "chrome"],
                        os="Windows",
                        browser="chrome",
                    ),
                    mode_calls=[
                        CompatibilityModeCall(
                            tool_key="smoke-suite-runner",
                            reason="baseline",
                            arguments={"target_url": "https://example.test"},
                        )
                    ],
                )
            ],
        )
        queued = asyncio.run(service.enqueue_dispatch_plan(dispatch_plan))
        task_id = queued[0].task_id

        response = client.post("/api/v1/compatibility/runners/runner-api-mode-exception/tasks/poll")
        assert response.status_code == 200
        assert response.json()["tasks"][0]["task_id"] == task_id

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-mode-exception/tasks/{task_id}/mode-calls/execute",
        )
        assert response.status_code == 500
        assert "mode runtime exploded" in response.json()["detail"]

        tasks_after_failure = client.get(f"/api/v1/compatibility/tasks?dispatch_id={dispatch_plan.dispatch_id}")
        assert tasks_after_failure.status_code == 200
        metadata_after_failure = tasks_after_failure.json()[0]["metadata"]
        assert metadata_after_failure["mode_calls_execution_status"] == "failed"
        assert metadata_after_failure["mode_calls_execution_result_count"] == 0
        assert metadata_after_failure["mode_calls_execution_runner_id"] == "runner-api-mode-exception"
        response = client.get(f"/api/v1/compatibility/report?dispatch_id={dispatch_plan.dispatch_id}")
        assert response.status_code == 200
        report_after_failure = response.json()
        assert report_after_failure["recoverable_tasks"][0]["task_id"] == task_id
        assert report_after_failure["recoverable_tasks"][0]["status"] == "assigned"
        assert "Mode call bridge ended as failed" in report_after_failure["recoverable_tasks"][0]["summary"]
        assert f"[assigned] env_api_exception_chrome / {task_id}" in report_after_failure["markdown"]

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-mode-exception/tasks/{task_id}/mode-calls/execute",
        )
        assert response.status_code == 409
        assert "already been executed" in response.json()["detail"]

        response = client.post(
            "/api/v1/compatibility/tasks/requeue",
            json={"task_ids": [task_id], "statuses": ["assigned"], "reason": "retry_mode_exception"},
        )
        assert response.status_code == 200
        assert response.json()["task_ids"] == [task_id]
        tasks_after_requeue = client.get(f"/api/v1/compatibility/tasks?dispatch_id={dispatch_plan.dispatch_id}")
        assert tasks_after_requeue.status_code == 200
        metadata_after_requeue = tasks_after_requeue.json()[0]["metadata"]
        assert metadata_after_requeue["mode_calls_execution_status"] is None
        assert metadata_after_requeue["mode_calls_execution_result_count"] is None
        assert metadata_after_requeue["mode_calls_execution_runner_id"] is None

        app.state.tool_runtime_service = _FakeToolRuntimeService()
        response = client.post("/api/v1/compatibility/runners/runner-api-mode-exception/tasks/poll")
        assert response.status_code == 200
        assert response.json()["tasks"][0]["task_id"] == task_id
        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-mode-exception/tasks/{task_id}/mode-calls/execute",
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mode_call_execution_partial_response_is_not_ok():
    root = Path(__file__).resolve().parent / ".tmp" / f"compat_api_mode_partial_{uuid4().hex}"
    try:
        service = CompatibilityRunnerService(settings=_Settings(root))
        app = FastAPI()
        app.state.compatibility_runner_service = service
        app.state.tool_registry = _FakeToolRegistry()
        app.state.tool_runtime_service = _PartialToolRuntimeService()
        app.include_router(compatibility_router, prefix="/api/v1")
        client = TestClient(app)

        response = client.post(
            "/api/v1/compatibility/runners/register",
            json={
                "runner_id": "runner-api-mode-partial",
                "name": "API Mode Partial Runner",
                "os": "Windows 11",
                "capabilities": ["local_browser", "playwright", "chrome"],
                "max_parallel": 1,
            },
        )
        assert response.status_code == 200

        dispatch_plan = CompatibilityDispatchPlan(
            plan_id="plan_api_mode_partial",
            tasks=[
                CompatibilityRunnerTask(
                    environment_id="env_api_partial_chrome",
                    case_ids=["case_launch"],
                    runner_selector=RunnerSelector(
                        provider="local_browser",
                        capabilities=["local_browser", "playwright", "chrome"],
                        os="Windows",
                        browser="chrome",
                    ),
                    mode_calls=[
                        CompatibilityModeCall(
                            tool_key="smoke-suite-runner",
                            reason="approval follow-up",
                            arguments={"target_url": "https://example.test"},
                        )
                    ],
                )
            ],
        )
        queued = asyncio.run(service.enqueue_dispatch_plan(dispatch_plan))
        task_id = queued[0].task_id

        response = client.post("/api/v1/compatibility/runners/runner-api-mode-partial/tasks/poll")
        assert response.status_code == 200
        assert response.json()["tasks"][0]["task_id"] == task_id

        response = client.post(
            f"/api/v1/compatibility/runners/runner-api-mode-partial/tasks/{task_id}/mode-calls/execute",
        )
        assert response.status_code == 200
        bridge = response.json()
        assert bridge["status"] == "partial"
        assert bridge["ok"] is False
        assert bridge["summary"] == "Executed 1 compatibility mode call(s): 0 completed, 1 partial, 0 failed."
        assert bridge["mode_call_results"][0]["status"] == "partial"

        tasks_after_partial = client.get(f"/api/v1/compatibility/tasks?dispatch_id={dispatch_plan.dispatch_id}")
        assert tasks_after_partial.status_code == 200
        metadata_after_partial = tasks_after_partial.json()[0]["metadata"]
        assert metadata_after_partial["mode_calls_execution_status"] == "partial"
        assert metadata_after_partial["mode_calls_execution_result_count"] == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def _exercise_runner_service(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))

    runner = await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-web-1",
            name="Web Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    assert runner.status == "online"

    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_web",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_chrome",
                case_ids=["case_login"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[
                    CompatibilityModeCall(
                        tool_key="smoke-suite-runner",
                        reason="baseline",
                        arguments={"target_url": "https://example.test"},
                    )
                ],
            )
        ],
    )

    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    assert len(queued) == 1
    task_id = queued[0].task_id
    replayed = await service.enqueue_dispatch_plan(
        CompatibilityDispatchPlan(
            dispatch_id=dispatch_plan.dispatch_id,
            plan_id=dispatch_plan.plan_id,
            tasks=[
                CompatibilityRunnerTask(
                    environment_id="env_chrome",
                    case_ids=["case_login"],
                    runner_selector=RunnerSelector(
                        provider="local_browser",
                        capabilities=["local_browser", "playwright", "chrome"],
                        os="Windows",
                        browser="chrome",
                    ),
                    mode_calls=[],
                )
            ],
        )
    )
    assert [task.task_id for task in replayed] == [task_id]
    assert len(await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)) == 1

    try:
        await service.report_task(
            "runner-web-1",
            task_id,
            CompatibilityRunnerTaskReportRequest(
                status="completed",
                result={"summary": "Should not write before assignment."},
            ),
        )
        raise AssertionError("Unassigned task report should fail.")
    except PermissionError as exc:
        assert "not assigned" in str(exc)

    try:
        await service.upload_task_artifact(
            "missing-runner",
            task_id,
            CompatibilityArtifactUploadRequest(
                filename="unauthorized-log.txt",
                content_base64=base64.b64encode(b"unauthorized").decode("ascii"),
                type="runner_log",
                label="Unauthorized log",
                mime_type="text/plain",
            ),
        )
        raise AssertionError("Unregistered runner artifact upload should fail.")
    except CompatibilityRunnerNotFound:
        pass

    polled = await service.poll_tasks("runner-web-1")
    assert [task.task_id for task in polled.tasks] == [task_id]
    assert polled.tasks[0].assigned_runner_id == "runner-web-1"

    heartbeat = await service.heartbeat(
        "runner-web-1",
        CompatibilityRunnerHeartbeatRequest(
            status="busy",
            active_task_ids=["client-bogus-task"],
            metadata={"heartbeat_source": "test"},
        ),
    )
    assert heartbeat.active_task_ids == [task_id]
    assert heartbeat.metadata["heartbeat_source"] == "test"

    try:
        await service.report_task(
            "runner-web-1",
            task_id,
            CompatibilityRunnerTaskReportRequest(
                status="queued",
                result={"summary": "Runner must report a terminal status."},
            ),
        )
        raise AssertionError("Non-terminal report status should fail.")
    except ValueError as exc:
        assert "completed, failed, or cancelled" in str(exc)

    artifact = await service.upload_task_artifact(
        "runner-web-1",
        task_id,
        CompatibilityArtifactUploadRequest(
            filename="runner-log.txt",
            content_base64=base64.b64encode(b"compatibility log").decode("ascii"),
            type="runner_log",
            label="Runner log",
            mime_type="text/plain",
        ),
    )
    assert artifact.uri.endswith(f"/compatibility/artifacts/{artifact.artifact_id}/content")
    content = await service.read_artifact_content(artifact.artifact_id)
    assert content["path"].read_text(encoding="utf-8") == "compatibility log"

    mode_call_running = await service.begin_mode_call_execution("runner-web-1", task_id)
    assert mode_call_running.metadata["mode_calls_execution_status"] == "running"
    mode_call_completed = await service.finish_mode_call_execution(
        "runner-web-1",
        task_id,
        status="completed",
        result_count=1,
    )
    assert mode_call_completed.metadata["mode_calls_execution_status"] == "completed"
    assert mode_call_completed.metadata["mode_calls_execution_result_count"] == 1

    completed = await service.report_task(
        "runner-web-1",
        task_id,
        CompatibilityRunnerTaskReportRequest(
            status="passed",
            result={
                "summary": "Chrome compatibility baseline passed.",
                "artifacts": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "uri": "https://example.test/runner-report-log.txt",
                        "type": "runner_log",
                        "label": "Runner report log",
                        "metadata": {
                            "local_path": str(content["path"]),
                            "storage_backend": "local",
                            "runner_note": "external reference",
                        },
                    }
                ],
                "mode_call_results": [
                    {
                        "tool_key": "smoke-suite-runner",
                        "status": "completed",
                        "summary": "Smoke baseline passed.",
                    },
                    {
                        "tool_key": "ui-automation-runner",
                        "status": "partial",
                        "summary": "UI exploration requires follow-up assertions.",
                    },
                ],
            },
            metadata={
                "executor": "dry_run",
                "uploaded_artifact_ids": [artifact.artifact_id],
                "retry_count": 999,
                "retry_history": [{"reason": "runner-forged"}],
                "mode_calls_execution_status": "failed",
                "mode_calls_execution_completed_at": "runner-forged-completed-at",
                "mode_calls_execution_result_count": 999,
                "mode_calls_execution_runner_id": "runner-forged",
            },
        ),
    )
    assert completed.status == "completed"
    assert completed.artifacts[0].artifact_id == artifact.artifact_id
    assert len(completed.artifacts) == 2
    assert completed.artifacts[1].artifact_id != artifact.artifact_id
    assert completed.artifacts[1].uri == "https://example.test/runner-report-log.txt"
    assert "local_path" not in completed.artifacts[1].metadata
    assert "storage_backend" not in completed.artifacts[1].metadata
    assert completed.artifacts[1].metadata["runner_note"] == "external reference"
    assert completed.metadata["executor"] == "dry_run"
    assert completed.metadata["uploaded_artifact_ids"] == [artifact.artifact_id]
    assert completed.metadata["mode_calls_execution_status"] == "completed"
    assert completed.metadata["mode_calls_execution_result_count"] == 1
    assert completed.metadata["mode_calls_execution_runner_id"] == "runner-web-1"
    assert completed.metadata["mode_calls_execution_completed_at"] != "runner-forged-completed-at"
    assert completed.metadata.get("retry_count") != 999
    assert "retry_history" not in completed.metadata
    retained_content = await service.read_artifact_content(artifact.artifact_id)
    assert retained_content["path"].read_text(encoding="utf-8") == "compatibility log"
    try:
        await service.report_task(
            "runner-web-1",
            task_id,
            CompatibilityRunnerTaskReportRequest(
                status="failed",
                result={"summary": "Duplicate report should not overwrite."},
            ),
        )
        raise AssertionError("Duplicate terminal report should fail.")
    except PermissionError as exc:
        assert "cannot be reported again" in str(exc)

    summary = await service.summarize_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert summary.total == 1
    assert summary.completed == 1
    assert summary.artifact_count == 2

    report = await service.build_report(dispatch_id=dispatch_plan.dispatch_id)
    assert report.status == "completed"
    assert report.pass_rate == 100
    assert "Chrome compatibility baseline passed." not in report.markdown
    assert "## 模式调用结果" in report.markdown
    assert "smoke-suite-runner=completed" in report.markdown
    assert "ui-automation-runner=partial" in report.markdown
    assert artifact.artifact_id in report.markdown

    requeued = await service.requeue_tasks(
        CompatibilityTaskRequeueRequest(task_ids=[task_id], statuses=["completed"], reason="verify_retry")
    )
    assert requeued.task_ids == [task_id]
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].status == "queued"
    assert tasks[0].result == {}
    assert tasks[0].artifacts == []
    assert tasks[0].metadata["retry_history"][-1]["previous_artifact_ids"] == [
        item.artifact_id for item in completed.artifacts
    ]
    retry_summary = await service.summarize_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert retry_summary.artifact_count == 0
    retry_report = await service.build_report(dispatch_id=dispatch_plan.dispatch_id)
    assert retry_report.artifact_count == 0
    assert retry_report.artifacts == []
    historical_artifacts = await service.list_artifacts(dispatch_id=dispatch_plan.dispatch_id)
    assert {item.artifact_id for item in historical_artifacts} == {item.artifact_id for item in completed.artifacts}


async def _exercise_offline_runner_poll_guard(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-offline-1",
            name="Offline Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    await service.heartbeat(
        "runner-offline-1",
        CompatibilityRunnerHeartbeatRequest(status="offline", metadata={"reason": "maintenance"}),
    )
    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_offline_guard",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_offline_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    poll_while_offline = await service.poll_tasks("runner-offline-1")
    assert poll_while_offline.tasks == []
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].task_id == queued[0].task_id
    assert tasks[0].status == "queued"
    assert tasks[0].assigned_runner_id is None

    await service.heartbeat("runner-offline-1", CompatibilityRunnerHeartbeatRequest(status="online"))
    poll_online = await service.poll_tasks("runner-offline-1")
    assert [task.task_id for task in poll_online.tasks] == [queued[0].task_id]


async def _exercise_offline_runner_cleanup(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    for runner_id in ("runner-old-offline", "runner-fresh-offline", "runner-online", "runner-assigned-offline"):
        await service.register_runner(
            CompatibilityRunnerRegistrationRequest(
                runner_id=runner_id,
                name=runner_id,
                os="Windows 11",
                capabilities=["local_browser", "playwright", "chrome"],
                max_parallel=1,
            )
        )
    old_heartbeat = "2020-01-01T00:00:00Z"
    async with service._lock:
        service._runners["runner-old-offline"] = service._runners["runner-old-offline"].model_copy(
            update={"status": "offline", "heartbeat_at": old_heartbeat}
        )
        service._runners["runner-fresh-offline"] = service._runners["runner-fresh-offline"].model_copy(
            update={"status": "offline"}
        )
        service._runners["runner-assigned-offline"] = service._runners["runner-assigned-offline"].model_copy(
            update={"status": "offline"}
        )
        service._save_state()

    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_cleanup",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_cleanup_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    async with service._lock:
        service._tasks[queued[0].task_id] = service._tasks[queued[0].task_id].model_copy(
            update={"status": "assigned", "assigned_runner_id": "runner-assigned-offline"}
        )
        service._save_state()

    cleanup = await service.cleanup_offline_runners(CompatibilityRunnerCleanupRequest(older_than_seconds=3600))

    assert cleanup.runner_ids == ["runner-old-offline"]
    assert cleanup.deleted_count == 1
    assert cleanup.skipped_reasons["runner-fresh-offline"] == "runner_not_old_enough"
    assert cleanup.skipped_reasons["runner-online"] == "runner_status_online_not_cleanupable"
    assert cleanup.skipped_reasons["runner-assigned-offline"] == "runner_has_assigned_tasks"
    remaining = {runner.runner_id for runner in await service.list_runners()}
    assert "runner-old-offline" not in remaining
    assert {"runner-fresh-offline", "runner-online", "runner-assigned-offline"}.issubset(remaining)
    persisted = json.loads((Path(root / "data") / "compatibility" / "runner_state.json").read_text(encoding="utf-8"))
    assert "runner-old-offline" not in {item["runner_id"] for item in persisted["runners"]}

    missing = await service.cleanup_offline_runners(
        CompatibilityRunnerCleanupRequest(runner_ids=["runner-missing"], older_than_seconds=0)
    )
    assert missing.deleted_count == 0
    assert missing.skipped_reasons["runner-missing"] == "runner_not_found"


async def _exercise_runner_os_match_guard(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-unknown-os",
            name="Unknown OS Runner",
            os="",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    selector = RunnerSelector(
        provider="local_browser",
        capabilities=["local_browser", "playwright", "chrome"],
        os="Windows",
        browser="chrome",
    )
    assert await service.find_matching_runners(selector.model_dump()) == []

    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_os_guard",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_windows_chrome",
                case_ids=["case_launch"],
                runner_selector=selector,
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    unknown_os_poll = await service.poll_tasks("runner-unknown-os")
    assert unknown_os_poll.tasks == []
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].task_id == queued[0].task_id
    assert tasks[0].status == "queued"
    assert tasks[0].assigned_runner_id is None

    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-windows-os",
            name="Windows Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    matching = await service.find_matching_runners(selector.model_dump())
    assert [runner.runner_id for runner in matching] == ["runner-windows-os"]
    windows_poll = await service.poll_tasks("runner-windows-os")
    assert [task.task_id for task in windows_poll.tasks] == [queued[0].task_id]


async def _exercise_runner_device_match_guard(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-android-no-device",
            name="Android Runner Without Device",
            os="Android 14",
            capabilities=["android_browser", "playwright_or_appium", "chrome"],
            devices=[],
            max_parallel=1,
        )
    )
    selector = RunnerSelector(
        provider="android_browser",
        capabilities=["android_browser", "playwright_or_appium", "chrome"],
        os="Android",
        browser="chrome",
        device="Pixel 7",
    )
    assert await service.find_matching_runners(selector.model_dump()) == []

    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_device_guard",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_android_pixel",
                case_ids=["case_mobile_launch"],
                runner_selector=selector,
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    no_device_poll = await service.poll_tasks("runner-android-no-device")
    assert no_device_poll.tasks == []
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].task_id == queued[0].task_id
    assert tasks[0].status == "queued"
    assert tasks[0].assigned_runner_id is None

    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-android-pixel",
            name="Android Pixel Runner",
            os="Android 14",
            capabilities=["android_browser", "playwright_or_appium", "chrome"],
            devices=["Pixel 7"],
            max_parallel=1,
        )
    )
    matching = await service.find_matching_runners(selector.model_dump())
    assert [runner.runner_id for runner in matching] == ["runner-android-pixel"]
    pixel_poll = await service.poll_tasks("runner-android-pixel")
    assert [task.task_id for task in pixel_poll.tasks] == [queued[0].task_id]


async def _exercise_runner_os_version_match_guard(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-android-13",
            name="Android 13 Pixel Runner",
            os="Android 13",
            capabilities=["android_browser", "playwright_or_appium", "chrome"],
            devices=["Pixel 7"],
            max_parallel=1,
        )
    )
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-android-114",
            name="Android 114 Pixel Runner",
            os="Android 114",
            capabilities=["android_browser", "playwright_or_appium", "chrome"],
            devices=["Pixel 7"],
            max_parallel=1,
        )
    )
    selector = RunnerSelector(
        provider="android_browser",
        capabilities=["android_browser", "playwright_or_appium", "chrome"],
        os="Android",
        os_version="14",
        browser="chrome",
        device="Pixel 7",
    )
    assert await service.find_matching_runners(selector.model_dump()) == []

    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_os_version_guard",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_android_14_pixel",
                case_ids=["case_mobile_launch"],
                runner_selector=selector,
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    android_13_poll = await service.poll_tasks("runner-android-13")
    assert android_13_poll.tasks == []
    android_114_poll = await service.poll_tasks("runner-android-114")
    assert android_114_poll.tasks == []
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].task_id == queued[0].task_id
    assert tasks[0].status == "queued"
    assert tasks[0].assigned_runner_id is None

    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-android-14",
            name="Android 14 Pixel Runner",
            os="Android",
            capabilities=["android_browser", "playwright_or_appium", "chrome"],
            devices=["Pixel 7"],
            max_parallel=1,
            metadata={"os_version": "14"},
        )
    )
    matching = await service.find_matching_runners(selector.model_dump())
    assert [runner.runner_id for runner in matching] == ["runner-android-14"]
    android_14_poll = await service.poll_tasks("runner-android-14")
    assert [task.task_id for task in android_14_poll.tasks] == [queued[0].task_id]


async def _exercise_runner_browser_version_match_guard(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-chrome-119",
            name="Chrome 119 Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
            metadata={"browser_versions": {"chrome": "119.0"}},
        )
    )
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-chrome-1120",
            name="Chrome 1120 Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
            metadata={"browser_versions": {"chrome": "1120.0"}},
        )
    )
    selector = RunnerSelector(
        provider="local_browser",
        capabilities=["local_browser", "playwright", "chrome"],
        os="Windows",
        browser="chrome",
        browser_version="120",
    )
    assert selector.model_dump()["browser_version"] == "120"
    assert await service.find_matching_runners(selector.model_dump()) == []

    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_browser_version_guard",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_windows_chrome_120",
                case_ids=["case_launch"],
                runner_selector=selector,
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    chrome_119_poll = await service.poll_tasks("runner-chrome-119")
    assert chrome_119_poll.tasks == []
    chrome_1120_poll = await service.poll_tasks("runner-chrome-1120")
    assert chrome_1120_poll.tasks == []
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].task_id == queued[0].task_id
    assert tasks[0].status == "queued"
    assert tasks[0].assigned_runner_id is None

    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-chrome-120",
            name="Chrome 120 Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
            metadata={"browser_versions": {"chrome": ["119.0", "120.0"]}},
        )
    )
    matching = await service.find_matching_runners(selector.model_dump())
    assert [runner.runner_id for runner in matching] == ["runner-chrome-120"]
    chrome_120_poll = await service.poll_tasks("runner-chrome-120")
    assert [task.task_id for task in chrome_120_poll.tasks] == [queued[0].task_id]


async def _exercise_runner_browser_match_guard(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-edge-only",
            name="Edge Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "edge"],
            max_parallel=1,
        )
    )
    selector = RunnerSelector(
        provider="local_browser",
        capabilities=[],
        os="Windows",
        browser="chrome",
    )
    assert await service.find_matching_runners(selector.model_dump()) == []

    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_browser_guard",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_windows_chrome_browser",
                case_ids=["case_launch"],
                runner_selector=selector,
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    edge_poll = await service.poll_tasks("runner-edge-only")
    assert edge_poll.tasks == []
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].task_id == queued[0].task_id
    assert tasks[0].status == "queued"
    assert tasks[0].assigned_runner_id is None

    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-chrome-browser",
            name="Chrome Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    matching = await service.find_matching_runners(selector.model_dump())
    assert [runner.runner_id for runner in matching] == ["runner-chrome-browser"]
    chrome_poll = await service.poll_tasks("runner-chrome-browser")
    assert [task.task_id for task in chrome_poll.tasks] == [queued[0].task_id]


async def _exercise_runner_restart_recovery(root: Path):
    settings = _Settings(root)
    service = CompatibilityRunnerService(settings=settings)
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-restart-1",
            name="Restart Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_restart",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_restart_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[
                    CompatibilityModeCall(
                        tool_key="smoke-suite-runner",
                        reason="restart recovery bridge lock",
                        arguments={"target_url": "https://example.test"},
                    )
                ],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    task_id = queued[0].task_id
    first_poll = await service.poll_tasks("runner-restart-1")
    assert [task.task_id for task in first_poll.tasks] == [task_id]
    restart_artifact = await service.upload_task_artifact(
        "runner-restart-1",
        task_id,
        CompatibilityArtifactUploadRequest(
            filename="restart-before-report.txt",
            content_base64=base64.b64encode(b"restart artifact").decode("ascii"),
            type="runner_log",
            label="Restart artifact",
            mime_type="text/plain",
        ),
    )
    running = await service.begin_mode_call_execution("runner-restart-1", task_id)
    assert running.metadata["mode_calls_execution_status"] == "running"
    async with service._lock:
        task = service._tasks[task_id]
        service._tasks[task_id] = task.model_copy(
            update={
                "result": {"summary": "Partial result before restart."},
                "error": "partial_restart_error",
            }
        )
        service._save_state()

    restarted = CompatibilityRunnerService(settings=settings)
    restored_runners = await restarted.list_runners()
    assert restored_runners[0].active_task_ids == []
    restored_tasks = await restarted.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert restored_tasks[0].status == "queued"
    assert restored_tasks[0].assigned_runner_id is None
    assert restored_tasks[0].result == {}
    assert restored_tasks[0].error is None
    assert restored_tasks[0].artifacts == []
    assert restored_tasks[0].metadata["mode_calls_execution_status"] is None
    assert restored_tasks[0].metadata["mode_calls_execution_runner_id"] is None
    assert restored_tasks[0].metadata["retry_history"][-1]["reason"] == "runner_service_restart"
    assert restored_tasks[0].metadata["retry_history"][-1]["previous_artifact_ids"] == [
        restart_artifact.artifact_id
    ]
    persisted_state = json.loads((Path(settings.data_dir) / "compatibility" / "runner_state.json").read_text(encoding="utf-8"))
    persisted_task = next(item for item in persisted_state["tasks"] if item["task_id"] == task_id)
    persisted_runner = next(item for item in persisted_state["runners"] if item["runner_id"] == "runner-restart-1")
    assert persisted_task["status"] == "queued"
    assert persisted_task["assigned_runner_id"] is None
    assert persisted_task["result"] == {}
    assert persisted_task["error"] is None
    assert persisted_task["artifacts"] == []
    assert persisted_task["metadata"]["mode_calls_execution_status"] is None
    assert persisted_task["metadata"]["retry_history"][-1]["reason"] == "runner_service_restart"
    assert persisted_task["metadata"]["retry_history"][-1]["previous_summary"] == "Partial result before restart."
    assert persisted_task["metadata"]["retry_history"][-1]["previous_error"] == "partial_restart_error"
    assert persisted_runner["active_task_ids"] == []

    second_poll = await restarted.poll_tasks("runner-restart-1")
    assert [task.task_id for task in second_poll.tasks] == [task_id]
    assert second_poll.tasks[0].assigned_runner_id == "runner-restart-1"
    second_running = await restarted.begin_mode_call_execution("runner-restart-1", task_id)
    assert second_running.metadata["mode_calls_execution_status"] == "running"


async def _exercise_cancelled_runner_report(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-cancelled-1",
            name="Cancelled Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_cancelled",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_cancelled_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[
                    CompatibilityModeCall(
                        tool_key="smoke-suite-runner",
                        reason="stale recovery bridge lock",
                        arguments={"target_url": "https://example.test"},
                    )
                ],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    task_id = queued[0].task_id
    await service.poll_tasks("runner-cancelled-1")
    await service.report_task(
        "runner-cancelled-1",
        task_id,
        CompatibilityRunnerTaskReportRequest(
            status="cancelled",
            result={"summary": "Runner cancelled before execution."},
            error="runner_cancelled",
        ),
    )

    summary = await service.summarize_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert summary.cancelled == 1

    report = await service.build_report(dispatch_id=dispatch_plan.dispatch_id)
    assert report.status == "cancelled"
    assert report.cancelled_tasks == 1
    assert report.completed_tasks == 0
    assert report.pass_rate == 0
    assert report.environments[0].status == "cancelled"
    assert report.environments[0].cancelled == 1
    assert report.recoverable_tasks[0].task_id == task_id
    assert report.recoverable_tasks[0].status == "cancelled"
    assert "取消：1" in report.markdown
    assert "取消 1 / 待完成 0" in report.markdown
    assert "## 可重跑任务" in report.markdown
    assert f"[cancelled] env_cancelled_chrome / {task_id}" in report.markdown

    requeued = await service.requeue_tasks(
        CompatibilityTaskRequeueRequest(
            task_ids=[report.recoverable_tasks[0].task_id],
            statuses=["cancelled"],
            reason="retry_cancelled",
        )
    )
    assert requeued.task_ids == [task_id]
    tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert tasks[0].status == "queued"
    assert tasks[0].assigned_runner_id is None


async def _exercise_stale_runner_recovery(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    for runner_id in ("runner-stale-1", "runner-stale-2"):
        await service.register_runner(
            CompatibilityRunnerRegistrationRequest(
                runner_id=runner_id,
                name=runner_id,
                os="Windows 11",
                capabilities=["local_browser", "playwright", "chrome"],
                max_parallel=1,
            )
        )
    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_stale",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_stale_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    task_id = queued[0].task_id
    first_poll = await service.poll_tasks("runner-stale-1")
    assert [task.task_id for task in first_poll.tasks] == [task_id]
    stale_artifact = await service.upload_task_artifact(
        "runner-stale-1",
        task_id,
        CompatibilityArtifactUploadRequest(
            filename="stale-before-report.txt",
            content_base64=base64.b64encode(b"stale artifact").decode("ascii"),
            type="runner_log",
            label="Stale artifact",
            mime_type="text/plain",
        ),
    )
    running = await service.begin_mode_call_execution("runner-stale-1", task_id)
    assert running.metadata["mode_calls_execution_status"] == "running"

    async with service._lock:
        runner = service._runners["runner-stale-1"]
        service._runners["runner-stale-1"] = runner.model_copy(
            update={"heartbeat_at": "2000-01-01T00:00:00Z", "active_task_ids": [task_id]}
        )
        task = service._tasks[task_id]
        service._tasks[task_id] = task.model_copy(
            update={
                "result": {"summary": "Partial result before stale recovery."},
                "error": "partial_stale_error",
            }
        )

    stale_tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert stale_tasks[0].status == "queued"
    assert stale_tasks[0].assigned_runner_id is None
    assert stale_tasks[0].result == {}
    assert stale_tasks[0].error is None
    assert stale_tasks[0].artifacts == []
    assert stale_tasks[0].metadata["mode_calls_execution_status"] is None
    assert stale_tasks[0].metadata["mode_calls_execution_runner_id"] is None
    assert stale_tasks[0].metadata["retry_count"] == 1
    assert stale_tasks[0].metadata["retry_history"][-1]["reason"] == "runner_heartbeat_timeout"
    assert stale_tasks[0].metadata["retry_history"][-1]["previous_runner_id"] == "runner-stale-1"
    assert stale_tasks[0].metadata["retry_history"][-1]["previous_summary"] == "Partial result before stale recovery."
    assert stale_tasks[0].metadata["retry_history"][-1]["previous_error"] == "partial_stale_error"
    assert stale_tasks[0].metadata["retry_history"][-1]["previous_artifact_ids"] == [stale_artifact.artifact_id]

    runners = {runner.runner_id: runner for runner in await service.list_runners()}
    assert runners["runner-stale-1"].status == "offline"
    assert runners["runner-stale-1"].active_task_ids == []

    second_poll = await service.poll_tasks("runner-stale-2")
    assert [task.task_id for task in second_poll.tasks] == [task_id]
    assert second_poll.tasks[0].assigned_runner_id == "runner-stale-2"
    second_running = await service.begin_mode_call_execution("runner-stale-2", task_id)
    assert second_running.metadata["mode_calls_execution_status"] == "running"


async def _exercise_stale_runner_reregister_recovery(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-reregister-stale",
            name="Stale Reregister Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_reregister_stale",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_reregister_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[
                    CompatibilityModeCall(
                        tool_key="smoke-suite-runner",
                        reason="reregister stale recovery bridge lock",
                        arguments={"target_url": "https://example.test"},
                    )
                ],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    task_id = queued[0].task_id
    first_poll = await service.poll_tasks("runner-reregister-stale")
    assert [task.task_id for task in first_poll.tasks] == [task_id]
    stale_artifact = await service.upload_task_artifact(
        "runner-reregister-stale",
        task_id,
        CompatibilityArtifactUploadRequest(
            filename="reregister-stale-before-report.txt",
            content_base64=base64.b64encode(b"reregister stale artifact").decode("ascii"),
            type="runner_log",
            label="Reregister stale artifact",
            mime_type="text/plain",
        ),
    )
    stale_content = await service.read_artifact_content(stale_artifact.artifact_id)
    assert stale_content["path"].read_bytes() == b"reregister stale artifact"
    assert len(str(stale_content["path"])) < 260
    running = await service.begin_mode_call_execution("runner-reregister-stale", task_id)
    assert running.metadata["mode_calls_execution_status"] == "running"

    async with service._lock:
        runner = service._runners["runner-reregister-stale"]
        service._runners["runner-reregister-stale"] = runner.model_copy(
            update={"heartbeat_at": "2000-01-01T00:00:00Z", "active_task_ids": [task_id]}
        )
        task = service._tasks[task_id]
        service._tasks[task_id] = task.model_copy(
            update={
                "result": {"summary": "Partial result before stale reregister."},
                "error": "partial_reregister_stale_error",
            }
        )

    registered = await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-reregister-stale",
            name="Stale Reregister Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
            metadata={"reconnected": True},
        )
    )
    assert registered.status == "online"
    assert registered.active_task_ids == []
    assert registered.metadata["reconnected"] is True

    recovered_tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert recovered_tasks[0].status == "queued"
    assert recovered_tasks[0].assigned_runner_id is None
    assert recovered_tasks[0].result == {}
    assert recovered_tasks[0].error is None
    assert recovered_tasks[0].artifacts == []
    assert recovered_tasks[0].metadata["mode_calls_execution_status"] is None
    assert recovered_tasks[0].metadata["retry_count"] == 1
    assert recovered_tasks[0].metadata["retry_history"][-1]["reason"] == "runner_heartbeat_timeout"
    assert recovered_tasks[0].metadata["retry_history"][-1]["previous_artifact_ids"] == [
        stale_artifact.artifact_id
    ]
    persisted_state = json.loads((Path(root / "data") / "compatibility" / "runner_state.json").read_text(encoding="utf-8"))
    persisted_task = next(item for item in persisted_state["tasks"] if item["task_id"] == task_id)
    persisted_runner = next(item for item in persisted_state["runners"] if item["runner_id"] == "runner-reregister-stale")
    assert persisted_task["status"] == "queued"
    assert persisted_task["assigned_runner_id"] is None
    assert persisted_task["result"] == {}
    assert persisted_task["artifacts"] == []
    assert persisted_task["error"] is None
    assert persisted_task["metadata"]["retry_history"][-1]["reason"] == "runner_heartbeat_timeout"
    assert persisted_runner["active_task_ids"] == []
    assert persisted_runner["metadata"]["reconnected"] is True

    second_poll = await service.poll_tasks("runner-reregister-stale")
    assert [task.task_id for task in second_poll.tasks] == [task_id]
    assert second_poll.tasks[0].assigned_runner_id == "runner-reregister-stale"
    second_running = await service.begin_mode_call_execution("runner-reregister-stale", task_id)
    assert second_running.metadata["mode_calls_execution_status"] == "running"


async def _exercise_stale_runner_heartbeat_recovery(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-heartbeat-stale",
            name="Stale Heartbeat Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_heartbeat_stale",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_heartbeat_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[
                    CompatibilityModeCall(
                        tool_key="smoke-suite-runner",
                        reason="heartbeat stale recovery bridge lock",
                        arguments={"target_url": "https://example.test"},
                    )
                ],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    task_id = queued[0].task_id
    first_poll = await service.poll_tasks("runner-heartbeat-stale")
    assert [task.task_id for task in first_poll.tasks] == [task_id]
    stale_artifact = await service.upload_task_artifact(
        "runner-heartbeat-stale",
        task_id,
        CompatibilityArtifactUploadRequest(
            filename="heartbeat-stale-before-report.txt",
            content_base64=base64.b64encode(b"heartbeat stale artifact").decode("ascii"),
            type="runner_log",
            label="Heartbeat stale artifact",
            mime_type="text/plain",
        ),
    )
    running = await service.begin_mode_call_execution("runner-heartbeat-stale", task_id)
    assert running.metadata["mode_calls_execution_status"] == "running"

    async with service._lock:
        runner = service._runners["runner-heartbeat-stale"]
        service._runners["runner-heartbeat-stale"] = runner.model_copy(
            update={"heartbeat_at": "2000-01-01T00:00:00Z", "active_task_ids": [task_id]}
        )
        task = service._tasks[task_id]
        service._tasks[task_id] = task.model_copy(
            update={
                "result": {"summary": "Partial result before stale heartbeat."},
                "error": "partial_heartbeat_stale_error",
            }
        )

    heartbeat = await service.heartbeat(
        "runner-heartbeat-stale",
        CompatibilityRunnerHeartbeatRequest(
            status="online",
            active_task_ids=[task_id],
            metadata={"heartbeat_after_stale": True},
        ),
    )
    assert heartbeat.status == "online"
    assert heartbeat.active_task_ids == []
    assert heartbeat.metadata["heartbeat_after_stale"] is True

    recovered_tasks = await service.list_tasks(dispatch_id=dispatch_plan.dispatch_id)
    assert recovered_tasks[0].status == "queued"
    assert recovered_tasks[0].assigned_runner_id is None
    assert recovered_tasks[0].result == {}
    assert recovered_tasks[0].error is None
    assert recovered_tasks[0].artifacts == []
    assert recovered_tasks[0].metadata["mode_calls_execution_status"] is None
    assert recovered_tasks[0].metadata["retry_count"] == 1
    assert recovered_tasks[0].metadata["retry_history"][-1]["reason"] == "runner_heartbeat_timeout"
    assert recovered_tasks[0].metadata["retry_history"][-1]["previous_artifact_ids"] == [
        stale_artifact.artifact_id
    ]
    assert recovered_tasks[0].metadata["retry_history"][-1]["previous_summary"] == "Partial result before stale heartbeat."
    assert recovered_tasks[0].metadata["retry_history"][-1]["previous_error"] == "partial_heartbeat_stale_error"
    persisted_state = json.loads((Path(root / "data") / "compatibility" / "runner_state.json").read_text(encoding="utf-8"))
    persisted_task = next(item for item in persisted_state["tasks"] if item["task_id"] == task_id)
    persisted_runner = next(item for item in persisted_state["runners"] if item["runner_id"] == "runner-heartbeat-stale")
    assert persisted_task["status"] == "queued"
    assert persisted_task["assigned_runner_id"] is None
    assert persisted_task["result"] == {}
    assert persisted_task["artifacts"] == []
    assert persisted_task["error"] is None
    assert persisted_task["metadata"]["retry_history"][-1]["reason"] == "runner_heartbeat_timeout"
    assert persisted_runner["active_task_ids"] == []
    assert persisted_runner["metadata"]["heartbeat_after_stale"] is True

    second_poll = await service.poll_tasks("runner-heartbeat-stale")
    assert [task.task_id for task in second_poll.tasks] == [task_id]
    assert second_poll.tasks[0].assigned_runner_id == "runner-heartbeat-stale"
    second_running = await service.begin_mode_call_execution("runner-heartbeat-stale", task_id)
    assert second_running.metadata["mode_calls_execution_status"] == "running"


class _BlockingArtifactStorage:
    enabled = True

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.deleted_uris: list[str] = []

    async def store_uploaded_bytes(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return {
            "uri": "rustfs://qa-agent/compatibility/blocking-log.txt",
            "storage_backend": "rustfs",
            "bucket": "qa-agent",
            "object_name": "compatibility/blocking-log.txt",
        }

    async def delete_object_uri(self, uri: str) -> None:
        self.deleted_uris.append(uri)


async def _exercise_artifact_storage_lock_boundary(root: Path):
    storage = _BlockingArtifactStorage()
    service = CompatibilityRunnerService(settings=_Settings(root), artifact_storage_service=storage)
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-lock-1",
            name="Lock Boundary Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    dispatch_plan = CompatibilityDispatchPlan(
        plan_id="plan_lock",
        tasks=[
            CompatibilityRunnerTask(
                environment_id="env_lock_chrome",
                case_ids=["case_launch"],
                runner_selector=RunnerSelector(
                    provider="local_browser",
                    capabilities=["local_browser", "playwright", "chrome"],
                    os="Windows",
                    browser="chrome",
                ),
                mode_calls=[],
            )
        ],
    )
    queued = await service.enqueue_dispatch_plan(dispatch_plan)
    task_id = queued[0].task_id
    await service.poll_tasks("runner-lock-1")

    upload = asyncio.create_task(
        service.upload_task_artifact(
            "runner-lock-1",
            task_id,
            CompatibilityArtifactUploadRequest(
                filename="blocking-log.txt",
                content_base64=base64.b64encode(b"blocking upload").decode("ascii"),
                type="runner_log",
                label="Blocking upload",
                mime_type="text/plain",
            ),
        )
    )
    await asyncio.wait_for(storage.started.wait(), timeout=1)
    tasks = await asyncio.wait_for(service.list_tasks(dispatch_id=dispatch_plan.dispatch_id), timeout=1)
    assert tasks[0].task_id == task_id
    storage.release.set()
    artifact = await asyncio.wait_for(upload, timeout=1)
    assert artifact.metadata["storage_backend"] == "rustfs"

    storage.started = asyncio.Event()
    storage.release = asyncio.Event()
    cleanup_upload = asyncio.create_task(
        service.upload_task_artifact(
            "runner-lock-1",
            task_id,
            CompatibilityArtifactUploadRequest(
                filename="cleanup-log.txt",
                content_base64=base64.b64encode(b"cleanup upload").decode("ascii"),
                type="runner_log",
                label="Cleanup upload",
                mime_type="text/plain",
            ),
        )
    )
    await asyncio.wait_for(storage.started.wait(), timeout=1)
    await service.requeue_tasks(
        CompatibilityTaskRequeueRequest(task_ids=[task_id], statuses=["assigned"], reason="race_cleanup")
    )
    storage.release.set()
    try:
        await asyncio.wait_for(cleanup_upload, timeout=1)
        raise AssertionError("Upload should fail when task is requeued before artifact commit.")
    except PermissionError as exc:
        assert "not assigned" in str(exc)
    assert "rustfs://qa-agent/compatibility/blocking-log.txt" in storage.deleted_uris


async def _exercise_compatibility_runtime(root: Path):
    service = CompatibilityRunnerService(settings=_Settings(root))
    await service.register_runner(
        CompatibilityRunnerRegistrationRequest(
            runner_id="runner-web-runtime",
            name="Runtime Web Runner",
            os="Windows 11",
            capabilities=["local_browser", "playwright", "chrome"],
            max_parallel=1,
        )
    )
    runtime = CompatibilityTestingModeRuntime(runner_service=service)
    context = SimpleNamespace(session_id="session-runtime", user_message="兼容性测试", context_bundle={})

    draft = await runtime.handle(
        {
            "action": "draft_plan",
            "product_name": "Runtime Web",
            "product_type": "web",
            "target_url": "https://example.test",
            "auth_strategy": "none",
            "priority_flows": ["搜索"],
        },
        context,
    )

    assert draft["ok"] is True
    plan = draft["plan"]
    available_environment_ids = [
        environment["environment_id"]
        for environment in plan["environments"]
        if environment["availability"] == "available"
    ]
    unavailable_environment_ids = [
        environment["environment_id"]
        for environment in plan["environments"]
        if environment["availability"] != "available"
    ]
    assert available_environment_ids
    assert unavailable_environment_ids
    assert all("登录" not in case["name"] for case in plan["cases"])

    invalid = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": plan,
            "selected_case_ids": ["missing-case"],
            "selected_environment_ids": available_environment_ids[:1],
        },
        context,
    )
    assert invalid["ok"] is False
    assert invalid["error"] == "unknown_selected_cases"
    assert invalid["unknown_case_ids"] == ["missing-case"]

    invalid_environment = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": plan,
            "selected_case_ids": [plan["cases"][0]["case_id"]],
            "selected_environment_ids": ["missing-environment"],
        },
        context,
    )
    assert invalid_environment["ok"] is False
    assert invalid_environment["error"] == "unknown_selected_environments"
    assert invalid_environment["unknown_environment_ids"] == ["missing-environment"]

    empty_cases = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": plan,
            "selected_case_ids": [],
            "selected_environment_ids": available_environment_ids[:1],
        },
        context,
    )
    assert empty_cases["ok"] is False
    assert empty_cases["error"] == "no_selected_cases"

    empty_environments = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": plan,
            "selected_case_ids": [plan["cases"][0]["case_id"]],
            "selected_environment_ids": [],
        },
        context,
    )
    assert empty_environments["ok"] is False
    assert empty_environments["error"] == "no_selected_environments"

    no_runnable_environments = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": plan,
            "selected_case_ids": [plan["cases"][0]["case_id"]],
            "selected_environment_ids": unavailable_environment_ids[:1],
        },
        context,
    )
    assert no_runnable_environments["ok"] is False
    assert no_runnable_environments["error"] == "no_runnable_environments"
    assert no_runnable_environments["dispatch_plan"]["tasks"] == []
    assert no_runnable_environments["missing_components"] == ["available runner/provider"]

    dispatched = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": plan,
            "selected_case_ids": [plan["cases"][0]["case_id"]],
            "selected_environment_ids": available_environment_ids[:1],
        },
        context,
    )
    assert dispatched["ok"] is True
    assert dispatched["runner_queue"]["queued_task_count"] == 1
    assert "artifact upload pipeline" not in dispatched["missing_components"]
    assert "mode invoker execution bridge" in dispatched["missing_components"]
    tasks = await service.list_tasks(dispatch_id=dispatched["dispatch_plan"]["dispatch_id"])
    assert len(tasks) == 1
    assert tasks[0].case_ids == [plan["cases"][0]["case_id"]]

    risk_draft = await runtime.handle(
        {
            "action": "draft_plan",
            "product_name": "Risk Scoped Web",
            "product_type": "web",
            "target_url": "https://example.test",
            "auth_strategy": "none",
            "priority_flows": ["删除资料", "搜索"],
        },
        context,
    )
    assert risk_draft["ok"] is True
    risk_plan = risk_draft["plan"]
    risk_available_environment_ids = [
        environment["environment_id"]
        for environment in risk_plan["environments"]
        if environment["availability"] == "available"
    ]
    safe_case_id = next(case["case_id"] for case in risk_plan["cases"] if "搜索" in case["name"])
    risky_case_id = next(case["case_id"] for case in risk_plan["cases"] if "删除" in case["name"])
    assert any(risk.get("case_id") == risky_case_id for risk in risk_plan["risks"])

    safe_subset = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": risk_plan,
            "selected_case_ids": [safe_case_id],
            "selected_environment_ids": risk_available_environment_ids[:1],
        },
        context,
    )
    assert safe_subset["ok"] is True
    assert safe_subset["runner_queue"]["queued_task_count"] == 1
    assert safe_subset["dispatch_plan"]["tasks"][0]["case_ids"] == [safe_case_id]

    risky_subset = await runtime.handle(
        {
            "action": "execute_approved_plan",
            "plan": risk_plan,
            "selected_case_ids": [risky_case_id],
            "selected_environment_ids": risk_available_environment_ids[:1],
        },
        context,
    )
    assert risky_subset["ok"] is True
    assert risky_subset["phase"] == "awaiting_approval"
    assert risky_subset["risks"][0]["case_id"] == risky_case_id
