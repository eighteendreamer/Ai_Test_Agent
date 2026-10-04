from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.document_impact_batches import ImpactBatchModel
from src.schemas.document_impact_calls import DocumentImpactCallSelection, DocumentImpactUnit


class DocumentImpactReasoningPolicy(ImpactBatchModel):
    # Evidence read limits, not provider token/context or execution time budgets.
    max_group_units: int = Field(default=64, ge=1, le=100)
    max_group_read_bytes: int = Field(default=256000, ge=1, le=10000000)


class DocumentImpactReasoningSettings(ImpactBatchModel):
    scope_key: str
    revision: int
    payload: DocumentImpactReasoningPolicy


class DocumentImpactDiscoveryTask(ImpactBatchModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    members: list[DocumentImpactUnit] = Field(min_length=1, max_length=100)
    selection: DocumentImpactCallSelection
    read_bytes: int | None = Field(default=None, ge=1)
    reason_codes: list[Literal["retrieval_bytes_limit", "group_read_bytes_limit"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_members(self):
        if any(item.kind == "case" for item in self.members) or self.selection.unit != self.members[0]:
            raise ValueError("发现任务必须使用首个固定证据为锚点，不混入用例")
        if len({(item.kind, item.id) for item in self.members}) != len(self.members):
            raise ValueError("发现任务证据不能重复")
        return self


class DocumentImpactCaseReasoningTask(ImpactBatchModel):
    unit: DocumentImpactUnit
    source_segment_ids: list[str]

    @model_validator(mode="after")
    def validate_case(self):
        if self.unit.kind != "case" or len(set(self.source_segment_ids)) != len(self.source_segment_ids):
            raise ValueError("用例推理任务必须绑定完整固定用例及不重复的原来源分段")
        return self


class DocumentImpactReasoningPlan(ImpactBatchModel):
    implementation_version: Literal["impact-reasoning-plan-v1"] = "impact-reasoning-plan-v1"
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: DocumentImpactReasoningSettings
    discovery: list[DocumentImpactDiscoveryTask]
    cases: list[DocumentImpactCaseReasoningTask]
    # Preparation never constitutes inference coverage or a semantic decision.
    analysis_status: Literal["not_started"] = "not_started"


class DocumentImpactReasoningCheckpoint(ImpactBatchModel):
    task_id: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    created_at: datetime
