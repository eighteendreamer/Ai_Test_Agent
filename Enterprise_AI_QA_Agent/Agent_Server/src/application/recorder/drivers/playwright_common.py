"""Playwright 系驱动公共基类（P1-1/P1-2）。

cdp-attach 与 playwright-managed 共用同一注入协议与事件通道
（context.expose_binding + add_init_script + 已开页 evaluate 立即生效），
差异仅在浏览器如何获得（attach 外部 / 自启受管）与 close 语义。
子类负责 ``open`` 中设置 ``self._context`` / ``self._page`` 并实现 ``close``。
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

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


class PlaywrightBindingDriverBase(BrowserDriver):
    """binding 通道 + 注入 + 控制/查询的通用实现。

    生命周期约定：子类 ``open`` 完成 playwright 连接并把既有 context/page
    绑定到 ``self._context`` / ``self._page``；本基类提供其余契约方法。
    """

    def __init__(self, *, recording_id: str) -> None:
        self._recording_id = recording_id
        self._channel = EventChannel()
        self._context: Any | None = None
        self._page: Any | None = None
        self._binding_name = DEFAULT_BINDING_NAME
        self._closed = False

    # ------------------------------------------------------------ 内部

    async def _binding_handler(self, source: dict[str, Any], payload: Any) -> None:
        """expose_binding 回调（context 级，所有 frame 的事件汇入同一通道）。

        recorder.js 按协议传 JSON 字符串（Electron CDP addBinding 链路同款，
        见 P0-9 recorder-window.mjs 的 JSON.parse）；兼容 dict（测试/未来
        序列化变更）。解析失败丢弃计数，不阻塞采集通道。
        """
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                logger.warning(
                    "playwright binding payload is not valid JSON, dropped: "
                    "recording_id=%s head=%s",
                    self._recording_id,
                    payload[:80],
                )
                return
        if isinstance(payload, dict):
            self._channel.publish(payload)
        else:
            logger.warning(
                "playwright binding payload is not an object, dropped: recording_id=%s",
                self._recording_id,
            )

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
                    "inject skipped on one page: recording_id=%s url=%s error=%s",
                    self._recording_id,
                    getattr(page, "url", "?"),
                    last_error,
                )

    # ------------------------------------------------------------ BrowserDriver 契约（通用部分）

    async def inject_recorder(self, binding_name: str = DEFAULT_BINDING_NAME) -> None:
        """context 级 binding + init_script；对已开 page 立即 evaluate。"""
        if self._closed or self._context is None:
            raise RuntimeError(
                f"{self.kind} inject before open: recording_id={self._recording_id}"
            )
        self._binding_name = binding_name
        script = _load_recorder_script()
        # 先注册 binding（recorder.js emit 时 binding 已就绪；其内部另有 2s 缓冲兜底）
        await self._context.expose_binding(binding_name, self._binding_handler)
        await self._context.add_init_script(script)
        await self._inject_into_current_pages(script)
        logger.info(
            "%s recorder injected: recording_id=%s binding=%s pages=%s",
            self.kind,
            self._recording_id,
            binding_name,
            len(list(getattr(self._context, "pages", None) or [])),
        )

    async def on_recorder_event(self) -> AsyncIterator[dict[str, Any]]:
        return self._channel.iterate()

    async def capture_screenshot(self) -> bytes:
        if self._page is None:
            raise RuntimeError(f"{self.kind} screenshot before open: recording_id={self._recording_id}")
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
            raise RuntimeError(f"{self.kind} toggle before open: recording_id={self._recording_id}")
        await self._page.evaluate(
            "enabled => window.__qaRecorderSetEnabled(!!enabled)", bool(enabled)
        )
        logger.info(
            "%s capture toggle: recording_id=%s enabled=%s",
            self.kind,
            self._recording_id,
            enabled,
        )
