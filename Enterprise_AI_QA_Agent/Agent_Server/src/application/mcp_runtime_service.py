from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from src.core.config import Settings
from src.registry.mcp import MCPRegistry


class MCPRuntimeService:
    def __init__(self, mcp_registry: MCPRegistry, settings: Settings) -> None:
        self._mcp_registry = mcp_registry
        self._settings = settings
        self._artifact_root = Path(__file__).resolve().parents[2] / settings.artifact_root_dir
        self._artifact_root.mkdir(parents=True, exist_ok=True)

    def list_active_servers(self) -> list[dict[str, Any]]:
        servers = []
        for server in self._mcp_registry.list():
            if not server.enabled:
                continue
            servers.append(server.model_dump(mode="python"))
        return servers

    def build_prompt_blocks(self, active_servers: list[dict[str, Any]]) -> list[str]:
        blocks: list[str] = []
        for server in active_servers:
            capabilities = ", ".join(server.get("capabilities", [])) or "none"
            blocks.append(
                f"- {server.get('name', server.get('key', 'mcp'))}: "
                f"transport={server.get('transport', 'unknown')}, "
                f"capabilities={capabilities}, status={server.get('status', 'unknown')}"
            )
        return blocks

    async def call(
        self,
        server_key: str,
        capability: str,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if server_key == "browser-mcp":
            if capability == "inspect-page":
                return self._inspect_page(payload, context)
            if capability == "browser-automation":
                return self._run_browser_automation(payload, context)
        if server_key == "filesystem-mcp":
            if capability == "write-artifact":
                return self._write_artifact(payload, context)
        raise ValueError(f"Unsupported MCP runtime call: {server_key}/{capability}")

    def _inspect_page(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        target_url = _resolve_target_url(payload, context)
        if not target_url:
            return {
                "summary": "DOM Inspector requires a target URL in the prompt or tool arguments.",
                "dom_summary": "No target URL provided.",
                "headings": [],
                "links": [],
                "artifacts": [],
            }

        artifact_dir = self._prepare_artifact_dir(context, "dom-inspector")
        driver = self._create_driver()
        try:
            driver.get(target_url)
            html_path = artifact_dir / "page.html"
            screenshot_path = artifact_dir / "page.png"
            html_path.write_text(driver.page_source, encoding="utf-8", errors="ignore")
            driver.save_screenshot(str(screenshot_path))
            headings = [
                item.text.strip()
                for item in driver.find_elements(By.CSS_SELECTOR, "h1, h2, h3")
                if item.text.strip()
            ][:12]
            links = [
                item.get_attribute("href")
                for item in driver.find_elements(By.CSS_SELECTOR, "a[href]")
                if item.get_attribute("href")
            ][:20]
            forms = len(driver.find_elements(By.CSS_SELECTOR, "form"))
            inputs = len(driver.find_elements(By.CSS_SELECTOR, "input, textarea, select"))
            buttons = len(driver.find_elements(By.CSS_SELECTOR, "button, input[type='submit'], input[type='button']"))
            artifacts = [
                {"type": "html", "path": str(html_path)},
                {"type": "screenshot", "path": str(screenshot_path)},
            ]
            summary = {
                "title": driver.title.strip(),
                "current_url": driver.current_url,
                "forms": forms,
                "inputs": inputs,
                "buttons": buttons,
                "headings": len(headings),
                "links": len(links),
            }
            (artifact_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            artifacts.append({"type": "summary", "path": str(artifact_dir / "summary.json")})
            return {
                "summary": f"Inspected DOM in a real browser session for {target_url}.",
                "target_url": target_url,
                "title": driver.title.strip(),
                "current_url": driver.current_url,
                "dom_summary": (
                    f"title={driver.title.strip() or 'n/a'}; headings={len(headings)}; "
                    f"forms={forms}; inputs={inputs}; buttons={buttons}; links={len(links)}"
                ),
                "headings": headings,
                "links": links,
                "artifacts": artifacts,
                "runtime_backend": f"{self._settings.browser_backend}:{self._settings.browser_default_name}",
            }
        finally:
            driver.quit()

    def _run_browser_automation(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        target_url = _resolve_target_url(payload, context)
        objective = str(payload.get("objective") or context.get("user_message") or "").strip()
        if not target_url:
            return {
                "summary": "Browser Automation requires a target URL in the prompt or tool arguments.",
                "steps": [],
                "artifacts": [],
            }

        artifact_dir = self._prepare_artifact_dir(context, "browser-automation")
        driver = self._create_driver()
        steps: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        try:
            driver.get(target_url)
            steps.append({"step": "open_url", "status": "completed", "detail": f"Opened {target_url}"})

            initial_screenshot = artifact_dir / "initial.png"
            driver.save_screenshot(str(initial_screenshot))
            artifacts.append({"type": "screenshot", "label": "initial", "path": str(initial_screenshot)})

            for index, action in enumerate(payload.get("actions", []), start=1):
                action_type = str(action.get("type") or "").strip().lower()
                if action_type == "click":
                    selector = str(action.get("selector") or "").strip()
                    driver.find_element(By.CSS_SELECTOR, selector).click()
                    steps.append({"step": f"action_{index}", "status": "completed", "detail": f"Clicked {selector}"})
                elif action_type == "input":
                    selector = str(action.get("selector") or "").strip()
                    value = str(action.get("value") or "")
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                    element.clear()
                    element.send_keys(value)
                    steps.append({"step": f"action_{index}", "status": "completed", "detail": f"Filled {selector}"})
                elif action_type == "wait":
                    seconds = float(action.get("seconds") or 1)
                    driver.implicitly_wait(int(max(seconds, 1)))
                    steps.append({"step": f"action_{index}", "status": "completed", "detail": f"Waited {seconds} seconds"})
                elif action_type == "screenshot":
                    label = _slug(str(action.get("label") or f"action_{index}"))
                    shot_path = artifact_dir / f"{label}.png"
                    driver.save_screenshot(str(shot_path))
                    artifacts.append({"type": "screenshot", "label": label, "path": str(shot_path)})
                    steps.append({"step": f"action_{index}", "status": "completed", "detail": f"Captured screenshot {label}"})
                elif action_type == "scroll":
                    y = int(action.get("y") or 600)
                    driver.execute_script("window.scrollBy(0, arguments[0]);", y)
                    steps.append({"step": f"action_{index}", "status": "completed", "detail": f"Scrolled by {y}px"})
                else:
                    steps.append({"step": f"action_{index}", "status": "skipped", "detail": f"Unsupported action type '{action_type}'"})

            final_screenshot = artifact_dir / "final.png"
            driver.save_screenshot(str(final_screenshot))
            artifacts.append({"type": "screenshot", "label": "final", "path": str(final_screenshot)})
            html_path = artifact_dir / "page.html"
            html_path.write_text(driver.page_source, encoding="utf-8", errors="ignore")
            artifacts.append({"type": "html", "path": str(html_path)})

            summary = {
                "title": driver.title.strip(),
                "current_url": driver.current_url,
                "forms": len(driver.find_elements(By.CSS_SELECTOR, "form")),
                "inputs": len(driver.find_elements(By.CSS_SELECTOR, "input, textarea, select")),
                "buttons": len(driver.find_elements(By.CSS_SELECTOR, "button, input[type='submit'], input[type='button']")),
                "objective": objective,
                "step_count": len(steps),
            }
            summary_path = artifact_dir / "summary.json"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts.append({"type": "summary", "path": str(summary_path)})

            return {
                "summary": f"Executed Selenium browser automation for {target_url}.",
                "target_url": target_url,
                "objective": objective,
                "title": driver.title.strip(),
                "current_url": driver.current_url,
                "steps": steps,
                "artifacts": artifacts,
                "runtime_backend": f"{self._settings.browser_backend}:{self._settings.browser_default_name}",
            }
        finally:
            driver.quit()

    def _write_artifact(self, payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        artifact_dir = self._prepare_artifact_dir(context, "file-artifact-manager")
        file_name = _slug(str(payload.get("file_name") or "artifact")) or "artifact"
        extension = str(payload.get("extension") or "txt").lstrip(".")
        target_path = artifact_dir / f"{file_name}.{extension}"

        if "json_data" in payload:
            target_path.write_text(
                json.dumps(payload["json_data"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        else:
            target_path.write_text(str(payload.get("content") or ""), encoding="utf-8")

        return {
            "summary": f"Persisted artifact to {target_path.name}.",
            "artifact_path": str(target_path),
            "artifacts": [{"type": "file", "path": str(target_path)}],
        }

    def _prepare_artifact_dir(self, context: dict[str, Any], tool_key: str) -> Path:
        session_id = _slug(str(context.get("session_id") or "session"))
        turn_id = _slug(str(context.get("turn_id") or datetime.utcnow().strftime("%Y%m%d%H%M%S")))
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        artifact_dir = self._artifact_root / session_id / turn_id / f"{tool_key}_{timestamp}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    def _create_driver(self):
        errors: list[str] = []
        browser_name = (self._settings.browser_default_name or "chrome").lower()
        for candidate in [browser_name, "chrome", "edge"]:
            try:
                if candidate == "chrome":
                    options = webdriver.ChromeOptions()
                    if self._settings.browser_headless:
                        options.add_argument("--headless=new")
                    options.add_argument(f"--window-size={self._settings.browser_window_width},{self._settings.browser_window_height}")
                    options.add_argument("--disable-gpu")
                    options.add_argument("--no-sandbox")
                    return webdriver.Chrome(options=options)
                if candidate == "edge":
                    options = webdriver.EdgeOptions()
                    if self._settings.browser_headless:
                        options.add_argument("--headless=new")
                    options.add_argument(f"--window-size={self._settings.browser_window_width},{self._settings.browser_window_height}")
                    return webdriver.Edge(options=options)
            except WebDriverException as exc:
                errors.append(f"{candidate}: {exc}")
        raise RuntimeError("Unable to start a Selenium browser driver. " + " | ".join(errors))


def _resolve_target_url(payload: dict[str, Any], context: dict[str, Any]) -> str:
    for candidate in [
        payload.get("target_url"),
        payload.get("url"),
        context.get("context_bundle", {}).get("target_url"),
    ]:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    match = re.search(r"https?://[^\s]+", str(context.get("user_message") or ""))
    return match.group(0) if match else ""


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_").lower()
