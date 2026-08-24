"""录制驱动注册表（方案 5.3）：embedded + cdp-attach + playwright-managed。"""

from __future__ import annotations

from functools import partial

from src.core.config import Settings
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
from src.application.recorder.drivers.playwright_managed import (
    PlaywrightManagedDriver,
    playwright_managed_factory,
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
    "PlaywrightManagedDriver",
    "playwright_managed_factory",
    "CMD_NAVIGATE",
    "CMD_SET_CAPTURE",
    "CMD_CLOSE",
    "build_default_registry",
]


def build_default_registry(
    bridge: EmbeddedBridge | None = None,
    *,
    settings: Settings | None = None,
) -> DriverRegistry:
    """默认注册表：embedded（桌面端）+ cdp-attach（外部浏览器）+ playwright-managed（服务端自启）。"""

    registry = DriverRegistry()
    owned_bridge = bridge or EmbeddedBridge()

    def _embedded_factory(_config, *, recording_id: str, **_context) -> EmbeddedDriver:
        return owned_bridge.attach(recording_id)

    registry.register("embedded", _embedded_factory)
    registry.register("cdp-attach", cdp_attach_factory)
    registry.register(
        "playwright-managed", partial(playwright_managed_factory, settings=settings)
    )
    return registry
