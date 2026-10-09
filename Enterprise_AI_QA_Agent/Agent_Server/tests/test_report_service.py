from __future__ import annotations

import asyncio
from datetime import datetime

from src.application.report_service import ReportService
from src.domain.models import SessionRecord
from src.schemas.session import RuntimeMode, SessionMode, SessionStatus
from src.schemas.tool_job import ToolArtifactRecord


class _Store:
    def __init__(self, sessions: list[SessionRecord]) -> None:
        self.sessions = sessions
        self.calls: list[dict] = []

    async def list_sessions(self, **kwargs):
        self.calls.append(kwargs)
        return self.sessions


class _ToolJobs:
    def __init__(self, artifacts: list[ToolArtifactRecord]) -> None:
        self.artifacts = artifacts
        self.requested_session_ids: list[str] = []

    async def list_artifacts_for_sessions(self, session_ids: list[str]):
        self.requested_session_ids = session_ids
        requested = set(session_ids)
        return [item for item in self.artifacts if item.session_id in requested]


def _session(session_id: str, metadata: dict) -> SessionRecord:
    now = datetime.utcnow()
    return SessionRecord(
        id=session_id,
        title=f"Report {session_id}",
        status=SessionStatus.completed,
        session_mode=SessionMode.normal,
        runtime_mode=RuntimeMode.interactive,
        mode_key="code_review",
        created_at=now,
        updated_at=now,
        metadata=metadata,
    )


def _artifact(artifact_id: str, session_id: str) -> ToolArtifactRecord:
    return ToolArtifactRecord(
        id=artifact_id,
        tool_job_id="job",
        session_id=session_id,
        turn_id="turn",
        trace_id="trace",
        tool_key="report-writer",
        artifact_type="report_markdown",
        path=f"{artifact_id}.md",
        created_at=datetime.utcnow(),
    )


def test_report_page_uses_lightweight_session_data_and_batches_artifacts():
    primary = _session(
        "primary",
        {
            "code_review_report": {
                "summary": "Ready",
                "report_session_id": "report-child",
            },
            "worker_dispatches": [{"task_id": "review", "status": "completed"}],
            "verification_results": [{"status": "passed", "summary": "ok"}],
        },
    )
    extra = _session("extra", {})
    store = _Store([primary, extra])
    tool_jobs = _ToolJobs(
        [
            _artifact("primary-artifact", "primary"),
            _artifact("report-artifact", "report-child"),
        ]
    )
    service = ReportService(  # type: ignore[arg-type]
        store=store,
        tool_job_service=tool_jobs,
    )

    page = asyncio.run(service.list_reports_page(limit=1, offset=0))

    assert page.has_more is True
    assert store.calls == [{"limit": 2, "offset": 0, "mode_keys": ["code_review", "security_testing", "performance_testing"]}]
    assert tool_jobs.requested_session_ids == ["primary", "report-child"]
    assert page.items[0].session.id == "primary"
    assert page.items[0].report_session_id == "report-child"
    assert [item.id for item in page.items[0].artifacts] == ["primary-artifact"]
    assert [item.id for item in page.items[0].report_artifacts] == ["report-artifact"]


def test_real_security_report_projection_keeps_counts_and_failed_activity():
    import json
    from pathlib import Path
    evidence = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-security-report-before"
    record = json.loads((evidence / "report-record.json").read_text(encoding="utf-8"))
    detail = json.loads((evidence / "report-job.json").read_text(encoding="utf-8"))
    session = _session("actual-security-report", {})
    session.mode_key = "security_testing"
    # Use actual final fields with the real originally stale artifact: this is
    # also the compatibility sample for historical incomplete settlement.
    artifact = next(ToolArtifactRecord.model_validate(a) for a in detail["artifacts"] if a["artifact_type"] == "report_json")
    meta, workers, verifications = ReportService._prepare_test_report(session, [artifact])
    assert meta["ready"] and meta["total_findings"] == record["output"]["report"]["total_findings"] == 4
    assert meta["failed_tasks"] == 1 and meta["completed_tasks"] == 2
    assert [w["status"] for w in workers] == ["completed", "completed", "failed"]
    assert meta["settlement"] == {} and verifications == []


def test_malformed_newest_report_does_not_fall_back_to_old_success():
    artifact = _artifact("bad-report", "actual-security-report")
    artifact.artifact_type = "report_json"
    artifact.metadata = {"__content_text": "{broken"}
    session = _session(artifact.session_id, {})
    session.mode_key = "security_testing"
    meta, workers, verifications = ReportService._prepare_test_report(session, [artifact])
    assert meta["ready"] is False and not workers and not verifications
    assert meta["tool_job_id"] == artifact.tool_job_id


def test_multiple_modes_are_filtered_before_paging_in_memory():
    from src.runtime.store import InMemorySessionStore
    store = InMemorySessionStore()
    async def exercise():
        for index, mode in enumerate(["default"] * 25 + ["code_review", "security_testing", "performance_testing"]):
            session = _session(str(index), {})
            session.mode_key = mode
            await store.save_session(session)
        modes = ["code_review", "security_testing", "performance_testing"]
        first = await store.list_sessions(limit=2, mode_keys=modes)
        second = await store.list_sessions(limit=2, offset=2, mode_keys=modes)
        assert {s.id for s in first + second} == {"25", "26", "27"}
        assert len(first) == 2 and len(second) == 1
        assert await store.list_sessions(mode_keys=[]) == []
        assert len(await store.list_sessions(mode_key="code_review")) == 1
    asyncio.run(exercise())


def test_existing_actual_performance_metrics_are_read_without_rescanning():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-perf-evidence-verified-final/normal-tool-record.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    source = next(a for a in record["output"]["artifacts"] if a["type"] == "performance_metrics_json")
    artifact = _artifact("actual-perf-metrics", "actual-perf-session")
    artifact.artifact_type = source["type"]
    artifact.metadata = {"__content_text": source["content"]}
    session = _session(artifact.session_id, {})
    session.mode_key = "performance_testing"
    meta, workers, verifications = ReportService._prepare_test_report(session, [artifact])
    assert meta["ready"] and meta["verdict"] == record["output"]["verdict"] == "pass"
    assert meta.get("metrics_persistence") == record["output"].get("metrics_persistence")
    assert not workers and not verifications
