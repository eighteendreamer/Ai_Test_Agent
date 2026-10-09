from __future__ import annotations

import json
import logging
from typing import Any

from src.application.runtime.tool_job_service import ToolJobService
from src.domain.models import SessionRecord
from src.runtime.store import SessionStore
from src.schemas.report import ReportListEntry, ReportListPage
from src.schemas.session import SessionSummary
from src.schemas.tool_job import ToolArtifactRecord, ToolJobRecord, ToolJobStatus

logger = logging.getLogger(__name__)

REPORT_MODES = ["code_review", "security_testing", "performance_testing"]
REPORT_TOOLS = {"security_testing": "security-scan-runner", "performance_testing": "performance-test-runner"}


class ReportService:
    def __init__(
        self, *, store: SessionStore, tool_job_service: ToolJobService
    ) -> None:
        self._store = store
        self._tool_job_service = tool_job_service

    async def list_reports_page(
        self, limit: int = 10, offset: int = 0
    ) -> ReportListPage:
        normalized_limit = max(1, min(int(limit or 10), 100))
        normalized_offset = max(int(offset or 0), 0)
        sessions = await self._store.list_sessions(
            limit=normalized_limit + 1,
            offset=normalized_offset,
            mode_keys=REPORT_MODES,
        )
        has_more = len(sessions) > normalized_limit
        visible = sessions[:normalized_limit]

        prepared = [self._prepare_session(item) for item in visible]
        artifact_session_ids = [item[0].id for item in prepared]
        artifact_session_ids.extend(
            report_session_id
            for _, _, _, _, report_session_id in prepared
            if report_session_id
        )
        artifacts = await self._tool_job_service.list_artifacts_for_sessions(
            artifact_session_ids
        )
        artifacts_by_session: dict[str, list[ToolArtifactRecord]] = {}
        for artifact in artifacts:
            artifacts_by_session.setdefault(artifact.session_id, []).append(artifact)

        test_session_ids = [session.id for session in visible if session.mode_key in REPORT_TOOLS]
        latest_jobs = await self._tool_job_service.list_latest_jobs_for_sessions(
            test_session_ids, list(REPORT_TOOLS.values())) if test_session_ids else []
        jobs_by_session_tool = {(job.session_id, job.tool_key): job for job in latest_jobs}

        entries = []
        for session, report_meta, workers, verifications, report_session_id in prepared:
            session_artifacts = artifacts_by_session.get(session.id, [])
            if session.mode_key != "code_review":
                report_meta, workers, verifications = self._prepare_test_report(session, session_artifacts,
                    jobs_by_session_tool.get((session.id, REPORT_TOOLS[session.mode_key])))
                report_session_id = session.id if report_meta.get("ready") else None
            report_artifacts = []
            if report_session_id and report_session_id != session.id:
                report_artifacts = artifacts_by_session.get(report_session_id, [])
            entries.append(
                ReportListEntry(
                    session=self._to_summary(session),
                    artifacts=session_artifacts,
                    verifications=verifications,
                    worker_dispatches=workers,
                    report_meta=report_meta,
                    report_session_id=report_session_id,
                    report_artifacts=report_artifacts,
                )
            )

        return ReportListPage(
            items=entries,
            limit=normalized_limit,
            offset=normalized_offset,
            has_more=has_more,
        )

    @staticmethod
    def _prepare_test_report(session: SessionRecord, artifacts: list[ToolArtifactRecord], job: ToolJobRecord | None = None):
        """Bind reports to the newest execution, including attempts with no report."""
        expected = "report_json" if session.mode_key == "security_testing" else "performance_metrics_json"
        meta = {"kind": session.mode_key, "ready": False}
        if job is not None:
            meta.update(tool_job_id=job.id, job_status=job.status.value, summary=job.summary)
            if job.status not in {ToolJobStatus.completed, ToolJobStatus.partial, ToolJobStatus.failed,
                ToolJobStatus.denied, ToolJobStatus.cancelled}:
                return meta, [], []
        candidates = sorted((a for a in artifacts if a.artifact_type == expected
            and (job is None or a.tool_job_id == job.id)), key=lambda a: (a.created_at, a.id), reverse=True)
        if not candidates:
            if job is not None:
                logger.info("report_current_attempt_without_report", extra={"session_id": session.id,
                    "tool_job_id": job.id, "job_status": job.status.value, "artifact_type": expected})
            return meta, [], []
        artifact = candidates[0]
        meta["tool_job_id"] = artifact.tool_job_id
        try:
            payload = json.loads(artifact.metadata.get("__content_text") or "")
            if not isinstance(payload, dict):
                raise ValueError("report JSON is not an object")
        except (ValueError, TypeError):
            logger.warning("report_projection_unavailable", extra={"session_id": session.id,
                "artifact_id": artifact.id, "artifact_type": expected})
            return meta, [], []
        for field in ("total_tasks", "completed_tasks", "failed_tasks", "total_findings",
            "settlement", "verdict", "metrics_persistence"):
            if field in payload:
                meta[field] = payload[field]
        meta.update(kind=session.mode_key, ready=True, tool_job_id=artifact.tool_job_id)
        if session.mode_key == "performance_testing":
            meta["summary"] = str(payload.get("verdict") or "inconclusive")
            return meta, [], []
        meta["summary"] = str(payload.get("executive_summary") or "")
        workers = [{"task_id": str(item.get("task_id") or item.get("activity_id") or ""),
            "agent_key": str(item.get("agent_key") or ""), "description": str(item.get("summary") or ""),
            "status": str(item.get("action") or ""), "completed_at": item.get("completed_at")}
            for item in payload.get("activities", []) if isinstance(item, dict)]
        verdict = payload.get("verification_result")
        verifications = [{"id": artifact.id, "verifier": "security-report-evidence",
            "status": "passed" if verdict.get("passed") is True else "failed",
            "summary": str(verdict.get("summary") or "")} ] if isinstance(verdict, dict) and verdict else []
        return meta, workers, verifications

    @staticmethod
    def _prepare_session(
        session: SessionRecord,
    ) -> tuple[
        SessionRecord,
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        str | None,
    ]:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        raw_report_meta = metadata.get("code_review_report")
        report_meta = dict(raw_report_meta) if isinstance(raw_report_meta, dict) else {}
        workers = [
            dict(item)
            for item in metadata.get("worker_dispatches", [])
            if isinstance(item, dict)
        ]
        verifications = [
            dict(item)
            for item in metadata.get("verification_results", [])
            if isinstance(item, dict)
        ]
        report_session_id = str(report_meta.get("report_session_id") or "").strip()
        if not report_session_id:
            completion_worker = next(
                (item for item in workers if item.get("is_completion_worker")),
                None,
            )
            report_session_id = str(
                (completion_worker or {}).get("child_session_id") or ""
            ).strip()
        return session, report_meta, workers, verifications, report_session_id or None

    @staticmethod
    def _to_summary(session: SessionRecord) -> SessionSummary:
        return SessionSummary(
            id=session.id,
            title=session.title,
            status=session.status,
            session_mode=session.session_mode,
            runtime_mode=session.runtime_mode,
            mode_key=session.mode_key,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )
