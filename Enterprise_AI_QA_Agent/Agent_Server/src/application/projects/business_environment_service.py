"""Configure a target and freeze the actual selected configuration once."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.runtime.postgres_business_environment_store import (
    environment_digest, environment_identity, environment_request_digest, fixed_target_configuration,
)
from src.schemas.business_environment import (
    TARGET_CONFIG_KEY, BusinessTargetConfiguration, BusinessTargetConfigurationRecord, BusinessEnvironmentSnapshot,
)
from src.application.projects.business_tool_observation import inspect_tool_observation

logger = logging.getLogger(__name__)


class BusinessEnvironmentService:
    def __init__(self, *, store, configs, projects, tool_jobs=None):
        self._store, self._configs, self._projects = store, configs, projects
        self._tool_jobs = tool_jobs

    async def initialize(self):
        await asyncio.to_thread(self._configs.initialize)
        await self._store.initialize()

    async def _write(self, project_id, operation, callback, *, environment_id=None, request_id=None):
        try:
            value = await callback()
        except Exception as exc:
            logger.warning("business_environment_write_failed", extra={"project_id": project_id,
                "environment_id": environment_id, "request_id": request_id,
                "operation": operation, "error_type": type(exc).__name__})
            raise
        logger.info("business_environment_written", extra={"project_id": project_id, "operation": operation,
            "environment_id": environment_id or getattr(value, "id", None), "request_id": request_id,
            "config_revision": getattr(value, "revision", None), "snapshot_id": value.id if
                isinstance(value, BusinessEnvironmentSnapshot) else None})
        return value

    async def register(self, project_id, request):
        await self._projects.get(project_id)
        return await self._write(project_id, "register", lambda: self._store.register(project_id, request), request_id=request.request_id)

    async def get_registration(self, project_id, request_id):
        await self._projects.get(project_id)
        value = await self._store.get_by_request(project_id, request_id)
        if value is None:
            raise KeyError("未找到当前项目的原环境登记")
        return value

    async def get(self, project_id, environment_id):
        await self._projects.get(project_id)
        value = await self._store.get(project_id, environment_id)
        if value is None:
            raise KeyError("环境不属于当前项目")
        return value

    async def list(self, project_id, *, status=None, limit=20, offset=0):
        await self._projects.get(project_id)
        return await self._store.list(project_id, status=status, limit=limit, offset=offset)

    async def archive(self, project_id, environment_id):
        await self._projects.get(project_id)
        return await self._write(project_id, "archive", lambda: self._store.archive(project_id, environment_id), environment_id=environment_id)

    @staticmethod
    def _configuration(project_id, environment_id, record):
        if record.config_key != TARGET_CONFIG_KEY or record.scope_key != environment_id:
            raise ValueError("环境配置不属于所选作用域")
        value = BusinessTargetConfigurationRecord(project_id=project_id, environment_id=environment_id,
            scope_key=record.scope_key, revision=record.revision,
            payload=BusinessTargetConfiguration.model_validate(record.payload), sha256="0"*64)
        value.sha256 = environment_digest(value.model_dump(mode="json", exclude={"sha256"}))
        return fixed_target_configuration(value)

    async def read_configuration(self, project_id, environment_id):
        await self.get(project_id, environment_id)
        record = await asyncio.to_thread(self._configs.get, TARGET_CONFIG_KEY, scope_key=environment_id)
        if record is None:
            raise ValueError("环境目标尚未配置，请先保存目标设置")
        return self._configuration(project_id, environment_id, record)

    async def save_configuration(self, project_id, environment_id, payload):
        registered = await self.get(project_id, environment_id)
        await self._projects.require_active(project_id)
        if registered.status != "active":
            raise ValueError("已归档环境不能修改目标配置")
        async def save():
            record = await asyncio.to_thread(self._configs.save, TARGET_CONFIG_KEY,
                payload.model_dump(mode="json"), scope_key=environment_id)
            return self._configuration(project_id, environment_id, record)
        return await self._write(project_id, "save_configuration", save, environment_id=environment_id)

    async def capture(self, project_id, environment_id, request):
        registered = await self.get(project_id, environment_id)
        digest = environment_request_digest(project_id, "capture", request, environment_id)
        prior = await self._store.get_by_request(project_id, request.request_id, capture=True, digest=digest)
        if prior:
            logger.info("business_environment_capture_replayed", extra={"project_id": project_id,
                "environment_id": environment_id, "snapshot_id": prior.id})
            return self._registration_link(registered, prior)
        await self._projects.require_active(project_id)
        if registered.status != "active":
            raise ValueError("已归档环境不能捕获新依据")
        config = await self.read_configuration(project_id, environment_id)
        if config.revision != request.expected_config_revision or config.sha256 != request.expected_config_sha256:
            raise ValueError("环境目标配置已变化，请重读后明确选择新的捕获依据")
        value = BusinessEnvironmentSnapshot(id=environment_identity(project_id, "capture", request.request_id),
            project_id=project_id, environment_id=environment_id, environment_key=registered.request.environment_key,
            registration_request_sha256=registered.request_sha256, request=request, request_sha256=digest,
            configuration=config, captured_at=datetime.now(timezone.utc), sha256="0"*64)
        value.sha256 = environment_digest(value.model_dump(mode="json", exclude={"sha256"}))
        return await self._write(project_id, "capture", lambda: self._store.capture(value),
            environment_id=environment_id, request_id=request.request_id)

    async def get_capture(self, project_id, environment_id, request_id):
        registered = await self.get(project_id, environment_id)
        value = await self._store.get_by_request(project_id, request_id, capture=True)
        if value is None or value.environment_id != environment_id:
            raise KeyError("未找到当前环境的原依据捕获")
        return self._registration_link(registered, value)

    async def get_snapshot(self, project_id, environment_id, snapshot_id):
        registered = await self.get(project_id, environment_id)
        value = await self._store.get_snapshot(project_id, environment_id, snapshot_id)
        if value is None:
            raise KeyError("固定依据不属于当前环境")
        return self._registration_link(registered, value)

    async def list_snapshots(self, project_id, environment_id, *, limit=20, offset=0):
        registered = await self.get(project_id, environment_id)
        page = await self._store.list(project_id, environment_id=environment_id, limit=limit, offset=offset)
        for value in page.items:
            self._registration_link(registered, value)
        return page

    async def inspect_tool(self, project_id, environment_id, snapshot_id, request):
        snapshot = await self.get_snapshot(project_id, environment_id, snapshot_id)
        if self._tool_jobs is None:
            raise RuntimeError("原工具证据存储暂不可用")
        job = await self._tool_jobs.get_job(request.tool_job_id)
        if job is None:
            raise KeyError("原工具证据不存在")
        result = inspect_tool_observation(snapshot, job, request)
        logger.info("business_target_tool_observed", extra={"project_id": project_id,
            "environment_id": environment_id, "snapshot_id": snapshot_id, "tool_job_id": job.id,
            "target_status": result.target_status, "limitations": result.limitations})
        return result

    @staticmethod
    def _registration_link(registered, value):
        if (value.environment_id != registered.id or value.project_id != registered.project_id
            or value.environment_key != registered.request.environment_key
            or value.registration_request_sha256 != registered.request_sha256):
            raise ValueError("固定环境依据与原登记身份或哈希不同")
        return value
