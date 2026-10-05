"""Module tasks reuse document leases; case drafts and receipts commit together."""
import asyncio
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_sources import impact_record_hash
from src.application.test_cases.generation_retry import generation_retry_strategy
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_document_operation_store import PostgresDocumentTaskStore
from src.runtime.postgres_requirement_store import PostgresRequirementStore, fixed_requirement_version
from src.schemas.case_generation import IncrementalGenerationModule, fixed_generation_snapshot, generation_hash
from src.schemas.case_generation_tasks import (
    GenerationGroup, GenerationTaskSeed, GenerationTaskRecord, GenerationTaskPage,
    GenerationTaskClaim, GenerationCommand, GenerationModuleCheckpoint, GenerationGroupPage,
    GenerationRetry, GenerationRetryReference, GenerationRetryView, GenerationRetryPage,
)
from src.schemas.case_management import TestCaseVersionRecord

logger = logging.getLogger(__name__)


class PostgresGenerationTaskStore(PostgresDocumentTaskStore):
    _table_suffix = "case_gen"
    _working_phase = "generating"
    _additional_phases = ("invoking", "response_saved")
    _timeout_json_path = "{config,payload,timeout_seconds}"
    _label = "增量生成"
    _log_prefix = "case_generation"
    _event_prefix = "case-generation"
    _seed_type, _task_type = GenerationTaskSeed, GenerationTaskRecord
    _claim_type, _command_type, _task_page_type = GenerationTaskClaim, GenerationCommand, GenerationTaskPage
    _extra_input_columns = ("group_id", "module_id")
    _uuid_record_fields = ("id", "project_id", "document_id", "version_id", "group_id", "module_id")

    def __init__(self, settings, case_store, *, lease_seconds=30):
        super().__init__(settings, lease_seconds=lease_seconds)
        self._groups = self._docs + "_case_gen_groups"
        self._retries = self._docs + "_case_gen_retries"
        self._cases = case_store
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table) for table in [self._groups, self._retries]):
            raise ValueError("Invalid generation group table identifier")

    @property
    def tables(self):
        return [self._retries, self._tasks, self._groups]

    async def initialize(self):
        def groups():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._groups} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._docs}(id), creation_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL, record JSONB NOT NULL, UNIQUE(project_id,document_id,creation_key))""")
        await asyncio.to_thread(groups)
        await super().initialize()
        def retries():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._retries} (
                    id UUID PRIMARY KEY, task_id UUID NOT NULL UNIQUE REFERENCES {self._tasks}(id),
                    parent_task_id UUID NOT NULL REFERENCES {self._tasks}(id),
                    project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._docs}(id), creation_key TEXT NOT NULL,
                    record JSONB NOT NULL, UNIQUE(project_id,document_id,creation_key))""")
                cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{self._retries}_parent ON {self._retries}(parent_task_id,id)")
        await asyncio.to_thread(retries)

    def _input_schema(self):
        return f"group_id UUID NOT NULL REFERENCES {self._groups}(id), module_id UUID NOT NULL,"

    def _input_values(self, seed):
        return seed.group_id, seed.module_id

    def _claim_phase_sql(self):
        return "CASE WHEN checkpoint IS NULL THEN 'generating' ELSE checkpoint->>'phase' END"

    def _validate_seed(self, cur, seed):
        cur.execute(f"SELECT record FROM {self._groups} WHERE id=%s AND project_id=%s AND document_id=%s", (seed.group_id, seed.project_id, seed.document_id))
        row = cur.fetchone()
        group = GenerationGroup.model_validate(row["record"]) if row else None
        if (group is None or group.sha256 != seed.group_sha256
            or group.version_id != seed.version_id or seed.module_id not in group.module_ids):
            raise ValueError("模块seed与固定生成组不同")
        if seed.retry is None:
            if group.config != seed.config: raise ValueError("原模块不能替换组策略")
            return
        cur.execute(f"SELECT record FROM {self._retries} WHERE id=%s AND project_id=%s AND document_id=%s",
            (seed.retry.id, seed.project_id, seed.document_id))
        saved = cur.fetchone()
        retry = GenerationRetry.model_validate(saved["record"]) if saved else None
        if (retry is None or retry.sha256 != seed.retry.sha256 or retry.group_id != group.id
            or retry.group_sha256 != group.sha256 or retry.scope_sha256 != group.scope_sha256
            or retry.module_id != seed.module_id or retry.module_sha256 != seed.module_sha256
            or retry.config != seed.config):
            raise ValueError("重试seed与原固定关系不同")
        cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s AND project_id=%s AND document_id=%s",
            (retry.parent_task_id, seed.project_id, seed.document_id))
        parent = cur.fetchone()
        if (not parent or generation_hash(self._record(parent).model_dump(mode="json")) != retry.parent_task_sha256
            or generation_retry_strategy(self._record(parent)) != retry.strategy):
            raise ValueError("重试来源的原失败或检查点不同")
        if retry.response != (self._record(parent).checkpoint.response if retry.strategy=="saved_response" else None):
            raise ValueError("重试关系不能替换原持久响应")
        return retry

    def _owned(self, cur, claim):
        row = super()._owned(cur, claim)
        if (GenerationTaskSeed.model_validate(row["seed"]) != claim.seed
            or str(row["group_id"]) != claim.seed.group_id or str(row["module_id"]) != claim.seed.module_id):
            raise ValueError("模块领取不能替换原seed/组/模块身份")
        if claim.seed.retry:
            retry = self._validate_seed(cur, claim.seed)
            if retry.task_id != claim.task.id: raise ValueError("重试引用不属于当前任务")
            if retry.strategy=="saved_response" and GenerationModuleCheckpoint.model_validate(row["checkpoint"]).response != retry.response:
                raise ValueError("重试任务不能替换复用的原响应")
        return row

    @staticmethod
    def _require_before_deadline(cur, row):
        cur.execute("SELECT clock_timestamp() AS now")
        if row["deadline_at"] <= cur.fetchone()["now"]:
            raise TimeoutError("原模块总期限已到，不能发送或提交新草稿")

    async def remaining_seconds(self, claim):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                row = self._owned(cur, claim)
                cur.execute("SELECT clock_timestamp() AS now")
                return (row["deadline_at"]-cur.fetchone()["now"]).total_seconds()
        return await asyncio.to_thread(read)

    async def get_group(self, project_id, document_id, *, group_id=None, creation_key=None, request_hash=None):
        if (group_id is None) == (creation_key is None):
            raise ValueError("请明确一个生成组身份或原请求编号")
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record,request_hash FROM {self._groups} WHERE project_id=%s AND document_id=%s "
                    "AND (%s::uuid IS NULL OR id=%s::uuid) AND (%s::text IS NULL OR creation_key=%s)",
                    (project_id, document_id, group_id, group_id, creation_key, creation_key))
                row = cur.fetchone()
                if row and request_hash is not None and row["request_hash"] != request_hash:
                    raise ValueError("同一增量生成请求不能替换范围")
                return GenerationGroup.model_validate(row["record"]) if row else None
        return await asyncio.to_thread(read)

    async def submit_group(self, scope, scope_object, config, *, creation_key, request_hash, stream):
        return await asyncio.to_thread(self._submit_group, scope, scope_object, config, creation_key, request_hash, stream)

    async def list_groups(self, project_id, document_id, *, limit=20, offset=0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._groups} WHERE project_id=%s AND document_id=%s "
                    "ORDER BY (record->>'created_at')::timestamptz DESC,id DESC LIMIT %s OFFSET %s",
                    (project_id, document_id, limit+1, offset))
                rows = cur.fetchall()
            return GenerationGroupPage(items=[GenerationGroup.model_validate(row["record"]) for row in rows[:limit]],
                has_more=len(rows)>limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    def _submit_group(self, scope, scope_object, config, creation_key, request_hash, stream):
        # No upstream counterpart (checked: document operation submit, import commit,
        # immutable requirement reviews): all modules share one frozen scope and atomic submission.
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR UPDATE", (scope.project_id,))
            project = cur.fetchone()
            cur.execute(f"SELECT record,request_hash FROM {self._groups} WHERE project_id=%s AND document_id=%s AND creation_key=%s",
                (scope.project_id, scope.document_id, creation_key))
            prior = cur.fetchone()
            if prior:
                if prior["request_hash"] != request_hash: raise ValueError("同一增量生成请求不能替换范围")
                return GenerationGroup.model_validate(prior["record"])
            if not project or project["status"] != "active": raise ValueError("已归档项目不能提交增量生成")
            cur.execute(f"SELECT project_id FROM {self._docs} WHERE id=%s FOR UPDATE", (scope.document_id,))
            doc = cur.fetchone()
            if not doc or str(doc["project_id"]) != scope.project_id: raise ValueError("文档不属于当前项目")
            cur.execute(f"SELECT v.record FROM {self._docs}_requirement_versions v JOIN {self._docs}_requirements r "
                "ON r.id=v.requirement_id WHERE v.id=%s AND r.project_id=%s AND r.document_id=%s FOR SHARE OF r,v",
                (scope.requirement.id, scope.project_id, scope.document_id))
            requirement = cur.fetchone()
            if not requirement or fixed_requirement_version(requirement["record"]) != scope.requirement:
                raise ValueError("提交的确认需求版本与原记录不同")
            cur.execute(f"SELECT record FROM {self._docs}_requirement_reviews WHERE id=%s AND project_id=%s AND document_id=%s FOR SHARE",
                (scope.review.id, scope.project_id, scope.document_id))
            review = cur.fetchone()
            if not review or PostgresRequirementStore._review(review) != scope.review:
                raise ValueError("提交的原接受决定不同")
            cur.execute(f"SELECT version_id,input_checkpoint FROM {self._docs}_impact_tasks WHERE id=%s AND project_id=%s AND document_id=%s FOR SHARE",
                (scope.review.source.task_id, scope.project_id, scope.document_id))
            basis = cur.fetchone()
            if not basis or basis["input_checkpoint"] != scope.input_checkpoint.model_dump(mode="json"):
                raise ValueError("提交的固定影响输入不同")
            for module in scope.modules:
                if not module.target: continue
                cur.execute(f"SELECT c.project_id,v.record FROM {self._cases._version_table} v JOIN {self._cases._case_table} c ON c.id=v.case_id "
                    "WHERE v.id=%s AND c.id=%s FOR SHARE OF c,v", (module.target.version_id, module.target.case.id))
                row = cur.fetchone()
                if not row or str(row["project_id"]) != scope.project_id or impact_record_hash(TestCaseVersionRecord.model_validate(row["record"])) != module.target.version_record_sha256:
                    raise ValueError("提交的固定用例版本或项目不同")
            group = fixed_generation_snapshot(GenerationGroup, id=str(uuid4()), project_id=scope.project_id,
                document_id=scope.document_id, version_id=str(basis["version_id"]), creation_key=creation_key,
                request_hash=request_hash, scope=scope_object, scope_sha256=scope.sha256,
                module_ids=[item.id for item in scope.modules], config=config, created_at=datetime.now(timezone.utc))
            cur.execute(f"INSERT INTO {self._groups}(id,project_id,document_id,creation_key,request_hash,record) VALUES(%s,%s,%s,%s,%s,%s::jsonb)",
                (group.id, group.project_id, group.document_id, creation_key, request_hash, group.model_dump_json()))
            for module in scope.modules:
                seed = GenerationTaskSeed(project_id=group.project_id, document_id=group.document_id,
                    version_id=group.version_id, group_id=group.id, group_sha256=group.sha256,
                    module_id=module.id, module_sha256=module.sha256, config=config)
                self.insert_task_in_transaction(cur, seed, creation_key=f"{group.id}:{module.id}",
                    request_hash=module.sha256, stream=stream)
        logger.info("case_generation_group_saved", extra={"group_id": group.id, "module_count": len(group.module_ids)})
        return group

    async def list_modules(self, project_id, group_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT t.* FROM {self._tasks} t WHERE t.project_id=%s AND t.group_id=%s "
                    f"AND NOT EXISTS (SELECT 1 FROM {self._retries} r WHERE r.task_id=t.id) ORDER BY t.created_at,t.id", (project_id, group_id))
                return [self._record(row) for row in cur.fetchall()]
        return await asyncio.to_thread(read)

    async def get_retry(self, project_id, document_id, *, retry_id=None, creation_key=None, parent_task_id=None):
        if (retry_id is None) == (creation_key is None): raise ValueError("请明确重试身份或原请求编号")
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._retries} WHERE project_id=%s AND document_id=%s "
                    "AND (%s::uuid IS NULL OR id=%s::uuid) AND (%s::text IS NULL OR creation_key=%s)",
                    (project_id, document_id, retry_id, retry_id, creation_key, creation_key))
                row = cur.fetchone()
                value = GenerationRetry.model_validate(row["record"]) if row else None
                if value and parent_task_id and value.parent_task_id != parent_task_id:
                    raise ValueError("同一重试请求不能替换原失败任务")
                return value
        return await asyncio.to_thread(read)

    async def list_retries(self, project_id, document_id, parent_task_id, *, limit=20, offset=0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT r.record AS retry_record,t.* FROM {self._retries} r JOIN {self._tasks} t ON t.id=r.task_id "
                    "WHERE r.project_id=%s AND r.document_id=%s AND r.parent_task_id=%s "
                    "ORDER BY t.created_at DESC,t.id DESC LIMIT %s OFFSET %s", (project_id, document_id, parent_task_id, limit+1, offset))
                rows = cur.fetchall()
                return GenerationRetryPage(items=[GenerationRetryView(retry=GenerationRetry.model_validate(row["retry_record"]),
                    task=self._record(row).model_dump(mode="json")) for row in rows[:limit]], has_more=len(rows)>limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    async def submit_retry(self, parent, group, module, config, *, creation_key, stream):
        return await asyncio.to_thread(self._submit_retry, parent, group, module, config, creation_key, stream)

    def _submit_retry(self, parent, group, module, config, creation_key, stream):
        # No upstream counterpart (checked: original group submit, Agent regression,
        # shared task/Outbox): preserve the parent and original hashes with explicit lineage.
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR UPDATE", (parent.project_id,))
            project = cur.fetchone()
            cur.execute(f"SELECT record FROM {self._retries} WHERE project_id=%s AND document_id=%s AND creation_key=%s",
                (parent.project_id, parent.document_id, creation_key))
            prior = cur.fetchone()
            if prior:
                retry = GenerationRetry.model_validate(prior["record"])
                if retry.parent_task_id != parent.id: raise ValueError("同一重试请求不能替换原失败任务")
                return retry
            if not project or project["status"] != "active": raise ValueError("归档项目不能创建新重试任务")
            cur.execute(f"SELECT project_id FROM {self._docs} WHERE id=%s FOR UPDATE", (parent.document_id,))
            doc = cur.fetchone()
            if not doc or str(doc["project_id"]) != parent.project_id: raise ValueError("重试文档不属于当前项目")
            cur.execute(f"SELECT * FROM {self._tasks} WHERE id=%s AND project_id=%s AND document_id=%s FOR UPDATE",
                (parent.id, parent.project_id, parent.document_id))
            row = cur.fetchone()
            if not row or self._record(row) != parent: raise ValueError("重试来源失败任务已变化")
            seed = GenerationTaskSeed.model_validate(row["seed"])
            self._validate_seed(cur, seed)
            if (seed.group_sha256 != group.sha256 or seed.group_id != group.id
                or seed.module_id != module.id or seed.module_sha256 != module.sha256):
                raise ValueError("重试来源与固定组/模块不同")
            strategy = generation_retry_strategy(parent)
            if strategy == "frozen_request" and module.generation and config.payload.transport_options != module.generation.authoring.transport_options:
                raise ValueError("重试策略不能替换原固定SDK选项")
            retry_id = str(uuid4())
            child_seed = seed.model_copy(update={"config":config})
            task = self.insert_task_in_transaction(cur, child_seed, creation_key=f"retry:{retry_id}",
                request_hash=module.sha256, stream=stream)
            retry = fixed_generation_snapshot(GenerationRetry, id=retry_id, task_id=task.id,
                parent_task_id=parent.id, parent_task_sha256=generation_hash(parent.model_dump(mode="json")),
                project_id=parent.project_id, document_id=parent.document_id, group_id=group.id,
                group_sha256=group.sha256, scope_sha256=group.scope_sha256, module_id=module.id,
                module_sha256=module.sha256, creation_key=creation_key, config=config, strategy=strategy,
                response=parent.checkpoint.response if strategy=="saved_response" else None, created_at=datetime.now(timezone.utc))
            child_seed = child_seed.model_copy(update={"retry":GenerationRetryReference(id=retry.id,sha256=retry.sha256)})
            checkpoint = parent.checkpoint.model_copy(update={"receipt":None}) if strategy=="saved_response" else None
            cur.execute(f"UPDATE {self._tasks} SET seed=%s::jsonb,checkpoint=%s::jsonb WHERE id=%s",
                (child_seed.model_dump_json(),checkpoint.model_dump_json() if checkpoint else None,task.id))
            cur.execute(f"INSERT INTO {self._retries}(id,task_id,parent_task_id,project_id,document_id,creation_key,record) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (retry.id,retry.task_id,parent.id,parent.project_id,parent.document_id,creation_key,retry.model_dump_json()))
        logger.info("case_generation_retry_saved",extra={"retry_id":retry.id,"parent_task_id":parent.id,"task_id":task.id,"strategy":strategy})
        return retry

    async def object_referenced(self, uri):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT 1 FROM {self._groups} WHERE record->'scope'->>'storage_uri'=%s UNION ALL "
                    f"SELECT 1 FROM {self._tasks} WHERE checkpoint->'response'->>'storage_uri'=%s LIMIT 1", (uri, uri))
                return cur.fetchone() is not None
        return await asyncio.to_thread(read)

    async def begin_invocation(self, claim, budget):
        checkpoint = GenerationModuleCheckpoint(phase="invoking", invocation_attempt_id=claim.attempt_id, budget=budget)
        def save():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                row = self._owned(cur, claim)
                self._validate_seed(cur, claim.seed)
                if row["checkpoint"] is not None: raise ValueError("已有模块调用意图，不能再次发送")
                self._require_before_deadline(cur, row)
                cur.execute(f"UPDATE {self._tasks} SET checkpoint=%s::jsonb,phase='invoking',updated_at=NOW() WHERE id=%s", (checkpoint.model_dump_json(), claim.task.id))
        await asyncio.to_thread(save)
        return checkpoint

    async def save_response(self, claim, response):
        return await asyncio.to_thread(self._save_response, claim, response)

    def _save_response(self, claim, response):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            row = self._owned(cur, claim)
            checkpoint = GenerationModuleCheckpoint.model_validate(row["checkpoint"])
            if checkpoint.response is not None:
                if checkpoint.response != response: raise ValueError("已存原响应不能改写")
                return checkpoint
            if checkpoint.phase != "invoking" or checkpoint.invocation_attempt_id != claim.attempt_id:
                raise ValueError("响应必须来自当前已保存调用")
            checkpoint = checkpoint.model_copy(update={"phase": "response_saved", "response": response})
            cur.execute(f"UPDATE {self._tasks} SET checkpoint=%s::jsonb,phase='response_saved',updated_at=NOW() WHERE id=%s", (checkpoint.model_dump_json(), claim.task.id))
            return checkpoint

    async def commit_module(self, claim, module, entries, *, scope_sha256):
        return await asyncio.to_thread(self._commit_module, claim, module, entries, scope_sha256)

    def _commit_module(self, claim, module, entries, scope_sha256):
        module = IncrementalGenerationModule.model_validate_json(module.model_dump_json())
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR SHARE", (claim.task.project_id,))
            project = cur.fetchone()
            if not project or project["status"] != "active": raise ValueError("归档后不能提交新草稿")
            self._fixed_document(cur, claim)
            row = self._owned(cur, claim)
            self._validate_seed(cur, claim.seed)
            self._require_before_deadline(cur, row)
            if module.id != claim.seed.module_id or module.sha256 != claim.seed.module_sha256:
                raise ValueError("草稿提交模块与固定seed不同")
            cur.execute(f"SELECT record FROM {self._groups} WHERE id=%s", (claim.seed.group_id,))
            group = GenerationGroup.model_validate(cur.fetchone()["record"])
            if scope_sha256 != group.scope_sha256: raise ValueError("草稿范围与原组不同")
            checkpoint = GenerationModuleCheckpoint.model_validate(row["checkpoint"]) if row["checkpoint"] else None
            if module.kind != "retirement_candidate" and (checkpoint is None or checkpoint.phase != "response_saved"):
                raise ValueError("草稿必须从持久原响应产生")
            if module.kind == "retirement_candidate" and entries: raise ValueError("停用候选不能写草稿")
            versions = []
            if module.kind == "update":
                if len(entries) != 1 or entries[0][0].id != module.target.case.id: raise ValueError("更新必须使用原case身份")
                _, version = self._cases.append_version_in_transaction(cur, module.target.case.id, entries[0][1],
                    expected_project_id=claim.task.project_id, expected_latest_version=module.expected_latest_version)
                versions.append(version)
            elif module.kind == "create":
                if not entries or any(case.project_id != claim.task.project_id for case, _ in entries): raise ValueError("新草稿项目不同或为空")
                self._cases.insert_drafts_in_transaction(cur, entries)
                versions = [version for _, version in entries]
            receipt = {"module_id": module.id, "kind": module.kind, "scope_sha256": scope_sha256,
                "response": checkpoint.response.model_dump(mode="json") if checkpoint and checkpoint.response else None,
                "versions": [{"case_id": v.case_id, "version_id": v.id, "version": v.version, "content_hash": v.content_hash} for v in versions],
                "retirement_target": module.target.model_dump(mode="json") if module.kind == "retirement_candidate" else None}
            checkpoint = (checkpoint or GenerationModuleCheckpoint(phase="result_saved")).model_copy(update={"phase": "result_saved", "receipt": receipt})
            # Lease and original deadline both fence the entire draft/receipt
            # transaction after any case row lock wait.
            self._require_before_deadline(cur, self._owned(cur, claim))
            cur.execute(f"UPDATE {self._tasks} SET checkpoint=%s::jsonb,status='completed',phase='finished',message='草稿或停用候选已保存，待评审',completed_at=NOW(),updated_at=NOW(),lease_token=NULL,worker_id=NULL,lease_expires_at=NULL WHERE id=%s RETURNING *",
                (checkpoint.model_dump_json(), claim.task.id))
            saved = self._record(cur.fetchone())
        logger.info("case_generation_module_committed", extra={"task_id": saved.id, "kind": module.kind, "draft_count": len(versions)})
        return saved
