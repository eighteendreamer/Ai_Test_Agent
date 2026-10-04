from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from src.schemas.document_parsing import DocumentParseWarning, DocumentSection


class ImpactBatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentImpactBatchPolicy(ImpactBatchModel):
    # These are evidence partition/retrieval limits, not model token budgets.
    max_segment_chars: int = Field(default=12000, ge=1, le=200000)
    max_segments: int = Field(default=10000, ge=1, le=100000)
    boundary_context_chars: int = Field(default=500, ge=0, le=20000)
    max_retrieval_bytes: int = Field(default=256000, ge=1, le=10000000)


class DocumentImpactBatchSettings(ImpactBatchModel):
    scope_key: str
    revision: int
    payload: DocumentImpactBatchPolicy


class DocumentImpactTextSource(ImpactBatchModel):
    id: str
    roles: list[str] = Field(min_length=1)
    document_id: str
    version_id: str
    asset_id: str
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_id: str | None
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    length_chars: int = Field(ge=0)
    status: Literal["complete", "partial", "unsupported", "unparsed"]
    sections: list[DocumentSection]
    warnings: list[DocumentParseWarning]


class DocumentImpactSegment(ImpactBatchModel):
    id: str
    source_id: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    read_start_char: int = Field(ge=0)
    read_end_char: int = Field(ge=0)
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    block_ids: list[str]
    section_ids: list[str]
    split_block_ids: list[str]

    @model_validator(mode="after")
    def validate_ranges(self):
        if not self.read_start_char <= self.start_char <= self.end_char <= self.read_end_char:
            raise ValueError("影响分段的正文与边界范围无效")
        return self


class DocumentImpactCaseUnit(ImpactBatchModel):
    version_id: str
    case_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentImpactDifferenceUnit(ImpactBatchModel):
    kind: Literal["raw", "normalized", "section", "table"]
    index: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentImpactSourceGap(ImpactBatchModel):
    case_version_id: str
    source_index: int = Field(ge=0)
    evidence_id: str | None
    status: Literal["partial", "missing"]
    codes: list[str]


class DocumentImpactBatchManifest(ImpactBatchModel):
    schema_version: Literal["impact-batch-manifest-v1"] = "impact-batch-manifest-v1"
    project_id: str
    document_id: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config: DocumentImpactBatchSettings
    sources: list[DocumentImpactTextSource]
    segments: list[DocumentImpactSegment]
    cases: list[DocumentImpactCaseUnit]
    differences: list[DocumentImpactDifferenceUnit]
    source_gaps: list[DocumentImpactSourceGap]
    # A manifest records scope, not successful analysis or verified deployment.
    analysis_status: Literal["not_started"] = "not_started"

    @model_validator(mode="after")
    def validate_coverage(self):
        sources = {source.id: source for source in self.sources}
        if len(sources) != len(self.sources) or len({segment.id for segment in self.segments}) != len(self.segments):
            raise ValueError("影响来源或分段身份重复")
        roles = [role for source in self.sources for role in source.roles]
        if len(roles) != len(set(roles)):
            raise ValueError("影响来源角色重复")
        cursors = dict.fromkeys(sources, 0)
        counts = dict.fromkeys(sources, 0)
        for segment in self.segments:
            source = sources.get(segment.source_id)
            if source is None or segment.start_char != cursors[source.id] or segment.read_end_char > source.length_chars:
                raise ValueError("影响分段存在遗漏、重叠或未知来源")
            if source.length_chars and segment.start_char == segment.end_char:
                raise ValueError("非空来源不能生成空分段")
            cursors[source.id] = segment.end_char
            counts[source.id] += 1
        if any(cursors[key] != source.length_chars or not counts[key] for key, source in sources.items()):
            raise ValueError("影响来源没有完整分段")
        if len({case.version_id for case in self.cases}) != len(self.cases):
            raise ValueError("影响用例版本重复")
        case_ids = {case.version_id for case in self.cases}
        gap_keys = [(gap.case_version_id, gap.source_index) for gap in self.source_gaps]
        if len(gap_keys) != len(set(gap_keys)) or any(key[0] not in case_ids for key in gap_keys):
            raise ValueError("影响来源缺口重复或不属于固定用例")
        keys = [(unit.kind, unit.index) for unit in self.differences]
        if len(keys) != len(set(keys)):
            raise ValueError("影响差异身份重复")
        return self


class DocumentImpactManifestCheckpoint(ImpactBatchModel):
    task_id: str
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    created_at: datetime


class DocumentImpactTextCitation(ImpactBatchModel):
    source_id: str
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_char: StrictInt = Field(ge=0)
    end_char: StrictInt = Field(ge=0)
    quote: str = Field(min_length=1)


class DocumentImpactCaseCitation(ImpactBatchModel):
    version_id: str
    version_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    root: Literal["content_json", "execution_notes"] = "content_json"
    # Keys are literal tokens: a key containing '.' is not split into a path.
    path: list[StrictStr | StrictInt] = Field(default_factory=list)
    value: Any = Field(...)
