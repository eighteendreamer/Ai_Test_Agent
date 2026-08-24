"""playwright-managed 驱动契约单测（P1-2，不连真实浏览器/不真启动浏览器）。

替身仅覆盖 ``_start_playwright``（驱动预留的测试注入口），``open``/
``inject_recorder``/``close`` 全部真实执行。验证：launch_persistent_context
以 headless=False + 配置 viewport 自启、profile 落 artifact 目录、复用
context 首页导航、binding + init_script + 已开页 evaluate 注入（共用基类）、
close 关 context（受管浏览器，与 cdp-attach 只断连相反）并停 playwright
driver、注册表接入三驱动齐备。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.application.recorder.drivers import (
    DriverRegistry,
    PlaywrightManagedDriver,
    build_default_registry,
    playwright_managed_factory,
)
from src.application.recorder.drivers.playwright_common import _load_recorder_script
from src.schemas.recording import RecordingDriverConfig, RecordingDriverKind


class FakeManagedPage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.goto_calls: list[str] = []
        self.evaluate_calls: list[tuple[Any, ...]] = []

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.url = url
        self.goto_calls.append(url)

    async def evaluate(self, expression: Any, arg: Any = None) -> Any:
        self.evaluate_calls.append((expression, arg))
        if isinstance(expression, str) and "location.href" in expression:
            return {
                "url": self.url,
                "title": "受管页",
                "vw": 1440,
                "vh": 960,
                "dpr": 1.0,
            }
        return None

    async def screenshot(self, type: str = "png") -> bytes:  # noqa: A002
        return b"managed-png"


class FakeManagedContext:
    def __init__(self, pages: list[FakeManagedPage] | None = None) -> None:
        self.pages = pages if pages is not None else []
        self.bindings: dict[str, Any] = {}
        self.init_scripts: list[str] = []
        self.close_calls = 0

    async def expose_binding(self, name: str, handler: Any) -> None:
        self.bindings[name] = handler

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def new_page(self) -> FakeManagedPage:
        page = FakeManagedPage()
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.close_calls += 1


class FakePlaywright:
    """playwright driver 替身：记录 launch_persistent_context 调用参数。"""

    def __init__(self, context: FakeManagedContext) -> None:
        self._context = context
        self.launch_calls: list[dict[str, Any]] = []
        self.stopped = 0
        self.chromium = self._Chromium(self)

    class _Chromium:
        def __init__(self, owner: "FakePlaywright") -> None:
            self._owner = owner

        async def launch_persistent_context(self, user_data_dir: str, **options: Any) -> Any:
            self._owner.launch_calls.append({"user_data_dir": user_data_dir, **options})
            return self._owner._context

    async def stop(self) -> None:
        self.stopped += 1


def _make_driver(tmp_path: Path, *, pages: list[FakeManagedPage] | None = None) -> tuple[
    PlaywrightManagedDriver, FakePlaywright, FakeManagedContext
]:
    context = FakeManagedContext(pages)
    pw = FakePlaywright(context)
    driver = PlaywrightManagedDriver(
        config=RecordingDriverConfig(kind=RecordingDriverKind.playwright_managed, viewport=(1280, 800)),
        recording_id="rec-1",
    )
    driver._profile_dir = tmp_path / "artifacts" / "recorder-profile"  # 测试产物落临时目录

    async def fake_start() -> FakePlaywright:
        return pw

    driver._start_playwright = fake_start  # type: ignore[method-assign]
    return driver, pw, context


# ---------------------------------------------------------------- open


def test_open_launches_headed_persistent_context_with_config_viewport(tmp_path: Path) -> None:
    async def scenario() -> None:
        initial = FakeManagedPage()
        driver, pw, _ = _make_driver(tmp_path, pages=[initial])
        await driver.open("https://app.example.com/login", viewport=(1280, 800))

        assert len(pw.launch_calls) == 1
        call = pw.launch_calls[0]
        assert call["headless"] is False  # 受管窗口必须可见
        assert call["viewport"] == {"width": 1280, "height": 800}
        assert call["user_data_dir"].endswith("recorder-profile")
        assert initial.goto_calls == ["https://app.example.com/login"]
        assert driver._page is initial  # 复用 persistent context 初始页

    asyncio.run(scenario())


def test_open_creates_page_when_context_empty(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver, _, context = _make_driver(tmp_path, pages=[])
        await driver.open("https://app.example.com", viewport=(1280, 800))
        assert len(context.pages) == 1
        assert driver._page is context.pages[0]

    asyncio.run(scenario())


# ---------------------------------------------------------------- 注入/事件（共用基类行为在真实 open 后验证）


def test_inject_registers_binding_and_init_script_and_evaluates_open_pages(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver, _, context = _make_driver(tmp_path, pages=[FakeManagedPage()])
        await driver.open("https://app.example.com", viewport=(1280, 800))
        context.pages[0].evaluate_calls.clear()

        await driver.inject_recorder()

        script = _load_recorder_script()
        assert context.bindings["__qaRecordEmit"] is not None
        assert context.init_scripts == [script]
        assert context.pages[0].evaluate_calls == [(script, None)]

    asyncio.run(scenario())


def test_binding_events_flow_into_channel(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver, _, context = _make_driver(tmp_path, pages=[FakeManagedPage()])
        await driver.open("https://app.example.com", viewport=(1280, 800))
        await driver.inject_recorder()

        await context.bindings["__qaRecordEmit"]({}, {"seq": 3, "type": "fill"})
        stream = await driver.on_recorder_event()
        event = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        assert event["seq"] == 3

    asyncio.run(scenario())


def test_screenshot_and_page_info(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver, _, _ = _make_driver(tmp_path, pages=[FakeManagedPage()])
        await driver.open("https://app.example.com/home", viewport=(1280, 800))
        assert await driver.capture_screenshot() == b"managed-png"
        info = await driver.current_page_info()
        assert info["url"] == "https://app.example.com/home"
        assert info["title"] == "受管页"

    asyncio.run(scenario())


def test_set_capture_enabled_uses_recorder_toggle(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver, _, _ = _make_driver(tmp_path, pages=[FakeManagedPage()])
        await driver.open("https://app.example.com", viewport=(1280, 800))
        await driver.set_capture_enabled(True)
        toggles = [
            call for call in driver._page.evaluate_calls if "SetEnabled" in str(call[0])
        ]
        assert toggles == [("enabled => window.__qaRecorderSetEnabled(!!enabled)", True)]

    asyncio.run(scenario())


# ---------------------------------------------------------------- close


def test_close_closes_managed_context_and_stops_playwright(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver, pw, context = _make_driver(tmp_path, pages=[FakeManagedPage()])
        await driver.open("https://app.example.com", viewport=(1280, 800))
        await driver.inject_recorder()

        await driver.close()
        await driver.close()  # 幂等

        assert context.close_calls == 1  # 受管浏览器：关窗（对照 cdp-attach 只断连）
        assert pw.stopped == 1

    asyncio.run(scenario())


def test_open_after_close_raises(tmp_path: Path) -> None:
    async def scenario() -> None:
        driver, _, _ = _make_driver(tmp_path, pages=[FakeManagedPage()])
        await driver.open("https://app.example.com", viewport=(1280, 800))
        await driver.close()
        with pytest.raises(RuntimeError, match="already closed"):
            await driver.open("https://app.example.com", viewport=(1280, 800))

    asyncio.run(scenario())


# ---------------------------------------------------------------- 注册表


def test_registry_creates_playwright_managed_driver() -> None:
    registry = DriverRegistry()
    registry.register("playwright-managed", playwright_managed_factory)
    driver = registry.create(
        RecordingDriverConfig(kind=RecordingDriverKind.playwright_managed),
        recording_id="rec-1",
    )
    assert isinstance(driver, PlaywrightManagedDriver)
    assert driver.kind == "playwright-managed"


def test_default_registry_includes_all_three_kinds() -> None:
    registry = build_default_registry()
    assert registry.kinds() == ["cdp-attach", "embedded", "playwright-managed"]
