"""embedded 驱动与 Electron 客户端桥接（方案 5.2① / 6.3，P0-5）。

架构事实：embedded 模式下真实浏览器运行在 Electron 客户端进程，后端无法
直接 attach。后端侧 EmbeddedDriver 是桥接代理，三条通道：

- 上行事件：Electron CDP Runtime.addBinding("__qaRecordEmit") 收到事件后
  批量 POST /api/v1/recordings/{id}/events:batch → 路由层调
  EmbeddedBridge.ingest_events → 驱动事件通道 → on_recorder_event 消费；
- 下行控制：后端 open/set_capture_enabled/close 产生指令入队，Electron
  轮询 poll_commands 拉取执行（navigate / __qaRecorderSetEnabled / 关窗）；
- 登记握手：open 下发 navigate 指令后，Electron 创建窗口、attach
  debugger、注入 recorder.js 完成时调 register_session，驱动进入就绪。

事件幂等：bridge 记录已转发 seq（与 PG (recording_id, seq) 唯一约束同键），
重复批次在此提前收敛，duplicates 计数可见——DB 层 ON CONFLICT 仍为最终防线。

不连客户端纯单测见 tests/test_recorder_drivers.py；与 Electron 侧的端到端
联调在 P0-9 完成后验证。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from src.schemas.recording import (
    RecorderEvent,
    RecordingDriverKind,
    dedupe_event_batch,
)

from .base import BrowserDriver, EventChannel

logger = logging.getLogger(__name__)

# 下发给 Electron 的指令动作（poll_commands 协议）
CMD_NAVIGATE = "navigate"
CMD_SET_CAPTURE = "set_capture_enabled"
CMD_CLOSE = "close"


@dataclass
class _EmbeddedSessionState:
    """单个录制会话在 bridge 侧的登记与转发状态。"""

    recording_id: str
    driver: EmbeddedDriver
    registered: bool = False
    capabilities: dict[str, Any] = field(default_factory=dict)
    # 已转发事件 seq（幂等预收敛；DB 唯一约束为最终防线）
    seen_seqs: set[int] = field(default_factory=set)
    duplicates: int = 0
    last_page_info: dict[str, Any] = field(default_factory=dict)
    last_screenshot: bytes | None = None
    closed: bool = False


@dataclass
class IngestResult:
    """ingest_events 回执：转发/批内去重/幂等去重计数（日志与测试可见）。"""

    forwarded: int
    duplicates_in_batch: int
    duplicates_retry: int
    rejected_reason: str | None = None


class EmbeddedBridge:
    """进程级桥接器：登记 Electron 侧会话、转发事件、缓存下行指令。

    由 app.state 持有单例（P0-7 main.py lifespan 初始化）；API 路由与
    RecorderSessionService 均经此与 Electron 客户端交互。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _EmbeddedSessionState] = {}

    # ------------------------------------------------------------ 登记

    def attach(self, recording_id: str) -> EmbeddedDriver:
        """创建（或复用）录制会话的 embedded 驱动实例。

        驱动实例与会话一一对应；viewport/entry_url 等经 open() 指令下发。
        """
        recording_id = (recording_id or "").strip()
        if not recording_id:
            raise ValueError("embedded bridge requires recording_id")
        state = self._sessions.get(recording_id)
        if state is not None:
            if state.closed:
                # closed 未 detach：保留 state 供 Electron 拉取 close 指令，
                # 此期间禁止重建（否则 close 指令随覆盖丢失）
                raise ValueError(
                    f"embedded session closed but not detached: {recording_id}"
                )
            return state.driver
        driver = EmbeddedDriver(recording_id=recording_id, bridge=self)
        self._sessions[recording_id] = _EmbeddedSessionState(
            recording_id=recording_id, driver=driver
        )
        logger.info("embedded bridge session attached: recording_id=%s", recording_id)
        return driver

    def register_session(self, recording_id: str, capabilities: dict[str, Any] | None = None) -> bool:
        """Electron 侧窗口创建 + 注入完成后的登记（就绪握手）。"""
        state = self._sessions.get(recording_id)
        if state is None or state.closed:
            logger.warning(
                "embedded register for unknown/closed session: recording_id=%s", recording_id
            )
            return False
        state.registered = True
        state.capabilities = dict(capabilities or {})
        state.driver._mark_registered()
        logger.info(
            "embedded bridge session registered: recording_id=%s capabilities=%s",
            recording_id,
            state.capabilities,
        )
        return True

    def is_registered(self, recording_id: str) -> bool:
        state = self._sessions.get(recording_id)
        return bool(state and state.registered and not state.closed)

    async def wait_registered(self, recording_id: str, timeout: float = 30.0) -> bool:
        """等待 Electron 登记完成（launching → ready 的判定依据）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_registered(recording_id):
                return True
            await asyncio.sleep(0.1)
        return self.is_registered(recording_id)

    # ------------------------------------------------------------ 上行：事件

    def ingest_events(self, recording_id: str, events: list[RecorderEvent]) -> IngestResult:
        """Electron 批量上报入口：校验登记 → 批内去重 → 幂等预收敛 → 转发。

        同步方法（纯内存操作，无阻塞 IO），供 async 路由直接调用。
        """
        state = self._sessions.get(recording_id)
        if state is None:
            return IngestResult(0, 0, 0, rejected_reason="unknown_recording")
        if state.closed:
            return IngestResult(0, 0, 0, rejected_reason="session_closed")

        deduped = dedupe_event_batch(events)
        duplicates_in_batch = len(events) - len(deduped)

        forwarded = 0
        duplicates_retry = 0
        for event in deduped:
            if event.seq in state.seen_seqs:
                duplicates_retry += 1
                continue
            payload = event.model_dump(mode="json")
            if state.driver._publish_event(payload):
                state.seen_seqs.add(event.seq)
                forwarded += 1
                page = payload.get("page")
                if isinstance(page, dict) and page.get("url"):
                    state.last_page_info = page
        state.duplicates += duplicates_in_batch + duplicates_retry
        return IngestResult(
            forwarded=forwarded,
            duplicates_in_batch=duplicates_in_batch,
            duplicates_retry=duplicates_retry,
        )

    def report_screenshot(self, recording_id: str, data: bytes) -> bool:
        """Electron 截图上报：缓存最近一帧（capture_screenshot 的数据源）。"""
        state = self._sessions.get(recording_id)
        if state is None or state.closed:
            return False
        state.last_screenshot = data
        return True

    # ------------------------------------------------------------ 下行：指令

    def enqueue_command(self, recording_id: str, action: str, payload: dict[str, Any]) -> bool:
        state = self._sessions.get(recording_id)
        if state is None or state.closed:
            return False
        state.driver._enqueue_command(action, payload)
        return True

    async def poll_commands(self, recording_id: str, wait_seconds: float = 0.0) -> list[dict[str, Any]]:
        """Electron 轮询下行指令；wait_seconds>0 时最多挂起该时长（long-poll）。

        会话 close 后仍允许 drain（Electron 需要拉到 close 指令才能关窗）；
        detach 后彻底不可见。
        """
        state = self._sessions.get(recording_id)
        if state is None:
            return []
        timeout = wait_seconds if wait_seconds > 0 else 0.0
        return await state.driver._drain_commands(timeout)

    # ------------------------------------------------------------ 生命周期

    def close_session(self, recording_id: str) -> bool:
        """标记会话关闭：事件/指令入口立即拒绝，state 保留供 Electron 拉取
        close 指令；终态清理由 detach 完成。"""
        state = self._sessions.get(recording_id)
        if state is None:
            return False
        if not state.closed:
            state.closed = True
            state.driver._mark_closed()
            logger.info(
                "embedded bridge session closed: recording_id=%s duplicates=%s",
                recording_id,
                state.duplicates,
            )
        return True

    def detach(self, recording_id: str) -> bool:
        """终态清理：Electron 处理完 close 指令（或超时兜底）后移除 state。"""
        state = self._sessions.pop(recording_id, None)
        if state is None:
            return False
        if not state.closed:
            state.closed = True
            state.driver._mark_closed()
        logger.info("embedded bridge session detached: recording_id=%s", recording_id)
        return True

    def session_stats(self, recording_id: str) -> dict[str, Any]:
        """会话转发统计（诊断/测试用）。"""
        state = self._sessions.get(recording_id)
        if state is None:
            return {}
        return {
            "registered": state.registered,
            "closed": state.closed,
            "forwarded_seqs": len(state.seen_seqs),
            "duplicates": state.duplicates,
        }


class EmbeddedDriver(BrowserDriver):
    """embedded 驱动的后端侧代理（真实浏览器在 Electron 客户端）。"""

    kind = RecordingDriverKind.embedded.value

    def __init__(self, *, recording_id: str, bridge: EmbeddedBridge) -> None:
        self._recording_id = recording_id
        self._bridge = bridge
        self._channel = EventChannel()
        self._commands: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._registered_event = asyncio.Event()
        self._binding_name = "__qaRecordEmit"
        self._closed = False

    # ------------------------------------------------------------ 内部

    def _mark_registered(self) -> None:
        self._registered_event.set()

    def _mark_closed(self) -> None:
        self._closed = True
        self._channel.close()

    def _publish_event(self, payload: dict[str, Any]) -> bool:
        return self._channel.publish(payload)

    def _enqueue_command(self, action: str, payload: dict[str, Any]) -> None:
        self._commands.put_nowait(
            {"action": action, "payload": payload, "recording_id": self._recording_id}
        )

    async def _drain_commands(self, wait_seconds: float) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        try:
            if self._commands.empty() and wait_seconds > 0:
                first = await asyncio.wait_for(
                    self._commands.get(), timeout=wait_seconds
                )
                commands.append(first)
        except asyncio.TimeoutError:
            return commands
        while not self._commands.empty():
            commands.append(self._commands.get_nowait())
        return commands

    # ------------------------------------------------------------ BrowserDriver 契约

    @property
    def recording_id(self) -> str:
        return self._recording_id

    async def open(self, url: str, *, viewport: tuple[int, int]) -> None:
        """向 Electron 下发 navigate 指令（窗口创建 + 注入由客户端完成）。"""
        if self._closed:
            raise RuntimeError(f"embedded driver already closed: {self._recording_id}")
        self._enqueue_command(
            CMD_NAVIGATE,
            {"url": url, "viewport": {"w": viewport[0], "h": viewport[1]}},
        )
        logger.info(
            "embedded navigate command issued: recording_id=%s url=%s", self._recording_id, url
        )

    async def inject_recorder(self, binding_name: str = "__qaRecordEmit") -> None:
        """记录 binding 名；实际注入由 Electron attach-debugger 时执行
        （recorder.js 由后端持有统一下发，见 P0-7 路由）。"""
        self._binding_name = binding_name
        logger.info(
            "embedded recorder injection handled by client: recording_id=%s binding=%s",
            self._recording_id,
            binding_name,
        )

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        """等待 Electron 登记握手完成（open 后调用）。"""
        if self._closed:
            return False
        try:
            await asyncio.wait_for(self._registered_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "embedded driver wait_ready timeout: recording_id=%s timeout=%s",
                self._recording_id,
                timeout,
            )
            return False

    async def on_recorder_event(self) -> AsyncIterator[dict[str, Any]]:
        return self._channel.iterate()

    async def capture_screenshot(self) -> bytes:
        """返回 Electron 最近上报帧；尚无帧时报错（调用方决定重试/降级）。"""
        state = self._bridge._sessions.get(self._recording_id)
        data = state.last_screenshot if state else None
        if not data:
            raise RuntimeError(
                f"embedded screenshot not available yet: recording_id={self._recording_id}"
            )
        return data

    async def current_page_info(self) -> dict[str, Any]:
        state = self._bridge._sessions.get(self._recording_id)
        if state is None:
            return {}
        return dict(state.last_page_info)

    async def set_capture_enabled(self, enabled: bool) -> None:
        """下发采集开关指令（暂停/继续），客户端转 __qaRecorderSetEnabled。"""
        if self._closed:
            raise RuntimeError(f"embedded driver already closed: {self._recording_id}")
        self._enqueue_command(CMD_SET_CAPTURE, {"enabled": bool(enabled)})
        logger.info(
            "embedded capture toggle issued: recording_id=%s enabled=%s",
            self._recording_id,
            enabled,
        )

    async def close(self) -> None:
        """下发 close 指令（Electron 关窗）并回收 bridge 侧会话。"""
        if not self._closed:
            self._enqueue_command(CMD_CLOSE, {})
        self._bridge.close_session(self._recording_id)
