"""Review fixed impact candidates without rewriting analysis or case history."""
from __future__ import annotations

import logging

from src.runtime.postgres_requirement_store import review_request_hash
from src.schemas.requirements import (
    RequirementMatrix, RequirementMatrixPoint, RequirementReviewSource,
)

logger = logging.getLogger(__name__)


class RequirementService:
    def __init__(self, *, store, workflow, projects, documents):
        self._store, self._workflow = store, workflow
        self._projects, self._documents = projects, documents

    async def initialize(self):
        await self._store.initialize()

    async def _require_document(self, project_id, document_id):
        await self._projects.get(project_id)
        document = await self._documents.get_document(document_id)
        if document.project_id != project_id:
            raise KeyError("需求文档不属于当前项目")

    async def submit_review(self, project_id, document_id, snapshot_id, request):
        await self._require_document(project_id, document_id)
        digest = review_request_hash(document_id, snapshot_id, request)
        prior = await self._store.get_by_request(project_id, request.request_id, digest=digest)
        if prior:
            logger.info("requirement_review_replayed", extra={"project_id": project_id, "review_id": prior.id})
            return prior
        await self._projects.require_active(project_id)
        snapshot, result = await self._workflow.read_snapshot(project_id, document_id, snapshot_id)
        source = self._review_source(snapshot, result, request.rule_id)
        try:
            saved = await self._store.save_review(project_id, document_id, request, source, digest)
        except Exception as exc:
            logger.warning("requirement_review_write_failed", extra={"project_id": project_id,
                "document_id": document_id, "request_id": request.request_id, "error_type": type(exc).__name__})
            raise
        logger.info("requirement_review_saved", extra={"project_id": project_id, "document_id": document_id,
            "review_id": saved.id, "decision": saved.request.decision,
            "requirement_version_id": saved.requirement_version.id if saved.requirement_version else None})
        return saved

    @staticmethod
    def _review_source(snapshot, result, rule_id):
        rule = next((rule for rule in result.rules if rule.id == rule_id), None)
        if rule is None:
            raise ValueError("评审规则不在该固定影响结果中")
        assessments = [candidate for unit in result.units if unit.unit.kind == "case" and
            unit.status in {"analyzed", "evidence_insufficient"} and unit.final_check is not None
            for candidate in unit.candidates if candidate.key == rule.id]
        return RequirementReviewSource(snapshot_id=snapshot.id, task_id=snapshot.task_id,
            snapshot_sha256=snapshot.sha256, input_sha256=result.input_sha256,
            manifest_sha256=result.manifest_sha256, rule=rule, case_assessments=assessments,
            case_checkpoints=[unit.final_check for unit in result.units if unit.unit.kind == "case" and
                unit.status in {"analyzed", "evidence_insufficient"} and unit.final_check is not None and
                any(candidate.key == rule.id for candidate in unit.candidates)],
            basis_limitations=result.basis_limitations, analysis_status=result.status)

    async def read_generation_basis(self, project_id, document_id, requirement_id, version_id):
        await self._require_document(project_id, document_id)
        _, versions = await self._store.get_requirement(project_id, document_id, requirement_id)
        version = next((item for item in versions if item.id == version_id), None)
        if version is None:
            raise KeyError("生成依据版本不属于指定需求")
        review = await self._store.get_review(project_id, document_id, version.review_id)
        if review is None or review.request.decision != "accept" or review.requirement_version != version:
            raise ValueError("生成依据没有匹配的明确接受决定")
        snapshot, result = await self._workflow.read_snapshot(project_id, document_id, review.source.snapshot_id)
        if review.source != self._review_source(snapshot, result, review.request.rule_id):
            raise ValueError("需求评审来源与固定影响结果不同")
        logger.info("requirement_generation_basis_read", extra={"project_id": project_id,
            "requirement_version_id": version.id, "review_id": review.id, "snapshot_id": snapshot.id})
        return version, review

    async def get_by_request(self, project_id, document_id, request_id):
        await self._require_document(project_id, document_id)
        saved = await self._store.get_by_request(project_id, request_id)
        if saved is None or saved.document_id != document_id:
            raise KeyError("需求评审请求不属于当前项目文档")
        return saved

    async def list_reviews(self, project_id, document_id, snapshot_id, *, limit=20, offset=0):
        await self._require_document(project_id, document_id)
        return await self._store.list_reviews(project_id, document_id, snapshot_id, limit=limit, offset=offset)

    async def list_requirements(self, project_id, document_id, *, limit=20, offset=0):
        await self._require_document(project_id, document_id)
        return await self._store.list_requirements(project_id, document_id, limit=limit, offset=offset)

    async def read_matrix(self, project_id, document_id, requirement_id, *, version_id=None):
        await self._require_document(project_id, document_id)
        record, versions = await self._store.get_requirement(project_id, document_id, requirement_id)
        selected = next((version for version in versions if version.id == (version_id or record.current_version_id)), None)
        if selected is None:
            raise KeyError("需求版本不属于当前需求")
        previous = {}
        for version in versions:
            if version.version >= selected.version:
                break
            for link in version.case_links:
                for key in link.point_keys:
                    previous.setdefault(key, {})[link.case_version_id] = link
        points = []
        for point in selected.points:
            links = [link for link in selected.case_links if point.key in link.point_keys]
            old = list(previous.get(point.key, {}).values())
            points.append(RequirementMatrixPoint(point=point, case_links=links, previous_case_links=old,
                design_status="design_covered" if links else "needs_review" if old else "uncovered"))
        return RequirementMatrix(requirement=record, version=selected, points=points, removed=selected.change == "removed")

    async def list_versions(self, project_id, document_id, requirement_id):
        await self._require_document(project_id, document_id)
        _, versions = await self._store.get_requirement(project_id, document_id, requirement_id)
        return versions
