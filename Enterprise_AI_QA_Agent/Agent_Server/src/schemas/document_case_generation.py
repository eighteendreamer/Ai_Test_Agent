"""Ordinary document scopes are independent of accepted requirement changes."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.case_generation import (
    CaseGenerationAuthoringSnapshot, GenerationSnapshot, IncrementalGenerationModule,
)
from src.schemas.case_management import GenerationTemplateRecord
from src.schemas.document_differences import UUID_PATTERN
from src.schemas.document_parsing import DocumentParseSnapshot


class DocumentGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parse_snapshot_id: str = Field(pattern=UUID_PATTERN)
    objective: str = Field(min_length=1, max_length=4000)
    mode_key: str = Field(min_length=1, max_length=80)
    model_key: str | None = Field(default=None, min_length=1, max_length=160)
    max_cases_per_module: int = Field(default=30, ge=1, le=200)


class DocumentGenerationTaskRequest(DocumentGenerationRequest):
    creation_key: str = Field(min_length=1, max_length=160)


class DocumentGenerationSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module_id: str = Field(pattern=UUID_PATTERN)
    index: int = Field(ge=0)
    title: str
    section_id: str | None
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    block_ids: list[str]


class DocumentGenerationScope(GenerationSnapshot):
    implementation_version: Literal["document-generation-scope-v1"] = "document-generation-scope-v1"
    project_id: str = Field(pattern=UUID_PATTERN)
    document_id: str = Field(pattern=UUID_PATTERN)
    request: DocumentGenerationRequest
    parse_snapshot: DocumentParseSnapshot
    text: str = Field(min_length=1)
    segments: list[DocumentGenerationSegment] = Field(min_length=1)
    template: GenerationTemplateRecord
    authoring: CaseGenerationAuthoringSnapshot
    modules: list[IncrementalGenerationModule] = Field(min_length=1)
    basis_limitations: list[str]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_fixed(self):
        seed = self.parse_snapshot.seed
        if (seed.project_id != self.project_id or seed.document_id != self.document_id
            or self.parse_snapshot.id != self.request.parse_snapshot_id):
            raise ValueError("普通文档生成必须绑定同一项目文档的固定解析快照")
        if len(self.segments) != len(self.modules):
            raise ValueError("文档范围与生成模块清单不同")
        position = 0
        identities = set()
        for index, (segment, module) in enumerate(zip(self.segments, self.modules)):
            if (segment.index != index or segment.start_char != position or segment.end_char <= position
                or segment.end_char > len(self.text) or segment.module_id != module.id or module.id in identities
                or module.kind != "create" or module.generation.authoring != self.authoring):
                raise ValueError("文档模块必须顺序覆盖全文且使用同一固定生成依据")
            position = segment.end_char
            identities.add(module.id)
        if position != len(self.text):
            raise ValueError("文档模块未覆盖完整解析文本")
        return self.check_hash("sha256")
