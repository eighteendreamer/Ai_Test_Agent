"""Immutable factual response checks, separate from the original call record."""
from __future__ import annotations

import asyncio
import logging
import re

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.document_impact_responses import DocumentImpactResponseCheckpoint, VALIDATOR_VERSION

logger = logging.getLogger(__name__)


class PostgresDocumentImpactResponses:
    def __init__(self, tasks):
        self._tasks = tasks
        self.table = tasks._docs + "_impact_response_checks"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.table):
            raise ValueError("Invalid impact response check table identifier")

    def initialize(self, cur):
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
            call_id UUID NOT NULL REFERENCES {self._tasks.calls.table}(id),
            validator_version TEXT NOT NULL, record JSONB NOT NULL,
            PRIMARY KEY(call_id,validator_version))""")

    async def get(self, project_id, document_id, task_id, call_id, *, validator_version=None):
        def read():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT r.record FROM {self.table} r JOIN {self._tasks.calls.table} c ON c.id=r.call_id "
                    f"JOIN {self._tasks._tasks} t ON t.id=c.task_id WHERE t.project_id=%s AND t.document_id=%s "
                    "AND t.id=%s AND r.call_id=%s AND r.validator_version=COALESCE(%s, "
                    "CASE WHEN t.seed->'response_protocol'->>'version'='impact-call-v7' "
                    "THEN 'impact-response-validator-v3' WHEN t.seed->'response_protocol'->>'version'='impact-call-v6' "
                    "THEN 'impact-response-validator-v2' ELSE 'impact-response-validator-v1' END)",
                    (project_id, document_id, task_id, call_id, validator_version))
                row = cur.fetchone()
                return DocumentImpactResponseCheckpoint.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    async def save(self, claim, checkpoint: DocumentImpactResponseCheckpoint):
        def write():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                self._tasks.calls._owned(cur, claim)
                call = self._tasks.calls._read(cur, claim.task.id, call_id=checkpoint.call_id)
                cur.execute(f"SELECT record FROM {self._tasks._manifests} WHERE task_id=%s", (claim.task.id,))
                manifest = cur.fetchone()
                if (checkpoint.task_id != claim.task.id or call is None or call.phase != "response_saved" or
                    call.request.sha256 != checkpoint.request_sha256 or call.response.sha256 != checkpoint.response_sha256 or
                    not manifest or manifest["record"]["sha256"] != checkpoint.manifest_sha256):
                    raise ValueError("影响响应核验不能替换任务、调用或固定请求/响应/清单")
                cur.execute(f"SELECT record FROM {self.table} WHERE call_id=%s AND validator_version=%s",
                    (checkpoint.call_id, checkpoint.validator_version))
                row = cur.fetchone()
                if row:
                    prior = DocumentImpactResponseCheckpoint.model_validate(row["record"])
                    if (prior.request_sha256 != checkpoint.request_sha256 or prior.response_sha256 != checkpoint.response_sha256 or
                        prior.manifest_sha256 != checkpoint.manifest_sha256 or prior.result.sha256 != checkpoint.result.sha256 or
                        prior.result.size_bytes != checkpoint.result.size_bytes):
                        raise ValueError("已保存的影响响应核验不能覆盖；修改实现须新增核验版本")
                    return prior
                cur.execute(f"INSERT INTO {self.table}(call_id,validator_version,record) VALUES(%s,%s,%s::jsonb)",
                    (checkpoint.call_id, checkpoint.validator_version, checkpoint.model_dump_json()))
                return checkpoint
        saved = await asyncio.to_thread(write)
        logger.info("document_impact_response_check_saved", extra={"task_id": saved.task_id,
            "call_id": saved.call_id, "validator_version": saved.validator_version, "result_sha256": saved.result.sha256})
        return saved

    async def object_is_referenced(self, uri):
        def read():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {self.table} WHERE record#>>'{{result,storage_uri}}'=%s LIMIT 1", (uri,))
                return cur.fetchone() is not None
        return await asyncio.to_thread(read)
