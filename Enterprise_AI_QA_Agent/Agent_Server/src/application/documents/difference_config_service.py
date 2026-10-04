from __future__ import annotations

import asyncio
import logging

from src.infrastructure.application_config_store import ApplicationConfigStore
from src.schemas.document_differences import DocumentDifferenceSettings, DocumentDifferenceSettingsRecord

CONFIG_KEY = "document.difference_settings"
logger = logging.getLogger(__name__)


class DocumentDifferenceConfigService:
    def __init__(self, *, config_store: ApplicationConfigStore, project_service):
        self._configs, self._projects = config_store, project_service

    async def initialize(self):
        await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            DocumentDifferenceSettings().model_dump(mode="json"), only_if_missing=True)
        logger.info("document_difference_settings_initialized", extra={"config_key": CONFIG_KEY})

    async def get_settings(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, CONFIG_KEY)
        if record is None:
            raise RuntimeError("文档比较策略尚未初始化")
        return DocumentDifferenceSettingsRecord(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentDifferenceSettings.model_validate(record.payload))

    async def save_settings(self, project_id, payload: DocumentDifferenceSettings):
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, CONFIG_KEY, payload.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_difference_settings_saved", extra={"project_id": project_id, "revision": record.revision})
        return DocumentDifferenceSettingsRecord(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentDifferenceSettings.model_validate(record.payload))
