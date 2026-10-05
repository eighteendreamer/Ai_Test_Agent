"""Internal shared SQL lifecycle for fixed document operations.

Adapted directly from PostgresDocumentParseStore; domain stores retain their DTOs,
tables, source checks, phases and log event names. No document body is stored here.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
import hashlib
import json
import logging
import re
from uuid import uuid4

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.store import ContinuationLeaseLostError

logger = logging.getLogger(__name__)


class PostgresDocumentTaskStore(ABC):
    _extra_input_columns = ()
    _uuid_record_fields = ("id", "project_id", "document_id", "version_id", "snapshot_id")
    _additional_phases = ()
    _timeout_json_path = "{config,payload,policy,timeout_seconds}"

    def __init__(self, settings, *, lease_seconds: float = 30):
        self._settings = settings
        self._docs = settings.database.postgres_api_doc_table
        self._projects = settings.database.postgres_project_table
        self._outbox = settings.database.postgres_task_outbox_table
        self._tasks = self._docs + "_" + self._table_suffix + "_tasks"
        self._snapshots = self._docs + "_" + self._table_suffix + "_snapshots"
        self.lease_seconds = lease_seconds
        if lease_seconds <= 0:
            raise ValueError("Document operation lease must be positive")
        for table in [self._docs, self._projects, self._outbox, self._tasks, self._snapshots, self._docs + "_versions"]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table):
                raise ValueError("Invalid document operation table identifier")
        for column in self._extra_input_columns:
            if not re.fullmatch(r"[a-z_]+", column):
                raise ValueError("Invalid fixed document input column")

    def _input_schema(self):
        return ""

    def _input_values(self, seed):
        return ()

    def _claim_phase_sql(self):
        return f"CASE WHEN checkpoint IS NULL THEN '{self._working_phase}' ELSE 'result_saved' END"

    def _claim_values(self, row):
        return {}

    def _initialize_results(self, cur):
        """Tasks without a final-result store need no additional tables."""

    @property
    def tables(self):
        return [self._tasks]

    async def initialize(self):
        phases = ",".join(f"'{phase}'" for phase in ("queued", self._working_phase, *self._additional_phases, "result_saved", "finished"))
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._tasks} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._docs}(id),
                    version_id UUID NOT NULL REFERENCES {self._docs}_versions(id),
                    {self._input_schema()}
                    creation_key TEXT NOT NULL, request_hash TEXT NOT NULL, seed JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed')),
                    phase TEXT NOT NULL DEFAULT 'queued' CHECK(phase IN ({phases})),
                    message TEXT NOT NULL DEFAULT '{self._label}任务已保存，等待 Worker',
                    attempt_no INTEGER NOT NULL DEFAULT 0, attempt_id UUID NULL, worker_id TEXT NULL,
                    lease_token TEXT NULL, lease_expires_at TIMESTAMPTZ NULL,
                    deadline_at TIMESTAMPTZ NULL, checkpoint JSONB NULL, snapshot_id UUID NULL,
                    error_code TEXT NULL, failure_artifact JSONB NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), completed_at TIMESTAMPTZ NULL,
                    UNIQUE(project_id,document_id,creation_key))""")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._tasks}_document ON {self._tasks}(project_id,document_id,created_at DESC)")
                self._initialize_results(cur)
        await asyncio.to_thread(create)

    @staticmethod
    def request_hash(payload):
        encoded = json.dumps(payload.model_dump(mode="json", exclude={"creation_key"}), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()

    def _check_request(self, row, request_hash):
        if row["request_hash"] != request_hash:
            raise ValueError(f"同一{self._label}请求身份不能使用不同输入或选项")

    @abstractmethod
    def _validate_seed(self, cur, seed):
        """Recheck operation-specific fixed inputs within the submission transaction."""

    async def submit(self, seed, *, creation_key: str, request_hash: str, stream: str):
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
                    raise ValueError(f"项目不存在或已归档，不能提交{self._label}任务")
                cur.execute(f"SELECT project_id FROM {self._docs} WHERE id=%s FOR UPDATE", (seed.document_id,))
                doc = cur.fetchone()
                if not doc or str(doc["project_id"]) != seed.project_id:
                    raise ValueError("文档不属于当前项目")
                self._validate_seed(cur, seed)
                return self.insert_task_in_transaction(cur, seed, creation_key=creation_key,
                    request_hash=request_hash, stream=stream)
        saved = await asyncio.to_thread(insert)
        logger.info(f"{self._log_prefix}_task_saved", extra={"project_id": saved.project_id,
            "document_id": saved.document_id, "task_id": saved.id, "version_id": saved.version_id})
        return saved

    def insert_task_in_transaction(self, cur, seed, *, creation_key, request_hash, stream):
        """Caller has validated and locked scope; task and Outbox share its transaction."""
        task_id = str(uuid4())
        columns = "".join("," + name for name in self._extra_input_columns)
        placeholders = ",%s" * len(self._extra_input_columns)
        values = self._input_values(seed)
        if len(values) != len(self._extra_input_columns):
            raise ValueError("固定输入列与参数数量不一致")
        cur.execute(f"""INSERT INTO {self._tasks}(id,project_id,document_id,version_id,creation_key,request_hash,seed,created_at{columns})
            VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,clock_timestamp(){placeholders}) RETURNING *""",
            (task_id, seed.project_id, seed.document_id, seed.version_id, creation_key, request_hash, seed.model_dump_json(), *values))
        row = cur.fetchone()
        command = self._command_type(project_id=seed.project_id, task_id=task_id)
        cur.execute(f"INSERT INTO {self._outbox}(event_key,stream,payload) VALUES(%s,%s,%s::jsonb)",
            (f"{self._event_prefix}:{task_id}", stream, command.model_dump_json()))
        return self._record(row)

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

    async def list_tasks(self, project_id, document_id, *, version_id=None, limit=20, offset=0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""SELECT * FROM {self._tasks} WHERE project_id=%s AND document_id=%s
                    AND (%s::uuid IS NULL OR version_id=%s::uuid)
                    ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s""",
                    (project_id, document_id, version_id, version_id, limit + 1, offset))
                rows = cur.fetchall()
            return self._task_page_type(items=[self._record(row) for row in rows[:limit]],
                has_more=len(rows) > limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    async def claim(self, project_id, task_id, *, worker_id):
        def update():
            attempt, token = str(uuid4()), uuid4().hex
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""UPDATE {self._tasks} SET status='running',phase={self._claim_phase_sql()},
                    attempt_no=attempt_no+1,attempt_id=%s,worker_id=%s,lease_token=%s,
                    lease_expires_at=clock_timestamp()+%s*INTERVAL '1 second',
                    deadline_at=COALESCE(deadline_at,clock_timestamp()+
                        (seed#>>'{self._timeout_json_path}')::double precision*INTERVAL '1 second'),
                    updated_at=NOW(),message='Worker 已领取{self._label}任务' WHERE project_id=%s AND id=%s
                    AND (status='queued' OR (status='running' AND lease_expires_at<=clock_timestamp()))
                    RETURNING *,EXTRACT(EPOCH FROM (deadline_at-clock_timestamp())) AS remaining_seconds""",
                    (attempt, worker_id, token, self.lease_seconds, project_id, task_id))
                row = cur.fetchone()
            if not row:
                return None
            return self._claim_type(task=self._record(row), seed=self._seed_type.model_validate(row["seed"]),
                attempt_id=attempt, worker_id=worker_id, lease_token=token, remaining_seconds=float(row["remaining_seconds"]),
                **self._claim_values(row))
        claim = await asyncio.to_thread(update)
        logger.info(f"{self._log_prefix}_task_claimed" if claim else f"{self._log_prefix}_task_claim_skipped",
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
                    raise ContinuationLeaseLostError(f"{self._label}任务租约已失效")
        await asyncio.to_thread(update)

    def _owned(self, cur, claim):
        cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s AND project_id=%s FOR UPDATE", (claim.task.id, claim.task.project_id))
        row = cur.fetchone()
        cur.execute("SELECT clock_timestamp() AS now")
        now = cur.fetchone()["now"]
        if (not row or row["status"] != "running" or row["lease_expires_at"] <= now or
            str(row["attempt_id"]) != claim.attempt_id or row["worker_id"] != claim.worker_id or row["lease_token"] != claim.lease_token):
            logger.warning(f"{self._log_prefix}_task_ownership_lost", extra={"task_id": claim.task.id, "attempt_id": claim.attempt_id})
            raise ContinuationLeaseLostError(f"{self._label}任务当前 attempt 不再拥有写入权")
        return row

    def _fixed_document(self, cur, claim):
        cur.execute(f"SELECT project_id FROM {self._docs} WHERE id=%s FOR SHARE", (claim.task.document_id,))
        doc = cur.fetchone()
        if not doc or str(doc["project_id"]) != claim.task.project_id:
            raise ValueError(f"文档项目归属已变化，不能发布原项目{self._label}结果")

    def _record(self, row):
        keys = {name: row[name] for name in self._task_type.model_fields}
        for name in self._uuid_record_fields:
            keys[name] = str(keys[name]) if keys[name] is not None else None
        return self._task_type.model_validate(keys)

    async def finish_failed(self, claim, *, error_code, message):
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._owned(cur, claim)
                cur.execute(f"""UPDATE {self._tasks} SET status='failed',phase='finished',error_code=%s,message=%s,
                    updated_at=NOW(),completed_at=NOW(),lease_token=NULL,worker_id=NULL,lease_expires_at=NULL
                    WHERE id=%s RETURNING *""", (error_code, message, claim.task.id))
                return self._record(cur.fetchone())
        saved = await asyncio.to_thread(update)
        logger.info(f"{self._log_prefix}_task_finished", extra={"task_id": saved.id, "status": saved.status})
        return saved


class PostgresDocumentOperationStore(PostgresDocumentTaskStore):
    """Single final snapshot over the shared document task lifecycle."""

    @property
    def tables(self):
        return [self._snapshots, self._tasks]

    def _claim_values(self, row):
        return {"snapshot": self._snapshot_type.model_validate(row["checkpoint"]) if row["checkpoint"] else None}

    def _initialize_results(self, cur):
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._snapshots} (
            id UUID PRIMARY KEY, task_id UUID NOT NULL UNIQUE REFERENCES {self._tasks}(id),
            project_id UUID NOT NULL REFERENCES {self._projects}(id),
            document_id UUID NOT NULL REFERENCES {self._docs}(id),
            version_id UUID NOT NULL REFERENCES {self._docs}_versions(id),
            record JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL)""")
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._snapshots}_version ON {self._snapshots}(project_id,document_id,version_id,created_at DESC)")

    @abstractmethod
    def _validate_snapshot(self, snapshot, seed):
        """Validate operation-specific output identity before saving a checkpoint."""

    def validate_snapshot_metadata(self, snapshot, seed):
        if snapshot.seed != seed:
            raise ValueError(f"{self._label}结果不能替换固定输入")
        self._validate_snapshot(snapshot, seed)

    async def list_snapshots(self, project_id, document_id, *, version_id=None, limit=20, offset=0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""SELECT s.record FROM {self._snapshots} s JOIN {self._tasks} t ON t.id=s.task_id
                    WHERE s.project_id=%s AND s.document_id=%s AND (%s::uuid IS NULL OR s.version_id=%s::uuid)
                    ORDER BY t.created_at DESC,t.id DESC LIMIT %s OFFSET %s""",
                    (project_id, document_id, version_id, version_id, limit + 1, offset))
                rows = cur.fetchall()
            return self._snapshot_page_type(items=[self._snapshot_type.model_validate(row["record"]) for row in rows[:limit]],
                has_more=len(rows) > limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    async def save_snapshot_checkpoint(self, claim, snapshot):
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                self._fixed_document(cur, claim)
                row = self._owned(cur, claim)
                fixed = self._seed_type.model_validate(row["seed"])
                if snapshot.seed != fixed or snapshot.task_id != claim.task.id:
                    raise ValueError(f"{self._label}快照不能替换固定输入或任务身份")
                self.validate_snapshot_metadata(snapshot, fixed)
                if row["checkpoint"] and self._snapshot_type.model_validate(row["checkpoint"]) != snapshot:
                    raise ValueError(f"已保存{self._label}结果检查点不能改写")
                cur.execute(f"UPDATE {self._tasks} SET checkpoint=%s::jsonb,phase='result_saved',updated_at=NOW() WHERE id=%s",
                    (snapshot.model_dump_json(), claim.task.id))
        await asyncio.to_thread(update)
        logger.info(f"{self._log_prefix}_checkpoint_saved", extra={"task_id": claim.task.id, "snapshot_id": snapshot.id})

    async def finish(self, claim, *, error_code=None, message=None, failure_artifact=None):
        message = message if message is not None else f"{self._label}结果已保存"
        def update():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                if error_code is None:
                    self._fixed_document(cur, claim)
                row = self._owned(cur, claim)
                snapshot = self._snapshot_type.model_validate(row["checkpoint"]) if row["checkpoint"] else None
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
        logger.info(f"{self._log_prefix}_task_finished", extra={"task_id": saved.id, "status": saved.status, "snapshot_id": saved.snapshot_id})
        return saved

    async def get_snapshot(self, project_id, document_id, snapshot_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._snapshots} WHERE project_id=%s AND document_id=%s AND id=%s",
                    (project_id, document_id, snapshot_id))
                row = cur.fetchone()
                return self._snapshot_type.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    async def latest_snapshot(self, project_id, document_id, version_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                # Select by database submission order, independent of Worker clocks or completion order.
                cur.execute(f"""SELECT s.record FROM {self._snapshots} s JOIN {self._tasks} t ON t.id=s.task_id
                    WHERE s.project_id=%s AND s.document_id=%s AND s.version_id=%s
                    ORDER BY t.created_at DESC,t.id DESC LIMIT 1""",
                    (project_id, document_id, version_id))
                row = cur.fetchone()
                return self._snapshot_type.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)
