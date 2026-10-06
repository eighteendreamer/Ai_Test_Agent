"""Read authoritative frozen targets/decisions using the existing project lock."""
import asyncio
import logging

from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.postgres_business_applicability_store import fixed_applicability
from src.runtime.postgres_business_environment_store import environment_digest, fixed_environment_snapshot
from src.schemas.business_planning import BusinessPlanningEnvironment

logger = logging.getLogger(__name__)


def fixed_planning_environment(project_id, selection, value):
    fixed = BusinessPlanningEnvironment.model_validate(value)
    snapshot = fixed_environment_snapshot(fixed.snapshot)
    if (selection is None or snapshot.project_id != project_id or
        snapshot.environment_id != selection.environment_id or snapshot.id != selection.snapshot_id or
        snapshot.sha256 != selection.expected_snapshot_sha256 or fixed.sha256 !=
        environment_digest(fixed.model_dump(mode="json", exclude={"sha256"}))):
        raise ValueError("规划环境与所选项目、固定快照或哈希不同")
    versions = set()
    for entry in fixed.reviews:
        review = fixed_applicability(entry)
        if (review.project_id != project_id or review.environment_id != snapshot.environment_id or
            review.snapshot != snapshot or review.request.requirement_version_id in versions):
            raise ValueError("规划环境确认不属于固定目标或需求版本重复")
        versions.add(review.request.requirement_version_id)
    return fixed


class PostgresBusinessPlanning:
    def __init__(self, *, applicability):
        self._decisions = applicability
        self._environments = applicability._environments
        self._settings = applicability._settings

    def _snapshot(self, cur, project_id, selection):
        env = self._environments
        cur.execute(f"SELECT * FROM {env._snapshots} WHERE project_id=%s AND environment_id=%s AND id=%s FOR SHARE",
            (project_id, selection.environment_id, selection.snapshot_id))
        row = cur.fetchone()
        if row is None:
            raise KeyError("所选规划环境快照不属于当前项目环境")
        snapshot = env._snapshot(row)
        if snapshot.sha256 != selection.expected_snapshot_sha256:
            raise ValueError("所选规划环境快照哈希不同")
        return snapshot

    async def freeze(self, project_id, selection):
        def read():
            env = self._environments
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                project = env._project(cur, project_id)
                registered = env._get(cur, project_id, selection.environment_id)
                if registered is None:
                    raise KeyError("所选规划环境不属于当前项目")
                if project["status"] != "active" or registered.status != "active":
                    raise ValueError("已归档项目或环境不能创建新环境规划")
                snapshot = self._snapshot(cur, project_id, selection)
                if snapshot.registration_request_sha256 != registered.request_sha256:
                    raise ValueError("规划环境快照与原登记不同")
                # Same project lock as append reviews: select one complete head
                # per requirement version, including explicit revoke/unknown.
                cur.execute(f"SELECT DISTINCT ON(requirement_version_id) * FROM {self._decisions.table} "
                    "WHERE project_id=%s AND environment_id=%s AND snapshot_id=%s "
                    "ORDER BY requirement_version_id,revision DESC",
                    (project_id, selection.environment_id, selection.snapshot_id))
                reviews = [self._decisions._review(row) for row in cur.fetchall()]
                result = BusinessPlanningEnvironment(snapshot=snapshot, reviews=reviews, sha256="0"*64)
                result.sha256 = environment_digest(result.model_dump(mode="json", exclude={"sha256"}))
                return fixed_planning_environment(project_id, selection, result)
        result = await asyncio.to_thread(read)
        logger.info("business_planning_environment_frozen", extra={"project_id": project_id,
            "environment_id": selection.environment_id, "snapshot_id": selection.snapshot_id,
            "review_count": len(result.reviews), "basis_sha256": result.sha256})
        return result

    def validate_in_cursor(self, cur, plan):
        """Validate historical bases, without loading current config or newer reviews."""
        if plan.environment is None:
            if plan.request.environment is not None:
                raise ValueError("所选环境缺少服务端固定规划依据")
            return
        fixed = fixed_planning_environment(plan.project_id, plan.request.environment, plan.environment)
        registered = self._environments._get(cur, plan.project_id, fixed.snapshot.environment_id)
        if registered is None or registered.status != "active":
            raise ValueError("已归档环境不能创建新规划或运行")
        if self._snapshot(cur, plan.project_id, plan.request.environment) != fixed.snapshot:
            raise ValueError("规划环境固定正文与PG原记录不同")
        if fixed.snapshot.registration_request_sha256 != registered.request_sha256:
            raise ValueError("固定规划环境与原登记不同")
        for review in fixed.reviews:
            cur.execute(f"SELECT * FROM {self._decisions.table} WHERE id=%s AND project_id=%s",
                (review.id, plan.project_id))
            row = cur.fetchone()
            if row is None or self._decisions._review(row) != review:
                raise ValueError("规划环境采用的确认与PG原记录不同")
