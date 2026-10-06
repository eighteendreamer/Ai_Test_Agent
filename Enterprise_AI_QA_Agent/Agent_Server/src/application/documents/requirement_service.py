"""Review fixed impact candidates without rewriting analysis or case history."""
from __future__ import annotations

import logging

from src.runtime.postgres_requirement_store import review_request_hash
from src.runtime.postgres_requirement_coverage_store import coverage_request_hash
from src.runtime.postgres_requirement_execution_store import execution_request_hash
from src.application.documents.requirement_execution_checks import point_status
from src.schemas.requirements import (
    RequirementCaseLink, RequirementMatrix, RequirementMatrixPoint, RequirementReviewSource,
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

    async def submit_coverage(self, project_id, document_id, requirement_id, version_id, request):
        await self._require_document(project_id, document_id)
        digest = coverage_request_hash(document_id, requirement_id, version_id, request)
        prior = await self._store.coverage.get_by_request(project_id, request.request_id, digest=digest)
        if prior:
            logger.info("requirement_coverage_replayed", extra={"project_id": project_id, "coverage_id": prior.id})
            return prior
        await self._projects.require_active(project_id)
        try:
            saved = await self._store.coverage.save(project_id, document_id, requirement_id, version_id, request, digest)
        except Exception as exc:
            logger.warning("requirement_coverage_write_failed", extra={"project_id": project_id,
                "document_id": document_id, "requirement_id": requirement_id, "request_id": request.request_id,
                "error_type": type(exc).__name__})
            raise
        logger.info("requirement_coverage_saved", extra={"project_id": project_id,
            "requirement_version_id": version_id, "coverage_id": saved.id, "revision": saved.revision,
            "link_count": len(saved.case_links)})
        return saved

    async def get_coverage_by_request(self, project_id, document_id, request_id):
        await self._require_document(project_id, document_id)
        saved = await self._store.coverage.get_by_request(project_id, request_id)
        if saved is None or saved.document_id != document_id:
            raise KeyError("覆盖确认请求不属于当前项目文档")
        return saved

    async def list_coverage(self, project_id, document_id, requirement_id, version_id, *, limit=20, offset=0):
        await self._require_document(project_id, document_id)
        _, versions = await self._store.get_requirement(project_id, document_id, requirement_id)
        if not any(version.id == version_id for version in versions):
            raise KeyError("覆盖确认版本不属于指定需求")
        return await self._store.coverage.list_reviews(project_id, document_id, requirement_id, version_id, limit=limit, offset=offset)

    async def submit_execution(self, project_id, document_id, requirement_id, version_id, request):
        await self._require_document(project_id, document_id)
        digest = execution_request_hash(document_id, requirement_id, version_id, request)
        prior = await self._store.execution.get_by_request(project_id, request.request_id, digest=digest)
        if prior:
            logger.info("requirement_execution_replayed", extra={"project_id": project_id, "execution_id": prior.id})
            return prior
        await self._projects.require_active(project_id)
        try:
            saved = await self._store.execution.save(project_id, document_id, requirement_id, version_id, request, digest)
        except Exception as exc:
            logger.warning("requirement_execution_write_failed", extra={"project_id": project_id,
                "document_id": document_id, "requirement_id": requirement_id, "request_id": request.request_id,
                "error_type": type(exc).__name__})
            raise
        logger.info("requirement_execution_saved", extra={"project_id": project_id,
            "requirement_version_id": version_id, "execution_id": saved.id, "revision": saved.revision,
            "check_count": len(saved.checks)})
        return saved

    async def get_execution_by_request(self, project_id, document_id, request_id):
        await self._require_document(project_id, document_id)
        saved = await self._store.execution.get_by_request(project_id, request_id)
        if saved is None or saved.document_id != document_id:
            raise KeyError("执行确认请求不属于当前项目文档")
        return saved

    async def list_execution(self, project_id, document_id, requirement_id, version_id, *, limit=20, offset=0):
        await self._require_document(project_id, document_id)
        _, versions = await self._store.get_requirement(project_id, document_id, requirement_id)
        if not any(version.id == version_id for version in versions):
            raise KeyError("执行确认版本不属于指定需求")
        return await self._store.execution.list_reviews(project_id, document_id, requirement_id, version_id, limit=limit, offset=offset)

    async def execution_candidates(self, project_id, document_id, requirement_id, version_id, coverage_id, *, limit=20, offset=0):
        await self._require_document(project_id, document_id)
        return await self._store.execution.candidates(project_id, document_id, requirement_id, version_id, coverage_id,
            limit=limit, offset=offset)

    async def read_matrix(self, project_id, document_id, requirement_id, *, version_id=None, coverage_id=None, execution_id=None):
        await self._require_document(project_id, document_id)
        record, versions = await self._store.get_requirement(project_id, document_id, requirement_id)
        selected = next((version for version in versions if version.id == (version_id or record.current_version_id)), None)
        if selected is None:
            raise KeyError("需求版本不属于当前需求")
        coverage = None if coverage_id == "initial" else await self._store.coverage.get(
            project_id, document_id, requirement_id, selected.id, coverage_id=coverage_id)
        if coverage and coverage.requirement_version_sha256 != selected.content_sha256:
            raise ValueError("覆盖确认与固定需求版本哈希不同")
        execution = None if execution_id == "initial" else await self._store.execution.get(
            project_id, document_id, requirement_id, selected.id, coverage.id if coverage else "initial",
            execution_id=execution_id)
        if execution and (execution.requirement_version_sha256 != selected.content_sha256 or
            execution.coverage_sha256 != (coverage.sha256 if coverage else selected.content_sha256)):
            raise ValueError("执行确认与固定需求或覆盖哈希不同")
        prior_reviews = await self._store.coverage.previous(project_id, document_id, requirement_id,
            at=coverage.created_at if coverage else selected.created_at)
        def links_for(version, review):
            if review is None:
                return version.case_links
            if review.requirement_version_sha256 != version.content_sha256:
                raise ValueError("历史覆盖确认与固定需求版本哈希不同")
            return [RequirementCaseLink(**link.model_dump(include={"case_id", "case_version_id", "point_keys", "version_record_sha256"}))
                for link in review.case_links]
        current_links = links_for(selected, coverage)
        previous = {}
        for version in versions:
            if version.version >= selected.version:
                break
            for link in links_for(version, prior_reviews.get(version.id)):
                for key in link.point_keys:
                    previous.setdefault(key, {})[link.case_version_id] = link
        points = []
        for point in selected.points:
            links = [link for link in current_links if point.key in link.point_keys]
            old = list(previous.get(point.key, {}).values())
            checks = [check for check in execution.checks if point.key in check.point_keys] if execution else []
            points.append(RequirementMatrixPoint(point=point, case_links=links, previous_case_links=old,
                execution_status=point_status(checks), execution_checks=checks,
                design_status="design_covered" if links else "needs_review" if old else "uncovered"))
        return RequirementMatrix(requirement=record, version=selected, points=points,
            removed=selected.change == "removed", coverage_review=coverage, execution_review=execution)

    async def list_versions(self, project_id, document_id, requirement_id):
        await self._require_document(project_id, document_id)
        _, versions = await self._store.get_requirement(project_id, document_id, requirement_id)
        return versions
