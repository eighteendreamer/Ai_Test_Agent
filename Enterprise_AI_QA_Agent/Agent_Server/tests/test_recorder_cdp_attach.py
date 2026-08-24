"""cdp-attach 驱动契约单测（P1-1，不连真实浏览器）。

Fake playwright 对象模拟 Browser/Context/Page（记录调用、可编程返回），
验证：endpoint 缺失 fail-fast、open 复用既有 context/page（不新建 profile）、
context 级 binding + init_script 注入、已开 page 立即 evaluate、
事件经 binding 汇入通道、开关/截图/页信息/close 断连不杀浏览器语义、
注册表接入（kind=cdp-attach 可创建）。

真实 Chrome 联调属 P1 验收（RUN_LIVE_CDP_RECORDING=1 手动执行）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.application.recorder.drivers import (
    DriverRegistry,
    build_default_registry,
)
from src.application.recorder.drivers.base import EventChannel
from src.application.recorder.drivers.cdp_attach import (
    CdpAttachDriver,
    _load_recorder_script,
    cdp_attach_factory,
)
from src.schemas.recording import RecordingDriverConfig, RecordingDriverKind


class FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url
        self.goto_calls: list[str] = []
        self.evaluate_calls: list[tuple[Any, ...]] = []
        self.screenshot_bytes = b"png-bytes"

    async def goto(self, url: str, wait_until: str | None = None) -> None:
        self.url = url
        self.goto_calls.append(url)

    async def evaluate(self, expression: Any, arg: Any = None) -> Any:
        self.evaluate_calls.append((expression, arg))
        if isinstance(expression, str) and "location.href" in expression:
            return {
                "url": self.url,
                "title": "示例页",
                "vw": 1440,
                "vh": 960,
                "dpr": 1.5,
            }
        return None

    async def screenshot(self, type: str = "png") -> bytes:  # noqa: A002
        assert type == "png"
        return self.screenshot_bytes


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = pages
        self.bindings: dict[str, Any] = {}
        self.init_scripts: list[str] = []

    async def expose_binding(self, name: str, handler: Any) -> None:
        self.bindings[name] = handler

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, contexts: list[FakeContext]) -> None:
        self.contexts = contexts
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _driver(browser: FakeBrowser) -> CdpAttachDriver:
    async def factory(endpoint: str) -> FakeBrowser:
        assert endpoint == "http://127.0.0.1:9222"
        return browser

    return CdpAttachDriver(
        config=RecordingDriverConfig(
            kind=RecordingDriverKind.cdp_attach, endpoint="http://127.0.0.1:9222"
        ),
        recording_id="rec-1",
        connect_factory=factory,
    )


def _browser_with_page() -> tuple[FakeBrowser, FakeContext, FakePage]:
    page = FakePage("https://mail.example.com/inbox")
    context = FakeContext([page])
    return FakeBrowser([context]), context, page


# ---------------------------------------------------------------- 启动防御


def test_missing_endpoint_fails_fast() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        CdpAttachDriver(
            config=RecordingDriverConfig(kind=RecordingDriverKind.cdp_attach),
            recording_id="rec-1",
        )


def test_blank_endpoint_fails_fast() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        CdpAttachDriver(
            config=RecordingDriverConfig(kind=RecordingDriverKind.cdp_attach, endpoint="  "),
            recording_id="rec-1",
        )


# ---------------------------------------------------------------- open


def test_open_reuses_existing_context_and_page_and_navigates() -> None:
    async def scenario() -> None:
        browser, context, page = _browser_with_page()
        driver = _driver(browser)

        await driver.open("https://app.example.com/login", viewport=(1440, 960))

        # 复用用户浏览器既有 context/page（登录态所在），不新建 profile
        assert driver._browser is browser
        assert driver._context is context
        assert driver._page is page
        assert page.goto_calls == ["https://app.example.com/login"]

    asyncio.run(scenario())


def test_open_connects_to_configured_endpoint() -> None:
    async def scenario() -> None:
        browser, _, _ = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))
        await driver.close()

    asyncio.run(scenario())


# ---------------------------------------------------------------- inject


def test_inject_registers_context_level_binding_and_init_script() -> None:
    async def scenario() -> None:
        browser, context, page = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))
        page.evaluate_calls.clear()

        await driver.inject_recorder()

        script = _load_recorder_script()
        assert context.bindings["__qaRecordEmit"] is not None
        assert context.init_scripts == [script]
        # 已打开的 page 立即注入（init_script 只对新文档生效）
        assert page.evaluate_calls == [(script, None)]

    asyncio.run(scenario())


def test_inject_before_open_raises() -> None:
    async def scenario() -> None:
        browser, _, _ = _browser_with_page()
        driver = _driver(browser)
        with pytest.raises(RuntimeError, match="before open"):
            await driver.inject_recorder()

    asyncio.run(scenario())


# ---------------------------------------------------------------- 事件通道


def test_binding_events_flow_into_channel() -> None:
    async def scenario() -> None:
        browser, context, _ = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))
        await driver.inject_recorder()

        handler = context.bindings["__qaRecordEmit"]
        await handler({"seq": 0}, {"seq": 0, "type": "click"})
        await handler({"seq": 1}, {"seq": 1, "type": "fill"})

        stream = await driver.on_recorder_event()
        first = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        second = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        assert [first["seq"], second["seq"]] == [0, 1]

    asyncio.run(scenario())


def test_non_dict_binding_payload_is_dropped_not_raised() -> None:
    async def scenario() -> None:
        browser, context, _ = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))
        await driver.inject_recorder()

        handler = context.bindings["__qaRecordEmit"]
        await handler({}, "malformed-string")  # 不抛错即可（防御分支）
        await handler({}, {"seq": 5, "type": "click"})

        stream = await driver.on_recorder_event()
        event = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        assert event["seq"] == 5

    asyncio.run(scenario())


# ---------------------------------------------------------------- 控制/查询


def test_set_capture_enabled_evaluates_recorder_toggle() -> None:
    async def scenario() -> None:
        browser, _, page = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))

        await driver.set_capture_enabled(False)
        await driver.set_capture_enabled(True)

        toggles = [call for call in page.evaluate_calls if "SetEnabled" in str(call[0])]
        assert toggles == [
            ("enabled => window.__qaRecorderSetEnabled(!!enabled)", False),
            ("enabled => window.__qaRecorderSetEnabled(!!enabled)", True),
        ]

    asyncio.run(scenario())


def test_screenshot_returns_page_png() -> None:
    async def scenario() -> None:
        browser, _, _ = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))
        assert await driver.capture_screenshot() == b"png-bytes"

    asyncio.run(scenario())


def test_current_page_info_reports_url_title_viewport_dpr() -> None:
    async def scenario() -> None:
        browser, _, _ = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com/login", viewport=(1440, 960))
        info = await driver.current_page_info()
        assert info == {
            "url": "https://app.example.com/login",
            "title": "示例页",
            "viewport": (1440, 960),
            "dpr": 1.5,
        }

    asyncio.run(scenario())


def test_wait_ready_default_true_for_sync_attach() -> None:
    async def scenario() -> None:
        browser, _, _ = _browser_with_page()
        driver = _driver(browser)
        assert await driver.wait_ready(timeout=0.1) is True

    asyncio.run(scenario())


# ---------------------------------------------------------------- close


def test_close_disconnects_without_killing_user_browser() -> None:
    async def scenario() -> None:
        browser, _, _ = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))
        await driver.inject_recorder()

        await driver.close()
        await driver.close()  # 幂等

        assert browser.close_calls == 1  # 断开连接；用户浏览器进程由用户自管

    asyncio.run(scenario())


def test_open_after_close_raises() -> None:
    async def scenario() -> None:
        browser, _, _ = _browser_with_page()
        driver = _driver(browser)
        await driver.open("https://app.example.com", viewport=(1440, 960))
        await driver.close()
        with pytest.raises(RuntimeError, match="already closed"):
            await driver.open("https://app.example.com", viewport=(1440, 960))

    asyncio.run(scenario())


# ---------------------------------------------------------------- 注册表


def test_registry_creates_cdp_attach_driver() -> None:
    registry = DriverRegistry()
    registry.register("cdp-attach", cdp_attach_factory)
    assert registry.is_registered("cdp-attach")

    driver = registry.create(
        RecordingDriverConfig(kind=RecordingDriverKind.cdp_attach, endpoint="http://127.0.0.1:9222"),
        recording_id="rec-1",
    )
    assert isinstance(driver, CdpAttachDriver)
    assert driver.kind == "cdp-attach"


def test_default_registry_includes_cdp_attach() -> None:
    registry = build_default_registry()
    assert "cdp-attach" in registry.kinds()
    assert "embedded" in registry.kinds()


def test_event_channel_shared_not_isolated() -> None:
    """驱动通道与 base.EventChannel 同实现（与 embedded 行为一致）。"""
    driver = CdpAttachDriver(
        config=RecordingDriverConfig(
            kind=RecordingDriverKind.cdp_attach, endpoint="http://127.0.0.1:9222"
        ),
        recording_id="rec-1",
    )
    assert isinstance(driver._channel, EventChannel)
