from __future__ import annotations

import asyncio
import json
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.config import Settings
from src.application.resources.session_resource_service import SessionResourceService
from src.application.runtime.python_playwright_cli import PythonPlaywrightCliRuntime
from src.registry.mcp import MCPRegistry
from src.schemas.session_resource import SessionResourceKind


class MCPRuntimeService:
    """Execute MCP-backed capabilities.

    Browser capabilities use the Agent_Server native Python implementation of
    the playwright-cli command contract.
    """

    def __init__(
        self,
        mcp_registry: MCPRegistry,
        settings: Settings,
        session_resource_service: SessionResourceService | None = None,
    ) -> None:
        self._mcp_registry = mcp_registry
        self._settings = settings
        self._session_resource_service = session_resource_service
        self._artifact_root = Path(__file__).resolve().parents[2] / settings.storage.artifact_root_dir
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        self._playwright_cli = PythonPlaywrightCliRuntime(settings)

    async def close_browser_session(self, session_name: str) -> None:
        await self._playwright_cli.close_session(session_name)

    def list_active_servers(self) -> list[dict[str, Any]]:
        return [server.model_dump() for server in self._mcp_registry.list_enabled()]

    def build_prompt_blocks(self, active_servers: list[dict[str, Any]] | None = None) -> list[str]:
        blocks: list[str] = []
        servers = active_servers or [server.model_dump() for server in self._mcp_registry.list_enabled()]
        for server in servers:
            capabilities = ", ".join(server.get("capabilities", [])) or "none"
            blocks.append(
                f"MCP server {server.get('name', server.get('key', 'mcp'))} "
                f"({server.get('key', 'mcp')}) exposes: {capabilities}. {server.get('summary', '')}"
            )
        return blocks

    async def call(
        self,
        server_key: str,
        capability: str,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        server = self._mcp_registry.get(server_key)
        if not server or not server.enabled:
            return {"status": "failed", "error": f"MCP server '{server_key}' is not available."}
        if capability not in server.capabilities:
            return {"status": "failed", "error": f"Capability '{capability}' is not exposed by {server.name}."}

        if capability == "inspect-page":
            return await self._inspect_page(payload, context)
        if capability == "browser-automation":
            return await self._run_browser_automation(payload, context)
        if capability == "browser-control":
            return await self._run_browser_control(payload, context)
        if capability == "write-artifact":
            return self._write_artifact(payload, context)
        return {
            "status": "success",
            "summary": f"Capability '{capability}' acknowledged by {server.name}.",
            "payload": payload,
        }

    async def _inspect_page(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        target_url = self._resolve_target_url(payload, context)
        if not target_url:
            return {"status": "failed", "error": "No target_url or url was supplied for page inspection."}

        artifact_dir = self._prepare_artifact_dir(context, "dom-inspection")
        session_name = self._playwright_session_name(context, "dom-inspector")
        command_log: list[dict[str, Any]] = []

        try:
            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["open", target_url, *self._playwright_open_args()],
                    artifact_dir,
                )
            )
            command_log.extend(await self._resize_browser(session_name, artifact_dir))

            snapshot_path = artifact_dir / "page_snapshot.yml"
            screenshot_path = artifact_dir / "page.png"
            html_path = artifact_dir / "page.html"
            summary_path = artifact_dir / "summary.json"

            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["snapshot", f"--filename={snapshot_path.name}"],
                    artifact_dir,
                )
            )
            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["screenshot", f"--filename={screenshot_path.name}"],
                    artifact_dir,
                )
            )

            metadata = await self._eval_json(
                session_name,
                artifact_dir,
                """JSON.stringify({
  title: document.title || "",
  current_url: location.href,
  forms: document.querySelectorAll("form").length,
  inputs: document.querySelectorAll("input, textarea, select").length,
  buttons: document.querySelectorAll("button, input[type='submit'], input[type='button']").length
})""",
                command_log,
                default={},
            )
            headings = await self._eval_json(
                session_name,
                artifact_dir,
                """JSON.stringify([...document.querySelectorAll("h1, h2, h3")]
  .map((el) => (el.textContent || "").trim())
  .filter(Boolean)
  .slice(0, 12))""",
                command_log,
                default=[],
            )
            links = await self._eval_json(
                session_name,
                artifact_dir,
                """JSON.stringify([...document.querySelectorAll("a[href]")]
  .map((el) => el.href)
  .filter(Boolean)
  .slice(0, 20))""",
                command_log,
                default=[],
            )
            html = await self._eval_text(
                session_name,
                artifact_dir,
                "document.documentElement.outerHTML",
                command_log,
            )
            html_path.write_text(html, encoding="utf-8")

            title = str(metadata.get("title") or "")
            current_url = str(metadata.get("current_url") or target_url)
            summary = {
                "title": title,
                "current_url": current_url,
                "forms": int(metadata.get("forms") or 0),
                "inputs": int(metadata.get("inputs") or 0),
                "buttons": int(metadata.get("buttons") or 0),
                "headings": headings,
                "links": links,
                "captured_at": datetime.utcnow().isoformat() + "Z",
            }
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            transcript_path = self._write_transcript(artifact_dir, command_log)

            return {
                "status": "success",
                "summary": f"Inspected {target_url} with playwright-cli.",
                "target_url": target_url,
                "title": title,
                "current_url": current_url,
                "dom_summary": {
                    "forms": summary["forms"],
                    "inputs": summary["inputs"],
                    "buttons": summary["buttons"],
                    "headings": headings,
                },
                "links": links,
                "runtime_backend": self._runtime_backend(),
                "artifacts": [
                    {"type": "html", "path": str(html_path)},
                    {"type": "screenshot", "path": str(screenshot_path)},
                    {"type": "snapshot", "path": str(snapshot_path)},
                    {"type": "summary", "path": str(summary_path)},
                    {"type": "transcript", "path": str(transcript_path)},
                ],
            }
        finally:
            await self._close_playwright_session(session_name, artifact_dir)

    async def _run_browser_automation(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        target_url = self._resolve_target_url(payload, context)
        if not target_url:
            return {"status": "failed", "error": "No target_url or url was supplied for browser automation."}

        artifact_dir = self._prepare_artifact_dir(context, "browser-automation")
        session_name = self._playwright_session_name(context, "browser-automation")
        command_log: list[dict[str, Any]] = []
        executed_steps: list[dict[str, Any]] = []
        objective = payload.get("objective") or context.get("user_message") or "Execute browser automation."

        try:
            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["open", target_url, *self._playwright_open_args()],
                    artifact_dir,
                )
            )
            command_log.extend(await self._resize_browser(session_name, artifact_dir))
            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["screenshot", "--filename=initial.png"],
                    artifact_dir,
                )
            )

            for index, action in enumerate(self._normalize_actions(payload), start=1):
                step = await self._execute_browser_action(session_name, artifact_dir, action, index, command_log)
                executed_steps.append(step)

            final_screenshot = artifact_dir / "final.png"
            final_snapshot = artifact_dir / "final_snapshot.yml"
            html_path = artifact_dir / "page.html"
            summary_path = artifact_dir / "automation_summary.json"

            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["screenshot", f"--filename={final_screenshot.name}"],
                    artifact_dir,
                )
            )
            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["snapshot", f"--filename={final_snapshot.name}"],
                    artifact_dir,
                )
            )
            metadata = await self._eval_json(
                session_name,
                artifact_dir,
                """JSON.stringify({
  title: document.title || "",
  current_url: location.href
})""",
                command_log,
                default={},
            )
            html = await self._eval_text(
                session_name,
                artifact_dir,
                "document.documentElement.outerHTML",
                command_log,
            )
            html_path.write_text(html, encoding="utf-8")

            title = str(metadata.get("title") or "")
            current_url = str(metadata.get("current_url") or target_url)
            summary = {
                "objective": objective,
                "target_url": target_url,
                "title": title,
                "current_url": current_url,
                "steps": executed_steps,
                "captured_at": datetime.utcnow().isoformat() + "Z",
            }
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            transcript_path = self._write_transcript(artifact_dir, command_log)

            return {
                "status": "success",
                "summary": f"Executed playwright-cli browser automation for {target_url}.",
                "target_url": target_url,
                "objective": objective,
                "title": title,
                "current_url": current_url,
                "steps": executed_steps,
                "runtime_backend": self._runtime_backend(),
                "artifacts": [
                    {"type": "screenshot", "path": str(artifact_dir / "initial.png")},
                    {"type": "screenshot", "path": str(final_screenshot)},
                    {"type": "snapshot", "path": str(final_snapshot)},
                    {"type": "html", "path": str(html_path)},
                    {"type": "summary", "path": str(summary_path)},
                    {"type": "transcript", "path": str(transcript_path)},
                ],
            }
        finally:
            await self._close_playwright_session(session_name, artifact_dir)

    async def _run_browser_control(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action") or "").strip().lower()
        if not action:
            return {"status": "failed", "error": "browser-control requires an action."}

        if action in {"inspect", "snapshot", "dom"}:
            return await self._inspect_page(payload, context)
        if action in {"run_actions", "act", "automate"}:
            return await self._run_browser_automation(payload, context)
        if action in {"command", "playwright-cli", "cli"}:
            return await self._run_browser_cli_command(payload, context)

        target_url = self._resolve_target_url(payload, context)
        if not target_url:
            return {"status": "failed", "error": f"browser-control action '{action}' requires a target_url or url."}

        artifact_dir = self._prepare_artifact_dir(context, f"browser-control-{self._slug(action)}")
        session_name = self._playwright_session_name(context, f"browser-control-{action}")
        command_log: list[dict[str, Any]] = []

        try:
            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["open", target_url, *self._playwright_open_args()],
                    artifact_dir,
                )
            )
            command_log.extend(await self._resize_browser(session_name, artifact_dir))

            artifacts: list[dict[str, str]] = []
            result: Any = None
            if action in {"navigate", "open"}:
                screenshot_path = artifact_dir / "navigate.png"
                command_log.append(
                    await self._run_playwright_cli(
                        session_name,
                        ["screenshot", f"--filename={screenshot_path.name}"],
                        artifact_dir,
                    )
                )
                artifacts.append({"type": "screenshot", "path": str(screenshot_path)})
            elif action in {"semantic-snapshot", "semantic_snapshot", "aria-snapshot", "aria_snapshot"}:
                snapshot_path = artifact_dir / "semantic_snapshot.json"
                command_log.append(
                    await self._run_playwright_cli(
                        session_name,
                        ["semantic-snapshot", f"--filename={snapshot_path.name}"],
                        artifact_dir,
                        raw=True,
                    )
                )
                result = self._parse_json_if_possible(str(command_log[-1].get("stdout") or ""))
                artifacts.append({"type": "semantic-snapshot", "path": str(snapshot_path)})
            elif action in {"screenshot", "capture"}:
                label = self._slug(str(payload.get("label") or action))
                screenshot_path = artifact_dir / f"{label}.png"
                command_log.append(
                    await self._run_playwright_cli(
                        session_name,
                        ["screenshot", f"--filename={screenshot_path.name}"],
                        artifact_dir,
                    )
                )
                artifacts.append({"type": "screenshot", "path": str(screenshot_path)})
            elif action in {"evaluate_js", "eval", "javascript"}:
                expression = str(payload.get("javascript") or payload.get("script") or "").strip()
                if not expression:
                    return {"status": "failed", "error": "evaluate_js requires a javascript or script value."}
                result_text = await self._eval_text(session_name, artifact_dir, expression, command_log)
                result = self._parse_json_if_possible(result_text)
                result_path = artifact_dir / "evaluate_js.json"
                result_path.write_text(
                    json.dumps({"result": result, "raw": result_text}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                artifacts.append({"type": "json", "path": str(result_path)})
            else:
                return {"status": "failed", "error": f"Unsupported browser-control action '{action}'."}

            transcript_path = self._write_transcript(artifact_dir, command_log)
            artifacts.append({"type": "transcript", "path": str(transcript_path)})
            return {
                "status": "success",
                "summary": f"browser-control action '{action}' completed with playwright-cli.",
                "action": action,
                "target_url": target_url,
                "result": result,
                "runtime_backend": self._runtime_backend(),
                "artifacts": artifacts,
            }
        finally:
            await self._close_playwright_session(session_name, artifact_dir)

    async def _run_browser_cli_command(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        raw_args = payload.get("args") or payload.get("command_args") or payload.get("command") or []
        if isinstance(raw_args, str):
            args = shlex.split(raw_args, posix=False)
        elif isinstance(raw_args, list):
            args = [str(item) for item in raw_args]
        else:
            args = []
        if not args:
            return {"status": "failed", "error": "browser-control action=command requires args or command."}

        artifact_dir = self._prepare_artifact_dir(context, "browser-control-command")
        session_name = self._playwright_session_name(context, "browser-control-command")
        command_log: list[dict[str, Any]] = []
        try:
            result = await self._run_playwright_cli(
                session_name=session_name,
                args=args,
                cwd=artifact_dir,
                raw=bool(payload.get("raw", False)),
            )
            await self._register_browser_resources(context, session_name, args, artifact_dir)
            command_log.append(result)
            transcript_path = self._write_transcript(artifact_dir, command_log)
            return {
                "status": "success",
                "summary": f"Executed python playwright-cli command: {' '.join(args)}",
                "command": args,
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "runtime_backend": self._runtime_backend(),
                "artifacts": [*result.get("artifacts", []), {"type": "transcript", "path": str(transcript_path)}],
            }
        finally:
            if args[0] in {"close", "delete-data"}:
                await self._close_playwright_session(session_name, artifact_dir)
                if self._session_resource_service is not None:
                    await self._session_resource_service.mark_released(
                        session_id=str(context.get("session_id") or ""),
                        kind=SessionResourceKind.browser_session,
                        resource_key=session_name,
                        reason=f"browser-control:{args[0]}",
                    )

    async def _register_browser_resources(
        self,
        context: dict[str, Any],
        session_name: str,
        args: list[str],
        artifact_dir: Path,
    ) -> None:
        if self._session_resource_service is None or not args:
            return
        verb = str(args[0] or "").strip().lower()
        session_id = str(context.get("session_id") or "").strip()
        if not session_id or verb in {"close", "close-all", "kill-all", "delete-data"}:
            return
        await self._session_resource_service.register(
            session_id=session_id,
            kind=SessionResourceKind.browser_session,
            resource_key=session_name,
            metadata={
                "artifact_dir": str(artifact_dir),
                "last_command": args,
                "runtime": "python-playwright-cli",
            },
        )
        profile = self._option_value(args, "--profile")
        if profile or "--persistent" in args:
            await self._session_resource_service.register(
                session_id=session_id,
                kind=SessionResourceKind.browser_profile,
                resource_key=profile or str(artifact_dir / ".playwright-cli" / session_name / "profile"),
                metadata={
                    "browser_session": session_name,
                    "artifact_dir": str(artifact_dir),
                },
            )

    async def _execute_browser_action(
        self,
        session_name: str,
        artifact_dir: Path,
        action: dict[str, Any],
        index: int,
        command_log: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action_type = str(action.get("type") or action.get("action") or "").strip().lower()
        selector = str(action.get("selector") or action.get("target") or action.get("ref") or "").strip()
        value = str(action.get("value") or action.get("text") or "").strip()

        if action_type == "click" and selector:
            command_log.append(await self._run_playwright_cli(session_name, ["click", selector], artifact_dir))
            return {"index": index, "action": "click", "target": selector, "status": "success"}
        if action_type in {"input", "fill"} and selector:
            command_log.append(await self._run_playwright_cli(session_name, ["fill", selector, value], artifact_dir))
            return {"index": index, "action": "fill", "target": selector, "value": value, "status": "success"}
        if action_type == "type" and value:
            command_log.append(await self._run_playwright_cli(session_name, ["type", value], artifact_dir))
            return {"index": index, "action": "type", "value": value, "status": "success"}
        if action_type == "press" and value:
            command_log.append(await self._run_playwright_cli(session_name, ["press", value], artifact_dir))
            return {"index": index, "action": "press", "key": value, "status": "success"}
        if action_type == "wait":
            seconds = self._coerce_float(action.get("seconds") or action.get("value"), default=1.0)
            await asyncio.sleep(max(0.0, min(seconds, 30.0)))
            return {"index": index, "action": "wait", "seconds": seconds, "status": "success"}
        if action_type == "screenshot":
            label = self._slug(str(action.get("label") or f"step-{index}"))
            command_log.append(
                await self._run_playwright_cli(
                    session_name,
                    ["screenshot", f"--filename={label}.png"],
                    artifact_dir,
                )
            )
            return {"index": index, "action": "screenshot", "path": str(artifact_dir / f"{label}.png"), "status": "success"}
        if action_type == "scroll":
            y = str(int(self._coerce_float(action.get("y") or action.get("delta_y"), default=600)))
            command_log.append(await self._run_playwright_cli(session_name, ["mousewheel", "0", y], artifact_dir))
            return {"index": index, "action": "scroll", "delta_y": y, "status": "success"}

        return {"index": index, "action": action_type or "unknown", "status": "skipped", "reason": "Unsupported or incomplete action."}

    async def _eval_json(
        self,
        session_name: str,
        artifact_dir: Path,
        expression: str,
        command_log: list[dict[str, Any]],
        default: Any,
    ) -> Any:
        text = await self._eval_text(session_name, artifact_dir, expression, command_log)
        parsed = self._parse_json_if_possible(text)
        return default if parsed is None else parsed

    async def _eval_text(
        self,
        session_name: str,
        artifact_dir: Path,
        expression: str,
        command_log: list[dict[str, Any]],
    ) -> str:
        result = await self._run_playwright_cli(session_name, ["eval", expression], artifact_dir, raw=True)
        command_log.append(result)
        return str(result.get("stdout") or "").strip()

    async def _resize_browser(self, session_name: str, artifact_dir: Path) -> list[dict[str, Any]]:
        width = int(self._settings.orchestration.browser_window_width or 0)
        height = int(self._settings.orchestration.browser_window_height or 0)
        if width <= 0 or height <= 0:
            return []
        return [await self._run_playwright_cli(session_name, ["resize", str(width), str(height)], artifact_dir)]

    async def _close_playwright_session(self, session_name: str, artifact_dir: Path) -> None:
        try:
            await self._run_playwright_cli(session_name, ["close"], artifact_dir, timeout_seconds=15)
        except Exception:
            pass

    async def _run_playwright_cli(
        self,
        session_name: str,
        args: list[str],
        cwd: Path,
        raw: bool = False,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        result = await self._playwright_cli.run(
            session_name=session_name,
            args=args,
            cwd=cwd,
            raw=raw,
            timeout_seconds=timeout_seconds,
        )
        if int(result["exit_code"]) != 0:
            stderr = str(result.get("stderr") or "").strip()
            stdout = str(result.get("stdout") or "").strip()
            detail = stderr or stdout or "unknown error"
            command = " ".join(str(item) for item in result.get("command", ["python-playwright-cli", *args]))
            raise RuntimeError(f"python playwright-cli command failed: {command}\n{detail}")
        return result

    def _write_artifact(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        artifact_dir = self._prepare_artifact_dir(context, "file-artifact-manager")
        if payload.get("filename"):
            filename = self._slug(str(payload.get("filename")))
            if "." not in filename:
                filename += ".txt"
            path = artifact_dir / filename
        else:
            file_name = self._slug(str(payload.get("file_name") or "artifact"))
            extension = str(payload.get("extension") or "txt").lstrip(".")
            path = artifact_dir / f"{file_name}.{extension}"

        if "json_data" in payload:
            path.write_text(json.dumps(payload["json_data"], ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            path.write_text(str(payload.get("content") or ""), encoding="utf-8")
        return {
            "status": "success",
            "summary": f"Wrote artifact {path.name}.",
            "artifact_path": str(path),
            "artifacts": [{"type": "file", "path": str(path)}],
        }

    def _prepare_artifact_dir(self, context: dict[str, Any], category: str) -> Path:
        session_id = self._slug(str(context.get("session_id") or "session"))
        turn_id = self._slug(str(context.get("turn_id") or "turn"))
        artifact_dir = self._artifact_root / session_id / turn_id / category
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    def _write_transcript(self, artifact_dir: Path, command_log: list[dict[str, Any]]) -> Path:
        transcript_path = artifact_dir / "playwright_cli_transcript.json"
        transcript_path.write_text(json.dumps(command_log, ensure_ascii=False, indent=2), encoding="utf-8")
        return transcript_path

    def _resolve_target_url(self, payload: dict[str, Any], context: dict[str, Any]) -> str:
        candidates = [
            payload.get("target_url"),
            payload.get("url"),
            context.get("target_url"),
        ]
        context_bundle = context.get("context_bundle")
        if isinstance(context_bundle, dict):
            candidates.append(context_bundle.get("target_url"))
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        match = re.search(r"https?://[^\s]+", str(context.get("user_message") or ""))
        return match.group(0) if match else ""

    def _normalize_actions(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        actions = payload.get("actions") or []
        if not isinstance(actions, list):
            return []
        return [action for action in actions if isinstance(action, dict)]

    def _playwright_open_args(self) -> list[str]:
        browser = str(self._settings.orchestration.browser_default_name or "chromium").strip().lower()
        browser_map = {"edge": "msedge", "chrome": "chrome", "msedge": "msedge", "firefox": "firefox", "webkit": "webkit"}
        args = [f"--browser={browser_map[browser]}"] if browser in browser_map else []
        if not bool(self._settings.orchestration.browser_headless):
            args.append("--headed")
        return args

    def _runtime_backend(self) -> str:
        return f"{self._settings.orchestration.browser_backend}:{self._settings.orchestration.browser_default_name}"

    def _playwright_session_name(self, context: dict[str, Any], tool_key: str) -> str:
        session_id = self._slug(str(context.get("session_id") or "session"))
        turn_id = self._slug(str(context.get("turn_id") or "turn"))
        return self._slug(f"{session_id}-{turn_id}-{tool_key}")[:80]

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
        return slug[:96] or "item"

    @staticmethod
    def _option_value(args: list[str], name: str) -> str | None:
        prefix = f"{name}="
        for index, item in enumerate(args):
            if item == name and index + 1 < len(args):
                return args[index + 1]
            if item.startswith(prefix):
                return item[len(prefix):]
        return None

    def _parse_json_if_possible(self, text: str) -> Any:
        stripped = text.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return stripped

    def _coerce_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
