"""Real public DeepSeek page recording quality and omission evidence."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from playwright.async_api import async_playwright

from src.infrastructure.memgraph_runtime import MemgraphRuntimeProvider
from src.infrastructure.postgres_runtime import postgres_connect
from test_execution_worker_cli import free_port, isolated_runtime, wait_api


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_DEEPSEEK_RECORDING") != "1",
        reason="real Chromium, DeepSeek public page and isolated recording stores",
    ),
]


async def test_actual_deepseek_manual_actions_are_recorded_and_diagnosed(tmp_path):
    evidence = Path(os.environ["DEEPSEEK_RECORDING_EVIDENCE"])
    evidence.mkdir(parents=True, exist_ok=False)
    graph = None
    project_id = None
    recording_id = None

    async with isolated_runtime(tmp_path) as (settings, processes, _, _, _):
        graph = MemgraphRuntimeProvider(settings)
        api_port = free_port()
        api = processes.start(
            "deepseek-recording-api",
            "-m",
            "uvicorn",
            "src.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
        )
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=90
        ) as client:
            await wait_api(client, api)
            project_response = await client.post(
                "/projects",
                json={
                    "project_key": "deepseek-recording-" + uuid4().hex[:12],
                    "name": "DeepSeek 人工录制验收",
                },
            )
            project_response.raise_for_status()
            project_id = project_response.json()["id"]

            async with async_playwright() as playwright:
                cdp_port = free_port()
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=[f"--remote-debugging-port={cdp_port}"],
                )
                try:
                    page = await browser.new_page(viewport={"width": 1440, "height": 900})
                    page_responses = []
                    page.on("response", lambda response: page_responses.append(response))
                    created = await client.post(
                        "/recordings",
                        json={
                            "project_id": project_id,
                            "name": "DeepSeek 首页人工操作",
                            "entry_url": "https://www.deepseek.com/",
                            "driver": {
                                "kind": "cdp-attach",
                                "endpoint": f"http://127.0.0.1:{cdp_port}",
                            },
                        },
                    )
                    created.raise_for_status()
                    recording_id = created.json()["id"]
                    async with asyncio.timeout(20):
                        while True:
                            state = (await client.get(f"/recordings/{recording_id}")).json()
                            if state["status"] == "ready":
                                break
                            await asyncio.sleep(0.1)
                    (await client.post(f"/recordings/{recording_id}/control", json={"action": "start"})).raise_for_status()

                    response = next((item for item in page_responses
                        if item.url == "https://www.deepseek.com/"), None)
                    await page.wait_for_timeout(4000)
                    thought = page.get_by_role("button", name="深度思考", exact=True)
                    search = page.get_by_role("button", name="智能搜索", exact=True)
                    facts = {
                        "http_status": response.status if response else None,
                        "title": await page.title(),
                        "url": page.url,
                        "deep_thought_button_count": await thought.count(),
                        "search_button_count": await search.count(),
                    }
                    (evidence / "page-facts.json").write_text(
                        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    assert facts["http_status"] == 200
                    assert facts["deep_thought_button_count"] == 1
                    assert facts["search_button_count"] == 1

                    # These are browser operations against the public page; no login,
                    # prompt submission, or account data is used.
                    await thought.click()
                    await thought.click()
                    await search.click()
                    await page.mouse.wheel(0, 700)
                    await page.wait_for_timeout(1200)
                    await page.screenshot(path=str(evidence / "deepseek-page.png"), full_page=True)
                    stopped = await client.post(
                        f"/recordings/{recording_id}/control", json={"action": "stop"}
                    )
                    stopped.raise_for_status()
                    detail = stopped.json()
                finally:
                    await browser.close()
            quality = detail["finalize_metrics"]["recording_quality"]
            with postgres_connect(settings) as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT seq, type, payload FROM {settings.database.postgres_recording_event_table} "
                    "WHERE recording_id=%s ORDER BY seq",
                    (recording_id,),
                )
                rows = [dict(row) for row in cur.fetchall()]
            (evidence / "recording.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            (evidence / "events.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            (evidence / "quality.json").write_text(
                json.dumps(quality, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )

            assert quality["protocol"] == "recording-quality-v1"
            assert quality["omission"]["status"] == "verified"
            assert quality["duplicate_candidates"]
            assert quality["removed_event_sequences"] == []
            assert any(event["type"] == "click" for event in rows)
            assert any(item["type"] in {"page_scan", "page_response"} for item in rows)
            assert quality["response_action_associations"]
            assert quality["unresolved_response_associations"] == []
            action_types = {"click", "dblclick", "fill", "key", "submit", "scroll", "navigate", "file_change"}
            actions = [event for event in rows if event["type"] in action_types]
            action_sequences = {event["seq"] for event in actions}
            action_signature = []
            for event in actions:
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
                locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
                role_name = locators.get("role_name") if isinstance(locators.get("role_name"), dict) else {}
                action_signature.append((event["type"], role_name.get("name") or locators.get("text") or ""))
            # Independent oracle: every scripted browser gesture must appear once,
            # in order, in the persisted user-action stream.
            assert action_signature == [
                ("click", "深度思考"),
                ("click", "深度思考"),
                ("click", "智能搜索"),
                ("scroll", ""),
            ]
            linked_sequences = {seq for link in quality["response_action_associations"]
                for seq in link["action_event_sequences"]}
            assert linked_sequences == action_sequences
            assert "Authorization" not in json.dumps(rows, ensure_ascii=False)
            assert "cookie" not in json.dumps(rows, ensure_ascii=False).lower()

        if graph and project_id:
            graph.execute_write(
                "MATCH (n {project_id:$project_id}) DETACH DELETE n",
                {"project_id": project_id},
            )
        if graph:
            graph.close()
