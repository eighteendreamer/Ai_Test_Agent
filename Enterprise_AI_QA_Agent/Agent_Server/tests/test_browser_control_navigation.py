from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from types import SimpleNamespace

import pytest

from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.core.config import get_settings


class CounterPage:
    def __init__(self) -> None:
        self.count = 0

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = b'''<!doctype html><html><meta charset="utf-8"><title>Browser control session</title>
<button id="increment" onclick="fetch('/increment',{method:'POST'}).then(r=>r.json()).then(d=>count.textContent=d.count)">Increment</button>
<output id="count">0</output></html>'''
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/increment":
                    self.send_error(404)
                    return
                owner.count += 1
                body = json.dumps({"count": owner.count}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.mark.asyncio
async def test_navigate_and_follow_up_commands_share_the_same_browser_session(tmp_path):
    settings = get_settings().model_copy(deep=True)
    settings.storage.artifact_root_dir = str(tmp_path / "artifacts")
    settings.orchestration.browser_headless = True
    registry = SimpleNamespace(get=lambda key: SimpleNamespace(
        enabled=True, capabilities=["browser-control"], name="browser"))
    runtime = MCPRuntimeService(registry, settings)
    page = CounterPage()
    target_url = page.start()
    context = {
        "project_id": "browser-control-navigation-project",
        "session_id": "browser-control-navigation-session",
        "turn_id": "browser-control-navigation-turn",
        "run_id": "browser-control-navigation-run",
        "run_item_id": "browser-control-navigation-item",
        "attempt_id": "browser-control-navigation-attempt",
        "trusted_resource_scope": {"project_url": target_url},
    }

    async def command(*args: str) -> dict:
        return await runtime.call("browser", "browser-control", {
            "action": "command", "args": list(args),
        }, context)

    try:
        navigation = await runtime.call("browser", "browser-control", {
            "action": "navigate", "target_url": target_url,
        }, context)
        assert navigation["status"] == "success"

        session_name = runtime._playwright_session_name(context, "browser-control-command")
        assert runtime._playwright_cli.session_info(session_name) is not None
        snapshot = await command("snapshot")
        assert snapshot["status"] == "success"
        assert "Increment" in snapshot["stdout"]
        assert (await command("eval", "document.querySelector('#count').textContent"))["result"] == "0"
        assert (await command("click", "#increment"))["status"] == "success"
        direct_eval = await runtime.call("browser", "browser-control", {
            "action": "eval", "target_url": target_url,
            "javascript": "document.querySelector('#count').textContent",
        }, context)
        assert direct_eval["status"] == "success"
        assert direct_eval["result"] == 1
        assert runtime._playwright_cli.session_info(session_name) is not None
        assert (await command("eval", "document.querySelector('#count').textContent"))["result"] == "1"
        assert page.count == 1

        closed = await command("close")
        assert closed["status"] == "success"
        assert runtime._playwright_cli.session_info(session_name) is None
    finally:
        await runtime.close()
        page.close()
