"""P1-1 cdp-attach 驱动真实浏览器验收（方案 12 章"集成"层，P1 验收）。

链路：playwright 启动真实 Chromium（--remote-debugging-port，模拟用户带
调试端口的外部浏览器）→ CdpAttachDriver.connect_over_cdp attach → 注入
recorder.js → 真实点击/输入 → 事件经 binding 回流驱动通道 → 事件结构
符合 RecorderEvent 契约（locator 链/像素三件套/seq）。

门控：RUN_LIVE_CDP_RECORDING=1（CI 与常规回归默认 skip；本机 Chromium
可用时执行，等价"本地 Chrome 带登录态"验收的自动化骨架——登录态来自
persistent profile，人工验收步骤见进度文档 P1-11 说明）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import quote

import pytest

from src.application.recorder.drivers.cdp_attach import CdpAttachDriver
from src.schemas.recording import RecorderEvent, RecordingDriverConfig, RecordingDriverKind

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_CDP_RECORDING") != "1",
    reason="set RUN_LIVE_CDP_RECORDING=1 to run live cdp-attach acceptance (needs chromium)",
)

_CDP_PORT = 9333
_TEST_PAGE = "data:text/html;charset=utf-8," + quote(
    "<html><head><title>验收页</title></head><body>"
    "<form class='login' onsubmit='return false'>"
    "<input id='username' placeholder='用户名' />"
    "<button id='submit' type='button'>登 录</button>"
    "</form></body></html>",
    safe="'=/< >:-.",
)


async def _collect_click_event(driver: CdpAttachDriver) -> dict[str, Any]:
    stream = await driver.on_recorder_event()
    deadline = asyncio.get_event_loop().time() + 10.0
    while asyncio.get_event_loop().time() < deadline:
        event = await asyncio.wait_for(stream.__anext__(), timeout=deadline - asyncio.get_event_loop().time())
        if event.get("type") == "click":
            return event
    raise AssertionError("no click event within 10s")


def test_live_cdp_attach_records_real_click_with_full_locator_chain() -> None:
    """真实 Chromium：attach → 注入 → 真实点击 → 事件回流且结构合规。"""

    async def scenario() -> None:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        outer = await pw.chromium.launch(
            headless=True, args=[f"--remote-debugging-port={_CDP_PORT}"]
        )
        driver = CdpAttachDriver(
            config=RecordingDriverConfig(
                kind=RecordingDriverKind.cdp_attach, endpoint=f"http://127.0.0.1:{_CDP_PORT}"
            ),
            recording_id="rec-live-1",
        )
        try:
            await driver.open(_TEST_PAGE, viewport=(1440, 960))
            await driver.inject_recorder()
            await driver.set_capture_enabled(True)

            # 在 attach 上下文里找到目标按钮并真实点击
            page = driver._page
            await page.click("#submit")

            raw = await _collect_click_event(driver)
            event = RecorderEvent.model_validate(raw)  # 结构符合录制契约
            assert event.type == "click"
            assert event.target is not None and event.target["locators"]
            locators = event.target["locators"]
            assert locators.get("id") == "submit"
            assert locators.get("text") == "登 录"
            assert event.pixel and "viewport_point" in event.pixel
            assert event.page and "url" in event.page
            assert isinstance(event.seq, int)
        finally:
            await driver.close()  # 断连，外部浏览器仍在
            await outer.close()
            await pw.stop()

    asyncio.run(scenario())
