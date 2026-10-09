"""Shipped App/router, actual files, isolated production API and persistent stores."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx
from openpyxl import load_workbook
from playwright.async_api import expect
import pytest

from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from test_case_import_service import _xlsx
from test_document_full_product import product_browser
from test_execution_worker_cli import free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_CASE_IMPORT_FULL_PRODUCT") != "1",
    reason="built product App, independent API and isolated MySQL/PG/RustFS/Redis")]


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


async def test_product_templates_excel_json_review_restart_and_isolation(tmp_path):
    evidence = Path(os.getenv("QA_CASE_IMPORT_FULL_PRODUCT_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    for layout in ("rows", "split"):
        shutil.copyfile(_xlsx(tmp_path, layout, keys=("product-" + layout,)), inputs / (layout + ".xlsx"))
    workbook = load_workbook(inputs / "rows.xlsx")
    headers = [cell.value for cell in workbook["用例"][1]]
    valid_row = [cell.value for cell in workbook["用例"][2]]
    valid_row[headers.index("用例编号")] = "product-valid-with-error"
    workbook["用例"].cell(2, headers.index("用例编号") + 1).value = "product-invalid"
    workbook["用例"].cell(2, headers.index("标题") + 1).value = None
    workbook["用例"].append(valid_row)
    workbook.save(inputs / "invalid.xlsx")
    workbook.close()
    custom = {"objective": "保留完整项目业务结构", "steps": [{"action": "保持未登录", "expected": "拒绝"}],
        "test_data": {"count": 0, "flag": False, "nullable": None},
        "custom": {"nested": [False, 0, None, {"原字段": "原值"}]}}
    save_json(inputs / "cases.json", [{"case_key": "product-json", "title": "完整JSON导入",
        "content_json": custom, "execution_notes": "保持负向条件"}])
    facts = {"full_P0_P5_complete": False, "model_execution_requested": False, "imports": []}
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            api_port = free_port()
            api_args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(api_port))
            api_process = processes.start("product-import-api", *api_args)
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=50) as client:
                    await wait_api(client, api_process)
                    async with product_browser(tmp_path, api_port, evidence) as (page, errors, network):
                        await page.locator('.left-nav a[href="/projects"]').click()

                        async def create_project(name):
                            await page.locator('.left-nav a[href="/projects"]').click()
                            await page.locator(".projects-page .page-head button").filter(has_text="新建项目").click()
                            await page.get_by_label("项目标识", exact=True).fill("import-product-" + uuid4().hex[:10])
                            await page.get_by_label("项目名称", exact=True).fill(name)
                            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/projects")) as pending:
                                await page.locator(".editor").get_by_role("button", name="保存", exact=True).click()
                            response = await pending.value
                            assert response.status == 201, await response.text()
                            project = await response.json()
                            await expect(page.locator(".editor")).not_to_be_visible()
                            await page.locator(".project-card").filter(has_text=name).click()
                            await page.locator('.tabs[aria-label="项目测试资源"]').get_by_role("button", name="测试用例", exact=True).click()
                            return project

                        project = await create_project("完整导入项目一")
                        prefix = f"/projects/{project['id']}"
                        template_path = prefix + "/test-case-generation-template"
                        default = (await client.get(template_path)).json()
                        assert default["is_default"] and default["payload"]["example_json"]["steps"]
                        await page.get_by_role("button", name="用例生成模板", exact=True).click()
                        editor = page.get_by_role("dialog", name="用例生成模板", exact=True)
                        await expect(editor.get_by_label("模板名称", exact=True)).to_have_value(default["payload"]["name"])
                        await editor.get_by_label("模板名称", exact=True).fill("本项目生成模板")
                        await editor.get_by_label("AI 写作指导", exact=True).fill("依据本项目文档，保留负向场景和原字段")
                        await editor.get_by_label("随用例保存的执行说明", exact=True).fill("本用例执行说明")
                        await editor.get_by_label("JSON 结构与示例", exact=True).fill(json.dumps(custom, ensure_ascii=False))
                        async with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith(template_path)) as pending:
                            await editor.get_by_role("button", name="保存项目模板", exact=True).click()
                        response = await pending.value
                        assert response.status == 200, await response.text()
                        saved = await response.json()
                        assert not saved["is_default"] and saved["payload"]["example_json"] == custom
                        mysql = MySQLApplicationConfigStore(settings)
                        assert mysql.get("test_case.generation_template", scope_key=project["id"]).payload == saved["payload"]
                        await editor.get_by_role("button", name="载入系统默认内容", exact=True).click()
                        await expect(editor.get_by_label("模板名称", exact=True)).to_have_value(default["payload"]["name"])
                        assert (await client.get(template_path)).json() == saved, "loading defaults must not silently save"
                        await editor.get_by_role("button", name="关闭", exact=True).click()
                        facts["template_saved"] = saved

                        async def upload_preview(filename, layout, *, commit):
                            await page.get_by_role("button", name="导入 Excel / JSON", exact=True).click()
                            dialog = page.get_by_role("dialog", name="导入测试用例", exact=True)
                            await expect(dialog.get_by_label("Excel / JSON 文件", exact=True)).to_be_enabled()
                            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(prefix + "/test-case-imports/sources")) as pending:
                                await dialog.get_by_label("Excel / JSON 文件", exact=True).set_input_files(inputs / filename)
                            response = await pending.value
                            assert response.status == 201, await response.text()
                            source = await response.json()
                            owned.append(source["storage_uri"])
                            original = (inputs / filename).read_bytes()
                            assert (await objects.read_object_uri(source["storage_uri"]))["content"] == original
                            await expect(dialog.get_by_role("link", name="下载原件", exact=True)).to_be_visible()
                            async with page.expect_download() as pending_download:
                                await dialog.get_by_role("link", name="下载原件", exact=True).click()
                            assert Path(await (await pending_download.value).path()).read_bytes() == original
                            if layout != "json":
                                await dialog.get_by_role("combobox", name="导入布局", exact=True).select_option(layout)
                            await dialog.get_by_role("combobox", name="测试模式", exact=True).select_option("api_testing")
                            async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/previews")) as pending:
                                await dialog.get_by_role("button", name="生成导入预览", exact=True).click()
                            response = await pending.value
                            assert response.status == 200, await response.text()
                            preview = await response.json()
                            batch = preview["batch"]
                            await expect(dialog.get_by_role("button", name="下载预览与错误报告", exact=True)).to_be_visible()
                            async with page.expect_download() as pending_download:
                                await dialog.get_by_role("button", name="下载预览与错误报告", exact=True).click()
                            assert json.loads(Path(await (await pending_download.value).path()).read_text(encoding="utf-8")) == preview
                            if commit:
                                assert not batch["preview"]["issues"] and not preview["conflicting_case_keys"]
                                async with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/test-case-imports/settings")) as pending:
                                    await dialog.get_by_role("button", name="记住项目导入格式", exact=True).click()
                                response = await pending.value
                                assert response.status == 200, await response.text()
                                configuration = await response.json()
                                assert mysql.get("test_case.import_settings", scope_key=project["id"]).payload == configuration["payload"]
                                async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/commit")) as pending:
                                    await dialog.get_by_role("button", name="提交草稿", exact=True).click()
                                response = await pending.value
                                assert response.status == 200, await response.text()
                                committed = await response.json()
                                assert committed["batch"]["status"] == "committed"
                                replay = await client.post(prefix + f"/test-case-imports/batches/{batch['id']}/commit")
                                assert replay.status_code == 200 and replay.json() == committed
                                facts["imports"].append({"source": source, "preview": preview, "committed": committed})
                            else:
                                await expect(dialog.get_by_role("button", name="提交草稿", exact=True)).to_be_disabled()
                                facts["imports"].append({"source": source, "preview": preview})
                            await dialog.get_by_role("button", name="关闭", exact=True).click()
                            return preview

                        rows = await upload_preview("rows.xlsx", "rows", commit=True)
                        split = await upload_preview("split.xlsx", "split", commit=True)
                        assert rows["batch"]["preview"]["candidates"][0]["draft"]["content_json"]["test_data"] == {"id": 0, "flag": False}
                        steps = split["batch"]["preview"]["candidates"][0]["draft"]["content_json"]["steps"]
                        assert [step["order"] for step in steps] == [1, 2]
                        imported = await upload_preview("cases.json", "json", commit=True)
                        assert imported["batch"]["preview"]["candidates"][0]["draft"]["content_json"] == custom
                        invalid = await upload_preview("invalid.xlsx", "rows", commit=False)
                        assert any(issue["severity"] == "error" and issue["row"] == 2 for issue in invalid["batch"]["preview"]["issues"])
                        assert len(invalid["batch"]["preview"]["candidates"]) == 1 and not invalid["conflicting_case_keys"]

                        record = (await client.get(prefix + "/test-cases")).json()
                        assert len(record["items"]) == 3 and not record["has_more"]
                        case = next(item for item in record["items"] if item["case_key"] == "product-json")
                        row = page.locator(".table-wrap tbody tr").filter(has_text="product-json")
                        await row.get_by_role("button", name="评审与启用", exact=True).click()
                        content = page.get_by_role("dialog", name="测试用例内容", exact=True)
                        await content.get_by_role("button", name="JSON 原文", exact=True).click()
                        await expect(content.locator("pre")).to_contain_text('"nullable": null')
                        assert json.loads(await content.locator("pre").inner_text()) == custom
                        async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/test-cases/{case['id']}/submit-review")) as pending:
                            await content.get_by_role("button", name="提交当前版本评审", exact=True).click()
                        response = await pending.value
                        assert response.status == 200, await response.text()
                        await expect(content.get_by_role("button", name="启用当前版本", exact=True)).to_be_enabled()
                        async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/test-cases/{case['id']}/activate")) as pending:
                            await content.get_by_role("button", name="启用当前版本", exact=True).click()
                        response = await pending.value
                        assert response.status == 200, await response.text()
                        await expect(content.get_by_text("当前版本：已确认 · 当前启用", exact=True)).to_be_visible()
                        await expect(content.get_by_role("button", name="启用当前版本", exact=True)).to_be_disabled()
                        active = (await client.get(f"/test-cases/{case['id']}")).json()
                        assert active["active_version_id"] and active["lifecycle_status"] == "active"
                        versions_before = (await client.get(f"/test-cases/{case['id']}/versions")).json()
                        revised = deepcopy(custom)
                        revised["custom"]["revision"] = 2
                        await content.get_by_role("button", name="编辑新版本", exact=True).click()
                        await content.get_by_label("完整 JSON", exact=True).fill(json.dumps(revised, ensure_ascii=False))
                        async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(f"/test-cases/{case['id']}/versions")) as pending:
                            await content.get_by_role("button", name="保存新草稿版本", exact=True).click()
                        response = await pending.value
                        assert response.status == 201, await response.text()
                        new_version = await response.json()
                        assert new_version["content_json"] == revised
                        after = (await client.get(f"/test-cases/{case['id']}")).json()
                        versions_after = (await client.get(f"/test-cases/{case['id']}/versions")).json()
                        assert after["active_version_id"] == active["active_version_id"]
                        assert all(version in versions_after for version in versions_before)
                        await content.get_by_role("button", name="关闭", exact=True).click()

                        # Changing generation guidance after activation must not
                        # rewrite either the enabled V1 or the new V2 draft.
                        template_before_change = saved
                        await page.get_by_role("button", name="用例生成模板", exact=True).click()
                        editor = page.get_by_role("dialog", name="用例生成模板", exact=True)
                        await expect(editor.get_by_label("AI 写作指导", exact=True)).to_have_value(saved["payload"]["writing_guidance"])
                        await editor.get_by_label("AI 写作指导", exact=True).fill("仅后续生成使用新指导；旧用例及启用指针不变")
                        async with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith(template_path)) as pending:
                            await editor.get_by_role("button", name="保存项目模板", exact=True).click()
                        response = await pending.value
                        assert response.status == 200, await response.text()
                        saved = await response.json()
                        assert saved["revision"] > template_before_change["revision"]
                        assert mysql.get("test_case.generation_template", scope_key=project["id"]).payload == saved["payload"]
                        assert (await client.get(f"/test-cases/{case['id']}")).json() == after
                        assert (await client.get(f"/test-cases/{case['id']}/versions")).json() == versions_after
                        facts["template_change_after_activation"] = {"before": template_before_change,
                            "after": saved, "case_unchanged": True, "versions_unchanged": True}
                        await editor.get_by_role("button", name="关闭", exact=True).click()

                        # Different options create a new preview, so conflict is exercised rather than committed-batch replay.
                        await page.get_by_role("button", name="导入 Excel / JSON", exact=True).click()
                        dialog = page.get_by_role("dialog", name="导入测试用例", exact=True)
                        await expect(dialog.get_by_label("Excel / JSON 文件", exact=True)).to_be_enabled()
                        await dialog.get_by_label("Excel / JSON 文件", exact=True).set_input_files(inputs / "rows.xlsx")
                        await expect(dialog.get_by_role("link", name="下载原件", exact=True)).to_be_visible()
                        await dialog.get_by_role("combobox", name="导入布局", exact=True).select_option("rows")
                        await dialog.get_by_role("combobox", name="测试模式", exact=True).select_option("ui_automation")
                        async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/previews")) as pending:
                            await dialog.get_by_role("button", name="生成导入预览", exact=True).click()
                        conflict = await (await pending.value).json()
                        assert conflict["conflicting_case_keys"] == ["product-rows"]
                        await expect(dialog.get_by_role("button", name="提交草稿", exact=True)).to_be_disabled()
                        await dialog.get_by_role("button", name="关闭", exact=True).click()

                        other = await create_project("完整导入项目二")
                        assert (await client.get(f"/projects/{other['id']}/test-cases")).json()["items"] == []
                        assert (await client.get(f"/projects/{other['id']}/test-case-generation-template")).json()["is_default"]
                        assert (await client.get(f"/projects/{other['id']}/test-case-imports/settings")).json()["scope_key"] == "global"
                        source_id = facts["imports"][0]["source"]["id"]
                        cross = await client.get(f"/projects/{other['id']}/test-case-imports/sources/{source_id}")
                        assert cross.status_code == 404
                        restart_start = len(network)
                        await processes.stop(api_process)
                        api_process = processes.start("product-import-api-restarted", *api_args)
                        await wait_api(client, api_process)
                        restart_end = len(network)
                        await page.reload()
                        await page.locator('.left-nav a[href="/projects"]').click()
                        await page.locator(".project-card").filter(has_text="完整导入项目一").click()
                        await expect(page.locator(".table-wrap tbody tr").filter(has_text="product-json")).to_be_visible()
                        assert (await client.get(template_path)).json() == saved
                        assert (await client.get(f"/test-cases/{case['id']}")).json() == after
                        persisted_page = (await client.get(prefix + "/test-cases")).json()
                        assert len(persisted_page["items"]) == 3 and not persisted_page["has_more"]
                        assert not errors
                        failures = [r for index, r in enumerate(network) if r["status"] >= 400
                            and "/api/" in r["url"] and not restart_start <= index < restart_end]
                        assert not failures, failures
                        assert not [r for r in network if "/generate" in r["url"] or "/invoke" in r["url"]]
                        assert any("/assets/" in r["url"] and r["status"] == 200 for r in network)
                        facts.update({"project": project, "other_project": other, "active_before": active,
                            "case_after_new_draft": after, "versions_before": versions_before,
                            "versions_after": versions_after, "conflict": conflict,
                            "cross_project_source_status": cross.status_code, "page_errors": errors,
                            "unplanned_api_errors": failures,
                            "planned_restart_response_failures": [r for r in network[restart_start:restart_end] if r["status"] >= 400],
                            "api_restart_persisted": True})
            finally:
                with postgres_connect(settings) as conn, conn.cursor() as cur:
                    tables = {}
                    for field in ("postgres_test_case_table", "postgres_test_case_version_table",
                        "postgres_case_import_source_table", "postgres_case_import_batch_table"):
                        table = getattr(settings.database, field)
                        cur.execute("SELECT to_regclass(%s) AS existing", (table,))
                        if cur.fetchone()["existing"]:
                            cur.execute(f"SELECT record FROM {table}")
                            records = [row["record"] for row in cur.fetchall()]
                            tables[field] = records
                            if field == "postgres_case_import_source_table":
                                for source in records:
                                    if source["storage_uri"] not in owned:
                                        owned.append(source["storage_uri"])
                    save_json(evidence / "postgres-finally.json", tables)
    finally:
        save_json(evidence / "facts.json", facts)
        for path in inputs.iterdir():
            shutil.copyfile(path, evidence / path.name)
        save_json(evidence / "original-hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs.iterdir()})
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name == "docker.json":
                shutil.copyfile(path, evidence / path.name)
