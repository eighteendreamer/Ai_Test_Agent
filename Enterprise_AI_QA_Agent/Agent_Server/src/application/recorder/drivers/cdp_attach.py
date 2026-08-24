"""cdp-attach 驱动：attach 外部 Chromium 系浏览器（方案 5.2②，P1-1）。

目标场景：用户以 ``--remote-debugging-port=9222`` 启动本地 Chrome/Edge
（自带真实登录态），后端经 Playwright ``chromium.connect_over_cdp`` attach，
在既有 BrowserContext 上注入同一 recorder.js——录制脚本与事件协议与
embedded 完全一致，产物格式不变（方案 5.3 切换机制）。

注入链路（Playwright 官方语义）：
- ``context.add_init_script(recorder.js)``：文档创建前注入，导航/新 tab 后仍生效；
- ``context.expose_binding("__qaRecordEmit")``：注册到 context 下所有 frame，
  导航后由 Playwright 自动重注册（内部即 CDP addBinding）；
- 对已打开的 page 先 ``evaluate(recorder.js)`` 立即生效（init_script 只对新文档）。

close 语义：Playwright 官方——经 connect_over_cdp 获得的 browser，
``browser.close()`` 只清除本连接创建的资源并断开，**不杀用户浏览器进程**，
登录态完整保留。

不连真实浏览器的契约单测见 tests/test_recorder_cdp_attach.py；真实
Chrome 联调属 P1 验收（RUN_LIVE_CDP_RECORDING=1）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from src.schemas.recording import RecordingDriverConfig

from .base import BrowserDriver, EventChannel

logger = logging.getLogger(__name__)

_RECORDER_JS_PATH = Path(__file__).resolve().parents[1] / "assets" / "recorder.js"

DEFAULT_BINDING_NAME = "__qaRecordEmit"

# 注入失败的兜底重试（evaluate 页面尚未就绪等瞬态错误）
_INJECT_RETRY_DELAYS = (0.5, 1.0, 2.0)


def _load_recorder_script() -> str:
    """读取后端持有的 recorder.js（唯一源，与 GET /recordings/recorder.js 同文件）。"""
    try:
        return _RECORDER_JS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"recorder.js asset unreadable: {_RECORDER_JS_PATH}") from exc


class CdpAttachDriver(BrowserDriver):
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
        self._endpoint = endpoint
        self._recording_id = recording_id
        self._connect_factory = connect_factory  # 测试注入口；生产为 None → playwright
        self._channel = EventChannel()
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._binding_name = DEFAULT_BINDING_NAME
        self._closed = False

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

    async def _binding_handler(self, source: dict[str, Any], payload: Any) -> None:
        """expose_binding 回调（context 级，所有 frame 的事件汇入同一通道）。"""
        if isinstance(payload, dict):
            self._channel.publish(payload)
        else:  # 防御：非对象载荷丢弃并计数（不阻塞采集通道）
            logger.warning(
                "cdp-attach binding payload is not a dict, dropped: recording_id=%s seq=%s",
                self._recording_id,
                (payload or {}).get("seq") if isinstance(payload, dict) else None,
            )

    def _pick_context_and_page(self, browser: Any) -> tuple[Any, Any]:
        """复用既有 context 与 page（登录态在用户浏览器里，绝不新建 profile）。"""
        contexts = list(getattr(browser, "contexts", None) or [])
        context = contexts[0] if contexts else browser.new_context()
        pages = list(getattr(context, "pages", None) or [])
        page = pages[0] if pages else context.new_page()
        return context, page

    async def _inject_into_current_pages(self, script: str) -> None:
        """对 context 下已打开 page 立即注入（init_script 只对新文档生效）。"""
        pages = list(getattr(self._context, "pages", None) or [])
        for page in pages:
            last_error: Exception | None = None
            for delay in (0.0, *_INJECT_RETRY_DELAYS):
                try:
                    await page.evaluate(script)
                    last_error = None
                    break
                except Exception as exc:  # noqa: BLE001 页面瞬态不可用：重试
                    last_error = exc
                    await asyncio.sleep(delay)
            if last_error is not None:
                logger.warning(
                    "cdp-attach inject skipped on one page: recording_id=%s url=%s error=%s",
                    self._recording_id,
                    getattr(page, "url", "?"),
                    last_error,
                )

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

    async def inject_recorder(self, binding_name: str = DEFAULT_BINDING_NAME) -> None:
        """context 级 binding + init_script；对已开 page 立即 evaluate。"""
        if self._closed or self._context is None:
            raise RuntimeError(
                f"cdp-attach inject before open: recording_id={self._recording_id}"
            )
        self._binding_name = binding_name
        script = _load_recorder_script()
        # 先注册 binding（recorder.js emit 时 binding 已就绪；其内部另有 2s 缓冲兜底）
        await self._context.expose_binding(binding_name, self._binding_handler)
        await self._context.add_init_script(script)
        await self._inject_into_current_pages(script)
        logger.info(
            "cdp-attach recorder injected: recording_id=%s binding=%s pages=%s",
            self._recording_id,
            binding_name,
            len(list(getattr(self._context, "pages", None) or [])),
        )

    async def on_recorder_event(self) -> AsyncIterator[dict[str, Any]]:
        return self._channel.iterate()

    async def capture_screenshot(self) -> bytes:
        if self._page is None:
            raise RuntimeError(f"cdp-attach screenshot before open: {self._recording_id}")
        return await self._page.screenshot(type="png")

    async def current_page_info(self) -> dict[str, Any]:
        if self._page is None:
            return {}
        try:
            info = await self._page.evaluate(
                "() => ({url: location.href, title: document.title,"
                " vw: window.innerWidth, vh: window.innerHeight, dpr: window.devicePixelRatio})"
            )
        except Exception:  # noqa: BLE001 页面导航间隙取不到 → 空（调用方容忍）
            return {}
        return {
            "url": info.get("url"),
            "title": info.get("title"),
            "viewport": (info.get("vw"), info.get("vh")),
            "dpr": info.get("dpr"),
        }

    async def set_capture_enabled(self, enabled: bool) -> None:
        if self._page is None:
            raise RuntimeError(f"cdp-attach toggle before open: {self._recording_id}")
        await self._page.evaluate(
            "enabled => window.__qaRecorderSetEnabled(!!enabled)", bool(enabled)
        )
        logger.info(
            "cdp-attach capture toggle: recording_id=%s enabled=%s", self._recording_id, enabled
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
