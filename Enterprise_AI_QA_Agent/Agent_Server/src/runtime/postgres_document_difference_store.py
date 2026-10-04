"""Fixed comparison inputs and output over the existing document task lifecycle."""
from __future__ import annotations

from src.runtime.postgres_document_operation_store import PostgresDocumentOperationStore
from src.schemas.document_differences import (
    DocumentDifferenceClaim, DocumentDifferenceCommand, DocumentDifferenceSeed,
    DocumentDifferenceSnapshot, DocumentDifferenceSnapshotPage, DocumentDifferenceTaskPage,
    DocumentDifferenceTaskRecord,
)


class PostgresDocumentDifferenceStore(PostgresDocumentOperationStore):
    _table_suffix = "diff"
    _working_phase = "comparing"
    _label = "比较"
    _log_prefix = "document_difference"
    _event_prefix = "document-difference"
    _seed_type = DocumentDifferenceSeed
    _snapshot_type = DocumentDifferenceSnapshot
    _task_type = DocumentDifferenceTaskRecord
    _command_type = DocumentDifferenceCommand
    _claim_type = DocumentDifferenceClaim
    _task_page_type = DocumentDifferenceTaskPage
    _snapshot_page_type = DocumentDifferenceSnapshotPage
    _extra_input_columns = ("old_snapshot_id", "new_snapshot_id")
    _uuid_record_fields = (*PostgresDocumentOperationStore._uuid_record_fields, *_extra_input_columns)

    def _input_schema(self):
        return f"""old_snapshot_id UUID NOT NULL REFERENCES {self._docs}_parse_snapshots(id),
                    new_snapshot_id UUID NOT NULL REFERENCES {self._docs}_parse_snapshots(id),"""

    def _input_values(self, seed):
        return seed.old_snapshot.id, seed.new_snapshot.id

    def _validate_seed(self, cur, seed):
        for snapshot in (seed.old_snapshot, seed.new_snapshot):
            cur.execute(f"SELECT record FROM {self._docs}_parse_snapshots WHERE project_id=%s AND document_id=%s AND id=%s",
                (seed.project_id, seed.document_id, snapshot.id))
            row = cur.fetchone()
            if not row or row["record"] != snapshot.model_dump(mode="json"):
                raise ValueError("比较输入与项目固定解析快照不一致")

    def _validate_snapshot(self, snapshot, seed):
        expected = "partial" if "partial" in {seed.old_snapshot.status, seed.new_snapshot.status} else "complete"
        if snapshot.status != expected:
            raise ValueError("比较结果不能改变输入解析的完整性状态")
        policy = snapshot.effective_policy
        if (policy.model_dump(exclude={"timeout_seconds"}) != seed.config.payload.policy.model_dump(exclude={"timeout_seconds"}) or
            policy.timeout_seconds > seed.config.payload.policy.timeout_seconds):
            raise ValueError("比较检查点不能放宽固定策略预算")
