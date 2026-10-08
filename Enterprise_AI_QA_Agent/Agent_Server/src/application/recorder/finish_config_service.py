from __future__ import annotations

import asyncio
import logging

from src.schemas.recording import RecordingFinishSettings

logger = logging.getLogger(__name__)
CONFIG_KEY = "recording.finish_settings"


class RecordingFinishConfigService:
    """Use the existing MySQL configuration store; freeze at session creation."""

    def __init__(self, *, config_store, project_service):
        self._configs, self._projects = config_store, project_service

    async def initialize(self):
        await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            RecordingFinishSettings().model_dump(mode="json"), only_if_missing=True)
        logger.info("recording_finish_settings_initialized", extra={"config_key": CONFIG_KEY})

    async def capture(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, CONFIG_KEY)
        if record is None:
            raise RuntimeError("Recording finish settings are not initialized")
        return {"scope_key": record.scope_key, "revision": record.revision,
            "payload": RecordingFinishSettings.model_validate(record.payload).model_dump(mode="json")}
