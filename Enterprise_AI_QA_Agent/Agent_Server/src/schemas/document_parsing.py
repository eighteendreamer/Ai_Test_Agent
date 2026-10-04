from __future__ import annotations

from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from src.schemas.api_docs import ApiDocumentAsset


class DocumentParseOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parser_id: str = Field(default="auto", min_length=1, max_length=120)
    encoding: Literal["utf-8-sig", "utf-16", "gb18030"] = "utf-8-sig"
    pdf_extraction_mode: Literal["plain", "layout"] = "plain"


class DocumentParseLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_bytes: int = Field(default=20_000_000, ge=1)
    max_expanded_bytes: int = Field(default=100_000_000, ge=1)
    max_zip_entries: int = Field(default=3000, ge=1)
    max_pages: int = Field(default=1000, ge=1)
    max_blocks: int = Field(default=50000, ge=1)
    max_text_chars: int = Field(default=5_000_000, ge=1)
    max_table_cells: int = Field(default=50000, ge=1)


class DocumentParsePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limits: DocumentParseLimits = Field(default_factory=DocumentParseLimits)
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_cpu_seconds: float = Field(default=30, gt=0, le=300)
    max_memory_bytes: int = Field(default=536_870_912, gt=0)
    max_output_bytes: int = Field(default=40_000_000, gt=0)


class DocumentParsingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: DocumentParsePolicy = Field(default_factory=DocumentParsePolicy)
    options: DocumentParseOptions = Field(default_factory=DocumentParseOptions)
    parser_by_extension: dict[str, str] = Field(default_factory=lambda: {
        ".md": "markdown", ".txt": "markdown", ".docx": "docx", ".pdf": "pdf"})


class DocumentParsingSettingsRecord(BaseModel):
    scope_key: str
    revision: int
    payload: DocumentParsingSettings


class DocumentSourceLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["line", "docx", "page"]
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    # Ordered DOCX body element / table row / cell / inner element indices.
    path: list[int] = Field(default_factory=list)
    part: str | None = None

    @model_validator(mode="after")
    def validate_origin(self):
        if any(token < 0 for token in self.path):
            raise ValueError("文档来源路径无效")
        if self.kind == "line" and (self.start_line is None or self.end_line is None or self.end_line < self.start_line):
            raise ValueError("文档来源行范围无效")
        if self.kind == "page" and self.page is None:
            raise ValueError("文档来源缺少页码")
        if self.kind == "docx" and (not self.part or not self.path):
            raise ValueError("文档来源缺少正文位置")
        return self


class DocumentParseWarning(BaseModel):
    code: str
    message: str
    source: DocumentSourceLocation | None = None


class DocumentTextBlock(BaseModel):
    id: str
    kind: Literal["text", "heading", "table", "code"]
    text: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    source: DocumentSourceLocation
    heading_level: int | None = Field(default=None, ge=1, le=9)


class DocumentSection(BaseModel):
    id: str
    heading_block_id: str
    title: str
    level: int = Field(ge=1, le=9)
    parent_id: str | None = None
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)


class DocumentTableCell(BaseModel):
    text: str
    source: DocumentSourceLocation


class DocumentTable(BaseModel):
    block_id: str
    rows: list[list[DocumentTableCell]]


class DocumentParserCapability(BaseModel):
    parser_id: str
    formats: list[str]
    implementation_version: str
    text: bool = True
    sections: bool = False
    tables: bool = False
    ocr: bool = False
    source_positions: list[str]
    transport: Literal["local", "http"] = "local"
    available: bool = True


class DocumentParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["document-parse-v1"] = "document-parse-v1"
    filename: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_size_bytes: int = Field(ge=0)
    parser: DocumentParserCapability
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    options: DocumentParseOptions
    text: str
    blocks: list[DocumentTextBlock] = Field(default_factory=list)
    sections: list[DocumentSection] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    warnings: list[DocumentParseWarning] = Field(default_factory=list)
    status: Literal["complete", "partial", "unsupported"] = "complete"
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_positions(self):
        blocks = {block.id: block for block in self.blocks}
        if len(blocks) != len(self.blocks):
            raise ValueError("解析块身份重复")
        previous_end = 0
        for block in self.blocks:
            if block.start_char < previous_end or block.end_char > len(self.text) or block.end_char < block.start_char:
                raise ValueError("解析块字符范围无效")
            if self.text[block.start_char:block.end_char] != block.text:
                raise ValueError("解析块与全文不一致")
            previous_end = block.end_char
        sections = {section.id: section for section in self.sections}
        if len(sections) != len(self.sections):
            raise ValueError("解析章节身份重复")
        for section in self.sections:
            block = blocks.get(section.heading_block_id)
            parent = sections.get(section.parent_id) if section.parent_id else None
            if block is None or block.kind != "heading" or section.start_char != block.start_char:
                raise ValueError("解析章节引用无效")
            if not section.start_char <= section.end_char <= len(self.text):
                raise ValueError("解析章节字符范围无效")
            if section.parent_id and (parent is None or parent.level >= section.level or
                parent.start_char > section.start_char or parent.end_char < section.end_char):
                raise ValueError("解析章节父引用无效")
        if len({table.block_id for table in self.tables}) != len(self.tables):
            raise ValueError("解析表格身份重复")
        for table in self.tables:
            if table.block_id not in blocks or blocks[table.block_id].kind != "table":
                raise ValueError("解析表格块引用无效")
        return self


class DocumentParseTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    creation_key: str = Field(min_length=1, max_length=160)
    version_id: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    options: DocumentParseOptions | None = None


class DocumentParseSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    document_id: str
    version_id: str
    original_asset: ApiDocumentAsset
    config: DocumentParsingSettingsRecord
    options: DocumentParseOptions
    parser: DocumentParserCapability


class DocumentParseSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    task_id: str
    seed: DocumentParseSeed
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    status: Literal["complete", "partial", "unsupported"]
    parser: DocumentParserCapability
    dependency_versions: dict[str, str]
    warnings: list[DocumentParseWarning]
    created_at: datetime


class DocumentParseFailureArtifact(BaseModel):
    storage_uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    exit_code: int | None = None


class DocumentParseSnapshotView(BaseModel):
    snapshot: DocumentParseSnapshot
    result: DocumentParseResult


class DocumentParseTaskRecord(BaseModel):
    id: str
    project_id: str
    document_id: str
    version_id: str
    creation_key: str
    status: Literal["queued", "running", "completed", "failed"]
    phase: Literal["queued", "parsing", "result_saved", "finished"]
    attempt_no: int
    snapshot_id: str | None = None
    error_code: str | None = None
    failure_artifact: DocumentParseFailureArtifact | None = None
    message: str
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime | None = None
    completed_at: datetime | None = None


class DocumentParseTaskPage(BaseModel):
    items: list[DocumentParseTaskRecord]
    has_more: bool
    limit: int
    offset: int


class DocumentParseSnapshotPage(BaseModel):
    items: list[DocumentParseSnapshot]
    has_more: bool
    limit: int
    offset: int


class DocumentParseFailureView(BaseModel):
    task_id: str
    artifact: DocumentParseFailureArtifact
    exit_code: int | None = None
    error: str
    stdout: str
    stderr: str


class DocumentParseClaim(BaseModel):
    task: DocumentParseTaskRecord
    seed: DocumentParseSeed
    snapshot: DocumentParseSnapshot | None = None
    attempt_id: str
    worker_id: str
    lease_token: str
    remaining_seconds: float


class DocumentParseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    task_type: Literal["document_parsing"] = "document_parsing"
    project_id: str
    task_id: str
