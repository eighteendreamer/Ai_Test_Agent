"""Read-only source progress before the first actual model invocation."""
from pydantic import Field, model_validator
from src.schemas.case_planning import PlanningModel
from src.schemas.case_management import TestCaseSourceRef
from src.schemas.document_content import DocumentContentBasis

LINKED_SOURCE_PROTOCOL = "case-planning-linked-documents-v1"
LINKED_SOURCE_PROMPT_VERSION = "case-planning-linked-documents-v10"


class PlanningLinkedSource(PlanningModel):
    case_evidence_ids: list[str]
    source: TestCaseSourceRef
    basis: DocumentContentBasis | None = None
    error: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    read: bool = False


class PlanningLinkedSources(PlanningModel):
    entries: list[PlanningLinkedSource] = Field(default_factory=list)
    next_index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def completed_reads_have_evidence(self):
        if self.next_index > len(self.entries) or any(not (entry.read or entry.error) for entry in self.entries[:self.next_index]):
            raise ValueError("关联来源进度必须对应已读取或明确失败的记录")
        if any(entry.read and (entry.basis is None or not entry.evidence_ids) for entry in self.entries):
            raise ValueError("关联来源读取完成需要固定身份及实际证据")
        return self
