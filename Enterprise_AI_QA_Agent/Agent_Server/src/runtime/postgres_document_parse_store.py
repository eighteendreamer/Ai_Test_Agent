"""Parsing domain checks over the shared fixed-document task lifecycle."""
from __future__ import annotations

from src.runtime.postgres_document_operation_store import PostgresDocumentOperationStore
from src.schemas.document_parsing import (
    DocumentParseClaim, DocumentParseCommand, DocumentParseSeed,
    DocumentParseSnapshot, DocumentParseTaskRecord, DocumentParseTaskPage, DocumentParseSnapshotPage,
)


class PostgresDocumentParseStore(PostgresDocumentOperationStore):
    _table_suffix = "parse"
    _working_phase = "parsing"
    _label = "解析"
    _log_prefix = "document_parse"
    _event_prefix = "document-parsing"
    _seed_type = DocumentParseSeed
    _snapshot_type = DocumentParseSnapshot
    _task_type = DocumentParseTaskRecord
    _command_type = DocumentParseCommand
    _claim_type = DocumentParseClaim
    _task_page_type = DocumentParseTaskPage
    _snapshot_page_type = DocumentParseSnapshotPage

    def _validate_seed(self, cur, seed):
        cur.execute(f"SELECT record FROM {self._docs}_versions WHERE id=%s AND document_id=%s",
            (seed.version_id, seed.document_id))
        version = cur.fetchone()
        if not version or version["record"]["snapshot"].get("original_asset") != seed.original_asset.model_dump(mode="json"):
            raise ValueError("原件资产与固定文档版本不一致")

    def _validate_snapshot(self, snapshot, seed):
        if snapshot.parser != seed.parser:
            raise ValueError("解析快照不能替换固定输入或任务身份")
