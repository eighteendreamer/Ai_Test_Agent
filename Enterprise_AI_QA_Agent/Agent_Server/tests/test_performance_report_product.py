"""Real k6 attempt ordering through the production reports API and built App."""
from contextlib import AsyncExitStack
import json
import os
from types import SimpleNamespace
from uuid import uuid4

from playwright.async_api import expect
import pytest

from test_document_full_product import product_browser
from test_performance_evidence_live import actual_performance_evidence

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_PERF_REPORT_PRODUCT") != "1",
    reason="actual k6, isolated stores, independent API and built full App; no model")]


async def test_latest_real_performance_attempt_in_built_app(tmp_path):
    async with AsyncExitStack() as stack:
        browser = None
        observed = []
        planned_start = planned_end = 0

        async def probe(**fixture):
            nonlocal browser, planned_start, planned_end
            stage, record, evidence = fixture["stage"], fixture["record"], fixture["evidence"]
            if stage == "restarting":
                planned_start = len(browser[2])
                return
            if stage == "restarted":
                planned_end = len(browser[2])
            response = await fixture["client"].get("/reports?limit=100")
            response.raise_for_status()
            listing = response.json()
            (evidence / (stage + "-reports.json")).write_text(json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8")
            entry = next(item for item in listing["items"] if item["session"]["id"] == fixture["session"]["id"])
            if browser is None:
                browser = await stack.enter_async_context(product_browser(tmp_path, fixture["api_port"], evidence))
                page, _, _ = browser
                await expect(page.locator(".left-nav")).to_be_visible()
                await page.locator('.left-nav a[href="/reports"]').click()
            page, errors, network = browser
            async with page.expect_response(lambda r: "/api/v1/reports?" in r.url) as refresh:
                await page.locator(".report-page .head-actions button").click()
            assert (await refresh.value).status == 200
            await page.locator(".batch-item").filter(has_text=fixture["session"]["id"][:8]).click()
            await page.locator(".report-detail").evaluate("element => { element.scrollTop = 0; }")
            grade = await page.locator(".report-detail .grade-box").inner_text()
            status = await page.locator(".report-detail .detail-desc").inner_text()
            body = await page.locator(".report-detail .summary-body").first.inner_text()
            facts = {"stage": stage, "expected_job_id": record.job_id, "meta": entry["report_meta"],
                "browser_grade": grade, "browser_status": status, "browser_body": body,
                "engine_runs": fixture["runner"].count}
            observed.append(facts)
            (evidence / "report-product-observations.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.screenshot(path=str(evidence / (stage + "-report.png")), full_page=True)
            assert entry["report_meta"]["tool_job_id"] == record.job_id, "older report masks the latest real attempt"
            if stage == "normal":
                assert grade == "良好" and entry["report_meta"]["verdict"] == "pass"
                assert "pass" in body and "已完成" in status
            elif stage in {"load-inconclusive", "storage-inconclusive", "restarted"}:
                assert grade == "需关注" and "已完成" in status
                assert entry["report_meta"]["ready"] is True and entry["report_meta"]["verdict"] == "inconclusive"
                assert "inconclusive" in body
            elif stage == "queued":
                assert grade == "进行中" and "运行中" in status
                assert entry["report_meta"]["ready"] is False and "pass" not in body
            elif stage == "cancelled":
                assert grade == "需关注" and "中断" in status
                assert entry["report_meta"]["ready"] is False and "pass" not in body
            else:
                assert grade == "较差" and "失败" in status
                assert entry["report_meta"]["ready"] is False
                assert "pass" not in body
            assert not errors, errors
            historical = next((a for a in entry["artifacts"] if a["tool_job_id"] != record.job_id
                and a["artifact_type"] == "performance_metrics_json"), None)
            if historical:
                old_link = page.locator(f'a[href$="/artifacts/{historical["id"]}/content"]')
                await expect(old_link.locator("..")).to_contain_text("历史执行")
                await expect(old_link.locator("..")).to_contain_text(historical["tool_job_id"][:8])
            if entry["report_meta"].get("ready"):
                artifact = next(a for a in entry["artifacts"] if a["tool_job_id"] == record.job_id
                    and a["artifact_type"] == "performance_metrics_json")
                async with page.expect_download() as downloaded:
                    await page.locator(f'a[href$="/artifacts/{artifact["id"]}/content"]').click()
                download = await downloaded.value
                path = evidence / (stage + "-browser-metrics.json")
                await download.save_as(str(path))
                payload = json.loads(path.read_text(encoding="utf-8"))
                assert payload["verdict"] == entry["report_meta"]["verdict"]
                assert payload["metrics"] == record.output["metrics"]
            if stage == "malformed":
                prior = fixture["runner"].count
                job = await fixture["jobs"].create_job(tool=fixture["tool"], call_id=str(uuid4()),
                    session_id=fixture["session"]["id"], turn_id="queued-report-product", trace_id="queued-report-product", input_payload={})
                pending = SimpleNamespace(job_id=job.id)
                await probe(**{**fixture, "stage": "queued", "record": pending})
                await fixture["jobs"].cancel_job(job.id, reason="owned queued task cancelled before any engine execution")
                await probe(**{**fixture, "stage": "cancelled", "record": pending})
                assert fixture["runner"].count == prior
            if stage == "restarted":
                unplanned = [r for i, r in enumerate(network) if r["status"] >= 400 and not planned_start <= i < planned_end]
                (evidence / "report-product-facts.json").write_text(json.dumps({
                    "scope": "actual k6 from existing guard and analyzed report checkpoints; built full App/main; no Agent/model",
                    "stages": [item["stage"] for item in observed], "restart_preserved_latest": True,
                    "browser_download_metrics_equal": True, "waiting_cancel_added_engine_runs": 0,
                    "planned_restart_failures": network[planned_start:planned_end], "unplanned_api_failures": unplanned,
                    "page_errors": errors}, ensure_ascii=False, indent=2), encoding="utf-8")
                assert not unplanned, unplanned

        await actual_performance_evidence(tmp_path, report_probe=probe)
