"""Shared fixed requirement and explicitly selected coverage lookup."""
from typing import NamedTuple

from src.runtime.postgres_requirement_store import fixed_requirement_version
from src.schemas.requirements import RequirementCaseLink, RequirementCoverageReview, RequirementVersion


class RequirementCoverageBasis(NamedTuple):
    version: RequirementVersion
    links: list[RequirementCaseLink]
    sha256: str
    review: RequirementCoverageReview | None


def load_requirement_coverage(cur, req, project_id, document_id, requirement_id, version_id, coverage_id):
    cur.execute(f"SELECT v.record FROM {req._versions} v JOIN {req._requirements} r ON r.id=v.requirement_id "
        "WHERE r.project_id=%s AND r.document_id=%s AND r.id=%s AND v.id=%s FOR SHARE OF r,v",
        (project_id, document_id, requirement_id, version_id))
    row = cur.fetchone()
    if row is None:
        raise KeyError("执行确认需求版本不属于当前项目文档")
    version = fixed_requirement_version(row["record"])
    if version.id != version_id or version.requirement_id != requirement_id:
        raise ValueError("执行确认数据库需求身份与固定版本不同")
    links, digest, coverage = version.case_links, version.content_sha256, None
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
    return RequirementCoverageBasis(version, links, digest, coverage)
