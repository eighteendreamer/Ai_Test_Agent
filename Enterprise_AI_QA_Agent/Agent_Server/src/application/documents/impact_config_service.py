from __future__ import annotations

import asyncio
import logging

from src.schemas.document_impact import DocumentImpactPolicy, DocumentImpactSettingsRecord
from src.schemas.document_impact_analysis import DocumentImpactAnalysisPolicy, DocumentImpactAnalysisSettings
from src.schemas.document_impact_batches import DocumentImpactBatchPolicy, DocumentImpactBatchSettings
from src.schemas.document_impact_calls import DocumentImpactInvocationPolicy, DocumentImpactInvocationSettings

CONFIG_KEY = "document.impact_settings"
ANALYSIS_CONFIG_KEY = "document.impact_analysis_settings"
BATCH_CONFIG_KEY = "document.impact_batch_settings"
INVOCATION_CONFIG_KEY = "document.impact_invocation_settings"
logger = logging.getLogger(__name__)


class DocumentImpactConfigService:
    def __init__(self, *, config_store, project_service):
        self._configs, self._projects = config_store, project_service

    async def initialize(self):
        await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            DocumentImpactPolicy().model_dump(mode="json"), only_if_missing=True)
        await asyncio.to_thread(self._configs.save, ANALYSIS_CONFIG_KEY,
            DocumentImpactAnalysisPolicy().model_dump(mode="json"), only_if_missing=True)
        await asyncio.to_thread(self._configs.save, BATCH_CONFIG_KEY,
            DocumentImpactBatchPolicy().model_dump(mode="json"), only_if_missing=True)
        await asyncio.to_thread(self._configs.save, INVOCATION_CONFIG_KEY,
            DocumentImpactInvocationPolicy().model_dump(mode="json"), only_if_missing=True)
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

    async def get_analysis_settings(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, ANALYSIS_CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, ANALYSIS_CONFIG_KEY)
        if record is None:
            raise RuntimeError("文档影响模型策略尚未初始化")
        return DocumentImpactAnalysisSettings(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactAnalysisPolicy.model_validate(record.payload))

    async def save_analysis_settings(self, project_id, payload: DocumentImpactAnalysisPolicy):
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, ANALYSIS_CONFIG_KEY,
            payload.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_impact_analysis_settings_saved", extra={"project_id": project_id,
            "revision": record.revision})
        return DocumentImpactAnalysisSettings(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactAnalysisPolicy.model_validate(record.payload))

    async def save_settings(self, project_id, payload: DocumentImpactPolicy):
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            payload.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_impact_settings_saved", extra={"project_id": project_id, "revision": record.revision})
        return DocumentImpactSettingsRecord(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactPolicy.model_validate(record.payload))

    async def get_batch_settings(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, BATCH_CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, BATCH_CONFIG_KEY)
        if record is None:
            raise RuntimeError("文档影响分批策略尚未初始化")
        return DocumentImpactBatchSettings(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactBatchPolicy.model_validate(record.payload))

    async def save_batch_settings(self, project_id, payload: DocumentImpactBatchPolicy):
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, BATCH_CONFIG_KEY,
            payload.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_impact_batch_settings_saved", extra={"project_id": project_id,
            "revision": record.revision})
        return DocumentImpactBatchSettings(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactBatchPolicy.model_validate(record.payload))

    async def get_invocation_settings(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, INVOCATION_CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, INVOCATION_CONFIG_KEY)
        if record is None:
            raise RuntimeError("文档影响调用策略尚未初始化")
        return DocumentImpactInvocationSettings(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactInvocationPolicy.model_validate(record.payload))

    async def save_invocation_settings(self, project_id, payload: DocumentImpactInvocationPolicy):
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, INVOCATION_CONFIG_KEY,
            payload.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_impact_invocation_settings_saved", extra={"project_id": project_id,
            "revision": record.revision})
        return DocumentImpactInvocationSettings(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentImpactInvocationPolicy.model_validate(record.payload))
