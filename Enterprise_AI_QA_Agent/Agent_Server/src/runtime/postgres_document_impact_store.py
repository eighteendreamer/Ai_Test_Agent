"""Impact submissions and immutable input/scope checkpoints on leased tasks.

Input readiness is not an analysis result. The final snapshot lifecycle remains
separate; a saved manifest is not a model response or a completed analysis.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from src.application.documents.impact_sources import impact_record_hash
from src.runtime.postgres_document_operation_store import PostgresDocumentTaskStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_management import TestCaseVersionRecord
from src.schemas.document_impact import (
    DocumentImpactClaim, DocumentImpactCommand, DocumentImpactSourceSeed,
    DocumentImpactTaskPage, DocumentImpactTaskRecord,
)
from src.schemas.document_impact_batches import DocumentImpactManifestCheckpoint
from src.runtime.postgres_document_impact_calls import PostgresDocumentImpactCalls
from src.runtime.postgres_document_impact_responses import PostgresDocumentImpactResponses

logger = logging.getLogger(__name__)


class PostgresDocumentImpactStore(PostgresDocumentTaskStore):
    _table_suffix = "impact"
    _working_phase = "preparing"
    _additional_phases = ("input_saved", "analyzing")
    _timeout_json_path = "{config,payload,timeout_seconds}"
    _label = "影响分析"
    _log_prefix = "document_impact"
    _event_prefix = "document-impact"
    _seed_type = DocumentImpactSourceSeed
    _task_type = DocumentImpactTaskRecord
    _command_type = DocumentImpactCommand
    _claim_type = DocumentImpactClaim
    _task_page_type = DocumentImpactTaskPage
    _extra_input_columns = ("difference_snapshot_id",)
    _uuid_record_fields = (*PostgresDocumentTaskStore._uuid_record_fields, *_extra_input_columns)

    def __init__(self, settings, *, lease_seconds=30):
        super().__init__(settings, lease_seconds=lease_seconds)
        self._cases = settings.database.postgres_test_case_table
        self._versions = settings.database.postgres_test_case_version_table
        self._manifests = self._docs + "_impact_manifests"
        self.calls = PostgresDocumentImpactCalls(self)
        self.responses = PostgresDocumentImpactResponses(self)
        for table in (self._cases, self._versions, self._manifests):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table):
                raise ValueError("Invalid impact case table identifier")

    @property
    def tables(self):
        return [self.responses.table, self.calls.table, self._manifests, self._tasks]

    def _initialize_results(self, cur):
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._manifests} (
            task_id UUID PRIMARY KEY REFERENCES {self._tasks}(id), record JSONB NOT NULL)""")
        self.calls.initialize(cur)
        self.responses.initialize(cur)

    def _input_schema(self):
        return f"""difference_snapshot_id UUID NOT NULL REFERENCES {self._docs}_diff_snapshots(id),
                    input_checkpoint JSONB NULL,"""

    def _input_values(self, seed):
        return (seed.difference_snapshot.id,)

    @staticmethod
    def request_hash(payload):
        parameters = payload.model_dump(mode="json", exclude={"creation_key"})
        # Preserve original submission hashes when new optional fields are absent.
        # An explicit model/environment/release still participates in identity.
        for key in ("model_key", "environment_key", "target_release"):
            if parameters.get(key) is None:
                parameters.pop(key, None)
        encoded = json.dumps(parameters, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _claim_phase_sql(self):
        return (f"CASE WHEN input_checkpoint IS NULL THEN 'preparing' WHEN EXISTS("
            f"SELECT 1 FROM {self._manifests} m WHERE m.task_id={self._tasks}.id) "
            "THEN 'analyzing' ELSE 'input_saved' END")

    def _validate_seed(self, cur, seed):
        cur.execute(f"SELECT record FROM {self._docs}_diff_snapshots WHERE project_id=%s AND document_id=%s AND id=%s",
            (seed.project_id, seed.document_id, seed.difference_snapshot.id))
        row = cur.fetchone()
        if not row or row["record"] != seed.difference_snapshot.model_dump(mode="json"):
            raise ValueError("影响提交与固定项目比较结果不同")
        # Recheck immutable version and current project binding in the same
        # transaction; active/latest pointers are intentionally not reselected.
        cur.execute(f"SELECT c.project_id,v.id,v.case_id,v.record FROM {self._versions} v "
            f"JOIN {self._cases} c ON c.id=v.case_id WHERE v.id=ANY(%s::uuid[]) FOR SHARE OF c,v",
            ([item.version_id for item in seed.cases],))
        rows = {str(row["id"]): row for row in cur.fetchall()}
        for fixed in seed.cases:
            row = rows.get(fixed.version_id)
            if (not row or str(row["project_id"]) != seed.project_id or str(row["case_id"]) != fixed.case.id or
                impact_record_hash(TestCaseVersionRecord.model_validate(row["record"])) != fixed.version_record_sha256):
                raise ValueError("影响提交的用例版本或项目与捕获依据不同")

    async def save_input_checkpoint(self, claim, checkpoint):
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._fixed_document(cur, claim)
                row = self._owned(cur, claim)
                if checkpoint.task_id != claim.task.id or self._seed_type.model_validate(row["seed"]) != claim.seed:
                    raise ValueError("影响输入检查点不能替换任务或固定提交")
                if row["input_checkpoint"] and row["input_checkpoint"] != checkpoint.model_dump(mode="json"):
                    raise ValueError("已保存影响输入检查点不能改写")
                cur.execute(f"UPDATE {self._tasks} SET input_checkpoint=%s::jsonb,phase='input_saved',"
                    "message='固定影响输入已保存，尚未进行AI分析',updated_at=NOW() WHERE id=%s RETURNING *",
                    (checkpoint.model_dump_json(), claim.task.id))
                return self._record(cur.fetchone())
        saved = await asyncio.to_thread(update)
        logger.info("document_impact_input_checkpoint_saved", extra={"project_id": saved.project_id,
            "task_id": saved.id, "input_sha256": checkpoint.input_sha256})
        return saved

    async def get_seed(self, project_id, document_id, task_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT seed FROM {self._tasks} WHERE project_id=%s AND document_id=%s AND id=%s",
                    (project_id, document_id, task_id))
                row = cur.fetchone()
                return self._seed_type.model_validate(row["seed"]) if row else None
        return await asyncio.to_thread(read)

    async def get_manifest_checkpoint(self, project_id, document_id, task_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT m.record FROM {self._manifests} m JOIN {self._tasks} t ON t.id=m.task_id "
                    "WHERE t.project_id=%s AND t.document_id=%s AND t.id=%s", (project_id, document_id, task_id))
                row = cur.fetchone()
                return DocumentImpactManifestCheckpoint.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    async def save_manifest_checkpoint(self, claim, checkpoint):
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._fixed_document(cur, claim)
                row = self._owned(cur, claim)
                if (checkpoint.task_id != claim.task.id or self._seed_type.model_validate(row["seed"]) != claim.seed or
                    not row["input_checkpoint"] or
                    row["input_checkpoint"]["input_sha256"] != checkpoint.input_sha256):
                    raise ValueError("影响批次检查点不能替换固定任务或输入")
                cur.execute(f"SELECT record FROM {self._manifests} WHERE task_id=%s", (claim.task.id,))
                prior = cur.fetchone()
                if prior:
                    if prior["record"] != checkpoint.model_dump(mode="json"):
                        raise ValueError("已保存影响批次检查点不能改写")
                    return DocumentImpactManifestCheckpoint.model_validate(prior["record"])
                cur.execute(f"INSERT INTO {self._manifests}(task_id,record) VALUES(%s,%s::jsonb)",
                    (claim.task.id, checkpoint.model_dump_json()))
                cur.execute(f"UPDATE {self._tasks} SET phase='analyzing',"
                    "message='影响范围清单已保存，尚未调用模型',updated_at=NOW() WHERE id=%s", (claim.task.id,))
                return checkpoint
        saved = await asyncio.to_thread(update)
        logger.info("document_impact_manifest_checkpoint_saved", extra={"project_id": claim.task.project_id,
            "task_id": claim.task.id, "input_sha256": saved.input_sha256, "manifest_sha256": saved.sha256})
        return saved
