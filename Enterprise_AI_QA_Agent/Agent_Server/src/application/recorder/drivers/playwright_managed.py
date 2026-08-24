"""playwright-managed 驱动：服务端自启受管浏览器（方案 5.2③，P1-2）。

场景：非桌面端部署（纯浏览器访问工作台）时，embedded 不可用、用户本地
浏览器无 CDP 端口——由后端启动一个有头 Chromium（headed + persistent
profile），用户在弹出的受管窗口里操作，登录态持久化到产物目录
（下次录制免重登）。

与 cdp-attach 的差异（其余契约共用 playwright_common）：
- open：``launch_persistent_context(profile_dir, headless=False)`` 自启；
- close：受管浏览器是我们启动的 → ``context.close()`` 关窗（与 cdp-attach
  只断连相反）；磁盘 profile 保留。

启动参数沿用 PythonPlaywrightCliRuntime._ensure_session 惯例：headed
强制开启（受管窗口必须可见）、viewport 取驱动配置、persistent profile
落 artifact_root 下。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.core.config import Settings
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
    ) -> None:
        super().__init__(recording_id=recording_id)
        self._viewport = tuple(config.viewport)
        base = (
            Path(__file__).resolve().parents[2] / str(getattr(settings, "artifact_root_dir", "data/artifacts"))
            if settings is not None
            else Path(__file__).resolve().parents[2] / "data" / "artifacts"
        )
        self._profile_dir = base / _RECORDER_PROFILE_SUBDIR
        self._playwright: Any | None = None

    # ------------------------------------------------------------ BrowserDriver 契约

    async def _start_playwright(self) -> Any:
        """启动 playwright driver（测试注入口：替身覆盖此方法，open 真实执行）。"""
        from playwright.async_api import async_playwright  # 延迟导入：未装 playwright 的环境可注册不实例化

        return await async_playwright().start()

    async def open(self, url: str, *, viewport: tuple[int, int]) -> None:
        """自启受管浏览器（headed + persistent profile）→ 首页 → 导航到入口 URL。"""
        if self._closed:
            raise RuntimeError(f"playwright-managed driver already closed: {self._recording_id}")
        self._playwright = await self._start_playwright()
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(self._profile_dir),
            headless=False,  # 受管窗口必须可见，用户在其中真实操作
            viewport={"width": int(self._viewport[0]), "height": int(self._viewport[1])},
        )
        pages = list(getattr(self._context, "pages", None) or [])
        self._page = pages[0] if pages else await self._context.new_page()
        await self._page.goto(url, wait_until="domcontentloaded")
        logger.info(
            "playwright-managed opened: recording_id=%s url=%s profile=%s",
            self._recording_id,
            url,
            self._profile_dir,
        )

    async def close(self) -> None:
        """关闭受管浏览器窗口（我们启动的，与 cdp-attach 只断连相反）；profile 留盘。"""
        if self._closed:
            return
        self._closed = True
        self._channel.close()
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001 浏览器已被用户手关等：关闭幂等
                logger.debug(
                    "playwright-managed context close ignored error: recording_id=%s",
                    self._recording_id,
                )
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001 停止幂等
                logger.debug(
                    "playwright-managed pw stop ignored error: recording_id=%s",
                    self._recording_id,
                )
        logger.info(
            "playwright-managed closed: recording_id=%s profile_preserved=%s",
            self._recording_id,
            self._profile_dir,
        )


def playwright_managed_factory(
    config: RecordingDriverConfig,
    *,
    recording_id: str,
    settings: Settings | None = None,
    **_context: Any,
) -> PlaywrightManagedDriver:
    """DriverRegistry 工厂：RecorderSessionService.main 接线时经 context 传 settings。"""
    return PlaywrightManagedDriver(config=config, recording_id=recording_id, settings=settings)
