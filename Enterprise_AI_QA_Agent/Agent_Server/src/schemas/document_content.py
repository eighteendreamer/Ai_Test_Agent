"""Fixed content identity, separate from the current document catalog."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from src.schemas.api_docs import ApiDocVersionRecord, ApiDocumentAsset
from src.schemas.document_parsing import DocumentParseSnapshot


class DocumentContentBasis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    version: ApiDocVersionRecord
    asset: ApiDocumentAsset
    parse_snapshot: DocumentParseSnapshot | None = None
    expected_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    role: Literal["authored_snapshot", "version_linked_source", "current_linked_source"]
    warnings: list[str] = Field(default_factory=list)
