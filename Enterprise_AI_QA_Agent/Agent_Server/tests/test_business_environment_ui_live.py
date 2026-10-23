"""Browser acceptance for environment management using real MySQL and PostgreSQL stores."""
from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from urllib.parse import urlsplit

import httpx
import pytest
from playwright.async_api import async_playwright, expect

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.business_environment import TARGET_CONFIG_KEY
from test_business_environment import actual_environment


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_BUSINESS_ENVIRONMENT_UI") != "1",
    reason="actual browser UI and isolated MySQL/PostgreSQL environment stores")]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_for_vite(origin: str, process: subprocess.Popen) -> None:
    async with httpx.AsyncClient(timeout=1) as probe:
        for _ in range(300):
            if process.poll() is not None:
                raise AssertionError("Vite test server exited; inspect preserved stdout/stderr")
            try:
                response = await probe.get(origin)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise AssertionError("Vite test server did not become ready; inspect preserved stdout/stderr")


async def test_browser_registers_saves_captures_and_selects_project_snapshot(tmp_path):
    evidence = Path(os.getenv("QA_BUSINESS_ENVIRONMENT_UI_EVIDENCE_DIR", str(tmp_path / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    web_root = Path(__file__).resolve().parents[2] / "agent_web"
    node = shutil.which("node")
    assert node and (web_root / "node_modules/vite/bin/vite.js").is_file()
    errors: list[str] = []
    network: list[dict[str, object]] = []

    async with actual_environment(tmp_path) as (env, _parsing, _parse_store, store, _service, client, project, _other):
        port = free_port()
        origin = f"http://127.0.0.1:{port}"
        with (evidence / "vite.stdout").open("wb") as stdout, (evidence / "vite.stderr").open("wb") as stderr:
            process = subprocess.Popen([node, str(web_root / "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1",
                "--port", str(port), "--strictPort"], cwd=web_root,
                env={**os.environ, "VITE_API_PROXY_TARGET": "http://127.0.0.1:1", "API_AUTH_TOKEN": ""},
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            try:
                await wait_for_vite(origin, process)
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.launch()
                    try:
                        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
                        page.on("pageerror", lambda value: errors.append(str(value)))

                        async def forward_business_environment(route):
                            request = route.request
                            split = urlsplit(request.url)
                            path = split.path.removeprefix("/api/v1") + ("?" + split.query if split.query else "")
                            body = request.post_data_json if request.method in {"POST", "PUT", "PATCH"} else None
                            response = await client.request(request.method, path, json=body) if body is not None else await client.request(request.method, path)
                            network.append({"method": request.method, "path": path, "status": response.status_code})
                            await route.fulfill(status=response.status_code,
                                headers={"content-type": response.headers.get("content-type", "application/json")},
                                body=response.content)

                        await page.route("**/api/v1/projects/*/business-environments**", forward_business_environment)
                        await page.goto(f"{origin}/tests/fixtures/business-environment-ui.html?project_id={project.id}")
                        await page.get_by_label("登记环境标识").fill("browser-stage")
                        await page.get_by_role("button", name="登记项目环境").click()
                        await expect(page.get_by_role("heading", name="browser-stage · active")).to_be_visible()
                        await page.get_by_label("目标显示名称").fill("浏览器验收环境")
                        await page.get_by_label("目标地址").fill("https://staging.example.test/application")
                        await page.get_by_role("button", name="保存目标配置").click()
                        await expect(page.get_by_role("status").filter(has_text="目标配置已保存")).to_be_visible()
                        await page.get_by_role("button", name="创建配置快照").click()
                        await expect(page.get_by_role("status").filter(has_text="目标配置快照已保存")).to_be_visible()

                        registration_list = await client.get(f"/projects/{project.id}/business-environments", params={"status": "active"})
                        assert registration_list.status_code == 200, registration_list.text
                        environment = next(item for item in registration_list.json()["items"]
                            if item["request"]["environment_key"] == "browser-stage")
                        config_response = await client.get(f"/projects/{project.id}/business-environments/{environment['id']}/configuration")
                        assert config_response.status_code == 200, config_response.text
                        stored_mysql = env.configs.get(TARGET_CONFIG_KEY, scope_key=environment["id"])
                        assert stored_mysql.payload == {"display_name": "浏览器验收环境",
                            "base_url": "https://staging.example.test/application", "description": ""}
                        snapshot_page = await client.get(f"/projects/{project.id}/business-environments/{environment['id']}/snapshots")
                        assert snapshot_page.status_code == 200, snapshot_page.text
                        snapshot = snapshot_page.json()["items"][0]
                        assert snapshot["configuration"]["revision"] == 1
                        assert snapshot["deployment_status"] == "unknown"
                        with postgres_connect(env.settings) as connection, connection.cursor() as cursor:
                            cursor.execute(f"SELECT record FROM {store._snapshots} WHERE project_id=%s AND environment_id=%s AND id=%s",
                                (project.id, environment["id"], snapshot["id"]))
                            stored_snapshot = cursor.fetchone()
                        assert stored_snapshot and stored_snapshot["record"]["sha256"] == snapshot["sha256"]

                        await page.goto(f"{origin}/tests/fixtures/business-environment-ui.html?project_id={project.id}&mode=picker")
                        await page.get_by_label("项目环境").select_option(environment["id"])
                        radio = page.get_by_role("radio")
                        await expect(radio).to_have_count(1)
                        await radio.check()
                        selected = json.loads(await page.get_by_test_id("selected-environment").inner_text())
                        assert selected == {"environment_id": environment["id"], "snapshot_id": snapshot["id"],
                            "expected_snapshot_sha256": snapshot["sha256"]}
                        await expect(page.get_by_text("部署状态未知").first).to_be_visible()
                        await page.screenshot(path=str(evidence / "environment-picker.png"), full_page=True)
                        (evidence / "environment-picker.txt").write_text(await page.locator("body").inner_text(), encoding="utf-8")

                        await page.goto(f"{origin}/tests/fixtures/business-environment-ui.html?project_id={project.id}")
                        await page.get_by_role("button", name="browser-stage · active").click()
                        archive_button = page.get_by_role("button", name="归档环境", exact=True)
                        await expect(archive_button).to_be_enabled()
                        await archive_button.click()
                        await expect(page.get_by_role("heading", name="browser-stage · archived")).to_be_visible()
                        await expect(page.get_by_role("button", name="浏览器验收环境 · 修订 1")).to_be_visible()
                        archived_list = await client.get(f"/projects/{project.id}/business-environments", params={"status": "archived"})
                        assert archived_list.status_code == 200 and archived_list.json()["items"][0]["id"] == environment["id"]
                        await page.screenshot(path=str(evidence / "environment-manager-archived.png"), full_page=True)
                        verification = {
                            "project_id": project.id,
                            "environment_id": environment["id"],
                            "environment_status_after_archive": archived_list.json()["items"][0]["status"],
                            "configuration_storage": "MySQL application config",
                            "configuration_revision": config_response.json()["revision"],
                            "configuration_payload_readback": stored_mysql.payload,
                            "snapshot_storage": "PostgreSQL business environment snapshot",
                            "snapshot_id": snapshot["id"],
                            "snapshot_sha256": snapshot["sha256"],
                            "postgres_record_sha256_matches_api": stored_snapshot["record"]["sha256"] == snapshot["sha256"],
                            "deployment_status": snapshot["deployment_status"],
                            "picker_selection": selected,
                            "browser_errors": errors,
                            "api_requests": network,
                        }
                        (evidence / "verification.json").write_text(json.dumps(verification, ensure_ascii=False,
                            indent=2) + "\n", encoding="utf-8")
                    finally:
                        await browser.close()
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                (evidence / "page-errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
                (evidence / "network.json").write_text(json.dumps(network, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not errors, errors
