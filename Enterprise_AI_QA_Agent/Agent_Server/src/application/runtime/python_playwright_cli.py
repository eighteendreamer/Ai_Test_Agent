from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from src.core.config import Settings


@dataclass
class PlaywrightCliCommandResult:
    command: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    artifacts: list[dict[str, str]] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "artifacts": self.artifacts,
        }


@dataclass
class _BrowserSession:
    name: str
    playwright: Any
    browser: Any
    context: Any
    page: Any
    output_dir: Path
    persistent: bool = False
    ref_map: dict[str, str] = field(default_factory=dict)
    console_messages: list[dict[str, Any]] = field(default_factory=list)
    network_events: list[dict[str, Any]] = field(default_factory=list)
    pending_request_count: int = 0


@dataclass
class _AriaNode:
    role: str
    name: str = ""
    raw: str = ""
    parent: "_AriaNode | None" = None
    children: list["_AriaNode"] = field(default_factory=list)
    ref: str = ""
    locator: str = ""
    visible: bool | None = None
    index: int = 0

    def walk(self) -> list["_AriaNode"]:
        nodes = [self]
        for child in self.children:
            nodes.extend(child.walk())
        return nodes


class PythonPlaywrightCliRuntime:
    """Python implementation of the playwright-cli command contract.

    This runtime intentionally mirrors the CLI shape used by Claude Code skills:
    model output can be represented as small commands such as `open`, `snapshot`,
    `click e3`, or `eval "document.title"`, while the implementation stays fully
    inside the Agent_Server Python process.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, _BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def run(
        self,
        session_name: str,
        args: list[str],
        cwd: Path,
        raw: bool = False,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout = timeout_seconds or int(self._settings.orchestration.browser_action_timeout_seconds or 30)
        timeout = max(1, min(timeout, 1800))
        try:
            return await asyncio.wait_for(
                self._run_locked(session_name=session_name, args=args, cwd=cwd, raw=raw),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return PlaywrightCliCommandResult(
                command=self._format_command(session_name, args, raw),
                exit_code=124,
                stderr=f"python playwright-cli command timed out after {timeout}s",
                timed_out=True,
            ).model_dump()

    async def _run_locked(self, session_name: str, args: list[str], cwd: Path, raw: bool) -> dict[str, Any]:
        async with self._lock:
            command = self._format_command(session_name, args, raw)
            if not args:
                return self._success(command, self._help_text())

            normalized_args, global_raw = self._strip_global_options(args)
            raw = raw or global_raw
            verb = normalized_args[0].strip().lower()
            rest = normalized_args[1:]

            try:
                if verb in {"--help", "-h", "help"}:
                    return self._success(command, self._help_text())
                if verb in {"--version", "-v", "version"}:
                    return self._success(command, "0.1.7-python\n")
                if verb == "list":
                    return self._success(command, self._list_sessions(raw))
                if verb == "close-all":
                    await self.close_all()
                    return self._success(command, "" if raw else "Closed all browser sessions.\n")
                if verb == "kill-all":
                    await self.close_all()
                    return self._success(command, "" if raw else "Killed all browser sessions.\n")

                session = await self._ensure_session(session_name, cwd, rest if verb == "open" else [])
                stdout, artifacts = await self._execute(session, verb, rest, cwd, raw)
                return self._success(command, stdout, artifacts)
            except Exception as exc:
                return PlaywrightCliCommandResult(
                    command=command,
                    exit_code=1,
                    stderr=f"{type(exc).__name__}: {exc}",
                ).model_dump()

    async def close_all(self) -> None:
        for session_name in list(self._sessions):
            await self._close_session(session_name)

    async def close_session(self, session_name: str) -> None:
        await self._close_session(session_name)

    async def _ensure_session(self, session_name: str, cwd: Path, open_args: list[str]) -> _BrowserSession:
        if session_name in self._sessions:
            return self._sessions[session_name]

        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError(
                "Python Playwright is not installed in the Agent_Server Python environment. "
                "Install it with: python -m pip install playwright && python -m playwright install"
            ) from exc

        browser_name = self._option_value(open_args, "--browser") or self._settings.orchestration.browser_default_name or "chromium"
        browser_name = browser_name.lower()
        headed = "--headed" in open_args or not bool(self._settings.orchestration.browser_headless)
        persistent = "--persistent" in open_args or bool(self._option_value(open_args, "--profile"))
        profile = self._option_value(open_args, "--profile")
        output_dir = cwd / ".playwright-cli" / self._slug(session_name)
        output_dir.mkdir(parents=True, exist_ok=True)

        playwright = await async_playwright().start()
        viewport = {
            "width": int(self._settings.orchestration.browser_window_width or 1440),
            "height": int(self._settings.orchestration.browser_window_height or 960),
        }
        launch_options: dict[str, Any] = {"headless": not headed}
        browser_type_name = browser_name
        if browser_name in {"chrome", "msedge"}:
            browser_type_name = "chromium"
            launch_options["channel"] = browser_name
        if browser_name == "edge":
            browser_type_name = "chromium"
            launch_options["channel"] = "msedge"
        browser_type = getattr(playwright, browser_type_name, playwright.chromium)

        if persistent:
            profile_dir = Path(profile) if profile else output_dir / "profile"
            context = await browser_type.launch_persistent_context(
                str(profile_dir),
                viewport=viewport,
                **launch_options,
            )
            browser = None
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await browser_type.launch(**launch_options)
            context = await browser.new_context(viewport=viewport)
            page = await context.new_page()

        session = _BrowserSession(
            name=session_name,
            playwright=playwright,
            browser=browser,
            context=context,
            page=page,
            output_dir=output_dir,
            persistent=persistent,
        )
        self._wire_session_events(session)
        self._sessions[session_name] = session
        return session

    def _wire_session_events(self, session: _BrowserSession) -> None:
        def on_console(message: Any) -> None:
            session.console_messages.append(
                {
                    "type": message.type,
                    "text": message.text,
                    "location": message.location,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
            )

        def on_request(request: Any) -> None:
            session.pending_request_count += 1
            session.network_events.append(
                {
                    "method": request.method,
                    "url": request.url,
                    "resource_type": request.resource_type,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                }
            )

        def on_request_done(request: Any) -> None:
            session.pending_request_count = max(0, session.pending_request_count - 1)

        session.page.on("console", on_console)
        session.page.on("request", on_request)
        session.page.on("requestfinished", on_request_done)
        session.page.on("requestfailed", on_request_done)

    async def _execute(
        self,
        session: _BrowserSession,
        verb: str,
        args: list[str],
        cwd: Path,
        raw: bool,
    ) -> tuple[str, list[dict[str, str]]]:
        page = session.page
        if verb == "open":
            url = self._first_positional(args)
            if url:
                await page.goto(self._normalize_url(url), wait_until="domcontentloaded")
                await self._wait_for_view_stable(session)
            return await self._page_output(session, raw)
        if verb == "goto":
            self._require_args(args, 1, "goto <url>")
            await page.goto(self._normalize_url(args[0]), wait_until="domcontentloaded")
            await self._wait_for_view_stable(session)
            return await self._page_output(session, raw)
        if verb == "close":
            await self._close_session(session.name)
            return ("" if raw else f"Closed browser session '{session.name}'.\n", [])
        if verb == "delete-data":
            output_dir = session.output_dir
            await self._close_session(session.name)
            if output_dir.exists():
                shutil.rmtree(output_dir, ignore_errors=True)
            return ("" if raw else f"Deleted user data for {session.name}.\n", [])
        if verb == "resize":
            self._require_args(args, 2, "resize <width> <height>")
            width, height = int(args[0]), int(args[1])
            await page.set_viewport_size({"width": width, "height": height})
            return await self._page_output(session, raw)
        if verb == "snapshot":
            target, filename, depth = self._parse_snapshot_args(args)
            text = await self._snapshot(session, target=target, depth=depth)
            artifacts = []
            if filename:
                path = cwd / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                artifacts.append({"type": "snapshot", "path": str(path)})
            return (text if raw else self._with_page_header(session, f"### Snapshot\n{text}", artifacts), artifacts)
        if verb == "semantic-snapshot":
            filename = self._option_value(args, "--filename")
            visible_only = "--include-hidden" not in args and self._option_value(args, "--visible-only") != "false"
            payload = await self._semantic_snapshot(session, visible_only=visible_only)
            text = json.dumps(payload, ensure_ascii=False, indent=None if raw else 2) + "\n"
            artifacts = []
            if filename:
                path = cwd / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                artifacts.append({"type": "semantic-snapshot", "path": str(path)})
            return (text if raw else self._with_page_header(session, f"### Semantic Snapshot\n{text}", artifacts), artifacts)
        if verb == "explore":
            payload, artifacts = await self._explore(session, args, cwd)
            text = json.dumps(payload, ensure_ascii=False, indent=None if raw else 2) + "\n"
            return (text if raw else self._with_page_header(session, f"### UI Exploration Graph\n{text}", artifacts), artifacts)
        if verb == "screenshot":
            target, filename = self._parse_target_filename(args, default_name="screenshot.png")
            path = cwd / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            if target:
                await self._locator(session, target).screenshot(path=str(path))
            else:
                await page.screenshot(path=str(path), full_page=True)
            return ("" if raw else f"Saved screenshot to {path}\n", [{"type": "screenshot", "path": str(path)}])
        if verb == "pdf":
            filename = self._option_value(args, "--filename") or "page.pdf"
            path = cwd / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.pdf(path=str(path))
            return ("" if raw else f"Saved PDF to {path}\n", [{"type": "pdf", "path": str(path)}])
        if verb == "eval":
            self._require_args(args, 1, "eval <expression> [ref]")
            expression = args[0]
            target = args[1] if len(args) > 1 else ""
            if target:
                result = await self._locator(session, target).evaluate(expression)
            else:
                result = await page.evaluate(expression)
            return (self._format_value(result, raw), [])
        if verb == "click":
            self._require_args(args, 1, "click <ref> [button]")
            await self._locator(session, args[0]).click(button=args[1] if len(args) > 1 else "left")
            return await self._page_output(session, raw)
        if verb == "dblclick":
            self._require_args(args, 1, "dblclick <ref> [button]")
            await self._locator(session, args[0]).dblclick(button=args[1] if len(args) > 1 else "left")
            return await self._page_output(session, raw)
        if verb == "fill":
            self._require_args(args, 2, "fill <ref> <text>")
            await self._locator(session, args[0]).fill(args[1])
            if "--submit" in args:
                await page.keyboard.press("Enter")
            return await self._page_output(session, raw)
        if verb == "type":
            self._require_args(args, 1, "type <text>")
            await page.keyboard.type(args[0])
            return await self._page_output(session, raw)
        if verb == "press":
            self._require_args(args, 1, "press <key>")
            await page.keyboard.press(args[0])
            return await self._page_output(session, raw)
        if verb == "keydown":
            self._require_args(args, 1, "keydown <key>")
            await page.keyboard.down(args[0])
            return await self._page_output(session, raw)
        if verb == "keyup":
            self._require_args(args, 1, "keyup <key>")
            await page.keyboard.up(args[0])
            return await self._page_output(session, raw)
        if verb == "hover":
            self._require_args(args, 1, "hover <ref>")
            await self._locator(session, args[0]).hover()
            return await self._page_output(session, raw)
        if verb == "select":
            self._require_args(args, 2, "select <ref> <value>")
            await self._locator(session, args[0]).select_option(args[1])
            return await self._page_output(session, raw)
        if verb == "check":
            self._require_args(args, 1, "check <ref>")
            await self._locator(session, args[0]).check()
            return await self._page_output(session, raw)
        if verb == "uncheck":
            self._require_args(args, 1, "uncheck <ref>")
            await self._locator(session, args[0]).uncheck()
            return await self._page_output(session, raw)
        if verb == "upload":
            self._require_args(args, 1, "upload <file>")
            await page.locator("input[type=file]").last.set_input_files([str((cwd / item).resolve()) for item in args])
            return await self._page_output(session, raw)
        if verb == "go-back":
            await page.go_back(wait_until="domcontentloaded")
            return await self._page_output(session, raw)
        if verb == "go-forward":
            await page.go_forward(wait_until="domcontentloaded")
            return await self._page_output(session, raw)
        if verb == "reload":
            await page.reload(wait_until="domcontentloaded")
            return await self._page_output(session, raw)
        if verb == "mousemove":
            self._require_args(args, 2, "mousemove <x> <y>")
            await page.mouse.move(float(args[0]), float(args[1]))
            return ("", [])
        if verb == "mousedown":
            await page.mouse.down(button=args[0] if args else "left")
            return ("", [])
        if verb == "mouseup":
            await page.mouse.up(button=args[0] if args else "left")
            return ("", [])
        if verb == "mousewheel":
            self._require_args(args, 2, "mousewheel <dx> <dy>")
            await page.mouse.wheel(float(args[0]), float(args[1]))
            return await self._page_output(session, raw)
        if verb == "tab-list":
            rows = [f"{index}: {tab.url} | {await tab.title()}" for index, tab in enumerate(session.context.pages)]
            return ("\n".join(rows) + ("\n" if rows else ""), [])
        if verb == "tab-new":
            new_page = await session.context.new_page()
            session.page = new_page
            self._wire_session_events(session)
            if args:
                await new_page.goto(self._normalize_url(args[0]), wait_until="domcontentloaded")
                await self._wait_for_view_stable(session)
            return await self._page_output(session, raw)
        if verb == "tab-select":
            self._require_args(args, 1, "tab-select <index>")
            session.page = session.context.pages[int(args[0])]
            await session.page.bring_to_front()
            return await self._page_output(session, raw)
        if verb == "tab-close":
            index = int(args[0]) if args else len(session.context.pages) - 1
            await session.context.pages[index].close()
            pages = session.context.pages
            session.page = pages[min(index, len(pages) - 1)] if pages else await session.context.new_page()
            return await self._page_output(session, raw)
        if verb.startswith("cookie-"):
            return await self._cookies(session, verb, args, raw)
        if verb.startswith("localstorage-"):
            return await self._web_storage(session, "localStorage", verb.replace("localstorage-", ""), args, raw)
        if verb.startswith("sessionstorage-"):
            return await self._web_storage(session, "sessionStorage", verb.replace("sessionstorage-", ""), args, raw)
        if verb == "state-save":
            filename = args[0] if args else "storage-state.json"
            path = cwd / filename
            await session.context.storage_state(path=str(path))
            return ("" if raw else f"Saved storage state to {path}\n", [{"type": "json", "path": str(path)}])
        if verb == "state-load":
            raise RuntimeError("state-load requires creating a new browser context; close and open with the saved state in a future turn.")
        if verb == "console":
            min_level = args[0] if args else "info"
            return (json.dumps(self._filter_console(session.console_messages, min_level), ensure_ascii=False, indent=None if raw else 2) + "\n", [])
        if verb == "network":
            return (json.dumps(session.network_events, ensure_ascii=False, indent=None if raw else 2) + "\n", [])
        if verb in {"dialog-accept", "dialog-dismiss", "route", "route-list", "unroute", "tracing-start", "tracing-stop", "video-start", "video-chapter", "video-stop", "run-code", "show", "attach"}:
            return ("" if raw else f"Command '{verb}' is acknowledged but not yet implemented by the Python runtime.\n", [])
        raise RuntimeError(f"Unsupported playwright-cli command '{verb}'.")

    async def _page_output(self, session: _BrowserSession, raw: bool) -> tuple[str, list[dict[str, str]]]:
        if raw:
            return ("", [])
        snapshot = await self._snapshot(session)
        return (self._with_page_header(session, f"### Snapshot\n{snapshot}", []), [])

    def _with_page_header(self, session: _BrowserSession, body: str, artifacts: list[dict[str, str]]) -> str:
        page = session.page
        artifact_lines = "\n".join(f"- {item['type']}: {item['path']}" for item in artifacts)
        return (
            "### Page\n"
            f"- Page URL: {page.url}\n"
            f"- Page Title: {getattr(page, '_last_known_title', '')}\n"
            f"{body}\n"
            f"{'### Artifacts' + chr(10) + artifact_lines + chr(10) if artifact_lines else ''}"
        )

    async def _snapshot(self, session: _BrowserSession, target: str = "", depth: int | None = None) -> str:
        page = session.page
        title = await page.title()
        setattr(page, "_last_known_title", title)
        selector = session.ref_map.get(target, target) if target else ""
        script = """
        ({ selector, depth }) => {
          const root = selector ? document.querySelector(selector) : document.body;
          if (!root) return [];
          const candidates = Array.from(root.querySelectorAll('a,button,input,textarea,select,[role],[aria-label],h1,h2,h3,label,summary'));
          const cssPath = (el) => {
            if (el.id) return '#' + CSS.escape(el.id);
            const parts = [];
            while (el && el.nodeType === Node.ELEMENT_NODE && el !== document.body) {
              let part = el.nodeName.toLowerCase();
              const parent = el.parentElement;
              if (parent) {
                const same = Array.from(parent.children).filter(c => c.nodeName === el.nodeName);
                if (same.length > 1) part += `:nth-of-type(${same.indexOf(el) + 1})`;
              }
              parts.unshift(part);
              el = parent;
            }
            return parts.length ? parts.join(' > ') : 'body';
          };
          return candidates.slice(0, 120).map((el, index) => ({
            ref: `e${index + 1}`,
            selector: cssPath(el),
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            name: el.getAttribute('aria-label') || el.getAttribute('name') || el.getAttribute('placeholder') || '',
            text: (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
            type: el.getAttribute('type') || '',
            href: el.getAttribute('href') || ''
          }));
        }
        """
        nodes = await page.evaluate(script, {"selector": selector, "depth": depth})
        session.ref_map = {node["ref"]: node["selector"] for node in nodes if node.get("ref") and node.get("selector")}
        lines = [f"- page: {page.url}", f"- title: {title}"]
        for node in nodes:
            label = node.get("text") or node.get("name") or node.get("href") or node.get("tag")
            attrs = []
            if node.get("role"):
                attrs.append(f"role={node['role']}")
            if node.get("type"):
                attrs.append(f"type={node['type']}")
            suffix = f" ({', '.join(attrs)})" if attrs else ""
            lines.append(f"- {node['ref']}: <{node.get('tag')}>{suffix} {label}".rstrip())
        return "\n".join(lines) + "\n"

    async def _semantic_snapshot(self, session: _BrowserSession, visible_only: bool = True) -> dict[str, Any]:
        page = session.page
        title = await page.title()
        setattr(page, "_last_known_title", title)
        aria_text = await self._aria_snapshot_text(page)
        roots = self._parse_aria_snapshot(aria_text)
        nodes = [node for root in roots for node in root.walk()]
        semantic_nodes: list[dict[str, Any]] = []
        ref_map: dict[str, str] = {}
        role_name_counts: dict[tuple[str, str], int] = {}
        ref_index = 1

        for node in nodes:
            if not self._is_semantic_element(node):
                continue
            key = (node.role, node.name)
            node.index = role_name_counts.get(key, 0)
            role_name_counts[key] = node.index + 1
            node.locator = self._locator_expression(node)
            if node.locator:
                node.visible = await self._is_locator_visible(session, node.locator)
            elif node.role == "text" and node.name:
                node.visible = await self._is_text_visible(session, node.name, node.index)
            else:
                node.visible = None
            if visible_only and node.visible is False:
                continue

            context_node = self._find_context_node(node)
            entity = self._extract_entity(context_node, exclude=node.name) if context_node else ""
            visual_context = await self._visual_context_for_locator(session, node) if node.locator else {}
            if not entity:
                entity = str(visual_context.get("entity") or "")
            context_payload = {
                "entity": entity,
                "container_role": context_node.role if context_node else visual_context.get("container_role"),
                "container_name": context_node.name if context_node else visual_context.get("container_name", ""),
                "path": self._semantic_path(node) or visual_context.get("path", []),
            }
            if visual_context:
                context_payload["visual"] = visual_context
            ref = f"e{ref_index}"
            ref_index += 1
            node.ref = ref
            if node.locator:
                ref_map[ref] = node.locator
            semantic_nodes.append(
                {
                    "id": self._stable_id(page.url, ref, node.role, node.name, entity),
                    "ref": ref,
                    "role": node.role,
                    "name": node.name,
                    "visible": node.visible,
                    "locator": node.locator,
                    "context": context_payload,
                    "action": await self._action_metadata(session, node) if node.locator else {},
                }
            )

        session.ref_map = {**session.ref_map, **ref_map}
        graph = self._build_semantic_graph(page.url, title, semantic_nodes)
        return {
            "status": "success",
            "source": "aria_snapshot",
            "page": {"url": page.url, "title": title},
            "metrics": {
                "aria_node_count": len(nodes),
                "semantic_node_count": len(semantic_nodes),
                "visible_only": visible_only,
            },
            "aria_snapshot": aria_text,
            "semantic_nodes": semantic_nodes,
            "graph": graph,
        }

    async def _explore(
        self,
        session: _BrowserSession,
        args: list[str],
        cwd: Path,
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        target_url = self._first_positional(args) or session.page.url
        if not target_url or target_url == "about:blank":
            raise ValueError("Usage: playwright-cli explore <url> [--max-pages=N] [--filename=file]")
        max_pages = int(self._option_value(args, "--max-pages") or 1)
        max_pages = max(1, min(max_pages, 12))
        max_interactions = int(self._option_value(args, "--max-interactions") or 0)
        max_interactions = max(0, min(max_interactions, 32))
        same_origin_only = self._option_value(args, "--same-origin-only") != "false"
        visible_only = "--include-hidden" not in args and self._option_value(args, "--visible-only") != "false"
        filename = self._option_value(args, "--filename") or "ui_explorer_graph.json"
        login_credentials = self._login_credentials_from_args(args)

        queue = [self._normalize_url(target_url)]
        visited: set[str] = set()
        explored_page_urls: set[str] = set()
        login_events: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        graph = {
            "pages": [],
            "elements": [],
            "entities": [],
            "edges": [],
        }

        while queue and len(pages) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            await session.page.goto(url, wait_until="domcontentloaded")
            stability = await self._wait_for_view_stable(session, max_seconds=12.0)
            login_probe = await self._wait_for_login_or_stable(session, max_seconds=8.0)
            if login_probe.get("required"):
                login_event: dict[str, Any] = {
                    "url": session.page.url,
                    "detected": True,
                    "reason": login_probe.get("reason"),
                    "performed": False,
                }
                if login_credentials:
                    login_event.update(await self._perform_detected_login(session, login_credentials))
                    login_event["post_login_stability"] = await self._wait_for_view_stable(session, max_seconds=12.0)
                login_events.append(login_event)
                stability = await self._wait_for_view_stable(session, max_seconds=8.0)
            snapshot = await self._semantic_snapshot(session, visible_only=visible_only)
            snapshot_page_url = str(snapshot.get("page", {}).get("url") or "")
            if snapshot_page_url in explored_page_urls:
                continue
            if snapshot_page_url:
                explored_page_urls.add(snapshot_page_url)
            page_record = {
                "url": snapshot["page"]["url"],
                "title": snapshot["page"]["title"],
                "status": "explored",
                "semantic_nodes": snapshot["semantic_nodes"],
                "metrics": snapshot["metrics"],
                "stability": stability,
                "scroll_states": [],
                "interactions": [],
            }
            pages.append(page_record)
            self._merge_graph(graph, snapshot["graph"])
            scroll_states = await self._explore_scroll_states(
                session=session,
                baseline_snapshot=snapshot,
                visible_only=visible_only,
            )
            page_record["scroll_states"] = scroll_states
            for scroll_state in scroll_states:
                scroll_graph = scroll_state.get("graph") if isinstance(scroll_state.get("graph"), dict) else {}
                self._merge_graph(graph, scroll_graph)
            if max_interactions > 0:
                interactions = await self._explore_interactions(
                    session=session,
                    baseline_snapshot=snapshot,
                    visible_only=visible_only,
                    max_interactions=max_interactions,
                )
                page_record["interactions"] = interactions
                for interaction in interactions:
                    interaction_graph = interaction.get("graph") if isinstance(interaction.get("graph"), dict) else {}
                    self._merge_graph(graph, interaction_graph)
                    interaction_target_url = str(interaction.get("target_url") or "").strip()
                    if interaction_target_url:
                        if same_origin_only and not self._same_origin(target_url, interaction_target_url):
                            continue
                        if (
                            interaction_target_url not in visited
                            and interaction_target_url not in explored_page_urls
                            and interaction_target_url not in queue
                            and len(pages) + len(queue) < max_pages
                        ):
                            queue.append(interaction_target_url)

            for node in snapshot["semantic_nodes"]:
                action = node.get("action") if isinstance(node.get("action"), dict) else {}
                href = str(action.get("href") or "").strip()
                if not href:
                    continue
                next_url = urljoin(snapshot["page"]["url"], href)
                if same_origin_only and not self._same_origin(target_url, next_url):
                    continue
                if (
                    next_url not in visited
                    and next_url not in explored_page_urls
                    and next_url not in queue
                    and len(pages) + len(queue) < max_pages
                ):
                    queue.append(next_url)

        app_map = {
            "status": "success",
            "source": "ui-explorer-agent",
            "entry_url": self._normalize_url(target_url),
            "page_count": len(pages),
            "same_origin_only": same_origin_only,
            "exploration_policy": {
                "uses_aria_snapshot": True,
                "does_not_assert": True,
                "does_not_validate": True,
                "follows_links_only": True,
                "login_is_detection_driven": True,
                "max_interactions_per_page": max_interactions,
            },
            "login_events": login_events,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "pages": pages,
            "graph": graph,
        }
        path = cwd / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(app_map, ensure_ascii=False, indent=2), encoding="utf-8")
        return app_map, [{"type": "ui-explorer-graph", "path": str(path)}]

    async def _aria_snapshot_text(self, page: Any) -> str:
        locator = page.locator("body")
        try:
            return await locator.aria_snapshot()
        except Exception:
            locator = page.locator(":root")
            return await locator.aria_snapshot()

    async def _wait_for_view_stable(
        self,
        session: _BrowserSession,
        max_seconds: float = 10.0,
        quiet_ms: int = 800,
        sample_ms: int = 200,
    ) -> dict[str, Any]:
        started = asyncio.get_running_loop().time()
        last_signature = ""
        last_change = started
        last_probe: dict[str, Any] = {}
        timed_out = False

        while True:
            now = asyncio.get_running_loop().time()
            try:
                probe = await session.page.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden'
                          && style.display !== 'none'
                          && rect.width > 0
                          && rect.height > 0;
                      };
                      const text = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').trim();
                      const controls = [...document.querySelectorAll('input, button, a[href], select, textarea, [role], summary')]
                        .filter(visible)
                        .map((el) => [
                          el.tagName,
                          el.getAttribute('role') || '',
                          el.getAttribute('type') || '',
                          el.getAttribute('placeholder') || '',
                          (el.innerText || el.getAttribute('aria-label') || el.getAttribute('href') || '').trim().slice(0, 80)
                        ].join('|'));
                      const passwordVisible = [...document.querySelectorAll('input[type="password"]')].some(visible);
                      return {
                        href: location.href,
                        title: document.title || '',
                        readyState: document.readyState,
                        textLength: text.length,
                        textHead: text.slice(0, 160),
                        textTail: text.slice(-160),
                        visibleControlCount: controls.length,
                        controlsHead: controls.slice(0, 30),
                        passwordVisible
                      };
                    }
                    """
                )
            except Exception as exc:
                return {
                    "status": "failed",
                    "error": str(exc),
                    "elapsed_ms": int((now - started) * 1000),
                }

            probe = probe if isinstance(probe, dict) else {}
            probe["pending_request_count"] = session.pending_request_count
            signature = json.dumps(
                {
                    "href": probe.get("href"),
                    "title": probe.get("title"),
                    "textLength": probe.get("textLength"),
                    "textHead": probe.get("textHead"),
                    "textTail": probe.get("textTail"),
                    "visibleControlCount": probe.get("visibleControlCount"),
                    "controlsHead": probe.get("controlsHead"),
                    "passwordVisible": probe.get("passwordVisible"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            content_ready = (
                bool(probe.get("passwordVisible"))
                or int(probe.get("visibleControlCount") or 0) > 0
                or int(probe.get("textLength") or 0) >= 20
            )
            if signature != last_signature:
                last_signature = signature
                last_change = now
            quiet_for_ms = int((now - last_change) * 1000)
            last_probe = probe
            if content_ready and quiet_for_ms >= quiet_ms:
                break
            if now - started >= max_seconds:
                timed_out = True
                break
            await session.page.wait_for_timeout(sample_ms)

        return {
            "status": "timeout" if timed_out else "stable",
            "elapsed_ms": int((asyncio.get_running_loop().time() - started) * 1000),
            "quiet_for_ms": int((asyncio.get_running_loop().time() - last_change) * 1000),
            "text_length": int(last_probe.get("textLength") or 0),
            "visible_control_count": int(last_probe.get("visibleControlCount") or 0),
            "password_visible": bool(last_probe.get("passwordVisible")),
            "pending_request_count": int(last_probe.get("pending_request_count") or 0),
        }

    async def _wait_for_login_or_stable(
        self,
        session: _BrowserSession,
        max_seconds: float = 8.0,
    ) -> dict[str, Any]:
        started = asyncio.get_running_loop().time()
        while True:
            probe = await self._detect_login_required(session)
            if probe.get("required"):
                probe["wait_elapsed_ms"] = int((asyncio.get_running_loop().time() - started) * 1000)
                return probe
            if asyncio.get_running_loop().time() - started >= max_seconds:
                probe["wait_elapsed_ms"] = int((asyncio.get_running_loop().time() - started) * 1000)
                return probe
            stability = await self._wait_for_view_stable(session, max_seconds=1.2, quiet_ms=500, sample_ms=150)
            probe = await self._detect_login_required(session)
            if probe.get("required"):
                probe["wait_elapsed_ms"] = int((asyncio.get_running_loop().time() - started) * 1000)
                probe["stability_before_detection"] = stability
                return probe
            if stability.get("status") == "stable" and not stability.get("password_visible"):
                probe["wait_elapsed_ms"] = int((asyncio.get_running_loop().time() - started) * 1000)
                probe["stability"] = stability
                return probe

    async def _detect_login_required(self, session: _BrowserSession) -> dict[str, Any]:
        script = """
        () => {
          const visible = (el) => {
            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
          };
          const password = [...document.querySelectorAll('input[type="password"]')].find(visible);
          if (!password) return {required: false};
          const form = password.closest('form') || document.body;
          const textInputs = [...form.querySelectorAll('input:not([type]), input[type="text"], input[type="email"], input[type="tel"], input[name], input[autocomplete]')].filter(visible);
          const submit = [...form.querySelectorAll('button, input[type="submit"], [role="button"]')].find(visible);
          return {
            required: true,
            reason: 'visible_password_input',
            has_username_input: textInputs.length > 0,
            has_submit: !!submit,
            title: document.title || '',
            url: location.href
          };
        }
        """
        try:
            result = await session.page.evaluate(script)
            return result if isinstance(result, dict) else {"required": False}
        except Exception as exc:
            return {"required": False, "error": str(exc)}

    async def _perform_detected_login(self, session: _BrowserSession, credentials: dict[str, str]) -> dict[str, Any]:
        username = str(credentials.get("username") or credentials.get("email") or credentials.get("account") or "")
        password = str(credentials.get("password") or "")
        if not username or not password:
            return {"performed": False, "reason": "missing_credentials"}
        page = session.page
        password_locator = page.locator("input[type='password']").first
        username_locator = page.locator(
            "input[type='email'], input[autocomplete='username'], input[name*='user' i], "
            "input[name*='email' i], input[name*='account' i], input[type='text'], input:not([type])"
        ).first
        try:
            await username_locator.fill(username, timeout=1500)
            await password_locator.fill(password, timeout=1500)
            submit = page.locator("button[type='submit'], input[type='submit'], button").first
            if await submit.count() > 0:
                await submit.click(timeout=3000)
            else:
                await password_locator.press("Enter", timeout=3000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                await page.wait_for_timeout(500)
            stability = await self._wait_for_view_stable(session, max_seconds=10.0)
            return {
                "performed": True,
                "username_field_detected": True,
                "password_field_detected": True,
                "stability": stability,
            }
        except Exception as exc:
            return {"performed": False, "reason": str(exc)}

    async def _explore_scroll_states(
        self,
        session: _BrowserSession,
        baseline_snapshot: dict[str, Any],
        visible_only: bool,
    ) -> list[dict[str, Any]]:
        baseline_nodes = baseline_snapshot.get("semantic_nodes") if isinstance(baseline_snapshot.get("semantic_nodes"), list) else []
        known_keys = {self._node_semantic_key(node) for node in baseline_nodes if isinstance(node, dict)}
        states: list[dict[str, Any]] = []
        try:
            scroll_info = await session.page.evaluate(
                """
                () => ({
                  scrollHeight: document.documentElement.scrollHeight || document.body.scrollHeight || 0,
                  clientHeight: document.documentElement.clientHeight || window.innerHeight || 0
                })
                """
            )
        except Exception:
            return states
        if not isinstance(scroll_info, dict):
            return states
        scroll_height = int(scroll_info.get("scrollHeight") or 0)
        client_height = int(scroll_info.get("clientHeight") or 0)
        if scroll_height <= client_height + 80:
            return states

        for position in [0.35, 0.7, 1.0]:
            try:
                await session.page.evaluate(
                    "(ratio) => window.scrollTo(0, Math.max(0, (document.documentElement.scrollHeight - window.innerHeight) * ratio))",
                    position,
                )
                await self._wait_for_view_stable(session, max_seconds=3.0, quiet_ms=450, sample_ms=150)
                snapshot = await self._semantic_snapshot(session, visible_only=visible_only)
                nodes = snapshot.get("semantic_nodes") if isinstance(snapshot.get("semantic_nodes"), list) else []
                new_nodes = [
                    node for node in nodes
                    if isinstance(node, dict) and self._node_semantic_key(node) not in known_keys
                ]
                for node in new_nodes:
                    known_keys.add(self._node_semantic_key(node))
                if new_nodes:
                    graph = self._build_semantic_graph(snapshot["page"]["url"], snapshot["page"]["title"], new_nodes)
                    states.append(
                        {
                            "position": position,
                            "new_node_count": len(new_nodes),
                            "new_nodes": new_nodes,
                            "graph": graph,
                        }
                    )
            except Exception as exc:
                states.append({"position": position, "error": str(exc), "new_node_count": 0, "graph": {}})
        try:
            await session.page.evaluate("() => window.scrollTo(0, 0)")
            await self._wait_for_view_stable(session, max_seconds=2.0, quiet_ms=350, sample_ms=150)
        except Exception:
            pass
        return states

    async def _explore_interactions(
        self,
        session: _BrowserSession,
        baseline_snapshot: dict[str, Any],
        visible_only: bool,
        max_interactions: int,
    ) -> list[dict[str, Any]]:
        interactions: list[dict[str, Any]] = []
        baseline_nodes = baseline_snapshot.get("semantic_nodes") if isinstance(baseline_snapshot.get("semantic_nodes"), list) else []
        baseline_keys = {self._node_semantic_key(node) for node in baseline_nodes if isinstance(node, dict)}
        baseline_url = str(baseline_snapshot.get("page", {}).get("url") or session.page.url)
        candidates = await self._interaction_candidates(session, baseline_nodes, baseline_url)
        no_progress_streak = 0

        for candidate in candidates[:max_interactions]:
            locator_expression = str(candidate.get("locator") or "")
            if not locator_expression:
                continue
            try:
                locator = self._locator(session, locator_expression)
                visibility_state = await self._locator_visibility_state(locator)
                if isinstance(visibility_state, dict):
                    if not (
                        visibility_state.get("visible")
                        and visibility_state.get("in_viewport")
                        and visibility_state.get("hit_target")
                    ):
                        continue
                await locator.click(timeout=1500)
                await self._wait_for_view_stable(session, max_seconds=4.0, quiet_ms=500, sample_ms=150)
                if session.page.url != baseline_url:
                    navigated_url = session.page.url
                    navigated_title = await session.page.title()
                    page_id = self._stable_id("page", navigated_url)
                    trigger_id = str(candidate.get("id") or "")
                    graph = {
                        "pages": [{"id": page_id, "url": navigated_url, "title": navigated_title}],
                        "elements": [],
                        "entities": [],
                        "edges": [
                            {
                                "type": "element_triggers_navigation",
                                "from": trigger_id,
                                "to": page_id,
                                "href": navigated_url,
                            }
                        ] if trigger_id else [],
                    }
                    interactions.append(
                        {
                            "trigger": {
                                "id": candidate.get("id"),
                                "ref": candidate.get("ref"),
                                "role": candidate.get("role"),
                                "name": candidate.get("name"),
                                "context": candidate.get("context"),
                                "source": candidate.get("source"),
                            },
                            "effect": "navigation",
                            "target_url": navigated_url,
                            "target_title": navigated_title,
                            "revealed_count": 0,
                            "revealed_nodes": [],
                            "graph": graph,
                        }
                    )
                    await session.page.goto(baseline_url, wait_until="domcontentloaded")
                    await self._wait_for_view_stable(session, max_seconds=6.0)
                    continue
                after_snapshot = await self._semantic_snapshot(session, visible_only=visible_only)
                after_nodes = after_snapshot.get("semantic_nodes") if isinstance(after_snapshot.get("semantic_nodes"), list) else []
                revealed_nodes = [
                    node for node in after_nodes
                    if isinstance(node, dict) and self._node_semantic_key(node) not in baseline_keys
                ]
                graph = self._build_interaction_graph(candidate, revealed_nodes)
                interactions.append(
                    {
                        "trigger": {
                            "id": candidate.get("id"),
                            "ref": candidate.get("ref"),
                            "role": candidate.get("role"),
                            "name": candidate.get("name"),
                            "context": candidate.get("context"),
                            "source": candidate.get("source"),
                        },
                        "effect": self._classify_interaction_effect(after_nodes, revealed_nodes),
                        "revealed_count": len(revealed_nodes),
                        "revealed_nodes": revealed_nodes,
                        "graph": graph,
                    }
                )
            except Exception as exc:
                interactions.append(
                    {
                        "trigger": {
                            "id": candidate.get("id"),
                            "role": candidate.get("role"),
                            "name": candidate.get("name"),
                            "source": candidate.get("source"),
                        },
                        "effect": "failed",
                        "error": str(exc),
                        "revealed_count": 0,
                        "revealed_nodes": [],
                        "graph": {"pages": [], "elements": [], "entities": [], "edges": []},
                    }
                )
            finally:
                try:
                    await session.page.keyboard.press("Escape")
                    await session.page.goto(baseline_url, wait_until="domcontentloaded")
                    await self._wait_for_view_stable(session, max_seconds=6.0)
                except Exception:
                    pass
            if interactions:
                last = interactions[-1]
                progressed = (
                    bool(last.get("target_url"))
                    or int(last.get("revealed_count") or 0) > 0
                    or str(last.get("effect") or "") in {"navigation", "dialog", "drawer", "tab-panel"}
                )
                no_progress_streak = 0 if progressed else no_progress_streak + 1
                if no_progress_streak >= 6 and len(interactions) >= 12:
                    break
        return interactions

    async def _interaction_candidates(self, session: _BrowserSession, nodes: list[Any], page_url: str) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            role = str(node.get("role") or "")
            name = str(node.get("name") or "")
            action = node.get("action") if isinstance(node.get("action"), dict) else {}
            if role not in {"button", "tab", "switch", "menuitem", "combobox", "textbox", "option"}:
                continue
            if action.get("href"):
                continue
            if self._is_unsafe_interaction_name(name) or self._is_static_metadata_name(name):
                continue
            key = (role, name, str((node.get("context") or {}).get("entity") or ""))
            if key in seen:
                continue
            seen.add(key)
            item = dict(node)
            item.setdefault("source", "aria")
            item["priority"] = self._candidate_priority(item)
            candidates.append(item)
        for node in await self._dom_interaction_candidates(session, page_url):
            key = (str(node.get("role") or ""), str(node.get("name") or ""), str((node.get("context") or {}).get("entity") or ""))
            if key in seen:
                continue
            seen.add(key)
            node["priority"] = self._candidate_priority(node)
            candidates.append(node)
        candidates.sort(
            key=lambda item: (
                -int(item.get("priority") or 0),
                str(item.get("source") or ""),
                len(str(item.get("name") or "")),
            )
        )
        return candidates

    def _candidate_priority(self, candidate: dict[str, Any]) -> int:
        role = str(candidate.get("role") or "").lower()
        name = str(candidate.get("name") or "")
        source = str(candidate.get("source") or "")
        context = candidate.get("context") if isinstance(candidate.get("context"), dict) else {}
        container = str(context.get("container_name") or "").lower()
        text = f"{name} {container}".lower()
        priority = 0
        if role in {"tab", "menuitem", "link"}:
            priority += 90
        if role in {"button", "switch"}:
            priority += 70
        if role in {"combobox", "textbox", "option"}:
            priority += 55
        if source == "dom-clickable":
            priority += 10
        if re.search(r"nav|menu|tab|side|route|breadcrumb|module|section", text):
            priority += 50
        if re.search(r"course|chapter|lesson|order|product|cart|user|profile|setting|report|task|project", text):
            priority += 35
        if re.search(r"课程|章节|作业|错题|实验|能力|订单|商品|购物车|用户|设置|报表|任务|项目|详情|列表", name):
            priority += 35
        if re.search(r"进入|查看|详情|展开|更多|切换|选择|搜索|筛选|下一页|分页", name):
            priority += 25
        if 1 <= len(name) <= 24:
            priority += 15
        if len(name) > 80:
            priority -= 35
        if len(name) > 140:
            priority -= 80
        return priority

    async def _dom_interaction_candidates(self, session: _BrowserSession, page_url: str) -> list[dict[str, Any]]:
        try:
            rows = await session.page.evaluate(
                """
                () => {
                  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const visible = (el) => {
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    const inViewport = rect.width > 4
                      && rect.height > 4
                      && rect.bottom >= 0
                      && rect.right >= 0
                      && rect.top <= window.innerHeight
                      && rect.left <= window.innerWidth;
                    if (!inViewport) return false;
                    const clamp = (value, lower, upper) => Math.min(upper, Math.max(lower, value));
                    const cx = clamp(rect.left + rect.width / 2, 0, Math.max(0, window.innerWidth - 1));
                    const cy = clamp(rect.top + rect.height / 2, 0, Math.max(0, window.innerHeight - 1));
                    const top = document.elementFromPoint(cx, cy);
                    const hitTarget = !!top && (top === el || el.contains(top) || top.contains(el));
                    return hitTarget
                      && style.visibility !== 'hidden'
                      && style.display !== 'none'
                      && style.opacity !== '0';
                  };
                  const cssPath = (el) => {
                    if (el.id) return `#${CSS.escape(el.id)}`;
                    const dataAttrs = ['data-testid', 'data-test', 'data-cy', 'name', 'aria-label', 'title'];
                    for (const attr of dataAttrs) {
                      const value = el.getAttribute(attr);
                      if (value) return `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(value)}"]`;
                    }
                    const parts = [];
                    let current = el;
                    for (let depth = 0; current && current.nodeType === 1 && depth < 5; depth += 1, current = current.parentElement) {
                      let part = current.tagName.toLowerCase();
                      const className = normalize(current.className).split(' ').filter(Boolean)[0];
                      if (className) part += `.${CSS.escape(className)}`;
                      const parent = current.parentElement;
                      if (parent) {
                        const siblings = [...parent.children].filter((item) => item.tagName === current.tagName);
                        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
                      }
                      parts.unshift(part);
                    }
                    return parts.join(' > ');
                  };
                  const contextFor = (el, name) => {
                    const skip = new Set([name, '点击进入', '查看课程简介', '收起课程简介', '展开全部', '收起全部', '退出登录', '切换']);
                    let current = el.parentElement;
                    for (let depth = 0; current && depth < 7; depth += 1, current = current.parentElement) {
                      const text = normalize(current.innerText || current.textContent || '');
                      const className = normalize(current.className);
                      const rect = current.getBoundingClientRect();
                      if (!text || text.length <= name.length + 2 || rect.width <= 0 || rect.height <= 0) continue;
                      if (/card|item|course|list|row|panel|menu|nav|tab|chapter|section|box/i.test(className) || text.length < 500) {
                        let cleaned = text;
                        for (const value of skip) cleaned = cleaned.split(value).join(' ');
                        const entity = cleaned.split(/\\n|\\r| {2,}/).map(normalize).find((line) => line && line.length <= 120) || '';
                        return { entity, container_role: 'visual-container', container_name: className || current.tagName.toLowerCase(), path: ['dom-clickable'] };
                      }
                    }
                    return { entity: '', container_role: 'dom-clickable', container_name: '', path: ['dom-clickable'] };
                  };
                  const all = [...document.querySelectorAll('a[href],button,input,select,textarea,[role],[onclick],[tabindex],summary,li,div,span')];
                  const rows = [];
                  const seen = new Set();
                  const scoreFor = (el, role, name) => {
                    const tag = el.tagName.toLowerCase();
                    const className = normalize(el.className).toLowerCase();
                    const text = `${name} ${className} ${role}`.toLowerCase();
                    let score = 0;
                    if (['tab', 'menuitem', 'link'].includes(role)) score += 90;
                    if (['button', 'switch'].includes(role)) score += 70;
                    if (['combobox', 'textbox', 'option'].includes(role)) score += 55;
                    if (tag === 'a' || tag === 'button') score += 20;
                    if (/nav|menu|tab|side|route|breadcrumb|module|section/.test(text)) score += 50;
                    if (/course|chapter|lesson|order|product|cart|user|profile|setting|report|task|project/.test(text)) score += 35;
                    if (/课程|章节|作业|错题|实验|能力|订单|商品|购物车|用户|设置|报表|任务|项目|详情|列表/.test(name)) score += 35;
                    if (/进入|查看|详情|展开|更多|切换|选择|搜索|筛选|下一页|分页/.test(name)) score += 25;
                    if (name.length > 80) score -= 30;
                    return score;
                  };
                  for (const el of all) {
                    if (!visible(el)) continue;
                    const style = getComputedStyle(el);
                    const tag = el.tagName.toLowerCase();
                    const roleAttr = normalize(el.getAttribute('role'));
                    const className = normalize(el.className);
                    const classSignal = /click|btn|button|tab|menu|nav|link/i.test(className);
                    let clickableAncestor = false;
                    for (let parent = el.parentElement, depth = 0; parent && depth < 4; parent = parent.parentElement, depth += 1) {
                      const parentRole = normalize(parent.getAttribute('role'));
                      const parentClass = normalize(parent.className);
                      if (parent.onclick
                        || Number(parent.getAttribute('tabindex')) >= 0
                        || ['button', 'tab', 'menuitem', 'option', 'switch', 'combobox', 'link'].includes(parentRole)
                        || /click|btn|button|tab|menu|nav|link/i.test(parentClass)) {
                        clickableAncestor = true;
                        break;
                      }
                    }
                    const directSignal = !!roleAttr || !!el.onclick || Number(el.getAttribute('tabindex')) >= 0;
                    const hasClickableSignal = tag === 'a'
                      || tag === 'button'
                      || ['input', 'select', 'textarea', 'summary'].includes(tag)
                      || ['button', 'tab', 'menuitem', 'option', 'switch', 'combobox', 'link'].includes(roleAttr)
                      || el.onclick
                      || Number(el.getAttribute('tabindex')) >= 0
                      || style.cursor === 'pointer'
                      || classSignal;
                    if (!hasClickableSignal) continue;
                    const name = normalize(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || el.getAttribute('placeholder') || el.value || el.getAttribute('href'));
                    if (!name || name.length > 140) continue;
                    if (['div', 'span', 'li'].includes(tag) && !directSignal && !classSignal) continue;
                    if (['div', 'span', 'li'].includes(tag)
                      && clickableAncestor
                      && !directSignal
                      && !/tab|menu|nav|link|btn|button/i.test(className)
                      && !/详情|更多|进入|查看|展开|切换|选择|搜索|筛选|下一页|分页/.test(name)) continue;
                    if (name.length <= 2 && !/详情|更多|进入|设置|AI|ai/.test(name)) continue;
                    const nestedControls = el.querySelectorAll('button,a,input,select,textarea,[role],[onclick],[tabindex]').length;
                    const nativeControl = tag === 'a' || tag === 'button' || ['input', 'select', 'textarea', 'summary'].includes(tag);
                    if (!nativeControl && nestedControls > 3 && name.length > 80) continue;
                    const locator = cssPath(el);
                    if (!locator || seen.has(locator)) continue;
                    seen.add(locator);
                    const role = roleAttr || (tag === 'a' ? 'link' : tag === 'input' || tag === 'textarea' ? 'textbox' : tag === 'select' ? 'combobox' : tag === 'li' ? 'menuitem' : 'button');
                    rows.push({ role, name, locator, tag, score: scoreFor(el, role, name), context: contextFor(el, name) });
                    if (rows.length >= 60) break;
                  }
                  rows.sort((a, b) => b.score - a.score || a.name.length - b.name.length);
                  return rows;
                }
                """
            )
        except Exception:
            return []
        candidates: list[dict[str, Any]] = []
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            locator = str(row.get("locator") or "").strip()
            if not name or not locator or self._is_unsafe_interaction_name(name) or self._is_static_metadata_name(name):
                continue
            role = str(row.get("role") or "button").strip() or "button"
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            candidates.append(
                {
                    "id": self._stable_id(page_url, "dom", index, role, name, context.get("entity") or ""),
                    "ref": "",
                    "role": role,
                    "name": name,
                    "visible": True,
                    "locator": locator,
                    "context": context,
                    "action": {},
                    "source": "dom-clickable",
                    "dom_score": row.get("score"),
                }
            )
        return candidates

    def _is_unsafe_interaction_name(self, name: str) -> bool:
        lowered = name.lower()
        skip_tokens = [
            "logout",
            "log out",
            "sign out",
            "delete",
            "remove",
            "drop",
            "destroy",
            "pay",
            "submit",
            "\u9000\u51fa",
            "\u767b\u51fa",
            "\u6ce8\u9500",
            "\u5220\u9664",
            "\u79fb\u9664",
            "\u652f\u4ed8",
            "\u63d0\u4ea4",
        ]
        return any(token in lowered for token in skip_tokens)

    def _is_static_metadata_name(self, name: str) -> bool:
        compact = re.sub(r"\s+", "", name)
        if not compact:
            return True
        action_pattern = r"进入|查看|详情|展开|更多|切换|选择|搜索|筛选|下一页|分页|编辑|添加|新建|打开"
        if re.search(action_pattern, compact):
            return False
        if len(compact) <= 2 and not re.search(r"AI|ai", compact):
            return True
        metadata_pattern = (
            r"教师|人数|学期|日期|时间|编号|价格|库存|状态|进度|已学|总章节|"
            r"手机号|邮箱|地址|评分|浏览量|创建人|创建时间|更新时间|授课|选课"
        )
        if re.search(metadata_pattern, compact) and (len(compact) <= 40 or "：" in compact or ":" in compact):
            return True
        if re.fullmatch(r"[\d.:%/年月日-]+", compact):
            return True
        return False

    def _build_interaction_graph(self, trigger: dict[str, Any], revealed_nodes: list[dict[str, Any]]) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        entities: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        trigger_id = str(trigger.get("id") or "")
        for node in revealed_nodes:
            element_id = str(node.get("id") or "")
            if not element_id:
                continue
            elements.append(
                {
                    "id": element_id,
                    "role": node.get("role"),
                    "name": node.get("name"),
                    "ref": node.get("ref"),
                    "locator": node.get("locator"),
                    "context": node.get("context") or {},
                    "visible": node.get("visible"),
                    "discovered_by": "interaction",
                    "trigger_id": trigger_id,
                }
            )
            if trigger_id:
                edges.append({"type": "element_reveals_element", "from": trigger_id, "to": element_id})
            context = node.get("context") if isinstance(node.get("context"), dict) else {}
            entity_name = str(context.get("entity") or "").strip()
            if entity_name:
                entity_id = self._stable_id("entity", "interaction", entity_name)
                entities[entity_id] = {"id": entity_id, "name": entity_name}
                edges.append({"type": "element_belongs_to_entity", "from": element_id, "to": entity_id})
        return {"pages": [], "elements": elements, "entities": list(entities.values()), "edges": edges}

    def _classify_interaction_effect(self, nodes: list[Any], revealed_nodes: list[dict[str, Any]]) -> str:
        roles = {str(node.get("role") or "") for node in revealed_nodes if isinstance(node, dict)}
        names = " ".join(str(node.get("name") or "") for node in revealed_nodes if isinstance(node, dict))
        if "dialog" in roles or "弹窗" in names:
            return "dialog"
        if "抽屉" in names or "drawer" in names.lower():
            return "drawer"
        if "tab" in roles:
            return "tab-panel"
        if revealed_nodes:
            return "expanded-content"
        return "no-new-semantic-node"

    def _node_semantic_key(self, node: dict[str, Any]) -> tuple[str, str, str, str]:
        context = node.get("context") if isinstance(node.get("context"), dict) else {}
        return (
            str(node.get("role") or ""),
            str(node.get("name") or ""),
            str(context.get("entity") or ""),
            "/".join(str(item) for item in context.get("path") or []),
        )

    def _parse_aria_snapshot(self, text: str) -> list[_AriaNode]:
        roots: list[_AriaNode] = []
        stack: list[tuple[int, _AriaNode]] = []
        for raw_line in text.splitlines():
            if not raw_line.strip():
                continue
            stripped = raw_line.lstrip(" ")
            if not stripped.startswith("- "):
                continue
            indent = len(raw_line) - len(stripped)
            role, name = self._parse_aria_line(stripped[2:].strip())
            if not role:
                continue
            node = _AriaNode(role=role, name=name, raw=stripped[2:].strip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if stack:
                parent = stack[-1][1]
                node.parent = parent
                parent.children.append(node)
            else:
                roots.append(node)
            stack.append((indent, node))
        return roots

    def _parse_aria_line(self, value: str) -> tuple[str, str]:
        value = value.rstrip(":").strip()
        if not value or value.startswith("/"):
            return "", ""
        colon_match = re.match(r"^([a-zA-Z][\w-]*):\s*(.*)$", value)
        if colon_match:
            return colon_match.group(1).lower(), colon_match.group(2).strip().strip("\"'")
        match = re.match(r"^([a-zA-Z][\w-]*)(?:\s+(['\"])(.*?)\2)?", value)
        if not match:
            return "", ""
        return match.group(1).lower(), (match.group(3) or "").strip()

    def _is_semantic_element(self, node: _AriaNode) -> bool:
        if node.role in self._interactive_roles():
            return True
        return node.role in {"heading", "text", "listitem", "row", "group", "region", "article", "form", "dialog"}

    def _interactive_roles(self) -> set[str]:
        return {
            "button",
            "link",
            "textbox",
            "checkbox",
            "radio",
            "combobox",
            "listbox",
            "menuitem",
            "tab",
            "switch",
            "option",
            "slider",
            "spinbutton",
        }

    def _find_context_node(self, node: _AriaNode) -> _AriaNode | None:
        parent = node.parent
        context_roles = {"listitem", "row", "group", "region", "article", "form", "dialog"}
        while parent:
            if parent.role in context_roles:
                return parent
            parent = parent.parent
        return node.parent

    def _extract_entity(self, container: _AriaNode | None, exclude: str = "") -> str:
        if container is None:
            return ""
        if container.name and container.name != exclude and container.role not in {"row", "listitem"}:
            return container.name
        for child in container.walk():
            if child is container:
                continue
            if child.name and child.name != exclude and child.role in {"heading", "text", "link"}:
                return child.name
        for child in container.walk():
            if child is not container and child.name and child.name != exclude:
                return child.name
        return ""

    def _semantic_path(self, node: _AriaNode) -> list[str]:
        path: list[str] = []
        parent = node.parent
        while parent:
            label = parent.role if not parent.name else f"{parent.role}:{parent.name}"
            path.append(label)
            parent = parent.parent
        path.reverse()
        return path

    def _locator_expression(self, node: _AriaNode) -> str:
        if node.role not in self._interactive_roles():
            return ""
        role = json.dumps(node.role, ensure_ascii=False)
        if node.name:
            name = json.dumps(node.name, ensure_ascii=False)
            return f"getByRole({role}, {{name: {name}}}).nth({node.index})"
        return f"getByRole({role}).nth({node.index})"

    async def _locator_visibility_state(self, locator: Any) -> dict[str, Any] | None:
        try:
            if not await locator.is_visible(timeout=250):
                return {"visible": False, "in_viewport": False, "hit_target": False}
            return await locator.evaluate(
                """
                (el) => {
                  const style = getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  const inViewport = rect.width > 0
                    && rect.height > 0
                    && rect.bottom >= 0
                    && rect.right >= 0
                    && rect.top <= window.innerHeight
                    && rect.left <= window.innerWidth;
                  if (!inViewport) {
                    return { visible: true, in_viewport: false, hit_target: false };
                  }
                  const clamp = (value, lower, upper) => Math.min(upper, Math.max(lower, value));
                  const cx = clamp(rect.left + rect.width / 2, 0, Math.max(0, window.innerWidth - 1));
                  const cy = clamp(rect.top + rect.height / 2, 0, Math.max(0, window.innerHeight - 1));
                  const top = document.elementFromPoint(cx, cy);
                  const hitTarget = !!top && (top === el || el.contains(top) || top.contains(el));
                  return {
                    visible: style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0',
                    in_viewport: inViewport,
                    hit_target: hitTarget,
                  };
                }
                """,
                timeout=350,
            )
        except Exception:
            return None

    async def _is_locator_visible(self, session: _BrowserSession, locator_expression: str) -> bool | None:
        try:
            state = await self._locator_visibility_state(self._locator(session, locator_expression))
            if not isinstance(state, dict):
                return None
            return bool(state.get("visible")) and bool(state.get("in_viewport")) and bool(state.get("hit_target"))
        except Exception:
            return None

    async def _is_text_visible(self, session: _BrowserSession, name: str, index: int) -> bool | None:
        try:
            state = await self._locator_visibility_state(session.page.get_by_text(name).nth(index))
            if not isinstance(state, dict):
                return None
            return bool(state.get("visible")) and bool(state.get("in_viewport")) and bool(state.get("hit_target"))
        except Exception:
            return None

    async def _action_metadata(self, session: _BrowserSession, node: _AriaNode) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        try:
            locator = self._locator(session, node.locator)
            visibility_state = await self._locator_visibility_state(locator)
            if isinstance(visibility_state, dict):
                metadata["visibility_state"] = visibility_state
            box = await locator.bounding_box(timeout=250)
            if box:
                metadata["bounding_box"] = box
            if node.role == "link":
                href = await locator.get_attribute("href", timeout=250)
                if href:
                    metadata["href"] = href
        except Exception:
            pass
        return metadata

    async def _visual_context_for_locator(self, session: _BrowserSession, node: _AriaNode) -> dict[str, Any]:
        try:
            return await self._locator(session, node.locator).evaluate(
                """
                (el, payload) => {
                  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                  const buttonName = normalize(payload.name);
                  const controlLabels = [
                    buttonName,
                    '点击进入',
                    '查看课程简介',
                    '收起课程简介',
                    '展开全部',
                    '收起全部',
                    '退出登录',
                    '切换'
                  ].filter(Boolean);
                  const skip = new Set(controlLabels);
                  const viewportArea = Math.max(1, window.innerWidth * window.innerHeight);
                  const candidates = [];
                  let current = el.parentElement;
                  for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                    const rect = current.getBoundingClientRect();
                    const text = normalize(current.innerText || current.textContent || '');
                    if (!text || text.length <= buttonName.length + 2 || rect.width <= 0 || rect.height <= 0) {
                      continue;
                    }
                    const className = normalize(current.className);
                    const role = normalize(current.getAttribute('role'));
                    const controlCount = current.querySelectorAll('button,a,input,select,textarea,[role="button"]').length;
                    let score = 0;
                    if (/card|item|course|list|row|panel|content|box/i.test(className)) score += 5;
                    if (text.length <= 600) score += 3;
                    if (controlCount > 0 && controlCount <= 4) score += 2;
                    if (rect.width * rect.height < viewportArea * 0.75) score += 2;
                    score -= depth * 0.2;
                    candidates.push({ depth, score, tag: current.tagName.toLowerCase(), role, className, text });
                  }
                  candidates.sort((a, b) => b.score - a.score || a.depth - b.depth);
                  const picked = candidates[0];
                  if (!picked) return {};
                  let cleanedText = String(picked.text || '');
                  for (const label of controlLabels) {
                    cleanedText = cleanedText.split(label).join(' ');
                  }
                  const lines = cleanedText
                    .split(/\\n|\\r| {2,}/)
                    .map((line) => normalize(line))
                    .filter(Boolean)
                    .filter((line) => !skip.has(line))
                    .filter((line) => !/^\\d+%$/.test(line));
                  const entity = (lines.find((line) => line.length <= 120) || lines[0] || '').slice(0, 120);
                  return {
                    entity,
                    container_role: picked.role || 'visual-container',
                    container_name: picked.className || picked.tag,
                    path: ['visual-container'],
                    text: picked.text.slice(0, 500),
                    confidence: entity ? 'fallback' : 'none'
                  };
                }
                """,
                {"name": node.name},
                timeout=500,
            )
        except Exception:
            return {}

    def _build_semantic_graph(self, url: str, title: str, semantic_nodes: list[dict[str, Any]]) -> dict[str, Any]:
        page_id = self._stable_id("page", url)
        page_vertex = {"id": page_id, "url": url, "title": title}
        elements: list[dict[str, Any]] = []
        entities_by_key: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for node in semantic_nodes:
            element_id = str(node.get("id") or self._stable_id(url, node.get("role"), node.get("name")))
            elements.append(
                {
                    "id": element_id,
                    "page_id": page_id,
                    "role": node.get("role"),
                    "name": node.get("name"),
                    "ref": node.get("ref"),
                    "locator": node.get("locator"),
                    "context": node.get("context") or {},
                    "visible": node.get("visible"),
                }
            )
            edges.append({"type": "page_contains_element", "from": page_id, "to": element_id})
            context = node.get("context") if isinstance(node.get("context"), dict) else {}
            entity_name = str(context.get("entity") or "").strip()
            if entity_name:
                entity_id = self._stable_id("entity", url, entity_name)
                entities_by_key[entity_id] = {"id": entity_id, "name": entity_name, "source_page_id": page_id}
                edges.append({"type": "element_belongs_to_entity", "from": element_id, "to": entity_id})
            action = node.get("action") if isinstance(node.get("action"), dict) else {}
            href = str(action.get("href") or "").strip()
            if href:
                target_page_id = self._stable_id("page", urljoin(url, href))
                edges.append({"type": "element_triggers_navigation", "from": element_id, "to": target_page_id, "href": href})
        return {
            "pages": [page_vertex],
            "elements": elements,
            "entities": list(entities_by_key.values()),
            "edges": edges,
        }

    def _merge_graph(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        for key in ["pages", "elements", "entities", "edges"]:
            existing = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in target[key]}
            for item in source.get(key, []):
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker not in existing:
                    target[key].append(item)
                    existing.add(marker)

    def _stable_id(self, *parts: Any) -> str:
        digest = hashlib.sha1(":".join(str(part) for part in parts).encode("utf-8")).hexdigest()
        return f"ui_{digest[:20]}"

    def _same_origin(self, base_url: str, link: str) -> bool:
        base = urlparse(self._normalize_url(base_url))
        other = urlparse(self._normalize_url(link))
        return (base.scheme, base.netloc) == (other.scheme, other.netloc)

    def _locator(self, session: _BrowserSession, target: str) -> Any:
        selector = session.ref_map.get(target, target)
        page = session.page
        role_match = re.fullmatch(
            r"getByRole\((['\"])(.*?)\1(?:\s*,\s*\{\s*name:\s*(['\"])(.*?)\3\s*\})?\)(?:\.nth\((\d+)\))?",
            selector,
        )
        if role_match:
            locator = page.get_by_role(role_match.group(2), name=role_match.group(4) or None)
            if role_match.group(5) is not None:
                locator = locator.nth(int(role_match.group(5)))
            return locator
        test_id_match = re.fullmatch(r"getByTestId\(['\"]([^'\"]+)['\"]\)", selector)
        if test_id_match:
            return page.get_by_test_id(test_id_match.group(1))
        return page.locator(selector).first

    async def _cookies(self, session: _BrowserSession, verb: str, args: list[str], raw: bool) -> tuple[str, list[dict[str, str]]]:
        action = verb.replace("cookie-", "")
        if action == "list":
            domain = self._option_value(args, "--domain")
            cookies = await session.context.cookies()
            if domain:
                cookies = [item for item in cookies if domain in item.get("domain", "")]
            return (json.dumps(cookies, ensure_ascii=False, indent=None if raw else 2) + "\n", [])
        if action == "get":
            self._require_args(args, 1, "cookie-get <name>")
            cookies = await session.context.cookies()
            value = next((item.get("value", "") for item in cookies if item.get("name") == args[0]), "")
            return (value + ("\n" if value else ""), [])
        if action == "set":
            self._require_args(args, 2, "cookie-set <name> <value>")
            url = session.page.url if session.page.url.startswith("http") else "https://example.com"
            cookie = {"name": args[0], "value": args[1], "url": url}
            domain = self._option_value(args, "--domain")
            if domain:
                cookie.pop("url", None)
                cookie["domain"] = domain
                cookie["path"] = "/"
            cookie["httpOnly"] = "--httpOnly" in args
            cookie["secure"] = "--secure" in args
            await session.context.add_cookies([cookie])
            return ("", [])
        if action == "delete":
            self._require_args(args, 1, "cookie-delete <name>")
            await session.context.clear_cookies(name=args[0])
            return ("", [])
        if action == "clear":
            await session.context.clear_cookies()
            return ("", [])
        raise RuntimeError(f"Unsupported cookie command '{verb}'.")

    async def _web_storage(
        self,
        session: _BrowserSession,
        storage_name: str,
        action: str,
        args: list[str],
        raw: bool,
    ) -> tuple[str, list[dict[str, str]]]:
        if action == "list":
            result = await session.page.evaluate(f"JSON.stringify(Object.fromEntries(Object.entries({storage_name})))")
            return (result + "\n", [])
        if action == "get":
            self._require_args(args, 1, f"{storage_name}-get <key>")
            result = await session.page.evaluate(f"{storage_name}.getItem({json.dumps(args[0])})")
            return ((result or "") + ("\n" if result else ""), [])
        if action == "set":
            self._require_args(args, 2, f"{storage_name}-set <key> <value>")
            await session.page.evaluate(f"{storage_name}.setItem({json.dumps(args[0])}, {json.dumps(args[1])})")
            return ("", [])
        if action == "delete":
            self._require_args(args, 1, f"{storage_name}-delete <key>")
            await session.page.evaluate(f"{storage_name}.removeItem({json.dumps(args[0])})")
            return ("", [])
        if action == "clear":
            await session.page.evaluate(f"{storage_name}.clear()")
            return ("", [])
        raise RuntimeError(f"Unsupported storage command '{storage_name}-{action}'.")

    async def _close_session(self, session_name: str) -> None:
        session = self._sessions.pop(session_name, None)
        if not session:
            return
        try:
            await session.context.close()
        finally:
            if session.browser:
                await session.browser.close()
            await session.playwright.stop()

    def _success(
        self,
        command: list[str],
        stdout: str,
        artifacts: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return PlaywrightCliCommandResult(
            command=command,
            exit_code=0,
            stdout=stdout,
            artifacts=artifacts or [],
        ).model_dump()

    def _list_sessions(self, raw: bool) -> str:
        data = [
            {
                "name": name,
                "url": session.page.url,
                "output_dir": str(session.output_dir),
                "persistent": session.persistent,
            }
            for name, session in sorted(self._sessions.items())
        ]
        if raw:
            return json.dumps(data, ensure_ascii=False) + "\n"
        if not data:
            return "No active browser sessions.\n"
        return "\n".join(f"{item['name']}: {item['url']} ({item['output_dir']})" for item in data) + "\n"

    def _format_command(self, session_name: str, args: list[str], raw: bool) -> list[str]:
        command = ["python-playwright-cli", f"-s={session_name}"]
        if raw:
            command.append("--raw")
        command.extend(args)
        return command

    def _help_text(self) -> str:
        return (
            "python-playwright-cli commands:\n"
            "  open [url], goto <url>, close, delete-data, list, close-all\n"
            "  snapshot [ref] [--filename=file] [--depth=N], screenshot [ref] [--filename=file]\n"
            "  semantic-snapshot [--filename=file] [--include-hidden], explore <url> [--max-pages=N]\n"
            "  click <ref>, dblclick <ref>, fill <ref> <text> [--submit], type <text>, press <key>\n"
            "  eval <expression> [ref], resize <w> <h>, mousewheel <dx> <dy>\n"
            "  tab-list, tab-new [url], tab-select <index>, tab-close [index]\n"
        )

    def _strip_global_options(self, args: list[str]) -> tuple[list[str], bool]:
        stripped: list[str] = []
        raw = False
        for arg in args:
            if arg == "--raw":
                raw = True
            elif arg.startswith("-s="):
                continue
            else:
                stripped.append(arg)
        return stripped, raw

    def _parse_snapshot_args(self, args: list[str]) -> tuple[str, str | None, int | None]:
        target = ""
        filename = self._option_value(args, "--filename")
        depth_value = self._option_value(args, "--depth")
        for arg in args:
            if not arg.startswith("--"):
                target = arg
                break
        return target, filename, int(depth_value) if depth_value else None

    def _parse_target_filename(self, args: list[str], default_name: str) -> tuple[str, str]:
        filename = self._option_value(args, "--filename") or default_name
        target = ""
        for arg in args:
            if not arg.startswith("--"):
                target = arg
                break
        return target, filename

    def _option_value(self, args: list[str], name: str) -> str | None:
        prefix = f"{name}="
        for index, arg in enumerate(args):
            if arg.startswith(prefix):
                return arg[len(prefix) :]
            if arg == name and index + 1 < len(args):
                return args[index + 1]
        return None

    def _login_credentials_from_args(self, args: list[str]) -> dict[str, str]:
        username = self._option_value(args, "--login-username") or self._option_value(args, "--username") or ""
        password = self._option_value(args, "--login-password") or self._option_value(args, "--password") or ""
        if not username and not password:
            return {}
        return {"username": username, "password": password}

    def _first_positional(self, args: list[str]) -> str:
        for arg in args:
            if not arg.startswith("--"):
                return arg
        return ""

    def _normalize_url(self, value: str) -> str:
        if value.startswith("data:text/html,"):
            payload = value[len("data:text/html,") :]
            if any(ord(ch) > 127 for ch in payload):
                return "data:text/html;charset=utf-8," + quote(payload, safe="/:=?&%#[]@!$&'()*+,;<>\" ")
        if value.startswith(("http://", "https://", "file://", "data:", "about:")):
            return value
        return f"https://{value}"

    def _require_args(self, args: list[str], count: int, usage: str) -> None:
        positional = [arg for arg in args if not arg.startswith("--")]
        if len(positional) < count:
            raise ValueError(f"Usage: playwright-cli {usage}")

    def _format_value(self, value: Any, raw: bool) -> str:
        if isinstance(value, str):
            return value + ("\n" if value else "")
        return json.dumps(value, ensure_ascii=False, indent=None if raw else 2) + "\n"

    def _filter_console(self, messages: list[dict[str, Any]], min_level: str) -> list[dict[str, Any]]:
        order = {"debug": 0, "info": 1, "log": 1, "warning": 2, "error": 3}
        threshold = order.get(min_level, 1)
        return [item for item in messages if order.get(str(item.get("type")), 1) >= threshold]

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
        return slug[:96] or "default"
