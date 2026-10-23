"""Real Chromium lifecycle acceptance; no external website or model required."""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.application.runtime.python_playwright_cli import PythonPlaywrightCliRuntime
from src.core.config import get_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_BROWSER_TESTS") != "1",
    reason="set RUN_LIVE_BROWSER_TESTS=1 (requires installed Chromium)",
)


def browser_settings():
    config = get_settings().model_copy(deep=True)
    config.orchestration.browser_headless = True
    config.orchestration.browser_default_name = "chromium"
    config.orchestration.browser_action_timeout_seconds = 15
    return config


def test_real_browser_parallel_contexts_and_scoped_close(tmp_path):
    async def run():
        runtime = PythonPlaywrightCliRuntime(browser_settings())
        first = None
        try:
            for name in ("a", "b"):
                result = await runtime.run(name, ["eval", "1"], tmp_path, raw=True)
                assert result["exit_code"] == 0, result["stderr"]
            a, b = runtime._sessions["a"], runtime._sessions["b"]
            await a.context.add_cookies([{"name": "test_scope", "value": "a", "url": "http://localhost"}])
            assert not await b.context.cookies()
            entered = asyncio.Event()
            await a.page.expose_function("entered", entered.set)
            first = asyncio.create_task(runtime.run("a", ["eval",
                "new Promise(resolve => { window.finish = resolve; window.entered(); })"], tmp_path, raw=True))
            await asyncio.wait_for(entered.wait(), 5)
            other = await asyncio.wait_for(runtime.run("b", ["eval", "2"], tmp_path, raw=True), 3)
            assert other["stdout"].strip() == "2"
            assert not first.done()
            await a.page.evaluate("window.finish(7)")
            assert (await first)["stdout"].strip() == "7"
            await runtime.run("a", ["close-all"], tmp_path)
            assert not a.browser.is_connected()
            assert b.browser.is_connected()
            assert (await runtime.run("b", ["eval", "3"], tmp_path))["exit_code"] == 0
        finally:
            await runtime.close()
            if first is not None:
                await asyncio.gather(first, return_exceptions=True)
        assert not runtime._sessions
        assert not runtime._session_locks
        assert not runtime._closing
    asyncio.run(run())


def test_real_browser_timeout_and_shutdown_stop_execution(tmp_path):
    async def run():
        runtime = PythonPlaywrightCliRuntime(browser_settings())
        try:
            assert (await runtime.run("a", ["eval", "1"], tmp_path))["exit_code"] == 0
            a = runtime._sessions["a"]
            result = await runtime.run("a", ["eval", "new Promise(() => {})"], tmp_path, timeout_seconds=1)
            assert result["timed_out"]
            assert not a.browser.is_connected()
            assert "a" not in runtime._sessions
            assert (await runtime.run("b", ["eval", "1"], tmp_path))["exit_code"] == 0
            b = runtime._sessions["b"]
            entered = asyncio.Event()
            await b.page.expose_function("entered", entered.set)
            command = asyncio.create_task(runtime.run("b", ["eval",
                "new Promise(() => { window.entered(); })"], tmp_path))
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.wait_for(runtime.close(), 10)
            with pytest.raises(asyncio.CancelledError):
                await command
            assert not b.browser.is_connected()
            assert (await runtime.run("c", ["eval", "1"], tmp_path))["exit_code"] == 1
        finally:
            await runtime.close()
        assert not runtime._sessions
    asyncio.run(run())


def test_real_browser_startup_failure_disposes_launched_process(tmp_path, monkeypatch):
    from playwright.async_api import Browser

    async def run():
        runtime = PythonPlaywrightCliRuntime(browser_settings())
        launched = []

        async def fail_new_context(browser, **kwargs):
            launched.append(browser)
            raise RuntimeError("injected new_context failure after real browser launch")

        monkeypatch.setattr(Browser, "new_context", fail_new_context)
        try:
            result = await runtime.run("a", ["eval", "1"], tmp_path)
            assert result["exit_code"] == 1
            assert "injected new_context failure" in result["stderr"]
            assert len(launched) == 1
            assert not launched[0].is_connected()
            assert not runtime._sessions
            assert not runtime._closing
        finally:
            await runtime.close()
    asyncio.run(run())


def test_mcp_commands_reuse_only_same_attempt_and_keep_artifacts(tmp_path):
    async def run():
        registry = SimpleNamespace(get=lambda key: SimpleNamespace(
            enabled=True, capabilities=["browser-control"], name="browser"))
        service = MCPRuntimeService(registry, browser_settings())
        service._artifact_root = tmp_path
        contexts = [{"session_id": "s-live", "turn_id": "t-live", "context_bundle": {
            "project_id": "p-live", "test_run_id": "r-live", "run_item_id": "i-live", "attempt_id": attempt,
        }} for attempt in ("a1", "a2")]

        async def command(context, args):
            return await service.call("browser", "browser-control", {"action": "command", "args": args, "raw": True}, context)

        try:
            first = await command(contexts[0], ["eval", "window.marker = 11"])
            second = await command(contexts[0], ["eval", "window.marker"])
            assert second["stdout"].strip() == "11"
            assert first["artifacts"][-1]["path"] != second["artifacts"][-1]["path"]
            isolated = await command(contexts[1], ["eval", "typeof window.marker"])
            assert "undefined" in isolated["stdout"]
            await command(contexts[0], ["close-all"])
            assert len(service._playwright_cli._sessions) == 1
            assert (await command(contexts[1], ["eval", "5"]))["stdout"].strip() == "5"
        finally:
            await service.close()
        assert not service._playwright_cli._sessions
    asyncio.run(run())


def test_mcp_navigation_keeps_the_command_session_for_follow_up_actions(tmp_path):
    async def run():
        from src.application.resources.session_resource_service import SessionResourceService
        from src.runtime.session_resource_store import InMemorySessionResourceStore
        from src.schemas.session_resource import SessionResourceKind

        registry = SimpleNamespace(get=lambda key: SimpleNamespace(
            enabled=True, capabilities=["browser-control"], name="browser"))
        resources = SessionResourceService(InMemorySessionResourceStore())
        service = MCPRuntimeService(registry, browser_settings(), resources)
        service._artifact_root = tmp_path
        context = {
            "project_id": "p-navigation",
            "session_id": "s-navigation",
            "turn_id": "t-navigation",
            "run_id": "r-navigation",
            "run_item_id": "i-navigation",
            "attempt_id": "a-navigation",
        }
        html = """<!doctype html><html><meta charset="utf-8"><title>Browser session</title>
<button id="increment">Increment</button><output id="count">0</output>
<script>document.querySelector('#increment').onclick=()=>{const node=document.querySelector('#count');node.textContent=String(Number(node.textContent)+1)};</script>
</html>"""
        target_url = "data:text/html;charset=utf-8," + quote(html, safe="")
        session_name = service._playwright_session_name(context, "browser-control-command")

        async def command(command_text):
            return await service.call("browser", "browser-control", {
                "action": "command", "command": command_text,
            }, context)

        try:
            navigation = await service.call("browser", "browser-control", {
                "action": "navigate", "target_url": target_url,
            }, context)
            assert navigation["status"] == "success"
            assert service._playwright_cli.session_info(session_name) is not None
            active = await resources.list_active(context["session_id"])
            assert any(item.kind == SessionResourceKind.browser_session
                       and item.resource_key == session_name for item in active)

            snapshot = await command("snapshot")
            assert snapshot["status"] == "success"
            assert "Increment" in snapshot["stdout"]
            assert (await command("eval \"document.querySelector('#count').textContent\""))["result"] == "0"
            assert (await command("click #increment"))["status"] == "success"
            assert (await command("eval \"document.querySelector('#count').textContent\""))["result"] == "1"
            active = await resources.list_active(context["session_id"])
            assert sum(item.kind == SessionResourceKind.browser_session
                       and item.resource_key == session_name for item in active) == 1

            closed = await command("close")
            assert closed["status"] == "success"
            assert service._playwright_cli.session_info(session_name) is None
            active = await resources.list_active(context["session_id"])
            assert not any(item.kind == SessionResourceKind.browser_session
                           and item.resource_key == session_name for item in active)
        finally:
            await service.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["driver", "context", "page"])
def test_cancel_during_real_startup_reclaims_driver_and_browser(tmp_path, monkeypatch, phase):
    from playwright.async_api import Browser, BrowserContext
    from playwright.async_api._context_manager import PlaywrightContextManager

    async def run():
        runtime = PythonPlaywrightCliRuntime(browser_settings())
        entered = asyncio.Event()
        release = asyncio.Event()
        drivers = []
        browsers = []
        original_start = PlaywrightContextManager.start
        original_context = Browser.new_context
        original_page = BrowserContext.new_page

        async def start(manager):
            driver = await original_start(manager)
            drivers.append(driver)
            if phase == "driver":
                entered.set()
                await release.wait()
            return driver

        async def new_context(browser, **kwargs):
            browsers.append(browser)
            context = await original_context(browser, **kwargs)
            if phase == "context":
                entered.set()
                await release.wait()
            return context

        async def new_page(context, **kwargs):
            page = await original_page(context, **kwargs)
            if phase == "page":
                entered.set()
                await release.wait()
            return page

        monkeypatch.setattr(PlaywrightContextManager, "start", start)
        monkeypatch.setattr(Browser, "new_context", new_context)
        monkeypatch.setattr(BrowserContext, "new_page", new_page)
        command = asyncio.create_task(runtime.run("starting", ["eval", "1"], tmp_path))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            command.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await command
            assert not runtime._sessions
            assert all(not browser.is_connected() for browser in browsers)
            assert drivers
            # Verify that the real driver is stopped, not merely removed from a map.
            with pytest.raises(Exception, match="closed"):
                await drivers[0].chromium.launch(headless=True)
        finally:
            release.set()
            await runtime.close()
            await asyncio.gather(command, return_exceptions=True)
    asyncio.run(run())


def test_persistent_profile_root_is_scoped_and_registered_from_runtime(tmp_path):
    from src.application.resources.session_resource_service import SessionResourceService
    from src.runtime.session_resource_store import InMemorySessionResourceStore
    from src.schemas.session_resource import SessionResourceKind

    async def run():
        resources = SessionResourceService(InMemorySessionResourceStore())
        registry = SimpleNamespace(get=lambda key: SimpleNamespace(
            enabled=True, capabilities=["browser-control"], name="browser"))
        settings = browser_settings()
        settings.storage.artifact_root_dir = str(tmp_path / "artifacts")
        service = MCPRuntimeService(registry, settings, resources)
        contexts = [{"session_id": "s-live", "turn_id": "t-live", "context_bundle": {"attempt_id": attempt}}
                    for attempt in ("a1", "a2")]
        try:
            for index, context in enumerate(contexts):
                result = await service.call("browser", "browser-control", {"action": "command",
                    "args": ["open", "--persistent", f"--profile={service._artifact_root / 'profiles'}"]}, context)
                assert result["status"] == "success"
                name = service._playwright_session_name(context, "browser-control-command")
                session = service._playwright_cli._sessions[name]
                profiles = [item for item in await resources.list_active("s-live")
                            if item.kind == SessionResourceKind.browser_profile]
                assert any(item.resource_key == str(session.profile_dir) for item in profiles)
                if index == 0:
                    await session.context.add_cookies([{"name": "private_state", "value": "a", "url": "http://localhost"}])
                    await service.close_browser_session(name)
                else:
                    assert not await session.context.cookies()
            assert len({item.resource_key for item in profiles}) == 2
        finally:
            await service.close()
    asyncio.run(run())


def test_browser_command_confines_navigation_uploads_outputs_and_profiles(tmp_path):
    async def run():
        root = tmp_path / "artifacts"
        root.mkdir()
        outside_page = tmp_path / "outside.html"
        outside_page.write_text("<html><body>LOCAL_FILE_MARKER</body></html>", encoding="utf-8")
        inside_file = root / "inside.txt"
        inside_file.write_text("ALLOWED_UPLOAD_MARKER", encoding="utf-8")
        runtime = PythonPlaywrightCliRuntime(browser_settings(), artifact_root=root)
        try:
            denied_page = await runtime.run("file-scope", ["open", outside_page.as_uri()], root)
            assert denied_page["exit_code"] == 1
            assert "local files is not allowed" in denied_page["stderr"]

            page = await runtime.run(
                "file-scope", ["open", 'data:text/html,<input type="file"><h1>PAGE_MARKER</h1>'], root,
            )
            assert page["exit_code"] == 0, page["stderr"]

            denied_upload = await runtime.run("file-scope", ["upload", str(outside_page)], root)
            assert denied_upload["exit_code"] == 1
            assert "Browser upload path" in denied_upload["stderr"]
            link = root / "outside_link.html"
            try:
                link.symlink_to(outside_page)
            except OSError:
                link = None
            if link is not None:
                denied_link_upload = await runtime.run("file-scope", ["upload", str(link)], root)
                assert denied_link_upload["exit_code"] == 1
            allowed_upload = await runtime.run("file-scope", ["upload", str(inside_file)], root)
            assert allowed_upload["exit_code"] == 0, allowed_upload["stderr"]
            uploaded_content = await runtime.run(
                "file-scope", ["eval", 'async () => await document.querySelector("input").files[0].text()'], root,
            )
            assert "ALLOWED_UPLOAD_MARKER" in uploaded_content["stdout"]

            denied_snapshot = await runtime.run(
                "file-scope", ["snapshot", "--filename=../outside_snapshot.txt"], root,
            )
            assert denied_snapshot["exit_code"] == 1
            assert not (tmp_path / "outside_snapshot.txt").exists()
            outside_target = tmp_path / "existing_outside.txt"
            outside_target.write_text("UNCHANGED_MARKER", encoding="utf-8")
            output_link = root / "linked_snapshot.txt"
            try:
                output_link.symlink_to(outside_target)
            except OSError:
                output_link = None
            if output_link is not None:
                denied_link_snapshot = await runtime.run(
                    "file-scope", ["snapshot", "--filename=linked_snapshot.txt"], root,
                )
                assert denied_link_snapshot["exit_code"] == 1
                assert outside_target.read_text(encoding="utf-8") == "UNCHANGED_MARKER"
            allowed_snapshot = await runtime.run(
                "file-scope", ["snapshot", "--filename=nested/inside_snapshot.txt"], root,
            )
            assert allowed_snapshot["exit_code"] == 0, allowed_snapshot["stderr"]
            assert (root / "nested" / "inside_snapshot.txt").exists()

            denied_profile = await runtime.run(
                "profile-outside", ["open", "--persistent", f"--profile={tmp_path / 'profiles'}"], root,
            )
            assert denied_profile["exit_code"] == 1
            assert not (tmp_path / "profiles").exists()
            allowed_profile = await runtime.run(
                "profile-inside", ["open", "--persistent", f"--profile={root / 'profiles'}"], root,
            )
            assert allowed_profile["exit_code"] == 0, allowed_profile["stderr"]
            assert runtime._sessions["profile-inside"].profile_dir.is_dir()
        finally:
            await runtime.close()

    asyncio.run(run())


def test_ui_exploration_completion_does_not_close_another_invocation(tmp_path, monkeypatch):
    from src.application.testing.ui_exploration_service import UIExplorationService

    async def run():
        service = UIExplorationService(browser_settings())
        service._artifact_root = tmp_path
        runtime = service._playwright_runtime
        execute = runtime._execute
        entered = asyncio.Event()
        release = asyncio.Event()
        held = []

        async def controlled_execute(session, verb, args, cwd, raw):
            if "slow" in args[0]:
                held.append(session)
                entered.set()
                await release.wait()
            return await execute(session, verb, args, cwd, raw)

        monkeypatch.setattr(runtime, "_execute", controlled_execute)
        context = SimpleNamespace(session_id="long-session-" * 20, turn_id="t-live", trace_id="trace-live", context_bundle={})
        first = asyncio.create_task(service.explore({"url": "data:text/html,<title>slow</title><button>A</button>"}, context))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            second = await service.explore({"url": "data:text/html,<title>fast</title><button>B</button>"}, context)
            assert second["status"] == "success"
            assert held[0].browser.is_connected()
            assert len(runtime._sessions) == 1
            release.set()
            assert (await first)["status"] == "success"
            assert not runtime._sessions
        finally:
            release.set()
            await service.close()
            await asyncio.gather(first, return_exceptions=True)
    asyncio.run(run())
