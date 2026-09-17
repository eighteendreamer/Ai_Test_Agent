"""playwright-managed 驱动：服务端自启受管浏览器（方案 5.2③，P1-2）。

场景：非桌面端部署（纯浏览器访问工作台）时，embedded 不可用、用户本地
浏览器无 CDP 端口——由后端启动一个有头 Chromium（headed + persistent
profile），用户在弹出的受管窗口里操作，登录态持久化到产物目录
（同一 recording 独占，不跨录制复用登录态）。

与 cdp-attach 的差异（其余契约共用 playwright_common）：
- open：经 PythonPlaywrightCliRuntime 创建 headed persistent context；
- close：复用运行时租约和取消清理路径关窗（与 cdp-attach
  只断连相反）；磁盘 profile 保留。

启动和收尾复用 PythonPlaywrightCliRuntime：headed
强制开启（受管窗口必须可见）、viewport 取驱动配置、persistent profile
落 artifact_root 下。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

from src.core.config import Settings, get_settings
from src.application.runtime.python_playwright_cli import PythonPlaywrightCliRuntime
from src.schemas.recording import RecordingDriverConfig

from .playwright_common import PlaywrightBindingDriverBase

logger = logging.getLogger(__name__)

_RECORDER_PROFILE_SUBDIR = "recorder-profile"


class PlaywrightManagedDriver(PlaywrightBindingDriverBase):
    """受管模式驱动：浏览器生命周期归驱动（自启自关），profile 持久化。"""

    kind = "playwright-managed"

    def __init__(
        self,
        *,
        config: RecordingDriverConfig,
        recording_id: str,
        settings: Settings | None = None,
        resource_manager=None,
    ) -> None:
        super().__init__(recording_id=recording_id)
        self._viewport = tuple(config.viewport)
        base = (
            Path(__file__).resolve().parents[2] / str(getattr(settings.storage, "artifact_root_dir", "data/artifacts"))
            if settings is not None
            else Path(__file__).resolve().parents[2] / "data" / "artifacts"
        )
        self._scope = f"recording:{recording_id}"
        self._profile_root = base / _RECORDER_PROFILE_SUBDIR
        self._profile_dir = self._profile_root / hashlib.sha256(self._scope.encode()).hexdigest()[:32]
        runtime_settings = (settings or get_settings()).model_copy(deep=True)
        runtime_settings.orchestration.browser_default_name = "chromium"
        runtime_settings.orchestration.browser_headless = False
        runtime_settings.orchestration.browser_window_width = int(self._viewport[0])
        runtime_settings.orchestration.browser_window_height = int(self._viewport[1])
        self._runtime = PythonPlaywrightCliRuntime(runtime_settings, resource_manager=resource_manager)
        self._closed_event = asyncio.Event()

    # ------------------------------------------------------------ BrowserDriver 契约

    async def open(self, url: str, *, viewport: tuple[int, int]) -> None:
        """自启受管浏览器（headed + persistent profile）→ 首页 → 导航到入口 URL。"""
        if self._closed:
            raise RuntimeError(f"playwright-managed driver already closed: {self._recording_id}")
        try:
            session = await self._runtime.open_context(self._scope, self._profile_root.parent,
                url=url, open_args=["--persistent", "--profile", str(self._profile_root)])
            self._context, self._page = session.context, session.page
            self._profile_dir = session.profile_dir
            self._context.on("close", lambda _: self._closed_event.set())
        except BaseException:
            await self.close()
            raise
        logger.info(
            "playwright-managed opened: recording_id=%s url=%s profile=%s",
            self._recording_id,
            url,
            self._profile_dir,
        )

    async def close(self) -> None:
        """关闭受管浏览器窗口（我们启动的，与 cdp-attach 只断连相反）；profile 留盘。"""
        self._closed = True
        self._channel.close()
        await self._runtime.close()
        self._closed_event.set()
        logger.info(
            "playwright-managed closed: recording_id=%s profile_preserved=%s",
            self._recording_id,
            self._profile_dir,
        )

    async def wait_closed(self) -> None:
        await self._closed_event.wait()


def playwright_managed_factory(
    config: RecordingDriverConfig,
    *,
    recording_id: str,
    settings: Settings | None = None,
    resource_manager=None,
    **_context: Any,
) -> PlaywrightManagedDriver:
    """DriverRegistry 工厂：RecorderSessionService.main 接线时经 context 传 settings。"""
    return PlaywrightManagedDriver(config=config, recording_id=recording_id, settings=settings,
                                   resource_manager=resource_manager)
