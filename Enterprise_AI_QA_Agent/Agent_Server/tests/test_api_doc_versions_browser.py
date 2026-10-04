"""Real files and shipped UI: uncertain receipts, V1/V2 and immutable history."""
from __future__ import annotations

import json
import os
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from playwright.async_api import expect
from test_case_planning import environment
from document_plugin_browser import document_plugin_browser
from src.schemas.project import ProjectCreateRequest
from src.schemas.api_docs import ApiDocRecord, ApiDocVersionUploadRequest
import base64


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_DOC_BROWSER") != "1", reason="real Chromium and isolated PG/MySQL/RustFS")
async def test_browser_recovers_lost_upload_and_version_receipts_and_downloads_history(tmp_path):
    async with environment(tmp_path, True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="browser-versions", name="浏览器版本验收"))
        first = tmp_path / "需求.md"
        first.write_bytes("# 旧规则\r\n订单查询有效期为 3 天。\r\n".encode("utf-8-sig"))
        second = tmp_path / "更新" / "需求.md"
        second.parent.mkdir()
        second.write_bytes("# 新规则\r\n订单查询有效期为 7 天。\r\n".encode())
        async with document_plugin_browser(env, "p3-doc-versions-browser") as (page, evidence, errors):
            captured = []

            async def lose_response(route):
                response = await route.fetch()
                assert response.status == 200
                captured.append(await response.json())
                await route.abort("failed")

            await page.get_by_role("button", name="打开导入", exact=True).click()
            await page.locator('.upload-modal input[type="file"]').set_input_files(first)
            await page.locator(".upload-form-grid label").filter(has_text="所属项目（建议填写）").locator(".n-base-selection").click()
            await page.get_by_text(project.name, exact=True).last.click()
            await page.route("**/api-docs/upload", lose_response)
            await page.get_by_role("button", name="开始导入", exact=True).click()
            await expect(page.locator(".pending-request")).to_be_visible()
            await expect(page.get_by_role("button", name="开始导入", exact=True)).to_be_enabled()
            assert len(captured) == 1
            first_doc = captured[0]
            assert first_doc["project_id"] == project.id and first_doc["version_number"] == 1
            await page.unroute("**/api-docs/upload")
            await page.reload()
            await page.get_by_role("button", name="打开导入", exact=True).click()
            await page.get_by_role("button", name="查询上次提交", exact=True).click()
            await expect(page.locator(".upload-modal")).not_to_be_visible()
            assert len(await env.docs.list_documents(project_id=project.id)) == 1
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await expect(page.get_by_role("heading", name="文档版本历史", exact=True)).to_be_visible()
            await page.get_by_label("选择新版本文件", exact=True).set_input_files(second)
            await page.route("**/api-docs/*/versions", lose_response)
            await page.get_by_role("button", name="上传新版本", exact=True).click()
            await expect(page.locator(".document-versions .pending-request")).to_be_visible()
            await expect(page.get_by_role("button", name="上传新版本", exact=True)).to_be_enabled()
            assert len(captured) == 2 and captured[1]["version"]["version"] == 2
            await page.unroute("**/api-docs/*/versions")
            await page.reload()
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await page.get_by_role("button", name="查询上次提交", exact=True).click()
            await expect(page.locator(".document-versions .pending-request")).not_to_be_visible()
            await expect(page.locator(".document-versions").get_by_text("当前版本: V2", exact=True)).to_be_visible()
            assert len(await env.docs.list_versions(first_doc["id"])) == 2
            for number, source in [(1, first), (2, second)]:
                await page.get_by_role("button", name=f"查看版本 V{number}", exact=True).click()
                panel = page.locator(".document-version-preview")
                await expect(panel.locator("pre")).to_contain_text("3 天" if number == 1 else "7 天")
                async with page.expect_download() as downloading:
                    await panel.get_by_role("button", name="下载", exact=True).first.click()
                target = tmp_path / f"download-V{number}.md"
                await (await downloading.value).save_as(target)
                assert target.read_bytes() == source.read_bytes()
            await page.get_by_label("选择新版本文件", exact=True).set_input_files(second)
            await page.get_by_role("button", name="上传新版本", exact=True).click()
            await expect(page.get_by_text("内容未变化，已保留当前版本。", exact=True)).to_be_visible()
            assert len(await env.docs.list_versions(first_doc["id"])) == 2
            # Another committed update makes this open page's parent stale.
            current = await env.docs.get_document(first_doc["id"])
            third = await env.docs.upload_version(current.id, ApiDocVersionUploadRequest(filename="third.md",
                content_base64=base64.b64encode(b"# third rule").decode(), project_id=project.id,
                request_id="other-window", expected_parent_version_id=current.current_version_id))
            await page.get_by_label("选择新版本文件", exact=True).set_input_files(first)
            await page.get_by_role("button", name="上传新版本", exact=True).click()
            await expect(page.locator(".document-versions [role='alert']")).to_contain_text("文档父版本已变化")
            assert len(await env.docs.list_versions(first_doc["id"])) == 3
            await page.get_by_role("button", name="重新按当前版本准备上传", exact=True).click()
            await expect(page.locator(".document-versions [role='status']")).to_contain_text("已读取当前版本")
            await page.get_by_role("button", name="上传新版本", exact=True).click()
            await expect(page.locator(".document-versions [role='status']")).to_contain_text("已保存版本 V4")
            newest = (await env.docs.list_versions(first_doc["id"]))[0]
            assert newest.parent_version_id == third.version.id
            assert not errors, errors
            (evidence / "p3-doc-versions-browser.json").write_text(json.dumps({
                "recovered_first_upload": True, "recovered_version_upload": True, "version_count": 4,
                "historical_downloads_equal": True, "unchanged_content_no_version": True,
                "stale_parent_rejected": True, "explicit_parent_refresh_then_restore_v4": True,
                "page_errors": errors, "aria_snapshot": await page.locator("body").aria_snapshot(),
                "scope": "actual ApiDocsPlugin in isolated host; production routes, real PG/MySQL/RustFS; no full product startup",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.screenshot(path=str(evidence / "p3-doc-versions-browser.png"), full_page=True)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_API_DOC_BROWSER") != "1", reason="real Chromium and isolated PG/MySQL/RustFS")
async def test_browser_initializes_legacy_history_with_original_missing(tmp_path):
    async with environment(tmp_path, True) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="legacy-browser", name="历史版本项目"))
        source = tmp_path / "历史内容.md"
        source.write_bytes(b"# legacy content\r\n")
        doc_id = str(uuid4())
        stored = await env.objects.store_uploaded_bytes(content=source.read_bytes(), filename=source.name,
            object_prefix=f"api-docs/{doc_id}/legacy", content_type="text/markdown")
        now = datetime.now(timezone.utc)
        old = ApiDocRecord(id=doc_id, title="旧目录文档", filename=source.name, project_id=project.id,
            storage_uri=stored["uri"], content_type="text/markdown", preview_text="# old preview", preview_available=True,
            uploaded_at=now, updated_at=now)
        await env.docs._catalog_store.create(old)
        try:
            async with document_plugin_browser(env, "p3-doc-history-browser") as (page, evidence, errors):
                await page.locator(".api-doc-card .api-doc-actions button").first.click()
                await page.get_by_role("button", name="初始化历史版本", exact=True).click()
                await expect(page.locator(".document-versions [role='status']")).to_contain_text("已保存版本 V1")
                await page.get_by_role("button", name="查看版本 V1", exact=True).click()
                panel = page.locator(".document-version-preview")
                await expect(panel.get_by_text("此历史记录未保存原件，当前内容不能替代原件。", exact=True)).to_be_visible()
                await expect(panel.locator("pre")).to_contain_text("legacy content")
                async with page.expect_download() as downloading:
                    await panel.get_by_role("button", name="下载", exact=True).click()
                target = tmp_path / "legacy-download.md"
                await (await downloading.value).save_as(target)
                assert target.read_bytes() == source.read_bytes()
                fixed = (await env.docs.list_versions(doc_id))[0]
                assert fixed.snapshot.original_asset is None
                assert not errors, errors
                (evidence / "p3-doc-history-browser.json").write_text(json.dumps({
                    "stable_document_id": old.id == fixed.document_id, "version": 1, "original_missing": True,
                    "actual_derived_download_equal": True, "page_errors": errors,
                    "aria_snapshot": await page.locator("body").aria_snapshot(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                await page.screenshot(path=str(evidence / "p3-doc-history-browser.png"), full_page=True)
        finally:
            await env.docs.delete_document(doc_id)
