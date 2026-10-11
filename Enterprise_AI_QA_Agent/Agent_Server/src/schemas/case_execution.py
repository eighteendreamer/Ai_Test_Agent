from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_serializer, model_validator

from src.schemas.case_planning import (
    CasePlanItem, PlanningEvidence, PlanningGap, PlanningModel, PlanInputBinding,
    PreparationScopeRecord, CasePlanItemV2, CasePlanItemV3,
)
from src.schemas.business_planning import BusinessPlanningEnvironment
from src.schemas.business_execution import BusinessExecutionAdmission
from src.schemas.model_config import ModelTransportOptions


class BoundCaseInput(PlanningModel):
    binding: PlanInputBinding
    resolved: bool
    value: Any = None
    run_item_id: str | None = None
    result_id: str | None = None
    actual_path: list[str] | None = None


class CaseExecutionStep(PlanningModel):
    item: CasePlanItem | CasePlanItemV2 | CasePlanItemV3
    phase: Literal["before", "case", "after"]
    inputs: dict[str, BoundCaseInput] = Field(default_factory=dict)
    content_json: dict[str, Any] | None = None
    execution_notes: str = ""
    source_refs: list[dict[str, Any]] = Field(default_factory=list)


class AgentCaseExecutionContext(PlanningModel):
    schema_version: Literal[1] = 1
    project_id: str
    run_id: str
    run_item_id: str
    plan_id: str
    objective: str
    target_key: str
    steps: list[CaseExecutionStep] = Field(min_length=1)
    runtime_inputs: dict[str, Any] = Field(default_factory=dict)
    evidence: list[PlanningEvidence] = Field(default_factory=list)
    unresolved: list[PlanningGap] = Field(default_factory=list)
    business_environment: BusinessPlanningEnvironment | None = None
    environment_admission: BusinessExecutionAdmission | None = None
    action_binding_protocol: Literal["case-action-binding-v1"] | None = None
    action_admission_protocol: Literal["case-action-admission-v1"] | None = None
    operation_protocol: Literal["case-operation-v1"] | None = None
    preparation_scope_protocol: Literal["preparation-scope-v1", "preparation-scope-v2"] | None = None
    preparation_scopes: dict[str, PreparationScopeRecord] | None = None
    effect_evidence_protocol: Literal["case-effect-evidence-v1", "case-effect-evidence-v2"] | None = None
    effect_evidence_max_bytes: int | None = Field(default=None, ge=1, le=10000000)
    effect_coverage_protocol: Literal["case-effect-coverage-v1", "case-effect-coverage-v2"] | None = None
    effect_coverage_max_bytes: int | None = Field(default=None, ge=1, le=10000000)
    effect_owner_item_ids: dict[str, str] | None = None
    effect_expected_source_protocol: Literal["case-effect-document-expected-v1"] | None = None
    scene_receipt_protocol: Literal["case-scene-receipt-v1"] | None = None
    consumption_protocol: Literal["case-consumption-phase-v1"] | None = None
    receipt_correction_protocol: Literal["case-receipt-correction-v1"] | None = None
    receipt_correction_transport_options: ModelTransportOptions | None = None

    @model_validator(mode="after")
    def require_operation_admission(self):
        if self.receipt_correction_transport_options is not None and self.receipt_correction_protocol is None:
            raise ValueError("回执调用设置缺少固定纠正协议")
        if self.consumption_protocol and self.effect_coverage_protocol != "case-effect-coverage-v2":
            raise ValueError("消费阶段结束上下文需要固定多效果关系")
        if self.effect_expected_source_protocol and self.effect_evidence_protocol != "case-effect-evidence-v2":
            raise ValueError("固定文档预期需要多效果上下文")
        if any(getattr(step.item, "effect_schema", None) == 3 for step in self.steps) and not self.effect_expected_source_protocol:
            raise ValueError("文档预期执行项缺少固定来源协议")
        protocols = (self.preparation_scope_protocol, self.effect_evidence_protocol, self.effect_coverage_protocol)
        if any(value and value.endswith("-v2") for value in protocols) and protocols != (
            "preparation-scope-v2", "case-effect-evidence-v2", "case-effect-coverage-v2"):
            raise ValueError("固定多效果上下文需要一致的v2协议")
        if self.operation_protocol and (not self.action_binding_protocol or not self.action_admission_protocol):
            raise ValueError("固定逻辑操作上下文需要原执行项关联和动作领取策略")
        if self.preparation_scope_protocol and (not self.operation_protocol or self.preparation_scopes is None):
            raise ValueError("固定准备消费范围需要逻辑操作策略与原范围快照")
        if self.preparation_scope_protocol is None and self.preparation_scopes is not None:
            raise ValueError("准备消费范围缺少固定协议")
        if self.effect_evidence_protocol and not self.preparation_scope_protocol:
            raise ValueError("对象证据上下文需要固定准备消费范围")
        if bool(self.effect_evidence_protocol) != (self.effect_evidence_max_bytes is not None):
            raise ValueError("对象证据上下文需要实际冻结的大小配置")
        if self.effect_coverage_protocol and not self.effect_evidence_protocol:
            raise ValueError("对象覆盖上下文需要固定对象证据")
        if bool(self.effect_coverage_protocol) != (self.effect_coverage_max_bytes is not None):
            raise ValueError("对象覆盖上下文需要实际冻结的大小配置")
        if self.effect_coverage_protocol:
            if (not self.effect_owner_item_ids or
                self.effect_owner_item_ids.get(self.target_key) != self.run_item_id or
                any(scope.source_owner_key not in self.effect_owner_item_ids
                    for scope in (self.preparation_scopes or {}).values())):
                raise ValueError("对象覆盖上下文缺少原固定拥有者与运行条目映射")
        elif self.effect_owner_item_ids is not None:
            raise ValueError("对象覆盖拥有者映射缺少固定协议")
        return self

    def execution_json(self, *, exclude=None) -> dict[str, Any]:
        omitted = set(exclude or ())
        if self.scene_receipt_protocol is None:
            omitted.add("scene_receipt_protocol")
        if self.receipt_correction_protocol is None:
            omitted.add("receipt_correction_protocol")
        if self.receipt_correction_transport_options is None:
            omitted.add("receipt_correction_transport_options")
        if self.consumption_protocol is None:
            omitted.add("consumption_protocol")
        if self.effect_expected_source_protocol is None:
            omitted.add("effect_expected_source_protocol")
        if self.business_environment is None:
            omitted.add("business_environment")
        if self.environment_admission is None:
            omitted.add("environment_admission")
        if self.action_binding_protocol is None:
            omitted.add("action_binding_protocol")
        if self.action_admission_protocol is None:
            omitted.add("action_admission_protocol")
        if self.operation_protocol is None:
            omitted.add("operation_protocol")
        if self.preparation_scope_protocol is None:
            omitted.add("preparation_scope_protocol")
        if self.preparation_scopes is None:
            omitted.add("preparation_scopes")
        if self.effect_evidence_protocol is None:
            omitted.add("effect_evidence_protocol")
        if self.effect_evidence_max_bytes is None:
            omitted.add("effect_evidence_max_bytes")
        if self.effect_coverage_protocol is None:
            omitted.add("effect_coverage_protocol")
        if self.effect_coverage_max_bytes is None:
            omitted.add("effect_coverage_max_bytes")
        if self.effect_owner_item_ids is None:
            omitted.add("effect_owner_item_ids")
        return self.model_dump(mode="json", exclude=omitted)

    def runtime_context(self) -> dict[str, Any]:
        """Use the existing graph/TurnState/snapshot context, without copies per layer."""
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "run_item_id": self.run_item_id,
            "execution_basis": "cases",
            "case_execution": self.execution_json(),
        }


class CaseReceiptObject(PlanningModel):
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class CaseReceiptCorrectionCheckpoint(PlanningModel):
    protocol: Literal["case-receipt-correction-v1"] = "case-receipt-correction-v1"
    phase: Literal["invoking", "response"]
    call_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model_key: str = Field(min_length=1)
    model_parameters: dict[str, Any]
    runtime_parameters: dict[str, Any]
    execution_model_calls: int = Field(ge=0)
    total_model_calls: int = Field(ge=1)
    max_iterations: int = Field(ge=1)
    request: CaseReceiptObject
    response: CaseReceiptObject | None = None

    @model_validator(mode="after")
    def validate_saved_boundary(self):
        if (self.phase == "response") != (self.response is not None):
            raise ValueError("回执响应身份与持久阶段不一致")
        if self.total_model_calls != self.execution_model_calls + 1 or self.total_model_calls > self.max_iterations:
            raise ValueError("回执调用必须只消耗一次原剩余预算")
        return self


class AgentCaseExecutionCheckpoint(PlanningModel):
    schema_version: Literal[1] = 1
    project_id: str
    run_id: str
    run_item_id: str
    plan_id: str
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_attempt_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    phase: Literal["admitted", "running", "snapshot"]
    snapshot_id: str | None = None
    snapshot_version: int | None = Field(default=None, ge=1)
    receipt_correction: CaseReceiptCorrectionCheckpoint | None = None

    @model_serializer(mode="wrap")
    def preserve_original_checkpoint(self, handler):
        data = handler(self)
        if self.receipt_correction is None:
            data.pop("receipt_correction", None)
        return data


class CaseToolEvidenceReference(PlanningModel):
    call_id: str = Field(min_length=1, max_length=256)
    output_path_tokens: list[str] = Field(default_factory=list, max_length=32)


class CaseExecutionCheck(PlanningModel):
    name: str = Field(min_length=1, max_length=240)
    expected_path_tokens: list[str] = Field(max_length=32)
    observations: list[CaseToolEvidenceReference] = Field(min_length=1, max_length=20,
        description="For any operator other than semantic, reference exactly one actual value. "
            "Semantic may reference multiple values. Do not add action-success evidence to a scalar comparison.")
    operator: Literal["equals", "not_equals", "contains", "not_contains", "lt", "lte", "gt", "gte", "semantic"]
    verdict: Literal["passed", "failed", "not_run"] | None = None
    reason: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def check_method(self):
        if self.operator == "semantic" and (self.verdict is None or not self.reason.strip()):
            raise ValueError("语义检查必须声明判定和依据说明")
        if self.operator != "semantic" and len(self.observations) != 1:
            raise ValueError("确定性比较需要且只能引用一个实际值")
        return self


class CaseOutputReference(PlanningModel):
    kind: Literal["reference"]
    source: CaseToolEvidenceReference


class CaseOutputObject(PlanningModel):
    kind: Literal["object"]
    fields: dict[str, CaseOutputNode]


class CaseOutputArray(PlanningModel):
    kind: Literal["array"]
    items: list[CaseOutputNode]


# No upstream counterpart (checked: CaseStepReceipt, literal_evidence and
# persisted ToolJob output): compose multiple real outputs without literal values.
CaseOutputNode = Annotated[
    CaseOutputReference | CaseOutputObject | CaseOutputArray, Field(discriminator="kind")]
CaseOutputObject.model_rebuild()
CaseOutputArray.model_rebuild()


class CaseStepReceipt(PlanningModel):
    item_key: str = Field(min_length=1, max_length=80)
    status: Literal["passed", "failed", "error", "blocked", "skipped"]
    summary: str = Field(min_length=1, max_length=4000)
    checks: list[CaseExecutionCheck] = Field(default_factory=list, max_length=200)
    outputs: dict[str, CaseToolEvidenceReference] = Field(default_factory=dict)
    output_source: CaseToolEvidenceReference | None = None
    output_structure: CaseOutputNode | None = None

    @model_serializer(mode="wrap")
    def preserve_original_receipt(self, handler):
        data = handler(self)
        if self.output_structure is None:
            data.pop("output_structure", None)
        return data

    @model_validator(mode="after")
    def one_output_shape(self):
        if sum((bool(self.outputs), self.output_source is not None, self.output_structure is not None)) > 1:
            raise ValueError("输出不能同时声明完整对象来源、字段映射或组合结构")
        return self


class AgentCaseExecutionReceipt(PlanningModel):
    schema_version: Literal[1] = 1
    steps: list[CaseStepReceipt] = Field(min_length=1, max_length=200)


class CaseConsumptionClosure(PlanningModel):
    reason: str = Field(min_length=1, max_length=4000)
    receipt: AgentCaseExecutionReceipt
