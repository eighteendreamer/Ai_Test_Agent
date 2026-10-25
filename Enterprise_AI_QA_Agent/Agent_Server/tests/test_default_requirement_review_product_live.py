"""Built App drives impact review and traceability pages over the real default model."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
import pytest
from pydantic import SecretStr
from playwright.async_api import expect

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_management import TestCaseBundle as CaseBundle
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_full_product import product_browser
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_REQUIREMENT_REVIEW_PRODUCT") != "1",
    reason="built App pages, production API/independent CLIs, real default model")]

V1 = """# 订单查询需求

## 查询订单详情
GET /orders/{id} 返回 200，JSON 字段：status（字符串）、total（整数，单位分，商品原价合计）。
"""

V2 = """# 订单查询需求

## 查询订单详情
GET /orders/{id} 返回 200，JSON 字段：status（字符串）、total（整数，单位分，折后应付合计）、
discount（整数，单位分，优惠总额，无优惠时为 0）。
"""


async def test_product_impact_review_and_trace_with_default_model(tmp_path):
    evidence = Path(os.environ["QA_DEFAULT_REQUIREMENT_REVIEW_PRODUCT_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    configured_store = MySQLModelConfigStore(get_settings().model_copy(deep=True))
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"model_id": configured.model_id, "transport": configured.transport,
             "configured_parameters": configured_parameters, "controlled_model": False,
             "product_impact": None, "product_review": None}
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            # The built-App proxy sends an empty bearer; like the controlled
            # product acceptance, run the API without a token so loopback calls
            # pass the middleware.
            settings.orchestration.redis_task_outbox_streams = ",".join(
                [settings.orchestration.redis_task_outbox_streams,
                 DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM])
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=configured.model_id, provider=configured.provider, transport=configured.transport,
                request_profile=configured.request_profile, base_url=configured.api_base_url,
                api_key=configured.api_key, auth_type=configured.auth_type,
                oauth_provider=configured.oauth_provider, oauth_refresh_token=configured.oauth_refresh_token,
                applications=configured.applications, is_active=True, use_provider_defaults=False,
                capability_overrides=configured.capability_overrides, context_window=configured.context_window,
                max_output_tokens=configured.max_output_tokens))
            assert model_invocation_parameters(models.get_default_active()) == configured_parameters
            save("model-parameters.json", configured_parameters)
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            api_port = free_port()
            api = processes.start("review-product-api", "-m", "tests.default_model_probe", "uvicorn",
                "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=300) as client:
                    await wait_api(client, api)

                    async def post(path, body=None, status=200):
                        response = await client.post(path, json=body)
                        assert response.status_code == status, (path, response.status_code, response.text)
                        return response.json()

                    async def get(path):
                        response = await client.get(path)
                        assert response.status_code == 200, (path, response.status_code, response.text)
                        return response.json()

                    async def relay(stream, limit=1):
                        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
                            group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                        await queue.connect()
                        try:
                            return await PostgresTaskOutbox(settings).relay_once(queue, limit=limit)
                        finally:
                            await queue.close()

                    async def run_worker(name, module, worker_id, timeout=600):
                        process = processes.start(name, "-m", "tests.default_model_probe", module,
                            "--once", "--worker-id", worker_id)
                        assert await processes.wait(process, timeout=timeout) == 0, (
                            name, (tmp_path / f"{name}.stderr").read_text(encoding="utf-8", errors="replace")[-2000:])

                    project = await post("/projects", {"project_key": "review-product-" + uuid4().hex[:10],
                        "name": "产品页面需求评审验收", "base_url": "http://127.0.0.1:1"}, 201)
                    root = "/projects/" + project["id"]
                    document = await post("/registry/api-docs/upload", {"project_id": project["id"],
                        "filename": "requirements.md", "request_id": "review-v1",
                        "content_base64": base64.b64encode(V1.encode("utf-8")).decode()})
                    v1_version_id = document["current_version_id"]
                    base = root + f"/documents/{document['id']}"
                    second = await post(f"/registry/api-docs/{document['id']}/versions", {
                        "project_id": project["id"], "filename": "requirements.md", "request_id": "review-v2",
                        "expected_parent_version_id": document["current_version_id"],
                        "content_base64": base64.b64encode(V2.encode("utf-8")).decode()})
                    document = second["document"]

                    from src.schemas.document_parsing import DocumentParseSnapshotView
                    views = []
                    for number, version_id in enumerate([v1_version_id, document["current_version_id"]], 1):
                        task = await post(base + "/parse-tasks",
                            {"creation_key": f"parse-v{number}", "version_id": version_id}, 202)
                        assert await relay(DOCUMENT_PARSE_STREAM, 1) == 1
                        await run_worker(f"parse-worker-{number}", "src.cli.run_document_parse_worker", f"parse-{number}")
                        parsed = await get(base + f"/parse-tasks/{task['id']}")
                        assert parsed["status"] == "completed", parsed
                        views.append(DocumentParseSnapshotView.model_validate(
                            await get(base + f"/parse-snapshots/{parsed['snapshot_id']}")))
                    comparison = await post(base + "/difference-tasks", {"creation_key": "review-comparison",
                        "old_snapshot_id": views[0].snapshot.id, "new_snapshot_id": views[1].snapshot.id}, 202)
                    assert await relay(DOCUMENT_DIFFERENCE_STREAM, 1) == 1
                    await run_worker("difference-worker", "src.cli.run_document_difference_worker", "compare")
                    compared = await get(base + f"/difference-tasks/{comparison['id']}")
                    assert compared["status"] == "completed", compared

                    case_payload = {"case_key": "order-total-check", "title": "核对订单总价",
                        "case_type": "json", "mode_key": "api_testing",
                        "content_json": {"objective": "查询订单详情并核对 total 为商品原价合计",
                            "steps": [{"order": 1, "action": "GET /orders/{{id}}，核对 status 与 total"}],
                            "expected": {"status_code": 200, "total": "商品原价合计"}},
                        "execution_notes": "完整读取用例 JSON；total 语义以需求文档为准",
                        "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                        "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"}}
                    created = await post(root + "/test-cases", case_payload, 201)
                    case = CaseBundle.model_validate(created)
                    await post(f"/test-cases/{case.case.id}/submit-review", {})
                    await post(f"/test-cases/{case.case.id}/activate", {})

                    async with product_browser(tmp_path, api_port, evidence) as (page, page_errors, network):
                        browser_url = urlsplit(page.url)
                        origin = f"{browser_url.scheme}://{browser_url.netloc}"
                        await page.goto(origin + base + "/differences/" + compared["id"])
                        panel = page.get_by_label("文档影响分析入口", exact=True)
                        await expect(panel).to_contain_text(compared["snapshot_id"])
                        await panel.get_by_text("项目影响分析设置", exact=True).click()
                        await panel.get_by_label("影响默认模型配置编号", exact=True).fill(model.key)
                        await panel.get_by_label("影响分析指导", exact=True).fill("以真实变更语义为准，引用新版本来源位置")
                        await panel.get_by_role("button", name="保存影响模型指导", exact=True).click()
                        await expect(panel.get_by_role("status")).to_contain_text("项目影响设置已保存")
                        await panel.get_by_label("影响请求发送策略", exact=True).select_option("single_attempt")
                        await panel.get_by_role("button", name="保存影响调用预算", exact=True).click()
                        await expect(panel.get_by_role("status")).to_contain_text("项目影响设置已保存")
                        await panel.get_by_label("影响分析用例范围", exact=True).select_option("explicit")
                        await panel.get_by_role("button", name="读取 order-total-check 的完整版本", exact=True).click()
                        await panel.get_by_label("order-total-check 版本 1", exact=True).check()
                        await panel.get_by_label("影响环境声明", exact=True).fill("qa")
                        await panel.get_by_label("影响发布版本声明", exact=True).fill("change-v2")
                        async with page.expect_response(lambda response: response.request.method == "POST"
                            and "/impact-tasks" in response.url) as impact_info:
                            await panel.get_by_role("button", name="提交固定影响分析", exact=True).click()
                        submitted = await (await impact_info.value).json()
                        facts["product_impact"] = {"task_id": submitted["id"], "submitted_from": "built App"}

                        # The review scenario reloads the page and relies on the
                        # same-origin sessionStorage submission recovery, so the
                        # worker must run inside this single browser session.
                        assert await relay(DOCUMENT_IMPACT_STREAM, 1) == 1
                        await run_worker("impact-worker", "src.cli.run_document_impact_worker", "impact")
                        done = await get(base + f"/impact-tasks/{submitted['id']}")
                        assert done["status"] == "completed", done
                        saved_view = await get(base + f"/impact-snapshots/{done['snapshot_id']}")
                        result = saved_view["result"]
                        save("product-impact-snapshot.json", saved_view)
                        assert result["rules"], result
                        facts["product_impact"].update(rules=len(result["rules"]),
                            units=len(result.get("units") or []), status=result["status"])

                        await expect(page.locator(".impact-task-status")).to_contain_text("影响结果已保存", timeout=30000)
                        from requirement_product_scenario import review_from_product
                        review_state = await review_from_product(page, base=base, view=saved_view, case=case,
                            get=get, post=post, evidence=evidence, settings=settings)
                        requirement_id = review_state["requirement_id"]
                        facts["product_review"] = {"panel": "需求评审与追踪",
                            "lost_reply_recovered": bool(review_state["submissions_with_lost_reply"]),
                            "review_history": len(review_state["reviews"]),
                            "requirement_id": requirement_id, "user_business_review": True}
                        assert not page_errors, page_errors
                        # 409s are expected: the review scenario deliberately
                        # submits conflicting revisions and asserts the rejections.
                        assert not [r for r in network if r["status"] >= 500], network
                        matrix = await get(base + f"/requirements/{requirement_id}/matrix")
                        save("requirement-matrix.json", matrix)
                        facts["product_review"]["matrix_points"] = len(matrix["points"])
            finally:
                if api.poll() is None:
                    await processes.stop(api)
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"}:
                (evidence / path.name).write_bytes(path.read_bytes())
        facts["global_model_unchanged"] = model_invocation_parameters(
            configured_store.get_default_active()) == configured_parameters
        save("verification.json", facts)
