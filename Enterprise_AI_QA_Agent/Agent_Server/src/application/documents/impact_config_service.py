from __future__ import annotations

import asyncio
import logging

from src.schemas.document_impact import DocumentImpactPolicy, DocumentImpactSettingsRecord

CONFIG_KEY = "document.impact_settings"
logger = logging.getLogger(__name__)


class DocumentImpactConfigService:
    def __init__(self, *, config_store, project_service):
        self._configs, self._projects = config_store, project_service

    async def initialize(self):
        await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            DocumentImpactPolicy().model_dump(mode="json"), only_if_missing=True)
        logger.info("document_impact_settings_initialized", extra={"config_key": CONFIG_KEY})

    async def get_settings(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, CONFIG_KEY)
        if record is None:
            raise RuntimeError("文档影响策略尚未初始化")
        return DocumentImpactSettingsRecord(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactPolicy.model_validate(record.payload))

    async def save_settings(self, project_id, payload: DocumentImpactPolicy):
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            payload.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_impact_settings_saved", extra={"project_id": project_id, "revision": record.revision})
        return DocumentImpactSettingsRecord(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactPolicy.model_validate(record.payload))
