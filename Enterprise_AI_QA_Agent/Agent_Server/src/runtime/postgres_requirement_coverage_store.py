"""Append-only explicit coverage of fixed requirement and case versions."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_sources import impact_record_hash
from src.application.test_cases.version_content import version_content_hash
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_store import fixed_requirement_version, requirement_hash
from src.schemas.case_management import TestCaseRecord, TestCaseVersionRecord
from src.schemas.requirements import (
    RequirementCoverageGeneration, RequirementCoverageLink, RequirementCoveragePage,
    RequirementCoverageReview,
)


def coverage_request_hash(document_id, requirement_id, version_id, request):
    return requirement_hash({"document_id": document_id, "requirement_id": requirement_id,
        "requirement_version_id": version_id,
        "request": request.model_dump(mode="json", exclude={"request_id"})})


def fixed_coverage(value):
    result = RequirementCoverageReview.model_validate(value)
    if requirement_hash(result.model_dump(mode="json", exclude={"sha256"})) != result.sha256:
        raise ValueError("覆盖确认正文与固定哈希不同")
    if coverage_request_hash(result.document_id, result.requirement_id, result.requirement_version_id,
        result.request) != result.request_sha256:
        raise ValueError("覆盖确认请求与固定哈希不同")
    if result.request.expected_coverage_id != result.previous_coverage_id or len(result.request.case_links) != len(result.case_links):
        raise ValueError("覆盖确认关联与原请求不同")
    for given, link in zip(result.request.case_links, result.case_links):
        if (given.case_version_id != link.case_version_id or given.content_hash != link.content_hash or
            given.point_keys != link.point_keys or given.generation_task_id != (link.generation.task_id if link.generation else None)):
            raise ValueError("覆盖确认关联与原请求不同")
        if link.review_status != (link.review.status if link.review else "unavailable"):
            raise ValueError("覆盖确认评审状态与固定记录不同")
        if link.review and link.review.version_id != link.case_version_id:
            raise ValueError("覆盖确认评审版本不同")
    return result


class PostgresRequirementCoverageStore:
    def __init__(self, requirements):
        self._requirements = requirements
        self._settings = requirements._settings
        self.table = requirements._docs + "_requirement_coverage"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", self.table):
            raise ValueError("Invalid requirement coverage table identifier")

    async def initialize(self):
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._requirements._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._requirements._docs}(id),
                    requirement_id UUID NOT NULL REFERENCES {self._requirements._requirements}(id),
                    requirement_version_id UUID NOT NULL REFERENCES {self._requirements._versions}(id),
                    request_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
                    created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL,
                    UNIQUE(project_id,request_id), UNIQUE(requirement_version_id,revision))""")
        await asyncio.to_thread(create)

    @staticmethod
    def _review(row):
        saved = fixed_coverage(row["record"])
        for key in ("id", "project_id", "document_id", "requirement_id", "requirement_version_id"):
            if str(row[key]) != getattr(saved, key):
                raise ValueError("覆盖确认数据库身份与记录不同")
        if row["revision"] != saved.revision or row["request_id"] != saved.request.request_id or row["created_at"] != saved.created_at:
            raise ValueError("覆盖确认数据库修订与记录不同")
        return saved

    def _prior(self, cur, project_id, request_id, digest=None):
        cur.execute(f"SELECT * FROM {self.table} WHERE project_id=%s AND request_id=%s", (project_id, request_id))
        row = cur.fetchone()
        result = self._review(row) if row else None
        if result and digest is not None and result.request_sha256 != digest:
            raise ValueError("同一覆盖确认请求不能改变需求版本或关联参数")
        return result

    async def get_by_request(self, project_id, request_id, *, digest=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                return self._prior(cur, project_id, request_id, digest)
        return await asyncio.to_thread(read)

    async def get(self, project_id, document_id, requirement_id, version_id, *, coverage_id=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table} WHERE project_id=%s AND document_id=%s AND requirement_id=%s "
                    "AND requirement_version_id=%s AND (%s::uuid IS NULL OR id=%s::uuid) ORDER BY revision DESC LIMIT 1",
                    (project_id, document_id, requirement_id, version_id, coverage_id, coverage_id))
                row = cur.fetchone()
                if coverage_id and row is None:
                    raise KeyError("覆盖确认不属于指定需求版本")
                return self._review(row) if row else None
        return await asyncio.to_thread(read)

    async def previous(self, project_id, document_id, requirement_id, *, at):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT DISTINCT ON (requirement_version_id) * FROM {self.table} "
                    "WHERE project_id=%s AND document_id=%s AND requirement_id=%s AND created_at<=%s "
                    "ORDER BY requirement_version_id,revision DESC", (project_id, document_id, requirement_id, at))
                return {saved.requirement_version_id: saved for row in cur.fetchall() for saved in [self._review(row)]}
        return await asyncio.to_thread(read)

    async def list_reviews(self, project_id, document_id, requirement_id, version_id, *, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("覆盖确认分页参数无效")
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table} WHERE project_id=%s AND document_id=%s AND requirement_id=%s "
                    "AND requirement_version_id=%s ORDER BY revision DESC LIMIT %s OFFSET %s",
                    (project_id, document_id, requirement_id, version_id, limit+1, offset))
                rows = cur.fetchall()
                return RequirementCoveragePage(items=[self._review(row) for row in rows[:limit]],
                    has_more=len(rows)>limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    async def save(self, project_id, document_id, requirement_id, version_id, request, digest):
        return await asyncio.to_thread(self._save, project_id, document_id, requirement_id, version_id, request, digest)

    def _save(self, project_id, document_id, requirement_id, version_id, request, digest):
        # No upstream counterpart (checked: immutable requirement review, case
        # review and generation receipt): later drafts need independent decisions.
        if digest != coverage_request_hash(document_id, requirement_id, version_id, request):
            raise ValueError("覆盖确认哈希与实际参数不同")
        req = self._requirements
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT status FROM {req._projects} WHERE id=%s FOR UPDATE", (project_id,))
            project = cur.fetchone()
            if project is None:
                raise KeyError("覆盖确认项目不存在")
            prior = self._prior(cur, project_id, request.request_id, digest)
            if prior:
                return prior
            if project["status"] != "active":
                raise ValueError("已归档项目不能提交新的覆盖确认")
            cur.execute(f"SELECT project_id FROM {req._docs} WHERE id=%s FOR SHARE", (document_id,))
            document = cur.fetchone()
            if document is None or str(document["project_id"]) != project_id:
                raise KeyError("覆盖确认文档不属于当前项目")
            cur.execute(f"SELECT v.record FROM {req._versions} v JOIN {req._requirements} r ON r.id=v.requirement_id "
                "WHERE r.id=%s AND r.project_id=%s AND r.document_id=%s AND v.id=%s FOR SHARE OF r,v",
                (requirement_id, project_id, document_id, version_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError("覆盖确认需求版本不属于当前项目文档")
            version = fixed_requirement_version(row["record"])
            if version.id != version_id or version.requirement_id != requirement_id:
                raise ValueError("覆盖确认需求数据库身份不同")
            keys = {point.key for point in version.points}
            if version.change == "removed" and request.case_links:
                raise ValueError("已移除需求版本不能增加覆盖")
            if any(not set(link.point_keys).issubset(keys) for link in request.case_links):
                raise ValueError("覆盖必须指向该固定需求版本的验证点")
            cur.execute(f"SELECT * FROM {self.table} WHERE requirement_version_id=%s ORDER BY revision DESC LIMIT 1", (version_id,))
            row = cur.fetchone()
            previous = self._review(row) if row else None
            if request.expected_coverage_id != (previous.id if previous else None):
                raise ValueError("覆盖确认已变化，请核对最新记录后提交新请求")
            links = {}
            for link in sorted(request.case_links, key=lambda item: item.case_version_id):
                cur.execute(f"SELECT v.id,v.case_id,v.content_hash,v.record,c.project_id,c.record AS case_record "
                    f"FROM {req._case_versions} v JOIN {req._cases} c ON c.id=v.case_id WHERE v.id=%s FOR SHARE OF c,v",
                    (link.case_version_id,))
                row = cur.fetchone()
                if row is None or str(row["project_id"]) != project_id:
                    raise ValueError("覆盖用例版本不属于当前项目")
                case, case_version = TestCaseRecord.model_validate(row["case_record"]), TestCaseVersionRecord.model_validate(row["record"])
                if (case.id != str(row["case_id"]) or case.project_id != project_id or case_version.id != str(row["id"]) or
                    case_version.case_id != case.id or case_version.content_hash != row["content_hash"] or
                    version_content_hash(case_version) != case_version.content_hash or case_version.content_hash != link.content_hash):
                    raise ValueError("覆盖用例正文、身份或预期哈希不同")
                review = case.version_reviews.get(case_version.id)
                if review and (review.version_id != case_version.id or review.version != case_version.version):
                    raise ValueError("覆盖用例评审身份不同")
                generation = self._generation(cur, project_id, link.generation_task_id, case_version) if link.generation_task_id else None
                links[link.case_version_id] = RequirementCoverageLink(case_version_id=case_version.id,
                    point_keys=link.point_keys, case_id=case.id, content_hash=case_version.content_hash,
                    version_record_sha256=impact_record_hash(case_version), review_status=review.status if review else "unavailable",
                    review=review, lifecycle_status=case.lifecycle_status, active_version_id=case.active_version_id, generation=generation)
            saved = RequirementCoverageReview(id=str(uuid4()), project_id=project_id, document_id=document_id,
                requirement_id=requirement_id, requirement_version_id=version_id, requirement_version_sha256=version.content_sha256,
                request=request, request_sha256=digest, previous_coverage_id=previous.id if previous else None,
                revision=previous.revision+1 if previous else 1, case_links=[links[link.case_version_id] for link in request.case_links],
                created_at=datetime.now(timezone.utc), sha256="0"*64)
            saved.sha256 = requirement_hash(saved.model_dump(mode="json", exclude={"sha256"}))
            fixed_coverage(saved)
            cur.execute(f"INSERT INTO {self.table}(id,project_id,document_id,requirement_id,requirement_version_id,request_id,revision,created_at,record) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", (saved.id, project_id, document_id, requirement_id,
                version_id, request.request_id, saved.revision, saved.created_at, saved.model_dump_json()))
        return saved

    def _generation(self, cur, project_id, task_id, version):
        # Reuse the production seed/retry validator; no objects/models/config reads.
        from src.runtime.postgres_generation_task_store import PostgresGenerationTaskStore
        from src.schemas.case_generation_tasks import GenerationGroup, GenerationTaskSeed
        from src.schemas.generation_receipts import GenerationReceipt

        tasks = PostgresGenerationTaskStore(self._settings, None)
        cur.execute("SELECT to_regclass(%s) AS existing", (tasks._tasks,))
        if cur.fetchone()["existing"] is None:
            raise ValueError("指定生成任务尚不存在")
        cur.execute(f"SELECT * FROM {tasks._tasks} WHERE id=%s AND project_id=%s FOR SHARE", (task_id, project_id))
        row = cur.fetchone()
        if row is None:
            raise ValueError("指定生成任务不属于当前项目")
        task, seed = tasks._record(row), GenerationTaskSeed.model_validate(row["seed"])
        if (task.status != "completed" or task.phase != "finished" or task.checkpoint is None or
            task.checkpoint.phase != "result_saved" or task.checkpoint.receipt is None):
            raise ValueError("覆盖生成来源必须有已完成草稿收据")
        if (seed.project_id != project_id or seed.document_id != task.document_id or seed.group_id != task.group_id or
            seed.version_id != task.version_id or seed.module_id != task.module_id):
            raise ValueError("生成来源seed身份不同")
        retry = tasks._validate_seed(cur, seed)
        if retry and retry.task_id != task.id:
            raise ValueError("生成来源重试身份不同")
        cur.execute(f"SELECT record FROM {tasks._groups} WHERE id=%s", (task.group_id,))
        group = GenerationGroup.model_validate(cur.fetchone()["record"])
        receipt = GenerationReceipt.model_validate(task.checkpoint.receipt)
        if (receipt.kind not in {"update", "create"} or receipt.module_id != task.module_id or receipt.scope_sha256 != group.scope_sha256 or
            receipt.response is None or receipt.response != task.checkpoint.response or receipt.retirement_target is not None):
            raise ValueError("生成来源范围或响应收据不同")
        matches = [item for item in receipt.versions if item.version_id == version.id]
        if len(matches) != 1 or (matches[0].case_id, matches[0].version, matches[0].content_hash) != (version.case_id, version.version, version.content_hash):
            raise ValueError("生成收据没有匹配的完整用例版本")
        provenance = version.generation_metadata.get("incremental_generation")
        if not isinstance(provenance, dict) or any(provenance.get(key) != expected for key, expected in {
            "group_id": group.id, "module_id": task.module_id, "scope_sha256": group.scope_sha256,
            "response": receipt.response.model_dump(mode="json"), "retry": seed.retry.model_dump(mode="json") if seed.retry else None,
        }.items()):
            raise ValueError("用例生成元数据与原任务收据不同")
        return RequirementCoverageGeneration(task_id=task.id, source_document_id=task.document_id,
            group_id=group.id, group_sha256=group.sha256, module_sha256=seed.module_sha256,
            receipt=receipt, retry=seed.retry.model_dump(mode="json") if seed.retry else None, provenance=provenance)
