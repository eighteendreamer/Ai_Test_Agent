from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

from src.application.projects.project_service import ProjectService
from src.application.test_cases.case_service import TestCaseService
from src.application.test_runs.run_store import TestRunStore
from src.application.test_suites.suite_service import TestSuiteService
from src.runtime.store import SessionStore
from src.schemas.run_management import (
    LeaseRecoveryResponse,
    RegressionArtifactLink,
    RegressionBatchPage,
    RegressionContext,
    RegressionEvidenceSummary,
    RegressionFailurePage,
    RegressionFailureStatus,
    RegressionFailureSummary,
    RegressionRunCreateRequest,
    RegressionVerificationSummary,
    RunClaimRequest,
    RunClaimResponse,
    RunItemClaim,
    RunItemCompleteRequest,
    RunItemApprovalWaitRequest,
    RunItemCompletion,
    RunItemHeartbeatRequest,
    RunItemLeaseRequest,
    TestCaseResultRecord,
    TestRunCreateRequest,
    TestRunDetail,
    TestRunItemRecord,
    TestRunPage,
    TestRunRecord,
    TestRunStats,
    TestRunStatus,
)
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)
REGRESSION_EVENT_SOURCE_ID_LIMIT = 100
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)(?:^|[\s;,(])(?:[a-z]:[\\/])")
_INTERNAL_LOCATION_KEYS = {
    "path",
    "uri",
    "local_path",
    "storage_uri",
    "object_uri",
    "minio_uri",
}
_REDACTED = object()


class TestRunService:
    def __init__(
        self,
        *,
        store: TestRunStore,
        project_service: ProjectService,
        suite_service: TestSuiteService,
        test_case_service: TestCaseService,
        session_store: SessionStore | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_reaper_interval_seconds: float | None = None,
    ) -> None:
        self._store = store
        self._projects = project_service
        self._suites = suite_service
        self._cases = test_case_service
        self._sessions = session_store
        self._clock = clock or _utc_now
        self._lease_reaper_interval_seconds = max(
            0.1,
            float(lease_reaper_interval_seconds or 30.0),
        )
        self._lease_reaper_stop: asyncio.Event | None = None
        self._lease_reaper_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        await self._store.initialize()
        recovered = await self._store.recover_all_expired(self._clock())
        if recovered:
            logger.warning(
                "test_run_startup_leases_recovered",
                extra={"recovered_count": recovered},
            )

    async def start_lease_reaper(self) -> None:
        """Start the process-local lease reaper after store initialization."""
        if self._lease_reaper_task is not None and not self._lease_reaper_task.done():
            return
        self._lease_reaper_stop = asyncio.Event()
        self._lease_reaper_task = asyncio.create_task(
            self._lease_reaper_loop(),
            name="test-run-lease-reaper",
        )
        logger.info(
            "test_run_lease_reaper_started",
            extra={"interval_seconds": self._lease_reaper_interval_seconds},
        )

    async def stop_lease_reaper(self) -> None:
        """Stop the lease reaper without leaving a background task behind."""
        task = self._lease_reaper_task
        stop = self._lease_reaper_stop
        self._lease_reaper_task = None
        self._lease_reaper_stop = None
        if task is None:
            return
        if stop is not None:
            stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("test_run_lease_reaper_stopped")

    async def _lease_reaper_loop(self) -> None:
        stop = self._lease_reaper_stop
        if stop is None:
            return
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._lease_reaper_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                recovered = await self._store.recover_all_expired(self._clock())
                if recovered:
                    logger.warning(
                        "test_run_expired_leases_recovered_online",
                        extra={"recovered_count": recovered},
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("test_run_lease_reaper_iteration_failed")

    async def create_run(
        self,
        suite_id: str,
        payload: TestRunCreateRequest,
        *,
        created_by: str | None = None,
    ) -> TestRunDetail:
        suite = await self._suites.get(suite_id)
        if suite.suite.status != "active":
            raise ValueError(f"Archived test suite cannot create a run: {suite_id}")
        await self._projects.require_active(suite.suite.project_id)
        case_ids = [item.case_id for item in suite.items]
        version_ids = [item.case_version_id for item in suite.items]
        cases = await self._cases.get_cases(case_ids)
        versions = await self._cases.get_versions(version_ids)
        for suite_item in suite.items:
            case = cases[suite_item.case_id]
            version = versions[suite_item.case_version_id]
            if case.project_id != suite.suite.project_id:
                raise ValueError(
                    f"Test case belongs to another project: {suite_item.case_id}"
                )
            if case.mode_key != payload.mode_key:
                raise ValueError(
                    f"Test suite contains case for another mode: {suite_item.case_id}"
                )
            if version.case_id != case.id:
                raise ValueError(
                    f"Test case version belongs to another case: {version.id}"
                )
        if payload.session_id:
            if self._sessions is None:
                raise RuntimeError("Session integration is not configured for test runs")
            session = await self._sessions.get_session(payload.session_id)
            if session is None:
                raise KeyError(f"Session not found: {payload.session_id}")
            if session.project_id != suite.suite.project_id:
                raise ValueError(
                    f"Session is not bound to test run project: {payload.session_id}"
                )
        now = self._clock()
        run = TestRunRecord(
            id=str(uuid4()),
            project_id=suite.suite.project_id,
            suite_id=suite_id,
            mode_key=payload.mode_key,
            session_id=payload.session_id,
            stats=TestRunStats(total=len(suite.items), queued=len(suite.items)),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        items = [
            TestRunItemRecord(
                id=str(uuid4()),
                run_id=run.id,
                case_id=item.case_id,
                case_version_id=item.case_version_id,
                position=item.position,
                created_at=now,
                updated_at=now,
            )
            for item in suite.items
        ]
        stored = await self._store.create_run(run, items)
        logger.info(
            "test_run_created",
            extra={
                "project_id": run.project_id,
                "run_id": run.id,
                "suite_id": suite_id,
                "mode_key": run.mode_key,
                "item_count": len(items),
            },
        )
        await self._emit(
            run,
            "test_run.created",
            {"run_id": run.id, "suite_id": suite_id, "item_count": len(items)},
        )
        return stored

    async def get(self, run_id: str) -> TestRunDetail:
        detail = await self._store.get_run(run_id)
        if detail is None:
            raise KeyError(f"Test run not found: {run_id}")
        return detail

    async def create_regression(
        self,
        parent_run_id: str,
        payload: RegressionRunCreateRequest,
        *,
        created_by: str | None = None,
    ) -> TestRunDetail:
        """从原始失败结果创建新运行，永不修改原 Run/Result。"""
        parent = await self._get_run_record(parent_run_id)
        if parent.origin != "native":
            raise ValueError(
                "Imported legacy test runs are read-only and cannot create regression runs: "
                f"{parent_run_id}"
            )
        if parent.status not in {"completed", "cancelled"}:
            raise ValueError(
                f"Only completed or cancelled test runs can create regression: {parent_run_id}"
            )
        await self._projects.require_active(parent.project_id)

        eligible_statuses = {"failed", "error", "blocked"}
        requested_ids = list(dict.fromkeys(payload.result_ids))
        candidates = await self._store.list_regression_candidates(
            run_id=parent_run_id,
            result_ids=requested_ids or None,
        )
        candidates_by_id = {
            candidate.result_id: candidate for candidate in candidates
        }
        if requested_ids:
            missing = [
                result_id
                for result_id in requested_ids
                if result_id not in candidates_by_id
            ]
            if missing:
                raise KeyError(
                    "Regression result does not belong to parent run: " + ", ".join(missing)
                )
            selected_candidates = [
                candidates_by_id[result_id] for result_id in requested_ids
            ]
            ineligible = [
                candidate.result_id
                for candidate in selected_candidates
                if candidate.status not in eligible_statuses
            ]
            if ineligible:
                raise ValueError(
                    "Regression result is not eligible (must be failed/error/blocked): "
                    + ", ".join(ineligible)
                )
        else:
            selected_candidates = [
                candidate
                for candidate in candidates
                if candidate.status in eligible_statuses
            ]
        if not selected_candidates:
            raise ValueError(
                "No failed/error/blocked results available for regression: "
                f"{parent_run_id}"
            )

        now = self._clock()
        session_id = payload.session_id or parent.session_id
        if payload.session_id:
            if self._sessions is None:
                raise RuntimeError("Session integration is not configured for regression runs")
            session = await self._sessions.get_session(payload.session_id)
            if session is None:
                raise KeyError(f"Session not found: {payload.session_id}")
            if session.project_id != parent.project_id:
                raise ValueError(
                    f"Session is not bound to regression project: {payload.session_id}"
                )

        cases: dict[str, object] = {}
        versions: dict[str, object] = {}
        if payload.version_overrides:
            override_case_ids = {
                candidate.case_id for candidate in selected_candidates
            }
            unknown_cases = [
                case_id
                for case_id in payload.version_overrides
                if case_id not in override_case_ids
            ]
            if unknown_cases:
                raise ValueError(
                    "Version override is not part of selected regression results: "
                    + ", ".join(unknown_cases)
                )
            cases = await self._cases.get_cases(list(payload.version_overrides))
            versions = await self._cases.get_versions(list(payload.version_overrides.values()))
            for case_id, version_id in payload.version_overrides.items():
                case = cases[case_id]
                version = versions.get(version_id)
                if version is None or version.case_id != case_id:
                    raise ValueError(
                        "Regression version override does not belong to case: "
                        f"{case_id}/{version_id}"
                    )
                if case.project_id != parent.project_id:
                    raise ValueError(f"Regression case belongs to another project: {case_id}")
                if (
                    case.lifecycle_status != "active"
                    or case.active_version_id != version_id
                ):
                    raise ValueError(
                        "Regression version override must reference the active version: "
                        f"{case_id}/{version_id}"
                    )

        for candidate in selected_candidates:
            if candidate.run_item_position is None:
                raise KeyError(
                    "Regression source run item not found: "
                    f"{candidate.run_item_id}"
                )
        selected_candidates.sort(
            key=lambda candidate: (
                candidate.run_item_position,
                candidate.result_id,
            )
        )
        run = TestRunRecord(
            id=str(uuid4()),
            project_id=parent.project_id,
            suite_id=parent.suite_id,
            run_kind="regression",
            mode_key=parent.mode_key,
            session_id=session_id,
            parent_run_id=parent.id,
            stats=TestRunStats(
                total=len(selected_candidates),
                queued=len(selected_candidates),
            ),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        items = []
        for position, candidate in enumerate(selected_candidates, start=1):
            items.append(
                TestRunItemRecord(
                    id=str(uuid4()),
                    run_id=run.id,
                    case_id=candidate.case_id,
                    case_version_id=payload.version_overrides.get(
                        candidate.case_id,
                        candidate.case_version_id,
                    ),
                    position=position,
                    regression_source_result_id=candidate.result_id,
                    created_at=now,
                    updated_at=now,
                )
            )
        stored = await self._store.create_run(run, items)
        source_result_ids = [
            candidate.result_id
            for candidate in selected_candidates[:REGRESSION_EVENT_SOURCE_ID_LIMIT]
        ]
        source_result_count = len(selected_candidates)
        source_result_ids_truncated = source_result_count > len(source_result_ids)
        logger.info(
            "test_regression_run_created",
            extra={
                "project_id": run.project_id,
                "run_id": run.id,
                "parent_run_id": parent.id,
                "source_result_ids": source_result_ids,
                "source_result_count": source_result_count,
                "source_result_ids_truncated": source_result_ids_truncated,
                "version_override_count": len(payload.version_overrides),
                "item_count": len(items),
            },
        )
        await self._emit(
            run,
            "test_run.regression_created",
            {
                "run_id": run.id,
                "parent_run_id": parent.id,
                "source_result_ids": source_result_ids,
                "source_result_count": source_result_count,
                "source_result_ids_truncated": source_result_ids_truncated,
                "item_count": len(items),
            },
        )
        return stored

    async def get_record(self, run_id: str) -> TestRunRecord:
        """读取运行头，供 Worker 热路径使用，避免加载完整运行明细。"""
        return await self._get_run_record(run_id)

    async def list(
        self,
        project_id: str,
        *,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> TestRunPage:
        await self._projects.get(project_id)
        items, has_more = await self._store.list_runs(
            project_id=project_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return TestRunPage(items=items, limit=limit, offset=offset, has_more=has_more)

    async def list_regression_failures(
        self,
        project_id: str,
        *,
        failure_status: RegressionFailureStatus | None,
        mode_key: str | None,
        cursor: str | None,
        limit: int,
    ) -> RegressionFailurePage:
        await self._projects.get(project_id)
        cursor_created_at, cursor_id = _decode_regression_cursor(cursor)
        records, has_more = await self._store.list_regression_failures(
            project_id=project_id,
            failure_status=failure_status,
            mode_key=(mode_key or "").strip() or None,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit,
        )
        cases = await self._cases.get_cases([record.case_id for record in records])
        items = [
            RegressionFailureSummary(
                **record.model_dump(mode="python"),
                case_key=cases[record.case_id].case_key,
                case_title=cases[record.case_id].title,
            )
            for record in records
        ]
        next_cursor = (
            _encode_regression_cursor(records[-1].failed_at, records[-1].source_result_id)
            if has_more and records
            else None
        )
        return RegressionFailurePage(
            items=items,
            limit=limit,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def get_regression_context(self, result_id: str) -> RegressionContext:
        result = await self._store.get_result(result_id)
        if result is None:
            raise KeyError(f"Test result not found: {result_id}")
        if result.status not in {"failed", "error", "blocked"}:
            raise ValueError(
                "Regression context is only available for failed/error/blocked results: "
                f"{result_id}"
            )
        run = await self._get_run_record(result.run_id)
        verification_ids = set(result.verification_ids)
        verification_values = result.actual.get("verification_results", [])
        verifications = []
        if isinstance(verification_values, list):
            for value in verification_values:
                if not isinstance(value, dict):
                    continue
                verification_id = str(value.get("id") or "").strip()
                status = str(value.get("status") or "").strip()
                if not verification_id or verification_id not in verification_ids or not status:
                    continue
                verifications.append(
                    RegressionVerificationSummary(
                        id=verification_id,
                        verifier=_public_text(
                            value.get("verifier"),
                            fallback="verification",
                        ),
                        status=status,
                        summary=_public_text(
                            value.get("summary"),
                            fallback="Verification details redacted",
                        ),
                        assertion_count=_non_negative_int(value.get("assertion_count")),
                        passed_count=_non_negative_int(value.get("passed_count")),
                        failed_count=_non_negative_int(value.get("failed_count")),
                        created_at=_optional_datetime(value.get("created_at")),
                    )
                )
        content_prefix = None
        if run.session_id:
            content_prefix = (
                f"/api/v1/sessions/{quote(run.session_id, safe='')}/artifacts"
            )
        return RegressionContext(
            source_result_id=result.id,
            source_run_id=result.run_id,
            case_id=result.case_id,
            case_version_id=result.case_version_id,
            mode_key=run.mode_key,
            failure_status=result.status,
            summary=_public_text(result.summary, fallback="Failure details redacted"),
            error_message=(
                _public_text(
                    result.error_message,
                    fallback="Internal location redacted",
                )
                if result.error_message
                else None
            ),
            metrics=_public_metrics(result.metrics),
            evidence=[
                RegressionEvidenceSummary(
                    evidence_type=evidence.evidence_type,
                    evidence_id=evidence.evidence_id,
                    label=_public_text(
                        evidence.label,
                        fallback=evidence.evidence_type,
                    ),
                )
                for evidence in result.evidence_refs
            ],
            artifacts=[
                RegressionArtifactLink(
                    artifact_id=artifact_id,
                    content_url=(
                        f"{content_prefix}/{quote(artifact_id, safe='')}/content"
                        if content_prefix
                        else None
                    ),
                )
                for artifact_id in result.artifact_ids
            ],
            verifications=verifications,
            failed_at=result.created_at,
        )

    async def list_regression_batches(
        self,
        result_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> RegressionBatchPage:
        result = await self._store.get_result(result_id)
        if result is None:
            raise KeyError(f"Test result not found: {result_id}")
        if result.status not in {"failed", "error", "blocked"}:
            raise ValueError(
                "Regression batches are only available for failed/error/blocked results: "
                f"{result_id}"
            )
        cursor_created_at, cursor_id = _decode_regression_batch_cursor(cursor)
        records, has_more = await self._store.list_regression_batches(
            source_result_id=result_id,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit,
        )
        next_cursor = (
            _encode_regression_batch_cursor(
                records[-1].created_at,
                records[-1].run_item_id,
            )
            if has_more and records
            else None
        )
        return RegressionBatchPage(
            source_result_id=result_id,
            items=records,
            limit=limit,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    async def claim(self, run_id: str, payload: RunClaimRequest) -> RunClaimResponse:
        run = await self._get_run_record(run_id)
        leases = await self._store.claim_items(
            run_id=run_id,
            worker_id=payload.worker_id,
            limit=payload.limit,
            lease_seconds=payload.lease_seconds,
            now=self._clock(),
        )
        if not leases:
            return RunClaimResponse()
        case_ids = [item.case_id for item, _ in leases]
        version_ids = [item.case_version_id for item, _ in leases]
        cases = await self._cases.get_cases(case_ids)
        versions = await self._cases.get_versions(version_ids)
        claims = [
            RunItemClaim(
                item=item,
                attempt=attempt,
                lease_token=attempt.lease_token,
                case=cases[item.case_id],
                version=versions[item.case_version_id],
            )
            for item, attempt in leases
        ]
        logger.info(
            "test_run_items_claimed",
            extra={
                "project_id": run.project_id,
                "run_id": run_id,
                "worker_id": payload.worker_id,
                "claim_count": len(claims),
            },
        )
        await self._emit(
            run,
            "test_run.items_claimed",
            {
                "run_id": run_id,
                "worker_id": payload.worker_id,
                "item_ids": [claim.item.id for claim in claims],
            },
        )
        return RunClaimResponse(claims=claims)

    async def start_item(
        self,
        item_id: str,
        payload: RunItemLeaseRequest,
    ) -> TestRunItemRecord:
        item = await self._store.start_item(item_id, payload.lease_token, self._clock())
        await self._emit_for_item(item, "test_run.item_started")
        return item

    async def get_item(self, item_id: str) -> TestRunItemRecord:
        item = await self._store.get_item(item_id)
        if item is None:
            raise KeyError(f"Test run item not found: {item_id}")
        return item

    async def get_result(self, result_id: str) -> TestCaseResultRecord:
        result = await self._store.get_result(result_id)
        if result is None:
            raise KeyError(f"Test result not found: {result_id}")
        return result

    async def heartbeat_item(
        self,
        item_id: str,
        payload: RunItemHeartbeatRequest,
    ) -> TestRunItemRecord:
        item = await self._store.heartbeat_item(
            item_id,
            payload.lease_token,
            payload.lease_seconds,
            self._clock(),
        )
        return item

    async def mark_waiting_approval(
        self,
        item_id: str,
        payload: RunItemApprovalWaitRequest,
    ) -> TestRunItemRecord:
        item = await self._store.mark_waiting_approval(
            item_id,
            payload.lease_token,
            payload.approval_id,
            payload.tool_job_id,
            self._clock(),
        )
        logger.info(
            "test_run_item_waiting_approval",
            extra={
                "run_id": item.run_id,
                "run_item_id": item.id,
                "approval_id": payload.approval_id,
                "tool_job_id": payload.tool_job_id,
                "approval_scope_hash": payload.approval_scope_hash,
            },
        )
        await self._emit_for_item(item, "test_run.item_waiting_approval")
        return item

    async def resume_waiting_approval(
        self,
        item_id: str,
        approval_id: str,
        *,
        lease_seconds: int = 90,
    ) -> TestRunItemRecord:
        item = await self._store.resume_waiting_approval(
            item_id,
            approval_id,
            lease_seconds,
            self._clock(),
        )
        logger.info(
            "test_run_item_approval_resume_requested",
            extra={
                "run_id": item.run_id,
                "run_item_id": item.id,
                "approval_id": approval_id,
            },
        )
        await self._emit_for_item(item, "test_run.item_approval_resume_requested")
        return item

    async def complete_item(
        self,
        item_id: str,
        payload: RunItemCompleteRequest,
    ) -> TestCaseResultRecord:
        content = payload.model_dump(mode="json", exclude={"lease_token"})
        payload_hash = hashlib.sha256(
            json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        completion = RunItemCompletion(
            **content,
            payload_hash=payload_hash,
        )
        result = await self._store.complete_item(
            item_id,
            payload.lease_token,
            completion,
            self._clock(),
        )
        logger.info(
            "test_run_item_completed",
            extra={
                "run_id": result.run_id,
                "run_item_id": item_id,
                "case_version_id": result.case_version_id,
                "result_id": result.id,
                "status": result.status,
            },
        )
        run = await self._get_run_record(result.run_id)
        await self._emit(
            run,
            "test_run.item_completed",
            {
                "run_id": result.run_id,
                "run_item_id": item_id,
                "result_id": result.id,
                "status": result.status,
            },
        )
        return result

    async def recover_expired(self, run_id: str) -> LeaseRecoveryResponse:
        run = await self._get_run_record(run_id)
        recovered = await self._store.recover_expired(run_id, self._clock())
        logger.info(
            "test_run_expired_leases_recovered",
            extra={"run_id": run_id, "recovered_count": recovered},
        )
        if recovered:
            await self._emit(
                run,
                "test_run.leases_recovered",
                {"run_id": run_id, "recovered_count": recovered},
            )
        return LeaseRecoveryResponse(recovered_count=recovered)

    async def cancel(self, run_id: str, reason: str) -> TestRunDetail:
        detail = await self._store.cancel_run(run_id, reason, self._clock())
        logger.info(
            "test_run_cancelled",
            extra={"run_id": run_id, "project_id": detail.run.project_id},
        )
        await self._emit(
            detail.run,
            "test_run.cancelled",
            {"run_id": run_id, "reason": reason},
        )
        return detail

    async def _emit_for_item(self, item: TestRunItemRecord, event_type: str) -> None:
        run = await self._get_run_record(item.run_id)
        await self._emit(
            run,
            event_type,
            {"run_id": item.run_id, "run_item_id": item.id, "status": item.status},
        )

    async def _emit(
        self,
        run: TestRunRecord,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        if self._sessions is None or not run.session_id:
            return
        await self._sessions.append_event(
            run.session_id,
            ExecutionEvent(
                type=event_type,
                session_id=run.session_id,
                timestamp=self._clock(),
                payload=payload,
            ),
        )

    async def _get_run_record(self, run_id: str) -> TestRunRecord:
        run = await self._store.get_run_record(run_id)
        if run is None:
            raise KeyError(f"Test run not found: {run_id}")
        return run


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _contains_internal_location(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        "minio://" in normalized
        or "file://" in normalized
        or bool(_WINDOWS_PATH_PATTERN.search(value))
    )


def _public_text(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or _contains_internal_location(text):
        return fallback
    return text


def _public_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    value = _public_metric_value(metrics)
    return value if isinstance(value, dict) else {}


def _public_metric_value(value: Any) -> Any:
    if isinstance(value, dict):
        public = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized_key = key.strip().lower()
            if (
                normalized_key in _INTERNAL_LOCATION_KEYS
                or normalized_key.endswith("_path")
                or normalized_key.endswith("_uri")
            ):
                continue
            public_child = _public_metric_value(child)
            if public_child is not _REDACTED:
                public[key] = public_child
        return public
    if isinstance(value, list):
        public_items = [_public_metric_value(item) for item in value]
        return [item for item in public_items if item is not _REDACTED]
    if isinstance(value, str) and _contains_internal_location(value):
        return _REDACTED
    return value


def _encode_regression_cursor(created_at: datetime, result_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "result_id": result_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_regression_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
        )
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        result_id = str(payload["result_id"]).strip()
        if created_at.tzinfo is None or not result_id:
            raise ValueError("cursor fields are incomplete")
        return created_at, result_id
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ValueError("Invalid regression pagination cursor") from exc


def _encode_regression_batch_cursor(created_at: datetime, run_item_id: str) -> str:
    payload = json.dumps(
        {"created_at": created_at.isoformat(), "run_item_id": run_item_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_regression_batch_cursor(
    cursor: str | None,
) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
        )
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        run_item_id = str(payload["run_item_id"]).strip()
        if created_at.tzinfo is None or not run_item_id:
            raise ValueError("cursor fields are incomplete")
        return created_at, run_item_id
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ValueError("Invalid regression batch pagination cursor") from exc
