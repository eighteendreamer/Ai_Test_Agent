from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.model_config import ModelAuthType, ModelCapabilities, ModelTransport
from src.schemas.skill_snapshot import SkillPromptSnapshot


class ImpactAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentImpactAnalysisPolicy(ImpactAnalysisModel):
    model_key: str | None = Field(default=None, min_length=1, max_length=255)
    analysis_guidance: str = Field(default="", max_length=20000)
    # Evidence capacity and exact UTF-8 request size are separate budgets.
    # This is not a tokenizer or a guarantee that the provider accepts the input.
    max_input_bytes: int = Field(default=256000, ge=1, le=10000000)


class DocumentImpactAnalysisSettings(ImpactAnalysisModel):
    scope_key: str
    revision: int
    payload: DocumentImpactAnalysisPolicy


class DocumentImpactModelSnapshot(ImpactAnalysisModel):
    key: str
    provider: str
    transport: ModelTransport
    model_id: str
    temperature: float | None
    max_tokens: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    context_window: int = Field(ge=1)
    capabilities: ModelCapabilities
    auth_type: ModelAuthType
    oauth_provider: str | None
    api_base_url_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    extra_headers_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentImpactProjectSnapshot(ImpactAnalysisModel):
    id: str
    project_key: str
    name: str
    base_url_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    description_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    updated_at: datetime


class DocumentImpactEnvironment(ImpactAnalysisModel):
    environment_key: str | None = Field(default=None, min_length=1, max_length=160)
    target_release: str | None = Field(default=None, min_length=1, max_length=160)
    provenance: Literal["user_declared"] = "user_declared"
    deployment_status: Literal["unknown"] = "unknown"


class DocumentImpactPromptSnapshot(ImpactAnalysisModel):
    version: Literal["document-impact-v1"] = "document-impact-v1"
    system_prompt: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skills: list[SkillPromptSnapshot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_content(self):
        if hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("影响Prompt正文与哈希不一致")
        if len({skill.key for skill in self.skills}) != len(self.skills):
            raise ValueError("影响Prompt的Skill身份不能重复")
        if any(skill.prompt_block not in self.system_prompt for skill in self.skills):
            raise ValueError("影响Prompt必须包含固定Skill全文")
        return self


class DocumentImpactAnalysisContext(ImpactAnalysisModel):
    implementation_version: Literal["impact-analysis-context-v1"] = "impact-analysis-context-v1"
    model: DocumentImpactModelSnapshot
    config: DocumentImpactAnalysisSettings
    prompt: DocumentImpactPromptSnapshot
    project: DocumentImpactProjectSnapshot
    environment: DocumentImpactEnvironment
    token_measurement: Literal["not_available"] = "not_available"

    @model_validator(mode="after")
    def validate_model(self):
        if not self.model.capabilities.text_input or not self.model.capabilities.text_output:
            raise ValueError("影响分析模型必须支持文本输入和输出")
        if self.model.max_output_tokens >= self.model.context_window:
            raise ValueError("影响分析模型输出预算必须小于上下文窗口")
        if self.model.max_tokens != self.model.max_output_tokens:
            raise ValueError("影响分析模型输出预算字段不一致")
        return self
