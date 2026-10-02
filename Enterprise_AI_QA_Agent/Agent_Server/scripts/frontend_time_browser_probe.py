"""Check real SessionService HTTP/SSE times in the built renderer.

Auxiliary catalogs/tool-job lists are empty successful fixtures. Session creation,
history and SSE run their normal implementations against an isolated memory store.
No external model, provider or user data is accessed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import socket
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from playwright.async_api import async_playwright

from src.api.routes.sessions import router
from src.application.sessions.session_service import SessionService
from src.core.config import get_settings
from src.registry.modes import ModeRegistry
from src.runtime.store import InMemorySessionStore

LOGGER = logging.getLogger(__name__)


async def run(dist: Path) -> dict:
    store = InMemorySessionStore()
    await store.initialize()
    modes = ModeRegistry()
    service = SessionService(store, object(), object(), modes)
    app = FastAPI(docs_url=None, redoc_url=None)
    app.state.session_service = service
    app.state.settings = get_settings().model_copy(deep=True)

    async def empty_list(**kwargs):
        return []

    app.state.tool_job_service = SimpleNamespace(list_jobs=empty_list, list_artifacts=empty_list)

    @app.get("/api/v1/registry/agents")
    @app.get("/api/v1/registry/tools/summary")
    @app.get("/api/v1/sponsors")
    @app.get("/api/v1/recordings")
    async def catalog_fixture():
        return []

    @app.get("/api/v1/registry/modes")
    async def list_modes():
        return modes.list()

    @app.get("/api/v1/settings/general")
    async def settings_fixture():
        return {}

    @app.get("/api/v1/health")
    async def health_fixture():
        return {"status": "ok", "name": "isolated UTC probe"}

    app.include_router(router, prefix="/api/v1")

    @app.get("/{path:path}")
    async def static_file(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "No probe fixture for this auxiliary API")
        candidate = (dist / path).resolve()
        if not candidate.is_relative_to(dist):
            raise HTTPException(400, "Invalid static path")
        return FileResponse(candidate if candidate.is_file() else dist / "index.html")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    results = []
    loop_errors = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def record_loop_error(current_loop, context):
        error = context.get("exception")
        loop_errors.append({"error_type": type(error).__name__, "message": str(error)})
        if previous_handler is None:
            current_loop.default_exception_handler(context)
        else:
            previous_handler(current_loop, context)

    loop.set_exception_handler(record_loop_error)
    try:
        for _ in range(100):
            if server.started:
                break
            if serving.done():
                await serving
                raise RuntimeError("Probe HTTP server stopped during startup")
            await asyncio.sleep(0.05)
        if not server.started:
            raise RuntimeError("Probe HTTP server did not start")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                for timezone in ("UTC", "Asia/Shanghai", "America/New_York"):
                    context = await browser.new_context(timezone_id=timezone, locale="zh-CN")
                    page = await context.new_page()
                    errors = []
                    failures = []
                    page.on("pageerror", lambda error, error_list=errors: error_list.append(str(error)))
                    page.on("response", lambda response, failed_responses=failures: failed_responses.append(
                        {"status": response.status, "url": response.url}
                    ) if response.status >= 400 else None)
                    try:
                        async with page.expect_response(lambda response: (
                            response.request.method == "POST" and response.url.endswith("/api/v1/sessions")
                        )) as created_response:
                            response = await page.goto(f"http://127.0.0.1:{port}/home")
                        assert response and response.status == 200
                        created = await created_response.value
                        assert created.status == 200
                        session_id = (await created.json())["id"]
                        # Wait for initial navigation. Clicking the whole header
                        # can hit a tab with stopPropagation instead of expanding.
                        await page.locator(".home-title").wait_for()
                        await page.locator(".log-panel-toggle").click()
                        await page.locator(".log-panel.expanded .log-panel-body").wait_for()
                        log = page.locator(".runtime-console-raw-log")
                        await page.wait_for_function(
                            "document.querySelector('.runtime-console-raw-log')?.textContent.includes('session.created')"
                        )
                        history_response = await page.request.get(
                            f"http://127.0.0.1:{port}/api/v1/sessions/{session_id}/events/history"
                        )
                        assert history_response.status == 200
                        event = (await history_response.json())[0]
                        timestamp = datetime.fromisoformat(event["timestamp"])
                        if timestamp.tzinfo is None:
                            timestamp = timestamp.replace(tzinfo=UTC)
                        epoch_ms = int(timestamp.timestamp() * 1000)
                        expected = await page.evaluate(
                            "epoch => new Date(epoch).toLocaleTimeString('zh-CN', {hour12:false})", epoch_ms
                        )
                        text = await log.inner_text()
                        assert f"[{expected}] session.created" in text, text
                        assert not errors, errors
                        assert not failures, failures
                        results.append({"timezone": timezone, "timestamp": event["timestamp"],
                                        "expected_local_time": expected, "page_errors": errors,
                                        "failed_http_responses": failures})
                        LOGGER.info("frontend_utc_probe_passed", extra={"timezone": timezone})
                    except Exception as exc:
                        print(json.dumps({"timezone": timezone, "error_type": type(exc).__name__,
                                          "page_errors": errors, "failed_http_responses": failures,
                                          "body": (await page.locator("body").inner_text())[:4000]},
                                         ensure_ascii=False))
                        raise
                    finally:
                        await context.close()
                version = browser.version
            finally:
                await browser.close()
    finally:
        try:
            server.should_exit = True
            await asyncio.wait_for(serving, 10)
            listener.close()
            await service.close()
        finally:
            loop.set_exception_handler(previous_handler)
    return {"generated_at": datetime.now(UTC).isoformat(), "browser": version,
            "status": "partial" if loop_errors else "passed", "loop_errors": loop_errors,
            "release_ready": False,
            "scope": "Real SessionService, memory store, HTTP and SSE, production renderer; auxiliary catalogs are fixtures; no model or external provider.",
            "index_sha256": hashlib.sha256((dist / "index.html").read_bytes()).hexdigest(),
            "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("../agent_web/dist"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dist = args.dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error("Build the renderer with npm run build before this probe")
    report = asyncio.run(run(dist))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"browser": report["browser"], "display_checks_passed": len(report["results"]),
                      "status": report["status"], "loop_error_count": len(report["loop_errors"])}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
