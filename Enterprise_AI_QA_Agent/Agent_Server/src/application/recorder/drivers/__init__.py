"""录制驱动注册表（方案 5.3）：P0 内置 embedded，P1/P3 扩展位就绪。"""

from __future__ import annotations

from src.application.recorder.drivers.base import (
    BrowserDriver,
    DriverRegistry,
    EventChannel,
)
from src.application.recorder.drivers.embedded_bridge import (
    CMD_CLOSE,
    CMD_NAVIGATE,
    CMD_SET_CAPTURE,
    EmbeddedBridge,
    EmbeddedDriver,
    IngestResult,
)

__all__ = [
    "BrowserDriver",
    "DriverRegistry",
    "EventChannel",
    "EmbeddedBridge",
    "EmbeddedDriver",
    "IngestResult",
    "CMD_NAVIGATE",
    "CMD_SET_CAPTURE",
    "CMD_CLOSE",
    "build_default_registry",
]


def build_default_registry(bridge: EmbeddedBridge | None = None) -> DriverRegistry:
    """默认注册表：embedded 可用；cdp-attach / playwright-managed 于 P1 注册。"""

    registry = DriverRegistry()
    owned_bridge = bridge or EmbeddedBridge()

    def _embedded_factory(_config, *, recording_id: str, **_context) -> EmbeddedDriver:
        return owned_bridge.attach(recording_id)

    registry.register("embedded", _embedded_factory)
    return registry
