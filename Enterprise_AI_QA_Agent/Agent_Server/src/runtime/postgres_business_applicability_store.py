"""Append scoped decisions with fixed bases and actual persisted observations."""
import asyncio
import hashlib
import re
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_sources import impact_record_hash
from src.application.projects.business_tool_observation import inspect_tool_observation
from src.application.test_cases.version_content import version_content_hash
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_business_environment_store import environment_digest, fixed_environment_snapshot
from src.runtime.postgres_requirement_store import fixed_requirement_version
from src.runtime.postgres_requirement_coverage_store import fixed_coverage
from src.runtime.postgres_tool_job_store import _job_from_row
from src.runtime.requirement_coverage_basis import load_requirement_coverage
from src.schemas.business_applicability import BusinessApplicabilityCase, BusinessApplicabilityPage, BusinessApplicabilityReview
from src.schemas.case_management import TestCaseVersionRecord


def applicability_request_hash(project_id, environment_id, snapshot_id, request):
    return environment_digest({"version": "business-applicability-v1", "project_id": project_id,
        "environment_id": environment_id, "snapshot_id": snapshot_id, "request": request.model_dump(mode="json")})


def fixed_applicability(value):
    review = BusinessApplicabilityReview.model_validate(value)
    request, snapshot = review.request, fixed_environment_snapshot(review.snapshot)
    requirement = fixed_requirement_version(review.requirement_version)
    links = requirement.case_links
    if request.coverage_id == "initial":
        if review.coverage is not None or request.expected_coverage_sha256 != requirement.content_sha256:
            raise ValueError("初始覆盖须引用需求固定正文")
    else:
        coverage = fixed_coverage(review.coverage)
        if (coverage.id != request.coverage_id or coverage.sha256 != request.expected_coverage_sha256 or
            coverage.project_id != review.project_id or coverage.document_id != request.document_id or
            coverage.requirement_id != request.requirement_id or coverage.requirement_version_id != requirement.id or
            coverage.requirement_version_sha256 != requirement.content_sha256):
            raise ValueError("环境适用确认覆盖与固定需求范围不同")
        links = coverage.case_links
    if (review.sha256 != environment_digest(review.model_dump(mode="json", exclude={"sha256"})) or
        review.request_sha256 != applicability_request_hash(review.project_id, review.environment_id, snapshot.id, request) or
        review.project_id != snapshot.project_id or review.environment_id != snapshot.environment_id or
        snapshot.sha256 != request.expected_snapshot_sha256 or requirement.id != request.requirement_version_id or
        requirement.requirement_id != request.requirement_id or requirement.content_sha256 != request.expected_requirement_sha256 or
        review.previous_review_id != request.expected_review_id or len(review.observations) != len(request.observations) or
        len(review.cases) != len(request.cases)):
        raise ValueError("环境适用确认与固定范围、正文或原请求不同")
    for given, observed in zip(request.observations, review.observations):
        if (observed.project_id != review.project_id or observed.environment_id != review.environment_id or
            observed.environment_snapshot_id != snapshot.id or observed.environment_snapshot_sha256 != snapshot.sha256 or
            observed.tool_job_id != given.tool_job_id or observed.output_path_tokens != given.output_path_tokens or
            observed.output_sha256 != given.expected_output_sha256 or observed.value_sha256 != given.expected_value_sha256 or
            observed.value_sha256 != environment_digest(observed.value)):
            raise ValueError("环境适用确认观察与原引用或字段哈希不同")
    for given, saved in zip(request.cases, review.cases):
        link = next((link for link in links if link.case_version_id == saved.case_version_id), None)
        if (given.model_dump() != saved.model_dump(include=set(type(given).model_fields)) or
            saved.case_version_id != saved.version.id or saved.version_record_sha256 != impact_record_hash(saved.version) or
            saved.version.content_hash != version_content_hash(saved.version) or link is None or
            not set(saved.point_keys).issubset(link.point_keys) or link.case_id != saved.version.case_id or
            link.version_record_sha256 != saved.version_record_sha256):
            raise ValueError("环境适用确认用例与完整固定版本不同")
    keys = {point.key for point in requirement.points}
    if any(point.point_key not in keys for point in request.points):
        raise ValueError("环境生效确认验证点不属于固定需求")
    for point in request.points:
        if point.status != "unknown" and any(review.observations[index].target_status != "matched" or
            "selected_value_redacted" in review.observations[index].limitations for index in point.observation_indexes):
            raise ValueError("确定生效结论不能使用目标未知、错目标或脱敏观察")
    return review


class PostgresBusinessApplicabilityStore:
    def __init__(self, *, requirements, environments):
        self._requirements, self._environments = requirements, environments
        self._settings = requirements._settings
        candidate = environments._projects + "_environment_decisions"
        self.table = candidate if len(candidate) <= 63 else "be_" + hashlib.sha256(candidate.encode()).hexdigest()[:16] + "_decisions"
        self._jobs = self._settings.database.postgres_tool_job_table
        for name in [self.table, self._jobs]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", name):
                raise ValueError("Invalid business applicability table identifier")

    async def initialize(self):
        def create():
            req, env = self._requirements, self._environments
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {req._projects}(id),
                    environment_id UUID NOT NULL, snapshot_id UUID NOT NULL REFERENCES {env._snapshots}(id),
                    document_id UUID NOT NULL REFERENCES {req._docs}(id),
                    requirement_id UUID NOT NULL REFERENCES {req._requirements}(id),
                    requirement_version_id UUID NOT NULL REFERENCES {req._versions}(id),
                    request_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
                    created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL,
                    FOREIGN KEY(environment_id,project_id) REFERENCES {env._environments}(id,project_id),
                    UNIQUE(project_id,request_id), UNIQUE(snapshot_id,requirement_version_id,revision))""")
        await asyncio.to_thread(create)

    @staticmethod
    def _review(row):
        review = fixed_applicability(row["record"])
        identities = {"id": review.id, "project_id": review.project_id, "environment_id": review.environment_id,
            "snapshot_id": review.snapshot.id, "document_id": review.request.document_id,
            "requirement_id": review.request.requirement_id, "requirement_version_id": review.request.requirement_version_id}
        if (any(str(row[k]) != v for k, v in identities.items()) or row["revision"] != review.revision or
            row["request_id"] != review.request.request_id or row["created_at"] != review.created_at):
            raise ValueError("环境适用确认与数据库目录身份不同")
        return review

    def _prior(self, cur, project_id, request_id, digest=None):
        cur.execute(f"SELECT * FROM {self.table} WHERE project_id=%s AND request_id=%s", (project_id, request_id))
        row = cur.fetchone()
        prior = self._review(row) if row else None
        if prior and digest is not None and prior.request_sha256 != digest:
            raise ValueError("同一环境适用请求不能改变参数或固定范围")
        return prior

    async def get_by_request(self, project_id, request_id, *, digest=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                return self._prior(cur, project_id, request_id, digest)
        return await asyncio.to_thread(read)

    async def read(self, project_id, environment_id, snapshot_id, *, review_id=None,
        requirement_version_id=None, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("环境适用确认分页参数无效")
        def fetch():
            clause, args = "project_id=%s AND environment_id=%s AND snapshot_id=%s", [project_id, environment_id, snapshot_id]
            if review_id:
                clause += " AND id=%s"; args.append(review_id)
            if requirement_version_id:
                clause += " AND requirement_version_id=%s"; args.append(requirement_version_id)
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT * FROM {self.table} WHERE {clause} ORDER BY revision DESC,id LIMIT %s OFFSET %s",
                    (*args, limit+1, offset))
                rows = cur.fetchall()
            return BusinessApplicabilityPage(items=[self._review(row) for row in rows[:limit]],
                limit=limit, offset=offset, has_more=len(rows)>limit)
        return await asyncio.to_thread(fetch)

    async def save(self, project_id, environment_id, snapshot_id, request):
        return await asyncio.to_thread(self._save, project_id, environment_id, snapshot_id, request)

    def _save(self, project_id, environment_id, snapshot_id, request):
        # No upstream counterpart (checked: requirement coverage/execution reviews,
        # fixed environment captures): rule deployment is an independent explicit decision.
        req, env = self._requirements, self._environments
        digest = applicability_request_hash(project_id, environment_id, snapshot_id, request)
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            project = env._project(cur, project_id)
            prior = self._prior(cur, project_id, request.request_id, digest)
            if prior:
                return prior
            registered = env._get(cur, project_id, environment_id)
            if registered is None:
                raise KeyError("环境不属于当前项目")
            if project["status"] != "active" or registered.status != "active":
                raise ValueError("已归档项目或环境不能追加新适用确认")
            cur.execute(f"SELECT * FROM {env._snapshots} WHERE project_id=%s AND environment_id=%s AND id=%s FOR SHARE",
                (project_id, environment_id, snapshot_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError("固定环境依据不属于当前环境")
            snapshot = env._snapshot(row)
            if (snapshot.registration_request_sha256 != registered.request_sha256 or
                snapshot.environment_key != registered.request.environment_key or snapshot.sha256 != request.expected_snapshot_sha256):
                raise ValueError("固定环境依据与原登记或选择的哈希不同")
            basis = load_requirement_coverage(cur, req, project_id, request.document_id,
                request.requirement_id, request.requirement_version_id, request.coverage_id)
            version, links, coverage_sha = basis.version, basis.links, basis.sha256
            if version.content_sha256 != request.expected_requirement_sha256 or coverage_sha != request.expected_coverage_sha256:
                raise ValueError("需求版本或覆盖依据与所选哈希不同")
            cur.execute(f"SELECT * FROM {self.table} WHERE snapshot_id=%s AND requirement_version_id=%s ORDER BY revision DESC LIMIT 1",
                (snapshot_id, version.id))
            row = cur.fetchone()
            previous = self._review(row) if row else None
            if request.expected_review_id != (previous.id if previous else None):
                raise ValueError("环境适用确认已变化，请核对最新历史后提交新请求")
            observations = []
            for ref in request.observations:
                cur.execute(f"SELECT * FROM {self._jobs} WHERE id=%s FOR SHARE", (ref.tool_job_id,))
                row = cur.fetchone()
                if row is None:
                    raise KeyError("原工具证据不存在")
                observations.append(inspect_tool_observation(snapshot, _job_from_row(row), ref))
            cases = []
            for given in request.cases:
                link = next((link for link in links if link.case_version_id == given.case_version_id), None)
                if link is None or not set(given.point_keys).issubset(link.point_keys):
                    raise ValueError("适用用例版本或验证点不属于所选覆盖依据")
                cur.execute(f"SELECT v.id,v.case_id,v.content_hash,v.record,c.project_id FROM {req._case_versions} v "
                    f"JOIN {req._cases} c ON c.id=v.case_id WHERE v.id=%s FOR SHARE OF c,v", (given.case_version_id,))
                row = cur.fetchone()
                if row is None or str(row["project_id"]) != project_id:
                    raise ValueError("适用用例版本不属于当前项目")
                case = TestCaseVersionRecord.model_validate(row["record"])
                if (case.id != str(row["id"]) or case.case_id != str(row["case_id"]) or case.case_id != link.case_id or
                    case.content_hash != row["content_hash"] or version_content_hash(case) != case.content_hash or
                    impact_record_hash(case) != link.version_record_sha256):
                    raise ValueError("适用用例正文或覆盖引用与固定版本不同")
                cases.append(BusinessApplicabilityCase(**given.model_dump(), version=case, version_record_sha256=impact_record_hash(case)))
            saved = BusinessApplicabilityReview(id=str(uuid4()), project_id=project_id, environment_id=environment_id,
                snapshot=snapshot, requirement_version=version, coverage=basis.review, request=request, request_sha256=digest,
                previous_review_id=previous.id if previous else None, revision=previous.revision+1 if previous else 1,
                observations=observations, cases=cases, created_at=datetime.now(timezone.utc), sha256="0"*64)
            saved.sha256 = environment_digest(saved.model_dump(mode="json", exclude={"sha256"}))
            fixed_applicability(saved)
            cur.execute(f"INSERT INTO {self.table}(id,project_id,environment_id,snapshot_id,document_id,requirement_id,"
                "requirement_version_id,request_id,revision,created_at,record) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (saved.id, project_id, environment_id, snapshot_id, request.document_id, request.requirement_id,
                    version.id, request.request_id, saved.revision, saved.created_at, saved.model_dump_json()))
            return saved
