"""P0-5 驱动层纯单测（不连 Electron 客户端）。

覆盖：接口契约（BrowserDriver 全方法实现）、注册表语义、embedded
桥接生命周期（登记握手/事件转发幂等/指令轮询/截图缓存/关闭回收）。
端到端联调见 P0-9。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

import pytest

from src.application.recorder.drivers import (
    CMD_CLOSE,
    CMD_NAVIGATE,
    CMD_SET_CAPTURE,
    BrowserDriver,
    DriverRegistry,
    EmbeddedBridge,
    EmbeddedDriver,
    build_default_registry,
)
from src.schemas.recording import (
    RecorderEvent,
    RecordingDriverConfig,
    RecordingDriverKind,
)


def _event(seq: int, *, url: str = "https://app.example.com/home", type_: str = "click") -> RecorderEvent:
    return RecorderEvent(
        seq=seq,
        type=type_,
        timestamp=datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc),
        page={"url": url, "title": "Home", "viewport": {"w": 1440, "h": 960}, "dpr": 1},
        target={"tag": "BUTTON"},
        pixel=None,
        value=None,
        page_effect={},
    )


# ---------------------------------------------------------------- 契约


def test_embedded_driver_implements_browser_driver_contract() -> None:
    """EmbeddedDriver 是 BrowserDriver 子类且契约方法齐全（方案 5.1）。"""
    assert issubclass(EmbeddedDriver, BrowserDriver)
    for name in (
        "open",
        "inject_recorder",
        "on_recorder_event",
        "capture_screenshot",
        "current_page_info",
        "set_capture_enabled",
        "close",
    ):
        assert name in dir(EmbeddedDriver), f"missing contract method: {name}"
        assert inspect.iscoroutinefunction(getattr(EmbeddedDriver, name)), f"{name} must be async"
    assert EmbeddedDriver.kind == "embedded"


def test_abstract_driver_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        BrowserDriver()  # type: ignore[abstract]


# ---------------------------------------------------------------- 注册表


def test_registry_register_and_create() -> None:
    bridge = EmbeddedBridge()
    registry = build_default_registry(bridge)
    assert registry.kinds() == ["embedded"]

    driver = registry.create(
        RecordingDriverConfig(kind=RecordingDriverKind.embedded),
        recording_id="rec-1",
    )
    assert isinstance(driver, EmbeddedDriver)
    assert driver.recording_id == "rec-1"
    # 同 recording_id 复用同一实例
    again = registry.create(RecordingDriverConfig(), recording_id="rec-1")
    assert again is driver


def test_registry_rejects_blank_duplicate_and_unknown_kind() -> None:
    registry = DriverRegistry()

    with pytest.raises(ValueError):
        registry.register("", lambda config, **ctx: None)

    registry.register("embedded", lambda config, **ctx: None)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("embedded", lambda config, **ctx: None)

    with pytest.raises(ValueError, match="unknown recorder driver kind"):
        registry.create(RecordingDriverConfig(kind=RecordingDriverKind.cdp_attach))


# ---------------------------------------------------------------- embedded 桥接


def test_bridge_attach_validates_recording_id() -> None:
    bridge = EmbeddedBridge()
    with pytest.raises(ValueError, match="recording_id"):
        bridge.attach("  ")


def test_full_embedded_lifecycle() -> None:
    """attach → open(指令) → 登记 → 事件转发 → 控制 → 截图 → close。

    全程单一事件循环（与 FastAPI 运行形态一致；asyncio.Queue 绑定首个
    消费循环，不可跨 asyncio.run 复用）。
    """

    async def _scenario() -> None:
        bridge = EmbeddedBridge()
        driver = bridge.attach("rec-100")

        # open → navigate 指令入队
        await driver.open("https://app.example.com", viewport=(1440, 960))
        commands = await bridge.poll_commands("rec-100")
        assert commands == [
            {
                "action": CMD_NAVIGATE,
                "payload": {"url": "https://app.example.com", "viewport": {"w": 1440, "h": 960}},
                "recording_id": "rec-100",
            }
        ]

        # 未登记前 wait_ready 不可就绪；Electron 注入完成 → 登记
        assert bridge.is_registered("rec-100") is False
        assert bridge.register_session("rec-100", {"window_id": "w1", "injected": True}) is True
        assert await driver.wait_ready(timeout=0.5) is True
        assert bridge.is_registered("rec-100") is True
        assert bridge.register_session("rec-100") is True  # 重复登记幂等

        # 事件上报 → 通道可消费（顺序保持）
        result = bridge.ingest_events("rec-100", [_event(0), _event(1)])
        assert result.forwarded == 2
        assert result.duplicates_in_batch == 0
        assert result.duplicates_retry == 0
        assert result.rejected_reason is None

        async def _consume(n: int) -> list[dict]:
            collected: list[dict] = []
            stream = await driver.on_recorder_event()
            async for ev in stream:
                collected.append(ev)
                if len(collected) >= n:
                    break
            return collected

        events = await asyncio.wait_for(_consume(2), timeout=1)
        assert [e["seq"] for e in events] == [0, 1]
        assert events[0]["target"]["tag"] == "BUTTON"

        # 页面信息来自最近事件
        page_info = await driver.current_page_info()
        assert page_info["url"] == "https://app.example.com/home"

        # 控制指令：set_capture_enabled → 轮询可见
        await driver.set_capture_enabled(False)
        commands = await bridge.poll_commands("rec-100")
        assert commands == [
            {
                "action": CMD_SET_CAPTURE,
                "payload": {"enabled": False},
                "recording_id": "rec-100",
            }
        ]

        # 截图：未上报前报错，上报后返回最近帧
        with pytest.raises(RuntimeError, match="screenshot not available"):
            await driver.capture_screenshot()
        assert bridge.report_screenshot("rec-100", b"png-bytes") is True
        assert await driver.capture_screenshot() == b"png-bytes"

        # close：指令下发 + 会话标记关闭（state 保留，Electron 仍可拉 close 指令）
        await driver.close()
        commands = await bridge.poll_commands("rec-100", wait_seconds=0)
        assert commands[0]["action"] == CMD_CLOSE

        # 关闭后：事件被拒、指令被拒、驱动操作报错、重建被拒（需先 detach）
        rejected = bridge.ingest_events("rec-100", [_event(2)])
        assert rejected.rejected_reason == "session_closed"
        assert bridge.enqueue_command("rec-100", CMD_SET_CAPTURE, {"enabled": True}) is False
        with pytest.raises(RuntimeError, match="already closed"):
            await driver.set_capture_enabled(True)
        with pytest.raises(ValueError, match="closed but not detached"):
            bridge.attach("rec-100")

        # detach：终态清理后彻底不可见，可重新 attach
        assert bridge.detach("rec-100") is True
        assert bridge.session_stats("rec-100") == {}
        assert await bridge.poll_commands("rec-100") == []
        new_driver = bridge.attach("rec-100")
        assert new_driver is not driver
        assert bridge.is_registered("rec-100") is False

    asyncio.run(_scenario())


def test_ingest_is_idempotent_on_retry() -> None:
    """重复批次/批内重复 seq 幂等收敛，不重复入通道。"""
    bridge = EmbeddedBridge()
    bridge.attach("rec-200")

    first = bridge.ingest_events("rec-200", [_event(0), _event(1), _event(1)])
    assert (first.forwarded, first.duplicates_in_batch, first.duplicates_retry) == (2, 1, 0)

    retry = bridge.ingest_events("rec-200", [_event(0), _event(1), _event(2)])
    assert (retry.forwarded, retry.duplicates_in_batch, retry.duplicates_retry) == (1, 0, 2)

    stats = bridge.session_stats("rec-200")
    assert stats["forwarded_seqs"] == 3
    assert stats["duplicates"] == 3  # 1 批内 + 2 重试


def test_ingest_unknown_recording_rejected() -> None:
    bridge = EmbeddedBridge()
    result = bridge.ingest_events("rec-unknown", [_event(0)])
    assert result.forwarded == 0
    assert result.rejected_reason == "unknown_recording"
    assert asyncio.run(bridge.poll_commands("rec-unknown")) == []
    assert bridge.register_session("rec-unknown") is False
    assert bridge.report_screenshot("rec-unknown", b"x") is False
    assert bridge.session_stats("rec-unknown") == {}


def test_poll_commands_long_poll_waits_for_command() -> None:
    """wait_seconds>0 时挂起等待指令到达（long-poll 语义）。"""

    async def _scenario() -> None:
        bridge = EmbeddedBridge()
        driver = bridge.attach("rec-300")

        async def _delayed_toggle() -> None:
            await asyncio.sleep(0.1)
            await driver.set_capture_enabled(True)

        toggle = asyncio.create_task(_delayed_toggle())
        commands = await asyncio.wait_for(
            bridge.poll_commands("rec-300", wait_seconds=2.0), timeout=1.0
        )
        await toggle
        assert len(commands) == 1
        assert commands[0]["action"] == CMD_SET_CAPTURE

        # 空队列 + wait_seconds>0 → 挂起超时后返回空
        empty = await asyncio.wait_for(
            bridge.poll_commands("rec-300", wait_seconds=0.05), timeout=1.0
        )
        assert empty == []

    asyncio.run(_scenario())


def test_default_registry_embedded_kind_available() -> None:
    registry = build_default_registry()
    assert registry.is_registered("embedded") is True
    assert registry.is_registered("cdp-attach") is False
