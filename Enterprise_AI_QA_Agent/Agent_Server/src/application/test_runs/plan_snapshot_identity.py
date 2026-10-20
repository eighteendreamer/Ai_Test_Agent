"""Bind the complete execution snapshot at claim, before an Agent checkpoint."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.application.test_runs.plan_mapping import json_digest
from src.schemas.case_planning import CasePlanExecutionSnapshot
from src.schemas.run_management import (RunPlanSnapshotIdentity, TestRunAttemptRecord, TestRunItemRecord,
    TestCaseResultRecord, TestRunRecord, TestRunDetail)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultExecutionContext:
    run: TestRunRecord
    item: TestRunItemRecord
    attempt: TestRunAttemptRecord
    result: TestCaseResultRecord


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


def result_attempt(detail: TestRunDetail, result_id: str) -> TestRunAttemptRecord:
    """Resolve the immutable attempt named by a saved result, independent of the current lease."""
    matches = [entry for entry in detail.results if entry.id == result_id]
    if len(matches) != 1:
        raise ValueError("测试结果在运行详情中不存在或身份重复")
    result = matches[0]
    if result.run_id != detail.run.id:
        raise ValueError("测试结果与运行身份不一致")

    items = [entry for entry in detail.items if entry.id == result.run_item_id]
    if len(items) != 1:
        raise ValueError("测试结果引用的运行条目不存在或身份重复")
    item = items[0]
    if (item.run_id != result.run_id or item.case_id != result.case_id
        or item.case_version_id != result.case_version_id):
        raise ValueError("测试结果与运行条目身份不一致")

    attempts = [entry for entry in detail.attempts if entry.id == result.attempt_id]
    if len(attempts) != 1:
        raise ValueError("测试结果引用的 Attempt 不存在或身份重复")
    attempt = attempts[0]
    if (attempt.run_id != result.run_id or attempt.run_item_id != result.run_item_id
        or attempt.attempt_no != result.attempt_no or attempt.status != result.status):
        raise ValueError("测试结果与原始 Attempt 身份不一致")
    return attempt


def result_execution_contexts(detail: TestRunDetail, archives: dict[str, dict],
    result_ids: list[str]) -> dict[str, ResultExecutionContext]:
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("测试结果列表包含重复身份")
    contexts = {}
    for result_id in result_ids:
        attempt = result_attempt(detail, result_id)
        result = next(entry for entry in detail.results if entry.id == result_id)
        item = next(entry for entry in detail.items if entry.id == result.run_item_id)
        identity = attempt.plan_snapshot_identity
        archive_present = identity is not None and identity.sha256 in archives
        fixed_run = run_with_claimed_snapshot(detail.run, attempt,
            archives.get(identity.sha256) if identity else None, archive_present=archive_present)
        contexts[result_id] = ResultExecutionContext(run=fixed_run, item=item,
            attempt=attempt, result=result)
    return contexts


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


def claim_plan_snapshot_identity(run: TestRunRecord, prior: TestRunAttemptRecord | None = None,
    *, source: str = "initial_claim") -> RunPlanSnapshotIdentity | None:
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
        "source": source})
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
