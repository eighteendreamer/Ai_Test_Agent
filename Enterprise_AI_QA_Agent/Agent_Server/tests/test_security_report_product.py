"""Actual tool results through existing campaign report stage and built App."""
import json
import os
from pathlib import Path
from uuid import uuid4

from playwright.async_api import expect
import pytest

from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.modes.security_testing_mode.campaign_state import SecurityCampaign, SecurityTask, SecurityTestingState, TargetCandidate
from src.modes.security_testing_mode.contracts import PHASE_ATTACK_PLAN_READY, STATE_METADATA_KEY
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.session import CreateSessionRequest
from test_document_full_product import product_browser
from test_execution_worker_cli import wait_api
from test_security_evidence_live import actual_header_evidence

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SECURITY_REPORT_PRODUCT") != "1",
    reason="real Kali, stores, independent API and built full App; no model")]


async def test_real_security_report_artifact_and_built_app(tmp_path):
    async def probe(**fixture):
        tools, client, session = fixture["tools"], fixture["client"], fixture["session"]
        evidence = fixture["evidence"]
        mode = tools._security_testing_mode_runtime
        campaign = SecurityCampaign(campaign_id=str(uuid4()), objective="owned response header report",
            targets=[TargetCandidate(value="http://127.0.0.1", target_type="url")])
        for variant, record in zip(("missing", "complete", "unreachable"), fixture["records"]):
            out = record.output
            task = SecurityTask(task_id=variant, name=variant, target=str(record.input["target"]),
                command_profile="http_headers_probe", tool_family="web_scan", surface_type="web",
                status="completed" if out["ok"] else "failed", max_retries=0, attempts=1,
                started_at=record.started_at.isoformat(), completed_at=record.completed_at.isoformat(),
                parsed_result=out["parsed_result"], result_summary=out["summary"], last_error=out.get("error") or "",
                raw_output=out["raw_output"], artifacts=[a["path"] for a in out["artifacts"]])
            campaign.tasks.append(task)
            mode._evidence_service.record_runner_result(campaign, task, out, started_at=task.started_at)
            mode._record_local_activity(task, task.started_at, campaign=campaign)
        mode._hydrate_campaign_from_task_results(campaign)
        # Exercise the existing persisted resume boundary after actual tools;
        # discovery, planning and Agent decisions are explicitly outside this test.
        state = SecurityTestingState(session_id=session["id"], phase=PHASE_ATTACK_PLAN_READY,
            campaign=campaign, execution_checkpoint={"post_recon_stage": 4})
        context = ToolExecutionContext(session_id=session["id"], turn_id="security-report", trace_id="security-report-product",
            user_message="owned response header report", normalized_input="security report",
            context_bundle={"project_id": session["project_id"], STATE_METADATA_KEY: state.model_dump(mode="json")})
        tool = ToolDescriptor(key="security-scan-runner", name="security report", description="existing campaign resume", category="test")
        result = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key,
            arguments={"objective": "owned response header report", "target_url": "http://127.0.0.1"}), context)
        (evidence / "report-record.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        for artifact in result.output.get("artifacts", []):
            if str(artifact.get("path", "")).startswith("rustfs://"):
                fixture["owned_objects"].append(artifact["path"])
        detail = (await client.get(f"/sessions/{session['id']}/tool-jobs/{result.job_id}")).json()
        (evidence / "report-job.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        problems = []
        report = result.output.get("report", {})
        expected = {"total_tasks": 3, "completed_tasks": 2, "failed_tasks": 1, "total_findings": 4}
        if any(report.get(k) != v for k, v in expected.items()):
            problems.append("report counts differ from real tool results")
        folder = evidence / "report-originals"
        folder.mkdir(exist_ok=True)
        for artifact in detail.get("artifacts", []):
            response = await client.get(f"/sessions/{session['id']}/artifacts/{artifact['id']}/content")
            response.raise_for_status()
            (folder / artifact["metadata"]["filename"]).write_bytes(response.content)
            if artifact["artifact_type"] == "report_json":
                saved = response.json()
                for field in ("settlement", "verification_result", "evaluation_result"):
                    if saved.get(field) != report.get(field):
                        problems.append("download JSON differs from final " + field)
        response = await client.get("/reports?limit=100")
        response.raise_for_status()
        listing = response.json()
        (evidence / "report-list.json").write_text(json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8")
        if not any(item["session"]["id"] == session["id"] for item in listing["items"]):
            problems.append("security report excluded from production report list")
        (evidence / "report-problems.json").write_text(json.dumps(problems, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not problems, problems
        added = []
        for mode_key in ["default"] * 12 + ["code_review", "performance_testing"]:
            response = await client.post("/sessions", json=CreateSessionRequest(project_id=session["project_id"], mode_key=mode_key).model_dump(mode="json"))
            response.raise_for_status()
            added.append(response.json())
        idle_perf = added[-1]
        pages = [(await client.get(f"/reports?limit=2&offset={offset}")).json() for offset in (0, 2)]
        assert pages[0]["has_more"] is True and pages[1]["has_more"] is False
        assert {item["session"]["id"] for p in pages for item in p["items"]} == {session["id"], added[-2]["id"], idle_perf["id"]}
        pending = next(item for p in pages for item in p["items"] if item["session"]["id"] == idle_perf["id"])
        assert pending["report_meta"]["ready"] is False
        (evidence / "report-pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
        async with product_browser(tmp_path, fixture["api_port"], evidence) as (page, errors, network):
            await expect(page.locator(".left-nav")).to_be_visible()
            await page.locator('.left-nav a[href="/reports"]').click()
            batch = page.locator(".batch-item").filter(has_text=session["id"][:8])
            await expect(batch).to_be_visible()
            await batch.click()
            body = page.locator(".report-detail .summary-body").first
            await expect(body).to_contain_text("任务总数：3")
            await expect(body).to_contain_text("失败：1")
            await expect(body).to_contain_text("缺少 X-Frame-Options")
            await expect(page.locator(".report-detail .grade-box")).to_have_text("需关注")
            await expect(page.locator(".report-detail .detail-desc")).to_contain_text("部分完成")
            json_card = page.locator(".artifact-card").filter(has_text="安全测试报告（JSON）")
            async with page.expect_download() as downloaded:
                await json_card.get_by_role("link", name="下载原件", exact=True).click()
            download = await downloaded.value
            path = evidence / "browser-report-download.json"
            await download.save_as(str(path))
            from_browser = json.loads(path.read_text(encoding="utf-8"))
            assert all(from_browser.get(k) == report.get(k) for k in (*expected, "settlement", "verification_result", "evaluation_result"))
            await page.screenshot(path=str(evidence / "security-report.png"), full_page=True)
            await page.locator(".batch-item").filter(has_text=idle_perf["id"][:8]).click()
            await expect(page.locator(".report-detail .grade-box")).to_have_text("进行中")
            planned_start = len(network)
            await fixture["processes"].stop(fixture["api"])
            restarted = fixture["processes"].start("security-report-product-restarted", "-m", "uvicorn", "src.main:app",
                "--host", "127.0.0.1", "--port", str(fixture["api_port"]))
            await wait_api(client, restarted)
            planned_end = len(network)
            async with page.expect_response(lambda r: "/api/v1/reports?" in r.url) as refreshed:
                await page.locator(".report-page .head-actions button").click()
            assert (await refreshed.value).status == 200
            await page.locator(".batch-item").filter(has_text=session["id"][:8]).click()
            await expect(body).to_contain_text("失败：1")
            await expect(page.locator(".report-detail .grade-box")).to_have_text("需关注")
            unplanned = [r for i, r in enumerate(network) if r["status"] >= 400 and not planned_start <= i < planned_end]
            assert not unplanned, unplanned
            (evidence / "report-product-facts.json").write_text(json.dumps({"scope": "actual tool results at existing report resume boundary; built full App and independent main; no AI/planning",
                "report_counts": expected, "report_status": result.status, "api_restart_report_equal": True,
                "browser_download_final_fields_equal": True, "mode_filter_before_paging": True, "pending_report_grade": "进行中",
                "planned_restart_failures": [r for r in network[planned_start:planned_end] if r["status"] >= 400],
                "unplanned_api_failures": unplanned, "page_errors": errors, "real_target_gets": len(fixture["events"])}, ensure_ascii=False, indent=2), encoding="utf-8")
            assert not errors, errors
            assert len(fixture["events"]) == 2

    await actual_header_evidence(tmp_path, report_probe=probe)
