"""Editable module execution limits use the existing MySQL config store."""
import asyncio
import logging

from src.schemas.case_generation_tasks import GenerationTaskPolicy, GenerationTaskSettings

logger = logging.getLogger(__name__)
CONFIG_KEY = "test_case.incremental_generation_settings"


class GenerationTaskConfig:
    def __init__(self, *, configs, projects):
        self._configs, self._projects = configs, projects

    async def initialize(self):
        await asyncio.to_thread(self._configs.save, CONFIG_KEY, GenerationTaskPolicy().model_dump(mode="json"), only_if_missing=True)

    async def get(self, project_id):
        await self._projects.get(project_id)
        record = await asyncio.to_thread(self._configs.get, CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._configs.get, CONFIG_KEY)
        if record is None:
            raise ValueError("增量生成策略尚未初始化")
        return GenerationTaskSettings(scope_key=record.scope_key, revision=record.revision, payload=GenerationTaskPolicy.model_validate(record.payload))

    async def save(self, project_id, policy):
        await self._projects.require_active(project_id)
        record = await asyncio.to_thread(self._configs.save, CONFIG_KEY, policy.model_dump(mode="json"), scope_key=project_id)
        logger.info("case_generation_settings_saved", extra={"project_id": project_id, "revision": record.revision})
        return GenerationTaskSettings(scope_key=record.scope_key, revision=record.revision, payload=GenerationTaskPolicy.model_validate(record.payload))
