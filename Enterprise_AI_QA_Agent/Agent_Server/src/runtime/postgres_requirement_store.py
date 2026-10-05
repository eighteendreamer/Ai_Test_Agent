"""Atomic explicit reviews and immutable requirement versions in PostgreSQL."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from src.application.documents.impact_sources import impact_record_hash
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_management import TestCaseVersionRecord
from src.schemas.requirements import (
    RequirementBundle, RequirementCaseLink, RequirementPage, RequirementPoint,
    RequirementRecord, RequirementReview, RequirementReviewPage, RequirementVersion,
)


def requirement_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def review_request_hash(document_id, snapshot_id, request):
    return requirement_hash({"document_id": document_id, "snapshot_id": snapshot_id,
        "request": request.model_dump(mode="json", exclude={"request_id"})})


def fixed_requirement_version(value):
    version = RequirementVersion.model_validate(value)
    if requirement_hash(version.model_dump(mode="json", exclude={"content_sha256"})) != version.content_sha256:
        raise ValueError("需求版本正文与固定哈希不同")
    return version


class PostgresRequirementStore:
    def __init__(self, settings):
        self._settings = settings
        self._docs = settings.database.postgres_api_doc_table
        self._projects = settings.database.postgres_project_table
        self._cases = settings.database.postgres_test_case_table
        self._case_versions = settings.database.postgres_test_case_version_table
        self._requirements = self._docs + "_requirements"
        self._versions = self._docs + "_requirement_versions"
        self._reviews = self._docs + "_requirement_reviews"
        self._snapshots = self._docs + "_impact_snapshots"
        from src.runtime.postgres_requirement_coverage_store import PostgresRequirementCoverageStore
        self.coverage = PostgresRequirementCoverageStore(self)
        for table in [self._docs, self._projects, self._cases, self._case_versions, self._snapshots, *self.tables]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table):
                raise ValueError("Invalid requirement table identifier")

    @property
    def tables(self):
        return [self.coverage.table, self._reviews, self._versions, self._requirements]

    async def initialize(self):
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._requirements} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._docs}(id), record JSONB NOT NULL)""")
                # Configured table names can already use all 63 identifier bytes;
                # a short deterministic name avoids PostgreSQL name truncation.
                index = "idx_req_scope_" + hashlib.sha256(self._requirements.encode("ascii")).hexdigest()[:16]
                cur.execute(f"CREATE INDEX IF NOT EXISTS {index} ON {self._requirements}(project_id,document_id,id)")
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._versions} (
                    id UUID PRIMARY KEY, requirement_id UUID NOT NULL REFERENCES {self._requirements}(id),
                    version INTEGER NOT NULL CHECK(version>0), record JSONB NOT NULL,
                    UNIQUE(requirement_id,version))""")
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._reviews} (
                    id UUID PRIMARY KEY, project_id UUID NOT NULL REFERENCES {self._projects}(id),
                    document_id UUID NOT NULL REFERENCES {self._docs}(id),
                    snapshot_id UUID NOT NULL REFERENCES {self._snapshots}(id), rule_id TEXT NOT NULL,
                    request_id TEXT NOT NULL, review_number INTEGER NOT NULL CHECK(review_number>0),
                    created_at TIMESTAMPTZ NOT NULL, record JSONB NOT NULL,
                    UNIQUE(project_id,request_id), UNIQUE(snapshot_id,rule_id,review_number))""")
        await asyncio.to_thread(create)
        await self.coverage.initialize()

    @staticmethod
    def _review(row):
        result = RequirementReview.model_validate(row["record"])
        if review_request_hash(result.document_id, result.source.snapshot_id, result.request) != result.request_sha256:
            raise ValueError("需求评审请求正文与固定哈希不同")
        if result.requirement_version:
            fixed_requirement_version(result.requirement_version)
        return result

    def _prior(self, cur, project_id, request_id, digest=None):
        cur.execute(f"SELECT record FROM {self._reviews} WHERE project_id=%s AND request_id=%s", (project_id, request_id))
        row = cur.fetchone()
        if row is None:
            return None
        result = self._review(row)
        if digest is not None and result.request_sha256 != digest:
            raise ValueError("同一需求评审请求不能改变文档、候选或决定参数")
        return result

    async def get_by_request(self, project_id, request_id, *, digest=None):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                return self._prior(cur, project_id, request_id, digest)
        return await asyncio.to_thread(read)

    async def get_review(self, project_id, document_id, review_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._reviews} WHERE project_id=%s AND document_id=%s AND id=%s",
                    (project_id, document_id, review_id))
                row = cur.fetchone()
                return self._review(row) if row else None
        return await asyncio.to_thread(read)

    async def save_review(self, project_id, document_id, request, source, digest):
        return await asyncio.to_thread(self._save_review, project_id, document_id, request, source, digest)

    def _save_review(self, project_id, document_id, request, source, digest):
        # No upstream counterpart (checked: case reviews, import transaction,
        # fixed impact snapshot and local evidence governance course): a formal
        # requirement identity must be chosen explicitly outside AI proposals.
        if digest != review_request_hash(document_id, source.snapshot_id, request):
            raise ValueError("需求评审请求哈希与实际参数不同")
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT status FROM {self._projects} WHERE id=%s FOR UPDATE", (project_id,))
            project = cur.fetchone()
            if project is None:
                raise KeyError("需求项目不存在")
            prior = self._prior(cur, project_id, request.request_id, digest)
            if prior:
                return prior
            if project["status"] != "active":
                raise ValueError("已归档项目不能提交新的需求评审")
            cur.execute(f"SELECT record FROM {self._snapshots} WHERE id=%s AND project_id=%s AND document_id=%s FOR SHARE",
                (source.snapshot_id, project_id, document_id))
            snapshot = cur.fetchone()
            if snapshot is None:
                raise KeyError("固定影响快照不属于当前项目文档")
            snapshot = snapshot["record"]
            if (source.rule.id != request.rule_id or source.snapshot_sha256 != snapshot["sha256"] or
                source.task_id != snapshot["task_id"] or source.input_sha256 != snapshot["input_sha256"] or
                source.manifest_sha256 != snapshot["manifest_sha256"]):
                raise ValueError("需求评审不能更换固定候选或来源快照")
            cur.execute(f"SELECT record FROM {self._reviews} WHERE snapshot_id=%s AND rule_id=%s ORDER BY review_number DESC LIMIT 1",
                (source.snapshot_id, request.rule_id))
            previous_row = cur.fetchone()
            previous = self._review(previous_row) if previous_row else None
            if request.expected_review_id != (previous.id if previous else None):
                raise ValueError("候选评审已变化，请核对最新决定后提交新请求")
            now, review_id = datetime.now(timezone.utc), str(uuid4())
            version = None
            if request.decision == "accept":
                version = self._accept(cur, project_id, document_id, request, snapshot, review_id, now)
            review = RequirementReview(id=review_id, project_id=project_id, document_id=document_id,
                request=request, request_sha256=digest, source=source,
                previous_review_id=previous.id if previous else None,
                review_number=previous.review_number+1 if previous else 1,
                requirement_version=version, created_at=now)
            cur.execute(f"INSERT INTO {self._reviews}(id,project_id,document_id,snapshot_id,rule_id,request_id,review_number,created_at,record) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)", (review.id, project_id, document_id, source.snapshot_id,
                request.rule_id, request.request_id, review.review_number, now, review.model_dump_json()))
        return review

    def _accept(self, cur, project_id, document_id, request, snapshot, review_id, now):
        identity = request.requirement_id or str(uuid4())
        existing, point_ids = None, {}
        if request.requirement_id:
            cur.execute(f"SELECT record FROM {self._requirements} WHERE id=%s AND project_id=%s AND document_id=%s FOR UPDATE",
                (identity, project_id, document_id))
            row = cur.fetchone()
            if row is None:
                raise KeyError("关联需求不属于当前项目文档")
            existing = RequirementRecord.model_validate(row["record"])
            if existing.current_version_id != request.expected_version_id:
                raise ValueError("需求父版本已变化，不能覆盖新决定")
            cur.execute(f"SELECT record FROM {self._versions} WHERE requirement_id=%s ORDER BY version", (identity,))
            for row in cur.fetchall():
                previous = fixed_requirement_version(row["record"])
                for point in previous.points:
                    if point.key in point_ids and point_ids[point.key] != point.id:
                        raise ValueError("需求验证点稳定身份不一致")
                    point_ids[point.key] = point.id
        links, fixed_cases = [], {item["version_id"]: item for item in snapshot["seed"]["cases"]}
        for link in request.case_links:
            fixed = fixed_cases.get(link.case_version_id)
            if fixed is None:
                raise ValueError("覆盖用例必须来自该固定分析范围")
            cur.execute(f"SELECT v.record,c.project_id FROM {self._case_versions} v JOIN {self._cases} c ON c.id=v.case_id "
                "WHERE v.id=%s FOR SHARE OF c,v", (link.case_version_id,))
            row = cur.fetchone()
            if (row is None or str(row["project_id"]) != project_id or
                impact_record_hash(TestCaseVersionRecord.model_validate(row["record"])) != fixed["version_record_sha256"]):
                raise ValueError("覆盖用例版本或项目与固定依据不同")
            links.append(RequirementCaseLink(**link.model_dump(), case_id=fixed["case"]["id"],
                version_record_sha256=fixed["version_record_sha256"]))
        content = dict(id=str(uuid4()), requirement_id=identity, version=existing.latest_version+1 if existing else 1,
            parent_version_id=existing.current_version_id if existing else None, review_id=review_id,
            change=request.change, title=request.title, statement=request.statement,
            points=[RequirementPoint(**point.model_dump(), id=point_ids.get(point.key) or str(uuid4())) for point in request.points],
            case_links=links, created_at=now)
        version = RequirementVersion(**content, content_sha256="0"*64)
        version.content_sha256 = requirement_hash(version.model_dump(mode="json", exclude={"content_sha256"}))
        record = RequirementRecord(id=identity, project_id=project_id, document_id=document_id,
            current_version_id=version.id, latest_version=version.version,
            created_at=existing.created_at if existing else now, updated_at=now)
        if existing:
            cur.execute(f"UPDATE {self._requirements} SET record=%s::jsonb WHERE id=%s", (record.model_dump_json(), identity))
        else:
            cur.execute(f"INSERT INTO {self._requirements}(id,project_id,document_id,record) VALUES(%s,%s,%s,%s::jsonb)",
                (identity, project_id, document_id, record.model_dump_json()))
        cur.execute(f"INSERT INTO {self._versions}(id,requirement_id,version,record) VALUES(%s,%s,%s,%s::jsonb)",
            (version.id, identity, version.version, version.model_dump_json()))
        return version

    async def list_reviews(self, project_id, document_id, snapshot_id, *, limit=20, offset=0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._reviews} WHERE project_id=%s AND document_id=%s AND snapshot_id=%s "
                    "ORDER BY created_at,id LIMIT %s OFFSET %s", (project_id, document_id, snapshot_id, limit+1, offset))
                rows = cur.fetchall()
                return RequirementReviewPage(items=[self._review(row) for row in rows[:limit]],
                    has_more=len(rows)>limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    async def list_requirements(self, project_id, document_id, *, limit=20, offset=0):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT r.record,v.record AS version FROM {self._requirements} r "
                    f"JOIN {self._versions} v ON v.id=(r.record->>'current_version_id')::uuid "
                    "WHERE r.project_id=%s AND r.document_id=%s ORDER BY r.id LIMIT %s OFFSET %s",
                    (project_id, document_id, limit+1, offset))
                rows = cur.fetchall()
                return RequirementPage(items=[RequirementBundle(requirement=RequirementRecord.model_validate(row["record"]),
                    version=fixed_requirement_version(row["version"])) for row in rows[:limit]],
                    has_more=len(rows)>limit, limit=limit, offset=offset)
        return await asyncio.to_thread(read)

    async def get_requirement(self, project_id, document_id, requirement_id):
        def read():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._requirements} WHERE id=%s AND project_id=%s AND document_id=%s",
                    (requirement_id, project_id, document_id))
                row = cur.fetchone()
                if row is None:
                    raise KeyError("需求不属于当前项目文档")
                record = RequirementRecord.model_validate(row["record"])
                cur.execute(f"SELECT record FROM {self._versions} WHERE requirement_id=%s ORDER BY version", (requirement_id,))
                versions = [fixed_requirement_version(row["record"]) for row in cur.fetchall()]
                return record, versions
        return await asyncio.to_thread(read)
