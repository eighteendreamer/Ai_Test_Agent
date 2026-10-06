"""Immutable logical-operation index; execution state remains on original Jobs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from src.application.runtime.case_action_binding import (
    CaseActionBinding, OPERATION_PROTOCOL, _digest,
)
from src.application.runtime.case_operation import RECEIPT_KEY, operation_identity
from src.infrastructure.postgres_runtime import postgres_connect


class PostgresCaseOperations:
    def __init__(self, settings):
        self._settings = settings
        jobs = settings.database.postgres_tool_job_table
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}",jobs):
            raise ValueError("Invalid original ToolJob table identifier")
        self.table = jobs[:25] + "_case_ops_" + hashlib.sha256(jobs.encode()).hexdigest()[:16]

    async def initialize(self):
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    operation_id TEXT PRIMARY KEY,
                    origin_job_id TEXT UNIQUE NOT NULL REFERENCES {self._settings.database.postgres_tool_job_table}(id),
                    record JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        await asyncio.to_thread(create)

    def read(self, cur, operation_id):
        cur.execute(f"SELECT record FROM {self.table} WHERE operation_id=%s", (operation_id,))
        row = cur.fetchone()
        return row["record"] if row else None

    def register(self, cur, identity, origin):
        # No upstream counterpart (checked: generation creation_key transaction,
        # original ToolJob claim and Agent trajectory course): preserve a logical
        # identity across calls without introducing another executor/state machine.
        record = {**identity,"origin_job_id":origin.id}
        cur.execute(f"INSERT INTO {self.table}(operation_id,origin_job_id,record) VALUES(%s,%s,%s::jsonb)",
            (identity["operation_id"],origin.id,json.dumps(record,ensure_ascii=False)))
        return record

    @staticmethod
    def validate_origin(record, origin):
        binding = CaseActionBinding.model_validate(origin.metadata.get("case_action_binding"))
        identity = operation_identity(origin,binding)
        if record != {**identity,"origin_job_id":origin.id}:
            raise ValueError("原逻辑操作索引、固定身份或完整输入不同")
        return identity

    async def validate_job(self, job):
        receipt = job.metadata.get(RECEIPT_KEY)
        if receipt is None:
            if job.started_at is not None:
                raise ValueError("已发送的新逻辑操作缺少原回执")
            return None  # Original permission/applicability denial can precede registration.
        if (receipt.get("version") != OPERATION_PROTOCOL or
            receipt.get("sha256") != _digest({k:v for k,v in receipt.items() if k != "sha256"})):
            raise ValueError("逻辑操作回执版本或SHA不同")
        binding = CaseActionBinding.model_validate(job.metadata["case_action_binding"])
        identity = operation_identity(job,binding)
        if any(receipt.get(k) != v for k,v in identity.items()):
            raise ValueError("逻辑操作回执与当前调用的固定身份或完整输入不同")
        def check():
            from src.runtime.postgres_tool_job_store import _job_from_row
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                record = self.read(cur,identity["operation_id"])
                if not record or record["origin_job_id"] != receipt["origin_job_id"]:
                    raise ValueError("逻辑操作来源缺少不可变原索引")
                cur.execute(f"SELECT * FROM {self._settings.database.postgres_tool_job_table} WHERE id=%s",
                    (record["origin_job_id"],))
                row = cur.fetchone()
                if not row:
                    raise ValueError("逻辑操作原Job不存在")
                origin = _job_from_row(row)
                original = self.validate_origin(record,origin)
                if receipt["decision"] != "input_conflict" and original != identity:
                    raise ValueError("逻辑操作来源与当前输入不同")
                if receipt["decision"] == "dispatched":
                    if origin.id != job.id or receipt["action_sent"] is not None or not receipt.get("execution_claimed"):
                        raise ValueError("别名逻辑操作不能宣称新发送")
                elif receipt["action_sent"] is not False or receipt.get("execution_claimed") or job.started_at is not None:
                    raise ValueError("未发送逻辑操作不能带工具领取或开始时间")
                if receipt["decision"] == "replayed":
                    if receipt.get("replayed_output_sha256") != _digest(job.output_payload):
                        raise ValueError("复用的固定逻辑操作输出SHA不同")
                    source = job.output_payload.get("case_operation_replay")
                    if source != {"origin_job_id":origin.id,
                        "origin_output_sha256":receipt["origin_output_sha256"],"action_sent":False}:
                        raise ValueError("复用逻辑操作输出与原来源不同")
                return receipt
        return await asyncio.to_thread(check)
