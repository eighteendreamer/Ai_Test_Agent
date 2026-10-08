"""Freeze the existing MySQL project configuration for DOM observation."""
from __future__ import annotations

import asyncio
import logging

from src.schemas.recording import RecordingCaptureSettings

logger = logging.getLogger(__name__)
CONFIG_KEY = "recording.capture_settings"


class RecordingCaptureConfigService:
    def __init__(self, *, config_store, project_service):
        self._configs, self._projects = config_store, project_service

    async def initialize(self):
        await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            RecordingCaptureSettings().model_dump(mode="json"), only_if_missing=True)
        logger.info("recording_capture_settings_initialized", extra={"config_key": CONFIG_KEY})

    async def capture(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, CONFIG_KEY)
        if record is None:
            raise RuntimeError("Recording capture settings are not initialized")
        return {"scope_key": record.scope_key, "revision": record.revision,
            "payload": RecordingCaptureSettings.model_validate(record.payload).model_dump(mode="json")}
