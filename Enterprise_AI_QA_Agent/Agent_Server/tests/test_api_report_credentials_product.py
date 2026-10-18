"""Cookie reports through real stores, production API, built App and restart."""
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.async_api import expect
import pytest

from test_api_cookie_session_http import campaign_tasks, cookie_target
from test_api_worker_auth_refresh_live import ControlledWorkerLauncher
from test_document_full_product import product_browser
from test_execution_worker_cli import free_port, isolated_runtime, wait_api
from src.application.projects.project_store import PostgresProjectStore
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.domain.models import SessionRecord
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestingState, AssertionSpec, ExecutionPolicy
from src.modes.api_testing_mode.contracts import PHASE_CAMPAIGN_READY, STATE_METADATA_KEY
from src.modes.api_testing_mode.credential_reference_service import ApiCredentialReferenceService
from src.registry.tools import ToolRegistry
from src.runtime.postgres_api_credential_store import PostgresApiCredentialStore
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.project import ProjectRecord
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from src.schemas.tool_runtime import ModelToolCall


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_COOKIE_REPORT_PRODUCT") != "1",
    reason="isolated MySQL/PG/Redis, production API, built App and Chromium; no external AI")
async def test_cookie_report_download_restart_and_original_job_recovery(tmp_path):
    evidence = Path(os.environ["QA_API_COOKIE_REPORT_PRODUCT_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    with cookie_target(True) as (base, events, values):
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            projects = PostgresProjectStore(settings)
            await projects.initialize()
            sessions = PostgresSessionStore(settings)
            await sessions.initialize()
            now = datetime.now(UTC)
            project = await projects.create(ProjectRecord(id=str(uuid4()), project_key="cookie-report",
                name="Cookie report fixture", created_at=now, updated_at=now))
            parent = SessionRecord(id=str(uuid4()), title="Cookie report acceptance", status=SessionStatus.running,
                session_mode=SessionMode.background_task, runtime_mode=RuntimeMode.background,
                mode_key="api_testing", project_id=project.id, created_at=now, updated_at=now)
            await sessions.save_session(parent)
            jobs = ToolJobService(PostgresToolJobStore(settings))
            await jobs.initialize()
            references = ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings), session_store=sessions)
            await references.initialize()
            runtime = ToolRuntimeService(tool_job_service=jobs, artifact_storage_service=objects, settings=settings,
                session_store=sessions, api_credential_reference_service=references)
            launcher = ControlledWorkerLauncher(sessions, runtime)
            runtime.set_coordinator_runtime_service(launcher)
            tasks = campaign_tasks(base)[:3]
            tasks[-1].assertions = [AssertionSpec(kind="status_code", expected=201)]
            state = ApiTestingState(phase=PHASE_CAMPAIGN_READY,
                campaign=ApiTestCampaign(campaign_id=str(uuid4()), project_name=project.name, tasks=tasks,
                    execution_policy=ExecutionPolicy(max_retries=0)))
            context = ToolExecutionContext(session_id=parent.id, turn_id=str(uuid4()), trace_id=str(uuid4()),
                user_message="", normalized_input="", context_bundle={STATE_METADATA_KEY: state.model_dump(mode="json")})
            call = ModelToolCall(id=str(uuid4()), name="api-test-runner", arguments={})
            result = await runtime.execute(ToolRegistry().get(call.name), call, context)
            job = await jobs.get_job_detail(result.job_id)
            for artifact in job.artifacts:
                if artifact.path.startswith("rustfs://"):
                    owned.append(artifact.path)
            assert result.output["report"]["completed"] == 2 and result.output["report"]["failed"] == 1
            assert len(events) == 4 and len(launcher.runs) == 3
            public_safe = all(value not in json.dumps(result.output)+json.dumps(job.output_payload)
                for value in values.values())
            assert public_safe, "Cookie value reached a durable public ToolJob."
            artifacts = []
            for artifact in job.artifacts:
                if artifact.path.startswith("rustfs://"):
                    raw = (await objects.read_object_uri(artifact.path))["content"]
                else:
                    assert artifact.path == "inline://artifact" and artifact.metadata["__storage_mode"] == "inline_text"
                    raw = artifact.metadata["__content_text"].encode("utf8")
                assert all(value.encode() not in raw for value in values.values()), "Cookie value reached a stored artifact."
                filename = artifact.artifact_type + (".md" if artifact.artifact_type.endswith("md") else ".json")
                (evidence / filename).write_bytes(raw)
                artifacts.append({"id": artifact.id, "uri": artifact.path, "type": artifact.artifact_type,
                    "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "saved_filename": filename})
            json_artifact = next(a for a in artifacts if a["type"] == "api_test_report_json")
            assert {a["type"] for a in artifacts} == {"api_test_report_json", "api_test_report_md", "api_test_task_evidence"}
            restored_jobs = ToolJobService(PostgresToolJobStore(settings))
            reread = await restored_jobs.get_job_detail(job.id)
            assert reread.artifacts == job.artifacts and reread.output_payload == job.output_payload
            restored = ToolRuntimeService(tool_job_service=restored_jobs, settings=settings,
                session_store=PostgresSessionStore(settings), artifact_storage_service=objects,
                api_credential_reference_service=ApiCredentialReferenceService(store=PostgresApiCredentialStore(settings),
                    session_store=PostgresSessionStore(settings)))
            replayed = await restored.execute(ToolRegistry().get(call.name), call, replace(context, tool_job_id=job.id))
            assert replayed.job_id == job.id and replayed.output["report"] == result.output["report"]
            assert len(events) == 4
            api_port = free_port()
            api_args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            api = processes.start("cookie-report-api", *api_args)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                await wait_api(client, api)

                async def read_entry():
                    response = await client.get("/reports?limit=100")
                    response.raise_for_status()
                    assert all(value not in response.text for value in values.values()), "Cookie value reached report listing."
                    entry = next(e for e in response.json()["items"] if e["session"]["id"] == parent.id)
                    assert entry["report_meta"]["tool_job_id"] == job.id
                    assert entry["report_meta"]["total_tasks"] == 3 and entry["report_meta"]["completed_tasks"] == 2
                    return entry

                entry = await read_entry()
                async with product_browser(tmp_path, api_port, evidence) as (page, errors, network):
                    await page.locator('.left-nav a[href="/reports"]').click()
                    batch = page.locator(".batch-item").filter(has_text=parent.id[:8])
                    await expect(batch).to_be_visible()
                    await batch.click()
                    cards = page.locator(".api-task-report-card")
                    await expect(cards).to_have_count(3)
                    await page.locator(".api-task-report-card details").evaluate_all(
                        "elements => elements.forEach(element => { element.open = true; })")
                    rows = page.locator(".report-detail .summary-body").first.get_by_role("row")
                    for label, count in (("总任务数", "3"), ("通过", "2"), ("失败", "1")):
                        row = rows.filter(has=page.get_by_role("cell", name=label, exact=True))
                        await expect(row.get_by_role("cell").nth(1)).to_have_text(count)
                    text = await page.locator(".report-detail").text_content()
                    assert all(value not in text for value in values.values()), "Cookie value reached rendered report."
                    assert "opaque_echo" in text and "[REDACTED]" in text
                    await page.screenshot(path=str(evidence / "cookie-report.png"), full_page=True)
                    card = page.locator(".artifact-card").filter(has_text="API 测试报告 (JSON)")
                    async with page.expect_download() as downloaded:
                        await card.get_by_role("link", name="下载原件", exact=True).click()
                    path = evidence / "browser-report-download.json"
                    await (await downloaded.value).save_as(str(path))
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == json_artifact["sha256"]
                    report = json.loads(path.read_bytes())
                    assert report["completed"] == 2 and report["failed"] == 1
                    restart_start = len(network)
                    await processes.stop(api)
                    restarted = processes.start("cookie-report-api-restarted", *api_args)
                    await wait_api(client, restarted)
                    restart_end = len(network)
                    assert await read_entry() == entry
                    response = await client.get(f"/sessions/{parent.id}/artifacts/{json_artifact['id']}/content")
                    response.raise_for_status()
                    assert hashlib.sha256(response.content).hexdigest() == json_artifact["sha256"]
                    await page.reload()
                    await page.locator('.left-nav a[href="/reports"]').click()
                    await page.locator(".batch-item").filter(has_text=parent.id[:8]).click()
                    await expect(page.locator(".api-task-report-card")).to_have_count(3)
                    for label, count in (("总任务数", "3"), ("通过", "2"), ("失败", "1")):
                        row = rows.filter(has=page.get_by_role("cell", name=label, exact=True))
                        await expect(row.get_by_role("cell").nth(1)).to_have_text(count)
                    text = await page.locator(".report-detail").text_content()
                    assert all(value not in text for value in values.values())
                    unplanned = [request for index, request in enumerate(network)
                        if request["status"] >= 400 and not restart_start <= index < restart_end]
                    assert not unplanned, unplanned
                    assert not errors, errors
                    assert len(events) == 4
                    (evidence / "verification.json").write_text(json.dumps({
                        "scope": "production ToolRuntime/PG encrypted credentials/jobs, controlled Worker launch, real owned HTTP, built App/Chromium and independent API restart; no external AI",
                        "project_id": project.id, "parent_job_id": job.id, "events": events, "artifacts": artifacts,
                        "report_counts": {"total": 3, "passed": 2, "failed": 1},
                        "public_job_and_artifacts_safe": public_safe, "download_matches_stored_bytes": True,
                        "restart_report_entry_and_download_equal": True, "terminal_replay_extra_http": 0,
                        "planned_restart_network_range": [restart_start, restart_end],
                        "unplanned_api_failures": unplanned,
                        "page_errors": errors, "network": network, "full_goal_complete": False,
                    }, ensure_ascii=False, indent=2), encoding="utf8")
            await processes.stop(restarted)
            for name in ("product-build.stdout", "product-build.stderr", "cookie-report-api.stdout", "cookie-report-api.stderr",
                         "cookie-report-api-restarted.stdout", "cookie-report-api-restarted.stderr"):
                raw = (tmp_path / name).read_bytes()
                assert all(value.encode() not in raw for value in values.values()), "Cookie value reached process output."
                (evidence / name).write_bytes(raw)
