"""Bind the complete execution snapshot at claim, before an Agent checkpoint."""
from __future__ import annotations

import logging

from src.application.test_runs.plan_mapping import json_digest
from src.schemas.run_management import RunPlanSnapshotIdentity, TestRunAttemptRecord, TestRunRecord


logger = logging.getLogger(__name__)


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
