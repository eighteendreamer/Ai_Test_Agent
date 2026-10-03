from __future__ import annotations

import logging

from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.case_planning_task import PlanningTaskSeed

logger = logging.getLogger(__name__)


class CasePlanningTaskService:
    """Durable submissions; model planning and test execution are separate work."""

    def __init__(self, *, store, planning_service, project_service, dispatch_stream: str | None):
        self._store, self._planner, self._projects = store, planning_service, project_service
        self._stream = dispatch_stream

    async def initialize(self):
        await self._store.initialize()

    async def submit(self, project_id: str, request: CasePlanningTaskRequest):
        await self._projects.get(project_id)
        payload = request.planning_request()
        digest = self._store.request_hash(payload)
        existing = await self._store.get_by_request(project_id, request.creation_key, request_hash=digest)
        if existing:
            logger.info("case_planning_submission_replayed", extra={"project_id": project_id, "task_id": existing.id})
            return existing
        if not self._stream:
            raise RuntimeError("持久规划的派发流尚未启用")
        seed = await self._planner.prepare_task(project_id, payload)
        task = await self._store.submit(project_id=project_id, creation_key=request.creation_key,
            seed=seed, request_hash=digest, stream=self._stream)
        logger.info("case_planning_submission_saved", extra={"project_id": project_id, "task_id": task.id,
                                                            "stream": self._stream})
        return task

    async def get(self, project_id: str, task_id: str):
        await self._projects.get(project_id)
        task = await self._store.get(project_id, task_id)
        if task is None:
            raise KeyError("当前项目没有该规划任务")
        return task

    async def find_regression_submission(self, project_id, parent_run_id, request):
        await self._projects.get(project_id)
        submission = request.model_dump(mode="json", exclude={"creation_key"})
        digest = self._store.regression_request_hash(parent_run_id, submission)
        return await self._store.get_by_request(project_id, request.creation_key, request_hash=digest)

    async def submit_regression(self, project_id, request, source, *, planning_request, prepared_plan=None):
        digest = self._store.regression_request_hash(source.parent_run_id, source.submission)
        existing = await self._store.get_by_request(project_id, request.creation_key, request_hash=digest)
        if existing:
            return existing
        if not self._stream:
            raise RuntimeError("持久规划的派发流尚未启用")
        if prepared_plan is None:
            seed = await self._planner.prepare_task(project_id, planning_request, regression=source)
        else:
            project = await self._projects.require_active(project_id)
            prepared_plan = prepared_plan.model_copy(deep=True, update={"regression": source})
            seed = PlanningTaskSeed(plan=prepared_plan, project_context={"id": project.id, "name": project.name,
                "base_url": project.base_url, "description": project.description}, system_prompt="")
        task = await self._store.submit(project_id=project_id, creation_key=request.creation_key,
            seed=seed, request_hash=digest, stream=self._stream)
        logger.info("regression_planning_submission_saved", extra={"project_id": project_id,
            "parent_run_id": source.parent_run_id, "task_id": task.id, "fixed_plan": prepared_plan is not None})
        return task

    async def get_by_request(self, project_id: str, creation_key: str):
        await self._projects.get(project_id)
        task = await self._store.get_by_request(project_id, creation_key)
        if task is None:
            raise KeyError("当前项目没有该规划提交")
        return task

    async def list(self, project_id: str, *, limit: int = 24, offset: int = 0):
        await self._projects.get(project_id)
        return await self._store.list(project_id, limit=limit, offset=offset)
