from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from src.schemas.business_planning import BusinessEnvironmentSelection, BusinessPlanningEnvironment
from src.schemas.case_effects import EffectEvidenceDeclaration


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
    preparation_scope_protocol: Literal["preparation-scope-v1"] | None = None
    effect_evidence_protocol: Literal["case-effect-evidence-v1"] | None = None
    effect_evidence_max_bytes: int | None = Field(default=None, ge=1, le=10000000)
    effect_coverage_protocol: Literal["case-effect-coverage-v1"] | None = None
    effect_coverage_max_bytes: int | None = Field(default=None, ge=1, le=10000000)

    @model_validator(mode="after")
    def require_action_ownership(self):
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
        return self

    @model_serializer(mode="wrap")
    def preserve_original_policy(self, handler):
        data = handler(self)
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
        return data


class CasePlanningRequest(PlanningModel):
    objective: str = Field(min_length=1, max_length=20000)
    model_key: str | None = Field(default=None, max_length=160)
    candidate_case_ids: list[str] = Field(default_factory=list, max_length=50)
    runtime_context: dict[str, Any] = Field(default_factory=dict)
    environment: BusinessEnvironmentSelection | None = None


class PlanningQuery(PlanningModel):
    kind: Literal["cases", "documents", "document", "recordings", "recording", "tools", "tool"]
    query: str = Field(default="", max_length=1000)
    source_id: str | None = Field(default=None, max_length=160)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=100000)


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
    kind: Literal["case", "document", "recording", "tool"]
    source_id: str
    version: str
    content_hash: str
    payload: dict[str, Any]


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
    decision: PlanningDecision | None = None
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

    @model_serializer(mode="wrap")
    def preserve_original_snapshot(self, handler):
        data = handler(self)
        if self.preparation_scopes is None:
            data.pop("preparation_scopes", None)
        return data
