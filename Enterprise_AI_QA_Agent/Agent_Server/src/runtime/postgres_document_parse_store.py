"""Version-bound document parsing inbox and immutable snapshots.

Follows PostgresPlanningTaskStore's project lock, Outbox transaction and leases.
Bodies remain in RustFS; this store never reads the current parser settings.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from uuid import uuid4

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.document_parsing import (
    DocumentParseClaim, DocumentParseCommand, DocumentParseSeed,
    DocumentParseSnapshot, DocumentParseTaskRecord,
)

logger = logging.getLogger(__name__)


class PostgresDocumentParseStore:
    def __init__(self, settings, *, lease_seconds: float = 30):
        self._settings = settings
        self._docs = settings.database.postgres_api_doc_table
        self._projects = settings.database.postgres_project_table
        self._outbox = settings.database.postgres_task_outbox_table
        self._tasks = self._docs + "_parse_tasks"
        self._snapshots = self._docs + "_parse_snapshots"
        self.lease_seconds = lease_seconds
        if lease_seconds <= 0:
            raise ValueError("Document parse lease must be positive")
        for table in [self._docs, self._projects, self._outbox, self._tasks, self._snapshots, self._docs + "_versions"]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table):
                raise ValueError("Invalid document parse table identifier")

    @property
    def tables(self):
        return [self._snapshots, self._tasks]

    async def initialize(self):
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._tasks} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._docs}(id),
                    version_id UUID NOT NULL REFERENCES {self._docs}_versions(id),
                    creation_key TEXT NOT NULL, request_hash TEXT NOT NULL, seed JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed')),
                    phase TEXT NOT NULL DEFAULT 'queued' CHECK(phase IN ('queued','parsing','result_saved','finished')),
                    message TEXT NOT NULL DEFAULT '解析任务已保存，等待 Worker',
                    attempt_no INTEGER NOT NULL DEFAULT 0, attempt_id UUID NULL, worker_id TEXT NULL,
                    lease_token TEXT NULL, lease_expires_at TIMESTAMPTZ NULL,
                    deadline_at TIMESTAMPTZ NULL, checkpoint JSONB NULL, snapshot_id UUID NULL,
                    error_code TEXT NULL, failure_artifact JSONB NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), completed_at TIMESTAMPTZ NULL,
                    UNIQUE(project_id,document_id,creation_key))""")
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._snapshots} (
                    id UUID PRIMARY KEY, task_id UUID NOT NULL UNIQUE REFERENCES {self._tasks}(id),
                    project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._docs}(id),
                    version_id UUID NOT NULL REFERENCES {self._docs}_versions(id),
                    record JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL)""")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._tasks}_document ON {self._tasks}(project_id,document_id,created_at DESC)")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._snapshots}_version ON {self._snapshots}(project_id,document_id,version_id,created_at DESC)")
        await asyncio.to_thread(create)

    @staticmethod
    def request_hash(payload):
        encoded = json.dumps(payload.model_dump(mode="json", exclude={"creation_key"}), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _check_request(row, request_hash):
        if row["request_hash"] != request_hash:
            raise ValueError("同一解析请求身份不能使用不同版本或选项")

    async def submit(self, seed: DocumentParseSeed, *, creation_key: str, request_hash: str, stream: str):
        def insert():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                # Match existing project/document lock order and recheck binding.
                cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR UPDATE", (seed.project_id,))
                project = cur.fetchone()
                cur.execute(f"SELECT * FROM {self._tasks} WHERE project_id=%s AND document_id=%s AND creation_key=%s",
                    (seed.project_id, seed.document_id, creation_key))
                prior = cur.fetchone()
                if prior:
                    self._check_request(prior, request_hash)
                    return self._record(prior)
                if not project or project["status"] != "active":
                    raise ValueError("项目不存在或已归档，不能提交解析任务")
                cur.execute(f"SELECT project_id FROM {self._docs} WHERE id=%s FOR UPDATE", (seed.document_id,))
                doc = cur.fetchone()
                if not doc or str(doc["project_id"]) != seed.project_id:
                    raise ValueError("文档不属于当前项目")
                cur.execute(f"SELECT record FROM {self._docs}_versions WHERE id=%s AND document_id=%s",
                    (seed.version_id, seed.document_id))
                version = cur.fetchone()
                if not version or version["record"]["snapshot"].get("original_asset") != seed.original_asset.model_dump(mode="json"):
                    raise ValueError("原件资产与固定文档版本不一致")
                task_id = str(uuid4())
                cur.execute(f"""INSERT INTO {self._tasks}(id,project_id,document_id,version_id,creation_key,request_hash,seed)
                    VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING *""",
                    (task_id, seed.project_id, seed.document_id, seed.version_id, creation_key, request_hash, seed.model_dump_json()))
                row = cur.fetchone()
                command = DocumentParseCommand(project_id=seed.project_id, task_id=task_id)
                cur.execute(f"INSERT INTO {self._outbox}(event_key,stream,payload) VALUES(%s,%s,%s::jsonb)",
                    (f"document-parsing:{task_id}", stream, command.model_dump_json()))
                return self._record(row)
        saved = await asyncio.to_thread(insert)
        logger.info("document_parse_task_saved", extra={"project_id": saved.project_id,
            "document_id": saved.document_id, "task_id": saved.id, "version_id": saved.version_id})
        return saved

    async def get_by_request(self, project_id, document_id, creation_key, *, request_hash=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._tasks} WHERE project_id=%s AND document_id=%s AND creation_key=%s",
                    (project_id, document_id, creation_key))
                row = cur.fetchone()
            if row and request_hash is not None:
                self._check_request(row, request_hash)
            return self._record(row) if row else None
        return await asyncio.to_thread(read)

    async def get(self, project_id, task_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self._tasks} WHERE project_id=%s AND id=%s", (project_id, task_id))
                row = cur.fetchone()
                return self._record(row) if row else None
        return await asyncio.to_thread(read)

    async def claim(self, project_id, task_id, *, worker_id):
        def update():
            attempt, token = str(uuid4()), uuid4().hex
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""UPDATE {self._tasks} SET status='running',phase=CASE WHEN checkpoint IS NULL THEN 'parsing' ELSE 'result_saved' END,
                    attempt_no=attempt_no+1,attempt_id=%s,worker_id=%s,lease_token=%s,
                    lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
                    deadline_at=COALESCE(deadline_at,clock_timestamp()+
                        (seed->'config'->'payload'->'policy'->>'timeout_seconds')::double precision*INTERVAL '1 second'),
                    updated_at=NOW(),message='Worker 已领取解析任务' WHERE project_id=%s AND id=%s
                    AND (status='queued' OR (status='running' AND lease_expires_at<=clock_timestamp()))
                    RETURNING *,EXTRACT(EPOCH FROM (deadline_at-clock_timestamp())) AS remaining_seconds""",
                    (attempt, worker_id, token, self.lease_seconds, project_id, task_id))
                row = cur.fetchone()
            if not row:
                return None
            return DocumentParseClaim(task=self._record(row), seed=DocumentParseSeed.model_validate(row["seed"]),
                snapshot=DocumentParseSnapshot.model_validate(row["checkpoint"]) if row["checkpoint"] else None,
                attempt_id=attempt, worker_id=worker_id, lease_token=token, remaining_seconds=float(row["remaining_seconds"]))
        claim = await asyncio.to_thread(update)
        logger.info("document_parse_task_claimed" if claim else "document_parse_task_claim_skipped",
            extra={"project_id": project_id, "task_id": task_id, "worker_id": worker_id})
        return claim

    async def heartbeat(self, claim):
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""UPDATE {self._tasks} SET lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second'
                    WHERE id=%s AND project_id=%s AND status='running' AND attempt_id=%s AND worker_id=%s
                    AND lease_token=%s AND lease_expires_at>clock_timestamp()""",
                    (self.lease_seconds, claim.task.id, claim.task.project_id, claim.attempt_id, claim.worker_id, claim.lease_token))
                if cur.rowcount != 1:
                    raise ContinuationLeaseLostError("解析任务租约已失效")
        await asyncio.to_thread(update)

    def _owned(self, cur, claim):
        cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s AND project_id=%s FOR UPDATE", (claim.task.id, claim.task.project_id))
        row = cur.fetchone()
        cur.execute("SELECT clock_timestamp() AS now")
        now = cur.fetchone()["now"]
        if (not row or row["status"] != "running" or row["lease_expires_at"] <= now or
            str(row["attempt_id"]) != claim.attempt_id or row["worker_id"] != claim.worker_id or row["lease_token"] != claim.lease_token):
            logger.warning("document_parse_task_ownership_lost", extra={"task_id": claim.task.id, "attempt_id": claim.attempt_id})
            raise ContinuationLeaseLostError("解析任务当前 attempt 不再拥有写入权")
        return row

    def _fixed_document(self, cur, claim):
        cur.execute(f"SELECT project_id FROM {self._docs} WHERE id=%s FOR SHARE", (claim.task.document_id,))
        doc = cur.fetchone()
        if not doc or str(doc["project_id"]) != claim.task.project_id:
            raise ValueError("文档项目归属已变化，不能发布原项目解析结果")

    async def save_snapshot_checkpoint(self, claim, snapshot: DocumentParseSnapshot):
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._fixed_document(cur, claim)
                row = self._owned(cur, claim)
                fixed = DocumentParseSeed.model_validate(row["seed"])
                if snapshot.seed != fixed or snapshot.task_id != claim.task.id or snapshot.parser != fixed.parser:
                    raise ValueError("解析快照不能替换固定输入或任务身份")
                if row["checkpoint"] and DocumentParseSnapshot.model_validate(row["checkpoint"]) != snapshot:
                    raise ValueError("已保存解析结果检查点不能改写")
                cur.execute(f"UPDATE {self._tasks} SET checkpoint=%s::jsonb,phase='result_saved',updated_at=NOW() WHERE id=%s",
                    (snapshot.model_dump_json(), claim.task.id))
        await asyncio.to_thread(update)
        logger.info("document_parse_checkpoint_saved", extra={"task_id": claim.task.id, "snapshot_id": snapshot.id})

    async def finish(self, claim, *, error_code=None, message="解析结果已保存", failure_artifact=None):
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                if error_code is None:
                    self._fixed_document(cur, claim)
                row = self._owned(cur, claim)
                snapshot = DocumentParseSnapshot.model_validate(row["checkpoint"]) if row["checkpoint"] else None
                if error_code is None and snapshot is None:
                    raise ValueError("成功必须有固定结果")
                if failure_artifact is not None and error_code is None:
                    raise ValueError("失败工件只能关联失败任务")
                if snapshot and error_code is None:
                    cur.execute(f"INSERT INTO {self._snapshots}(id,task_id,project_id,document_id,version_id,record,created_at) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)",
                        (snapshot.id, claim.task.id, snapshot.seed.project_id, snapshot.seed.document_id,
                         snapshot.seed.version_id, snapshot.model_dump_json(), snapshot.created_at))
                cur.execute(f"""UPDATE {self._tasks} SET status=%s,phase='finished',snapshot_id=%s,error_code=%s,message=%s,
                    failure_artifact=%s::jsonb,updated_at=NOW(),completed_at=NOW(),lease_token=NULL,worker_id=NULL,lease_expires_at=NULL
                    WHERE id=%s RETURNING *""", ("failed" if error_code else "completed", snapshot.id if snapshot and error_code is None else None,
                    error_code, message, failure_artifact.model_dump_json() if failure_artifact else None, claim.task.id))
                return self._record(cur.fetchone())
        saved = await asyncio.to_thread(update)
        logger.info("document_parse_task_finished", extra={"task_id": saved.id, "status": saved.status, "snapshot_id": saved.snapshot_id})
        return saved

    async def get_snapshot(self, project_id, document_id, snapshot_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._snapshots} WHERE project_id=%s AND document_id=%s AND id=%s",
                    (project_id, document_id, snapshot_id))
                row = cur.fetchone()
                return DocumentParseSnapshot.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    async def latest_snapshot(self, project_id, document_id, version_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._snapshots} WHERE project_id=%s AND document_id=%s AND version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1",
                    (project_id, document_id, version_id))
                row = cur.fetchone()
                return DocumentParseSnapshot.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    @staticmethod
    def _record(row):
        keys = {name: row[name] for name in DocumentParseTaskRecord.model_fields}
        for name in ["id", "project_id", "document_id", "version_id", "snapshot_id"]:
            keys[name] = str(keys[name]) if keys[name] is not None else None
        return DocumentParseTaskRecord.model_validate(keys)
