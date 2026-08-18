from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.exploration.ui_graph_store import UIGraphStore
from src.application.runtime.python_playwright_cli import PythonPlaywrightCliRuntime
from src.core.config import Settings

if TYPE_CHECKING:
    from src.application.runtime.tool_runtime_service import ToolExecutionContext


class UIExplorationService:
    """Build a semantic UI graph from ARIA snapshots.

    This service intentionally explores and models UI structure only. It does
    not generate tests, assertions, or verification verdicts.
    """

    def __init__(
        self,
        settings: Settings,
        mcp_runtime_service: MCPRuntimeService | None = None,
        memory_runtime_service: MemoryRuntimeService | None = None,
        ui_graph_store: UIGraphStore | None = None,
    ) -> None:
        self._settings = settings
        self._mcp_runtime_service = mcp_runtime_service
        self._memory_runtime_service = memory_runtime_service
        self._ui_graph_store = ui_graph_store
        self._playwright_runtime = PythonPlaywrightCliRuntime(settings)
        self._artifact_root = Path(__file__).resolve().parents[2] / settings.artifact_root_dir

    async def explore(self, arguments: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        target_url = str(arguments.get("target_url") or arguments.get("url") or "").strip()
        if not target_url:
            return {"status": "failed", "error": "ui-page-explorer requires target_url or url."}

        max_pages = int(arguments.get("max_pages") or 1)
        max_pages = max(1, min(max_pages, 12))
        max_interactions = int(arguments.get("max_interactions") or 0)
        max_interactions = max(0, min(max_interactions, 32))
        same_origin_only = bool(arguments.get("same_origin_only", True))
        login_credentials = arguments.get("login_credentials") if isinstance(arguments.get("login_credentials"), dict) else {}
        output_dir = self._artifact_root / self._slug(context.session_id) / self._slug(context.turn_id) / "ui-page-explorer"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = "ui_explorer_graph.json"
        args = [
            "explore",
            target_url,
            f"--max-pages={max_pages}",
            f"--max-interactions={max_interactions}",
            f"--same-origin-only={'true' if same_origin_only else 'false'}",
            f"--filename={filename}",
        ]
        if login_credentials:
            username = str(
                login_credentials.get("username")
                or login_credentials.get("email")
                or login_credentials.get("account")
                or ""
            )
            password = str(login_credentials.get("password") or "")
            if username:
                args.append(f"--login-username={username}")
            if password:
                args.append(f"--login-password={password}")
        if arguments.get("include_hidden"):
            args.append("--include-hidden")
        try:
            base_timeout = int(self._settings.browser_action_timeout_seconds or 15)
            per_page_budget = max(base_timeout, 8 + max_interactions * 4)
            command_result = await self._playwright_runtime.run(
                session_name=f"ui-explorer-{context.session_id}",
                args=args,
                cwd=output_dir,
                raw=True,
                timeout_seconds=max(45, max_pages * per_page_budget),
            )
        finally:
            await self._playwright_runtime.close_all()
        artifacts = list(command_result.get("artifacts") or [])
        if int(command_result.get("exit_code") or 0) != 0:
            return {
                "status": "failed",
                "ok": False,
                "summary": "UI Explorer failed before a semantic graph could be built.",
                "error": command_result.get("stderr") or "python_playwright_cli_failed",
                "artifacts": artifacts,
            }
        app_map = json.loads(str(command_result.get("stdout") or "{}"))
        app_map.setdefault("generated_at", datetime.utcnow().isoformat() + "Z")
        app_map["artifacts"] = artifacts
        graph_write = await self._write_graph(app_map, arguments, context)
        memory_write_ids = await self._write_page_memory(app_map, context)
        return {
            "status": "success",
            "summary": (
                f"UI Explorer built an ARIA semantic graph for {int(app_map.get('page_count') or 0)} "
                f"page(s) starting from {target_url}."
            ),
            "entry_url": target_url,
            "app_map": app_map,
            "semantic_graph": app_map.get("graph") or {},
            "graph_storage": graph_write,
            "memory_write_ids": memory_write_ids,
            "artifacts": artifacts,
            "metrics": {
                "page_count": int(app_map.get("page_count") or 0),
                "element_count": len((app_map.get("graph") or {}).get("elements") or []),
                "entity_count": len((app_map.get("graph") or {}).get("entities") or []),
                "edge_count": len((app_map.get("graph") or {}).get("edges") or []),
                "login_event_count": len(app_map.get("login_events") or []),
                "interaction_count": sum(
                    len(page.get("interactions") or [])
                    for page in app_map.get("pages") or []
                    if isinstance(page, dict)
                ),
            },
        }

    async def _write_graph(
        self,
        app_map: dict[str, Any],
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        if self._ui_graph_store is None:
            return {"status": "skipped", "reason": "ui_graph_store_not_configured"}
        try:
            return await self._ui_graph_store.write_exploration_graph(
                app_map.get("graph") or {},
                app_map=app_map,
                session_id=context.session_id,
                turn_id=context.turn_id,
                trace_id=context.trace_id,
                project_id=str(arguments.get("project_id") or context.context_bundle.get("project_id") or "").strip() or None,
                project_scope=str(arguments.get("project_scope") or context.context_bundle.get("project_scope") or "default"),
            )
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    async def _write_page_memory(
        self,
        app_map: dict[str, Any],
        context: ToolExecutionContext,
    ) -> list[str]:
        if self._memory_runtime_service is None:
            return []
        write_ids: list[str] = []
        for page in app_map.get("pages") or []:
            if not isinstance(page, dict):
                continue
            nodes = page.get("semantic_nodes") if isinstance(page.get("semantic_nodes"), list) else []
            summary = (
                f"ARIA semantic UI model with {len(nodes)} semantic node(s). "
                "This is structural knowledge only; no test assertions or verification were produced."
            )
            try:
                write_id = await self._memory_runtime_service.write_page_memory(
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    trace_id=context.trace_id,
                    title=str(page.get("title") or ""),
                    current_url=str(page.get("url") or ""),
                    summary=summary,
                    selectors=[str(node.get("locator") or "") for node in nodes if isinstance(node, dict) and node.get("locator")],
                    artifacts=(app_map.get("artifacts") or []),
                )
                if write_id:
                    write_ids.append(write_id)
            except Exception:
                continue
        return write_ids

    def _slug(self, value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value).strip("-") or "item"
