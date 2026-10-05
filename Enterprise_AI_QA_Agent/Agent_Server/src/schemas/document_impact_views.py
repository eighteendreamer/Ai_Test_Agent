from __future__ import annotations

from pydantic import BaseModel

from src.schemas.document_impact import DocumentImpactSnapshot
from src.schemas.document_impact_grouped import ImpactWorkflowResult


class DocumentImpactSnapshotView(BaseModel):
    snapshot: DocumentImpactSnapshot
    result: ImpactWorkflowResult
