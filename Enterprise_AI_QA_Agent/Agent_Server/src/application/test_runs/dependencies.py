"""Deterministic RunItem dependencies, separate from AI business reasoning."""
from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.schemas.run_management import (TestRunRecord, TestRunItemRecord, TestCaseResultRecord,
    TestRunAttemptRecord, RunItemCompletion)

TERMINAL_DEPENDENCY_STATUSES = {"passed", "failed", "error", "blocked", "skipped", "cancelled"}


def validate_run_dependencies(run: TestRunRecord, items: list[TestRunItemRecord]) -> None:
    by_id = {item.id: item for item in items}
    if len(by_id) != len(items) or any(item.run_id != run.id for item in items):
        raise ValueError("运行条目身份重复或不属于当前运行")
    graph = {}
    for item in items:
        parents = [dependency.item_id for dependency in item.dependencies]
        if len(set(parents)) != len(parents):
            raise ValueError("运行条目的依赖身份重复")
        if any(parent not in by_id or parent == item.id for parent in parents):
            raise ValueError("运行依赖不存在、属于其他运行或引用自身")
        graph[item.id] = parents
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError:
        raise ValueError("运行依赖存在循环") from None


def output_at_path(actual: dict[str, Any], path: list[str]) -> Any:
    """Same JSON key/index semantics as PostgreSQL #>; null is unavailable."""
    current: Any = actual
    for token in path:
        if isinstance(current, dict):
            current = current.get(token)
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


def dependencies_ready(item: TestRunItemRecord, items: dict[str, TestRunItemRecord],
                       results: dict[str, TestCaseResultRecord]) -> bool:
    for dependency in item.dependencies:
        parent = items.get(dependency.item_id)
        acceptable = TERMINAL_DEPENDENCY_STATUSES if dependency.completion_condition == "terminal" else {"passed"}
        if parent is None or parent.run_id != item.run_id or parent.status not in acceptable:
            return False
        if not dependency.required_output_paths and not dependency.required_preparation_keys:
            continue
        result = results.get(parent.result_id or "")
        if result is None or result.run_item_id != parent.id or result.status != parent.status:
            return False
        if any(output_at_path(result.actual, path) is None for path in dependency.required_output_paths):
            return False
        if any(output_at_path(result.actual, ["preparations", key, "status"]) != "passed"
            for key in dependency.required_preparation_keys):
            return False
    return True


def dependency_failures(item: TestRunItemRecord, items: dict[str, TestRunItemRecord],
                        results: dict[str, TestCaseResultRecord]) -> list[dict[str, Any]]:
    failures = []
    for dependency in item.dependencies:
        parent = items.get(dependency.item_id)
        result = results.get(parent.result_id or "") if parent else None
        if parent is None or parent.run_id != item.run_id:
            failures.append({"item_id": dependency.item_id, "reason": "missing_predecessor"})
        elif parent.status in TERMINAL_DEPENDENCY_STATUSES - {"passed"} and dependency.completion_condition == "passed":
            failures.append({"item_id": parent.id, "result_id": parent.result_id,
                "reason": "predecessor_unsuccessful", "status": parent.status})
        elif parent.status in TERMINAL_DEPENDENCY_STATUSES:
            missing = [path for path in dependency.required_output_paths
                if result is None or result.run_item_id != parent.id or result.status != parent.status
                or output_at_path(result.actual, path) is None]
            if missing:
                failures.append({"item_id": parent.id, "result_id": parent.result_id,
                    "reason": "required_output_missing", "paths": missing})
            unsuccessful = [key for key in dependency.required_preparation_keys
                if result is None or result.run_item_id != parent.id or result.status != parent.status
                or output_at_path(result.actual, ["preparations", key, "status"]) != "passed"]
            if unsuccessful:
                failures.append({"item_id": parent.id, "result_id": parent.result_id,
                    "reason": "preparation_unsuccessful", "preparation_keys": unsuccessful})
    return failures


def blocked_dependency_records(item: TestRunItemRecord, failures: list[dict[str, Any]], now: datetime
                               ) -> tuple[TestRunItemRecord, TestRunAttemptRecord, TestCaseResultRecord]:
    """An unexecuted terminal receipt, using existing attempt/result records."""
    completion = RunItemCompletion(status="blocked", summary="必要前置失败或输出缺失；未执行业务操作",
        actual={"dependency_failures": failures, "business_execution_started": False},
        evidence_refs=[{"evidence_type": "run_item", "evidence_id": failure["item_id"],
            "label": failure["reason"], "metadata": {"result_id": failure.get("result_id")}} for failure in failures],
        payload_hash="0" * 64)
    content = completion.model_dump(mode="json", exclude={"payload_hash"})
    completion.payload_hash = hashlib.sha256(json.dumps(content, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    attempt = TestRunAttemptRecord(id=str(uuid4()), run_id=item.run_id, run_item_id=item.id,
        attempt_no=item.attempt_no + 1, worker_id="dependency-resolution", lease_token=str(uuid4()),
        status="blocked", claimed_at=now, completed_at=now)
    result = TestCaseResultRecord(id=str(uuid4()), run_id=item.run_id, run_item_id=item.id,
        case_id=item.case_id, case_version_id=item.case_version_id,
        regression_source_result_id=item.regression_source_result_id, attempt_id=attempt.id,
        attempt_no=attempt.attempt_no, created_at=now, **completion.model_dump(mode="python"))
    updated = item.model_copy(deep=True, update={"status": "blocked", "result_id": result.id,
        "attempt_no": attempt.attempt_no, "completed_at": now, "updated_at": now,
        "wall_clock_duration_ms": max(0, int((now - item.created_at).total_seconds() * 1000))})
    return updated, attempt, result
