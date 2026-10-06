"""Append-only point evidence decisions over fixed coverage and real run results."""
import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_sources import impact_record_hash
from src.application.documents.requirement_execution_checks import body_checks, fixed_result
from src.application.test_cases.version_content import version_content_hash
from src.application.test_runs.agent_case_assessment import literal_value
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_requirement_store import fixed_requirement_version, requirement_hash
from src.runtime.postgres_tool_job_store import _job_from_row
from src.schemas.case_management import TestCaseVersionRecord
from src.schemas.requirement_execution import (
    RequirementExecutionCheck, RequirementExecutionReview, RequirementExecutionPage,
    RequirementExecutionCandidate, RequirementExecutionCandidates,
)
from src.schemas.run_management import TestRunRecord, TestRunItemRecord, TestRunAttemptRecord


def execution_request_hash(document_id, requirement_id, version_id, request):
    return requirement_hash({"document_id": document_id, "requirement_id": requirement_id,
        "requirement_version_id": version_id, "request": request.model_dump(mode="json", exclude={"request_id"})})


def fixed_execution(value):
    review = RequirementExecutionReview.model_validate(value)
    if (requirement_hash(review.model_dump(mode="json", exclude={"sha256"})) != review.sha256 or
        execution_request_hash(review.document_id, review.requirement_id, review.requirement_version_id,
            review.request) != review.request_sha256 or
        review.previous_execution_id != review.request.expected_execution_id or
        len(review.request.checks) != len(review.checks)):
        raise ValueError("执行确认与固定正文或原请求不同")
    for given, check in zip(review.request.checks, review.checks):
        if (given.model_dump() != check.model_dump(include=set(type(given).model_fields)) or
            requirement_hash(check.check) != check.check_sha256):
            raise ValueError("执行确认核验与原引用或哈希不同")
    return review


class PostgresRequirementExecutionStore:
    def __init__(self, requirements):
        self._requirements = requirements
        self._settings = requirements._settings
        self.table = requirements._docs + "_requirement_execution"
        db = self._settings.database
        self._runs, self._items = db.postgres_test_run_table, db.postgres_test_run_item_table
        self._attempts, self._results = db.postgres_test_run_attempt_table, db.postgres_test_case_result_table
        self._jobs = db.postgres_tool_job_table
        for name in [self.table, self._runs, self._items, self._attempts, self._results, self._jobs]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
                raise ValueError("Invalid requirement execution table identifier")

    async def initialize(self):
        def create():
            req = self._requirements
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {req._projects}(id),
                    document_id UUID NOT NULL REFERENCES {req._docs}(id),
                    requirement_id UUID NOT NULL REFERENCES {req._requirements}(id),
                    requirement_version_id UUID NOT NULL REFERENCES {req._versions}(id),
                    coverage_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision>0), created_at TIMESTAMPTZ NOT NULL,
                    record JSONB NOT NULL, UNIQUE(project_id,request_id), UNIQUE(requirement_version_id,revision))""")
        await asyncio.to_thread(create)

    @staticmethod
    def _review(row):
        review = fixed_execution(row["record"])
        for key in ["id", "project_id", "document_id", "requirement_id", "requirement_version_id"]:
            if str(row[key]) != getattr(review, key):
                raise ValueError("执行确认数据库身份与固定记录不同")
        if (row["revision"] != review.revision or row["created_at"] != review.created_at or
            row["request_id"] != review.request.request_id or row["coverage_id"] != review.request.coverage_id):
            raise ValueError("执行确认数据库修订与固定记录不同")
        return review

    def _prior(self, cur, project_id, request_id, digest=None):
        cur.execute(f"SELECT * FROM {self.table} WHERE project_id=%s AND request_id=%s", (project_id, request_id))
        row = cur.fetchone()
        prior = self._review(row) if row else None
        if prior and digest is not None and digest != prior.request_sha256:
            raise ValueError("同一执行确认请求不能改变参数或固定依据")
        return prior

    async def get_by_request(self, project_id, request_id, *, digest=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                return self._prior(cur, project_id, request_id, digest)
        return await asyncio.to_thread(read)

    async def get(self, project_id, document_id, requirement_id, version_id, coverage_id, *, execution_id=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table} WHERE project_id=%s AND document_id=%s AND requirement_id=%s "
                    "AND requirement_version_id=%s AND coverage_id=%s AND (%s::uuid IS NULL OR id=%s::uuid) ORDER BY revision DESC LIMIT 1",
                    (project_id, document_id, requirement_id, version_id, coverage_id, execution_id, execution_id))
                row = cur.fetchone()
                if execution_id and row is None:
                    raise KeyError("执行确认不属于指定需求版本和覆盖依据")
                return self._review(row) if row else None
        return await asyncio.to_thread(read)

    async def list_reviews(self, project_id, document_id, requirement_id, version_id, *, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("执行确认分页参数无效")
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table} WHERE project_id=%s AND document_id=%s AND requirement_id=%s "
                    "AND requirement_version_id=%s ORDER BY revision DESC LIMIT %s OFFSET %s",
                    (project_id, document_id, requirement_id, version_id, limit+1, offset))
                rows = cur.fetchall()
                return RequirementExecutionPage(items=[self._review(row) for row in rows[:limit]],
                    has_more=len(rows)>limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    def _basis(self, cur, project_id, document_id, requirement_id, version_id, coverage_id):
        req = self._requirements
        cur.execute(f"SELECT v.record FROM {req._versions} v JOIN {req._requirements} r ON r.id=v.requirement_id "
            "WHERE r.project_id=%s AND r.document_id=%s AND r.id=%s AND v.id=%s FOR SHARE OF r,v",
            (project_id, document_id, requirement_id, version_id))
        row = cur.fetchone()
        if row is None:
            raise KeyError("执行确认需求版本不属于当前项目文档")
        version = fixed_requirement_version(row["record"])
        if version.id != version_id or version.requirement_id != requirement_id:
            raise ValueError("执行确认数据库需求身份与固定版本不同")
        links, digest = version.case_links, version.content_sha256
        if coverage_id != "initial":
            cur.execute(f"SELECT * FROM {req.coverage.table} WHERE id=%s AND project_id=%s AND document_id=%s "
                "AND requirement_id=%s AND requirement_version_id=%s", (coverage_id, project_id, document_id, requirement_id, version_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError("执行确认覆盖依据不属于指定需求版本")
            coverage = req.coverage._review(row)
            if coverage.requirement_version_sha256 != version.content_sha256:
                raise ValueError("执行确认覆盖与固定需求版本哈希不同")
            links, digest = coverage.case_links, coverage.sha256
        return version, links, digest

    def _result(self, cur, project_id, result_id):
        cur.execute(f"SELECT x.* FROM {self._results} x JOIN {self._runs} r ON r.id=x.run_id "
            "WHERE x.id=%s AND r.project_id=%s FOR SHARE OF x,r", (result_id, project_id))
        row = cur.fetchone()
        if row is None:
            raise KeyError("执行结果不属于当前项目")
        result = fixed_result(row["record"])
        for key in ["id", "run_id", "run_item_id", "case_id", "case_version_id", "attempt_id"]:
            if str(row[key]) != getattr(result, key):
                raise ValueError("执行结果数据库身份与原记录不同")
        if row["status"] != result.status or row["payload_hash"] != result.payload_hash or row["created_at"] != result.created_at:
            raise ValueError("执行结果数据库内容与原记录不同")
        cur.execute(f"SELECT record FROM {self._runs} WHERE id=%s", (result.run_id,))
        run = TestRunRecord.model_validate(cur.fetchone()["record"])
        cur.execute(f"SELECT record FROM {self._items} WHERE id=%s FOR SHARE", (result.run_item_id,))
        item = TestRunItemRecord.model_validate(cur.fetchone()["record"])
        cur.execute(f"SELECT record FROM {self._attempts} WHERE id=%s FOR SHARE", (result.attempt_id,))
        attempt = TestRunAttemptRecord.model_validate(cur.fetchone()["record"])
        if (run.project_id != project_id or run.id != result.run_id or item.id != result.run_item_id or item.run_id != run.id or
            item.result_id != result.id or item.case_id != result.case_id or item.case_version_id != result.case_version_id or
            item.attempt_no != result.attempt_no or item.regression_source_result_id != result.regression_source_result_id or
            item.status != result.status or attempt.id != result.attempt_id or attempt.run_id != result.run_id or
            attempt.run_item_id != item.id or attempt.attempt_no != result.attempt_no or attempt.status != result.status):
            raise ValueError("执行结果与原运行、条目或attempt不同")
        return result, run, item, body_checks(result, item.plan_item_key, project_id)

    def _tools(self, cur, project_id, result, verification_id, check):
        verification = next(value for value in result.actual["verification_results"] if value["id"] == verification_id)
        refs = [ref for ref in result.evidence_refs if ref.evidence_type == "tool_job"]
        ids = {}
        for ref in refs:
            call_id = ref.metadata.get("call_id")
            if not call_id or (call_id in ids and ids[call_id] != ref.evidence_id):
                raise ValueError("原结果工具调用身份缺失或对应不同证据")
            ids[call_id] = ref.evidence_id
        observed, sources, usable = [], [], True
        for ref in check["observations"]:
            job_id = ids.get(ref["call_id"])
            if not job_id:
                raise ValueError("核验引用没有对应的实际工具")
            cur.execute(f"SELECT * FROM {self._jobs} WHERE id=%s FOR SHARE", (job_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError("核验实际工具证据不存在")
            job = _job_from_row(row)
            if (job.session_id != verification["session_id"] or job.turn_id != verification["turn_id"] or
                job.call_id != ref["call_id"] or any(job.metadata.get(key) != value for key, value in {
                "project_id": project_id, "run_id": result.run_id, "run_item_id": result.run_item_id, "attempt_id": result.attempt_id}.items())):
                raise ValueError("实际工具证据不属于原执行身份")
            if job.status.value not in {"completed", "partial", "failed", "denied", "cancelled"}:
                raise ValueError("实际工具证据没有终态")
            usable = usable and job.status.value in {"completed", "partial"}
            observed.append(literal_value(job.output_payload, ref["output_path_tokens"]))
            sources.append({"job_id": job.id, "call_id": job.call_id, "status": job.status.value,
                "output_sha256": requirement_hash(job.output_payload)})
        actual = observed[0] if len(observed) == 1 else observed
        if not observed or requirement_hash(actual) != requirement_hash(check["actual"]) or ((check["status"] == "not_run") == usable):
            raise ValueError("原核验实际值或可用性与工具原输出不同")
        return sources

    async def save(self, project_id, document_id, requirement_id, version_id, request, digest):
        return await asyncio.to_thread(self._save, project_id, document_id, requirement_id, version_id, request, digest)

    def _save(self, project_id, document_id, requirement_id, version_id, request, digest):
        if digest != execution_request_hash(document_id, requirement_id, version_id, request):
            raise ValueError("执行确认请求哈希与实际参数不同")
        req = self._requirements
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT status FROM {req._projects} WHERE id=%s FOR UPDATE", (project_id,))
            project = cur.fetchone()
            if project is None:
                raise KeyError("执行确认项目不存在")
            prior = self._prior(cur, project_id, request.request_id, digest)
            if prior:
                return prior
            if project["status"] != "active":
                raise ValueError("已归档项目不能提交新的执行确认")
            version, links, coverage_sha = self._basis(cur, project_id, document_id, requirement_id, version_id, request.coverage_id)
            keys = {point.key for point in version.points}
            if any(not set(ref.point_keys).issubset(keys) for ref in request.checks):
                raise ValueError("执行确认必须指向固定需求版本的验证点")
            cur.execute(f"SELECT * FROM {self.table} WHERE requirement_version_id=%s ORDER BY revision DESC LIMIT 1", (version_id,))
            row = cur.fetchone()
            previous = self._review(row) if row else None
            if request.expected_execution_id != (previous.id if previous else None):
                raise ValueError("执行确认已变化，请核对最新历史后提交新请求")
            saved_checks, loaded = [], {}
            for ref in request.checks:
                if ref.result_id not in loaded:
                    loaded[ref.result_id] = self._result(cur, project_id, ref.result_id)
                result, run, item, checks = loaded[ref.result_id]
                link = next((link for link in links if link.case_id == result.case_id and link.case_version_id == result.case_version_id), None)
                if link is None or not set(ref.point_keys).issubset(link.point_keys):
                    raise ValueError("实际执行版本或验证点不属于该固定设计覆盖")
                check = checks.get((ref.verification_id, ref.check_index))
                if check is None:
                    raise ValueError("引用不是该结果的正文核验；准备或清理不能证明需求通过")
                cur.execute(f"SELECT c.project_id,v.record FROM {req._case_versions} v JOIN {req._cases} c ON c.id=v.case_id "
                    "WHERE v.id=%s FOR SHARE OF c,v", (result.case_version_id,))
                case_row = cur.fetchone()
                if case_row is None:
                    raise KeyError("执行确认的固定用例版本不存在")
                fixed = TestCaseVersionRecord.model_validate(case_row["record"])
                if (str(case_row["project_id"]) != project_id or fixed.id != result.case_version_id or fixed.case_id != result.case_id or
                    version_content_hash(fixed) != fixed.content_hash or impact_record_hash(fixed) != link.version_record_sha256 or
                    requirement_hash(literal_value(fixed.business_content(), check["expected_path_tokens"])) != requirement_hash(check["expected"])):
                    raise ValueError("执行正文预期与固定完整用例或hash不同")
                sources = self._tools(cur, project_id, result, ref.verification_id, check)
                saved_checks.append(RequirementExecutionCheck(**ref.model_dump(), run_id=run.id, run_item_id=item.id,
                    attempt_id=result.attempt_id, attempt_no=result.attempt_no, case_id=result.case_id, case_version_id=result.case_version_id,
                    content_hash=fixed.content_hash, version_record_sha256=link.version_record_sha256,
                    result_payload_hash=result.payload_hash, result_record_sha256=impact_record_hash(result), result_status=result.status,
                    run_kind=run.run_kind, run_origin=run.origin, execution_strategy=run.execution_strategy, mode_key=run.mode_key,
                    regression_source_result_id=result.regression_source_result_id,
                    plan_sha256=impact_record_hash(run.plan_snapshot) if run.plan_snapshot else None,
                    check=check, check_sha256=requirement_hash(check), tool_sources=sources))
            value = dict(id=str(uuid4()), project_id=project_id, document_id=document_id, requirement_id=requirement_id,
                requirement_version_id=version_id, requirement_version_sha256=version.content_sha256, coverage_sha256=coverage_sha,
                request=request, request_sha256=digest, previous_execution_id=previous.id if previous else None,
                revision=previous.revision+1 if previous else 1, checks=saved_checks, created_at=datetime.now(timezone.utc))
            review = RequirementExecutionReview(**value, sha256="0"*64)
            review.sha256 = requirement_hash(review.model_dump(mode="json", exclude={"sha256"}))
            fixed_execution(review)
            cur.execute(f"INSERT INTO {self.table} (id,project_id,document_id,requirement_id,requirement_version_id,coverage_id,request_id,revision,created_at,record) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", (review.id, project_id, document_id, requirement_id, version_id,
                request.coverage_id, request.request_id, review.revision, review.created_at, review.model_dump_json()))
            return review

    async def candidates(self, project_id, document_id, requirement_id, version_id, coverage_id, *, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("执行候选分页参数无效")
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                _, links, _ = self._basis(cur, project_id, document_id, requirement_id, version_id, coverage_id)
                versions = [link.case_version_id for link in links]
                cur.execute(f"SELECT x.id FROM {self._results} x JOIN {self._runs} r ON r.id=x.run_id "
                    "WHERE r.project_id=%s AND x.case_version_id=ANY(%s::uuid[]) ORDER BY x.created_at DESC,x.id LIMIT %s OFFSET %s",
                    (project_id, versions, limit+1, offset))
                rows, items = cur.fetchall(), []
                for row in rows[:limit]:
                    result, _, _, checks = self._result(cur, project_id, str(row["id"]))
                    items.append(RequirementExecutionCandidate(result_id=result.id, run_id=result.run_id, case_id=result.case_id,
                        case_version_id=result.case_version_id, status=result.status, summary=result.summary, created_at=result.created_at,
                        checks=[{"verification_id": identity, "check_index": index, "name": check["name"],
                            "status": check["status"], "method": check["method"], "expected_path_tokens": check["expected_path_tokens"]}
                            for (identity, index), check in checks.items()],
                        warnings=[] if checks else ["原结果没有可关联的结构化正文核验，不能推断验证点通过"]))
                return RequirementExecutionCandidates(items=items, has_more=len(rows)>limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)
