"""Immutable generation requests; durable tasks own invocation/response state."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.case_management import GenerationTemplateRecord, TestCaseGenerateRequest
from src.schemas.document_differences import UUID_PATTERN
from src.schemas.document_impact import DocumentImpactCaseReference, DocumentImpactInputCheckpoint
from src.schemas.model_config import ModelInvocationRequest, ModelTransportOptions
from src.schemas.requirements import RequirementReview, RequirementVersion
from src.schemas.skill_snapshot import SkillPromptSnapshot


def generation_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class GenerationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def check_hash(self, field):
        if generation_hash(self.model_dump(mode="json", exclude={field})) != getattr(self, field):
            raise ValueError("生成快照正文与固定哈希不一致")
        return self


class CaseGenerationAuthoringSnapshot(GenerationSnapshot):
    model_key: str
    model_parameters: dict
    runtime_parameters: dict
    transport_options: ModelTransportOptions | None = None
    skills: list[SkillPromptSnapshot] = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_fixed(self):
        if self.model_parameters.get("key") != self.model_key:
            raise ValueError("生成模型快照身份不一致")
        return self.check_hash("sha256")


class PreparedCaseGeneration(GenerationSnapshot):
    implementation_version: Literal["case-generation-frozen-v1"] = "case-generation-frozen-v1"
    authoring: CaseGenerationAuthoringSnapshot
    request: TestCaseGenerateRequest
    invocation: ModelInvocationRequest
    prompt_version: str
    requires_content_json: bool
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_fixed(self):
        if self.request.model_key and self.request.model_key != self.authoring.model_key:
            raise ValueError("生成请求不能替换固定模型")
        return self.check_hash("sha256")


class IncrementalGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str = Field(pattern=UUID_PATTERN)
    requirement_version_id: str = Field(pattern=UUID_PATTERN)
    target_case_version_ids: list[Annotated[str, Field(pattern=UUID_PATTERN)]] = Field(default_factory=list, max_length=10000)
    include_new_cases: bool = False
    new_case_mode_key: str | None = Field(default=None, min_length=1, max_length=80)
    max_new_cases: int = Field(default=1, ge=1, le=200)
    model_key: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_scope(self):
        if len(self.target_case_version_ids) != len(set(self.target_case_version_ids)):
            raise ValueError("增量目标用例版本不能重复")
        if not self.target_case_version_ids and not self.include_new_cases:
            raise ValueError("请明确更新目标或新建范围")
        if self.include_new_cases and not self.new_case_mode_key:
            raise ValueError("新建用例需明确模式元数据，不改变现有模式执行方式")
        return self


class IncrementalGenerationModule(GenerationSnapshot):
    id: str = Field(pattern=UUID_PATTERN)
    kind: Literal["create", "update", "retirement_candidate"]
    target: DocumentImpactCaseReference | None = None
    expected_latest_version: int | None = Field(default=None, ge=1)
    generation: PreparedCaseGeneration | None = None
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_fixed(self):
        if (self.kind == "create") != (self.target is None):
            raise ValueError("更新/停用候选需明确原用例版本；新建不指定原用例")
        if (self.kind != "retirement_candidate") != (self.generation is not None):
            raise ValueError("生成模块需固定请求；停用候选不能调用生成模型")
        return self.check_hash("sha256")


class IncrementalGenerationScope(GenerationSnapshot):
    implementation_version: Literal["incremental-generation-scope-v1"] = "incremental-generation-scope-v1"
    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    request: IncrementalGenerationRequest
    requirement: RequirementVersion
    review: RequirementReview
    input_checkpoint: DocumentImpactInputCheckpoint
    template: GenerationTemplateRecord | None
    authoring: CaseGenerationAuthoringSnapshot | None
    modules: list[IncrementalGenerationModule] = Field(min_length=1)
    basis_limitations: list[str]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_fixed(self):
        if (self.requirement.id != self.request.requirement_version_id
            or self.requirement.requirement_id != self.request.requirement_id
            or self.review.project_id != self.project_id or self.review.document_id != self.document_id
            or self.review.requirement_version != self.requirement
            or self.input_checkpoint.task_id != self.review.source.task_id
            or self.input_checkpoint.input_sha256 != self.review.source.input_sha256):
            raise ValueError("增量范围必须绑定同一项目/需求版本/原评审及固定输入")
        return self.check_hash("sha256")


def fixed_generation_snapshot(model, **values):
    """Factory for new snapshots only; recovery always validates saved bytes."""
    candidate = model.model_construct(**values)
    body = candidate.model_dump(mode="json", exclude={"sha256"})
    return model.model_validate({**body, "sha256": generation_hash(body)})
