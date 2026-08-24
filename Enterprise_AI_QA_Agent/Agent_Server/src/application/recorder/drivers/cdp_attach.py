"""cdp-attach 驱动：attach 外部 Chromium 系浏览器（方案 5.2②，P1-1）。

目标场景：用户以 ``--remote-debugging-port=9222`` 启动本地 Chrome/Edge
（自带真实登录态），后端经 Playwright ``chromium.connect_over_cdp`` attach，
在既有 BrowserContext 上注入同一 recorder.js——录制脚本与事件协议与
embedded 完全一致，产物格式不变（方案 5.3 切换机制）。

注入链路与事件通道见 playwright_common（与 playwright-managed 共用）；
本驱动的差异点：复用既有 context/page（绝不新建 profile）与 close 只断连
不杀用户浏览器（Playwright 官方 connect_over_cdp close 语义）。

契约单测见 tests/test_recorder_cdp_attach.py（Fake playwright，不连真实
浏览器）；真实 Chrome 联调属 P1 验收（RUN_LIVE_CDP_RECORDING=1）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from src.schemas.recording import RecordingDriverConfig

from .playwright_common import PlaywrightBindingDriverBase

logger = logging.getLogger(__name__)


class CdpAttachDriver(PlaywrightBindingDriverBase):
    """attach 模式驱动：连接与浏览器生命周期归用户，驱动只负责注入与采集。"""

    kind = "cdp-attach"

    def __init__(
        self,
        *,
        config: RecordingDriverConfig,
        recording_id: str,
        connect_factory: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        endpoint = (config.endpoint or "").strip()
        if not endpoint:
            raise ValueError(
                "cdp-attach driver requires endpoint "
                "(e.g. http://127.0.0.1:9222 — start Chrome with --remote-debugging-port)"
            )
        super().__init__(recording_id=recording_id)
        self._endpoint = endpoint
        self._connect_factory = connect_factory  # 测试注入口；生产为 None → playwright
        self._browser: Any | None = None

    # ------------------------------------------------------------ 内部

    async def _connect(self) -> Any:
        if self._connect_factory is not None:
            return await self._connect_factory(self._endpoint)
        from playwright.async_api import async_playwright  # 延迟导入：未装 playwright 的环境可注册不实例化

        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(self._endpoint)
        # 连接断开时同步停止 playwright driver，避免进程泄漏
        browser.on("disconnected", lambda _b: asyncio.create_task(pw.stop()))
        return browser

    def _pick_context_and_page(self, browser: Any) -> tuple[Any, Any]:
        """复用既有 context 与 page（登录态在用户浏览器里，绝不新建 profile）。"""
        contexts = list(getattr(browser, "contexts", None) or [])
        context = contexts[0] if contexts else browser.new_context()
        pages = list(getattr(context, "pages", None) or [])
        page = pages[0] if pages else context.new_page()
        return context, page

    # ------------------------------------------------------------ BrowserDriver 契约

    async def open(self, url: str, *, viewport: tuple[int, int]) -> None:
        """attach CDP 端点 → 复用既有 context/page → 导航到入口 URL。

        viewport 参数不强制套用（attach 复用用户窗口尺寸，只记录不改动）。
        """
        if self._closed:
            raise RuntimeError(f"cdp-attach driver already closed: {self._recording_id}")
        self._browser = await self._connect()
        self._context, self._page = self._pick_context_and_page(self._browser)
        await self._page.goto(url, wait_until="domcontentloaded")
        logger.info(
            "cdp-attach opened: recording_id=%s endpoint=%s url=%s",
            self._recording_id,
            self._endpoint,
            url,
        )

    async def close(self) -> None:
        """断开 CDP 连接（用户浏览器与其登录态保留，Playwright 官方 close 语义）。"""
        if self._closed:
            return
        self._closed = True
        self._channel.close()
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001 连接已断开等：关闭幂等
                logger.debug(
                    "cdp-attach browser close ignored error: recording_id=%s", self._recording_id
                )
        logger.info("cdp-attach closed: recording_id=%s", self._recording_id)


def cdp_attach_factory(config: RecordingDriverConfig, *, recording_id: str, **_context: Any) -> CdpAttachDriver:
    """DriverRegistry 工厂：endpoint 校验在实例化时 fail-fast。"""
    return CdpAttachDriver(config=config, recording_id=recording_id)
