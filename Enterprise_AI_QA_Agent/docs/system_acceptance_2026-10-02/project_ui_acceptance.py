"""Exercise production project UI with real project/case services and isolated memory.

Not the full application: catalogs are empty fixtures; resource-monitor APIs are
unavailable. Those responses are recorded as harness gaps, never product defects.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Agent_Server"))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from playwright.async_api import async_playwright

from src.api.routes.case_management import router as cases_router
from src.api.routes.projects import router as projects_router
from src.application.projects.project_overview_service import (
    ProjectOverviewService,
)
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.application.test_cases.case_service import TestCaseService
from src.application.test_cases.case_store import InMemoryTestCaseStore
from src.registry.modes import ModeRegistry
from src.runtime.store import InMemorySessionStore

LOGGER = logging.getLogger(__name__)


async def run(output):
    output.mkdir(parents=True, exist_ok=False)
    dist = (ROOT / "agent_web/dist").resolve()
    if not (dist / "index.html").exists():
        raise FileNotFoundError("Run the formal frontend build first")
    projects = ProjectService(store=InMemoryProjectStore())
    await projects.initialize()
    sessions = InMemorySessionStore()
    await sessions.initialize()
    case_store = InMemoryTestCaseStore()
    await case_store.initialize()
    cases = TestCaseService(store=case_store, project_service=projects)

    async def zero_count(project_id):
        return 0

    app = FastAPI(docs_url=None, redoc_url=None)
    app.state.project_service = projects
    app.state.test_case_service = cases
    app.state.project_overview_service = ProjectOverviewService(
        project_service=projects, api_doc_store=SimpleNamespace(count_by_project=zero_count),
        session_store=sessions, knowledge_graph_service=object(), test_case_store=case_store)
    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(cases_router, prefix="/api/v1")
    modes = ModeRegistry()

    @app.get("/api/v1/registry/modes")
    async def mode_catalog():
        return modes.list()

    @app.get("/api/v1/sponsors")
    async def empty_sponsors():
        return []

    @app.get("/api/v1/settings/general")
    async def empty_settings():
        return {}

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "name": "isolated project acceptance"}

    @app.get("/{path:path}")
    async def files(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Not wired in isolated project acceptance")
        candidate = (dist / path).resolve()
        if not candidate.is_relative_to(dist):
            raise HTTPException(400, "Invalid static path")
        return FileResponse(candidate if candidate.is_file() else dist / "index.html")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=5))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    records, errors, unavailable = [], [], []
    phase = "startup"
    report = {"started_at": datetime.now(UTC).isoformat(), "feature_ids": ["B01", "H01"],
              "scope": "Built ProjectsView; actual HTTP project CRUD, overview and empty cases; memory stores; empty auxiliary fixtures; resource monitor not wired; no model, PostgreSQL or full application lifecycle.",
              "full_feature_accepted": False, "status": "running"}
    try:
        for _ in range(100):
            if server.started:
                break
            if serving.done():
                await serving
            await asyncio.sleep(.05)
        if not server.started:
            raise RuntimeError("Isolated HTTP server not ready")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: unavailable.append(
                    {"path": response.url.removeprefix(base), "status": response.status}
                ) if response.status >= 400 else None)
                phase = "project_catalog"
                await page.goto(base+"/projects")
                await page.get_by_role("heading", name="测试项目", exact=True).wait_for()
                # Preserve observed UI before interacting.
                (output / "initial-dom.txt").write_text(await page.locator("body").inner_text(), encoding="utf-8")
                await page.screenshot(path=str(output / "01-catalog.png"), full_page=True)
                await page.locator(".page-head button.primary").filter(has_text="新建项目").click()
                await page.get_by_placeholder("orders-api", exact=True).fill("acceptance-"+uuid4().hex[:8])
                await page.get_by_placeholder("订单服务", exact=True).fill("验收专用测试项目")
                phase = "project_create"
                async with page.expect_response(lambda r: r.request.method == "POST" and r.url == base+"/api/v1/projects") as response:
                    await page.get_by_role("button", name="保存", exact=True).click()
                created = await response.value
                assert created.status == 201
                project = await created.json()
                await page.locator(".project-card").filter(has_text="验收专用测试项目").wait_for()
                records.append({"case": phase, "http_status": 201, "result": "passed"})
                phase = "project_search"
                await page.get_by_placeholder("搜索项目名称或标识").fill("验收专用")
                async with page.expect_response(lambda r: "/api/v1/projects?" in r.url and "query=" in r.url) as response:
                    await page.get_by_role("button", name="查询", exact=True).click()
                assert (await response.value).status == 200
                assert await page.locator(".project-card").count() == 1
                records.append({"case": phase, "result": "passed"})
                phase = "project_detail"
                await page.locator(".project-card").click()
                await page.locator(".project-detail h2").wait_for()
                await page.screenshot(path=str(output / "02-detail.png"), full_page=True)
                assert "验收专用测试项目" in await page.locator(".project-detail h2").inner_text()
                phase = "project_update"
                await page.get_by_role("button", name="编辑", exact=True).click()
                await page.get_by_placeholder("订单服务", exact=True).fill("验收专用项目已更新")
                async with page.expect_response(lambda r: r.request.method == "PATCH" and r.url.endswith(project["id"])) as response:
                    await page.get_by_role("button", name="保存", exact=True).click()
                assert (await response.value).status == 200
                await page.wait_for_function("document.querySelector('.project-detail h2')?.textContent.includes('已更新')")
                records.append({"case": phase, "result": "passed"})
                phase = "project_archive"
                async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/archive")) as response:
                    await page.get_by_role("button", name="归档", exact=True).click()
                assert (await response.value).status == 200
                await page.wait_for_function("document.querySelector('.detail-head button.danger')?.disabled")
                saved = await page.request.get(base+f"/api/v1/projects/{project['id']}")
                assert (await saved.json())["status"] == "archived"
                await page.screenshot(path=str(output / "03-archived.png"), full_page=True)
                records.append({"case": phase, "result": "passed", "history_preserved": True})
                assert not errors, errors
                report["status"] = "passed_declared_crud_scope"
                report["browser"] = browser.version
            finally:
                await browser.close()
    except Exception as error:
        LOGGER.exception("project_ui_acceptance_failed", extra={"phase": phase})
        report.update(status="failed", failed_phase=phase, error_type=type(error).__name__, error=str(error))
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        listener.close()
        report.update(finished_at=datetime.now(UTC).isoformat(), cases=records, page_errors=errors,
                      harness_unavailable_http=unavailable,
                      limitation="Unavailable auxiliary resource APIs belong to this harness; do not infer production defects.")
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": report["status"], "cases": records}, ensure_ascii=False))
    return 0 if report["status"] == "passed_declared_crud_scope" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.output)))
