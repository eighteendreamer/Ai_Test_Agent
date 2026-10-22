"""Build an append-only revision of the unclaimed portion of an Agent Run."""
from __future__ import annotations

from datetime import datetime
import logging
from uuid import NAMESPACE_URL, uuid5

from src.application.test_runs.dependencies import validate_run_dependencies
from src.application.test_runs.plan_mapping import json_digest
from src.schemas.run_management import (
    RunPlanRevisionItemChange,
    RunPlanRevisionRequest,
    TestRunPlanRevisionRecord,
    TestRunItemRecord,
    TestRunRecord,
)


logger = logging.getLogger(__name__)


def _owned_execution_definition(snapshot, owner_key):
    # Same ownership/phase projection as build_agent_case_context. Reasons and
    # evidence selections may be restated; actual operations and data flow may
    # not be silently ignored when an item has already been claimed.
    nodes = {}
    owned = {key for key, owner in snapshot.owners.items() if owner == owner_key}
    for node in snapshot.plan.decision.items:
        if node.key in owned:
            content = node.model_dump(mode="json", exclude={"reason", "evidence_ids"})
            content["depends_on"] = sorted(content["depends_on"])
            nodes[node.key] = {"item": content, "phase": snapshot.preparation_phases.get(node.key, "case")}
    scopes = {}
    for key, scope in (snapshot.preparation_scopes or {}).items():
        if (scope.source_owner_key == owner_key or owner_key in scope.consumer_keys
            or owned.intersection(scope.cleanup_keys)):
            scopes[key] = {**scope.model_dump(mode="json"),
                "consumer_keys": sorted(scope.consumer_keys), "cleanup_keys": sorted(scope.cleanup_keys)}
    return {"nodes": nodes, "scopes": scopes}


def build_run_plan_revision(
    *,
    current_run: TestRunRecord,
    current_items: list[TestRunItemRecord],
    attempted_item_ids: set[str],
    candidate_run: TestRunRecord,
    candidate_items: list[TestRunItemRecord],
    request: RunPlanRevisionRequest,
    request_sha256: str,
    now: datetime,
) -> tuple[TestRunRecord, list[TestRunItemRecord], TestRunPlanRevisionRecord]:
    """Rebase a frozen candidate plan without rewriting any attempted item."""
    current = current_run.plan_snapshot
    proposed = candidate_run.plan_snapshot
    if current_run.plan_revision != request.expected_revision:
        raise ValueError("运行计划修订版本已变化，请重新读取运行后再提交")
    if (current_run.status in {"completed", "cancelled"} or current_run.origin != "native"
        or current_run.execution_strategy != "agent" or current is None or proposed is None):
        raise ValueError("只有进行中的原生 Agent 运行可以修订固定计划")
    if (candidate_run.project_id != current_run.project_id
        or proposed.plan.project_id != current_run.project_id
        or candidate_run.mode_key != current_run.mode_key
        or candidate_run.session_id != current_run.session_id
        or candidate_run.run_kind != current_run.run_kind
        or candidate_run.parent_run_id != current_run.parent_run_id
        or proposed.regression != current.regression
        or proposed.dependency_mapping_version != current.dependency_mapping_version):
        raise ValueError("候选计划不能更改运行项目、模式、会话或回归依据")
    current_by_id = {item.id: item for item in current_items}
    if len(current_by_id) != len(current_items) or any(item.run_id != current_run.id for item in current_items):
        raise ValueError("当前运行条目身份重复或不属于当前运行")
    current_by_pair = {(item.case_id, item.case_version_id): item for item in current_items}
    if len(current_by_pair) != len(current_items):
        raise ValueError("当前运行包含重复的用例固定版本，不能安全修订")

    candidate_by_key = {item.plan_item_key: item for item in candidate_items if item.plan_item_key}
    if len(candidate_by_key) != len(candidate_items):
        raise ValueError("候选计划运行条目缺少唯一计划节点身份")
    if (set(candidate_by_key) != set(proposed.run_item_ids)
        or any(proposed.run_item_ids[key] != item.id for key, item in candidate_by_key.items())):
        raise ValueError("候选计划快照与正式运行条目映射不一致")
    validate_run_dependencies(candidate_run, candidate_items)

    candidate_id_to_run_id: dict[str, str] = {}
    selected: dict[str, TestRunItemRecord] = {}
    eligible_updates: set[str] = set()
    added_ids: list[str] = []
    candidate_order = [item.plan_item_key for item in candidate_items]
    if len(set(candidate_order)) != len(candidate_order):
        raise ValueError("候选计划包含重复的运行节点")

    for key in candidate_order:
        candidate_item = candidate_by_key[key]
        pair = (candidate_item.case_id, candidate_item.case_version_id)
        previous = current_by_pair.get(pair)
        if previous is not None:
            if previous.status == "cancelled":
                raise ValueError("已从运行中停用的用例版本不能重新加入；请创建新运行")
            candidate_id_to_run_id[candidate_item.id] = previous.id
            can_update = (previous.status == "queued" and previous.attempt_no == 0
                and previous.id not in attempted_item_ids and previous.lease_token is None
                and previous.result_id is None)
            if can_update:
                eligible_updates.add(previous.id)
                selected[key] = candidate_item.model_copy(deep=True, update={
                    "id": previous.id,
                    "run_id": current_run.id,
                    "created_at": previous.created_at,
                    "updated_at": now,
                })
            else:
                selected[key] = previous.model_copy(deep=True)
        else:
            new_id = str(uuid5(NAMESPACE_URL,
                f"test-run-plan-revision:{current_run.id}:{request.expected_revision + 1}:"
                f"{proposed.plan.id}:{key}"))
            if new_id in current_by_id or new_id in candidate_id_to_run_id.values():
                raise ValueError("候选计划生成的运行条目身份与现有运行冲突")
            candidate_id_to_run_id[candidate_item.id] = new_id
            added_ids.append(new_id)
            selected[key] = candidate_item.model_copy(deep=True, update={
                "id": new_id,
                "run_id": current_run.id,
                "created_at": now,
                "updated_at": now,
            })

    for key in candidate_order:
        item = selected[key]
        remapped = []
        for dependency in candidate_by_key[key].dependencies:
            parent_id = candidate_id_to_run_id.get(dependency.item_id)
            if parent_id is None:
                raise ValueError("候选计划的运行依赖无法映射到当前运行条目")
            remapped.append(dependency.model_copy(update={"item_id": parent_id}))
        if item.id not in eligible_updates and item.id not in added_ids:
            if (_owned_execution_definition(current, item.plan_item_key)
                != _owned_execution_definition(proposed, key)
                or {dependency.item_id: dependency for dependency in item.dependencies}
                != {dependency.item_id: dependency for dependency in remapped}):
                logger.warning("run_plan_revision_execution_conflict", extra={"run_id": current_run.id,
                    "run_item_id": item.id, "plan_item_key": item.plan_item_key,
                    "candidate_plan_id": proposed.plan.id, "item_status": item.status,
                    "attempt_no": item.attempt_no})
                raise ValueError(f"候选计划改变了已领取或已有执行记录条目 {item.plan_item_key} 的执行步骤、依赖或数据来源；请基于当前状态重新规划")
            continue
        selected[key] = item.model_copy(update={"dependencies": remapped})

    active_ids = {item.id for item in selected.values()}
    final_by_id = {item.id: item.model_copy(deep=True) for item in current_items}
    final_by_id.update({item.id: item.model_copy(deep=True) for item in selected.values()})
    cancelled_ids: list[str] = []
    for previous in current_items:
        if previous.id in active_ids:
            continue
        can_cancel = (previous.status == "queued" and previous.attempt_no == 0
            and previous.id not in attempted_item_ids and previous.lease_token is None
            and previous.result_id is None)
        if can_cancel:
            final_by_id[previous.id] = previous.model_copy(deep=True, update={
                "status": "cancelled",
                "completed_at": now,
                "updated_at": now,
                "lease_owner": None,
                "lease_token": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "waiting_reason": None,
                "approval_id": None,
                "tool_job_id": None,
            })
            cancelled_ids.append(previous.id)

    historical_ids = [item.id for item in sorted(current_items, key=lambda value: (value.position, value.id))
        if item.id not in active_ids]
    ordered_ids = [selected[key].id for key in candidate_order] + historical_ids
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(final_by_id):
        raise ValueError("修订后的运行条目集合不完整")
    final_items = []
    for position, item_id in enumerate(ordered_ids, 1):
        item = final_by_id[item_id]
        if item.position != position:
            item = item.model_copy(update={"position": position, "updated_at": now})
        final_items.append(item)

    run_item_ids = {key: candidate_id_to_run_id[candidate_by_key[key].id] for key in candidate_order}
    snapshot = proposed.model_copy(deep=True, update={"run_item_ids": run_item_ids})
    revised_run = current_run.model_copy(deep=True, update={
        "plan_snapshot": snapshot,
        "plan_revision": request.expected_revision + 1,
        "updated_at": now,
    })
    validate_run_dependencies(revised_run, final_items)

    before_by_id = current_by_id
    changes: list[RunPlanRevisionItemChange] = []
    updated_ids: list[str] = []
    for item in final_items:
        before = before_by_id.get(item.id)
        if before is None:
            changes.append(RunPlanRevisionItemChange(change="added", run_item_id=item.id, after=item))
        elif before != item:
            change_kind = "cancelled" if item.status == "cancelled" and before.status != "cancelled" else "updated"
            changes.append(RunPlanRevisionItemChange(change=change_kind, run_item_id=item.id,
                before=before, after=item))
            if change_kind == "updated":
                updated_ids.append(item.id)
    old_snapshot_sha = json_digest(current.model_dump(mode="json"))
    new_snapshot_sha = json_digest(snapshot.model_dump(mode="json"))
    if old_snapshot_sha == new_snapshot_sha and not changes:
        raise ValueError("候选计划没有产生可执行变更")
    record = TestRunPlanRevisionRecord(
        run_id=current_run.id,
        revision=request.expected_revision + 1,
        idempotency_key=request.idempotency_key,
        request_sha256=request_sha256,
        candidate_plan_id=proposed.plan.id,
        reason=request.reason,
        before_snapshot_sha256=old_snapshot_sha,
        after_snapshot_sha256=new_snapshot_sha,
        added_item_ids=added_ids,
        updated_item_ids=updated_ids,
        cancelled_item_ids=cancelled_ids,
        item_changes=changes,
        created_at=now,
    )
    return revised_run, final_items, record
