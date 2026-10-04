"""Shipped document plugin and actual original/derived downloads."""
from __future__ import annotations

import json
import os

import pytest
from playwright.async_api import expect
from test_case_planning import environment
from document_plugin_browser import document_plugin_browser


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_DOC_BROWSER") != "1", reason="real Chromium and isolated PG/MySQL/RustFS")
async def test_actual_upload_detail_download_and_immutable_metadata_derivatives(tmp_path):
    async with environment(tmp_path, True) as env:
        source = tmp_path / "订单接口.json"
        source.write_bytes(json.dumps({"openapi": "3.0.1", "info": {"title": "浏览器原件验收", "version": "1"},
            "paths": {"/orders": {"get": {"summary": "查询订单", "responses": {"200": {"description": "成功"}}}}}},
            ensure_ascii=False).encode("utf-8-sig"))
        async with document_plugin_browser(env, "p3-doc-assets-version-regression-browser") as (page, evidence, errors):
            await expect(page.get_by_text("还没有导入任何 API 文档", exact=True)).to_be_visible()
            await page.get_by_role("button", name="打开导入").click()
            await page.locator('input[type="file"]').set_input_files(source)
            async with page.expect_response(lambda response: response.url.endswith("/api-docs/upload")) as uploading:
                await page.get_by_role("button", name="开始导入", exact=True).click()
            uploaded = await (await uploading.value).json()
            await expect(page.locator(".upload-modal")).not_to_be_visible()
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await expect(page.get_by_role("heading", name="上传原件", exact=True)).to_be_visible()
            downloads = []

            async def save_download(index):
                async with page.expect_download() as downloading:
                    await page.locator(".document-assets").get_by_role("button", name="下载", exact=True).nth(index).click()
                download = await downloading.value
                target = tmp_path / f"download-{len(downloads)}"
                await download.save_as(target)
                downloads.append((download.suggested_filename, target.read_bytes()))
                return target.read_bytes()

            assert await save_download(0) == source.read_bytes()
            first_markdown = await save_download(1)
            assert b"GET /orders" in first_markdown
            for host_url in ("https://first.example.test", "https://second.example.test"):
                await page.locator(".preview-form-grid label").filter(has_text="项目 URL / Base URL").locator("input").fill(host_url)
                async with page.expect_response(lambda response: response.request.method == "PATCH") as updating:
                    await page.get_by_role("button", name="保存文档信息", exact=True).click()
                response = await updating.value
                assert response.status == 200
                current = await response.json()
                await expect(page.locator(".document-assets").get_by_role("button", name="下载", exact=True)).to_have_count(1 + len(current["derived_assets"]))
            assert await save_download(0) == source.read_bytes()
            assert await save_download(1) == first_markdown
            assert b"second.example.test" in await save_download(3)
            persisted = await env.docs.get_document(uploaded["id"])
            assert len(persisted.derived_assets) == 3
            assert persisted.original_asset == (await env.docs._catalog_store.get(uploaded["id"])).original_asset
            assert not errors, errors
            (evidence / "p3-doc-assets-version-regression-browser.json").write_text(json.dumps({
                "source_file": source.name, "original_bytes_equal": True, "old_derived_bytes_equal": True,
                "derived_assets": len(persisted.derived_assets), "downloads": [name for name, _ in downloads],
                "page_errors": errors, "aria_snapshot": await page.locator("body").aria_snapshot(),
                "scope": "actual ApiDocsPlugin in isolated host; real routes/PG/RustFS; full product startup not covered",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.screenshot(path=str(evidence / "p3-doc-assets-version-regression-browser.png"), full_page=True)
