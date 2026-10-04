"""Review governance lives in the case record, outside immutable version content/hashes."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from src.schemas.case_management import (
    TestCaseRecord,
    TestCaseReviewEvent,
    TestCaseVersionRecord,
    TestCaseVersionReview,
)


def ensure_reviews(case: TestCaseRecord, versions: list[TestCaseVersionRecord], at: datetime) -> TestCaseRecord:
    by_id = {version.id: version for version in versions}
    if case.active_version_id and case.active_version_id not in by_id:
        raise ValueError("用例启用版本身份无效，不能推导历史评审")
    if versions and max(version.version for version in versions) != case.latest_version:
        raise ValueError("用例最新版本与实际版本记录不一致，不能推导历史评审")
    result = case.model_copy(deep=True)
    legacy = not case.version_reviews
    for version in versions:
        if version.id not in result.version_reviews:
            status = ("approved" if version.id == case.active_version_id else
                "pending_review" if legacy and case.lifecycle_status == "pending_review" and version.version == case.latest_version else "draft")
            result.version_reviews[version.id] = TestCaseVersionReview(version_id=version.id, version=version.version,
                status=status, origin="legacy_state", events=[TestCaseReviewEvent(action="legacy_inferred", at=at)])
        review = result.version_reviews[version.id]
        if review.version_id != version.id or review.version != version.version:
            raise ValueError("用例版本评审身份与正文版本不一致")
    if legacy and case.active_version_id and case.lifecycle_status in {"draft", "pending_review"}:
        result.lifecycle_status = "active"
    return result


def add_draft_review(case: TestCaseRecord, version: TestCaseVersionRecord) -> TestCaseRecord:
    result = case.model_copy(deep=True)
    result.version_reviews[version.id] = TestCaseVersionReview(version_id=version.id, version=version.version)
    return result


def review_transition(
    case: TestCaseRecord,
    versions: list[TestCaseVersionRecord],
    version_id: str,
    action: Literal["submitted", "activated"],
    at: datetime,
    request_id: str | None = None,
    *,
    expected_active: str | None = None,
    check_active: bool = False,
) -> tuple[TestCaseRecord, bool]:
    if action not in {"submitted", "activated"}:
        raise ValueError("未知用例版本评审操作")
    current = ensure_reviews(case, versions, at)
    review = current.version_reviews.get(version_id)
    if review is None or not any(version.id == version_id and version.case_id == case.id for version in versions):
        raise ValueError("评审版本不属于当前用例")
    signature = hashlib.sha256(json.dumps({"version_id": version_id, "action": action,
        "expected_active": expected_active, "check_active": check_active}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if request_id:
        # Scope an operation to the case/action, so changing version is also a conflict.
        previous = next((event for item in current.version_reviews.values() for event in item.events
            if event.action == action and event.request_id == request_id), None)
        if previous:
            if previous.request_hash != signature:
                raise ValueError("同一评审操作编号不能改变参数")
            return current, False
    if current.lifecycle_status == "archived":
        raise ValueError("已归档用例不能提交评审或启用")
    needs_embedding = False
    if action == "submitted":
        review.status = "pending_review"
        if current.active_version_id is None and current.lifecycle_status != "disabled":
            current.lifecycle_status = "pending_review"
    else:
        if check_active and current.active_version_id != expected_active:
            raise ValueError("启用版本已变化，请核对当前版本后另建请求")
        if review.status not in {"pending_review", "approved"}:
            raise ValueError("必须先提交该明确版本进行评审，不能启用未评审草稿")
        needs_embedding = current.active_version_id != version_id or current.lifecycle_status != "active"
        review.status = "approved"
        current.active_version_id = version_id
        current.lifecycle_status = "active"
    review.events.append(TestCaseReviewEvent(action=action, at=at, request_id=request_id, request_hash=signature))
    current.updated_at = at
    return current, needs_embedding
