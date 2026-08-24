"""录制驱动注册表（方案 5.3）：embedded + cdp-attach；playwright-managed 于 P1-2 注册。"""

from __future__ import annotations

from src.application.recorder.drivers.base import (
    BrowserDriver,
    DriverRegistry,
    EventChannel,
)
from src.application.recorder.drivers.cdp_attach import (
    CdpAttachDriver,
    cdp_attach_factory,
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
    "CdpAttachDriver",
    "cdp_attach_factory",
    "CMD_NAVIGATE",
    "CMD_SET_CAPTURE",
    "CMD_CLOSE",
    "build_default_registry",
]


def build_default_registry(bridge: EmbeddedBridge | None = None) -> DriverRegistry:
    """默认注册表：embedded（桌面端）+ cdp-attach（外部浏览器，P1-1）。"""

    registry = DriverRegistry()
    owned_bridge = bridge or EmbeddedBridge()

    def _embedded_factory(_config, *, recording_id: str, **_context) -> EmbeddedDriver:
        return owned_bridge.attach(recording_id)

    registry.register("embedded", _embedded_factory)
    registry.register("cdp-attach", cdp_attach_factory)
    return registry
