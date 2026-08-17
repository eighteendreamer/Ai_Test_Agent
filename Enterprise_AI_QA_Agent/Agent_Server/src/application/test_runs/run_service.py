from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from src.application.projects.project_service import ProjectService
from src.application.test_cases.case_service import TestCaseService
from src.application.test_runs.run_store import TestRunStore
from src.application.test_suites.suite_service import TestSuiteService
from src.runtime.store import SessionStore
from src.schemas.run_management import (
    LeaseRecoveryResponse,
    RunClaimRequest,
    RunClaimResponse,
    RunItemClaim,
    RunItemCompleteRequest,
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
    ) -> None:
        self._store = store
        self._projects = project_service
        self._suites = suite_service
        self._cases = test_case_service
        self._sessions = session_store
        self._clock = clock or _utc_now

    async def initialize(self) -> None:
        await self._store.initialize()
        recovered = await self._store.recover_all_expired(self._clock())
        if recovered:
            logger.warning(
                "test_run_startup_leases_recovered",
                extra={"recovered_count": recovered},
            )

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
