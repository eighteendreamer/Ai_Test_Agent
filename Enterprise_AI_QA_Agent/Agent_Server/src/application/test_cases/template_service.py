from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from src.application.projects.project_service import ProjectService
from src.infrastructure.application_config_store import ApplicationConfigStore
from src.schemas.case_management import GenerationTemplatePayload, GenerationTemplateRecord

logger = logging.getLogger(__name__)
TEMPLATE_CONFIG_KEY = "test_case.generation_template"


def default_generation_template() -> GenerationTemplatePayload:
    return GenerationTemplatePayload(
        name="默认测试用例模板",
        writing_guidance=("依据项目文档逐条编写可评审用例，覆盖正常、异常与边界条件。每条用例明确目标、前置条件、"
                          "操作、预期结果和可观察验证点。负向条件必须保留。缺少业务依据时给出警告，禁止编造接口或元素。"
                          "用例内容放入 content_json，JSON 结构可按项目调整；不要预先编译为测试模式的工具参数。"),
        execution_notes="阅读完整用例内容，按目标、操作与预期执行并保存证据；步骤所需前置由项目依据确认，保留负向条件。",
        example_json={
            "objective": "验证示例业务行为",
            "preconditions": ["根据项目文档填写实际条件"],
            "steps": [{"order": 1, "action": "执行有来源依据的操作", "expected": "可观察结果"}],
            "checkpoints": [{"description": "核对实际结果与需求一致", "evidence": "实际响应或页面证据"}],
            "test_data": {}, "cleanup": [],
        },
    )


class TestCaseTemplateService:
    def __init__(self, *, store: ApplicationConfigStore, project_service: ProjectService) -> None:
        self._store = store
        self._projects = project_service

    async def initialize(self) -> None:
        await asyncio.to_thread(self._store.save, TEMPLATE_CONFIG_KEY,
                                default_generation_template().model_dump(mode="json"), only_if_missing=True)

    async def get(self, project_id: str, *, use_default: bool = False) -> GenerationTemplateRecord:
        await self._projects.get(project_id)
        record = None if use_default else await asyncio.to_thread(self._store.get, TEMPLATE_CONFIG_KEY, scope_key=project_id)
        if record is None:
            record = await asyncio.to_thread(self._store.get, TEMPLATE_CONFIG_KEY)
        if record is None:
            raise RuntimeError("Default test case generation template has not been initialized")
        payload = GenerationTemplatePayload.model_validate(record.payload)
        canonical = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return GenerationTemplateRecord(project_id=project_id, scope_key=record.scope_key, revision=record.revision,
                                        is_default=record.scope_key == "global", content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                                        payload=payload)

    async def save(self, project_id: str, payload: GenerationTemplatePayload) -> GenerationTemplateRecord:
        await self._projects.require_active(project_id)
        await asyncio.to_thread(self._store.save, TEMPLATE_CONFIG_KEY, payload.model_dump(mode="json"), scope_key=project_id)
        record = await self.get(project_id)
        logger.info("test_case_template_saved", extra={"project_id": project_id, "revision": record.revision})
        return record
