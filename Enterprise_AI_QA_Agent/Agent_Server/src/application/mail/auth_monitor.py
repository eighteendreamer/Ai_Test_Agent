"""Background health checks for persistent Agent Mail authentication."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.application.mail.provider_registry import MailProviderRegistry
    from src.core.config import Settings
    from src.infrastructure.email_config_store import MySQLEmailConfigStore


logger = logging.getLogger(__name__)


class TencentAuthMonitor:
    """Periodically validates the globally active Tencent mailbox."""

    def __init__(
        self,
        *,
        settings: "Settings",
        email_config_store: "MySQLEmailConfigStore",
        registry: "MailProviderRegistry",
    ) -> None:
        self._settings = settings
        self._email_config_store = email_config_store
        self._registry = registry
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._latest: dict[int, dict[str, Any]] = {}

    @property
    def latest(self) -> dict[int, dict[str, Any]]:
        return {key: dict(value) for key, value in self._latest.items()}

    async def startup(self) -> None:
        self._closing = False
        interval = float(self._settings.mail.agently_auth_check_interval_seconds)
        if interval > 0 and self._task is None:
            self._task = asyncio.create_task(
                self._health_loop(interval),
                name="tencent-agent-mail-auth-monitor",
            )

    async def shutdown(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def check_once(self) -> dict[int, dict[str, Any]]:
        records = await asyncio.to_thread(self._email_config_store.list_all)
        active_records = [
            record
            for record in records
            if record.enabled and record.provider == "tencent_agently"
        ]
        adapter = self._registry.resolve("tencent_agently")
        results: dict[int, dict[str, Any]] = {}
        for record in active_records:
            if record.id is None:
                continue
            try:
                result = await asyncio.to_thread(adapter.status, record)
            except Exception as exc:  # pragma: no cover - operational guard
                result = {
                    "ok": False,
                    "provider": "tencent_agently",
                    "auth_state": "failed",
                    "error": str(exc),
                }
            results[record.id] = result
            if result.get("auth_state") == "reauth_required":
                logger.warning(
                    "Tencent Agent Mail config %s requires re-authorization: %s",
                    record.id,
                    result.get("error") or result.get("auth_status") or "unknown",
                )
            elif not result.get("ok"):
                logger.warning(
                    "Tencent Agent Mail config %s health check failed: %s",
                    record.id,
                    result.get("error") or "unknown",
                )
        self._latest = results
        return self.latest

    async def _health_loop(self, interval: float) -> None:
        while not self._closing:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - operational guard
                logger.exception("Tencent Agent Mail authentication monitor failed")
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
