"""浏览器驱动接口契约与注册表（方案 5.1 / 5.3，P0-5）。

所有驱动实现同一契约：录制脚本（recorder.js）与事件协议与驱动无关，
切换浏览器 = 换驱动实现，产物格式不变（cdp-attach / playwright-managed
于 P1 落地，ego-lite 于 P3 经同一注册表接入，零协议改动）。

接口契约单测见 tests/test_recorder_drivers.py。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable

from src.schemas.recording import RecordingDriverConfig

logger = logging.getLogger(__name__)


class BrowserDriver(ABC):
    """驱动契约（方案 5.1）。

    生命周期：open → inject_recorder → on_recorder_event 消费循环
    →（set_capture_enabled 控制暂停/继续）→ close。
    """

    kind: str  # "embedded" | "cdp-attach" | "playwright-managed"

    @abstractmethod
    async def open(self, url: str, *, viewport: tuple[int, int]) -> None:
        """打开目标页面（embedded 语义为向客户端下发 navigate 指令）。"""
        raise NotImplementedError

    @abstractmethod
    async def inject_recorder(self, binding_name: str = "__qaRecordEmit") -> None:
        """注入 recorder.js 并建立事件回传 binding。"""
        raise NotImplementedError

    @abstractmethod
    async def on_recorder_event(self) -> AsyncIterator[dict[str, Any]]:
        """返回录制事件异步迭代器（驱动侧 → 消费方，如 RecorderSessionService）。"""
        raise NotImplementedError

    @abstractmethod
    async def capture_screenshot(self) -> bytes:
        """抓取当前视口截图（PNG bytes）。"""
        raise NotImplementedError

    @abstractmethod
    async def current_page_info(self) -> dict[str, Any]:
        """当前页面信息：url/title/viewport/dpr。"""
        raise NotImplementedError

    @abstractmethod
    async def set_capture_enabled(self, enabled: bool) -> None:
        """采集开关（暂停/继续），对应 recorder.js __qaRecorderSetEnabled。"""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """关闭驱动资源（embedded 语义为向客户端下发 close 指令）。"""
        raise NotImplementedError


DriverFactory = Callable[..., BrowserDriver]
"""工厂签名：(config, **context) -> BrowserDriver。

context 携带驱动实现所需的会话上下文（如 recording_id）；不依赖 context
的驱动（playwright-managed 等）以 **_context 忽略。
"""


class DriverRegistry:
    """kind → 驱动工厂注册表（方案 5.3 切换机制）。

    审批卡片上给出的可用驱动即注册表当前 kinds；创建录制会话时按
    RecordingDriverConfig.kind 实例化对应实现。
    """

    def __init__(self) -> None:
        self._factories: dict[str, DriverFactory] = {}

    def register(self, kind: str, factory: DriverFactory) -> None:
        normalized = (kind or "").strip()
        if not normalized:
            raise ValueError("driver kind must not be blank")
        if normalized in self._factories:
            raise ValueError(f"driver kind already registered: {normalized}")
        self._factories[normalized] = factory
        logger.info("recorder driver registered: kind=%s", normalized)

    def is_registered(self, kind: str) -> bool:
        return (kind or "").strip() in self._factories

    def kinds(self) -> list[str]:
        return sorted(self._factories)

    def create(self, config: RecordingDriverConfig, **context: Any) -> BrowserDriver:
        kind_value = config.kind.value if hasattr(config.kind, "value") else str(config.kind)
        normalized = (kind_value or "").strip()
        factory = self._factories.get(normalized)
        if factory is None:
            raise ValueError(
                f"unknown recorder driver kind: {normalized!r} "
                f"(registered: {', '.join(self._factories) or 'none'})"
            )
        driver = factory(config, **context)
        logger.info(
            "recorder driver created: kind=%s endpoint=%s viewport=%s",
            normalized,
            config.endpoint,
            config.viewport,
        )
        return driver


class EventChannel:
    """驱动侧事件通道：publish → 消费方 iterate。

    独立于具体驱动（embedded 由 bridge 转发喂入；playwright 驱动在
    binding 回调里喂入），提供容量保护与关闭语义。
    """

    def __init__(self, maxsize: int = 10000) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._dropped = 0
        self._closed = False

    @property
    def dropped(self) -> int:
        """因队列满被丢弃的事件数（异常场景才非 0，出问题时凭日志还原）。"""
        return self._dropped

    def publish(self, event: dict[str, Any]) -> bool:
        """非阻塞发布；队列满时丢弃并计数（宁可漏单条，不可阻塞采集通道）。"""
        if self._closed:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self._dropped += 1
            logger.error(
                "recorder event channel full, event dropped: seq=%s dropped_total=%s",
                event.get("seq"),
                self._dropped,
            )
            return False

    async def iterate(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            event = await self._queue.get()
            yield event

    def close(self) -> None:
        self._closed = True
