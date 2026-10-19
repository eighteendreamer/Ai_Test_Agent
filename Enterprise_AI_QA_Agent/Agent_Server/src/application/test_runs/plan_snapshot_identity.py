"""Bind the complete execution snapshot at claim, before an Agent checkpoint."""
from __future__ import annotations

import logging

from src.application.test_runs.plan_mapping import json_digest
from src.schemas.case_planning import CasePlanExecutionSnapshot
from src.schemas.run_management import RunPlanSnapshotIdentity, TestRunAttemptRecord, TestRunRecord, TestRunDetail


logger = logging.getLogger(__name__)


def initial_plan_snapshots(run: TestRunRecord) -> dict:
    # Reuse the frozen Run envelope and canonical digest, not a rebuilt plan.
    if run.plan_snapshot is None or run.execution_strategy != "agent":
        return {}
    raw = run.plan_snapshot.model_dump(mode="json")
    return {json_digest(raw): raw}


def execution_attempt(detail: TestRunDetail, item_id: str, attempt_id: str) -> TestRunAttemptRecord:
    latest = max((entry for entry in detail.attempts if entry.run_item_id == item_id),
        key=lambda entry: entry.attempt_no, default=None)
    if latest is None or latest.id != attempt_id or latest.run_id != detail.run.id:
        raise ValueError("Agent 执行与当前运行条目或 attempt 租约不一致")
    return latest


def run_with_claimed_snapshot(run: TestRunRecord, attempt: TestRunAttemptRecord,
    archive: dict | None, *, archive_present: bool) -> TestRunRecord:
    identity = attempt.plan_snapshot_identity
    if identity is None:
        return run
    if not archive_present:
        # Rows created before archival may use the current snapshot only when
        # the complete original digest still matches. Never fabricate history.
        try:
            validate_claimed_plan_snapshot(run, attempt)
        except ValueError as exc:
            logger.warning("test_run_claimed_snapshot_archive_missing", extra={"run_id": run.id,
                "attempt_id": attempt.id, "snapshot_sha256": identity.sha256})
            raise ValueError("领取时固定的计划归档缺失，当前投影也不匹配；需要恢复原快照") from exc
        logger.info("test_run_claimed_snapshot_current_fallback", extra={"run_id": run.id,
            "attempt_id": attempt.id, "snapshot_sha256": identity.sha256})
        return run
    try:
        if not isinstance(archive, dict) or json_digest(archive) != identity.sha256:
            raise ValueError("归档内容与领取哈希不同")
        snapshot = CasePlanExecutionSnapshot.model_validate(archive).model_copy(deep=True)
        fixed = run.model_copy(update={"plan_snapshot": snapshot})
        validate_claimed_plan_snapshot(fixed, attempt)
    except ValueError as exc:
        logger.warning("test_run_claimed_snapshot_archive_invalid", extra={"run_id": run.id,
            "attempt_id": attempt.id, "snapshot_sha256": identity.sha256})
        raise ValueError("领取时固定的计划归档损坏或身份不一致；不能退回当前计划") from exc
    logger.info("test_run_claimed_snapshot_loaded", extra={"run_id": run.id,
        "attempt_id": attempt.id, "plan_id": identity.plan_id, "snapshot_sha256": identity.sha256})
    return fixed


def validate_supplied_snapshot(supplied: TestRunRecord | None, current: TestRunRecord,
    fixed: TestRunRecord) -> None:
    if supplied is not None and supplied.plan_snapshot not in (current.plan_snapshot, fixed.plan_snapshot):
        raise ValueError("Agent 执行与当前运行条目或 attempt 租约不一致：传入计划既非当前投影也非原快照")


def claim_plan_snapshot_identity(run: TestRunRecord, prior: TestRunAttemptRecord | None = None) -> RunPlanSnapshotIdentity | None:
    # Reuse canonical plan hashing and the existing expired-attempt inheritance.
    # Never attach today's plan to a historical execution that had no pin.
    if prior is not None:
        value = prior.plan_snapshot_identity
        if value is not None:
            logger.info("test_run_claim_plan_selected", extra={"run_id": run.id,
                "run_item_id": prior.run_item_id, "source_attempt_id": prior.id,
                "plan_id": value.plan_id, "snapshot_sha256": value.sha256, "source": "expired_attempt"})
        return value.model_copy(deep=True) if value is not None else None
    if run.execution_strategy != "agent":
        return None
    if run.plan_snapshot is None or run.plan_snapshot.plan.project_id != run.project_id:
        raise ValueError("Agent 领取缺少所属项目的固定计划快照")
    value = RunPlanSnapshotIdentity(plan_id=run.plan_snapshot.plan.id,
        sha256=json_digest(run.plan_snapshot.model_dump(mode="json")))
    logger.info("test_run_claim_plan_selected", extra={"run_id": run.id,
        "project_id": run.project_id, "plan_id": value.plan_id, "snapshot_sha256": value.sha256,
        "source": "initial_claim"})
    return value


def validate_claimed_plan_snapshot(run: TestRunRecord, attempt: TestRunAttemptRecord) -> None:
    identity = attempt.plan_snapshot_identity
    if identity is None:
        return
    snapshot = run.plan_snapshot
    if (attempt.run_id != run.id or run.execution_strategy != "agent" or snapshot is None
        or snapshot.plan.project_id != run.project_id or snapshot.plan.id != identity.plan_id
        or json_digest(snapshot.model_dump(mode="json")) != identity.sha256):
        logger.warning("test_run_claimed_plan_snapshot_mismatch", extra={"run_id": run.id,
            "run_item_id": attempt.run_item_id, "attempt_id": attempt.id,
            "claimed_plan_id": identity.plan_id, "snapshot_sha256": identity.sha256})
        raise ValueError("当前执行依据与领取时固定的计划快照不同；需要读取原快照，不能重新绑定执行依据")
