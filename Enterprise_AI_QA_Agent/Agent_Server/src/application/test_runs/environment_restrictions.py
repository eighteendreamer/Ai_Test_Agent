"""Only explicit invalid exact versions restrict new execution in a fixed target."""
import logging
from src.application.test_runs.dependencies import unexecuted_records
from src.runtime.postgres_business_environment_store import environment_digest
from src.schemas.run_management import RunItemCompletion

logger = logging.getLogger(__name__)


def restriction_reviews(plan, item, reviews):
    # No upstream counterpart (checked: fixed applicability, planning inputs,
    # RunItem dependencies): only explicit decisions can restrict this exact version.
    relevant = [(review, case) for review in reviews for case in review.cases
        if case.case_version_id == item.case_version_id]
    states = {}
    for review, case in relevant:
        if case.version.case_id != item.case_id:
            raise ValueError("环境适用确认与运行精确用例身份不同")
        fixed = next((entry for entry in plan.evidence if entry.kind == "case"
            and entry.version == item.case_version_id), None)
        if fixed is None or fixed.content_hash != case.version.content_hash:
            raise ValueError("环境适用确认与原规划用例hash不同")
        for key in case.point_keys:
            states.setdefault((review.request.requirement_id, key), set()).add(case.status)
    restricted = []
    for (requirement_id,key),statuses in states.items():
        if "invalid" in statuses and len(statuses)>1:
            logger.warning("environment_confirmation_conflict_not_restricted",extra={"project_id":plan.project_id,
                "case_version_id":item.case_version_id,"requirement_id":requirement_id,
                "point_key":key,"statuses":sorted(statuses)})
    for review, case in relevant:
        if case.status == "invalid" and any(states[(review.request.requirement_id,key)] == {"invalid"}
            for key in case.point_keys):
            restricted.append(review)
    return restricted


def blocked_environment_records(run, item, reviews, now, phase, *, attempt=None, current_reviews=None):
    fixed = run.plan_snapshot.plan.environment
    receipt = {"version": "business-environment-restriction-v1", "phase": phase,
        "checked_at": now.isoformat(), "project_id": run.project_id,
        "plan_id": run.plan_snapshot.plan.id, "planning_basis_sha256": fixed.sha256,
        "environment_id": fixed.snapshot.environment_id, "snapshot_id": fixed.snapshot.id,
        "snapshot_sha256": fixed.snapshot.sha256, "case_id": item.case_id,
        "case_version_id": item.case_version_id, "reviews": [review.model_dump(mode="json") for review in reviews],
        "current_reviews": [review.model_dump(mode="json") for review in (current_reviews if current_reviews is not None else reviews)]}
    receipt["sha256"] = environment_digest(receipt)
    completion = RunItemCompletion(status="blocked", summary="固定目标已明确确认此用例版本失效；未执行业务操作",
        actual={"environment_restriction": receipt, "business_execution_started": False}, payload_hash="0"*64,
        evidence_refs=[{"evidence_type": "environment_applicability_review", "evidence_id": review.id,
            "label": "固定环境精确用例失效确认", "metadata": {"snapshot_id": fixed.snapshot.id,
                "snapshot_sha256": fixed.snapshot.sha256, "review_sha256": review.sha256}} for review in reviews])
    return unexecuted_records(item, completion, now, worker_id="environment-restriction", attempt=attempt)
