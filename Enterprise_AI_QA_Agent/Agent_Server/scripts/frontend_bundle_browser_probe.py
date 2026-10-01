"""Verify production chunks in Chromium with isolated successful API fixtures.

This is a renderer/loading regression, not a real provider or backend E2E test.
Run a Vite preview first, then pass its URL and a JSON report output path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Route, async_playwright

MARKDOWN = "# Bundle regression\n\n$$x^2 + y^2 = z^2$$\n\n```python\nprint('loaded')\n```"
SESSION_ID = "bundle-renderer-probe"


async def scenario(browser, base_url: str, *, conversation: bool) -> dict:
    context = await browser.new_context(service_workers="block")
    page = await context.new_page()
    errors: list[str] = []
    unexpected_api: list[str] = []
    scripts: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: scripts.append(urlparse(request.url).path)
            if request.resource_type == "script" else None)
    now = datetime.now(UTC).isoformat()
    session = {
        "id": SESSION_ID, "title": "Bundle renderer probe", "status": "idle",
        "session_mode": "normal", "runtime_mode": "interactive", "mode_key": "default",
        "created_at": now, "updated_at": now, "messages": [],
        "event_count": 0, "snapshot_count": 0, "pending_approvals": [],
        "control_state": "idle", "is_resumable": False, "is_interrupted": False,
        "replay_available": False, "verification_results": [], "metadata": {},
    }
    if conversation:
        session["messages"] = [{
            "id": "bundle-answer", "role": "assistant", "content": MARKDOWN,
            "created_at": now, "metadata": {"delivery_status": "completed"},
        }]
    settings: dict = {}

    async def api_fixture(route: Route) -> None:
        nonlocal settings
        path = urlparse(route.request.url).path
        if path == "/api/v1/settings/general":
            if route.request.method == "PUT":
                settings = route.request.post_data_json
            payload = settings
        elif path == "/api/v1/health":
            payload = {"status": "ok", "name": "isolated renderer fixture"}
        elif path in {
            "/api/v1/registry/agents", "/api/v1/registry/tools/summary",
            "/api/v1/registry/modes", "/api/v1/settings/models",
            "/api/v1/sponsors",
            "/api/v1/recordings",
        } or path.endswith(("/tool-jobs", "/artifacts", "/events/history")):
            payload = []
        elif path in {"/api/v1/sessions", f"/api/v1/sessions/{SESSION_ID}"}:
            payload = session
        elif path.endswith("/verifications"):
            payload = {"session_id": SESSION_ID, "verification_results": [], "metadata": {}}
        elif path.endswith("/events"):
            await route.fulfill(content_type="text/event-stream", body=": ready\n\n")
            return
        else:
            unexpected_api.append(f"{route.request.method} {path}")
            await route.fulfill(status=501, json={"detail": "Missing renderer test fixture"})
            return
        await route.fulfill(json=payload)

    await page.route("**/api/v1/**", api_fixture)
    try:
        await page.goto(f"{base_url}/home")
        if conversation:
            await page.locator(".assistant-markdown .katex").first.wait_for()
            assert await page.locator(".assistant-markdown pre.hljs code").inner_text() == "print('loaded')\n"
            assert await page.locator(".assistant-markdown .hljs-keyword, .assistant-markdown .hljs-built_in").count() > 0
            assert any("ConversationWorkbenchPlugin-" in name for name in scripts)
            assert any("vendor-math-" in name for name in scripts)
            assert any("vendor-highlight-" in name for name in scripts)
            initial_scripts = list(scripts)
        else:
            await page.locator(".home-title").wait_for()
            await page.wait_for_function("document.querySelector('.log-badge')?.textContent.trim() === 'idle'")
            initial_scripts = list(scripts)
            assert not any("vendor-math-" in name or "vendor-highlight-" in name
                           or "ConversationWorkbenchPlugin-" in name for name in scripts), scripts
            assert not any("fr-FR-" in name or "ja-JP-" in name for name in scripts)
            await page.locator('a[href="/settings"]').click()
            await page.locator(".language-setting-container button").click()
            await page.get_by_text("Français", exact=True).click()
            await page.locator(".general-footer .primary").click()
            await page.get_by_role("heading", name="Paramètres généraux", exact=True).wait_for()
            assert sum("fr-FR-" in name for name in scripts) == 1
            await page.locator('a[href="/home"]').click()
            await page.locator(".home-title").wait_for()
            assert await page.locator('a[href="/home"]').get_attribute("title") == "Accueil"
            await page.reload()
            await page.wait_for_function("document.querySelector('a[href=\"/home\"]')?.title === 'Accueil'")
        assert not errors, errors
        assert not unexpected_api, unexpected_api
        return {"conversation": conversation, "initialScripts": initial_scripts,
                "allScripts": scripts, "pageErrors": errors, "unexpectedAPI": unexpected_api}
    finally:
        await context.close()


async def run(base_url: str) -> dict:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            results = [await scenario(browser, base_url, conversation=value) for value in (False, True)]
            return {"generatedAt": datetime.now(UTC).isoformat(), "browser": browser.version,
                    "scope": "Production renderer with API fixtures; no real model/provider/backend.",
                    "scenarios": results}
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url.rstrip("/")))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"browser": report["browser"], "scenariosPassed": len(report["scenarios"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
