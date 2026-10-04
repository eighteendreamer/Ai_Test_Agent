"""Shipped document plugin, actual files, PG/MySQL/RustFS and Redis Worker."""
from __future__ import annotations

import base64
import json
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from docx import Document
from playwright.async_api import expect

from src.api.routes.document_parsing import router
from src.application.security.upload_security_service import UploadSecurityService, UploadSecurityError
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_parse_task_handler import DocumentParseTaskHandler
from src.runtime.redis_task_worker import RedisTaskWorker
from src.schemas.project import ProjectCreateRequest
from test_document_parse_tasks import parsing_environment
from test_document_parsers import actual_docx, actual_pdf
from document_plugin_browser import document_plugin_browser

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DOCUMENT_PARSE_BROWSER") != "1", reason="actual Chromium, document API, files and isolated stores")]


@asynccontextmanager
async def parsing_browser(env, service, store, configs, outbox, prefix):
    store.lease_seconds = 30
    queue = RedisTaskQueue(env.settings.database.redis_url, stream=service._stream, group="parsing-browser")
    await queue.connect()
    worker = RedisTaskWorker(queue, consumer="parsing-browser-worker", block_ms=10,
        handler=DocumentParseTaskHandler(service, store, worker_id="parsing-browser-worker"))
    def setup(app):
        app.state.document_parsing_service = service
        app.state.document_parser_config_service = configs
        app.include_router(router, prefix="/api/v1")
    async def process_one():
        assert await outbox.relay_once(queue, limit=1) == 1
        assert await worker.run_once() == 1
    try:
        async with document_plugin_browser(env, prefix, setup_app=setup) as (page, evidence, errors):
            yield page, evidence, errors, process_one
    finally:
        worker.stop()
        await queue._client.delete(queue.stream)
        await queue.close()


async def submit_and_process(page, process_one, *, failed=False):
    async with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/parse-tasks")) as submitted:
        await page.get_by_role("button", name="提交解析", exact=True).click()
    response = await submitted.value
    assert response.status == 202, await response.text()
    task = await response.json()
    await process_one()
    await expect(page.locator(".parse-task-status")).to_contain_text("解析任务失败" if failed else "解析任务已完成", timeout=15000)
    return task


async def test_browser_docx_lost_submission_refresh_immutable_versions_and_json_download(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, configs, outbox):
        env.docs._upload_security_service = UploadSecurityService(settings=env.settings, artifact_storage_service=env.objects)
        project = await env.projects.create(ProjectCreateRequest(project_key="parse-docx-browser", name="需求解析浏览器验收"))
        first = tmp_path / "订单需求.docx"
        actual_docx(first)
        second = tmp_path / "新版订单需求.docx"
        updated = Document(first)
        updated.tables[0].cell(1, 1).text = "9 天"
        updated.save(second)
        async with parsing_browser(env, service, store, configs, outbox, "p3-document-parse-browser-docx") as (page, evidence, errors, process_one):
            await page.get_by_role("button", name="打开导入", exact=True).click()
            await page.locator('.upload-modal input[type="file"]').set_input_files(first)
            await page.locator(".upload-form-grid label").filter(has_text="所属项目（建议填写）").locator(".n-base-selection").click()
            await page.get_by_text(project.name, exact=True).last.click()
            await page.get_by_role("button", name="开始导入", exact=True).click()
            await expect(page.locator(".upload-modal")).not_to_be_visible()
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await expect(page.get_by_role("button", name="提交解析", exact=True)).to_be_enabled()
            captured = []
            async def lose_response(route):
                response = await route.fetch()
                assert response.status == 202, await response.text()
                captured.append(await response.json())
                await route.abort("failed")
            await page.route("**/documents/*/parse-tasks", lose_response)
            await page.get_by_role("button", name="提交解析", exact=True).click()
            await expect(page.locator(".parse-submission")).to_contain_text("提交结果尚未确认")
            await expect(page.get_by_role("button", name="重试原解析提交", exact=True)).to_be_enabled()
            assert len(captured) == 1
            first_task = captured[0]
            await page.unroute("**/documents/*/parse-tasks")
            await page.reload()
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await expect(page.locator(".parse-task-status")).to_contain_text("等待解析")
            await process_one()
            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("7 天", timeout=15000)
            saved_first = await service.get(project.id, first_task["document_id"], first_task["id"])
            assert saved_first.status == "completed" and saved_first.attempt_no == 1
            assert len((await service.list_tasks(project.id, first_task["document_id"])).items) == 1
            old_snapshot, old_result = await service.read_snapshot(project.id, first_task["document_id"], saved_first.snapshot_id)
            assert old_result.tables[0].rows[1][1].text == "7 天"
            await page.get_by_text("结构与来源位置", exact=True).click()
            await expect(page.locator(".parse-structure")).to_contain_text("正文/表格位置")
            async with page.expect_download() as downloading:
                await page.get_by_role("button", name="下载完整解析结果 JSON", exact=True).click()
            downloaded = tmp_path / "downloaded-result.json"
            await (await downloading.value).save_as(downloaded)
            download_body = json.loads(downloaded.read_text(encoding="utf-8"))
            assert download_body["snapshot"]["id"] == old_snapshot.id and download_body["result"]["text"] == old_result.text
            assert ".docx" in await page.get_by_label("选择新版本文件", exact=True).get_attribute("accept")
            await page.get_by_label("选择新版本文件", exact=True).set_input_files(second)
            await page.get_by_role("button", name="上传新版本", exact=True).click()
            await expect(page.locator(".document-versions [role='status']")).to_contain_text("已保存版本 V2")
            await expect(page.get_by_role("button", name="提交解析", exact=True)).to_be_enabled()
            await submit_and_process(page, process_one)
            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("9 天", timeout=15000)
            current = await env.docs.get_document(first_task["document_id"])
            assert current.version_number == 2
            new_default = await env.docs.read_document_content(current.id)
            assert "9 天" in new_default["content"]
            assert (await service.read_snapshot(project.id, current.id, old_snapshot.id))[1] == old_result
            await page.get_by_label("解析原件版本", exact=True).select_option(old_snapshot.seed.version_id)
            await page.get_by_role("button", name=f"查看解析快照 {old_snapshot.id}", exact=True).click()
            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("7 天")
            assert not errors, errors
            originals = evidence / "p3-document-parse-browser-files"
            originals.mkdir(exist_ok=True)
            (originals / "requirements-v1.docx").write_bytes(first.read_bytes())
            (originals / "requirements-v2.docx").write_bytes(second.read_bytes())
            (evidence / "p3-document-parse-browser-docx.json").write_text(json.dumps({
                "lost_response_recovered_same_request": True, "first_task": saved_first.model_dump(mode="json"),
                "old_snapshot": old_snapshot.model_dump(mode="json"), "new_default": new_default["parse_snapshot"],
                "source_versions": [old_snapshot.seed.version_id, current.current_version_id],
                "historical_table_7_new_table_9": True, "json_download_exact_result": True,
                "page_errors": errors, "aria_snapshot": await page.locator(".document-parsing").aria_snapshot(),
                "scope": "shipped ApiDocsPlugin in isolated host; production routes, actual PG/MySQL/RustFS/Redis and parser subprocess; no full product startup",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.locator(".document-parsing").screenshot(path=str(evidence / "p3-document-parse-browser-docx.png"))


async def test_browser_pdf_mysql_settings_new_snapshot_and_actual_failure_diagnostics(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, configs, outbox):
        env.docs._upload_security_service = UploadSecurityService(settings=env.settings, artifact_storage_service=env.objects)
        project = await env.projects.create(ProjectCreateRequest(project_key="parse-pdf-browser", name="PDF 解析设置验收"))
        files = [("正常 PDF", actual_pdf(tmp_path / "正常.pdf")), ("加密 PDF", actual_pdf(tmp_path / "加密.pdf", encrypted=True))]
        docs = [await env.docs.upload_document(filename=f"{title}.pdf", title=title, project_id=project.id,
            request_id=uuid4().hex, content_base64=base64.b64encode(content).decode()) for title, content in files]
        async with parsing_browser(env, service, store, configs, outbox, "p3-document-parse-browser-pdf") as (page, evidence, errors, process_one):
            await page.locator(".api-doc-card").filter(has_text="正常 PDF").locator(".api-doc-actions button").first.click()
            await expect(page.get_by_role("button", name="提交解析", exact=True)).to_be_enabled()
            first = await submit_and_process(page, process_one)
            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("7 days", timeout=15000)
            old = await service.get(project.id, docs[0].id, first["id"])
            old_snapshot, old_result = await service.read_snapshot(project.id, docs[0].id, old.snapshot_id)
            assert old_result.options.pdf_extraction_mode == "plain"
            await page.get_by_text("项目解析设置", exact=True).click()
            await page.get_by_label("PDF 文本提取", exact=True).select_option("layout")
            await page.get_by_role("button", name="保存项目解析设置", exact=True).click()
            await expect(page.locator(".document-parse-settings [role='status']")).to_contain_text("项目解析设置已保存")
            live_config = await configs.get_settings(project.id)
            assert live_config.scope_key == project.id and live_config.payload.options.pdf_extraction_mode == "layout"
            await submit_and_process(page, process_one)
            await expect(page.locator(".document-parse-snapshot")).not_to_contain_text(f"快照：{old_snapshot.id}", timeout=15000)
            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("7 days", timeout=15000)
            current, result = await service.latest_snapshot(project.id, docs[0].id, docs[0].current_version_id)
            assert current.id != old_snapshot.id and result.options.pdf_extraction_mode == "layout"
            assert (await service.read_snapshot(project.id, docs[0].id, old_snapshot.id))[1] == old_result
            await page.get_by_role("button", name=f"查看解析快照 {old_snapshot.id}", exact=True).click()
            await expect(page.locator(".document-parse-snapshot")).to_contain_text(f"快照：{old_snapshot.id}")
            await page.locator(".api-doc-preview-modal .tools-modal-head-actions .icon-btn").click()
            await page.locator(".api-doc-card").filter(has_text="加密 PDF").locator(".api-doc-actions button").first.click()
            await expect(page.get_by_role("button", name="提交解析", exact=True)).to_be_enabled()
            failed = await submit_and_process(page, process_one, failed=True)
            await expect(page.locator(".parse-task-status")).to_contain_text("解析任务失败", timeout=15000)
            await page.get_by_role("button", name="查看解析失败诊断", exact=True).click()
            await expect(page.locator(".parse-failure")).to_contain_text("加密 PDF")
            await expect(page.locator(".parse-failure")).to_contain_text("空输出")
            assert (await service.list_snapshots(project.id, docs[1].id)).items == []
            unparsed = await env.docs.read_document_content(docs[1].id)
            assert unparsed["read_error"] and unparsed["content"] == ""
            assert not errors, errors
            originals = evidence / "p3-document-parse-browser-files"
            originals.mkdir(exist_ok=True)
            (originals / "requirements.pdf").write_bytes(files[0][1])
            (originals / "encrypted.pdf").write_bytes(files[1][1])
            (evidence / "p3-document-parse-browser-pdf.json").write_text(json.dumps({
                "mysql_config": live_config.model_dump(mode="json"), "old_snapshot": old_snapshot.model_dump(mode="json"),
                "new_snapshot": current.model_dump(mode="json"), "failed_task": (await service.get(project.id, docs[1].id, failed["id"])).model_dump(mode="json"),
                "old_snapshot_unchanged": True, "failure_diagnostics_actual": True, "no_binary_text_fallback": True,
                "page_errors": errors, "aria_snapshot": await page.locator(".document-parsing").aria_snapshot(),
                "scope": "shipped ApiDocsPlugin isolated host; actual stores/Redis/Worker/parser; no full product startup",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.locator(".document-parsing").screenshot(path=str(evidence / "p3-document-parse-browser-pdf.png"))


async def test_browser_markdown_source_is_escaped_and_archived_project_keeps_history_readable(tmp_path):
    async with parsing_environment(tmp_path) as (env, service, store, configs, outbox):
        env.docs._upload_security_service = UploadSecurityService(settings=env.settings, artifact_storage_service=env.objects)
        project = await env.projects.create(ProjectCreateRequest(project_key="parse-md-browser", name="Markdown 历史验收"))
        unsafe = '# 订单查询\r\n<script>window.parseFixtureExecuted = true</script>\r\n'.encode("utf-8-sig")
        with pytest.raises(UploadSecurityError, match="脚本标签"):
            await env.docs.upload_document(filename="unsafe.md", project_id=project.id, request_id=uuid4().hex,
                content_base64=base64.b64encode(unsafe).decode())
        raw = '# 订单查询\r\n有效期：7 天。\r\n<em>规则原文</em>\r\n'.encode("utf-8-sig")
        document = await env.docs.upload_document(filename="requirements.md", project_id=project.id, request_id=uuid4().hex,
            content_base64=base64.b64encode(raw).decode())
        async with parsing_browser(env, service, store, configs, outbox, "p3-document-parse-browser-markdown") as (page, evidence, errors, process_one):
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await expect(page.get_by_role("button", name="提交解析", exact=True)).to_be_enabled()
            submitted = await submit_and_process(page, process_one)
            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("7 天", timeout=15000)
            assert await page.evaluate("window.parseFixtureExecuted === undefined")
            completed = await service.get(project.id, document.id, submitted["id"])
            snapshot, result = await service.read_snapshot(project.id, document.id, completed.snapshot_id)
            assert result.text == raw.decode("utf-8-sig") and result.blocks[0].source.start_line == 1
            await env.projects.archive(project.id)
            await page.reload()
            await page.locator(".api-doc-card .api-doc-actions button").first.click()
            await expect(page.get_by_role("button", name=f"查看解析快照 {snapshot.id}", exact=True)).to_be_visible()
            assert await page.get_by_role("button", name="提交解析", exact=True).count() == 0
            assert await page.get_by_role("button", name="保存项目解析设置", exact=True).count() == 0
            await page.get_by_role("button", name=f"查看解析快照 {snapshot.id}", exact=True).click()
            await expect(page.locator(".document-parse-snapshot .parse-text")).to_contain_text("<em>")
            assert await page.locator(".document-parse-snapshot .parse-text em").count() == 0
            assert await page.evaluate("window.parseFixtureExecuted === undefined")
            base_url = page.url.split("/__doc_probe_")[0]
            rejected = await page.request.post(base_url + f"/api/v1/projects/{project.id}/documents/{document.id}/parse-tasks",
                data={"creation_key": "archived-new", "version_id": document.current_version_id})
            assert rejected.status == 409
            originals = evidence / "p3-document-parse-browser-files"
            originals.mkdir(exist_ok=True)
            (originals / "requirements.md").write_bytes(raw)
            assert not errors, errors
            (evidence / "p3-document-parse-browser-markdown.json").write_text(json.dumps({
                "snapshot": snapshot.model_dump(mode="json"), "preserved_bom_crlf_source": True,
                "archived_history_readable": True, "archived_new_parse_rejected": rejected.status,
                "source_text_not_executed": True, "embedded_script_upload_rejected": True, "page_errors": errors,
                "aria_snapshot": await page.locator(".document-parsing").aria_snapshot(),
                "scope": "actual shipped plugin and stores in isolated host; no full product startup",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            await page.locator(".document-parsing").screenshot(path=str(evidence / "p3-document-parse-browser-markdown.png"))
