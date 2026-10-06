"""First-start decision evidence, bound to the original execution attempt."""
from src.application.test_runs.environment_restrictions import restriction_reviews
from src.runtime.postgres_business_applicability_store import fixed_applicability
from src.runtime.postgres_business_environment_store import environment_digest
from src.schemas.business_execution import BusinessExecutionAdmission


def create_environment_admission(run,item,attempt,reviews,now):
    plan = run.plan_snapshot.plan
    fixed = plan.environment
    if restriction_reviews(plan,item,reviews):
        raise ValueError("明确失效条目不能保存允许开始回执")
    value = BusinessExecutionAdmission(project_id=run.project_id,run_id=run.id,run_item_id=item.id,
        plan_id=plan.id,case_id=item.case_id,case_version_id=item.case_version_id,
        planning_basis_sha256=fixed.sha256,environment_id=fixed.snapshot.environment_id,
        snapshot_id=fixed.snapshot.id,snapshot_sha256=fixed.snapshot.sha256,
        execution_attempt_id=attempt.id,checked_at=now,reviews=reviews,sha256="0"*64)
    value.sha256 = environment_digest(value.model_dump(mode="json",exclude={"sha256"}))
    return value


def fixed_environment_admission(run,item,attempt,attempts):
    value = attempt.environment_admission
    if value is None:
        return None
    plan = run.plan_snapshot.plan if run.plan_snapshot else None
    fixed = plan.environment if plan else None
    if (fixed is None or value.sha256 != environment_digest(value.model_dump(mode="json",exclude={"sha256"}))
        or (value.project_id,value.run_id,value.run_item_id,value.plan_id,value.case_id,value.case_version_id,
            value.planning_basis_sha256,value.environment_id,value.snapshot_id,value.snapshot_sha256) !=
        (run.project_id,run.id,item.id,plan.id,item.case_id,item.case_version_id,fixed.sha256,
            fixed.snapshot.environment_id,fixed.snapshot.id,fixed.snapshot.sha256)):
        raise ValueError("开始确认回执与原项目、精确用例、固定目标或SHA不同")
    versions = set()
    for raw in value.reviews:
        review = fixed_applicability(raw)
        if (review.project_id != run.project_id or review.snapshot != fixed.snapshot
            or review.request.requirement_version_id in versions):
            raise ValueError("开始确认回执包含其他固定目标或重复需求版本")
        versions.add(review.request.requirement_version_id)
    if restriction_reviews(plan,item,value.reviews):
        raise ValueError("开始确认回执的原依据包含明确失效版本")
    lineage = {row.id:row for row in attempts if row.run_item_id == item.id and row.run_id == run.id}
    current,visited = attempt,set()
    while current.id != value.execution_attempt_id:
        if current.id in visited or current.recovered_from_attempt_id not in lineage:
            raise ValueError("开始确认回执不属于本条目的原执行恢复链")
        visited.add(current.id)
        current = lineage[current.recovered_from_attempt_id]
    if current.environment_admission != value or current.started_at != value.checked_at:
        raise ValueError("开始确认回执与原开始时间或执行Attempt不同")
    return value.model_copy(deep=True)
