from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.application.runtime.python_playwright_cli import PythonPlaywrightCliRuntime
from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.core.request_context import bind_request_context


def settings():
    return SimpleNamespace(orchestration=SimpleNamespace(browser_action_timeout_seconds=5))


class ControlledRuntime(PythonPlaywrightCliRuntime):
    def __init__(self):
        super().__init__(settings())
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()
        self.started = []

    async def _ensure_session(self, session_name, cwd, open_args):
        if session_name not in self._sessions:
            self.started.append(session_name)
            self._sessions[session_name] = SimpleNamespace(
                name=session_name, output_dir=cwd, persistent=False,
                page=SimpleNamespace(url="about:blank"),
                context=SimpleNamespace(close=AsyncMock()),
                allocation=None, lease_watch=None,
                browser=SimpleNamespace(close=AsyncMock()),
                playwright=SimpleNamespace(stop=AsyncMock()),
            )
        return self._sessions[session_name]

    async def _execute(self, session, verb, args, cwd, raw):
        if verb == "hold":
            self.entered.set()
            await self.resume.wait()
        return session.name, []


def test_other_session_progresses_while_first_is_busy(tmp_path):
    async def run():
        runtime = ControlledRuntime()
        first = asyncio.create_task(runtime.run("a", ["hold"], tmp_path))
        await runtime.entered.wait()
        try:
            second = await asyncio.wait_for(runtime.run("b", ["eval"], tmp_path), 0.5)
            assert second["stdout"] == "b"
        finally:
            runtime.resume.set()
            await first
            await runtime.close_all()
    asyncio.run(run())


def test_same_session_commands_serialize_and_release_unused_locks(tmp_path):
    async def run():
        runtime = ControlledRuntime()
        first = asyncio.create_task(runtime.run("a", ["hold"], tmp_path))
        await runtime.entered.wait()
        second = asyncio.create_task(runtime.run("a", ["eval"], tmp_path))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not second.done()
        runtime.resume.set()
        assert (await first)["exit_code"] == 0
        assert (await second)["exit_code"] == 0
        assert runtime.started == ["a"]
        await runtime.close()
        assert not runtime._session_locks
    asyncio.run(run())


def test_cleanup_attempts_driver_stop_after_context_and_browser_errors(tmp_path):
    async def run():
        runtime = ControlledRuntime()
        await runtime.run("a", ["eval"], tmp_path)
        session = runtime._sessions["a"]
        session.context.close.side_effect = RuntimeError("context close failed")
        session.browser.close.side_effect = RuntimeError("browser close failed")
        with pytest.raises(RuntimeError, match="context close failed"):
            await runtime.close()
        session.browser.close.assert_awaited_once()
        session.playwright.stop.assert_awaited_once()
        session.context.close.side_effect = None
        session.browser.close.side_effect = None
        await runtime.close()
        assert not runtime._sessions
        assert not runtime._closing
    asyncio.run(run())


def test_repeated_cancellation_does_not_interrupt_disposal(tmp_path):
    async def run():
        runtime = ControlledRuntime()
        await runtime.run("a", ["eval"], tmp_path)
        session = runtime._sessions["a"]
        closing = asyncio.Event()
        release = asyncio.Event()

        async def slow_close():
            closing.set()
            await release.wait()

        session.context.close.side_effect = slow_close
        owner = asyncio.create_task(runtime.run("a", ["hold"], tmp_path))
        await runtime.entered.wait()
        owner.cancel()
        await closing.wait()
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        disposal = runtime._closing["a"]
        assert not disposal.cancelled()
        release.set()
        await runtime.close()
        session.context.close.assert_awaited_once()
        session.playwright.stop.assert_awaited_once()
        assert not runtime._session_locks
    asyncio.run(run())


def test_session_identity_uses_full_ids_and_attempt_context():
    runtime = MCPRuntimeService.__new__(MCPRuntimeService)
    base = {"session_id": "s" * 150, "turn_id": "t1", "context_bundle": {"attempt_id": "a1"}}
    name = runtime._playwright_session_name(base, "browser-control-command")
    assert len(name) <= 80
    for variant in (
        {**base, "session_id": "s" * 149 + "x"},
        {**base, "turn_id": "t2"},
        {**base, "context_bundle": {"attempt_id": "a2"}},
        {**base, "context_bundle": {"attempt_id": "a1", "test_run_id": "r2"}},
        {**base, "context_bundle": {"attempt_id": "a1", "test_run_item_id": "i2"}},
    ):
        assert runtime._playwright_session_name(variant, "browser-control-command") != name
    assert runtime._playwright_session_name({"session_id": "s/a"}, "tool") != runtime._playwright_session_name({"session_id": "s-a"}, "tool")
    with bind_request_context(session_id="s1", turn_id="t1", attempt_id="a1"):
        one = runtime._playwright_session_name({}, "tool")
    with bind_request_context(session_id="s1", turn_id="t1", attempt_id="a2"):
        two = runtime._playwright_session_name({}, "tool")
    assert one != two


def test_tool_runtime_closes_browser_when_other_resource_close_fails():
    from src.application.runtime.tool_runtime_service import ToolRuntimeService

    async def run():
        runtime = ToolRuntimeService.__new__(ToolRuntimeService)
        runtime._mock_target_runner = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("mock close failed")))
        runtime._security_execution_environment = SimpleNamespace(close=AsyncMock())
        runtime._ui_exploration_service = SimpleNamespace(close=AsyncMock())
        with pytest.raises(RuntimeError, match="mock close failed"):
            await runtime.close()
        runtime._security_execution_environment.close.assert_awaited_once()
        runtime._ui_exploration_service.close.assert_awaited_once()
    asyncio.run(run())


def test_command_close_all_and_list_are_scoped(tmp_path):
    async def run():
        runtime = ControlledRuntime()
        try:
            await runtime.run("a", ["eval"], tmp_path)
            await runtime.run("b", ["eval"], tmp_path)
            listing = await runtime.run("a", ["list"], tmp_path, raw=True)
            assert [item["name"] for item in json.loads(listing["stdout"])] == ["a"]
            await runtime.run("a", ["close-all"], tmp_path)
            assert set(runtime._sessions) == {"b"}
        finally:
            await runtime.close_all()
    asyncio.run(run())


@pytest.mark.parametrize("verb", ["close", "delete-data", "close-all", "kill-all"])
def test_close_missing_session_does_not_launch_browser(tmp_path, verb):
    async def run():
        runtime = ControlledRuntime()
        try:
            result = await runtime.run("absent", [verb], tmp_path)
            assert result["exit_code"] == 0
            assert not runtime.started
        finally:
            await runtime.close_all()
    asyncio.run(run())


def test_cancel_waiter_preserves_active_command_but_cancel_owner_closes(tmp_path):
    async def run():
        runtime = ControlledRuntime()
        first = asyncio.create_task(runtime.run("a", ["hold"], tmp_path))
        await runtime.entered.wait()
        session = runtime._sessions["a"]
        waiter = asyncio.create_task(runtime.run("a", ["eval"], tmp_path))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert runtime._sessions["a"] is session
        session.context.close.assert_not_awaited()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not runtime._sessions
        session.context.close.assert_awaited_once()
        session.browser.close.assert_awaited_once()
        session.playwright.stop.assert_awaited_once()
    asyncio.run(run())
