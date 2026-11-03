from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from src.core.config import Settings
from src.application.test_runs.timing import add_active_duration, add_waiting_duration, elapsed_ms
from src.application.test_runs.dependencies import (dependencies_ready, validate_run_dependencies,
    dependency_failures, blocked_dependency_records)
from src.application.test_runs.environment_restrictions import restriction_reviews, blocked_environment_records
from src.application.test_runs.environment_admission import create_environment_admission, fixed_environment_admission
from src.application.test_runs.plan_snapshot_identity import (claim_plan_snapshot_identity,
    validate_claimed_plan_snapshot, initial_plan_snapshots, run_with_claimed_snapshot, execution_attempt,
    ResultExecutionContext, result_execution_contexts,
    validate_supplied_snapshot)
from src.application.test_runs.plan_revision import build_run_plan_revision
from src.application.test_runs.plan_revision_lock import lock_plan_projection
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_identifiers import (
    derived_postgres_table,
    postgres_index_name,
    postgres_relation_parts,
    postgres_relation_sql,
)
from src.infrastructure.storage_utils import ensure_utc_datetime
from src.schemas.session import ExecutionEvent, ToolApprovalRequest
from src.schemas.controlled_case_admission import CONTROLLED_CASE_ADMISSION_KEY
from src.schemas.suite_management import TestSuiteBundle
from src.schemas.tool_job import ToolJobRecord
from src.schemas.run_management import (
    LatestRegressionRecord,
    RegressionBatchRecord,
    RegressionFailureRecord,
    RegressionFailureStatus,
    RegressionSourceRecord,
    RunItemCompletion,
    RunPlanRevisionRequest,
    TestCaseResultRecord,
    TestRunAttemptRecord,
    TestRunDetail,
    TestRunItemRecord,
    TestRunPlanRevisionRecord,
    TestRunRecord,
    TestRunStats,
    TestRunStatus,
)


logger = logging.getLogger(__name__)
TERMINAL_ITEM_STATUSES = {
    "passed",
    "failed",
    "error",
    "blocked",
    "skipped",
    "cancelled",
}
ACTIVE_ITEM_STATUSES = {"claimed", "running"}


def is_controlled_case_attempt(attempt: TestRunAttemptRecord) -> bool:
    # The durable reservation, not mutable approval metadata or a known
    # payload version, determines which transaction owns the original Attempt.
    return attempt.checkpoint_key == CONTROLLED_CASE_ADMISSION_KEY


def require_generic_checkpoint_write(attempt: TestRunAttemptRecord, checkpoint_key: str) -> None:
    # The reservation owns task/input/budget/outbox identities in one atomic
    # writer. The generic checkpoint API cannot install or replace that fact.
    if is_controlled_case_attempt(attempt) or checkpoint_key == CONTROLLED_CASE_ADMISSION_KEY:
        logger.warning("controlled_case_checkpoint_atomic_writer_required", extra={"run_id": attempt.run_id,
            "run_item_id": attempt.run_item_id, "attempt_id": attempt.id,
            "recovery_reason": "controlled_case_admission_reserved"})
        raise ValueError("Controlled case admission checkpoint requires its atomic writer")


def require_generic_result_write(attempt: TestRunAttemptRecord) -> None:
    # Like the checkpoint reservation, the original admission cannot be
    # completed by a writer which does not settle its AgentTask in the same
    # transaction. Do not parse/upgrade the reserved payload or add a bypass.
    if is_controlled_case_attempt(attempt):
        logger.warning("controlled_case_result_atomic_writer_required", extra={"run_id": attempt.run_id,
            "run_item_id": attempt.run_item_id, "attempt_id": attempt.id,
            "recovery_reason": "controlled_case_atomic_settlement_required"})
        raise ValueError("Controlled case result requires its atomic settlement")


def require_generic_approval_resume(attempt: TestRunAttemptRecord) -> None:
    # Like result/checkpoint writes, resume must not grant a RunItem-only lease
    # for a reserved execution. Its completed producer remains immutable; only
    # the dedicated continuation may establish the next dual-owner identity.
    if is_controlled_case_attempt(attempt):
        logger.warning("controlled_case_approval_continuation_required", extra={"run_id": attempt.run_id,
            "run_item_id": attempt.run_item_id, "attempt_id": attempt.id,
            "recovery_reason": "controlled_case_dedicated_approval_required", "automatic_replay": False})
        raise ValueError("Controlled case requires its dedicated approval continuation")


def can_requeue_expired_attempt(attempt: TestRunAttemptRecord) -> bool:
    # The reservation also owns the original execution identity. Expiry is not
    # permission to move its checkpoint to a fresh generic execution attempt.
    # Match only the reserved key; unknown payload versions remain untouched.
    if is_controlled_case_attempt(attempt):
        logger.warning("controlled_case_expiry_requires_reconciliation", extra={"run_id": attempt.run_id,
            "run_item_id": attempt.run_item_id, "attempt_id": attempt.id,
            "recovery_reason": "controlled_case_original_attempt_preserved", "automatic_replay": False})
        return False
    return True


class TestRunStore(Protocol):
    async def initialize(self) -> None: ...
    async def initialize_plan_revision_storage(self) -> None: ...
    async def project_execution_counts(self, project_id: str) -> tuple[TestRunStats, dict[str, int]]: ...
    async def create_run(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail: ...
    async def create_planned_run(self, suite: TestSuiteBundle, run: TestRunRecord,
        items: list[TestRunItemRecord], *, suite_store, outbox_table: str | None,
        event_key: str, stream: str | None, payload: dict[str, object]) -> TestRunDetail: ...
    async def create_run_with_outbox(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
        *,
        outbox_table: str,
        event_key: str,
        stream: str,
        payload: dict[str, object],
    ) -> TestRunDetail: ...
    async def get_run(self, run_id: str) -> TestRunDetail | None: ...
    async def revise_plan(self, *, run_id: str, request: RunPlanRevisionRequest,
        request_sha256: str, candidate_run: TestRunRecord, candidate_items: list[TestRunItemRecord],
        now: datetime) -> TestRunPlanRevisionRecord: ...
    async def list_plan_revisions(self, run_id: str) -> list[TestRunPlanRevisionRecord]: ...
    async def get_execution_detail(self, run_id: str, item_id: str, attempt_id: str,
        *, supplied_run: TestRunRecord | None = None) -> TestRunDetail: ...
    async def get_result_execution_detail(self, run_id: str, result_id: str) -> TestRunDetail: ...
    async def get_result_execution_contexts(self, run_id: str, result_ids: list[str]
        ) -> dict[str, ResultExecutionContext]: ...
    async def get_run_record(self, run_id: str) -> TestRunRecord | None: ...
    async def get_result(self, result_id: str) -> TestCaseResultRecord | None: ...
    async def get_item(self, item_id: str) -> TestRunItemRecord | None: ...
    async def get_latest_attempt(self, item_id: str) -> TestRunAttemptRecord | None: ...
    async def find_projected_legacy_smoke_run_ids(self, run_ids: list[str]) -> set[str]: ...
    async def list_regression_candidates(
        self,
        *,
        run_id: str,
        result_ids: list[str] | None,
    ) -> list[RegressionSourceRecord]: ...
    async def list_regression_failures(
        self,
        *,
        project_id: str,
        failure_status: RegressionFailureStatus | None,
        mode_key: str | None,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionFailureRecord], bool]: ...
    async def list_regression_batches(
        self,
        *,
        source_result_id: str,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionBatchRecord], bool]: ...
    async def list_runs(
        self,
        *,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]: ...
    async def claim_items(
        self,
        *,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]: ...
    async def start_item(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord: ...
    async def mark_waiting_resource(
        self, item_id: str, lease_token: str, reason: str, now: datetime,
    ) -> TestRunItemRecord: ...
    async def heartbeat_item(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord: ...
    async def save_checkpoint(
        self,
        item_id: str,
        lease_token: str,
        checkpoint_key: str,
        checkpoint_payload: dict[str, object],
        checkpoint_version: int | None,
        now: datetime,
    ) -> TestRunAttemptRecord: ...
    async def mark_waiting_approval(
        self,
        item_id: str,
        lease_token: str,
        approval_id: str,
        tool_job_id: str,
        now: datetime,
    ) -> TestRunItemRecord: ...
    async def resume_waiting_approval(
        self,
        item_id: str,
        approval_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord: ...
    async def list_waiting_approval_items(
        self, *, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]: ...
    async def list_cancelled_resource_items(
        self, *, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]: ...
    async def mark_cancelled_resources_reconciled(
        self,
        item_id: str,
        now: datetime,
    ) -> TestRunItemRecord: ...
    async def complete_denied_approval(
        self,
        item_id: str,
        approval_id: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord: ...
    async def complete_item(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord: ...
    async def recover_expired(self, run_id: str, now: datetime) -> int: ...
    async def recover_all_expired(self, now: datetime) -> int: ...
    async def cancel_run(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail: ...
    async def count_by_project(self, project_id: str) -> int: ...


class InMemoryTestRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, TestRunRecord] = {}
        self._plan_snapshots: dict[str, dict] = {}
        self._items: dict[str, TestRunItemRecord] = {}
        self._item_ids_by_run: dict[str, list[str]] = {}
        self._attempts: dict[str, TestRunAttemptRecord] = {}
        self._attempt_ids_by_run: dict[str, list[str]] = {}
        self._results: dict[str, TestCaseResultRecord] = {}
        self._result_ids_by_run: dict[str, list[str]] = {}
        self._plan_revisions_by_run: dict[str, list[TestRunPlanRevisionRecord]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def initialize_plan_revision_storage(self) -> None:
        return None

    async def project_execution_counts(self, project_id: str) -> tuple[TestRunStats, dict[str, int]]:
        async with self._lock:
            run_ids = {run.id for run in self._runs.values() if run.project_id == project_id}
            counts: Counter[str] = Counter()
            reasons: Counter[str] = Counter()
            for item in self._items.values():
                if item.run_id not in run_ids:
                    continue
                counts[item.status] += 1
                if item.status == "waiting_resource":
                    reasons[item.waiting_reason or "resource_unavailable"] += 1
            return TestRunStats(total=sum(counts.values()), **counts), dict(reasons)

    async def create_run(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail:
        validate_run_dependencies(run, items)
        async with self._lock:
            if run.id in self._runs:
                raise ValueError(f"Test run already exists: {run.id}")
            stored_run = run.model_copy(deep=True)
            stored_items = [item.model_copy(deep=True) for item in items]
            self._runs[run.id] = stored_run
            self._plan_snapshots[run.id] = initial_plan_snapshots(run)
            self._item_ids_by_run[run.id] = [item.id for item in stored_items]
            self._attempt_ids_by_run[run.id] = []
            self._result_ids_by_run[run.id] = []
            self._plan_revisions_by_run[run.id] = []
            for item in stored_items:
                self._items[item.id] = item
            self._refresh_run(run.id, run.created_at)
            return self._detail(run.id)

    async def create_planned_run(self, suite: TestSuiteBundle, run: TestRunRecord,
        items: list[TestRunItemRecord], *, suite_store, outbox_table: str | None,
        event_key: str, stream: str | None, payload: dict[str, object]) -> TestRunDetail:
        from src.application.test_suites.suite_store import InMemoryTestSuiteStore
        from src.application.test_runs.plan_mapping import validate_plan_bundle, validate_regression_parent
        if not isinstance(suite_store, InMemoryTestSuiteStore):
            raise ValueError("内存计划运行必须使用同类套件存储")
        validate_plan_bundle(suite, run, items)
        async with suite_store._lock, self._lock:
            existing = self._runs.get(run.id)
            if existing:
                if existing.creation_hash != run.creation_hash:
                    raise ValueError("相同创建请求已经关联其他计划或执行参数")
                return self._detail(run.id)
            if suite.suite.id in suite_store._suites:
                raise ValueError("固定套件身份已被其他记录使用")
            if run.plan_snapshot.regression:
                source = run.plan_snapshot.regression
                parent = self._runs.get(source.parent_run_id)
                source_results = {key: self._results[key] for key in source.source_results.values() if key in self._results}
                historical_parent = None
                if parent is not None:
                    parent_detail = self._detail(source.parent_run_id)
                    contexts = result_execution_contexts(parent_detail,
                        self._plan_snapshots.get(source.parent_run_id, {}), list(source.source_results.values()))
                    for context in contexts.values():
                        fixed_parent = context.run
                        if (historical_parent is not None
                            and historical_parent.plan_snapshot != fixed_parent.plan_snapshot):
                            raise ValueError("一个回归运行不能混合引用不同计划快照的结果")
                        historical_parent = fixed_parent
                validate_regression_parent(run, historical_parent or parent, source_results)
            suite_store._suites[suite.suite.id] = suite.suite.model_copy(deep=True)
            suite_store._items_by_suite[suite.suite.id] = [i.model_copy(deep=True) for i in suite.items]
            self._runs[run.id] = run.model_copy(deep=True)
            self._plan_snapshots[run.id] = initial_plan_snapshots(run)
            self._item_ids_by_run[run.id] = [i.id for i in items]
            self._attempt_ids_by_run[run.id], self._result_ids_by_run[run.id] = [], []
            self._plan_revisions_by_run[run.id] = []
            self._items.update({i.id: i.model_copy(deep=True) for i in items})
            return self._detail(run.id)

    async def create_run_with_outbox(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
        *,
        outbox_table: str,
        event_key: str,
        stream: str,
        payload: dict[str, object],
    ) -> TestRunDetail:
        # The in-memory store has no external transaction boundary.  Keeping
        # this method in the protocol lets the service use one code path while
        # PostgreSQL performs both writes on one connection.
        return await self.create_run(run, items)

    async def get_run(self, run_id: str) -> TestRunDetail | None:
        if run_id not in self._runs:
            return None
        return self._detail(run_id)

    async def revise_plan(self, *, run_id: str, request: RunPlanRevisionRequest,
        request_sha256: str, candidate_run: TestRunRecord, candidate_items: list[TestRunItemRecord],
        now: datetime) -> TestRunPlanRevisionRecord:
        async with self._lock:
            current_run = self._runs.get(run_id)
            if current_run is None:
                raise KeyError(f"Test run not found: {run_id}")
            history = self._plan_revisions_by_run.setdefault(run_id, [])
            existing = next((record for record in history if record.idempotency_key == request.idempotency_key), None)
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise ValueError("相同计划修订幂等键已用于不同请求")
                return existing.model_copy(deep=True)
            prior_ids = set(self._item_ids_by_run.get(run_id, []))
            current_items = [self._items[item_id] for item_id in self._item_ids_by_run.get(run_id, [])]
            attempted_ids = {attempt.run_item_id for attempt in self._attempts.values() if attempt.run_id == run_id}
            updated_run, updated_items, revision = build_run_plan_revision(
                current_run=current_run,
                current_items=current_items,
                attempted_item_ids=attempted_ids,
                candidate_run=candidate_run,
                candidate_items=candidate_items,
                request=request,
                request_sha256=request_sha256,
                now=now,
            )
            updated_ids = [item.id for item in updated_items]
            if len(updated_ids) != len(set(updated_ids)) or not prior_ids.issubset(set(updated_ids)):
                raise RuntimeError("计划修订意外丢失或重复了历史 RunItem")
            self._runs[run_id] = updated_run
            self._item_ids_by_run[run_id] = updated_ids
            self._items.update({item.id: item for item in updated_items})
            self._plan_snapshots.setdefault(run_id, {}).update(initial_plan_snapshots(updated_run))
            self._refresh_run(run_id, now)
            history.append(revision)
            logger.info("test_run_plan_revision_committed", extra={"run_id": run_id,
                "revision": revision.revision, "added_item_count": len(revision.added_item_ids),
                "updated_item_count": len(revision.updated_item_ids),
                "cancelled_item_count": len(revision.cancelled_item_ids)})
            return revision.model_copy(deep=True)

    async def list_plan_revisions(self, run_id: str) -> list[TestRunPlanRevisionRecord]:
        async with self._lock:
            if run_id not in self._runs:
                raise KeyError(f"Test run not found: {run_id}")
            return [record.model_copy(deep=True) for record in self._plan_revisions_by_run.get(run_id, [])]

    async def get_run_record(self, run_id: str) -> TestRunRecord | None:
        run = self._runs.get(run_id)
        return run.model_copy(deep=True) if run else None

    async def get_execution_detail(self, run_id: str, item_id: str, attempt_id: str,
        *, supplied_run: TestRunRecord | None = None) -> TestRunDetail:
        async with self._lock:
            detail = self._detail(run_id)
            attempt = execution_attempt(detail, item_id, attempt_id)
            fixed = self._run_for_attempt(detail.run, attempt)
            validate_supplied_snapshot(supplied_run, detail.run, fixed)
            detail.run = fixed
            return detail

    async def get_result_execution_detail(self, run_id: str, result_id: str) -> TestRunDetail:
        async with self._lock:
            detail = self._detail(run_id)
            contexts = result_execution_contexts(detail, self._plan_snapshots.get(run_id, {}), [result_id])
            detail.run = contexts[result_id].run
            return detail

    async def get_result_execution_contexts(self, run_id: str, result_ids: list[str]
        ) -> dict[str, ResultExecutionContext]:
        async with self._lock:
            detail = self._detail(run_id)
            return result_execution_contexts(detail, self._plan_snapshots.get(run_id, {}), result_ids)

    def _run_for_attempt(self, run, attempt):
        identity = attempt.plan_snapshot_identity
        archives = self._plan_snapshots.get(run.id, {})
        archive = archives.get(identity.sha256) if identity else None
        return run_with_claimed_snapshot(run, attempt, archive,
            archive_present=identity is not None and identity.sha256 in archives)

    async def get_result(self, result_id: str) -> TestCaseResultRecord | None:
        result = self._results.get(result_id)
        return result.model_copy(deep=True) if result else None

    async def get_item(self, item_id: str) -> TestRunItemRecord | None:
        item = self._items.get(item_id)
        return item.model_copy(deep=True) if item else None

    async def get_latest_attempt(self, item_id: str) -> TestRunAttemptRecord | None:
        async with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return None
            attempts = [
                self._attempts[attempt_id]
                for attempt_id in self._attempt_ids_by_run.get(item.run_id, [])
                if self._attempts[attempt_id].run_item_id == item_id
            ]
            if not attempts:
                return None
            return max(attempts, key=lambda attempt: attempt.attempt_no).model_copy(deep=True)

    async def find_projected_legacy_smoke_run_ids(self, run_ids: list[str]) -> set[str]:
        wanted = {str(run_id).strip() for run_id in run_ids if str(run_id).strip()}
        if not wanted:
            return set()
        projected: set[str] = set()
        for result in self._results.values():
            actual = result.actual if isinstance(result.actual, dict) else {}
            run_result = actual.get("run_result")
            candidate = (
                run_result.get("run_id")
                if isinstance(run_result, dict)
                else actual.get("legacy_smoke_run_id")
            )
            if isinstance(candidate, str) and candidate in wanted:
                projected.add(candidate)
        return projected

    async def list_regression_candidates(
        self,
        *,
        run_id: str,
        result_ids: list[str] | None,
    ) -> list[RegressionSourceRecord]:
        async with self._lock:
            selected_ids = (
                result_ids
                if result_ids is not None
                else self._result_ids_by_run.get(run_id, [])
            )
            candidates = []
            for result_id in selected_ids:
                result = self._results.get(result_id)
                if result is None or result.run_id != run_id:
                    continue
                if result_ids is None and result.status not in {
                    "failed",
                    "error",
                    "blocked",
                }:
                    continue
                item = self._items.get(result.run_item_id)
                candidates.append(
                    RegressionSourceRecord(
                        result_id=result.id,
                        run_item_id=result.run_item_id,
                        case_id=result.case_id,
                        case_version_id=result.case_version_id,
                        status=result.status,
                        run_item_position=item.position if item else None,
                    )
                )
            candidates.sort(
                key=lambda candidate: (
                    candidate.run_item_position
                    if candidate.run_item_position is not None
                    else float("inf"),
                    candidate.result_id,
                )
            )
            return candidates

    async def list_regression_failures(
        self,
        *,
        project_id: str,
        failure_status: RegressionFailureStatus | None,
        mode_key: str | None,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionFailureRecord], bool]:
        async with self._lock:
            child_items_by_source: dict[str, list[TestRunItemRecord]] = {}
            for item in self._items.values():
                if item.regression_source_result_id:
                    child_items_by_source.setdefault(
                        item.regression_source_result_id,
                        [],
                    ).append(item)
            records = []
            for result in self._results.values():
                if result.status not in {"failed", "error", "blocked"}:
                    continue
                run = self._runs.get(result.run_id)
                if run is None or run.project_id != project_id:
                    continue
                if failure_status and result.status != failure_status:
                    continue
                if mode_key and run.mode_key != mode_key:
                    continue
                if cursor_created_at is not None and (
                    result.created_at,
                    result.id,
                ) >= (cursor_created_at, cursor_id or ""):
                    continue
                children = child_items_by_source.get(result.id, [])
                children.sort(
                    key=lambda item: (
                        self._runs[item.run_id].created_at,
                        item.updated_at,
                        item.id,
                    ),
                    reverse=True,
                )
                latest = children[0] if children else None
                latest_run = self._runs.get(latest.run_id) if latest else None
                latest_result = (
                    self._results.get(latest.result_id)
                    if latest and latest.result_id
                    else None
                )
                records.append(
                    RegressionFailureRecord(
                        source_result_id=result.id,
                        source_run_id=run.id,
                        source_run_status=run.status,
                        source_run_created_at=run.created_at,
                        case_id=result.case_id,
                        case_version_id=result.case_version_id,
                        mode_key=run.mode_key,
                        failure_status=result.status,
                        summary=result.summary,
                        error_message=result.error_message,
                        failed_at=result.created_at,
                        evidence_count=len(result.evidence_refs),
                        artifact_count=len(result.artifact_ids),
                        verification_count=len(result.verification_ids),
                        has_actual=bool(result.actual),
                        regression_batch_count=len(children),
                        latest_regression=(
                            LatestRegressionRecord(
                                run_id=latest_run.id,
                                run_status=latest_run.status,
                                run_item_id=latest.id,
                                item_status=latest.status,
                                result_id=latest.result_id,
                                result_status=(
                                    latest_result.status if latest_result else None
                                ),
                                case_version_id=latest.case_version_id,
                                created_at=latest_run.created_at,
                                updated_at=latest.updated_at,
                            )
                            if latest and latest_run
                            else None
                        ),
                    )
                )
            records.sort(
                key=lambda record: (record.failed_at, record.source_result_id),
                reverse=True,
            )
            selected = records[: limit + 1]
            return selected[:limit], len(selected) > limit

    async def list_regression_batches(
        self,
        *,
        source_result_id: str,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionBatchRecord], bool]:
        records = []
        for item in self._items.values():
            if item.regression_source_result_id != source_result_id:
                continue
            run = self._runs.get(item.run_id)
            if run is None:
                continue
            if cursor_created_at is not None and (
                run.created_at,
                item.id,
            ) >= (cursor_created_at, cursor_id or ""):
                continue
            result = self._results.get(item.result_id) if item.result_id else None
            records.append(
                RegressionBatchRecord(
                    run_id=run.id,
                    run_kind=run.run_kind,
                    run_status=run.status,
                    parent_run_id=run.parent_run_id,
                    run_item_id=item.id,
                    item_status=item.status,
                    result_id=item.result_id,
                    result_status=result.status if result else None,
                    case_version_id=item.case_version_id,
                    created_at=run.created_at,
                    updated_at=item.updated_at,
                )
            )
        records.sort(
            key=lambda record: (record.created_at, record.run_item_id),
            reverse=True,
        )
        selected = records[: limit + 1]
        return selected[:limit], len(selected) > limit

    async def list_runs(
        self,
        *,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]:
        runs = [run for run in self._runs.values() if run.project_id == project_id]
        if status:
            runs = [run for run in runs if run.status == status]
        runs.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        selected = runs[offset : offset + limit + 1]
        return [run.model_copy(deep=True) for run in selected[:limit]], len(selected) > limit

    async def claim_items(
        self,
        *,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]:
        async with self._lock:
            run = self._require_run(run_id)
            if run.status in {"completed", "cancelled"}:
                return []
            blocked_count = 0
            while True:
                changed = False
                for item_id in self._item_ids_by_run[run_id]:
                    item = self._items[item_id]
                    if item.status != "queued":
                        continue
                    failures = dependency_failures(item, self._items, self._results)
                    if not failures:
                        continue
                    updated, attempt, result = blocked_dependency_records(item, failures, now)
                    attempt = attempt.model_copy(update={"plan_snapshot_identity":
                        claim_plan_snapshot_identity(run, source="synthetic_blocked_receipt")})
                    self._items[item_id] = updated
                    self._attempts[attempt.id] = attempt
                    self._attempt_ids_by_run[run_id].append(attempt.id)
                    self._results[result.id] = result
                    self._result_ids_by_run[run_id].append(result.id)
                    blocked_count += 1
                    changed = True
                if not changed:
                    break
            if blocked_count:
                self._refresh_run(run_id, now)
                logger.info("test_run_dependencies_blocked", extra={"run_id": run_id, "blocked_count": blocked_count})
            queued = sorted(
                (
                    self._items[item_id]
                    for item_id in self._item_ids_by_run[run_id]
                    if self._items[item_id].status == "queued"
                    and dependencies_ready(self._items[item_id], self._items, self._results)
                ),
                key=lambda item: (item.position, item.id),
            )[:limit]
            claimed: list[tuple[TestRunItemRecord, TestRunAttemptRecord]] = []
            for item in queued:
                token = str(uuid4())
                attempt_no = item.attempt_no + 1
                prior = next(
                    (
                        self._attempts[attempt_id]
                        for attempt_id in reversed(self._attempt_ids_by_run[run_id])
                        if self._attempts[attempt_id].run_item_id == item.id
                        and self._attempts[attempt_id].status == "expired"
                    ),
                    None,
                )
                attempt = TestRunAttemptRecord(
                    id=str(uuid4()),
                    run_id=run_id,
                    run_item_id=item.id,
                    attempt_no=attempt_no,
                    worker_id=worker_id,
                    lease_token=token,
                    claimed_at=now,
                    checkpoint_version=prior.checkpoint_version if prior else 0,
                    checkpoint_key=prior.checkpoint_key if prior else None,
                    checkpoint_payload=prior.checkpoint_payload if prior else {},
                    checkpoint_at=prior.checkpoint_at if prior else None,
                    recovered_from_attempt_id=prior.id if prior else None,
                    environment_admission=prior.environment_admission if prior else None,
                    plan_snapshot_identity=claim_plan_snapshot_identity(run, prior),
                )
                stored_item = item.model_copy(
                    deep=True,
                    update={
                        "status": "claimed",
                        "attempt_no": attempt_no,
                        "lease_owner": worker_id,
                        "lease_token": token,
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "heartbeat_at": now,
                        "updated_at": now,
                    },
                )
                self._items[item.id] = stored_item
                self._attempts[attempt.id] = attempt
                self._attempt_ids_by_run[run_id].append(attempt.id)
                claimed.append((stored_item.model_copy(deep=True), attempt.model_copy(deep=True)))
            self._refresh_run(run_id, now)
            return claimed

    async def start_item(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._require_active_lease(item_id, lease_token, now, {"claimed"})
            if not dependencies_ready(item, self._items, self._results):
                raise ValueError("运行条目的必要前置或输出尚未满足")
            attempt = self._active_attempt(item_id, lease_token)
            self._run_for_attempt(self._require_run(item.run_id), attempt)
            updated = item.model_copy(
                deep=True,
                update={
                    "status": "running",
                    "started_at": item.started_at or now,
                    "active_started_at": item.active_started_at or now,
                    "updated_at": now,
                },
            )
            self._items[item_id] = updated
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={
                    "status": "running",
                    "started_at": attempt.started_at or now,
                    "active_started_at": attempt.active_started_at or now,
                    "heartbeat_at": now,
                },
            )
            self._refresh_run(item.run_id, now)
            return updated.model_copy(deep=True)

    async def mark_waiting_resource(
        self, item_id: str, lease_token: str, reason: str, now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._require_active_lease(item_id, lease_token, now, {"running"})
            attempt = self._active_attempt(item_id, lease_token)
            updated = item.model_copy(deep=True, update={
                "status": "waiting_resource",
                "waiting_reason": str(reason or "resource_unavailable")[:240],
                "active_duration_ms": add_active_duration(
                    active_duration_ms=item.active_duration_ms,
                    active_started_at=item.active_started_at,
                    now=now,
                ),
                "active_started_at": None,
                "waiting_started_at": now,
                "updated_at": now,
            })
            self._items[item_id] = updated
            self._attempts[attempt.id] = attempt.model_copy(deep=True, update={
                "active_duration_ms": add_active_duration(
                    active_duration_ms=attempt.active_duration_ms,
                    active_started_at=attempt.active_started_at,
                    now=now,
                ),
                "active_started_at": None,
                "waiting_started_at": now,
            })
            self._refresh_run(item.run_id, now)
            return updated.model_copy(deep=True)

    async def heartbeat_item(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._require_active_lease(
                item_id,
                lease_token,
                now,
                {"claimed", "running"},
            )
            updated = item.model_copy(
                deep=True,
                update={
                    "heartbeat_at": now,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
            )
            self._items[item_id] = updated
            attempt = self._active_attempt(item_id, lease_token)
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={"heartbeat_at": now},
            )
            return updated.model_copy(deep=True)

    async def mark_waiting_approval(
        self,
        item_id: str,
        lease_token: str,
        approval_id: str,
        tool_job_id: str,
        now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._require_active_lease(item_id, lease_token, now, {"running"})
            attempt = self._active_attempt(item_id, lease_token)
            updated = item.model_copy(
                deep=True,
                update={
                    "status": "waiting_approval",
                    "lease_expires_at": None,
                    "heartbeat_at": now,
                    "approval_id": approval_id,
                    "tool_job_id": tool_job_id,
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=item.active_duration_ms,
                        active_started_at=item.active_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": now,
                    "updated_at": now,
                },
            )
            self._items[item_id] = updated
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={
                    "status": "waiting_approval",
                    "heartbeat_at": now,
                    "approval_id": approval_id,
                    "tool_job_id": tool_job_id,
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=attempt.active_duration_ms,
                        active_started_at=attempt.active_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": now,
                },
            )
            self._refresh_run(item.run_id, now)
            return updated.model_copy(deep=True)

    async def save_checkpoint(
        self,
        item_id: str,
        lease_token: str,
        checkpoint_key: str,
        checkpoint_payload: dict[str, object],
        checkpoint_version: int | None,
        now: datetime,
    ) -> TestRunAttemptRecord:
        async with self._lock:
            item = self._require_active_lease(item_id, lease_token, now, {"claimed", "running"})
            attempt = self._active_attempt(item_id, lease_token)
            require_generic_checkpoint_write(attempt, checkpoint_key)
            version = checkpoint_version or attempt.checkpoint_version + 1
            if version <= attempt.checkpoint_version:
                raise ValueError(f"Checkpoint version must increase for run item: {item_id}")
            updated = attempt.model_copy(
                deep=True,
                update={
                    "checkpoint_version": version,
                    "checkpoint_key": checkpoint_key,
                    "checkpoint_payload": dict(checkpoint_payload),
                    "checkpoint_at": now,
                    "heartbeat_at": now,
                },
            )
            self._attempts[attempt.id] = updated
            return updated.model_copy(deep=True)

    async def resume_waiting_approval(
        self,
        item_id: str,
        approval_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(f"Test run item not found: {item_id}")
            if item.status == "claimed" and item.approval_id == approval_id:
                if (
                    not item.lease_token
                    or item.lease_expires_at is None
                    or item.lease_expires_at <= now
                ):
                    raise ValueError(f"Resumed run item lease is not active: {item_id}")
                attempt = self._active_attempt(item_id, item.lease_token)
                require_generic_approval_resume(attempt)
                if attempt.status != "claimed":
                    raise ValueError(f"Resumed run item attempt is not claimed: {item_id}")
                return item.model_copy(deep=True)
            if item.status != "waiting_approval" or item.approval_id != approval_id:
                raise ValueError(f"Run item is not waiting for this approval: {item_id}")
            if not item.lease_token:
                raise ValueError(f"Waiting run item has no preserved lease token: {item_id}")
            attempt = self._active_attempt(item_id, item.lease_token)
            require_generic_approval_resume(attempt)
            updated = item.model_copy(
                deep=True,
                update={
                    "status": "claimed",
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "heartbeat_at": now,
                    "waiting_duration_ms": add_waiting_duration(
                        waiting_duration_ms=item.waiting_duration_ms,
                        waiting_started_at=item.waiting_started_at,
                        now=now,
                    ),
                    "waiting_started_at": None,
                    "updated_at": now,
                },
            )
            self._items[item_id] = updated
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={
                    "status": "claimed",
                    "heartbeat_at": now,
                    "waiting_duration_ms": add_waiting_duration(
                        waiting_duration_ms=attempt.waiting_duration_ms,
                        waiting_started_at=attempt.waiting_started_at,
                        now=now,
                    ),
                    "waiting_started_at": None,
                },
            )
            self._refresh_run(item.run_id, now)
            return updated.model_copy(deep=True)

    async def list_waiting_approval_items(
        self, *, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]:
        if limit is not None and limit < 0:
            raise ValueError("Reconciliation page limit cannot be negative")
        async with self._lock:
            items = [
                item.model_copy(deep=True)
                for item in self._items.values()
                if item.status == "waiting_approval"
                and item.approval_id
                and item.tool_job_id
            ]
            items.sort(key=lambda item: (ensure_utc_datetime(item.updated_at), item.id))
            if after is not None:
                cursor = (ensure_utc_datetime(after[0]), after[1])
                items = [
                    item for item in items
                    if (ensure_utc_datetime(item.updated_at), item.id) > cursor
                ]
            return items[:limit] if limit is not None else items

    async def list_cancelled_resource_items(
        self, *, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]:
        if limit is not None and limit < 0:
            raise ValueError("Reconciliation page limit cannot be negative")
        async with self._lock:
            items = [
                item.model_copy(deep=True)
                for item in self._items.values()
                if item.status == "cancelled"
                and (item.approval_id or item.tool_job_id)
                and item.resource_cleanup_completed_at is None
            ]
            items.sort(key=lambda item: (ensure_utc_datetime(item.updated_at), item.id))
            if after is not None:
                cursor = (ensure_utc_datetime(after[0]), after[1])
                items = [
                    item for item in items
                    if (ensure_utc_datetime(item.updated_at), item.id) > cursor
                ]
            return items[:limit] if limit is not None else items

    async def mark_cancelled_resources_reconciled(
        self,
        item_id: str,
        now: datetime,
    ) -> TestRunItemRecord:
        async with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(f"Test run item not found: {item_id}")
            if item.status != "cancelled":
                raise ValueError(f"Run item is not cancelled: {item_id}")
            updated = item.model_copy(
                deep=True,
                update={"resource_cleanup_completed_at": now, "updated_at": now},
            )
            self._items[item_id] = updated
            return updated.model_copy(deep=True)

    async def complete_denied_approval(
        self,
        item_id: str,
        approval_id: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        async with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(f"Test run item not found: {item_id}")
            if item.approval_id != approval_id:
                raise ValueError(f"Run item is not bound to this approval: {item_id}")
            if item.result_id:
                existing = self._results[item.result_id]
                require_generic_result_write(self._attempts[existing.attempt_id])
                return existing.model_copy(deep=True)
            if item.status != "waiting_approval":
                raise ValueError(f"Run item is not waiting for this approval: {item_id}")
            if completion.status != "blocked":
                raise ValueError("Denied approval completion status must be blocked.")
            if not item.lease_token:
                raise ValueError(f"Waiting run item has no preserved lease token: {item_id}")
            attempt = self._active_attempt(item_id, item.lease_token)
            require_generic_result_write(attempt)
            result = TestCaseResultRecord(
                id=str(uuid4()),
                run_id=item.run_id,
                run_item_id=item.id,
                case_id=item.case_id,
                case_version_id=item.case_version_id,
                regression_source_result_id=item.regression_source_result_id,
                attempt_id=attempt.id,
                attempt_no=attempt.attempt_no,
                created_at=now,
                **completion.model_dump(mode="python"),
            )
            self._results[result.id] = result
            self._result_ids_by_run[item.run_id].append(result.id)
            self._items[item_id] = item.model_copy(
                deep=True,
                update={
                    "status": "blocked",
                    "result_id": result.id,
                    "completed_at": now,
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=item.active_duration_ms,
                        active_started_at=item.active_started_at,
                        now=now,
                    ),
                    "waiting_duration_ms": add_waiting_duration(
                        waiting_duration_ms=item.waiting_duration_ms,
                        waiting_started_at=item.waiting_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": None,
                    "wall_clock_duration_ms": elapsed_ms(item.created_at, now),
                    "updated_at": now,
                },
            )
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={
                    "status": "blocked",
                    "completed_at": now,
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=attempt.active_duration_ms,
                        active_started_at=attempt.active_started_at,
                        now=now,
                    ),
                    "waiting_duration_ms": add_waiting_duration(
                        waiting_duration_ms=attempt.waiting_duration_ms,
                        waiting_started_at=attempt.waiting_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": None,
                    "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
                },
            )
            self._refresh_run(item.run_id, now)
            return result.model_copy(deep=True)

    async def complete_item(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        async with self._lock:
            item = self._items.get(item_id)
            if item is None:
                raise KeyError(f"Test run item not found: {item_id}")
            if item.result_id:
                existing = self._results[item.result_id]
                attempt = self._attempts[existing.attempt_id]
                require_generic_result_write(attempt)
                if attempt.lease_token == lease_token and existing.payload_hash == completion.payload_hash:
                    return existing.model_copy(deep=True)
                raise ValueError(f"Test run item already completed: {item_id}")
            item = self._require_active_lease(item_id, lease_token, now, {"running"})
            attempt = self._active_attempt(item_id, lease_token)
            require_generic_result_write(attempt)
            result = TestCaseResultRecord(
                id=str(uuid4()),
                run_id=item.run_id,
                run_item_id=item.id,
                case_id=item.case_id,
                case_version_id=item.case_version_id,
                regression_source_result_id=item.regression_source_result_id,
                attempt_id=attempt.id,
                attempt_no=attempt.attempt_no,
                created_at=now,
                **completion.model_dump(mode="python"),
            )
            self._results[result.id] = result
            self._result_ids_by_run[item.run_id].append(result.id)
            self._items[item_id] = item.model_copy(
                deep=True,
                update={
                    "status": completion.status,
                    "result_id": result.id,
                    "completed_at": now,
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=item.active_duration_ms,
                        active_started_at=item.active_started_at,
                        now=now,
                    ),
                    "waiting_duration_ms": add_waiting_duration(
                        waiting_duration_ms=item.waiting_duration_ms,
                        waiting_started_at=item.waiting_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": None,
                    "wall_clock_duration_ms": elapsed_ms(item.created_at, now),
                    "updated_at": now,
                },
            )
            self._attempts[attempt.id] = attempt.model_copy(
                deep=True,
                update={
                    "status": completion.status,
                    "completed_at": now,
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=attempt.active_duration_ms,
                        active_started_at=attempt.active_started_at,
                        now=now,
                    ),
                    "waiting_duration_ms": add_waiting_duration(
                        waiting_duration_ms=attempt.waiting_duration_ms,
                        waiting_started_at=attempt.waiting_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": None,
                    "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
                },
            )
            self._refresh_run(item.run_id, now)
            return result.model_copy(deep=True)

    async def recover_expired(self, run_id: str, now: datetime) -> int:
        async with self._lock:
            self._require_run(run_id)
            return self._recover_expired_unlocked(run_id, now)

    async def recover_all_expired(self, now: datetime) -> int:
        async with self._lock:
            return sum(
                self._recover_expired_unlocked(run_id, now)
                for run_id in list(self._runs)
            )

    async def cancel_run(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail:
        async with self._lock:
            run = self._require_run(run_id)
            if run.status == "completed":
                raise ValueError(f"Completed test run cannot be cancelled: {run_id}")
            if run.status == "cancelled":
                return self._detail(run_id)
            for item_id in self._item_ids_by_run[run_id]:
                item = self._items[item_id]
                if item.status in TERMINAL_ITEM_STATUSES:
                    continue
                if item.lease_token and item.status in ACTIVE_ITEM_STATUSES | {"waiting_approval", "waiting_resource"}:
                    attempt = self._active_attempt(item_id, item.lease_token)
                    self._attempts[attempt.id] = attempt.model_copy(
                        deep=True,
                        update={
                            "status": "cancelled",
                            "completed_at": now,
                            "active_duration_ms": add_active_duration(
                                active_duration_ms=attempt.active_duration_ms,
                                active_started_at=attempt.active_started_at,
                                now=now,
                            ),
                            "waiting_duration_ms": add_waiting_duration(
                                waiting_duration_ms=attempt.waiting_duration_ms,
                                waiting_started_at=attempt.waiting_started_at,
                                now=now,
                            ),
                            "active_started_at": None,
                            "waiting_started_at": None,
                            "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
                        },
                    )
                self._items[item_id] = item.model_copy(
                    deep=True,
                    update={
                        "status": "cancelled",
                        "completed_at": now,
                        "updated_at": now,
                        "active_duration_ms": add_active_duration(
                            active_duration_ms=item.active_duration_ms,
                            active_started_at=item.active_started_at,
                            now=now,
                        ),
                        "waiting_duration_ms": add_waiting_duration(
                            waiting_duration_ms=item.waiting_duration_ms,
                            waiting_started_at=item.waiting_started_at,
                            now=now,
                        ),
                        "active_started_at": None,
                        "waiting_started_at": None,
                        "wall_clock_duration_ms": elapsed_ms(item.created_at, now),
                    },
                )
            self._runs[run_id] = run.model_copy(
                deep=True,
                update={
                    "status": "cancelled",
                    "cancel_reason": reason,
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            self._refresh_run(run_id, now, preserve_cancelled=True)
            return self._detail(run_id)

    async def count_by_project(self, project_id: str) -> int:
        return sum(1 for run in self._runs.values() if run.project_id == project_id)

    def _require_run(self, run_id: str) -> TestRunRecord:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Test run not found: {run_id}")
        return run

    def _recover_expired_unlocked(self, run_id: str, now: datetime) -> int:
        recovered = 0
        for item_id in self._item_ids_by_run[run_id]:
            item = self._items[item_id]
            if (
                item.status in ACTIVE_ITEM_STATUSES | {"waiting_resource"}
                and item.lease_expires_at is not None
                and item.lease_expires_at <= now
            ):
                attempt = self._active_attempt(item_id, item.lease_token) if item.lease_token else None
                if attempt is not None and not can_requeue_expired_attempt(attempt):
                    continue
                interval_end = min(now, item.lease_expires_at)
                if attempt is not None:
                    self._attempts[attempt.id] = attempt.model_copy(
                        deep=True,
                        update={
                            "status": "expired",
                            "completed_at": now,
                            "active_duration_ms": add_active_duration(
                                active_duration_ms=attempt.active_duration_ms,
                                active_started_at=attempt.active_started_at,
                                now=interval_end,
                            ),
                            "waiting_duration_ms": add_waiting_duration(
                                waiting_duration_ms=attempt.waiting_duration_ms,
                                waiting_started_at=attempt.waiting_started_at,
                                now=interval_end,
                            ),
                            "active_started_at": None,
                            "waiting_started_at": None,
                            "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
                        },
                    )
                self._items[item_id] = item.model_copy(
                    deep=True,
                    update={
                        "status": "queued",
                        "lease_owner": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                        "heartbeat_at": None,
                        "updated_at": now,
                        "started_at": None,
                        "active_duration_ms": add_active_duration(
                            active_duration_ms=item.active_duration_ms,
                            active_started_at=item.active_started_at,
                            now=interval_end,
                        ),
                        "waiting_duration_ms": add_waiting_duration(
                            waiting_duration_ms=item.waiting_duration_ms,
                            waiting_started_at=item.waiting_started_at,
                            now=interval_end,
                        ),
                        "active_started_at": None,
                        "waiting_started_at": None,
                    },
                )
                recovered += 1
        if recovered:
            self._refresh_run(run_id, now)
        return recovered

    def _require_active_lease(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
        allowed_statuses: set[str],
    ) -> TestRunItemRecord:
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"Test run item not found: {item_id}")
        if item.status not in allowed_statuses:
            raise ValueError(
                f"Illegal run item transition from {item.status}: {item_id}"
            )
        if item.lease_token != lease_token:
            raise ValueError(f"Invalid lease token for run item: {item_id}")
        if item.lease_expires_at is None or item.lease_expires_at <= now:
            raise ValueError(f"Lease expired for run item: {item_id}")
        return item

    def _active_attempt(
        self,
        item_id: str,
        lease_token: str,
    ) -> TestRunAttemptRecord:
        attempt = next(
            (
                self._attempts[attempt_id]
                for attempt_id in reversed(
                    self._attempt_ids_by_run[self._items[item_id].run_id]
                )
                if self._attempts[attempt_id].run_item_id == item_id
                and self._attempts[attempt_id].lease_token == lease_token
            ),
            None,
        )
        if attempt is None:
            raise ValueError(f"Active attempt not found for run item: {item_id}")
        return attempt

    def _refresh_run(
        self,
        run_id: str,
        now: datetime,
        *,
        preserve_cancelled: bool = False,
    ) -> None:
        run = self._runs[run_id]
        items = [self._items[item_id] for item_id in self._item_ids_by_run[run_id]]
        counts = {status: 0 for status in TestRunStats.model_fields if status != "total"}
        for item in items:
            counts[item.status] += 1
        stats = TestRunStats(total=len(items), **counts)
        all_terminal = bool(items) and all(
            item.status in TERMINAL_ITEM_STATUSES for item in items
        )
        if preserve_cancelled or run.status == "cancelled":
            status = "cancelled"
        elif all_terminal:
            status = "completed"
        elif any(item.status != "queued" for item in items):
            status = "running"
        else:
            status = "queued"
        started_at = run.started_at
        if status == "running" and started_at is None:
            started_at = now
        completed_at = run.completed_at
        if status == "completed" and completed_at is None:
            completed_at = now
        active_duration_ms = sum(item.active_duration_ms for item in items)
        waiting_duration_ms = sum(item.waiting_duration_ms for item in items)
        self._runs[run_id] = run.model_copy(
            deep=True,
            update={
                "status": status,
                "stats": stats,
                "started_at": started_at,
                "completed_at": completed_at,
                "active_duration_ms": active_duration_ms,
                "waiting_duration_ms": waiting_duration_ms,
                "wall_clock_duration_ms": elapsed_ms(run.created_at, completed_at or now),
                "updated_at": now,
            },
        )

    def _detail(self, run_id: str) -> TestRunDetail:
        return TestRunDetail(
            run=self._runs[run_id].model_copy(deep=True),
            items=[
                self._items[item_id].model_copy(deep=True)
                for item_id in self._item_ids_by_run.get(run_id, [])
            ],
            attempts=[
                self._attempts[attempt_id].model_copy(deep=True)
                for attempt_id in self._attempt_ids_by_run.get(run_id, [])
            ],
            results=[
                self._results[result_id].model_copy(deep=True)
                for result_id in self._result_ids_by_run.get(run_id, [])
            ],
        )


class PostgresTestRunStore:
    def __init__(self, settings: Settings, *, environment_store=None) -> None:
        self._settings = settings
        self._environments = environment_store

    @property
    def _run_table(self) -> str:
        return self._settings.database.postgres_test_run_table

    @property
    def _item_table(self) -> str:
        return self._settings.database.postgres_test_run_item_table

    @property
    def _attempt_table(self) -> str:
        return self._settings.database.postgres_test_run_attempt_table

    @property
    def _result_table(self) -> str:
        return self._settings.database.postgres_test_case_result_table

    @property
    def _plan_revision_table(self) -> str:
        return derived_postgres_table(self._run_table, "planrev")

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def initialize_plan_revision_storage(self) -> None:
        await asyncio.to_thread(self._initialize_plan_revision_storage_sync)

    def _initialize_plan_revision_storage_sync(self) -> None:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"""CREATE TABLE IF NOT EXISTS {self._plan_revision_table} (
                run_id UUID NOT NULL REFERENCES {self._run_table}(id) ON DELETE CASCADE,
                revision BIGINT NOT NULL CHECK (revision > 0),
                idempotency_key TEXT NOT NULL,
                request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{{64}}$'),
                created_at TIMESTAMPTZ NOT NULL,
                record JSONB NOT NULL,
                PRIMARY KEY (run_id, revision),
                UNIQUE (run_id, idempotency_key)
            )""")

    async def project_execution_counts(self, project_id: str) -> tuple[TestRunStats, dict[str, int]]:
        return await asyncio.to_thread(self._project_execution_counts_sync, project_id)

    def _project_execution_counts_sync(self, project_id: str) -> tuple[TestRunStats, dict[str, int]]:
        # Same grouped status aggregation as _refresh_run_in_cursor, scoped to
        # the entire project and read in one PostgreSQL statement snapshot.
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"""
                SELECT item.status,
                    CASE WHEN item.status = 'waiting_resource'
                        THEN COALESCE(NULLIF(item.record->>'waiting_reason', ''), 'resource_unavailable')
                        ELSE NULL END AS reason,
                    COUNT(*) AS total
                FROM {self._item_table} item JOIN {self._run_table} run ON run.id = item.run_id
                WHERE run.project_id = %s
                GROUP BY item.status, reason
            """, (project_id,))
            rows = cur.fetchall()
        counts: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        for row in rows:
            counts[row["status"]] += int(row["total"])
            if row["reason"] is not None:
                reasons[row["reason"]] += int(row["total"])
        return TestRunStats(total=sum(counts.values()), **counts), dict(reasons)

    async def create_run(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail:
        return await asyncio.to_thread(self._create_run_sync, run, items)

    async def create_planned_run(self, suite: TestSuiteBundle, run: TestRunRecord,
        items: list[TestRunItemRecord], *, suite_store, outbox_table: str | None,
        event_key: str, stream: str | None, payload: dict[str, object]) -> TestRunDetail:
        return await asyncio.to_thread(self._create_planned_run_sync, suite, run, items,
            suite_store, outbox_table, event_key, stream, payload)

    async def create_run_with_outbox(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
        *,
        outbox_table: str,
        event_key: str,
        stream: str,
        payload: dict[str, object],
    ) -> TestRunDetail:
        return await asyncio.to_thread(
            self._create_run_with_outbox_sync,
            run,
            items,
            outbox_table,
            event_key,
            stream,
            payload,
        )

    async def get_run(self, run_id: str) -> TestRunDetail | None:
        return await asyncio.to_thread(self._get_run_sync, run_id)

    async def revise_plan(self, *, run_id: str, request: RunPlanRevisionRequest,
        request_sha256: str, candidate_run: TestRunRecord, candidate_items: list[TestRunItemRecord],
        now: datetime) -> TestRunPlanRevisionRecord:
        return await asyncio.to_thread(self._revise_plan_sync, run_id, request, request_sha256,
            candidate_run, candidate_items, now)

    def _revise_plan_sync(self, run_id: str, request: RunPlanRevisionRequest, request_sha256: str,
        candidate_run: TestRunRecord, candidate_items: list[TestRunItemRecord], now: datetime
        ) -> TestRunPlanRevisionRecord:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            self._lock_plan_projection(cur, run_id, exclusive=True)
            cur.execute(f"SELECT record FROM {self._plan_revision_table} "
                "WHERE run_id=%s AND idempotency_key=%s", (run_id, request.idempotency_key))
            prior_row = cur.fetchone()
            if prior_row is not None:
                prior = TestRunPlanRevisionRecord.model_validate(self._value(prior_row["record"]))
                if prior.request_sha256 != request_sha256:
                    raise ValueError("相同计划修订幂等键已用于不同请求")
                return prior

            cur.execute(f"SELECT record FROM {self._item_table} WHERE run_id=%s "
                "ORDER BY position,id FOR UPDATE", (run_id,))
            current_items = [self._item_from_value(row["record"]) for row in cur.fetchall() or []]
            cur.execute(f"SELECT DISTINCT run_item_id FROM {self._attempt_table} WHERE run_id=%s", (run_id,))
            attempted_ids = {str(row["run_item_id"]) for row in cur.fetchall() or []}
            current_run = self._lock_run(cur, run_id)
            revised_run, revised_items, revision = build_run_plan_revision(
                current_run=current_run,
                current_items=current_items,
                attempted_item_ids=attempted_ids,
                candidate_run=candidate_run,
                candidate_items=candidate_items,
                request=request,
                request_sha256=request_sha256,
                now=now,
            )
            prior_ids = {item.id for item in current_items}
            revised_ids = {item.id for item in revised_items}
            if not prior_ids.issubset(revised_ids) or len(revised_ids) != len(revised_items):
                raise RuntimeError("计划修订意外丢失或重复了历史 RunItem")

            # All positions are reassigned as one frozen projection. Move existing
            # unique values out of the positive range before writing final values.
            cur.execute(f"UPDATE {self._item_table} SET position=-position WHERE run_id=%s", (run_id,))
            for item in revised_items:
                if item.id in prior_ids:
                    self._write_item(cur, item)
                else:
                    cur.execute(f"INSERT INTO {self._item_table} "
                        "(id,run_id,case_id,case_version_id,position,status,attempt_no,lease_owner,lease_token,"
                        "lease_expires_at,result_id,regression_source_result_id,updated_at,record) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                        (item.id, item.run_id, item.case_id, item.case_version_id, item.position, item.status,
                         item.attempt_no, item.lease_owner, item.lease_token, item.lease_expires_at,
                         item.result_id, item.regression_source_result_id, item.updated_at, self._json(item)))
            self._write_run(cur, revised_run)
            self._refresh_run_in_cursor(cur, run_id, now)
            cur.execute(f"INSERT INTO {self._plan_revision_table} "
                "(run_id,revision,idempotency_key,request_sha256,created_at,record) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                (revision.run_id, revision.revision, revision.idempotency_key, revision.request_sha256,
                 revision.created_at, self._json(revision)))
            logger.info("test_run_plan_revision_committed", extra={"run_id": run_id,
                "revision": revision.revision, "added_item_count": len(revision.added_item_ids),
                "updated_item_count": len(revision.updated_item_ids),
                "cancelled_item_count": len(revision.cancelled_item_ids)})
            return revision

    async def list_plan_revisions(self, run_id: str) -> list[TestRunPlanRevisionRecord]:
        return await asyncio.to_thread(self._list_plan_revisions_sync, run_id)

    def _list_plan_revisions_sync(self, run_id: str) -> list[TestRunPlanRevisionRecord]:
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {self._run_table} WHERE id=%s", (run_id,))
            if cur.fetchone() is None:
                raise KeyError(f"Test run not found: {run_id}")
            cur.execute(f"SELECT record FROM {self._plan_revision_table} WHERE run_id=%s ORDER BY revision", (run_id,))
            return [TestRunPlanRevisionRecord.model_validate(self._value(row["record"]))
                for row in cur.fetchall() or []]

    async def get_execution_detail(self, run_id: str, item_id: str, attempt_id: str,
        *, supplied_run: TestRunRecord | None = None) -> TestRunDetail:
        return await asyncio.to_thread(self._get_execution_detail_sync, run_id, item_id, attempt_id, supplied_run)

    async def get_result_execution_detail(self, run_id: str, result_id: str) -> TestRunDetail:
        return await asyncio.to_thread(self._get_result_execution_detail_sync, run_id, result_id)

    async def get_result_execution_contexts(self, run_id: str, result_ids: list[str]
        ) -> dict[str, ResultExecutionContext]:
        return await asyncio.to_thread(self._get_result_execution_contexts_sync, run_id, result_ids)

    async def get_run_record(self, run_id: str) -> TestRunRecord | None:
        return await asyncio.to_thread(self._get_run_record_sync, run_id)

    async def get_result(self, result_id: str) -> TestCaseResultRecord | None:
        return await asyncio.to_thread(self._get_result_sync, result_id)

    async def get_item(self, item_id: str) -> TestRunItemRecord | None:
        return await asyncio.to_thread(self._get_item_sync, item_id)

    async def get_latest_attempt(self, item_id: str) -> TestRunAttemptRecord | None:
        return await asyncio.to_thread(self._get_latest_attempt_sync, item_id)

    async def find_projected_legacy_smoke_run_ids(self, run_ids: list[str]) -> set[str]:
        return await asyncio.to_thread(
            self._find_projected_legacy_smoke_run_ids_sync,
            run_ids,
        )

    async def list_regression_candidates(
        self,
        *,
        run_id: str,
        result_ids: list[str] | None,
    ) -> list[RegressionSourceRecord]:
        return await asyncio.to_thread(
            self._list_regression_candidates_sync,
            run_id,
            result_ids,
        )

    async def list_regression_failures(
        self,
        *,
        project_id: str,
        failure_status: RegressionFailureStatus | None,
        mode_key: str | None,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionFailureRecord], bool]:
        return await asyncio.to_thread(
            self._list_regression_failures_sync,
            project_id,
            failure_status,
            mode_key,
            cursor_created_at,
            cursor_id,
            limit,
        )

    async def list_regression_batches(
        self,
        *,
        source_result_id: str,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionBatchRecord], bool]:
        return await asyncio.to_thread(
            self._list_regression_batches_sync,
            source_result_id,
            cursor_created_at,
            cursor_id,
            limit,
        )

    async def list_runs(
        self,
        *,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]:
        return await asyncio.to_thread(
            self._list_runs_sync,
            project_id,
            status,
            limit,
            offset,
        )

    async def claim_items(
        self,
        *,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]:
        return await asyncio.to_thread(
            self._claim_items_sync,
            run_id,
            worker_id,
            limit,
            lease_seconds,
            now,
        )

    async def mark_waiting_approval(
        self,
        item_id: str,
        lease_token: str,
        approval_id: str,
        tool_job_id: str,
        now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._mark_waiting_approval_sync,
            item_id,
            lease_token,
            approval_id,
            tool_job_id,
            now,
        )

    async def resume_waiting_approval(
        self,
        item_id: str,
        approval_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._resume_waiting_approval_sync,
            item_id,
            approval_id,
            lease_seconds,
            now,
        )

    async def list_waiting_approval_items(
        self, *, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]:
        if limit is not None and limit < 0:
            raise ValueError("Reconciliation page limit cannot be negative")
        return await asyncio.to_thread(self._list_waiting_approval_items_sync, limit, after)

    async def list_cancelled_resource_items(
        self, *, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]:
        if limit is not None and limit < 0:
            raise ValueError("Reconciliation page limit cannot be negative")
        return await asyncio.to_thread(self._list_cancelled_resource_items_sync, limit, after)

    async def mark_cancelled_resources_reconciled(
        self,
        item_id: str,
        now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._mark_cancelled_resources_reconciled_sync,
            item_id,
            now,
        )

    async def complete_denied_approval(
        self,
        item_id: str,
        approval_id: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        return await asyncio.to_thread(
            self._complete_denied_approval_sync,
            item_id,
            approval_id,
            completion,
            now,
        )

    async def start_item(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._start_item_sync,
            item_id,
            lease_token,
            now,
        )

    async def mark_waiting_resource(
        self, item_id: str, lease_token: str, reason: str, now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._mark_waiting_resource_sync, item_id, lease_token, reason, now,
        )

    async def heartbeat_item(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        return await asyncio.to_thread(
            self._heartbeat_item_sync,
            item_id,
            lease_token,
            lease_seconds,
            now,
        )

    async def save_checkpoint(
        self,
        item_id: str,
        lease_token: str,
        checkpoint_key: str,
        checkpoint_payload: dict[str, object],
        checkpoint_version: int | None,
        now: datetime,
    ) -> TestRunAttemptRecord:
        return await asyncio.to_thread(
            self._save_checkpoint_sync,
            item_id,
            lease_token,
            checkpoint_key,
            checkpoint_payload,
            checkpoint_version,
            now,
        )

    async def complete_item(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        return await asyncio.to_thread(
            self._complete_item_sync,
            item_id,
            lease_token,
            completion,
            now,
        )

    async def recover_expired(self, run_id: str, now: datetime) -> int:
        return await asyncio.to_thread(self._recover_expired_sync, run_id, now)

    async def recover_all_expired(self, now: datetime) -> int:
        return await asyncio.to_thread(self._recover_all_expired_sync, now)

    async def cancel_run(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail:
        return await asyncio.to_thread(self._cancel_run_sync, run_id, reason, now)

    async def count_by_project(self, project_id: str) -> int:
        return await asyncio.to_thread(self._count_by_project_sync, project_id)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._run_table} (
                        id UUID PRIMARY KEY,
                        project_id UUID NOT NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                        suite_id UUID NOT NULL REFERENCES {self._settings.database.postgres_test_suite_table}(id),
                        status TEXT NOT NULL,
                        mode_key TEXT NOT NULL,
                        session_id TEXT NULL,
                        parent_run_id UUID NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL,
                        plan_snapshots JSONB NOT NULL DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                cur.execute(f"ALTER TABLE {self._run_table} ADD COLUMN IF NOT EXISTS "
                    "plan_snapshots JSONB NOT NULL DEFAULT '{}'::jsonb")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._item_table} (
                        id UUID PRIMARY KEY,
                        run_id UUID NOT NULL REFERENCES {self._run_table}(id),
                        case_id UUID NOT NULL REFERENCES {self._settings.database.postgres_test_case_table}(id),
                        case_version_id UUID NOT NULL REFERENCES {self._settings.database.postgres_test_case_version_table}(id),
                        position INTEGER NOT NULL,
                        status TEXT NOT NULL,
                        attempt_no INTEGER NOT NULL DEFAULT 0,
                        lease_owner TEXT NULL,
                        lease_token TEXT NULL,
                        lease_expires_at TIMESTAMPTZ NULL,
                        result_id UUID NULL,
                        regression_source_result_id UUID NULL,
                        updated_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(run_id, position),
                        UNIQUE(run_id, case_id, case_version_id)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._attempt_table} (
                        id UUID PRIMARY KEY,
                        run_id UUID NOT NULL REFERENCES {self._run_table}(id),
                        run_item_id UUID NOT NULL REFERENCES {self._item_table}(id),
                        attempt_no INTEGER NOT NULL,
                        lease_token TEXT NOT NULL,
                        status TEXT NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(run_item_id, attempt_no),
                        UNIQUE(lease_token)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._result_table} (
                        id UUID PRIMARY KEY,
                        run_id UUID NOT NULL REFERENCES {self._run_table}(id),
                        run_item_id UUID NOT NULL REFERENCES {self._item_table}(id),
                        case_id UUID NOT NULL REFERENCES {self._settings.database.postgres_test_case_table}(id),
                        case_version_id UUID NOT NULL REFERENCES {self._settings.database.postgres_test_case_version_table}(id),
                        attempt_id UUID NOT NULL REFERENCES {self._attempt_table}(id),
                        status TEXT NOT NULL,
                        payload_hash TEXT NOT NULL,
                        regression_source_result_id UUID NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        record JSONB NOT NULL,
                        UNIQUE(run_item_id)
                    )
                    """
                )
                cur.execute(
                    f"ALTER TABLE {self._run_table} "
                    "ADD COLUMN IF NOT EXISTS parent_run_id UUID NULL"
                )
                cur.execute(
                    f"ALTER TABLE {self._item_table} "
                    "ADD COLUMN IF NOT EXISTS regression_source_result_id UUID NULL"
                )
                cur.execute(
                    f"ALTER TABLE {self._result_table} "
                    "ADD COLUMN IF NOT EXISTS regression_source_result_id UUID NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._run_table}_project_created "
                    f"ON {self._run_table} (project_id, created_at DESC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._run_table}_parent_run "
                    f"ON {self._run_table} (parent_run_id) WHERE parent_run_id IS NOT NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_run_status_position "
                    f"ON {self._item_table} (run_id, status, position ASC)"
                )
                item_relation_parts = postgres_relation_parts(self._item_table)
                lease_expiry_index_name = f"idx_{item_relation_parts[-1]}_lease_expiry"
                lease_expiry_index = postgres_index_name(self._item_table, "lease_expiry")
                lease_expiry_relation = postgres_relation_sql(
                    (*item_relation_parts[:-1], lease_expiry_index_name)
                )
                cur.execute(
                    "SELECT pg_catalog.pg_get_indexdef(i.indexrelid) AS definition, "
                    "pg_catalog.pg_get_expr(i.indpred,i.indrelid) AS predicate "
                    "FROM pg_catalog.pg_index i "
                    "JOIN pg_catalog.pg_class index_class ON index_class.oid=i.indexrelid "
                    "WHERE index_class.relname=%s AND i.indrelid=to_regclass(%s)",
                    (lease_expiry_index_name, self._item_table),
                )
                lease_expiry_row = cur.fetchone()
                if lease_expiry_row is not None and (
                    "run_id, lease_expires_at" not in lease_expiry_row["definition"]
                    or "waiting_resource" not in (lease_expiry_row["predicate"] or "")
                ):
                    cur.execute(f"DROP INDEX IF EXISTS {lease_expiry_relation}")
                    logger.info(
                        "test_run_lease_expiry_index_upgraded",
                        extra={"table": self._item_table, "index": lease_expiry_index_name},
                    )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {lease_expiry_index} "
                    f"ON {self._item_table} (run_id, lease_expires_at) "
                    "WHERE status IN ('claimed', 'running', 'waiting_resource')"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_approval_recovery "
                    f"ON {self._item_table} (updated_at, id) "
                    "WHERE status = 'waiting_approval'"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_cancel_cleanup "
                    f"ON {self._item_table} (updated_at, id) "
                    "WHERE status = 'cancelled' "
                    "AND ((record->>'approval_id') IS NOT NULL "
                    "OR (record->>'tool_job_id') IS NOT NULL) "
                    "AND (record->>'resource_cleanup_completed_at') IS NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._item_table}_regression_source "
                    f"ON {self._item_table} (regression_source_result_id) "
                    "WHERE regression_source_result_id IS NOT NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._attempt_table}_run_item "
                    f"ON {self._attempt_table} (run_id, run_item_id, attempt_no ASC)"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._result_table}_regression_source "
                    f"ON {self._result_table} (regression_source_result_id) "
                    "WHERE regression_source_result_id IS NOT NULL"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"idx_{self._result_table}_run_regression_candidates "
                    f"ON {self._result_table} (run_id, run_item_id, id) "
                    "INCLUDE (status, case_id, case_version_id) "
                    "WHERE status IN ('failed', 'error', 'blocked')"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"idx_{self._result_table}_run_failed_created "
                    f"ON {self._result_table} (run_id, created_at DESC, id DESC) "
                    "INCLUDE (status, case_id, case_version_id, run_item_id) "
                    "WHERE status IN ('failed', 'error', 'blocked')"
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS "
                    f"idx_{self._item_table}_regression_source_latest "
                    f"ON {self._item_table} "
                    "(regression_source_result_id, updated_at DESC, id DESC) "
                    "INCLUDE (run_id, status, result_id, case_version_id) "
                    "WHERE regression_source_result_id IS NOT NULL"
                )

    def _create_planned_run_sync(self, suite: TestSuiteBundle, run: TestRunRecord,
        items: list[TestRunItemRecord], suite_store, outbox_table: str | None,
        event_key: str, stream: str | None, payload: dict[str, object]) -> TestRunDetail:
        from src.application.test_suites.suite_store import PostgresTestSuiteStore
        from src.application.test_runs.plan_mapping import json_digest, validate_frozen_cases, validate_plan_bundle, validate_regression_parent
        from src.schemas.case_planning import CasePlanRecord, case_plan_json
        from src.schemas.case_management import TestCaseRecord, TestCaseVersionRecord
        if not isinstance(suite_store, PostgresTestSuiteStore):
            raise ValueError("PostgreSQL 计划运行必须使用同类套件存储")
        if suite_store._suite_table != self._settings.database.postgres_test_suite_table:
            raise ValueError("计划运行与套件存储不属于同一目录")
        if run.plan_snapshot is None or suite.suite.project_id != run.project_id or suite.suite.id != run.suite_id:
            raise ValueError("固定计划、套件和运行引用不一致")
        if outbox_table and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", outbox_table):
            raise ValueError("Invalid task outbox table identifier")
        validate_plan_bundle(suite, run, items)
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            # Serialize creation requests for the project, and keep archive from
            # racing this transaction. No network or model work holds this lock.
            cur.execute(f"SELECT status FROM {self._settings.database.postgres_project_table} WHERE id=%s FOR UPDATE", (run.project_id,))
            project = cur.fetchone()
            cur.execute(f"SELECT record FROM {self._run_table} WHERE id=%s", (run.id,))
            existing = cur.fetchone()
            if existing:
                saved = self._run_from_value(existing["record"])
                if saved.creation_hash != run.creation_hash:
                    raise ValueError("相同创建请求已经关联其他计划或执行参数")
                return self._detail_from_cursor(cur, saved.id)
            if project is None or project["status"] != "active":
                raise ValueError("当前项目已停用，不能创建计划运行")
            cur.execute(f"SELECT record FROM {self._settings.database.postgres_case_plan_table} WHERE id=%s AND project_id=%s",
                (run.plan_snapshot.plan.id, run.project_id))
            plan_row = cur.fetchone()
            if plan_row is None or json_digest(case_plan_json(CasePlanRecord.model_validate(plan_row["record"]))) != json_digest(case_plan_json(run.plan_snapshot.plan)):
                raise ValueError("固定计划不存在或与保存依据不一致")
            if run.plan_snapshot.plan.request.environment is not None or run.plan_snapshot.plan.environment is not None:
                if self._environments is None:
                    raise RuntimeError("计划运行尚未配置固定环境核对服务")
                self._environments.validate_in_cursor(cur, run.plan_snapshot.plan)
            source = run.plan_snapshot.regression
            if source:
                cur.execute(f"SELECT record FROM {self._run_table} WHERE id=%s FOR SHARE", (source.parent_run_id,))
                parent_row = cur.fetchone()
                cur.execute(f"SELECT record FROM {self._result_table} WHERE id=ANY(%s::uuid[])",
                    (list(source.source_results.values()),))
                source_results = {r.id: r for r in [self._result_from_value(row["record"]) for row in cur.fetchall()]}
                contexts = self._result_execution_contexts_in_cursor(cur, source.parent_run_id,
                    list(source.source_results.values()))
                historical_parent = None
                for context in contexts.values():
                    if historical_parent is not None and historical_parent.plan_snapshot != context.run.plan_snapshot:
                        raise ValueError("一个回归运行不能混合引用不同计划快照的结果")
                    historical_parent = context.run
                if historical_parent is None and parent_row:
                    historical_parent = self._run_from_value(parent_row["record"])
                validate_regression_parent(run, historical_parent, source_results)
            case_ids = sorted({i.case_id for i in items})
            cur.execute(f"SELECT record FROM {self._settings.database.postgres_test_case_table} WHERE id=ANY(%s::uuid[]) ORDER BY id FOR SHARE", (case_ids,))
            cases = {c.id: c for c in [TestCaseRecord.model_validate(r["record"]) for r in cur.fetchall()]}
            cur.execute(f"SELECT record FROM {self._settings.database.postgres_test_case_version_table} WHERE id=ANY(%s::uuid[])",
                ([i.case_version_id for i in items],))
            versions = {v.id: v for v in [TestCaseVersionRecord.model_validate(r["record"]) for r in cur.fetchall()]}
            validate_frozen_cases(run.plan_snapshot.plan, cases, versions)
            if source and any(cases[case_id].active_version_id != version_id
                              for case_id, version_id in source.version_overrides.items()):
                raise ValueError("回归指定启用版本在提交前发生变化，请重新规划")
            suite_store.insert_in_cursor(cur, suite.suite, suite.items)
            self._insert_run_and_items(cur, run, items)
            reviews = self._current_environment_reviews(cur, run)
            restricted = self._block_environment_items_in_cursor(cur, run, reviews, run.created_at, "create")
            dependent = self._block_failed_dependencies_in_cursor(cur, run, run.created_at) if restricted else 0
            if restricted or dependent:
                self._refresh_run_in_cursor(cur, run.id, run.created_at,
                    status_deltas=[("queued", "blocked")]*(restricted+dependent))
            if outbox_table:
                cur.execute(f"INSERT INTO {outbox_table} (event_key, stream, payload) VALUES (%s,%s,%s::jsonb)",
                    (event_key, stream, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
            stored = self._detail_from_cursor(cur, run.id)
        return stored

    def _create_run_sync(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
    ) -> TestRunDetail:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._insert_run_and_items(cur, run, items)
        return TestRunDetail(run=run, items=items)

    def _create_run_with_outbox_sync(
        self,
        run: TestRunRecord,
        items: list[TestRunItemRecord],
        outbox_table: str,
        event_key: str,
        stream: str,
        payload: dict[str, object],
    ) -> TestRunDetail:
        """Commit the run rows and its durable task intent atomically."""
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._insert_run_and_items(cur, run, items)
                cur.execute(
                    f"INSERT INTO {outbox_table} (event_key, stream, payload) "
                    "VALUES (%s, %s, %s::jsonb) "
                    "ON CONFLICT (event_key) DO NOTHING",
                    (
                        event_key,
                        stream,
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
        return TestRunDetail(run=run, items=items)

    def _insert_run_and_items(self, cur, run: TestRunRecord, items: list[TestRunItemRecord]) -> None:
        validate_run_dependencies(run, items)
        cur.execute(
            f"INSERT INTO {self._run_table} "
            "(id, project_id, suite_id, status, mode_key, session_id, parent_run_id, "
            "created_at, updated_at, record, plan_snapshots) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
            (
                run.id,
                run.project_id,
                run.suite_id,
                run.status,
                run.mode_key,
                run.session_id,
                run.parent_run_id,
                run.created_at,
                run.updated_at,
                self._json(run),
                json.dumps(initial_plan_snapshots(run), ensure_ascii=False),
            ),
        )
        if not items:
            return
        cur.executemany(
            f"INSERT INTO {self._item_table} "
            "(id, run_id, case_id, case_version_id, position, status, attempt_no, "
            "lease_owner, lease_token, lease_expires_at, result_id, "
            "regression_source_result_id, updated_at, record) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, NULL, NULL, "
            "%s, %s, %s::jsonb)",
            [
                (
                    item.id,
                    item.run_id,
                    item.case_id,
                    item.case_version_id,
                    item.position,
                    item.status,
                    item.attempt_no,
                    item.regression_source_result_id,
                    item.updated_at,
                    self._json(item),
                )
                for item in items
            ],
        )

    def _get_run_sync(self, run_id: str) -> TestRunDetail | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                return self._detail_from_cursor(cur, run_id)

    def _get_run_record_sync(self, run_id: str) -> TestRunRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._run_table} WHERE id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
        return self._run_from_value(row["record"]) if row else None

    def _lock_plan_projection(self, cur, run_id: str, *, exclusive: bool = False) -> None:
        lock_plan_projection(cur, run_table=self._run_table, run_id=run_id, exclusive=exclusive)

    def _get_execution_detail_sync(self, run_id, item_id, attempt_id, supplied_run):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            detail = self._detail_from_cursor(cur, run_id)
            if detail is None:
                raise KeyError(f"Test run not found: {run_id}")
            attempt = execution_attempt(detail, item_id, attempt_id)
            fixed = self._run_for_attempt_in_cursor(cur, detail.run, attempt)
            validate_supplied_snapshot(supplied_run, detail.run, fixed)
            detail.run = fixed
            return detail

    def _get_result_execution_detail_sync(self, run_id, result_id):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            detail, contexts = self._result_execution_state_in_cursor(cur, run_id, [result_id])
            detail.run = contexts[result_id].run
            return detail

    def _get_result_execution_contexts_sync(self, run_id, result_ids):
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            _, contexts = self._result_execution_state_in_cursor(cur, run_id, result_ids)
            return contexts

    def _result_execution_contexts_in_cursor(self, cur, run_id, result_ids):
        return self._result_execution_state_in_cursor(cur, run_id, result_ids)[1]

    def _result_execution_state_in_cursor(self, cur, run_id, result_ids):
        # Hold the current projection stable while its archived snapshots are resolved.
        cur.execute(f"SELECT plan_snapshots FROM {self._run_table} WHERE id=%s FOR SHARE", (run_id,))
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"Test run not found: {run_id}")
        detail = self._detail_from_cursor(cur, run_id)
        archives = self._value(row["plan_snapshots"])
        return detail, result_execution_contexts(detail, archives, result_ids)

    def _run_for_attempt_in_cursor(self, cur, run, attempt):
        identity = attempt.plan_snapshot_identity
        if identity is None:
            return run
        cur.execute(f"SELECT plan_snapshots ? %s AS archived, plan_snapshots->%s AS snapshot "
            f"FROM {self._run_table} WHERE id=%s", (identity.sha256, identity.sha256, run.id))
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"Test run not found: {run.id}")
        return run_with_claimed_snapshot(run, attempt, row["snapshot"], archive_present=row["archived"])

    def _read_attempt(self, cur, item_id, lease_token):
        cur.execute(f"SELECT record FROM {self._attempt_table} WHERE run_item_id=%s AND lease_token=%s",
            (item_id, lease_token))
        row = cur.fetchone()
        if row is None:
            raise ValueError("运行条目缺少当前租约的 Attempt")
        return self._attempt_from_value(row["record"])

    def _get_result_sync(self, result_id: str) -> TestCaseResultRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._result_table} WHERE id = %s",
                    (result_id,),
                )
                row = cur.fetchone()
        return self._result_from_value(row["record"]) if row else None

    def _get_item_sync(self, item_id: str) -> TestRunItemRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._item_table} WHERE id = %s",
                    (item_id,),
                )
                row = cur.fetchone()
        return self._item_from_value(row["record"]) if row else None

    def _get_latest_attempt_sync(self, item_id: str) -> TestRunAttemptRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._attempt_table} "
                    "WHERE run_item_id = %s ORDER BY attempt_no DESC LIMIT 1",
                    (item_id,),
                )
                row = cur.fetchone()
        return self._attempt_from_value(row["record"]) if row else None

    def _find_projected_legacy_smoke_run_ids_sync(self, run_ids: list[str]) -> set[str]:
        normalized = list(dict.fromkeys(str(run_id).strip() for run_id in run_ids if str(run_id).strip()))
        if not normalized:
            return set()
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT DISTINCT COALESCE("
                    "record #>> '{actual,run_result,run_id}', "
                    "record #>> '{actual,legacy_smoke_run_id}'"
                    f") AS legacy_run_id FROM {self._result_table} "
                    "WHERE COALESCE("
                    "record #>> '{actual,run_result,run_id}', "
                    "record #>> '{actual,legacy_smoke_run_id}'"
                    ") = ANY(%s)",
                    (normalized,),
                )
                rows = cur.fetchall() or []
        return {str(row["legacy_run_id"]) for row in rows if row.get("legacy_run_id")}

    def _list_regression_candidates_sync(
        self,
        run_id: str,
        result_ids: list[str] | None,
    ) -> list[RegressionSourceRecord]:
        params: list[object] = [run_id]
        if result_ids is None:
            selector = "result.status IN ('failed', 'error', 'blocked')"
        else:
            valid_result_ids = []
            for result_id in result_ids:
                try:
                    valid_result_ids.append(str(UUID(result_id)))
                except (TypeError, ValueError):
                    continue
            if not valid_result_ids:
                return []
            selector = "result.id = ANY(%s::uuid[])"
            params.append(valid_result_ids)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT result.id AS result_id, "
                    "result.run_item_id AS run_item_id, "
                    "result.case_id AS case_id, "
                    "result.case_version_id AS case_version_id, "
                    "result.status AS status, "
                    "item.position AS run_item_position "
                    f"FROM {self._result_table} AS result "
                    f"LEFT JOIN {self._item_table} AS item "
                    "ON item.id = result.run_item_id AND item.run_id = result.run_id "
                    f"WHERE result.run_id = %s AND {selector} "
                    "ORDER BY item.position ASC NULLS LAST, result.id ASC",
                    tuple(params),
                )
                rows = cur.fetchall() or []
        return [
            RegressionSourceRecord(
                result_id=str(row["result_id"]),
                run_item_id=str(row["run_item_id"]),
                case_id=str(row["case_id"]),
                case_version_id=str(row["case_version_id"]),
                status=str(row["status"]),
                run_item_position=(
                    int(row["run_item_position"])
                    if row.get("run_item_position") is not None
                    else None
                ),
            )
            for row in rows
        ]

    def _list_regression_failures_sync(
        self,
        project_id: str,
        failure_status: RegressionFailureStatus | None,
        mode_key: str | None,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionFailureRecord], bool]:
        clauses = [
            "source_run.project_id = %s",
            "result.status IN ('failed', 'error', 'blocked')",
        ]
        params: list[object] = [project_id]
        if failure_status:
            clauses.append("result.status = %s")
            params.append(failure_status)
        if mode_key:
            clauses.append("source_run.mode_key = %s")
            params.append(mode_key)
        if cursor_created_at is not None and cursor_id is not None:
            clauses.append("(result.created_at, result.id) < (%s, %s::uuid)")
            params.extend([cursor_created_at, cursor_id])
        params.append(limit + 1)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT result.id AS source_result_id, "
                    "source_run.id AS source_run_id, "
                    "source_run.status AS source_run_status, "
                    "source_run.created_at AS source_run_created_at, "
                    "result.case_id AS case_id, "
                    "result.case_version_id AS case_version_id, "
                    "source_run.mode_key AS mode_key, "
                    "result.status AS failure_status, "
                    "COALESCE(result.record->>'summary', '') AS summary, "
                    "NULLIF(result.record->>'error_message', '') AS error_message, "
                    "result.created_at AS failed_at, "
                    "jsonb_array_length(COALESCE(result.record->'evidence_refs', '[]'::jsonb)) "
                    "AS evidence_count, "
                    "jsonb_array_length(COALESCE(result.record->'artifact_ids', '[]'::jsonb)) "
                    "AS artifact_count, "
                    "jsonb_array_length(COALESCE(result.record->'verification_ids', '[]'::jsonb)) "
                    "AS verification_count, "
                    "COALESCE(result.record->'actual', '{}'::jsonb) <> '{}'::jsonb AS has_actual, "
                    "COALESCE(batch.total, 0) AS regression_batch_count, "
                    "latest.latest_run_id, latest.latest_run_status, "
                    "latest.latest_run_item_id, latest.latest_item_status, "
                    "latest.latest_result_id, latest.latest_result_status, "
                    "latest.latest_case_version_id, latest.latest_run_created_at, "
                    "latest.latest_item_updated_at "
                    f"FROM {self._result_table} AS result "
                    f"JOIN {self._run_table} AS source_run ON source_run.id = result.run_id "
                    "LEFT JOIN LATERAL ("
                    "SELECT COUNT(*) AS total "
                    f"FROM {self._item_table} AS child_item "
                    "WHERE child_item.regression_source_result_id = result.id"
                    ") AS batch ON TRUE "
                    "LEFT JOIN LATERAL ("
                    "SELECT child_run.id AS latest_run_id, "
                    "child_run.status AS latest_run_status, "
                    "child_item.id AS latest_run_item_id, "
                    "child_item.status AS latest_item_status, "
                    "child_result.id AS latest_result_id, "
                    "child_result.status AS latest_result_status, "
                    "child_item.case_version_id AS latest_case_version_id, "
                    "child_run.created_at AS latest_run_created_at, "
                    "child_item.updated_at AS latest_item_updated_at "
                    f"FROM {self._item_table} AS child_item "
                    f"JOIN {self._run_table} AS child_run ON child_run.id = child_item.run_id "
                    f"LEFT JOIN {self._result_table} AS child_result "
                    "ON child_result.id = child_item.result_id "
                    "WHERE child_item.regression_source_result_id = result.id "
                    "ORDER BY child_run.created_at DESC, child_item.updated_at DESC, "
                    "child_item.id DESC LIMIT 1"
                    ") AS latest ON TRUE "
                    f"WHERE {' AND '.join(clauses)} "
                    "ORDER BY result.created_at DESC, result.id DESC "
                    "LIMIT %s",
                    tuple(params),
                )
                rows = cur.fetchall() or []
        selected = rows[:limit]
        records = []
        for row in selected:
            latest = None
            if row.get("latest_run_id") is not None:
                latest = LatestRegressionRecord(
                    run_id=str(row["latest_run_id"]),
                    run_status=str(row["latest_run_status"]),
                    run_item_id=str(row["latest_run_item_id"]),
                    item_status=str(row["latest_item_status"]),
                    result_id=(
                        str(row["latest_result_id"])
                        if row.get("latest_result_id") is not None
                        else None
                    ),
                    result_status=row.get("latest_result_status"),
                    case_version_id=str(row["latest_case_version_id"]),
                    created_at=row["latest_run_created_at"],
                    updated_at=row["latest_item_updated_at"],
                )
            records.append(
                RegressionFailureRecord(
                    source_result_id=str(row["source_result_id"]),
                    source_run_id=str(row["source_run_id"]),
                    source_run_status=str(row["source_run_status"]),
                    source_run_created_at=row["source_run_created_at"],
                    case_id=str(row["case_id"]),
                    case_version_id=str(row["case_version_id"]),
                    mode_key=str(row["mode_key"]),
                    failure_status=str(row["failure_status"]),
                    summary=str(row["summary"]),
                    error_message=row.get("error_message"),
                    failed_at=row["failed_at"],
                    evidence_count=int(row["evidence_count"]),
                    artifact_count=int(row["artifact_count"]),
                    verification_count=int(row["verification_count"]),
                    has_actual=bool(row["has_actual"]),
                    regression_batch_count=int(row["regression_batch_count"]),
                    latest_regression=latest,
                )
            )
        return records, len(rows) > limit

    def _list_regression_batches_sync(
        self,
        source_result_id: str,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
    ) -> tuple[list[RegressionBatchRecord], bool]:
        clauses = ["child_item.regression_source_result_id = %s"]
        params: list[object] = [source_result_id]
        if cursor_created_at is not None and cursor_id is not None:
            clauses.append(
                "(child_run.created_at, child_item.id) < (%s, %s::uuid)"
            )
            params.extend([cursor_created_at, cursor_id])
        params.append(limit + 1)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT child_run.id AS run_id, "
                    "COALESCE(child_run.record->>'run_kind', 'normal') AS run_kind, "
                    "child_run.status AS run_status, "
                    "child_run.parent_run_id AS parent_run_id, "
                    "child_item.id AS run_item_id, "
                    "child_item.status AS item_status, "
                    "child_result.id AS result_id, "
                    "child_result.status AS result_status, "
                    "child_item.case_version_id AS case_version_id, "
                    "child_run.created_at AS created_at, "
                    "child_item.updated_at AS updated_at "
                    f"FROM {self._item_table} AS child_item "
                    f"JOIN {self._run_table} AS child_run "
                    "ON child_run.id = child_item.run_id "
                    f"LEFT JOIN {self._result_table} AS child_result "
                    "ON child_result.id = child_item.result_id "
                    f"WHERE {' AND '.join(clauses)} "
                    "ORDER BY child_run.created_at DESC, child_item.id DESC "
                    "LIMIT %s",
                    tuple(params),
                )
                rows = cur.fetchall() or []
        records = [
            RegressionBatchRecord(
                run_id=str(row["run_id"]),
                run_kind=str(row["run_kind"]),
                run_status=str(row["run_status"]),
                parent_run_id=(
                    str(row["parent_run_id"])
                    if row.get("parent_run_id") is not None
                    else None
                ),
                run_item_id=str(row["run_item_id"]),
                item_status=str(row["item_status"]),
                result_id=(
                    str(row["result_id"])
                    if row.get("result_id") is not None
                    else None
                ),
                result_status=row.get("result_status"),
                case_version_id=str(row["case_version_id"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows[:limit]
        ]
        return records, len(rows) > limit

    def _list_runs_sync(
        self,
        project_id: str,
        status: TestRunStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TestRunRecord], bool]:
        clauses = ["project_id = %s"]
        params: list[object] = [project_id]
        if status:
            clauses.append("status = %s")
            params.append(status)
        params.extend([limit + 1, offset])
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._run_table} "
                    f"WHERE {' AND '.join(clauses)} "
                    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                    tuple(params),
                )
                rows = cur.fetchall() or []
        runs = [self._run_from_value(row["record"]) for row in rows]
        return runs[:limit], len(runs) > limit

    def _claim_items_sync(
        self,
        run_id: str,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime,
    ) -> list[tuple[TestRunItemRecord, TestRunAttemptRecord]]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._lock_plan_projection(cur, run_id)
                run = self._read_run(cur, run_id)
                if run.status in {"completed", "cancelled"}:
                    return []
                reviews = self._current_environment_reviews(cur, run)
                if reviews is not None:
                    run = self._read_run(cur, run_id)
                    if run.status in {"completed", "cancelled"}:
                        return []
                restricted_count = self._block_environment_items_in_cursor(cur, run, reviews, now, "claim")
                blocked_count = restricted_count + self._block_failed_dependencies_in_cursor(cur, run, now)
                cur.execute(
                    f"SELECT candidate.record FROM {self._item_table} AS candidate "
                    "WHERE candidate.run_id = %s AND candidate.status = 'queued' "
                    f"AND {self._dependencies_ready_sql()} "
                    "ORDER BY candidate.position ASC, candidate.id ASC "
                    "FOR UPDATE SKIP LOCKED LIMIT %s",
                    (run_id, limit),
                )
                candidates = [
                    self._item_from_value(row["record"])
                    for row in (cur.fetchall() or [])
                ]
                claimed: list[tuple[TestRunItemRecord, TestRunAttemptRecord]] = []
                for item in candidates:
                    token = str(uuid4())
                    attempt_no = item.attempt_no + 1
                    cur.execute(
                        f"SELECT record FROM {self._attempt_table} "
                        "WHERE run_item_id = %s AND status = 'expired' "
                        "ORDER BY attempt_no DESC LIMIT 1",
                        (item.id,),
                    )
                    prior_row = cur.fetchone()
                    prior = self._attempt_from_value(prior_row["record"]) if prior_row else None
                    attempt = TestRunAttemptRecord(
                        id=str(uuid4()),
                        run_id=run_id,
                        run_item_id=item.id,
                        attempt_no=attempt_no,
                        worker_id=worker_id,
                        lease_token=token,
                        claimed_at=now,
                        checkpoint_version=prior.checkpoint_version if prior else 0,
                        checkpoint_key=prior.checkpoint_key if prior else None,
                        checkpoint_payload=prior.checkpoint_payload if prior else {},
                        checkpoint_at=prior.checkpoint_at if prior else None,
                        recovered_from_attempt_id=prior.id if prior else None,
                        environment_admission=prior.environment_admission if prior else None,
                        plan_snapshot_identity=claim_plan_snapshot_identity(run, prior),
                    )
                    updated = item.model_copy(
                        update={
                            "status": "claimed",
                            "attempt_no": attempt_no,
                            "lease_owner": worker_id,
                            "lease_token": token,
                            "lease_expires_at": now + timedelta(seconds=lease_seconds),
                            "heartbeat_at": now,
                            "updated_at": now,
                        }
                    )
                    claimed.append((updated, attempt))
                if claimed:
                    cur.executemany(
                        f"UPDATE {self._item_table} SET status = %s, attempt_no = %s, "
                        "lease_owner = %s, lease_token = %s, lease_expires_at = %s, "
                        "updated_at = %s, record = %s::jsonb WHERE id = %s",
                        [
                            (
                                item.status,
                                item.attempt_no,
                                item.lease_owner,
                                item.lease_token,
                                item.lease_expires_at,
                                item.updated_at,
                                self._json(item),
                                item.id,
                            )
                            for item, _ in claimed
                        ],
                    )
                    cur.executemany(
                        f"INSERT INTO {self._attempt_table} "
                        "(id, run_id, run_item_id, attempt_no, lease_token, status, record) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
                        [
                            (
                                attempt.id,
                                attempt.run_id,
                                attempt.run_item_id,
                                attempt.attempt_no,
                                attempt.lease_token,
                                attempt.status,
                                self._json(attempt),
                            )
                            for _, attempt in claimed
                        ],
                    )
                if claimed or blocked_count:
                    self._refresh_run_in_cursor(
                        cur,
                        run_id,
                        now,
                        status_deltas=([("queued", "claimed")] * len(claimed)
                            + [("queued", "blocked")] * blocked_count),
                    )
        if blocked_count:
            logger.info("test_run_dependencies_blocked", extra={"run_id": run_id, "blocked_count": blocked_count})
        return claimed

    def _start_item_sync(
        self,
        item_id: str,
        lease_token: str,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._item_table} WHERE id=%s", (item_id,))
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"Test run item not found: {item_id}")
                original = self._item_from_value(row["record"])
                # Terminal replay needs only its original result/token, even after
                # archive, revoke or a change to the current environment decisions.
                if original.status == "blocked":
                    item = self._lock_item(cur, item_id)
                    if self._environment_block_replayed(cur, item, lease_token):
                        return item
                    self._validate_lease(item, lease_token, now, {"claimed"})
                if original.status == "running":
                    item = self._lock_item(cur,item_id)
                    if self._environment_running_replayed(cur,item,lease_token,now):
                        return item
                    self._validate_lease(item,lease_token,now,{"claimed"})
                run = self._read_run(cur, original.run_id)
                # Read immutable evidence before project/item locks. Recheck the
                # locked attempt below; preserve project -> item -> attempt order.
                selected_attempt = self._read_attempt(cur, item_id, lease_token)
                run = self._run_for_attempt_in_cursor(cur, run, selected_attempt)
                reviews = self._current_environment_reviews(cur, run)
                item = self._lock_item(cur, item_id)
                if self._environment_block_replayed(cur, item, lease_token):
                    return item
                if self._environment_running_replayed(cur,item,lease_token,now):
                    return item
                self._validate_lease(item, lease_token, now, {"claimed"})
                if item.dependencies:
                    cur.execute(f"SELECT candidate.id FROM {self._item_table} AS candidate "
                        f"WHERE candidate.id=%s AND {self._dependencies_ready_sql()}", (item.id,))
                    if cur.fetchone() is None:
                        raise ValueError("运行条目的必要前置或输出尚未满足")
                attempt = self._lock_attempt(cur, item_id, lease_token)
                if attempt.plan_snapshot_identity != selected_attempt.plan_snapshot_identity:
                    logger.warning("test_run_claimed_snapshot_selection_changed", extra={"run_id": run.id,
                        "run_item_id": item.id, "attempt_id": attempt.id})
                    raise ValueError("领取计划身份在环境确认期间变化，不能使用陈旧依据开始执行")
                validate_claimed_plan_snapshot(run, attempt)
                restricted = restriction_reviews(run.plan_snapshot.plan, item, reviews) if reviews else []
                if restricted and not self._prior_business_started(cur, item):
                    updated, ended, result = blocked_environment_records(run, item, restricted, now, "start",
                        attempt=attempt, current_reviews=reviews)
                    self._save_unexecuted_in_cursor(cur, updated, ended, result, existing_attempt=True)
                    dependent = self._block_failed_dependencies_in_cursor(cur, run, now)
                    self._refresh_run_in_cursor(cur, run.id, now,
                        status_deltas=[("claimed", "blocked")]+[("queued", "blocked")]*dependent)
                    logger.info("test_run_environment_restricted", extra={"run_id": run.id,
                        "run_item_id": item.id, "phase": "start", "review_ids": [r.id for r in restricted]})
                    return updated
                if reviews is not None and attempt.environment_admission is None:
                    if not self._prior_business_started(cur, item):
                        attempt = attempt.model_copy(update={"environment_admission":
                            create_environment_admission(run,item,attempt,reviews,now)})
                        logger.info("test_run_environment_admission_saved",extra={"project_id":run.project_id,
                            "run_id":run.id,"run_item_id":item.id,"attempt_id":attempt.id,
                            "review_ids":[r.id for r in reviews],"admission_sha256":attempt.environment_admission.sha256})
                    else:
                        logger.warning("test_run_historical_environment_admission_missing",extra={"run_id":run.id,
                            "run_item_id":item.id,"attempt_id":attempt.id,"reason":"preserve_original_started_context"})
                item = item.model_copy(
                    update={
                        "status": "running",
                        "started_at": item.started_at or now,
                        "active_started_at": item.active_started_at or now,
                        "updated_at": now,
                    }
                )
                attempt = attempt.model_copy(
                    update={
                        "status": "running",
                        "started_at": attempt.started_at or now,
                        "active_started_at": attempt.active_started_at or now,
                        "heartbeat_at": now,
                    }
                )
                self._write_item(cur, item)
                self._write_attempt(cur, attempt)
                self._refresh_run_in_cursor(
                    cur,
                    item.run_id,
                    now,
                    status_deltas=[("claimed", "running")],
                )
        return item

    def _mark_waiting_resource_sync(
        self,
        item_id: str,
        lease_token: str,
        reason: str,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                self._validate_lease(item, lease_token, now, {"running"})
                attempt = self._lock_attempt(cur, item_id, lease_token)
                updated = item.model_copy(update={
                    "status": "waiting_resource",
                    "waiting_reason": str(reason or "resource_unavailable")[:240],
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=item.active_duration_ms,
                        active_started_at=item.active_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": now,
                    "updated_at": now,
                })
                self._write_item(cur, updated)
                self._write_attempt(cur, attempt.model_copy(update={
                    "active_duration_ms": add_active_duration(
                        active_duration_ms=attempt.active_duration_ms,
                        active_started_at=attempt.active_started_at,
                        now=now,
                    ),
                    "active_started_at": None,
                    "waiting_started_at": now,
                }))
                self._refresh_run_delta_in_cursor(
                    cur, item.run_id, now, [("running", "waiting_resource")],
                    active_duration_delta_ms=updated.active_duration_ms - item.active_duration_ms,
                )
        return updated

    def _heartbeat_item_sync(
        self,
        item_id: str,
        lease_token: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                self._validate_lease(item, lease_token, now, {"claimed", "running"})
                attempt = self._lock_attempt(cur, item_id, lease_token)
                item = item.model_copy(
                    update={
                        "heartbeat_at": now,
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "updated_at": now,
                    }
                )
                attempt = attempt.model_copy(update={"heartbeat_at": now})
                self._write_item(cur, item)
                self._write_attempt(cur, attempt)
        return item

    def _save_checkpoint_sync(
        self,
        item_id: str,
        lease_token: str,
        checkpoint_key: str,
        checkpoint_payload: dict[str, object],
        checkpoint_version: int | None,
        now: datetime,
    ) -> TestRunAttemptRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                self._validate_lease(item, lease_token, now, {"claimed", "running"})
                attempt = self._lock_attempt(cur, item_id, lease_token)
                require_generic_checkpoint_write(attempt, checkpoint_key)
                version = checkpoint_version or attempt.checkpoint_version + 1
                if version <= attempt.checkpoint_version:
                    raise ValueError(f"Checkpoint version must increase for run item: {item_id}")
                attempt = attempt.model_copy(
                    update={
                        "checkpoint_version": version,
                        "checkpoint_key": checkpoint_key,
                        "checkpoint_payload": dict(checkpoint_payload),
                        "checkpoint_at": now,
                        "heartbeat_at": now,
                    }
                )
                self._write_attempt(cur, attempt)
        return attempt

    def _complete_item_sync(
        self,
        item_id: str,
        lease_token: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                return self._complete_item_in_transaction(cur, item_id, lease_token, completion, now)

    def _complete_item_in_transaction(
        self, cur, item_id: str, lease_token: str, completion: RunItemCompletion, now: datetime,
    ) -> TestCaseResultRecord:
        """Original generic completion, using only the caller's transaction.

        The reservation guard still applies here, including idempotent reads.
        This is not a controlled-case settlement or an owner-check bypass.
        """
        item = self._lock_item(cur, item_id)
        if item.result_id:
            result = self._get_result_in_cursor(cur, item.result_id)
            attempt = self._get_attempt_by_id(cur, result.attempt_id)
            require_generic_result_write(attempt)
            if (
                attempt.lease_token == lease_token
                and result.payload_hash == completion.payload_hash
            ):
                return result
            raise ValueError(f"Test run item already completed: {item_id}")
        self._validate_lease(item, lease_token, now, {"running"})
        attempt = self._lock_attempt(cur, item_id, lease_token)
        require_generic_result_write(attempt)
        return self._complete_locked_item_in_transaction(cur, item, attempt, completion, now)

    def _complete_locked_item_in_transaction(
        self, cur, item: TestRunItemRecord, attempt: TestRunAttemptRecord,
        completion: RunItemCompletion, now: datetime,
    ) -> TestCaseResultRecord:
        """Original result/item/attempt/totals writer; never opens or commits a connection.

        Internal primitive, like _write_item/_write_attempt: its caller must
        hold the original locks and validate execution authority before writing.
        It does not grant settlement or recovery authority to a reserved case.
        """
        active_duration_delta_ms = elapsed_ms(item.active_started_at, now)
        waiting_duration_delta_ms = elapsed_ms(item.waiting_started_at, now)
        result = TestCaseResultRecord(
            id=str(uuid4()),
            run_id=item.run_id,
            run_item_id=item.id,
            case_id=item.case_id,
            case_version_id=item.case_version_id,
            regression_source_result_id=item.regression_source_result_id,
            attempt_id=attempt.id,
            attempt_no=attempt.attempt_no,
            created_at=now,
            **completion.model_dump(mode="python"),
        )
        cur.execute(
            f"INSERT INTO {self._result_table} "
            "(id, run_id, run_item_id, case_id, case_version_id, "
            "regression_source_result_id, attempt_id, status, payload_hash, "
            "created_at, record) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            (
                result.id,
                result.run_id,
                result.run_item_id,
                result.case_id,
                result.case_version_id,
                result.regression_source_result_id,
                result.attempt_id,
                result.status,
                result.payload_hash,
                result.created_at,
                self._json(result),
            ),
        )
        item = item.model_copy(
            update={
                "status": completion.status,
                "result_id": result.id,
                "completed_at": now,
                "active_duration_ms": add_active_duration(
                    active_duration_ms=item.active_duration_ms,
                    active_started_at=item.active_started_at,
                    now=now,
                ),
                "waiting_duration_ms": add_waiting_duration(
                    waiting_duration_ms=item.waiting_duration_ms,
                    waiting_started_at=item.waiting_started_at,
                    now=now,
                ),
                "active_started_at": None,
                "waiting_started_at": None,
                "wall_clock_duration_ms": elapsed_ms(item.created_at, now),
                "updated_at": now,
            }
        )
        attempt = attempt.model_copy(
            update={
                "status": completion.status,
                "completed_at": now,
                "active_duration_ms": add_active_duration(
                    active_duration_ms=attempt.active_duration_ms,
                    active_started_at=attempt.active_started_at,
                    now=now,
                ),
                "waiting_duration_ms": add_waiting_duration(
                    waiting_duration_ms=attempt.waiting_duration_ms,
                    waiting_started_at=attempt.waiting_started_at,
                    now=now,
                ),
                "active_started_at": None,
                "waiting_started_at": None,
                "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
            }
        )
        self._write_item(cur, item)
        self._write_attempt(cur, attempt)
        self._refresh_run_in_cursor(
            cur,
            item.run_id,
            now,
            status_deltas=[("running", completion.status)],
            active_duration_delta_ms=active_duration_delta_ms,
            waiting_duration_delta_ms=waiting_duration_delta_ms,
        )
        return result

    def _mark_waiting_approval_sync(
        self,
        item_id: str,
        lease_token: str,
        approval_id: str,
        tool_job_id: str,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                return self._mark_waiting_approval_in_transaction(
                    cur, item_id, lease_token, approval_id, tool_job_id, now,
                )

    def _mark_waiting_approval_in_transaction(
        self, cur, item_id: str, lease_token: str, approval_id: str,
        tool_job_id: str, now: datetime,
    ) -> TestRunItemRecord:
        item = self._lock_item(cur, item_id)
        self._validate_lease(item, lease_token, now, {"running"})
        attempt = self._lock_attempt(cur, item_id, lease_token)
        active_duration_delta_ms = elapsed_ms(item.active_started_at, now)
        item = item.model_copy(
            update={
                "status": "waiting_approval",
                "lease_expires_at": None,
                "heartbeat_at": now,
                "approval_id": approval_id,
                "tool_job_id": tool_job_id,
                "active_duration_ms": add_active_duration(
                    active_duration_ms=item.active_duration_ms,
                    active_started_at=item.active_started_at,
                    now=now,
                ),
                "active_started_at": None,
                "waiting_started_at": now,
                "updated_at": now,
            }
        )
        attempt = attempt.model_copy(
            update={
                "status": "waiting_approval",
                "heartbeat_at": now,
                "approval_id": approval_id,
                "tool_job_id": tool_job_id,
                "active_duration_ms": add_active_duration(
                    active_duration_ms=attempt.active_duration_ms,
                    active_started_at=attempt.active_started_at,
                    now=now,
                ),
                "active_started_at": None,
                "waiting_started_at": now,
            }
        )
        self._write_item(cur, item)
        self._write_attempt(cur, attempt)
        self._refresh_run_in_cursor(
            cur,
            item.run_id,
            now,
            status_deltas=[("running", "waiting_approval")],
            active_duration_delta_ms=active_duration_delta_ms,
        )
        return item

    async def commit_approval_wait(
        self, item_id: str, lease_token: str, approval: ToolApprovalRequest,
        job: ToolJobRecord, session_store, job_service,
    ) -> TestRunItemRecord:
        item, event = await asyncio.to_thread(
            self._commit_approval_wait_sync, item_id, lease_token, approval, job,
            session_store, job_service,
        )
        if event is not None:
            await session_store._publish_hot_event(approval.session_id, event)
        return item

    def _commit_approval_wait_sync(
        self, item_id: str, lease_token: str, approval: ToolApprovalRequest,
        job: ToolJobRecord, session_store, job_service,
    ) -> tuple[TestRunItemRecord, ExecutionEvent | None]:
        # Reuse append_event_in_transaction and the existing item/attempt/run
        # transition so every approval prerequisite commits or rolls back together.
        with postgres_connect(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM {self._settings.database.postgres_session_table} "
                "WHERE id=%s FOR UPDATE", (approval.session_id,),
            )
            if cur.fetchone() is None:
                raise KeyError(approval.session_id)
            item = self._lock_item(cur, item_id)
            if item.lease_token != lease_token:
                raise ValueError(f"Run item approval lease changed: {item_id}")
            cur.execute(f"SELECT record FROM {self._run_table} WHERE id=%s", (item.run_id,))
            run = TestRunRecord.model_validate(cur.fetchone()["record"])
            if (
                run.session_id != approval.session_id or job.session_id != approval.session_id
                or approval.metadata.get("run_item_id") != item_id
                or job.metadata.get("run_item_id") != item_id
                or approval.metadata.get("tool_job_id") != job.id
                or approval.tool_key != job.tool_key
                or approval.metadata.get("case_version_id") != item.case_version_id
            ):
                raise ValueError(f"Run item approval resources do not match: {item_id}")
            if item.status == "waiting_approval":
                if item.approval_id != approval.id or item.tool_job_id != job.id:
                    raise ValueError(f"Run item already waits for another approval: {item_id}")
                cur.execute(
                    f"SELECT metadata FROM {self._settings.database.postgres_approval_table} "
                    "WHERE id=%s AND session_id=%s", (approval.id, approval.session_id),
                )
                stored = cur.fetchone()
                if not stored or stored["metadata"].get("approval_scope_hash") != approval.metadata.get("approval_scope_hash"):
                    raise ValueError(f"Run item approval scope changed: {item_id}")
                cur.execute(
                    f"SELECT session_id, tool_key, input_payload FROM {self._settings.database.postgres_tool_job_table} "
                    "WHERE id=%s", (job.id,),
                )
                stored_job = cur.fetchone()
                if (
                    not stored_job or stored_job["session_id"] != job.session_id
                    or stored_job["tool_key"] != job.tool_key or stored_job["input_payload"] != job.input_payload
                ):
                    raise ValueError(f"Run item approval ToolJob missing or changed: {item_id}")
                return item, None
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"].astimezone(UTC)
            self._validate_lease(item, lease_token, now, {"running"})
            if item.approval_id or item.tool_job_id:
                raise ValueError(f"Run item must resume its existing approval: {item_id}")
            job_service.save_job_in_transaction(cur, job)
            session_store.save_approval_in_transaction(cur, approval.session_id, approval)
            waiting = self._mark_waiting_approval_in_transaction(
                cur, item_id, lease_token, approval.id, job.id, now,
            )
            event = ExecutionEvent(
                id=f"test-run-approval-wait:{approval.id}",
                type="test_run.item_waiting_approval", session_id=approval.session_id,
                timestamp=now,
                payload={"run_id": item.run_id, "run_item_id": item.id, "status": waiting.status},
            )
            session_store.append_event_in_transaction(cur, approval.session_id, event)
            return waiting, event

    def _resume_waiting_approval_sync(
        self,
        item_id: str,
        approval_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                if item.status == "claimed" and item.approval_id == approval_id:
                    if (
                        not item.lease_token
                        or item.lease_expires_at is None
                        or item.lease_expires_at <= now
                    ):
                        raise ValueError(f"Resumed run item lease is not active: {item_id}")
                    attempt = self._lock_attempt(cur, item_id, item.lease_token)
                    require_generic_approval_resume(attempt)
                    if attempt.status != "claimed":
                        raise ValueError(f"Resumed run item attempt is not claimed: {item_id}")
                    return item
                if item.status != "waiting_approval" or item.approval_id != approval_id:
                    raise ValueError(f"Run item is not waiting for this approval: {item_id}")
                if not item.lease_token:
                    raise ValueError(f"Waiting run item has no preserved lease token: {item_id}")
                attempt = self._lock_attempt(cur, item_id, item.lease_token)
                require_generic_approval_resume(attempt)
                waiting_duration_delta_ms = elapsed_ms(item.waiting_started_at, now)
                item = item.model_copy(
                    update={
                        "status": "claimed",
                        "lease_expires_at": now + timedelta(seconds=lease_seconds),
                        "heartbeat_at": now,
                        "waiting_duration_ms": add_waiting_duration(
                            waiting_duration_ms=item.waiting_duration_ms,
                            waiting_started_at=item.waiting_started_at,
                            now=now,
                        ),
                        "waiting_started_at": None,
                        "updated_at": now,
                    }
                )
                attempt = attempt.model_copy(
                    update={
                        "status": "claimed",
                        "heartbeat_at": now,
                        "waiting_duration_ms": add_waiting_duration(
                            waiting_duration_ms=attempt.waiting_duration_ms,
                            waiting_started_at=attempt.waiting_started_at,
                            now=now,
                        ),
                        "waiting_started_at": None,
                    }
                )
                self._write_item(cur, item)
                self._write_attempt(cur, attempt)
                self._refresh_run_in_cursor(
                    cur,
                    item.run_id,
                    now,
                    status_deltas=[("waiting_approval", "claimed")],
                    waiting_duration_delta_ms=waiting_duration_delta_ms,
                )
        return item

    def _list_waiting_approval_items_sync(
        self, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]:
        query = (
            f"SELECT record FROM {self._item_table} "
            "WHERE status = 'waiting_approval' "
        )
        params: list[object] = []
        if after is not None:
            query += "AND (updated_at, id) > (%s, %s::uuid) "
            cursor_time = after[0]
            params.extend((
                cursor_time.replace(tzinfo=UTC) if cursor_time.tzinfo is None else cursor_time,
                after[1],
            ))
        query += "ORDER BY updated_at ASC, id ASC"
        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall() or []
        return [self._item_from_value(row["record"]) for row in rows]

    def _list_cancelled_resource_items_sync(
        self, limit: int | None = None, after: tuple[datetime, str] | None = None,
    ) -> list[TestRunItemRecord]:
        query = (
            f"SELECT record FROM {self._item_table} "
            "WHERE status = 'cancelled' "
            "AND ((record->>'approval_id') IS NOT NULL "
            "OR (record->>'tool_job_id') IS NOT NULL) "
            "AND (record->>'resource_cleanup_completed_at') IS NULL "
        )
        params: list[object] = []
        if after is not None:
            query += "AND (updated_at, id) > (%s, %s::uuid) "
            cursor_time = after[0]
            params.extend((
                cursor_time.replace(tzinfo=UTC) if cursor_time.tzinfo is None else cursor_time,
                after[1],
            ))
        query += "ORDER BY updated_at ASC, id ASC"
        if limit is not None:
            query += " LIMIT %s"
            params.append(limit)
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall() or []
        return [self._item_from_value(row["record"]) for row in rows]

    def _mark_cancelled_resources_reconciled_sync(
        self,
        item_id: str,
        now: datetime,
    ) -> TestRunItemRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                if item.status != "cancelled":
                    raise ValueError(f"Run item is not cancelled: {item_id}")
                item = item.model_copy(
                    update={
                        "resource_cleanup_completed_at": now,
                        "updated_at": now,
                    }
                )
                self._write_item(cur, item)
        return item

    def _complete_denied_approval_sync(
        self,
        item_id: str,
        approval_id: str,
        completion: RunItemCompletion,
        now: datetime,
    ) -> TestCaseResultRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                item = self._lock_item(cur, item_id)
                if item.approval_id != approval_id:
                    raise ValueError(f"Run item is not bound to this approval: {item_id}")
                if item.result_id:
                    existing = self._get_result_in_cursor(cur, item.result_id)
                    require_generic_result_write(self._get_attempt_by_id(cur, existing.attempt_id))
                    return existing
                if item.status != "waiting_approval":
                    raise ValueError(f"Run item is not waiting for this approval: {item_id}")
                if completion.status != "blocked":
                    raise ValueError("Denied approval completion status must be blocked.")
                if not item.lease_token:
                    raise ValueError(f"Waiting run item has no preserved lease token: {item_id}")
                attempt = self._lock_attempt(cur, item_id, item.lease_token)
                require_generic_result_write(attempt)
                waiting_duration_delta_ms = elapsed_ms(item.waiting_started_at, now)
                result = TestCaseResultRecord(
                    id=str(uuid4()),
                    run_id=item.run_id,
                    run_item_id=item.id,
                    case_id=item.case_id,
                    case_version_id=item.case_version_id,
                    regression_source_result_id=item.regression_source_result_id,
                    attempt_id=attempt.id,
                    attempt_no=attempt.attempt_no,
                    created_at=now,
                    **completion.model_dump(mode="python"),
                )
                cur.execute(
                    f"INSERT INTO {self._result_table} "
                    "(id, run_id, run_item_id, case_id, case_version_id, "
                    "regression_source_result_id, attempt_id, status, payload_hash, "
                    "created_at, record) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                    (
                        result.id,
                        result.run_id,
                        result.run_item_id,
                        result.case_id,
                        result.case_version_id,
                        result.regression_source_result_id,
                        result.attempt_id,
                        result.status,
                        result.payload_hash,
                        result.created_at,
                        self._json(result),
                    ),
                )
                self._write_item(
                    cur,
                    item.model_copy(
                        update={
                            "status": "blocked",
                            "result_id": result.id,
                            "completed_at": now,
                            "updated_at": now,
                            "waiting_duration_ms": add_waiting_duration(
                                waiting_duration_ms=item.waiting_duration_ms,
                                waiting_started_at=item.waiting_started_at,
                                now=now,
                            ),
                            "waiting_started_at": None,
                            "wall_clock_duration_ms": elapsed_ms(item.created_at, now),
                        }
                    ),
                )
                self._write_attempt(
                    cur,
                    attempt.model_copy(
                        update={
                            "status": "blocked",
                            "completed_at": now,
                            "waiting_duration_ms": add_waiting_duration(
                                waiting_duration_ms=attempt.waiting_duration_ms,
                                waiting_started_at=attempt.waiting_started_at,
                                now=now,
                            ),
                            "waiting_started_at": None,
                            "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
                        }
                    ),
                )
                self._refresh_run_in_cursor(
                    cur,
                    item.run_id,
                    now,
                    status_deltas=[("waiting_approval", "blocked")],
                    waiting_duration_delta_ms=waiting_duration_delta_ms,
                )
        return result

    def _recover_expired_sync(self, run_id: str, now: datetime) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._read_run(cur, run_id)
                cur.execute(
                    f"SELECT record FROM {self._item_table} "
                    "WHERE run_id = %s AND status IN ('claimed', 'running', 'waiting_resource') "
                    "AND lease_expires_at <= %s FOR UPDATE SKIP LOCKED",
                    (run_id, now),
                )
                expired = [
                    self._item_from_value(row["record"])
                    for row in (cur.fetchall() or [])
                ]
                attempts: list[TestRunAttemptRecord] = []
                queued: list[TestRunItemRecord] = []
                for item in expired:
                    attempt = self._lock_attempt(cur, item.id, item.lease_token) if item.lease_token else None
                    if attempt is not None and not can_requeue_expired_attempt(attempt):
                        continue
                    interval_end = min(now, item.lease_expires_at)
                    if attempt is not None:
                        attempts.append(
                            attempt.model_copy(
                                update={
                                    "status": "expired",
                                    "completed_at": now,
                                    "active_duration_ms": add_active_duration(
                                        active_duration_ms=attempt.active_duration_ms,
                                        active_started_at=attempt.active_started_at,
                                        now=interval_end,
                                    ),
                                    "waiting_duration_ms": add_waiting_duration(
                                        waiting_duration_ms=attempt.waiting_duration_ms,
                                        waiting_started_at=attempt.waiting_started_at,
                                        now=interval_end,
                                    ),
                                    "active_started_at": None,
                                    "waiting_started_at": None,
                                    "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
                                }
                            )
                        )
                    queued.append(
                        item.model_copy(
                            update={
                                "status": "queued",
                                "lease_owner": None,
                                "lease_token": None,
                                "lease_expires_at": None,
                                "heartbeat_at": None,
                                "started_at": None,
                                "updated_at": now,
                                "active_duration_ms": add_active_duration(
                                    active_duration_ms=item.active_duration_ms,
                                    active_started_at=item.active_started_at,
                                    now=interval_end,
                                ),
                                "waiting_duration_ms": add_waiting_duration(
                                    waiting_duration_ms=item.waiting_duration_ms,
                                    waiting_started_at=item.waiting_started_at,
                                    now=interval_end,
                                ),
                                "active_started_at": None,
                                "waiting_started_at": None,
                            }
                        )
                    )
                for item in queued:
                    self._write_item(cur, item)
                for attempt in attempts:
                    self._write_attempt(cur, attempt)
                if queued:
                    self._refresh_run_in_cursor(cur, run_id, now)
        return len(queued)

    def _recover_all_expired_sync(self, now: datetime) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT DISTINCT run_id FROM {self._item_table} "
                    "WHERE status IN ('claimed', 'running', 'waiting_resource') AND lease_expires_at <= %s",
                    (now,),
                )
                run_ids = [str(row["run_id"]) for row in (cur.fetchall() or [])]
        return sum(self._recover_expired_sync(run_id, now) for run_id in run_ids)

    def _cancel_run_sync(
        self,
        run_id: str,
        reason: str,
        now: datetime,
    ) -> TestRunDetail:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                self._lock_plan_projection(cur, run_id)
                run = self._read_run(cur, run_id)
                if run.status == "completed":
                    raise ValueError(f"Completed test run cannot be cancelled: {run_id}")
                if run.status == "cancelled":
                    detail = self._detail_from_cursor(cur, run_id)
                    if detail is None:
                        raise KeyError(f"Test run not found: {run_id}")
                    return detail
                cur.execute(
                    f"SELECT record FROM {self._item_table} "
                    "WHERE run_id = %s AND status NOT IN "
                    "('passed', 'failed', 'error', 'blocked', 'skipped', 'cancelled') "
                    "FOR UPDATE",
                    (run_id,),
                )
                items = [
                    self._item_from_value(row["record"])
                    for row in (cur.fetchall() or [])
                ]
                run = self._lock_run(cur, run_id)
                if run.status == "completed":
                    raise ValueError(f"Completed test run cannot be cancelled: {run_id}")
                if run.status == "cancelled":
                    detail = self._detail_from_cursor(cur, run_id)
                    if detail is None:
                        raise KeyError(f"Test run not found: {run_id}")
                    return detail
                for item in items:
                    if item.lease_token and item.status in ACTIVE_ITEM_STATUSES | {"waiting_approval", "waiting_resource"}:
                        attempt = self._lock_attempt(cur, item.id, item.lease_token)
                        self._write_attempt(
                            cur,
                            attempt.model_copy(
                                update={
                                    "status": "cancelled",
                                    "completed_at": now,
                                    "active_duration_ms": add_active_duration(
                                        active_duration_ms=attempt.active_duration_ms,
                                        active_started_at=attempt.active_started_at,
                                        now=now,
                                    ),
                                    "waiting_duration_ms": add_waiting_duration(
                                        waiting_duration_ms=attempt.waiting_duration_ms,
                                        waiting_started_at=attempt.waiting_started_at,
                                        now=now,
                                    ),
                                    "active_started_at": None,
                                    "waiting_started_at": None,
                                    "wall_clock_duration_ms": elapsed_ms(attempt.claimed_at, now),
                                }
                            ),
                        )
                    self._write_item(
                        cur,
                        item.model_copy(
                            update={
                                "status": "cancelled",
                                "completed_at": now,
                                "updated_at": now,
                                "active_duration_ms": add_active_duration(
                                    active_duration_ms=item.active_duration_ms,
                                    active_started_at=item.active_started_at,
                                    now=now,
                                ),
                                "waiting_duration_ms": add_waiting_duration(
                                    waiting_duration_ms=item.waiting_duration_ms,
                                    waiting_started_at=item.waiting_started_at,
                                    now=now,
                                ),
                                "active_started_at": None,
                                "waiting_started_at": None,
                                "wall_clock_duration_ms": elapsed_ms(item.created_at, now),
                            }
                        ),
                    )
                cancelled = run.model_copy(
                    update={
                        "status": "cancelled",
                        "cancel_reason": reason,
                        "completed_at": now,
                        "updated_at": now,
                    }
                )
                self._write_run(cur, cancelled)
                self._refresh_run_in_cursor(
                    cur,
                    run_id,
                    now,
                    preserve_cancelled=True,
                )
                detail = self._detail_from_cursor(cur, run_id)
                if detail is None:
                    raise KeyError(f"Test run not found: {run_id}")
                return detail

    def _count_by_project_sync(self, project_id: str) -> int:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT count(*) AS total FROM {self._run_table} WHERE project_id = %s",
                    (project_id,),
                )
                row = cur.fetchone() or {}
        return int(row.get("total") or 0)

    def _detail_from_cursor(self, cur, run_id: str) -> TestRunDetail | None:
        # READ COMMITTED takes a snapshot per statement. Keep all projections in
        # one SELECT so a concurrent claim/completion cannot mix old run totals
        # with new items or receipts. This also sees this transaction's writes.
        # https://www.postgresql.org/docs/17/transaction-iso.html
        cur.execute(
            "SELECT saved.record, "
            f"COALESCE((SELECT jsonb_agg(item.record ORDER BY item.position ASC) FROM {self._item_table} AS item "
            "WHERE item.run_id = saved.id), '[]'::jsonb) AS items, "
            f"COALESCE((SELECT jsonb_agg(attempt.record ORDER BY attempt.run_item_id, attempt.attempt_no ASC) "
            f"FROM {self._attempt_table} AS attempt WHERE attempt.run_id = saved.id), '[]'::jsonb) AS attempts, "
            f"COALESCE((SELECT jsonb_agg(result.record ORDER BY result.created_at ASC) FROM {self._result_table} AS result "
            "WHERE result.run_id = saved.id), '[]'::jsonb) AS results "
            f"FROM {self._run_table} AS saved WHERE saved.id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return TestRunDetail(
            run=self._run_from_value(row["record"]),
            items=[self._item_from_value(value) for value in self._value(row["items"])],
            attempts=[self._attempt_from_value(value) for value in self._value(row["attempts"])],
            results=[self._result_from_value(value) for value in self._value(row["results"])],
        )

    def _lock_run(self, cur, run_id: str) -> TestRunRecord:
        cur.execute(
            f"SELECT record FROM {self._run_table} WHERE id = %s FOR NO KEY UPDATE",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run not found: {run_id}")
        return self._run_from_value(row["record"])

    def _read_run(self, cur, run_id: str) -> TestRunRecord:
        cur.execute(
            f"SELECT record FROM {self._run_table} WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run not found: {run_id}")
        return self._run_from_value(row["record"])

    def _lock_item(self, cur, item_id: str) -> TestRunItemRecord:
        cur.execute(
            f"SELECT record FROM {self._item_table} WHERE id = %s FOR UPDATE",
            (item_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run item not found: {item_id}")
        return self._item_from_value(row["record"])

    def _lock_attempt(
        self,
        cur,
        item_id: str,
        lease_token: str,
    ) -> TestRunAttemptRecord:
        cur.execute(
            f"SELECT record FROM {self._attempt_table} "
            "WHERE run_item_id = %s AND lease_token = %s "
            "ORDER BY attempt_no DESC LIMIT 1 FOR UPDATE",
            (item_id, lease_token),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Active attempt not found for run item: {item_id}")
        return self._attempt_from_value(row["record"])

    def _get_attempt_by_id(self, cur, attempt_id: str) -> TestRunAttemptRecord:
        cur.execute(
            f"SELECT record FROM {self._attempt_table} WHERE id = %s",
            (attempt_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run attempt not found: {attempt_id}")
        return self._attempt_from_value(row["record"])

    def _get_result_in_cursor(self, cur, result_id: str) -> TestCaseResultRecord:
        cur.execute(
            f"SELECT record FROM {self._result_table} WHERE id = %s",
            (result_id,),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test case result not found: {result_id}")
        return self._result_from_value(row["record"])

    @staticmethod
    def _validate_lease(
        item: TestRunItemRecord,
        lease_token: str,
        now: datetime,
        allowed_statuses: set[str],
    ) -> None:
        if item.status not in allowed_statuses:
            raise ValueError(
                f"Illegal run item transition from {item.status}: {item.id}"
            )
        if item.lease_token != lease_token:
            raise ValueError(f"Invalid lease token for run item: {item.id}")
        if item.lease_expires_at is None or item.lease_expires_at <= now:
            raise ValueError(f"Lease expired for run item: {item.id}")

    def _refresh_run_in_cursor(
        self,
        cur,
        run_id: str,
        now: datetime,
        *,
        preserve_cancelled: bool = False,
        status_deltas: list[tuple[str, str]] | None = None,
        active_duration_delta_ms: int = 0,
        waiting_duration_delta_ms: int = 0,
    ) -> TestRunRecord:
        if status_deltas is not None and not preserve_cancelled:
            return self._refresh_run_delta_in_cursor(
                cur, run_id, now, status_deltas,
                active_duration_delta_ms=active_duration_delta_ms,
                waiting_duration_delta_ms=waiting_duration_delta_ms,
            )
        run = self._lock_run(cur, run_id)
        cur.execute(
            f"SELECT status, count(*) AS total FROM {self._item_table} "
            "WHERE run_id = %s GROUP BY status",
            (run_id,),
        )
        rows = cur.fetchall() or []
        counts = {status: 0 for status in TestRunStats.model_fields if status != "total"}
        total = 0
        for row in rows:
            status = str(row["status"])
            count = int(row["total"])
            if status in counts:
                counts[status] = count
            total += count
        cur.execute(
            f"SELECT record FROM {self._item_table} WHERE run_id = %s",
            (run_id,),
        )
        item_records = [self._item_from_value(row["record"]) for row in (cur.fetchall() or [])]
        stats = TestRunStats(total=total, **counts)
        terminal_count = sum(counts[status] for status in TERMINAL_ITEM_STATUSES)
        if preserve_cancelled or run.status == "cancelled":
            status: TestRunStatus = "cancelled"
        elif total > 0 and terminal_count == total:
            status = "completed"
        elif total > counts["queued"]:
            status = "running"
        else:
            status = "queued"
        started_at = run.started_at
        if status == "running" and started_at is None:
            started_at = now
        completed_at = run.completed_at
        if status == "completed" and completed_at is None:
            completed_at = now
        updated = run.model_copy(
            update={
                "status": status,
                "stats": stats,
                "started_at": started_at,
                "completed_at": completed_at,
                "active_duration_ms": sum(item.active_duration_ms for item in item_records),
                "waiting_duration_ms": sum(item.waiting_duration_ms for item in item_records),
                "wall_clock_duration_ms": elapsed_ms(run.created_at, completed_at or now),
                "updated_at": now,
            }
        )
        self._write_run(cur, updated)
        return updated

    def _refresh_run_delta_in_cursor(
        self,
        cur,
        run_id: str,
        now: datetime,
        status_deltas: list[tuple[str, str]],
        *,
        active_duration_delta_ms: int = 0,
        waiting_duration_delta_ms: int = 0,
    ) -> TestRunRecord:
        if active_duration_delta_ms < 0 or waiting_duration_delta_ms < 0:
            raise ValueError("Run duration deltas cannot be negative")
        statuses = [status for status in TestRunStats.model_fields if status != "total"]
        deltas = {status: 0 for status in statuses}
        for from_status, to_status in status_deltas:
            if from_status not in deltas or to_status not in deltas:
                raise ValueError(
                    f"Unsupported run item status delta: {from_status} -> {to_status}"
                )
            if from_status == to_status:
                continue
            deltas[from_status] -= 1
            deltas[to_status] += 1

        def current_count(status: str) -> str:
            return f"COALESCE((record #>> '{{stats,{status}}}')::int, 0)"

        def next_count(status: str) -> str:
            delta = deltas[status]
            return f"({current_count(status)} + {delta})"

        total_expr = current_count("total")
        queued_expr = next_count("queued")
        terminal_expr = " + ".join(
            next_count(status) for status in TERMINAL_ITEM_STATUSES
        )
        next_status = (
            "CASE "
            "WHEN status = 'cancelled' THEN 'cancelled' "
            f"WHEN {total_expr} > 0 AND ({terminal_expr}) = {total_expr} "
            "THEN 'completed' "
            f"WHEN {total_expr} > {queued_expr} THEN 'running' "
            "ELSE 'queued' END"
        )
        stats_pairs = [f"'total', {total_expr}"] + [
            f"'{status}', {next_count(status)}" for status in statuses
        ]
        stats_expr = f"jsonb_build_object({', '.join(stats_pairs)})"
        started_expr = (
            f"CASE WHEN ({next_status}) = 'running' AND record->>'started_at' IS NULL "
            "THEN to_jsonb(%s::timestamptz) "
            "ELSE COALESCE(record->'started_at', 'null'::jsonb) END"
        )
        completed_expr = (
            f"CASE WHEN ({next_status}) = 'completed' AND record->>'completed_at' IS NULL "
            "THEN to_jsonb(%s::timestamptz) "
            "ELSE COALESCE(record->'completed_at', 'null'::jsonb) END"
        )
        cur.execute(
            f"UPDATE {self._run_table} SET status = ({next_status}), updated_at = %s, "
            "record = record || jsonb_build_object("
            f"'status', to_jsonb(({next_status})), "
            f"'stats', {stats_expr}, "
            "'updated_at', to_jsonb(%s::timestamptz), "
            f"'started_at', {started_expr}, "
            f"'completed_at', {completed_expr}, "
            "'active_duration_ms', to_jsonb(COALESCE((record->>'active_duration_ms')::bigint, 0) + %s::bigint), "
            "'waiting_duration_ms', to_jsonb(COALESCE((record->>'waiting_duration_ms')::bigint, 0) + %s::bigint), "
            "'wall_clock_duration_ms', to_jsonb(GREATEST(0, FLOOR(EXTRACT(EPOCH FROM "
            "(%s::timestamptz - created_at)) * 1000))::bigint)"
            ") WHERE id = %s RETURNING record",
            (
                now, now, now, now,
                active_duration_delta_ms, waiting_duration_delta_ms, now, run_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(f"Test run not found: {run_id}")
        return self._run_from_value(row["record"])

    def _current_environment_reviews(self, cur, run):
        if run.plan_snapshot is None:
            return None
        plan = run.plan_snapshot.plan
        if plan.environment is None and plan.request.environment is None:
            return None
        if self._environments is None:
            raise RuntimeError("固定目标运行尚未配置环境适用读取服务")
        return self._environments.current_reviews_in_cursor(cur, plan)

    def _prior_business_started(self, cur, item):
        cur.execute(f"SELECT EXISTS(SELECT 1 FROM {self._attempt_table} WHERE run_item_id=%s "
            "AND (record->>'started_at' IS NOT NULL OR COALESCE((record->>'checkpoint_version')::integer,0)>0)) AS started",
            (item.id,))
        started = bool(cur.fetchone()["started"])
        if started:
            logger.warning("test_run_environment_recovery_preserved", extra={"run_id": item.run_id,
                "run_item_id": item.id, "reason": "prior_started_or_checkpoint_requires_recovery"})
        return started

    def _block_environment_items_in_cursor(self, cur, run, reviews, now, phase):
        if not reviews:
            return 0
        versions = sorted({case.case_version_id for review in reviews for case in review.cases if case.status == "invalid"})
        if not versions:
            return 0
        cur.execute(f"SELECT record FROM {self._item_table} WHERE run_id=%s AND status='queued' "
            "AND case_version_id=ANY(%s::uuid[]) ORDER BY position,id FOR UPDATE SKIP LOCKED", (run.id, versions))
        candidates = [self._item_from_value(row["record"]) for row in cur.fetchall()]
        count = 0
        for item in candidates:
            restricted = restriction_reviews(run.plan_snapshot.plan, item, reviews)
            if not restricted or self._prior_business_started(cur, item):
                continue
            updated, attempt, result = blocked_environment_records(run, item, restricted, now, phase, current_reviews=reviews)
            attempt = attempt.model_copy(update={"plan_snapshot_identity":
                claim_plan_snapshot_identity(run, source="synthetic_blocked_receipt")})
            self._save_unexecuted_in_cursor(cur, updated, attempt, result)
            count += 1
            logger.info("test_run_environment_restricted", extra={"project_id": run.project_id,
                "run_id": run.id, "run_item_id": item.id, "phase": phase,
                "review_ids": [r.id for r in restricted]})
        return count

    def _environment_block_replayed(self, cur, item, lease_token):
        if item.status != "blocked" or not item.result_id or item.lease_token != lease_token:
            return False
        cur.execute(f"SELECT record FROM {self._result_table} WHERE id=%s AND run_item_id=%s", (item.result_id,item.id))
        row = cur.fetchone()
        if row is None:
            return False
        from src.application.documents.requirement_execution_checks import fixed_result
        from src.runtime.postgres_business_environment_store import environment_digest
        result = fixed_result(row["record"])
        receipt = result.actual.get("environment_restriction")
        if receipt and (receipt.get("case_id") != item.case_id or receipt.get("case_version_id") != item.case_version_id
            or receipt.get("sha256") != environment_digest({k:v for k,v in receipt.items() if k != "sha256"})):
            raise ValueError("环境未执行回执与精确版本或原hash不同")
        return result.actual.get("business_execution_started") is False and bool(receipt)

    def _environment_running_replayed(self,cur,item,lease_token,now):
        if item.status != "running" or item.lease_token != lease_token:
            return False
        attempt = self._lock_attempt(cur,item.id,lease_token)
        if attempt.environment_admission is None:
            return False
        self._validate_lease(item,lease_token,now,{"running"})
        detail = self._detail_from_cursor(cur,item.run_id)
        detail.run = self._run_for_attempt_in_cursor(cur, detail.run, attempt)
        fixed_environment_admission(detail.run,item,attempt,detail.attempts)
        logger.info("test_run_environment_start_receipt_replayed",extra={"run_id":item.run_id,
            "run_item_id":item.id,"attempt_id":attempt.id,"admission_sha256":attempt.environment_admission.sha256})
        return True

    def _save_unexecuted_in_cursor(self, cur, item, attempt, result, *, existing_attempt=False):
        if existing_attempt:
            self._write_attempt(cur, attempt)
        else:
            cur.execute(f"INSERT INTO {self._attempt_table} "
                "(id,run_id,run_item_id,attempt_no,lease_token,status,record) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (attempt.id, attempt.run_id, attempt.run_item_id, attempt.attempt_no,
                 attempt.lease_token, attempt.status, self._json(attempt)))
        cur.execute(f"INSERT INTO {self._result_table} "
            "(id,run_id,run_item_id,case_id,case_version_id,regression_source_result_id,"
            "attempt_id,status,payload_hash,created_at,record) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
            (result.id, result.run_id, result.run_item_id, result.case_id, result.case_version_id,
             result.regression_source_result_id, result.attempt_id, result.status, result.payload_hash,
             result.created_at, self._json(result)))
        self._write_item(cur, item)

    def _dependencies_ready_sql(self, *, failed: bool = False) -> str:
        # Existing stores use jsonb_array_elements and #>>. PostgreSQL #> returns
        # SQL NULL for absent/invalid paths; explicit JSON null is also missing.
        # Filter before LIMIT and SKIP LOCKED so waiting items cannot consume slots.
        source = (
            "SELECT 1 FROM jsonb_array_elements("
            "COALESCE(candidate.record->'dependencies', '[]'::jsonb)) AS dependency(value) "
            f"LEFT JOIN {self._item_table} AS parent ON parent.id=(dependency.value->>'item_id')::uuid "
            "AND parent.run_id=candidate.run_id "
            f"LEFT JOIN {self._result_table} AS result ON result.id=parent.result_id "
            "AND result.run_item_id=parent.id AND result.status=parent.status "
        )
        missing_output = (
            "EXISTS ("
            "SELECT 1 FROM jsonb_array_elements(COALESCE("
            "dependency.value->'required_output_paths', '[]'::jsonb)) AS output_path(value) "
            "WHERE COALESCE((result.record->'actual') #> ARRAY("
            "SELECT jsonb_array_elements_text(output_path.value)), 'null'::jsonb)='null'::jsonb)"
        )
        missing_preparation = (
            "EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE("
            "dependency.value->'required_preparation_keys', '[]'::jsonb)) AS preparation(key) "
            "WHERE COALESCE((result.record->'actual') #> ARRAY['preparations',preparation.key,'status'], "
            "'null'::jsonb) <> '\"passed\"'::jsonb)"
        )
        terminal = "parent.status IN ('passed','failed','error','blocked','skipped','cancelled')"
        passed_required = "COALESCE(dependency.value->>'completion_condition','passed')='passed'"
        unavailable = f"({missing_output} OR {missing_preparation})"
        if failed:
            return (f"EXISTS ({source} WHERE parent.id IS NULL OR "
                f"({passed_required} AND {terminal} AND parent.status<>'passed') OR "
                f"({terminal} AND {unavailable}))")
        return (f"NOT EXISTS ({source} WHERE parent.id IS NULL OR "
            f"({passed_required} AND parent.status IS DISTINCT FROM 'passed') OR "
            f"(NOT {passed_required} AND NOT ({terminal})) OR {unavailable})")

    def _block_failed_dependencies_in_cursor(self, cur, run: TestRunRecord, now: datetime) -> int:
        run_id = run.id
        blocked_count = 0
        while True:
            cur.execute(f"SELECT candidate.record FROM {self._item_table} AS candidate "
                "WHERE candidate.run_id=%s AND candidate.status='queued' "
                f"AND {self._dependencies_ready_sql(failed=True)} "
                "ORDER BY candidate.position,candidate.id FOR UPDATE OF candidate SKIP LOCKED", (run_id,))
            candidates = [self._item_from_value(row["record"]) for row in cur.fetchall()]
            if not candidates:
                break
            changed = 0
            for item in candidates:
                parent_ids = [dependency.item_id for dependency in item.dependencies]
                cur.execute(f"SELECT record FROM {self._item_table} WHERE run_id=%s AND id=ANY(%s::uuid[])",
                    (run_id, parent_ids))
                parents = {parent.id: parent for parent in [self._item_from_value(row["record"]) for row in cur.fetchall()]}
                result_ids = [parent.result_id for parent in parents.values() if parent.result_id]
                cur.execute(f"SELECT record FROM {self._result_table} WHERE run_id=%s AND id=ANY(%s::uuid[])",
                    (run_id, result_ids))
                results = {result.id: result for result in [self._result_from_value(row["record"]) for row in cur.fetchall()]}
                failures = dependency_failures(item, parents, results)
                if not failures:
                    continue
                updated, attempt, result = blocked_dependency_records(item, failures, now)
                attempt = attempt.model_copy(update={"plan_snapshot_identity":
                    claim_plan_snapshot_identity(run, source="synthetic_blocked_receipt")})
                self._save_unexecuted_in_cursor(cur, updated, attempt, result)
                changed += 1
            if not changed:
                break
            blocked_count += changed
        # The caller updates run totals only after it has locked every candidate.
        # Updating the run row here and then locking more items reverses the
        # existing item-before-run lock order and can deadlock another claimant.
        return blocked_count

    def _write_run(self, cur, run: TestRunRecord) -> None:
        cur.execute(
            f"UPDATE {self._run_table} SET status = %s, parent_run_id = %s, "
            "updated_at = %s, record = %s::jsonb, plan_snapshots = plan_snapshots || %s::jsonb WHERE id = %s",
            (
                run.status,
                run.parent_run_id,
                run.updated_at,
                self._json(run),
                json.dumps(initial_plan_snapshots(run), ensure_ascii=False, separators=(",", ":")),
                run.id,
            ),
        )

    def _write_item(self, cur, item: TestRunItemRecord) -> None:
        cur.execute(
            f"UPDATE {self._item_table} SET position = %s, status = %s, attempt_no = %s, "
            "lease_owner = %s, lease_token = %s, lease_expires_at = %s, "
            "result_id = %s, regression_source_result_id = %s, updated_at = %s, "
            "record = %s::jsonb WHERE id = %s",
            (
                item.position,
                item.status,
                item.attempt_no,
                item.lease_owner,
                item.lease_token,
                item.lease_expires_at,
                item.result_id,
                item.regression_source_result_id,
                item.updated_at,
                self._json(item),
                item.id,
            ),
        )

    def _write_attempt(self, cur, attempt: TestRunAttemptRecord) -> None:
        cur.execute(
            f"UPDATE {self._attempt_table} SET status = %s, record = %s::jsonb WHERE id = %s",
            (attempt.status, self._json(attempt), attempt.id),
        )

    @staticmethod
    def _json(record) -> str:
        return json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _value(value):
        return json.loads(value) if isinstance(value, str) else value

    @classmethod
    def _run_from_value(cls, value) -> TestRunRecord:
        return TestRunRecord.model_validate(cls._value(value))

    @classmethod
    def _item_from_value(cls, value) -> TestRunItemRecord:
        return TestRunItemRecord.model_validate(cls._value(value))

    @classmethod
    def _attempt_from_value(cls, value) -> TestRunAttemptRecord:
        return TestRunAttemptRecord.model_validate(cls._value(value))

    @classmethod
    def _result_from_value(cls, value) -> TestCaseResultRecord:
        return TestCaseResultRecord.model_validate(cls._value(value))
