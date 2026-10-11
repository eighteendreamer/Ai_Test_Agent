"""Opt-in scene expected references; original receipts remain unchanged."""
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from src.schemas.case_effect_sources import DocumentExpectedReference
from src.schemas.case_execution import AgentCaseExecutionReceipt, CaseConsumptionClosure, CaseExecutionCheck, CaseStepReceipt

SCENE_RECEIPT_PROTOCOL = "case-scene-receipt-v1"
SCENE_RECEIPT_PROMPT_VERSION = "case-execution-scene-receipt-v6"


class SceneExpectedReference(DocumentExpectedReference):
    """Same literal-reference mechanics, with a distinct scene-only contract."""


class SceneExecutionCheck(CaseExecutionCheck):
    expected_path_tokens: list[str] | None = Field(default=None,max_length=32)
    expected_source: SceneExpectedReference | None = None

    @model_validator(mode="after")
    def one_expected_basis(self):
        if (self.expected_path_tokens is None) == (self.expected_source is None):
            raise ValueError("检查需要且只能引用自身预期路径或固定场景预期")
        return self

    @model_serializer(mode="wrap")
    def omit_unused_basis(self,handler):
        return {k:v for k,v in handler(self).items() if k not in {"expected_path_tokens","expected_source"} or v is not None}


class SceneStepReceipt(CaseStepReceipt):
    checks: list[SceneExecutionCheck] = Field(default_factory=list,max_length=200)


class SceneExecutionReceipt(AgentCaseExecutionReceipt):
    schema_version: Literal[2] = 2
    steps: list[SceneStepReceipt] = Field(min_length=1,max_length=200)


class SceneConsumptionClosure(CaseConsumptionClosure):
    receipt: SceneExecutionReceipt


def case_receipt_type(protocol=None):
    if protocol is None:
        return AgentCaseExecutionReceipt
    if protocol != SCENE_RECEIPT_PROTOCOL:
        raise ValueError("不支持该场景回执协议")
    return SceneExecutionReceipt


def consumption_closure_type(protocol=None):
    case_receipt_type(protocol)
    return SceneConsumptionClosure if protocol else CaseConsumptionClosure
