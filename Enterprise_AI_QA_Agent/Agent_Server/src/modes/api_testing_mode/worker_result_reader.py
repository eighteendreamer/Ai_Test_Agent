"""Read a settled worker's original result using its frozen dispatch identity."""
from __future__ import annotations

from copy import deepcopy
import logging
from types import SimpleNamespace

from src.runtime.postgres_api_credential_store import ApiCredentialReferenceError
from src.schemas.tool_job import ToolJobStatus

logger = logging.getLogger("uvicorn.error.api_testing_mode.coordinator")


class ApiWorkerResultReader:
    def __init__(self, *, tool_job_service, credential_reference_service=None):
        self._jobs = tool_job_service
        self._references = credential_reference_service

    async def read(self, *, session, task, parent_context):
        dispatch_id = task.task_id if task.attempts <= 1 else f"{task.task_id}:attempt:{task.attempts}"
        meta = session.metadata
        if (meta.get("parent_session_id") != parent_context.get("session_id")
            or meta.get("parent_turn_id") != parent_context.get("turn_id")
            or meta.get("task_id") != dispatch_id
            or meta.get("dispatch_role") != "api_execution_worker"):
            raise ApiCredentialReferenceError("api_worker_result_scope_invalid")
        matching = []
        for job in await self._jobs.list_jobs(session_id=session.id):
            if job.session_id != session.id or job.tool_key != "api-test-runner":
                continue
            arguments = job.input_payload
            if arguments.get("worker_action") != "execute_task":
                continue
            if arguments.get("dispatch_ref"):
                if self._references is None:
                    raise ApiCredentialReferenceError("api_worker_result_reference_service_missing")
                frozen, _, parent = await self._references.resolve_dispatch(
                    arguments["dispatch_ref"], SimpleNamespace(session_id=session.id))
                if (parent.id != parent_context.get("session_id") or parent.project_id != session.project_id
                    or frozen["parent_turn_id"] != parent_context.get("turn_id")
                    or frozen["dispatch_task_id"] != dispatch_id):
                    raise ApiCredentialReferenceError("api_worker_result_dispatch_invalid")
                original_task = frozen["task"]
            else:
                original_task = arguments.get("task") or {}
            if not isinstance(original_task, dict) or original_task.get("task_id") != task.task_id:
                continue
            if any(original_task.get(key, "") != getattr(task, key)
                   for key in ("method", "path", "full_url")):
                raise ApiCredentialReferenceError("api_worker_result_request_invalid")
            if original_task.get("attempts", 0) != task.attempts - 1:
                raise ApiCredentialReferenceError("api_worker_result_attempt_invalid")
            matching.append(job)
        if len(matching) > 1:
            raise ApiCredentialReferenceError("api_worker_result_ambiguous")
        if not matching:
            logger.warning("api_worker_result_job_missing", extra={
                "child_session_id": session.id, "task_id": task.task_id})
            return None
        job = matching[0]
        if job.status not in {ToolJobStatus.completed, ToolJobStatus.partial, ToolJobStatus.failed}:
            logger.info("api_worker_result_not_settled", extra={
                "child_session_id": session.id, "tool_job_id": job.id, "status": job.status.value})
            return None
        result = job.output_payload.get("task_result")
        if not isinstance(result, dict):
            return None
        if (any(result.get(key, "") != getattr(task, key) for key in ("task_id", "method", "path", "full_url"))
            or result.get("attempts") != task.attempts):
            raise ApiCredentialReferenceError("api_worker_result_identity_invalid")
        output = deepcopy(job.output_payload)
        if ref := output.get("private_result_ref"):
            checkpoint_ref = task.execution_checkpoint.get("private_result_ref")
            if checkpoint_ref and checkpoint_ref != ref:
                raise ApiCredentialReferenceError("api_worker_result_reference_conflict")
            if self._references is None or not job.input_payload.get("dispatch_ref"):
                raise ApiCredentialReferenceError("api_worker_result_reference_service_missing")
            original = await self._references.load_worker_result(ref,
                dispatch_ref=job.input_payload["dispatch_ref"], job=job, session=session, parent_context=parent_context)
            if (any(original.get(key, "") != getattr(task, key) for key in ("task_id", "method", "path", "full_url"))
                or original.get("attempts") != task.attempts):
                raise ApiCredentialReferenceError("api_worker_result_identity_invalid")
            original.setdefault("execution_checkpoint", {})["private_result_ref"] = ref
            output["task_result"] = original
        elif result.get("execution_checkpoint", {}).get("private_result_ref"):
            raise ApiCredentialReferenceError("api_worker_result_reference_missing")
        logger.info("api_worker_result_read_from_job", extra={
            "child_session_id": session.id, "tool_job_id": job.id, "task_id": task.task_id,
            "status": job.status.value})
        return output
