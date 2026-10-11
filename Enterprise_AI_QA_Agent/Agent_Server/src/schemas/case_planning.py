from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from src.schemas.business_planning import BusinessEnvironmentSelection, BusinessPlanningEnvironment
from src.schemas.case_effects import EffectEvidenceDeclaration, EffectRelationDeclaration
from src.schemas.case_effect_sources import SourceEffectRelationDeclaration
from src.schemas.model_config import ModelTransportOptions


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CasePlanningPolicy(PlanningModel):
    max_rounds: int = Field(default=5, ge=1, le=20)
    max_candidates: int = Field(default=20, ge=1, le=50)
    max_items: int = Field(default=50, ge=1, le=200)
    max_context_chars: int = Field(default=180000, ge=10000, le=1000000)
    timeout_seconds: float = Field(default=180, gt=0, le=600)
    action_binding_protocol: Literal["case-action-binding-v1"] | None = None
    action_admission_protocol: Literal["case-action-admission-v1"] | None = None
    operation_protocol: Literal["case-operation-v1"] | None = None
    preparation_scope_protocol: Literal["preparation-scope-v1", "preparation-scope-v2"] | None = None
    effect_evidence_protocol: Literal["case-effect-evidence-v1", "case-effect-evidence-v2"] | None = None
    effect_evidence_max_bytes: int | None = Field(default=None, ge=1, le=10000000)
    effect_coverage_protocol: Literal["case-effect-coverage-v1", "case-effect-coverage-v2"] | None = None
    effect_coverage_max_bytes: int | None = Field(default=None, ge=1, le=10000000)
    planning_stage_protocol: Literal["case-planning-staged-evidence-v1", "case-planning-staged-evidence-v2", "case-planning-staged-evidence-v3"] | None = None
    effect_expected_source_protocol: Literal["case-effect-document-expected-v1"] | None = None
    operation_prefetch_protocol: Literal["case-planning-declared-operation-prefetch-v1", "case-planning-item-operation-prefetch-v2"] | None = None
    source_identity_protocol: Literal["case-planning-source-full-hash-v1"] | None = None
    linked_source_protocol: Literal["case-planning-linked-documents-v1", "case-planning-linked-sources-v2"] | None = None
    evidence_reference_protocol: Literal["case-planning-evidence-references-v1"] | None = None
    scene_query_protocol: Literal["case-planning-scene-query-v1"] | None = None
    scene_receipt_protocol: Literal["case-scene-receipt-v1"] | None = None
    consumption_protocol: Literal["case-consumption-phase-v1"] | None = None
    receipt_correction_protocol: Literal["case-receipt-correction-v1"] | None = None
    receipt_correction_transport_options: ModelTransportOptions | None = None

    @model_validator(mode="after")
    def require_action_ownership(self):
        if self.planning_stage_protocol == "case-planning-staged-evidence-v3" and not self.evidence_reference_protocol:
            raise ValueError("分任务证据规划需要固定短引用及文档预期协议")
        if self.scene_receipt_protocol and not (self.scene_query_protocol or self.linked_source_protocol == "case-planning-linked-sources-v2"):
            raise ValueError("场景预期回执需要固定场景查询或明确关联来源读取")
        if self.scene_query_protocol and not self.source_identity_protocol:
            raise ValueError("场景按需查询需要固定完整来源身份")
        if self.receipt_correction_transport_options is not None and self.receipt_correction_protocol is None:
            raise ValueError("回执调用设置需要启用只读纠正协议")
        if self.consumption_protocol and self.effect_coverage_protocol != "case-effect-coverage-v2":
            raise ValueError("消费阶段结束需要固定多效果关系和原动作领取")
        if self.evidence_reference_protocol and not self.source_identity_protocol:
            raise ValueError("证据短引用需要固定完整来源身份及文档预期协议")
        if self.linked_source_protocol and not self.source_identity_protocol:
            raise ValueError("关联来源预读需要固定完整来源身份")
        if self.operation_prefetch_protocol == "case-planning-item-operation-prefetch-v2" and not self.source_identity_protocol:
            raise ValueError("执行项操作选择需要固定完整来源身份")
        if self.source_identity_protocol and not self.effect_expected_source_protocol:
            raise ValueError("完整摘要来源身份需要固定文档预期及分阶段协议")
        if self.operation_prefetch_protocol and not self.effect_expected_source_protocol:
            raise ValueError("明确操作预读需要固定文档预期及分阶段协议")
        if self.effect_expected_source_protocol and self.planning_stage_protocol not in {"case-planning-staged-evidence-v2", "case-planning-staged-evidence-v3"}:
            raise ValueError("文档预期引用需要固定多效果分阶段协议")
        protocols = (self.preparation_scope_protocol, self.effect_evidence_protocol,
            self.effect_coverage_protocol, self.planning_stage_protocol)
        if any(value and value.endswith("-v2") for value in protocols) and (protocols[:3] != (
            "preparation-scope-v2", "case-effect-evidence-v2", "case-effect-coverage-v2") or protocols[3] not in {
                "case-planning-staged-evidence-v2", "case-planning-staged-evidence-v3"}):
            raise ValueError("正式多效果关系需要一致的v2范围、证据、覆盖和分阶段协议")
        if self.action_admission_protocol and not self.action_binding_protocol:
            raise ValueError("动作确认检查需要固定执行项关联")
        if self.operation_protocol and not self.action_admission_protocol:
            raise ValueError("逻辑操作恢复需要固定执行项关联和动作领取")
        if self.preparation_scope_protocol and not self.operation_protocol:
            raise ValueError("准备消费范围需要固定逻辑操作策略")
        if self.effect_evidence_protocol and not self.preparation_scope_protocol:
            raise ValueError("对象证据需要固定准备消费范围策略")
        if self.effect_evidence_protocol and self.effect_evidence_max_bytes is None:
            self.effect_evidence_max_bytes = 512000
        if not self.effect_evidence_protocol and self.effect_evidence_max_bytes is not None:
            raise ValueError("对象证据大小配置需要固定对象证据策略")
        if self.effect_coverage_protocol and not self.effect_evidence_protocol:
            raise ValueError("对象覆盖需要固定对象证据策略")
        if self.effect_coverage_protocol and self.effect_coverage_max_bytes is None:
            self.effect_coverage_max_bytes = 1024000
        if not self.effect_coverage_protocol and self.effect_coverage_max_bytes is not None:
            raise ValueError("对象覆盖大小配置需要固定覆盖策略")
        if self.planning_stage_protocol and not self.effect_coverage_protocol:
            raise ValueError("分阶段规划需要固定对象覆盖策略")
        return self

    @model_serializer(mode="wrap")
    def preserve_original_policy(self, handler):
        data = handler(self)
        if self.scene_receipt_protocol is None:
            data.pop("scene_receipt_protocol", None)
        if self.scene_query_protocol is None:
            data.pop("scene_query_protocol", None)
        if self.receipt_correction_protocol is None:
            data.pop("receipt_correction_protocol", None)
        if self.receipt_correction_transport_options is None:
            data.pop("receipt_correction_transport_options", None)
        if self.consumption_protocol is None:
            data.pop("consumption_protocol", None)
        if self.evidence_reference_protocol is None:
            data.pop("evidence_reference_protocol", None)
        if self.linked_source_protocol is None:
            data.pop("linked_source_protocol", None)
        if self.effect_expected_source_protocol is None:
            data.pop("effect_expected_source_protocol", None)
        if self.operation_prefetch_protocol is None:
            data.pop("operation_prefetch_protocol", None)
        if self.source_identity_protocol is None:
            data.pop("source_identity_protocol", None)
        if self.action_binding_protocol is None:
            data.pop("action_binding_protocol", None)
        if self.action_admission_protocol is None:
            data.pop("action_admission_protocol", None)
        if self.operation_protocol is None:
            data.pop("operation_protocol", None)
        if self.preparation_scope_protocol is None:
            data.pop("preparation_scope_protocol", None)
        if self.effect_evidence_protocol is None:
            data.pop("effect_evidence_protocol", None)
        if self.effect_evidence_max_bytes is None:
            data.pop("effect_evidence_max_bytes", None)
        if self.effect_coverage_protocol is None:
            data.pop("effect_coverage_protocol", None)
        if self.effect_coverage_max_bytes is None:
            data.pop("effect_coverage_max_bytes", None)
        if self.planning_stage_protocol is None:
            data.pop("planning_stage_protocol", None)
        return data


class RequiredPlanningSource(PlanningModel):
    kind: Literal["document", "recording"]
    source_id: str = Field(min_length=1, max_length=160)


class CasePlanningRequest(PlanningModel):
    objective: str = Field(min_length=1, max_length=20000)
    model_key: str | None = Field(default=None, max_length=160)
    candidate_case_ids: list[str] = Field(default_factory=list, max_length=50)
    runtime_context: dict[str, Any] = Field(default_factory=dict)
    environment: BusinessEnvironmentSelection | None = None
    required_sources: list[RequiredPlanningSource] | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_required_sources(self):
        if self.required_sources and len({(source.kind, source.source_id) for source in self.required_sources}) != len(self.required_sources):
            raise ValueError("明确要求的规划来源不能重复")
        return self

    @model_serializer(mode="wrap")
    def preserve_original_request(self, handler):
        data = handler(self)
        if self.required_sources is None:
            data.pop("required_sources", None)
        return data


class PlanningQuery(PlanningModel):
    kind: Literal["cases", "documents", "document", "recordings", "recording", "tools", "tool"]
    query: str = Field(default="", max_length=1000)
    source_id: str | None = Field(default=None, max_length=160)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=100000)
    operation: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def operation_requires_tool(self):
        if self.operation is not None and (self.kind != "tool" or not self.source_id):
            raise ValueError("操作契约读取需要指定tool及source_id")
        return self

    @model_serializer(mode="wrap")
    def preserve_original_query(self, handler):
        data = handler(self)
        if self.operation is None:
            data.pop("operation", None)
        return data


class PlanInputBinding(PlanningModel):
    source_item: str | None = None
    input_key: str | None = None
    output_path: str = Field(default="", max_length=1000)
    # Literal JSON keys / array indexes; [] means the complete source value.
    # The legacy dotted field is retained for existing frozen plans.
    output_path_tokens: list[Annotated[str, Field(max_length=256)]] | None = Field(default=None, max_length=32)
    required: bool = True

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.source_item) == bool(self.input_key):
            raise ValueError("数据绑定需要且只能指定一个来源")
        if self.output_path_tokens is not None and self.output_path:
            raise ValueError("数据路径不能同时指定字面量数组与旧点分路径")
        return self

    @property
    def path_tokens(self) -> list[str]:
        if self.output_path_tokens is not None:
            return list(self.output_path_tokens)
        return self.output_path.split(".") if self.output_path else []


class CasePlanItemBase(PlanningModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    reason: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=50)
    depends_on: list[str] = Field(default_factory=list, max_length=200)
    input_bindings: dict[str, PlanInputBinding] = Field(default_factory=dict)
    effect_evidence: EffectEvidenceDeclaration | None = None

    @model_serializer(mode="wrap")
    def preserve_original_item(self, handler):
        data = handler(self)
        if self.effect_evidence is None:
            data.pop("effect_evidence", None)
        return data

class FormalCasePlanItem(CasePlanItemBase):
    kind: Literal["target", "dependency"]
    case_id: str = Field(min_length=1, max_length=160)
    case_version_id: str = Field(min_length=1, max_length=160)


class PreparationPlanItem(CasePlanItemBase):
    kind: Literal["prepare"]
    case_id: None = None
    case_version_id: None = None
    instructions: dict[str, Any] = Field(min_length=1)
    cleanup_sources: list[Annotated[str, Field(min_length=1, max_length=80)]] | None = Field(default=None, min_length=1, max_length=200)

    @model_serializer(mode="wrap")
    def preserve_original_preparation(self, handler):
        data = handler(self)
        if self.cleanup_sources is None:
            data.pop("cleanup_sources", None)
        if self.effect_evidence is None:
            data.pop("effect_evidence", None)
        return data


CasePlanItem = Annotated[FormalCasePlanItem | PreparationPlanItem, Field(discriminator="kind")]


class PlanningGap(PlanningModel):
    item_key: str | None = None
    condition: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=4000)


class PlanningDecision(PlanningModel):
    action: Literal["query", "finish"]
    summary: str = Field(min_length=1, max_length=4000)
    queries: list[PlanningQuery] = Field(default_factory=list, max_length=8)
    items: list[CasePlanItem] = Field(default_factory=list, max_length=200)
    unresolved: list[PlanningGap] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def valid_action(self):
        if self.action == "query" and not self.queries:
            raise ValueError("查询回合需要指定项目资料查询")
        if self.action == "finish" and self.queries:
            raise ValueError("最终计划不能携带未完成查询")
        return self


class PlanningEvidence(PlanningModel):
    id: str
    kind: Literal["case", "document", "recording", "scene", "tool"]
    source_id: str
    version: str
    content_hash: str
    payload: dict[str, Any]
    payload_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_serializer(mode="wrap")
    def preserve_original_evidence(self, handler):
        data = handler(self)
        if self.payload_hash is None:
            data.pop("payload_hash", None)
        return data


class FormalCasePlanItemV2(FormalCasePlanItem):
    effect_schema: Literal[2] = 2
    effect_evidence: None = None
    cleanup_sources: list[Annotated[str, Field(min_length=1, max_length=80)]] | None = Field(default=None, min_length=1, max_length=200)
    effect_declarations: list[EffectRelationDeclaration] | None = Field(default=None, min_length=1, max_length=200)

    @model_serializer(mode="wrap")
    def preserve_relation_item(self, handler):
        data = handler(self)
        for name in ("effect_evidence", "cleanup_sources", "effect_declarations"):
            if getattr(self, name) is None:
                data.pop(name, None)
        return data


class PreparationPlanItemV2(PreparationPlanItem):
    effect_schema: Literal[2] = 2
    effect_evidence: None = None
    effect_declarations: list[EffectRelationDeclaration] | None = Field(default=None, min_length=1, max_length=200)

    @model_serializer(mode="wrap")
    def preserve_relation_item(self, handler):
        data = handler(self)
        for name in ("effect_evidence", "cleanup_sources", "effect_declarations"):
            if getattr(self, name) is None:
                data.pop(name, None)
        return data


CasePlanItemV2 = Annotated[FormalCasePlanItemV2 | PreparationPlanItemV2, Field(discriminator="kind")]


class PlanningDecisionV2(PlanningDecision):
    items: list[CasePlanItemV2] = Field(default_factory=list, max_length=200)


class FormalCasePlanItemV3(FormalCasePlanItemV2):
    effect_schema: Literal[3] = 3
    effect_declarations: list[SourceEffectRelationDeclaration] | None = Field(default=None, min_length=1, max_length=200)


class PreparationPlanItemV3(PreparationPlanItemV2):
    effect_schema: Literal[3] = 3
    effect_declarations: list[SourceEffectRelationDeclaration] | None = Field(default=None, min_length=1, max_length=200)


CasePlanItemV3 = Annotated[FormalCasePlanItemV3 | PreparationPlanItemV3, Field(discriminator="kind")]


class PlanningDecisionV3(PlanningDecisionV2):
    items: list[CasePlanItemV3] = Field(default_factory=list, max_length=200)


class CasePlanningConfig(PlanningModel):
    scope_key: str
    revision: int
    policy: CasePlanningPolicy


class RegressionPlanningInput(PlanningModel):
    parent_run_id: str = Field(min_length=1, max_length=160)
    source_plan_id: str = Field(min_length=1, max_length=160)
    source_results_by_case: dict[str, str] = Field(min_length=1, max_length=200)
    required_case_versions: dict[str, str] = Field(min_length=1, max_length=200)
    version_overrides: dict[str, str] = Field(default_factory=dict, max_length=200)
    mode_key: str = Field(min_length=1, max_length=80)
    session_id: str | None = None
    submission: dict[str, Any]
    context: dict[str, Any]

    @model_validator(mode="after")
    def valid_scope(self):
        if (set(self.source_results_by_case) != set(self.required_case_versions)
            or len(set(self.source_results_by_case.values())) != len(self.source_results_by_case)
            or not set(self.version_overrides).issubset(self.required_case_versions)
            or any(self.required_case_versions[key] != version for key, version in self.version_overrides.items())
            or self.context.get("parent_run_id") != self.parent_run_id
            or self.context.get("source_plan_id") != self.source_plan_id):
            raise ValueError("回归固定目标、失败来源或版本引用不一致")
        return self


class CasePlanRecord(PlanningModel):
    id: str
    project_id: str
    request: CasePlanningRequest
    status: Literal["planned", "no_match", "blocked", "retrieval_failed", "planning_failed"]
    summary: str
    decision: PlanningDecision | PlanningDecisionV2 | PlanningDecisionV3 | None = None
    evidence: list[PlanningEvidence] = Field(default_factory=list)
    config_snapshot: CasePlanningConfig
    model_key: str | None = None
    prompt_version: str = "case-planning-v2"
    rounds: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime
    regression: RegressionPlanningInput | None = None
    environment: BusinessPlanningEnvironment | None = None


def planning_request_json(request: CasePlanningRequest) -> dict:
    data = request.model_dump(mode="json")
    if request.environment is None:
        data.pop("environment", None)
    return data


def case_plan_json(plan: CasePlanRecord) -> dict:
    data = plan.model_dump(mode="json")
    data["request"] = planning_request_json(plan.request)
    if plan.environment is None:
        data.pop("environment", None)
    return data


class CasePlanRunRequest(PlanningModel):
    creation_key: str = Field(min_length=1, max_length=160)
    mode_key: str = Field(min_length=1, max_length=80)
    session_id: str | None = Field(default=None, min_length=1, max_length=160)


class RegressionPlanSource(PlanningModel):
    parent_run_id: str = Field(min_length=1, max_length=160)
    source_plan_id: str = Field(min_length=1, max_length=160)
    source_results: dict[str, str] = Field(min_length=1, max_length=200)
    version_overrides: dict[str, str] = Field(default_factory=dict, max_length=200)


class PreparationScopeRecord(PlanningModel):
    source_key: str
    source_owner_key: str
    consumer_keys: list[str]
    cleanup_keys: list[str]


class CasePlanExecutionSnapshot(PlanningModel):
    plan: CasePlanRecord
    run_item_ids: dict[str, str]
    owners: dict[str, str]
    preparation_phases: dict[str, Literal["before", "after"]]
    regression: RegressionPlanSource | None = None
    preparation_scopes: dict[str, PreparationScopeRecord] | None = None
    dependency_mapping_version: Literal["case-cleanup-waits-v1"] | None = None

    @model_serializer(mode="wrap")
    def preserve_original_snapshot(self, handler):
        data = handler(self)
        if self.preparation_scopes is None:
            data.pop("preparation_scopes", None)
        if self.dependency_mapping_version is None:
            data.pop("dependency_mapping_version", None)
        return data
