"""Built App ordinary entry, lost submit reply, progress and shared JSON detail."""
import asyncio
import json

from playwright.async_api import expect
from test_document_full_product import open_documents, product_browser


async def document_generation_product(*, tmp_path, port, evidence, project, doc, snapshot, model_key, client, run, calls):
    base = f"/projects/{project['id']}/documents/{doc['id']}"
    async with product_browser(tmp_path, port, evidence) as (page, errors, network):
        async def open():
            await open_documents(page)
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await page.get_by_role("button", name="查看解析快照 "+snapshot["snapshot"]["id"], exact=True).click()
            await expect(page.get_by_label("普通文档模块生成", exact=True)).to_be_visible()
        await open()
        panel = page.get_by_label("普通文档模块生成", exact=True)
        await panel.get_by_label("普通文档生成模型", exact=True).fill(model_key)
        await panel.get_by_label("普通文档每模块草稿数", exact=True).fill("3")
        lost = asyncio.Event(); submissions = []
        async def lose_reply(route):
            response = await route.fetch()
            assert response.status == 202, await response.text()
            submissions.append({"request": route.request.post_data_json, "group": await response.json()})
            await route.abort("failed"); lost.set()
        pattern = "**/document-case-generations"
        await page.route(pattern, lose_reply)
        before = len(calls)
        await panel.get_by_role("button", name="提交普通文档模块生成", exact=True).click()
        await asyncio.wait_for(lost.wait(), timeout=30)
        await expect(panel.get_by_role("button", name="提交普通文档模块生成", exact=True)).to_be_disabled()
        assert len(submissions) == 1 and len(calls) == before
        await page.unroute(pattern, lose_reply); await page.reload(); await open()
        await expect(panel).to_contain_text("模块进度 0 / 3；成功 0")
        group = submissions[0]["group"]
        assert (await client.get(base+"/case-generations/by-request", params={"creation_key": submissions[0]["request"]["creation_key"]})).json() == group
        for index in range(3): await run("ordinary-product-worker-"+str(index))
        await panel.get_by_role("button", name="回查原普通文档生成", exact=True).click()
        await expect(panel).to_contain_text("模块进度 3 / 3；成功 3")
        assert len(calls) == before+3
        report = (await client.get(base+f"/case-generations/{group['id']}")).json()
        scope = (await client.get(base+f"/case-generations/{group['id']}/scope")).json()
        assert all(task["status"] == "completed" for task in report["modules"])
        await panel.get_by_role("link", name="查看固定模块、完整草稿及原响应", exact=True).first.click()
        detail = page.get_by_label("固定生成任务详情", exact=True)
        await expect(detail).to_contain_text("普通文档生成 · 固定解析快照")
        reference = report["modules"][0]["checkpoint"]["receipt"]["versions"][0]
        await detail.get_by_role("button", name="读取完整任务草稿 "+reference["version_id"], exact=True).click()
        await expect(detail).to_contain_text("手机号必须唯一")
        await expect(detail).to_contain_text('"布尔": false')
        await expect(detail).to_contain_text("商城需求 😀")
        await detail.get_by_role("button", name="读取固定任务原响应", exact=True).click()
        await expect(detail.get_by_text("固定任务完整原响应", exact=True)).to_be_visible()
        await detail.get_by_role("button", name="评审与修订此草稿 "+reference["version_id"], exact=True).click()
        dialog = page.get_by_role("dialog", name="测试用例内容")
        await expect(dialog).to_be_visible()
        await expect(dialog.locator("select")).to_have_value(reference["version_id"])
        before_versions = await client.get(f"/test-cases/{reference['case_id']}/versions")
        assert before_versions.status_code == 200
        original_version = next(item for item in before_versions.json() if item["id"] == reference["version_id"])
        await dialog.get_by_role("button", name="编辑新版本", exact=True).click()
        edited = {**original_version["content_json"], "人工评审备注": "保留固定来源，等待业务确认"}
        await dialog.get_by_label("完整 JSON", exact=True).fill(json.dumps(edited, ensure_ascii=False))
        async with page.expect_response(lambda value: value.request.method == "POST"
                and value.url.endswith(f"/test-cases/{reference['case_id']}/versions")) as saved:
            await dialog.get_by_role("button", name="保存新草稿版本", exact=True).click()
        saved_response = await saved.value
        assert saved_response.status == 201, await saved_response.text()
        saved_revision = await saved_response.json()
        await expect(dialog.locator("select")).to_have_value(saved_revision["id"])
        after_versions = await client.get(f"/test-cases/{reference['case_id']}/versions")
        assert after_versions.status_code == 200
        saved_versions = after_versions.json()
        assert any(item["id"] == reference["version_id"] and item["content_hash"] == original_version["content_hash"]
            for item in saved_versions)
        revised = max(saved_versions, key=lambda item: item["version"])
        assert revised == saved_revision
        assert revised["version"] == original_version["version"] + 1
        assert revised["content_json"] == edited
        assert revised["generation_metadata"]["manual_edit"]["source_version_id"] == reference["version_id"]
        case_response = await client.get(f"/test-cases/{reference['case_id']}")
        assert case_response.status_code == 200 and case_response.json()["active_version_id"] is None
        await dialog.get_by_role("button", name="关闭", exact=True).click()
        fixed_after_review = (await client.get(base+f"/case-generations/{group['id']}")).json()
        assert fixed_after_review["modules"][0]["checkpoint"]["receipt"]["versions"][0]["version_id"] == reference["version_id"]
        assert errors == []
        (evidence/"product.json").write_text(json.dumps({"scope":"built App, actual document UI entry, production API and independent CLI with controlled model",
            "submissions":submissions,"group":group,"fixed_scope":scope,"report":report,
            "model_calls_before":before,"model_calls_after":len(calls),"lost_reply_recovered_without_post":True,
            "reviewed_version": reference["version_id"], "saved_revision": revised["id"],
            "page_errors":errors,"network":network}, ensure_ascii=False, indent=2), encoding="utf-8")
