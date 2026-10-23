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


def legacy_default_generation_template_v1() -> GenerationTemplatePayload:
    """Previous shipped global default; retained only for a guarded MySQL upgrade."""
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


def default_generation_template() -> GenerationTemplatePayload:
    return GenerationTemplatePayload(
        name="默认测试用例模板",
        writing_guidance=("依据项目文档逐条编写可评审用例，覆盖正常、异常与边界条件。每条用例明确目标、前置条件、"
                          "操作、预期结果和可观察验证点。每个步骤只写一个角色、一个条件和一项主要操作；互斥条件或不同角色拆为"
                          "不同步骤或用例，每步预期只对应该步操作。不要把登录/会话建立与目标业务操作合并；已有登录态写入前置条件，"
                          "若测试登录行为，则单独成步并写明预期。模块内每条明确规则都必须映射到至少一个步骤预期；同时覆盖要求中的"
                          "成功行为和拒绝/异常行为，不得只生成其中一类。保留负向条件。缺少业务依据时给出警告，禁止编造接口或元素。"
                          "用例内容放入 content_json，JSON 结构可按项目调整；不要预先编译为测试模式的工具参数。"
                          "仅当用例本身明确测试文件或报告的创建/验证行为时，才在 required_tool_keys 中列出"
                          "file-artifact-manager 或 report-writer；否则保持空数组。不得仅为保存正式回执或重复浏览器证据而声明。"),
        execution_notes=("阅读完整用例内容，按目标、操作与预期执行并保存证据；步骤所需前置由项目依据确认，保留负向条件。"
                         "托管用例中 required_tool_keys 仅用于声明被测行为确实需要的文件/报告工具，普通用例保持空数组。"),
        example_json={
            "objective": "验证已登录用户创建订单的成功规则和空标题拒绝规则",
            "preconditions": ["测试用户A已登录", "项目接口文档确认订单标题字段"],
            "steps": [
                {"order": 1, "action": "以已登录用户A身份提交包含非空标题的订单", "expected": "订单创建成功，返回系统生成的订单编号，且订单归属用户A"},
                {"order": 2, "action": "以已登录用户A身份提交标题为空的订单", "expected": "请求被拒绝，不创建订单，也不返回新订单编号"},
                {"order": 3, "action": "查询用户A的订单列表，核对本用例提交的两次请求", "expected": "仅存在步骤1成功创建的订单，不存在步骤2对应的订单"},
            ],
            "negative_conditions": ["不得将不同角色、互斥条件或不同操作写在同一个步骤动作中"],
            "checkpoints": [{"description": "核对成功创建、空标题拒绝及用户归属", "evidence": "实际响应和用户A的订单列表"}],
            "test_data": {"valid_title": "使用项目认可的非空测试标题", "empty_title": ""},
            "cleanup": ["若项目提供安全清理方式，删除步骤1创建的测试订单"],
            "required_tool_keys": [],
        },
    )


def legacy_default_generation_template_v2() -> GenerationTemplatePayload:
    """Previous shipped default; used only to upgrade an untouched MySQL default."""
    return GenerationTemplatePayload(
        name="默认测试用例模板",
        writing_guidance=("依据项目文档逐条编写可评审用例，覆盖正常、异常与边界条件。每条用例明确目标、前置条件、"
                          "操作、预期结果和可观察验证点。每个步骤只写一个角色、一个条件和一项主要操作；互斥条件或不同角色拆为"
                          "不同步骤或用例，每步预期只对应该步操作。不要把登录/会话建立与目标业务操作合并；已有登录态写入前置条件，"
                          "若测试登录行为，则单独成步并写明预期。模块内每条明确规则都必须映射到至少一个步骤预期；同时覆盖要求中的"
                          "成功行为和拒绝/异常行为，不得只生成其中一类。保留负向条件。缺少业务依据时给出警告，禁止编造接口或元素。"
                          "用例内容放入 content_json，JSON 结构可按项目调整；不要预先编译为测试模式的工具参数。"),
        execution_notes="阅读完整用例内容，按目标、操作与预期执行并保存证据；步骤所需前置由项目依据确认，保留负向条件。",
        example_json={
            "objective": "验证已登录用户创建订单的成功规则和空标题拒绝规则",
            "preconditions": ["测试用户A已登录", "项目接口文档确认订单标题字段"],
            "steps": [
                {"order": 1, "action": "以已登录用户A身份提交包含非空标题的订单", "expected": "订单创建成功，返回系统生成的订单编号，且订单归属用户A"},
                {"order": 2, "action": "以已登录用户A身份提交标题为空的订单", "expected": "请求被拒绝，不创建订单，也不返回新订单编号"},
                {"order": 3, "action": "查询用户A的订单列表，核对本用例提交的两次请求", "expected": "仅存在步骤1成功创建的订单，不存在步骤2对应的订单"},
            ],
            "negative_conditions": ["不得将不同角色、互斥条件或不同操作写在同一个步骤动作中"],
            "checkpoints": [{"description": "核对成功创建、空标题拒绝及用户归属", "evidence": "实际响应和用户A的订单列表"}],
            "test_data": {"valid_title": "使用项目认可的非空测试标题", "empty_title": ""},
            "cleanup": ["若项目提供安全清理方式，删除步骤1创建的测试订单"],
        },
    )


class TestCaseTemplateService:
    def __init__(self, *, store: ApplicationConfigStore, project_service: ProjectService) -> None:
        self._store = store
        self._projects = project_service

    async def initialize(self) -> None:
        current_template = default_generation_template().model_dump(mode="json")
        existing = await asyncio.to_thread(self._store.get, TEMPLATE_CONFIG_KEY)
        if existing is None:
            await asyncio.to_thread(self._store.save, TEMPLATE_CONFIG_KEY, current_template, only_if_missing=True)
            return
        legacy_templates = {
            json.dumps(legacy_default_generation_template_v1().model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            json.dumps(legacy_default_generation_template_v2().model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        }
        existing_payload = json.dumps(existing.payload, ensure_ascii=False, sort_keys=True)
        if existing.scope_key == "global" and existing_payload in legacy_templates:
            migrated = await asyncio.to_thread(self._store.save_if_revision, TEMPLATE_CONFIG_KEY,
                current_template, expected_revision=existing.revision, scope_key="global")
            if migrated is not None:
                logger.info("test_case_default_template_migrated", extra={"revision": migrated.revision})

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
