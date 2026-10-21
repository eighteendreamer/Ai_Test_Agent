"""Actual MySQL migration/API/restart and built App editing, with owned stores."""
import asyncio
import json
import os
from pathlib import Path
import shutil

import httpx
import pytest
from playwright.async_api import expect

from test_document_full_product import product_browser
from test_execution_worker_cli import isolated_runtime, free_port, wait_api
from test_model_completion_http import provider
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.models import ModelRegistry
from src.application.models.model_runtime_service import ModelRuntimeService
from src.schemas.settings import ModelConfigUpdateRequest
from src.schemas.model_config import ModelCapabilitiesOverride, ModelInvocationRequest, model_invocation_parameters

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_MODEL_REQUEST_PROFILE") != "1",
    reason="requires independent production API, built App and isolated MySQL/PG/Redis/RustFS")]


async def test_actual_mysql_profile_migration_editor_reload_and_restart(tmp_path):
    evidence = Path(os.environ["QA_MODEL_REQUEST_PROFILE_PRODUCT_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    def save(name, value):
        (evidence / (name+".json")).write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    try:
        with provider("openai_chat_completions", False) as (url, sends, _):
            async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
                store = MySQLModelConfigStore(settings)
                store.initialize()
                payload = {"model_name": "owned-profile-model", "provider": "deepseek", "transport": "openai_chat_completions",
                    "base_url": url, "is_active": True, "max_output_tokens": 2048}
                capability_overrides = ModelCapabilitiesOverride(tool_calling=False, streaming=False)
                store.upsert(ModelConfigUpdateRequest(**payload, api_key="local-fixture-marker",
                    use_provider_defaults=False, capability_overrides=capability_overrides))
                # Simulate the pre-upgrade nullable-column absence in this fixture's owned table only.
                table = settings.database.llm_model_table
                assert table.startswith("up_cli_llm_model_")
                with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"ALTER TABLE `{table}` DROP COLUMN `request_profile`")
                    conn.commit()
                store.initialize(); store.initialize()
                assert store.get_by_name(payload["model_name"]).request_profile is None
                with mysql_raw_connection(settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'request_profile'")
                    save("mysql-migration", cur.fetchone())
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("profile-api-first", *args)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=60) as client:
                    await wait_api(client, api)
                    profile = {"version": 1, "revision": 3, "output_formats": ["json_object"], "chat_token_parameter": "max_tokens",
                        "reasoning_style": "effort", "reasoning_efforts": ["low"], "default_reasoning": {"effort": "low"}}
                    async with product_browser(tmp_path, port, evidence) as (page, errors, network):
                        await page.locator('.left-nav a[href="/settings"]').click()
                        await page.locator(".settings-section-btn").filter(has_text="模型设置").click()
                        card = page.locator("article.settings-model-card").filter(has_text=payload["model_name"])
                        await card.locator(".settings-model-card__action-edit").click()
                        editor = page.locator(".settings-model-editor-modal")
                        textarea = editor.get_by_label("模型请求配置（JSON，可选）")
                        capability_textarea = editor.locator("label").filter(has_text="能力覆盖").locator("textarea")
                        await expect(textarea).to_have_value("")
                        assert json.loads(await capability_textarea.input_value()) == {"tool_calling": False, "streaming": False}
                        assert await editor.locator('input[placeholder="请选择或输入供应商"]').input_value() == "deepseek"
                        await capability_textarea.fill("[]")
                        await editor.locator(".settings-modal-actions .primary-btn").click()
                        await expect(page.get_by_text("能力覆盖必须是有效 JSON 对象，字段值只能是 true、false 或 null。", exact=True)).to_be_visible()
                        assert not any(item["method"] == "PATCH" for item in network)
                        await capability_textarea.fill(json.dumps({"tool_calling": False, "streaming": False}, ensure_ascii=False, indent=2))
                        await textarea.fill("[]")
                        await editor.locator(".settings-modal-actions .primary-btn").click()
                        await expect(page.get_by_text("请求配置必须是有效的 JSON 对象。", exact=True)).to_be_visible()
                        assert not any(item["method"] == "PATCH" for item in network)
                        await textarea.fill(json.dumps(profile, ensure_ascii=False, indent=2))
                        async with page.expect_response(lambda r: r.request.method == "PATCH" and "/settings/models/" in r.url) as saved:
                            await editor.locator(".settings-modal-actions .primary-btn").click()
                        response = await saved.value
                        assert response.status == 200, await response.text()
                        saved_model = await response.json()
                        saved_profile = saved_model["request_profile"]
                        assert saved_model["provider"] == "deepseek"
                        assert saved_model["capability_overrides"]["tool_calling"] is False
                        assert saved_model["capability_overrides"]["streaming"] is False
                        save("product-saved", {"request_profile": saved_profile,
                            "provider": saved_model["provider"], "capability_overrides": saved_model["capability_overrides"],
                            "capabilities": saved_model["capabilities"]})
                        await expect(editor).not_to_be_visible()
                        await page.reload()
                        await page.locator(".settings-section-btn").filter(has_text="模型设置").click()
                        await card.locator(".settings-model-card__action-edit").click()
                        assert json.loads(await textarea.input_value()) == saved_profile
                        assert json.loads(await capability_textarea.input_value()) == {"tool_calling": False, "streaming": False}
                        await page.screenshot(path=str(evidence / "profile-editor.png"), full_page=True)
                        assert not errors
                    frozen_record = MySQLModelConfigStore(settings).get_by_name(payload["model_name"])
                    assert frozen_record.provider == "deepseek"
                    assert frozen_record.capabilities.tool_calling is False
                    assert frozen_record.capabilities.streaming is False
                    assert frozen_record.capabilities.vision is False
                    assert frozen_record.capabilities.parallel_tool_calls is False
                    frozen = model_invocation_parameters(frozen_record)
                    runtime = ModelRuntimeService(ModelRegistry(MySQLModelConfigStore(settings)), settings)
                    request = ModelInvocationRequest(system_prompt="Return JSON.", messages=[{"role": "user", "content": "{}"}])
                    runtime_fixed = runtime.get_invocation_runtime_parameters(frozen_record.key)
                    result = await runtime.invoke(frozen_record.key, request, expected_parameters=frozen, expected_runtime_parameters=runtime_fixed)
                    assert result.response_summary["mode"] == "ok" and len(sends) == 1
                    assert sends[0]["body"]["max_tokens"] == 2048 and sends[0]["body"]["reasoning_effort"] == "low"
                    tool_request = ModelInvocationRequest(system_prompt="Do not call unsupported tools.",
                        messages=[{"role": "user", "content": "Inspect the order."}],
                        tools=[{"name": "inspect_order", "description": "Inspect one order",
                            "input_schema": {"type": "object", "properties": {}}}])
                    tool_result = await runtime.invoke(frozen_record.key, tool_request)
                    assert tool_result.response_summary["mode"] == "ok" and len(sends) == 2
                    assert "tools" not in sends[-1]["body"]
                    assert "parallel_tool_calls" not in sends[-1]["body"]
                    save("reloaded-actual-http", {"frozen": frozen, "runtime": runtime_fixed, "requests": sends,
                        "result": result.model_dump(mode="json"), "tool_request_result": tool_result.model_dump(mode="json")})
                    omitted = await client.patch("/settings/models/"+payload["model_name"], json=payload)
                    omitted.raise_for_status()
                    assert omitted.json()["request_profile"] == saved_profile
                    assert omitted.json()["capability_overrides"]["tool_calling"] is False
                    assert omitted.json()["capability_overrides"]["streaming"] is False
                    rejected = await client.patch("/settings/models/"+payload["model_name"], json={**payload, "request_profile": {"unknown": True}})
                    assert rejected.status_code == 422
                    incompatible = await client.patch("/settings/models/"+payload["model_name"], json={**payload,
                        "transport": "anthropic_messages", "request_profile": profile})
                    assert incompatible.status_code == 409
                    save("api-profile-errors", {"unknown_status": rejected.status_code, "transport_status": incompatible.status_code,
                        "transport_error": incompatible.json()})
                    await processes.stop(api)
                    api = processes.start("profile-api-restarted", *args)
                    await wait_api(client, api)
                    listing = await client.get("/settings/models"); listing.raise_for_status()
                    reloaded = next(item for item in listing.json() if item["name"] == payload["model_name"])
                    assert reloaded["request_profile"] == saved_profile
                    assert reloaded["capabilities"]["tool_calling"] is False
                    assert reloaded["capabilities"]["streaming"] is False
                    assert "api_key" not in reloaded
                    cleared = await client.patch("/settings/models/"+payload["model_name"], json={**payload, "request_profile": None})
                    cleared.raise_for_status()
                    assert MySQLModelConfigStore(settings).get_by_name(payload["model_name"]).request_profile is None
                    with pytest.raises(ValueError, match="模型参数已变化"):
                        await runtime.invoke(frozen_record.key, request, expected_parameters=frozen)
                    assert len(sends) == 2
                    save("restart-and-clear", {"reloaded": reloaded, "cleared": cleared.json(), "sends_after_rejected_resume": len(sends)})
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name in {"docker.json", "postgres-finally.json"}:
                shutil.copyfile(path, evidence / path.name)
