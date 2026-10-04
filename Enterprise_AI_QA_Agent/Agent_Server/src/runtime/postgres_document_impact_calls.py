"""Leased impact calls; large immutable requests/responses remain object assets."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.document_impact_calls import DocumentImpactCallRecord
from src.application.documents.impact_checkpoint_io import ImpactCallBudgetExceeded

logger = logging.getLogger(__name__)


class PostgresDocumentImpactCalls:
    def __init__(self, tasks):
        self._tasks = tasks
        self.table = tasks._docs + "_impact_calls"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.table):
            raise ValueError("Invalid impact call table identifier")

    def initialize(self, cur):
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
            id UUID PRIMARY KEY, task_id UUID NOT NULL REFERENCES {self._tasks._manifests}(task_id),
            call_key TEXT NOT NULL, record JSONB NOT NULL, UNIQUE(task_id,call_key))""")

    def _owned(self, cur, claim, *, require_time=False):
        self._tasks._fixed_document(cur, claim)
        row = self._tasks._owned(cur, claim)
        if self._tasks._seed_type.model_validate(row["seed"]) != claim.seed:
            raise ValueError("影响调用不能替换任务固定seed")
        if require_time:
            cur.execute("SELECT clock_timestamp() AS now")
            if not row["deadline_at"] or row["deadline_at"] <= cur.fetchone()["now"]:
                raise TimeoutError("影响调用原任务预算已耗尽")
        return row

    def _read(self, cur, task_id, *, call_id=None, call_key=None):
        clause, value = ("id", call_id) if call_id is not None else ("call_key", call_key)
        cur.execute(f"SELECT record FROM {self.table} WHERE task_id=%s AND {clause}=%s", (task_id, value))
        row = cur.fetchone()
        return DocumentImpactCallRecord.model_validate(row["record"]) if row else None

    async def get(self, project_id, document_id, task_id, *, call_id=None, call_key=None):
        if (call_id is None) == (call_key is None):
            raise ValueError("影响调用查询需要一种明确身份")
        def read():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                clause, value = ("c.id", call_id) if call_id is not None else ("c.call_key", call_key)
                cur.execute(f"SELECT c.record FROM {self.table} c JOIN {self._tasks._tasks} t ON t.id=c.task_id "
                    f"WHERE t.project_id=%s AND t.document_id=%s AND t.id=%s AND {clause}=%s",
                    (project_id, document_id, task_id, value))
                row = cur.fetchone()
                return DocumentImpactCallRecord.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    async def list(self, project_id, document_id, task_id):
        def read():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT c.record FROM {self.table} c JOIN {self._tasks._tasks} t ON t.id=c.task_id "
                    "WHERE t.project_id=%s AND t.document_id=%s AND t.id=%s ORDER BY c.record->>'created_at',c.id",
                    (project_id, document_id, task_id))
                return [DocumentImpactCallRecord.model_validate(row["record"]) for row in cur.fetchall()]
        return await asyncio.to_thread(read)

    @staticmethod
    def check_identity(record, selection_hash):
        if record.selection_hash != selection_hash:
            raise ValueError("同一影响调用身份不能使用不同范围或续读/重试身份")

    async def prepare(self, claim, *, call_key, selection_hash, selection, request, budget, manifest_sha256):
        def insert():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                self._owned(cur, claim, require_time=True)
                prior = self._read(cur, claim.task.id, call_key=call_key)
                if prior:
                    self.check_identity(prior, selection_hash)
                    return prior
                snapshot = claim.seed.invocation_snapshot
                if snapshot is None:
                    raise ValueError("历史影响任务未冻结调用策略")
                cur.execute(f"SELECT record FROM {self._tasks._manifests} WHERE task_id=%s", (claim.task.id,))
                fixed = cur.fetchone()
                if not fixed or fixed["record"]["sha256"] != manifest_sha256:
                    raise ValueError("影响调用不能替换固定范围清单")
                cur.execute(f"SELECT record FROM {self.table} WHERE task_id=%s", (claim.task.id,))
                records = [DocumentImpactCallRecord.model_validate(row["record"]) for row in cur.fetchall()]
                policy = snapshot.config.payload
                if len(records) >= policy.max_logical_calls:
                    logger.warning("document_impact_call_budget_rejected", extra={"task_id": claim.task.id,
                        "budget_kind": "logical_calls", "reserved_calls": len(records), "max_calls": policy.max_logical_calls})
                    raise ImpactCallBudgetExceeded("影响逻辑调用额度已耗尽，未请求模型", kind="logical_calls")
                if sum(record.budget.reserved_estimated_tokens for record in records) + budget.reserved_estimated_tokens > policy.max_reserved_estimated_tokens:
                    logger.warning("document_impact_call_budget_rejected", extra={"task_id": claim.task.id,
                        "budget_kind": "estimated_tokens", "max_reserved_estimated_tokens": policy.max_reserved_estimated_tokens})
                    raise ImpactCallBudgetExceeded("影响调用累计估算预算已耗尽，未请求模型", kind="estimated_tokens")
                predecessor_id = selection.previous_call_id or selection.retry_of_call_id
                predecessor = self._read(cur, claim.task.id, call_id=predecessor_id) if predecessor_id else None
                if predecessor_id and (predecessor is None or predecessor.selection.unit != selection.unit):
                    raise ValueError("影响续读或重试必须关联相同固定单元")
                if selection.previous_call_id and (predecessor.phase != "response_saved" or predecessor.response_disposition != "received"):
                    raise ValueError("影响续读必须有完整的已保存成功响应")
                if selection.retry_of_call_id and predecessor.phase not in {"outcome_unknown", "response_unavailable"}:
                    raise ValueError("影响未知结果重试必须引用原未知调用")
                # A new initial key must not silently repeat the same scope unit.
                if not predecessor_id and any(record.selection.unit == selection.unit for record in records):
                    raise ValueError("影响单元已有调用，请读取原结果或明确续读/重试")
                now = datetime.now(timezone.utc)
                record = DocumentImpactCallRecord(id=str(uuid4()), task_id=claim.task.id, call_key=call_key,
                    selection_hash=selection_hash, selection=selection, request=request, budget=budget, phase="ready",
                    created_at=now, updated_at=now)
                cur.execute(f"INSERT INTO {self.table}(id,task_id,call_key,record) VALUES(%s,%s,%s,%s::jsonb)",
                    (record.id, record.task_id, record.call_key, record.model_dump_json()))
                return record
        saved = await asyncio.to_thread(insert)
        logger.info("document_impact_call_prepared", extra={"task_id": saved.task_id, "call_id": saved.id,
            "request_sha256": saved.request.sha256, "reserved_estimated_tokens": saved.budget.reserved_estimated_tokens})
        return saved

    def _write(self, cur, record):
        DocumentImpactCallRecord.model_validate(record.model_dump())
        record.updated_at = datetime.now(timezone.utc)
        cur.execute(f"UPDATE {self.table} SET record=%s::jsonb WHERE id=%s AND task_id=%s",
            (record.model_dump_json(), record.id, record.task_id))
        return record

    async def start(self, claim, call_id):
        """Commit intent atomically. Only its winner receives permission to send."""
        def update():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                self._owned(cur, claim, require_time=True)
                record = self._read(cur, claim.task.id, call_id=call_id)
                if record is None:
                    raise KeyError("影响调用不存在")
                if record.phase != "ready":
                    return record, False
                record.phase = "invoking"
                record.invocation_attempt_id = claim.attempt_id
                return self._write(cur, record), True
        result = await asyncio.to_thread(update)
        logger.info("document_impact_call_intent_saved", extra={"task_id": claim.task.id,
            "call_id": call_id, "send_owner": result[1], "attempt_id": claim.attempt_id})
        return result

    async def save_response(self, claim, call_id, response, disposition):
        def update():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                self._owned(cur, claim)
                record = self._read(cur, claim.task.id, call_id=call_id)
                if record is None or record.invocation_attempt_id != claim.attempt_id:
                    raise ValueError("影响响应不属于当前调用attempt")
                if record.phase == "response_saved":
                    if record.response != response or record.response_disposition != disposition:
                        raise ValueError("已保存影响响应不能改写")
                    return record
                if record.phase != "invoking":
                    raise ValueError("影响调用当前状态不能保存响应")
                record.phase, record.response, record.response_disposition = "response_saved", response, disposition
                return self._write(cur, record)
        saved = await asyncio.to_thread(update)
        logger.info("document_impact_call_response_saved", extra={"task_id": saved.task_id,
            "call_id": saved.id, "response_sha256": response.sha256, "disposition": disposition})
        return saved

    async def mark_unknown(self, claim, call_id, *, code, response_unavailable=False):
        def update():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                self._owned(cur, claim)
                record = self._read(cur, claim.task.id, call_id=call_id)
                if record is None:
                    raise KeyError("影响调用不存在")
                if record.phase != "invoking":
                    return record
                if record.invocation_attempt_id == claim.attempt_id and code == "recovered_without_response":
                    raise ValueError("当前attempt调用仍可能进行中，不能当作恢复未知结果")
                record.phase = "response_unavailable" if response_unavailable else "outcome_unknown"
                record.outcome_code = code
                return self._write(cur, record)
        saved = await asyncio.to_thread(update)
        logger.warning("document_impact_call_outcome_unknown", extra={"task_id": saved.task_id,
            "call_id": saved.id, "phase": saved.phase, "outcome_code": saved.outcome_code})
        return saved

    async def object_is_referenced(self, uri):
        def read():
            with postgres_connect(self._tasks._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {self.table} WHERE record#>>'{{request,storage_uri}}'=%s "
                    "OR record#>>'{response,storage_uri}'=%s LIMIT 1", (uri, uri))
                return cur.fetchone() is not None
        return await asyncio.to_thread(read)
