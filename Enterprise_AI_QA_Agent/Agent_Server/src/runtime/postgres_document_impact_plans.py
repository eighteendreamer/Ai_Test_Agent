"""Leased, immutable references to inference plans, separate from evidence manifests."""
from __future__ import annotations

import asyncio
import logging
import re

from src.application.documents.impact_sources import impact_record_hash
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.document_impact_reasoning import DocumentImpactReasoningCheckpoint

logger = logging.getLogger(__name__)


class PostgresDocumentImpactPlans:
    def __init__(self, tasks):
        self._tasks = tasks
        self.table = tasks._docs + "_impact_reasoning_plans"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.table):
            raise ValueError("Invalid impact reasoning plan table identifier")

    def initialize(self, cur):
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
            task_id UUID PRIMARY KEY REFERENCES {self._tasks._tasks}(id), record JSONB NOT NULL)""")

    async def get(self, project_id, document_id, task_id):
        def read():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT p.record FROM {self.table} p JOIN {self._tasks._tasks} t ON t.id=p.task_id "
                    "WHERE t.project_id=%s AND t.document_id=%s AND t.id=%s", (project_id, document_id, task_id))
                row = cur.fetchone()
                return DocumentImpactReasoningCheckpoint.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    async def save(self, claim, checkpoint):
        def write():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                self._tasks._fixed_document(cur, claim)
                row = self._tasks._owned(cur, claim)
                seed = self._tasks._seed_type.model_validate(row["seed"])
                cur.execute(f"SELECT record FROM {self._tasks._manifests} WHERE task_id=%s", (claim.task.id,))
                manifest = cur.fetchone()
                if (seed != claim.seed or seed.reasoning_settings is None or checkpoint.task_id != claim.task.id or
                    checkpoint.config_sha256 != impact_record_hash(seed.reasoning_settings) or
                    not row["input_checkpoint"] or checkpoint.input_sha256 != row["input_checkpoint"]["input_sha256"] or
                    not manifest or checkpoint.manifest_sha256 != manifest["record"]["sha256"]):
                    raise ValueError("推理计划检查点不能替换固定配置、输入、清单或任务")
                cur.execute(f"SELECT record FROM {self.table} WHERE task_id=%s", (claim.task.id,))
                prior = cur.fetchone()
                if prior:
                    saved = DocumentImpactReasoningCheckpoint.model_validate(prior["record"])
                    if any(getattr(saved, key) != getattr(checkpoint, key) for key in (
                        "task_id", "input_sha256", "manifest_sha256", "config_sha256", "sha256", "size_bytes")):
                        raise ValueError("已保存推理计划不能覆盖；新策略须创建新任务")
                    return saved
                cur.execute(f"INSERT INTO {self.table}(task_id,record) VALUES(%s,%s::jsonb)",
                    (claim.task.id, checkpoint.model_dump_json()))
                return checkpoint
        saved = await asyncio.to_thread(write)
        logger.info("document_impact_reasoning_plan_saved", extra={"task_id": saved.task_id,
            "plan_sha256": saved.sha256, "manifest_sha256": saved.manifest_sha256})
        return saved

    async def object_is_referenced(self, uri):
        def read():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {self.table} WHERE record->>'storage_uri'=%s LIMIT 1", (uri,))
                return cur.fetchone() is not None
        return await asyncio.to_thread(read)
