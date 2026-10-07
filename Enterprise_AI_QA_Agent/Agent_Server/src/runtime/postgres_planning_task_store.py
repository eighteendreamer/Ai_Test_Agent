"""Planning inbox, leased checkpoints and immutable result in PostgreSQL.

Uses the existing Embedding job/Outbox transaction pattern. Planning owns no
Session: attempt and lease tokens fence all writes, including final insertion.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from uuid import uuid4

from src.application.test_cases.planning_sources import json_text
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.case_planning_task import (
    CasePlanningTaskClaim, CasePlanningTaskCommand, CasePlanningTaskPage,
    CasePlanningTaskRecord, PlanningTaskCheckpoint, PlanningTaskSeed,
)
from src.schemas.case_planning import planning_request_json
from src.schemas.case_planning_stages import PlanningStageCheckpoint

logger = logging.getLogger(__name__)


class PostgresPlanningTaskStore:
    def __init__(self, settings, *, lease_seconds: float = 30, environment_store=None) -> None:
        if lease_seconds <= 0:
            raise ValueError("Planning lease must be positive")
        self._settings = settings
        self._environments = environment_store
        self._table = settings.database.postgres_case_planning_task_table
        self._projects = settings.database.postgres_project_table
        self._plans = settings.database.postgres_case_plan_table
        self._outbox = settings.database.postgres_task_outbox_table
        self.lease_seconds = lease_seconds
        for identifier in (self._table, self._projects, self._plans, self._outbox):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", identifier):
                raise ValueError("Invalid planning task table identifier")

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._table} (
                id UUID PRIMARY KEY,
                project_id UUID NOT NULL REFERENCES {self._projects}(id),
                creation_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                seed JSONB NOT NULL,
                checkpoint JSONB NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed')),
                phase TEXT NOT NULL DEFAULT 'retrieval',
                message TEXT NOT NULL DEFAULT '规划任务已保存，等待 Worker',
                attempt_no INTEGER NOT NULL DEFAULT 0,
                attempts JSONB NOT NULL DEFAULT '[]'::jsonb,
                attempt_id UUID NULL,
                lease_token TEXT NULL,
                worker_id TEXT NULL,
                lease_expires_at TIMESTAMPTZ NULL,
                plan_id UUID NULL REFERENCES {self._plans}(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                started_at TIMESTAMPTZ NULL,
                deadline_at TIMESTAMPTZ NULL,
                completed_at TIMESTAMPTZ NULL,
                UNIQUE(project_id,creation_key)
            )""")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._table}_project ON {self._table}(project_id,created_at DESC,id)")

    @staticmethod
    def request_hash(request) -> str:
        return hashlib.sha256(json_text(planning_request_json(request)).encode()).hexdigest()

    @staticmethod
    def regression_request_hash(parent_run_id, submission) -> str:
        return hashlib.sha256(json_text({"kind": "agent_regression", "parent_run_id": parent_run_id,
            "request": submission}).encode()).hexdigest()

    async def submit(self, *, project_id: str, creation_key: str, seed: PlanningTaskSeed,
                     request_hash: str, stream: str) -> CasePlanningTaskRecord:
        return await asyncio.to_thread(self._submit, project_id, creation_key, seed, request_hash, stream)

    def _submit(self, project_id, creation_key, seed, request_hash, stream):
        if seed.plan.project_id != project_id or seed.project_context.get("id") != project_id:
            raise ValueError("规划输入项目身份不一致")
        source = seed.plan.regression
        expected = (self.regression_request_hash(source.parent_run_id, source.submission) if source
            else self.request_hash(seed.plan.request))
        if expected != request_hash:
            raise ValueError("规划输入与提交请求不一致")
        task_id = str(uuid4())
        checkpoint = PlanningTaskCheckpoint(plan=seed.plan.model_copy(deep=True),
            phase="finished" if source and seed.plan.status in {"planned", "blocked"} else "retrieval",
            stages=PlanningStageCheckpoint() if seed.stage_prompts is not None else None)
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            # As with the run creation service, bindability is checked again
            # under the project lock, not only before the transaction.
            cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR UPDATE", (project_id,))
            project = cur.fetchone()
            if project is None:
                raise KeyError("项目不存在")
            cur.execute(f"SELECT * FROM {self._table} WHERE project_id=%s AND creation_key=%s", (project_id, creation_key))
            previous = cur.fetchone()
            if previous:
                self._check_request(previous, request_hash)
                return self._record(previous)
            if project["status"] != "active":
                raise ValueError("归档项目不能创建规划任务")
            if seed.plan.request.environment is not None or seed.plan.environment is not None:
                if self._environments is None:
                    raise RuntimeError("持久规划尚未配置固定环境核对服务")
                self._environments.validate_in_cursor(cur, seed.plan)
            cur.execute(f"""INSERT INTO {self._table}(id,project_id,creation_key,request_hash,seed,checkpoint,phase)
                VALUES(%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s) RETURNING *""",
                (task_id, project_id, creation_key, request_hash, seed.model_dump_json(), checkpoint.model_dump_json(), checkpoint.phase))
            row = cur.fetchone()
            payload = CasePlanningTaskCommand(task_id=task_id, project_id=project_id).model_dump_json()
            cur.execute(f"INSERT INTO {self._outbox}(event_key,stream,payload) VALUES(%s,%s,%s::jsonb)",
                        (f"case-planning:{task_id}", stream, payload))
        return self._record(row)

    @staticmethod
    def _check_request(row, request_hash):
        if row["request_hash"] != request_hash:
            raise ValueError("同一规划请求身份不能使用不同目标或选项")

    async def get_by_request(self, project_id: str, creation_key: str, *, request_hash: str | None = None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._table} WHERE project_id=%s AND creation_key=%s", (project_id, creation_key))
                row = cur.fetchone()
            if row and request_hash is not None:
                self._check_request(row, request_hash)
            return self._record(row) if row else None
        return await asyncio.to_thread(read)

    async def get(self, project_id: str, task_id: str):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._table} WHERE id=%s AND project_id=%s", (task_id, project_id))
                row = cur.fetchone()
            return self._record(row) if row else None
        return await asyncio.to_thread(read)

    async def list(self, project_id: str, *, limit: int = 24, offset: int = 0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._table} WHERE project_id=%s ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s",
                            (project_id, limit + 1, offset))
                rows = cur.fetchall()
            return CasePlanningTaskPage(items=[self._record(row) for row in rows[:limit]],
                                        has_more=len(rows) > limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    async def claim(self, project_id: str, task_id: str, *, worker_id: str):
        def update():
            attempt_id, token = str(uuid4()), uuid4().hex
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""UPDATE {self._table} SET status='running', attempt_no=attempt_no+1,
                    attempt_id=%s,lease_token=%s,worker_id=%s,
                    attempts=attempts||jsonb_build_array(jsonb_build_object('id',%s::text,
                        'worker_id',%s::text,'attempt_no',attempt_no+1,'claimed_at',NOW(),
                        'reason',CASE WHEN status='queued' THEN 'initial' ELSE 'lease_expired' END)),
                    lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
                    started_at=COALESCE(started_at,NOW()),
                    deadline_at=COALESCE(deadline_at,NOW()+((seed->'plan'->'config_snapshot'->'policy'->>'timeout_seconds')::double precision)*INTERVAL '1 second'),
                    updated_at=NOW(),message='Worker 已领取规划任务'
                    WHERE id=%s AND project_id=%s AND
                    (status='queued' OR (status='running' AND lease_expires_at<=clock_timestamp()))
                    RETURNING *,EXTRACT(EPOCH FROM (deadline_at-clock_timestamp())) AS remaining_seconds""",
                    (attempt_id, token, worker_id, attempt_id, worker_id, self.lease_seconds, task_id, project_id))
                row = cur.fetchone()
            if not row:
                return None
            return CasePlanningTaskClaim(task=self._record(row), worker_id=worker_id,
                attempt_id=attempt_id, lease_token=token, remaining_seconds=float(row["remaining_seconds"]),
                seed=PlanningTaskSeed.model_validate(row["seed"]),
                checkpoint=PlanningTaskCheckpoint.model_validate(row["checkpoint"]))
        claim = await asyncio.to_thread(update)
        logger.info("case_planning_task_claimed" if claim else "case_planning_task_claim_skipped",
            extra={"project_id": project_id, "task_id": task_id, "worker_id": worker_id,
                   "attempt_id": claim.attempt_id if claim else None})
        return claim

    async def heartbeat(self, claim: CasePlanningTaskClaim) -> None:
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""UPDATE {self._table} SET lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second'
                    WHERE id=%s AND project_id=%s AND status='running' AND attempt_id=%s AND lease_token=%s
                    AND worker_id=%s AND lease_expires_at>clock_timestamp()""",
                    (self.lease_seconds, claim.task.id, claim.task.project_id, claim.attempt_id, claim.lease_token, claim.worker_id))
                if cur.rowcount != 1:
                    raise ContinuationLeaseLostError("规划任务租约已失效")
        await asyncio.to_thread(update)

    def _lock_owned(self, cur, claim):
        cur.execute(f"SELECT * FROM {self._table} WHERE id=%s AND project_id=%s FOR UPDATE",
                    (claim.task.id, claim.task.project_id))
        row = cur.fetchone()
        # Evaluate time after any row-lock wait, as the existing run store does.
        cur.execute("SELECT clock_timestamp() AS now")
        now = cur.fetchone()["now"]
        if (not row or row["status"] != "running" or not row["lease_expires_at"] or row["lease_expires_at"] <= now
            or str(row["attempt_id"]) != claim.attempt_id or row["lease_token"] != claim.lease_token
            or row["worker_id"] != claim.worker_id):
            logger.warning("case_planning_task_ownership_lost", extra={"project_id": claim.task.project_id,
                "task_id": claim.task.id, "attempt_id": claim.attempt_id, "worker_id": claim.worker_id})
            raise ContinuationLeaseLostError("规划任务当前 attempt 不再拥有写入权")
        return row

    @staticmethod
    def _check_checkpoint(row, checkpoint):
        original = PlanningTaskSeed.model_validate(row["seed"]).plan
        fixed = ("id", "project_id", "request", "config_snapshot", "model_key", "prompt_version", "created_at", "regression", "environment")
        if any(getattr(checkpoint.plan, key) != getattr(original, key) for key in fixed):
            raise ValueError("规划检查点不能替换固定输入或身份")
        prior = PlanningTaskCheckpoint.model_validate(row["checkpoint"])
        if original.config_snapshot.policy.linked_source_protocol:
            progress = checkpoint.linked_sources
            if checkpoint.phase not in {"retrieval", "finished"} and progress is None:
                raise ValueError("关联来源检查点不能丢弃")
            if progress is not None:
                if progress.next_index > len(progress.entries):
                    raise ValueError("关联来源进度超过固定范围")
                if prior.linked_sources is None:
                    from src.application.test_cases.planning_linked_sources import linked_source_queue
                    expected = linked_source_queue(checkpoint.plan.evidence)
                    if progress != expected or prior.phase != "retrieval":
                        raise ValueError("首次关联来源必须来自已读完整用例引用")
                else:
                    previous = prior.linked_sources
                    if len(progress.entries) != len(previous.entries) or progress.next_index < previous.next_index:
                        raise ValueError("关联来源不能变更固定范围或回退进度")
                    for entry, old in zip(progress.entries, previous.entries):
                        if entry.source != old.source or entry.case_evidence_ids != old.case_evidence_ids:
                            raise ValueError("关联来源不能替换固定引用")
                        if old.basis is not None and entry.basis != old.basis:
                            raise ValueError("关联来源不能替换固定版本或解析快照")
                        if (old.read or old.error is not None) and entry != old:
                            raise ValueError("关联来源不能改写已保存的读取结果")
        elif checkpoint.linked_sources is not None or checkpoint.phase == "sources":
            raise ValueError("旧规划任务不能新增关联来源阶段")
        if PlanningTaskSeed.model_validate(row["seed"]).stage_prompts is not None:
            if checkpoint.stages is None:
                raise ValueError("阶段检查点不能丢弃")
            PlanningStageCheckpoint.model_validate(checkpoint.stages.model_dump(mode="json"))
            if checkpoint.stages.stage == "effects" and (
                (checkpoint.stages.operation_selections is not None) != (original.config_snapshot.policy.operation_prefetch_protocol == "case-planning-item-operation-prefetch-v2")):
                raise ValueError("阶段操作选择不能替换原规划协议")
            if prior.stages is not None and prior.stages.stage == "effects":
                if checkpoint.stages != prior.stages:
                    raise ValueError("检查点不能回退阶段或改写固定逻辑决定")
        elif checkpoint.stages is not None:
            raise ValueError("旧任务不能改用新阶段协议")
        if checkpoint.next_round < prior.next_round or checkpoint.next_round > original.config_snapshot.policy.max_rounds:
            raise ValueError("规划检查点不能重置或超过原回合预算")
        # Any evidence already used by a persisted round stays byte-for-byte
        # stable. New read-only sources may be appended with their own identity.
        current = {item.id: item for item in checkpoint.plan.evidence}
        if any(current.get(item.id) != item for item in prior.plan.evidence):
            raise ValueError("规划检查点不能删除或改写已读取依据")
        if len(checkpoint.plan.rounds) < len(prior.plan.rounds):
            raise ValueError("规划检查点不能删除已记录的调用回合")
        for old, new in zip(prior.plan.rounds, checkpoint.plan.rounds):
            if old.get("evidence_reference_catalog") is not None and new.get("evidence_reference_catalog") != old["evidence_reference_catalog"]:
                raise ValueError("规划检查点不能改写已发送调用的短引用目录")
        sealed = max(0, len(prior.plan.rounds) - 1)
        if checkpoint.plan.rounds[:sealed] != prior.plan.rounds[:sealed]:
            raise ValueError("规划检查点不能改写此前完成的调用回合")

    async def save_checkpoint(self, claim, checkpoint: PlanningTaskCheckpoint, *, message: str):
        if checkpoint.phase == "finished":
            raise ValueError("最终检查点必须与结果计划一同提交")
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                row = self._lock_owned(cur, claim)
                self._check_checkpoint(row, checkpoint)
                cur.execute(f"UPDATE {self._table} SET checkpoint=%s::jsonb,phase=%s,message=%s,updated_at=NOW() WHERE id=%s RETURNING *",
                    (checkpoint.model_dump_json(), checkpoint.phase, message, claim.task.id))
                return self._record(cur.fetchone())
        saved = await asyncio.to_thread(update)
        logger.info("case_planning_checkpoint_saved", extra={"project_id": saved.project_id,
            "task_id": saved.id, "attempt_id": claim.attempt_id, "phase": saved.phase, "round_count": saved.round_count})
        return saved

    async def finish(self, claim, checkpoint: PlanningTaskCheckpoint):
        if checkpoint.phase != "finished":
            raise ValueError("只有最终检查点可以结束规划任务")
        record = checkpoint.plan
        if record.status in {"planned", "blocked", "no_match"} and record.decision is None:
            raise ValueError("规划成功需要保存最终结构化决定")
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR UPDATE", (record.project_id,))
                project = cur.fetchone()
                if not project or (record.status in {"planned", "blocked"} and project["status"] != "active"):
                    raise ValueError("项目已归档，不能发布新的可执行计划")
                row = self._lock_owned(cur, claim)
                self._check_checkpoint(row, checkpoint)
                cur.execute(f"""INSERT INTO {self._plans}(id,project_id,created_at,record) VALUES(%s,%s,%s,%s::jsonb)
                    ON CONFLICT(id) DO NOTHING""", (record.id, record.project_id, record.created_at, record.model_dump_json()))
                cur.execute(f"SELECT record FROM {self._plans} WHERE id=%s AND project_id=%s", (record.id, record.project_id))
                saved = cur.fetchone()
                if not saved or json_text(saved["record"]) != json_text(record.model_dump(mode="json")):
                    raise ValueError("最终计划身份已被不同结果占用")
                status = "failed" if record.status in {"retrieval_failed", "planning_failed"} else "completed"
                cur.execute(f"""UPDATE {self._table} SET status=%s,phase='finished',checkpoint=%s::jsonb,
                    message=%s,plan_id=%s,completed_at=NOW(),updated_at=NOW(),lease_token=NULL,
                    worker_id=NULL,lease_expires_at=NULL WHERE id=%s RETURNING *""",
                    (status, checkpoint.model_dump_json(), record.summary, record.id, claim.task.id))
                return self._record(cur.fetchone())
        saved = await asyncio.to_thread(update)
        logger.info("case_planning_task_finished", extra={"project_id": saved.project_id,
            "task_id": saved.id, "plan_id": saved.plan_id, "attempt_id": claim.attempt_id, "status": saved.status})
        return saved

    @staticmethod
    def _record(row):
        checkpoint = row["checkpoint"]
        return CasePlanningTaskRecord(id=str(row["id"]), project_id=str(row["project_id"]),
            creation_key=row["creation_key"], status=row["status"], phase=row["phase"], message=row["message"],
            attempt_no=row["attempt_no"], round_count=checkpoint.get("next_round", 0),
            plan_id=str(row["plan_id"]) if row["plan_id"] else None, created_at=row["created_at"],
            updated_at=row["updated_at"], started_at=row["started_at"], deadline_at=row["deadline_at"],
            completed_at=row["completed_at"])
