from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.application.documents.parser_factory import DocumentParserFactory
from src.infrastructure.application_config_store import ApplicationConfigStore
from src.schemas.document_parsing import DocumentParsingSettings, DocumentParsingSettingsRecord, DocumentParseOptions

CONFIG_KEY = "document.parsing_settings"
logger = logging.getLogger(__name__)


class DocumentParserConfigService:
    def __init__(self, *, config_store: ApplicationConfigStore, project_service, factory=None):
        self._configs, self._projects = config_store, project_service
        self._factory = factory or DocumentParserFactory()

    async def initialize(self):
        await asyncio.to_thread(self._configs.save, CONFIG_KEY,
            DocumentParsingSettings().model_dump(mode="json"), only_if_missing=True)
        logger.info("document_parser_settings_initialized", extra={"config_key": CONFIG_KEY})

    async def get_settings(self, project_id: str) -> DocumentParsingSettingsRecord:
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, CONFIG_KEY)
        if record is None:
            raise RuntimeError("文档解析策略尚未初始化")
        payload = DocumentParsingSettings.model_validate(record.payload)
        self._validate_parsers(payload)
        return DocumentParsingSettingsRecord(scope_key=record.scope_key, revision=record.revision, payload=payload)

    async def save_settings(self, project_id: str, payload: DocumentParsingSettings) -> DocumentParsingSettingsRecord:
        await self._projects.require_active(project_id)
        self._validate_parsers(payload)
        record = await asyncio.to_thread(self._configs.save, CONFIG_KEY, payload.model_dump(mode="json"), scope_key=project_id)
        logger.info("document_parser_settings_saved", extra={"project_id": project_id, "revision": record.revision})
        return DocumentParsingSettingsRecord(scope_key=record.scope_key, revision=record.revision,
            payload=DocumentParsingSettings.model_validate(record.payload))

    async def prepare(self, project_id: str, filename: str, options: DocumentParseOptions | None = None):
        await self._projects.require_active(project_id)
        record = await self.get_settings(project_id)
        selected = (options or record.payload.options).model_copy(deep=True)
        extension = Path(filename).suffix.lower()
        if selected.parser_id == "auto":
            parser_id = record.payload.parser_by_extension.get(extension)
            if parser_id is None:
                raise ValueError("此文档格式没有配置解析器")
            selected.parser_id = parser_id
        capability = next((value for value in self._factory.capabilities() if value.parser_id == selected.parser_id), None)
        if capability is None or not capability.available:
            raise ValueError("文档解析器未注册或不可用")
        if extension not in capability.formats:
            raise ValueError("解析器不支持此文档格式")
        # This complete record is a future PG task input, not a live settings reference.
        logger.info("document_parser_input_prepared", extra={"project_id": project_id,
            "config_scope": record.scope_key, "config_revision": record.revision,
            "parser_id": capability.parser_id, "extension": extension})
        return record, selected, capability

    def _validate_parsers(self, payload):
        capabilities = {item.parser_id: item for item in self._factory.capabilities()}
        for extension, parser_id in payload.parser_by_extension.items():
            capability = capabilities.get(parser_id)
            if capability is None or not capability.available:
                raise ValueError("文档解析器未注册或不可用")
            if extension not in capability.formats:
                raise ValueError("解析器配置与文档格式不一致")
        parser_id = payload.options.parser_id
        if parser_id != "auto" and (parser_id not in capabilities or not capabilities[parser_id].available):
            raise ValueError("文档解析器未注册或不可用")
