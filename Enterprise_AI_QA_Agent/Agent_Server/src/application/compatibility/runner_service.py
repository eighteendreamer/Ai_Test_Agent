from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
import json
import mimetypes
from pathlib import Path
from typing import Any

from src.modes.compatibility_testing_mode.contracts import CompatibilityDispatchPlan, new_id
from src.schemas.compatibility_runner import (
    CompatibilityArtifactRecord,
    CompatibilityArtifactUploadRequest,
    CompatibilityEnvironmentReport,
    CompatibilityExecutionReport,
    CompatibilityFailureReport,
    CompatibilityQueuedTask,
    CompatibilityRecoverableTaskReport,
    CompatibilityRunnerCleanupRequest,
    CompatibilityRunnerCleanupResponse,
    CompatibilityRunnerHeartbeatRequest,
    CompatibilityRunnerPollResponse,
    CompatibilityRunnerRecord,
    CompatibilityRunnerRegistrationRequest,
    CompatibilityRunnerTaskReportRequest,
    CompatibilityRunnerTaskSummary,
    CompatibilityTaskRequeueRequest,
    CompatibilityTaskRequeueResponse,
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


class CompatibilityRunnerNotFound(KeyError):
    pass


class CompatibilityRunnerService:
    def __init__(self, *, settings=None, artifact_storage_service=None) -> None:
        self._settings = settings
        self._artifact_storage_service = artifact_storage_service
        self._runners: dict[str, CompatibilityRunnerRecord] = {}
        self._tasks: dict[str, CompatibilityQueuedTask] = {}
        self._artifacts: dict[str, CompatibilityArtifactRecord] = {}
        self._lock = asyncio.Lock()
        self._local_artifact_root = (
            Path(__file__).resolve().parents[2] / str(getattr(settings.storage, "artifact_root_dir", "data/artifacts"))
        ).resolve()
        self._local_artifact_root.mkdir(parents=True, exist_ok=True)
        self._data_dir = (
            Path(__file__).resolve().parents[2] / str(getattr(settings, "data_dir", "data")) / "compatibility"
        ).resolve()
        self._state_path = self._data_dir / "runner_state.json"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

    async def register_runner(self, payload: CompatibilityRunnerRegistrationRequest) -> CompatibilityRunnerRecord:
        now = _utc_now()
        async with self._lock:
            self._requeue_stale_assigned_tasks_locked(now, persist=False)
            existing = self._runners.get(payload.runner_id)
            existing_active_task_ids = (
                []
                if existing and self._is_runner_stale(existing)
                else self._active_task_ids_for_runner(payload.runner_id)
            )
            record = CompatibilityRunnerRecord(
                runner_id=payload.runner_id,
                name=payload.name or payload.runner_id,
                os=payload.os,
                capabilities=sorted(set(self._normalize_list(payload.capabilities))),
                devices=payload.devices,
                max_parallel=max(1, int(payload.max_parallel or 1)),
                status="online",
                active_task_ids=existing_active_task_ids,
                registered_at=existing.registered_at if existing else now,
                heartbeat_at=now,
                metadata=payload.metadata,
            )
            self._runners[record.runner_id] = record
            self._save_state()
            return record

    async def heartbeat(
        self,
        runner_id: str,
        payload: CompatibilityRunnerHeartbeatRequest,
    ) -> CompatibilityRunnerRecord:
        now = _utc_now()
        async with self._lock:
            record = self._runners.get(runner_id)
            if record is None:
                raise KeyError(runner_id)
            self._requeue_stale_assigned_tasks_locked(now, persist=False)
            record = self._runners.get(runner_id)
            if record is None:
                raise KeyError(runner_id)
            record = record.model_copy(
                update={
                    "status": payload.status or "online",
                    "active_task_ids": self._active_task_ids_for_runner(runner_id),
                    "heartbeat_at": now,
                    "metadata": {**record.metadata, **payload.metadata},
                }
            )
            self._runners[runner_id] = record
            self._save_state()
            return record

    async def list_runners(self) -> list[CompatibilityRunnerRecord]:
        async with self._lock:
            self._requeue_stale_assigned_tasks_locked()
            runners = [self._effective_runner_record(item) for item in self._runners.values()]
            return sorted(runners, key=lambda item: item.heartbeat_at, reverse=True)

    async def cleanup_offline_runners(
        self,
        payload: CompatibilityRunnerCleanupRequest,
    ) -> CompatibilityRunnerCleanupResponse:
        now = _utc_now()
        older_than_seconds = max(0, int(payload.older_than_seconds or 0))
        wanted_runner_ids = {str(item).strip() for item in payload.runner_ids if str(item).strip()}
        deleted: list[str] = []
        skipped_reasons: dict[str, str] = {}
        async with self._lock:
            recovered_task_ids = self._requeue_stale_assigned_tasks_locked(now, persist=False)
            for runner_id, runner in list(self._runners.items()):
                if wanted_runner_ids and runner_id not in wanted_runner_ids:
                    continue
                active_task_ids = self._active_task_ids_for_runner(runner_id)
                if active_task_ids:
                    skipped_reasons[runner_id] = "runner_has_assigned_tasks"
                    continue
                effective_runner = self._effective_runner_record(runner)
                if effective_runner.status not in {"offline", "disabled"}:
                    skipped_reasons[runner_id] = f"runner_status_{effective_runner.status}_not_cleanupable"
                    continue
                age_seconds = self._runner_heartbeat_age_seconds(runner)
                if age_seconds is None:
                    skipped_reasons[runner_id] = "runner_heartbeat_unknown"
                    continue
                if age_seconds < older_than_seconds:
                    skipped_reasons[runner_id] = "runner_not_old_enough"
                    continue
                del self._runners[runner_id]
                deleted.append(runner_id)
            for runner_id in sorted(wanted_runner_ids - set(deleted) - set(skipped_reasons)):
                if runner_id not in self._runners:
                    skipped_reasons.setdefault(runner_id, "runner_not_found")
            if deleted or recovered_task_ids:
                self._save_state()
        return CompatibilityRunnerCleanupResponse(
            deleted_count=len(deleted),
            runner_ids=sorted(deleted),
            skipped_runner_ids=sorted(skipped_reasons),
            skipped_reasons=skipped_reasons,
        )

    async def find_matching_runners(self, selector: dict[str, Any]) -> list[CompatibilityRunnerRecord]:
        async with self._lock:
            matches = [
                runner
                for runner in self._runners.values()
                if not self._is_runner_stale(runner)
                and runner.status in {"online", "busy"}
                and self._runner_matches_selector(runner, selector)
            ]
            return sorted(matches, key=lambda item: (len(item.active_task_ids), item.runner_id))

    async def enqueue_dispatch_plan(self, dispatch_plan: CompatibilityDispatchPlan) -> list[CompatibilityQueuedTask]:
        now = _utc_now()
        queued: list[CompatibilityQueuedTask] = []
        async with self._lock:
            existing_dispatch_tasks = [
                task for task in self._tasks.values() if task.dispatch_id == dispatch_plan.dispatch_id
            ]
            if existing_dispatch_tasks:
                return sorted(existing_dispatch_tasks, key=lambda item: (item.created_at, item.task_id))
            for task in dispatch_plan.tasks:
                if task.task_id in self._tasks:
                    queued.append(self._tasks[task.task_id])
                    continue
                queued_task = CompatibilityQueuedTask(
                    task_id=task.task_id,
                    dispatch_id=dispatch_plan.dispatch_id,
                    plan_id=dispatch_plan.plan_id,
                    environment_id=task.environment_id,
                    case_ids=list(task.case_ids),
                    runner_selector=task.runner_selector.model_dump(),
                    mode_calls=[call.model_dump() for call in task.mode_calls],
                    status="queued",
                    created_at=now,
                    updated_at=now,
                    metadata={
                        "plan_version": dispatch_plan.plan_version,
                        **task.metadata,
                    },
                )
                self._tasks[queued_task.task_id] = queued_task
                queued.append(queued_task)
            if queued:
                self._save_state()
        return queued

    async def poll_tasks(self, runner_id: str, limit: int = 1) -> CompatibilityRunnerPollResponse:
        now = _utc_now()
        async with self._lock:
            self._requeue_stale_assigned_tasks_locked(now)
            runner = self._runners.get(runner_id)
            if runner is None:
                raise KeyError(runner_id)
            active_task_ids_changed = False
            active_task_ids = self._active_task_ids_for_runner(runner_id)
            if active_task_ids != runner.active_task_ids:
                runner = runner.model_copy(update={"active_task_ids": active_task_ids})
                self._runners[runner_id] = runner
                active_task_ids_changed = True
            if self._is_runner_stale(runner) or runner.status not in {"online", "busy"}:
                if active_task_ids_changed:
                    self._save_state()
                return CompatibilityRunnerPollResponse(runner_id=runner_id, tasks=[])
            assigned: list[CompatibilityQueuedTask] = []
            capacity = max(1, runner.max_parallel) - len(runner.active_task_ids)
            effective_limit = max(0, min(max(1, limit), capacity))
            if effective_limit <= 0:
                if active_task_ids_changed:
                    self._save_state()
                return CompatibilityRunnerPollResponse(runner_id=runner_id, tasks=[])

            for task in self._tasks.values():
                if len(assigned) >= effective_limit:
                    break
                if task.status != "queued":
                    continue
                if not self._runner_matches_task(runner, task):
                    continue
                updated = task.model_copy(
                    update={
                        "status": "assigned",
                        "assigned_runner_id": runner_id,
                        "updated_at": now,
                    }
                )
                self._tasks[task.task_id] = updated
                assigned.append(updated)

            if assigned:
                runner = runner.model_copy(
                    update={
                        "active_task_ids": sorted(set([*runner.active_task_ids, *[task.task_id for task in assigned]])),
                        "heartbeat_at": now,
                    }
                )
                self._runners[runner_id] = runner
            if active_task_ids_changed or assigned:
                self._save_state()
            return CompatibilityRunnerPollResponse(runner_id=runner_id, tasks=assigned)

    async def report_task(
        self,
        runner_id: str,
        task_id: str,
        payload: CompatibilityRunnerTaskReportRequest,
    ) -> CompatibilityQueuedTask:
        now = _utc_now()
        status = self._normalize_report_status(payload.status)
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            self._ensure_runner_can_write_task(runner_id, task)
            self._ensure_task_can_be_reported(task)
            raw_artifacts = payload.artifacts
            if not raw_artifacts and isinstance(payload.result.get("artifacts"), list):
                raw_artifacts = payload.result.get("artifacts") or []
            artifacts: list[CompatibilityArtifactRecord] = []
            reserved_artifact_ids = {artifact.artifact_id for artifact in task.artifacts}
            for item in raw_artifacts:
                if not isinstance(item, dict):
                    continue
                artifact = self._normalize_artifact(
                    item,
                    task=task,
                    runner_id=runner_id,
                    created_at=now,
                    reserved_artifact_ids=reserved_artifact_ids,
                )
                artifacts.append(artifact)
                reserved_artifact_ids.add(artifact.artifact_id)
            merged_artifacts = [*task.artifacts, *artifacts]
            result = {
                **payload.result,
                "artifacts": [artifact.model_dump() for artifact in merged_artifacts],
                "error": payload.error,
            }
            updated = task.model_copy(
                update={
                    "status": status,
                    "result": result,
                    "artifacts": merged_artifacts,
                    "error": payload.error,
                    "updated_at": now,
                    "metadata": {**task.metadata, **self._reported_task_metadata(payload.metadata)},
                }
            )
            self._tasks[task_id] = updated
            for artifact in artifacts:
                self._artifacts[artifact.artifact_id] = artifact
            runner = self._runners.get(runner_id)
            if runner is not None:
                runner = runner.model_copy(
                    update={
                        "active_task_ids": [item for item in runner.active_task_ids if item != task_id],
                        "heartbeat_at": now,
                    }
                )
                self._runners[runner_id] = runner
            self._save_state()
            return updated

    async def upload_task_artifact(
        self,
        runner_id: str,
        task_id: str,
        payload: CompatibilityArtifactUploadRequest,
    ) -> CompatibilityArtifactRecord:
        now = _utc_now()
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            self._ensure_runner_can_write_task(runner_id, task)
            task_snapshot = task

        content = self._decode_base64(payload.content_base64)
        artifact_id = new_id("artifact")
        uri, metadata = await self._store_uploaded_artifact(
            artifact_id=artifact_id,
            task=task_snapshot,
            payload=payload,
            content=content,
        )

        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    raise KeyError(task_id)
                self._ensure_runner_can_write_task(runner_id, task)
                artifact = CompatibilityArtifactRecord(
                    artifact_id=artifact_id,
                    task_id=task.task_id,
                    dispatch_id=task.dispatch_id,
                    plan_id=task.plan_id,
                    runner_id=runner_id,
                    environment_id=task.environment_id,
                    type=payload.type or "evidence",
                    uri=uri,
                    mime_type=payload.mime_type or self._content_type(payload.filename),
                    label=payload.label or payload.filename or payload.type or "artifact",
                    created_at=now,
                    metadata={
                        **payload.metadata,
                        **metadata,
                        "case_ids": task.case_ids,
                        "original_filename": payload.filename,
                        "size_bytes": len(content),
                    },
                )
                self._artifacts[artifact.artifact_id] = artifact
                updated = task.model_copy(
                    update={
                        "artifacts": [*task.artifacts, artifact],
                        "updated_at": now,
                    }
                )
                self._tasks[task_id] = updated
                self._save_state()
                return artifact
        except Exception:
            await self._delete_stored_artifact(uri, metadata)
            raise

    async def list_tasks(self, *, dispatch_id: str | None = None, runner_id: str | None = None) -> list[CompatibilityQueuedTask]:
        async with self._lock:
            self._requeue_stale_assigned_tasks_locked()
            tasks = list(self._tasks.values())
            if dispatch_id:
                tasks = [task for task in tasks if task.dispatch_id == dispatch_id]
            if runner_id:
                tasks = [task for task in tasks if task.assigned_runner_id == runner_id]
            return sorted(tasks, key=lambda item: item.updated_at, reverse=True)

    async def get_assigned_task_for_runner(self, runner_id: str, task_id: str) -> CompatibilityQueuedTask:
        async with self._lock:
            self._requeue_stale_assigned_tasks_locked()
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            self._ensure_runner_can_write_task(runner_id, task)
            return task

    async def begin_mode_call_execution(self, runner_id: str, task_id: str) -> CompatibilityQueuedTask:
        now = _utc_now()
        async with self._lock:
            self._requeue_stale_assigned_tasks_locked(now)
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            self._ensure_runner_can_write_task(runner_id, task)
            execution_status = str(task.metadata.get("mode_calls_execution_status") or "").strip().lower()
            if execution_status in {"running", "completed", "failed", "partial"}:
                raise PermissionError(f"Mode calls for task {task_id} have already been executed or are running.")
            updated = task.model_copy(
                update={
                    "updated_at": now,
                    "metadata": {
                        **task.metadata,
                        "mode_calls_execution_status": "running",
                        "mode_calls_execution_started_at": now,
                        "mode_calls_execution_runner_id": runner_id,
                    },
                }
            )
            self._tasks[task_id] = updated
            self._save_state()
            return updated

    async def finish_mode_call_execution(
        self,
        runner_id: str,
        task_id: str,
        *,
        status: str,
        result_count: int,
    ) -> CompatibilityQueuedTask:
        now = _utc_now()
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            self._ensure_runner_can_write_task(runner_id, task)
            updated = task.model_copy(
                update={
                    "updated_at": now,
                    "metadata": {
                        **task.metadata,
                        "mode_calls_execution_status": status,
                        "mode_calls_execution_completed_at": now,
                        "mode_calls_execution_result_count": result_count,
                    },
                }
            )
            self._tasks[task_id] = updated
            self._save_state()
            return updated

    async def requeue_tasks(self, payload: CompatibilityTaskRequeueRequest) -> CompatibilityTaskRequeueResponse:
        now = _utc_now()
        wanted_task_ids = set(payload.task_ids)
        wanted_statuses = set(self._normalize_list(payload.statuses or ["failed"]))
        requeued: list[str] = []
        skipped_reasons: dict[str, str] = {}
        async with self._lock:
            for task_id, task in list(self._tasks.items()):
                if wanted_task_ids and task_id not in wanted_task_ids:
                    continue
                if payload.dispatch_id and task.dispatch_id != payload.dispatch_id:
                    if wanted_task_ids:
                        skipped_reasons[task_id] = "dispatch_filter_mismatch"
                    continue
                if payload.runner_id and task.assigned_runner_id != payload.runner_id:
                    if wanted_task_ids:
                        skipped_reasons[task_id] = "runner_filter_mismatch"
                    continue
                if wanted_statuses and str(task.status).lower() not in wanted_statuses:
                    if wanted_task_ids:
                        skipped_reasons[task_id] = f"status_{task.status}_not_requeueable"
                    continue
                retry_history = list(task.metadata.get("retry_history") or [])
                retry_history.append(
                    {
                        "at": now,
                        "reason": payload.reason,
                        "previous_status": task.status,
                        "previous_runner_id": task.assigned_runner_id,
                        "previous_summary": task.result.get("summary") if isinstance(task.result, dict) else "",
                        "previous_error": task.error,
                        "previous_artifact_ids": [artifact.artifact_id for artifact in task.artifacts],
                    }
                )
                updated = task.model_copy(
                    update={
                        "status": "queued",
                        "assigned_runner_id": None,
                        "result": {},
                        "artifacts": [],
                        "error": None,
                        "updated_at": now,
                        "metadata": {
                            **self._clear_mode_call_execution_metadata(task.metadata),
                            "retry_count": int(task.metadata.get("retry_count") or 0) + 1,
                            "retry_history": retry_history[-10:],
                        },
                    }
                )
                self._tasks[task_id] = updated
                requeued.append(task_id)
            if requeued:
                for runner_id, runner in list(self._runners.items()):
                    active = [item for item in runner.active_task_ids if item not in requeued]
                    if active != runner.active_task_ids:
                        self._runners[runner_id] = runner.model_copy(update={"active_task_ids": active})
                self._save_state()
        for task_id in sorted(wanted_task_ids - set(requeued) - set(skipped_reasons)):
            skipped_reasons[task_id] = "task_not_found"
        return CompatibilityTaskRequeueResponse(
            requeued_count=len(requeued),
            task_ids=requeued,
            skipped_task_ids=sorted(skipped_reasons),
            skipped_reasons=skipped_reasons,
        )

    async def list_artifacts(
        self,
        *,
        task_id: str | None = None,
        dispatch_id: str | None = None,
        runner_id: str | None = None,
        artifact_type: str | None = None,
    ) -> list[CompatibilityArtifactRecord]:
        async with self._lock:
            artifacts = list(self._artifacts.values())
            if task_id:
                artifacts = [artifact for artifact in artifacts if artifact.task_id == task_id]
            if dispatch_id:
                artifacts = [artifact for artifact in artifacts if artifact.dispatch_id == dispatch_id]
            if runner_id:
                artifacts = [artifact for artifact in artifacts if artifact.runner_id == runner_id]
            if artifact_type:
                artifacts = [artifact for artifact in artifacts if artifact.type == artifact_type]
            return sorted(artifacts, key=lambda item: item.created_at, reverse=True)

    async def read_artifact_content(self, artifact_id: str) -> dict[str, Any]:
        async with self._lock:
            artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        if artifact.uri.startswith("rustfs://") and self._artifact_storage_service is not None:
            stored = await self._artifact_storage_service.read_object_uri(artifact.uri)
            return {
                "content": stored["content"],
                "content_type": stored.get("content_type") or artifact.mime_type or "application/octet-stream",
                "filename": artifact.metadata.get("original_filename") or artifact.label or artifact.artifact_id,
            }
        local_path = str(artifact.metadata.get("local_path") or "").strip()
        if local_path:
            path = Path(local_path).resolve()
            root = self._local_artifact_root.resolve()
            if path == root or root in path.parents:
                if not path.exists() or not path.is_file():
                    raise ValueError(f"Artifact file is missing: {artifact_id}.")
                return {
                    "path": path,
                    "content_type": artifact.mime_type or self._content_type(path.name),
                    "filename": artifact.metadata.get("original_filename") or path.name,
                }
        raise ValueError(f"Artifact content is not available for {artifact_id}.")

    async def summarize_tasks(
        self,
        *,
        dispatch_id: str | None = None,
        runner_id: str | None = None,
    ) -> CompatibilityRunnerTaskSummary:
        tasks = await self.list_tasks(dispatch_id=dispatch_id, runner_id=runner_id)
        summary = CompatibilityRunnerTaskSummary(total=len(tasks))
        for task in tasks:
            status = str(task.status or "other").lower()
            if status in {"queued", "assigned", "completed", "failed", "cancelled"}:
                setattr(summary, status, getattr(summary, status) + 1)
            else:
                summary.other += 1
            summary.artifact_count += len(task.artifacts)
            env_summary = summary.by_environment.setdefault(
                task.environment_id,
                {
                    "total": 0,
                    "queued": 0,
                    "assigned": 0,
                    "completed": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "other": 0,
                    "artifact_count": 0,
                },
            )
            env_summary["total"] += 1
            env_summary[status if status in env_summary else "other"] += 1
            env_summary["artifact_count"] += len(task.artifacts)
            if status == "failed":
                summary.failure_summaries.append(
                    {
                        "task_id": task.task_id,
                        "environment_id": task.environment_id,
                        "runner_id": task.assigned_runner_id,
                        "summary": task.result.get("summary") or task.error or "Runner task failed.",
                        "error": task.error,
                        "artifact_count": len(task.artifacts),
                    }
                )
        return summary

    async def build_report(
        self,
        *,
        dispatch_id: str | None = None,
        runner_id: str | None = None,
    ) -> CompatibilityExecutionReport:
        tasks = await self.list_tasks(dispatch_id=dispatch_id, runner_id=runner_id)
        artifacts = self._current_task_artifacts(tasks)
        completed = sum(1 for task in tasks if task.status == "completed")
        failed = sum(1 for task in tasks if task.status == "failed")
        cancelled = sum(1 for task in tasks if task.status == "cancelled")
        pending = sum(1 for task in tasks if task.status not in {"completed", "failed", "cancelled"})
        total = len(tasks)
        pass_rate = round((completed / total) * 100, 2) if total else 0
        environment_reports = self._environment_reports(tasks)
        failures = [
            CompatibilityFailureReport(
                task_id=task.task_id,
                environment_id=task.environment_id,
                runner_id=task.assigned_runner_id,
                summary=str(task.result.get("summary") or task.error or "Runner task failed."),
                error=task.error,
                artifact_ids=[artifact.artifact_id for artifact in task.artifacts],
            )
            for task in tasks
            if task.status == "failed"
        ]
        recoverable_tasks = [
            CompatibilityRecoverableTaskReport(
                task_id=task.task_id,
                environment_id=task.environment_id,
                runner_id=task.assigned_runner_id,
                status=task.status,
                summary=self._recoverable_task_summary(task),
                error=task.error,
            )
            for task in tasks
            if self._is_recoverable_task(task)
        ]
        status = "empty"
        if total:
            if failed:
                status = "failed"
            elif pending:
                status = "running"
            elif cancelled and completed:
                status = "partial"
            elif cancelled:
                status = "cancelled"
            else:
                status = "completed"
        summary = self._report_summary(
            total=total,
            completed=completed,
            failed=failed,
            cancelled=cancelled,
            pending=pending,
            pass_rate=pass_rate,
        )
        report = CompatibilityExecutionReport(
            report_id=new_id("compat_report"),
            dispatch_id=dispatch_id,
            runner_id=runner_id,
            generated_at=_utc_now(),
            status=status,
            summary=summary,
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            cancelled_tasks=cancelled,
            pending_tasks=pending,
            pass_rate=pass_rate,
            artifact_count=len(artifacts),
            environments=environment_reports,
            failures=failures,
            recoverable_tasks=recoverable_tasks,
            artifacts=artifacts[:50],
        )
        return report.model_copy(update={"markdown": self._report_markdown(report, tasks)})

    def _runner_matches_task(self, runner: CompatibilityRunnerRecord, task: CompatibilityQueuedTask) -> bool:
        return self._runner_matches_selector(runner, task.runner_selector or {})

    def _current_task_artifacts(self, tasks: list[CompatibilityQueuedTask]) -> list[CompatibilityArtifactRecord]:
        artifacts_by_id: dict[str, CompatibilityArtifactRecord] = {}
        for task in tasks:
            for artifact in task.artifacts:
                artifacts_by_id[artifact.artifact_id] = artifact
        return sorted(artifacts_by_id.values(), key=lambda item: item.created_at, reverse=True)

    def _runner_matches_selector(self, runner: CompatibilityRunnerRecord, selector: dict[str, Any]) -> bool:
        capabilities = set(self._normalize_list(runner.capabilities))
        required = set(self._normalize_list(selector.get("capabilities", [])))
        provider = str(selector.get("provider") or "").strip().lower()
        if provider:
            required.add(provider)
        browser = str(selector.get("browser") or "").strip().lower()
        if browser:
            required.add(browser)
        if required and not required.issubset(capabilities):
            return False
        os_name = str(selector.get("os") or "").strip().lower()
        runner_os = str(runner.os or "").strip().lower()
        if os_name and (not runner_os or os_name not in runner_os):
            return False
        os_version = str(selector.get("os_version") or "").strip().lower()
        if os_version:
            runner_os_version = str(runner.metadata.get("os_version") or "").strip().lower()
            if not self._version_matches(os_version, [runner_os, runner_os_version]):
                return False
        browser_version = str(selector.get("browser_version") or "").strip().lower()
        if browser_version and browser_version not in {"latest", "stable", "current"}:
            runner_browser_versions = self._runner_browser_versions(runner, browser)
            if not self._version_matches(browser_version, runner_browser_versions):
                return False
        device = str(selector.get("device") or "").strip().lower()
        if device:
            runner_devices = self._normalize_list(runner.devices)
            if not runner_devices or not any(device in item or item in device for item in runner_devices):
                return False
        return True

    def _runner_browser_versions(self, runner: CompatibilityRunnerRecord, browser: str) -> list[str]:
        values: list[Any] = []
        version = runner.metadata.get("browser_version")
        if version:
            values.append(version)
        versions = runner.metadata.get("browser_versions")
        if isinstance(versions, dict):
            if browser:
                values.extend(
                    self._metadata_values(
                        versions.get(browser) or versions.get(browser.lower()) or versions.get(browser.title())
                    )
                )
        elif versions:
            values.extend(self._metadata_values(versions))
        return self._normalize_list(values)

    def _metadata_values(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _version_matches(self, required: str, candidates: list[str]) -> bool:
        expected = str(required or "").strip().lower()
        if not expected:
            return True
        for raw_candidate in candidates:
            candidate = str(raw_candidate or "").strip().lower()
            if not candidate:
                continue
            if candidate == expected:
                return True
            if candidate.startswith(expected) and self._version_boundary(candidate, len(expected)):
                return True
            for token in self._version_tokens(candidate):
                if token == expected:
                    return True
                if token.startswith(expected) and self._version_boundary(token, len(expected)):
                    return True
        return False

    def _version_boundary(self, value: str, index: int) -> bool:
        return index >= len(value) or not value[index].isalnum()

    def _version_tokens(self, value: str) -> list[str]:
        tokens: list[str] = []
        current: list[str] = []
        for char in value:
            if char.isalnum() or char == ".":
                current.append(char)
            elif current:
                tokens.append("".join(current))
                current = []
        if current:
            tokens.append("".join(current))
        return tokens

    def _effective_runner_record(self, runner: CompatibilityRunnerRecord) -> CompatibilityRunnerRecord:
        if not self._is_runner_stale(runner):
            return runner
        return runner.model_copy(
            update={
                "status": "offline",
                "active_task_ids": [],
                "metadata": {
                    **runner.metadata,
                    "offline_reason": "heartbeat_timeout",
                    "heartbeat_timeout_seconds": self._runner_heartbeat_timeout_seconds(),
                },
            }
        )

    def _is_runner_stale(self, runner: CompatibilityRunnerRecord) -> bool:
        age_seconds = self._runner_heartbeat_age_seconds(runner)
        if age_seconds is None:
            return True
        return age_seconds > self._runner_heartbeat_timeout_seconds()

    def _runner_heartbeat_age_seconds(self, runner: CompatibilityRunnerRecord) -> float | None:
        heartbeat_at = self._parse_utc_timestamp(runner.heartbeat_at)
        if heartbeat_at is None:
            return None
        return (datetime.now(UTC).replace(tzinfo=None) - heartbeat_at).total_seconds()

    def _runner_heartbeat_timeout_seconds(self) -> int:
        return max(30, int(getattr(self._settings, "compatibility_runner_heartbeat_timeout_seconds", 120) or 120))

    def _requeue_stale_assigned_tasks_locked(self, now: str | None = None, *, persist: bool = True) -> list[str]:
        now = now or _utc_now()
        requeued: list[str] = []
        stale_runner_ids = {
            runner_id
            for runner_id, runner in self._runners.items()
            if self._is_runner_stale(runner)
        }
        for task_id, task in list(self._tasks.items()):
            if task.status != "assigned" or not task.assigned_runner_id:
                continue
            if task.assigned_runner_id not in stale_runner_ids and task.assigned_runner_id in self._runners:
                continue
            retry_history = list(task.metadata.get("retry_history") or [])
            retry_history.append(
                {
                    "at": now,
                    "reason": "runner_heartbeat_timeout",
                    "previous_status": task.status,
                    "previous_runner_id": task.assigned_runner_id,
                    "previous_summary": task.result.get("summary") if isinstance(task.result, dict) else "",
                    "previous_error": task.error,
                    "previous_artifact_ids": [artifact.artifact_id for artifact in task.artifacts],
                }
            )
            self._tasks[task_id] = task.model_copy(
                update={
                    "status": "queued",
                    "assigned_runner_id": None,
                    "result": {},
                    "artifacts": [],
                    "error": None,
                    "updated_at": now,
                    "metadata": {
                        **self._clear_mode_call_execution_metadata(task.metadata),
                        "retry_count": int(task.metadata.get("retry_count") or 0) + 1,
                        "retry_history": retry_history[-10:],
                    },
                }
            )
            requeued.append(task_id)
        if requeued:
            for runner_id, runner in list(self._runners.items()):
                active = [item for item in runner.active_task_ids if item not in requeued]
                if active != runner.active_task_ids:
                    self._runners[runner_id] = runner.model_copy(update={"active_task_ids": active})
            if persist:
                self._save_state()
        return requeued

    def _parse_utc_timestamp(self, value: str) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.removesuffix("Z"))
        except ValueError:
            return None

    def _normalize_list(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            values = [values] if values else []
        return [str(item).strip().lower() for item in values if str(item).strip()]

    def _clear_mode_call_execution_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            **metadata,
            "mode_calls_execution_status": None,
            "mode_calls_execution_started_at": None,
            "mode_calls_execution_completed_at": None,
            "mode_calls_execution_result_count": None,
            "mode_calls_execution_runner_id": None,
        }

    def _unique_artifact_id(self, requested_id: str, reserved_artifact_ids: set[str]) -> str:
        artifact_id = str(requested_id or "").strip()
        if artifact_id and artifact_id not in self._artifacts and artifact_id not in reserved_artifact_ids:
            return artifact_id
        while True:
            artifact_id = new_id("artifact")
            if artifact_id not in self._artifacts and artifact_id not in reserved_artifact_ids:
                return artifact_id

    def _normalize_artifact(
        self,
        item: dict[str, Any],
        *,
        task: CompatibilityQueuedTask,
        runner_id: str,
        created_at: str,
        reserved_artifact_ids: set[str] | None = None,
    ) -> CompatibilityArtifactRecord:
        artifact_id = self._unique_artifact_id(
            str(item.get("artifact_id") or item.get("id") or ""),
            reserved_artifact_ids or set(),
        )
        artifact_type = str(item.get("type") or item.get("kind") or "evidence").strip() or "evidence"
        uri = str(item.get("uri") or item.get("url") or "").strip()
        metadata = self._reported_artifact_metadata(item.get("metadata"))
        return CompatibilityArtifactRecord(
            artifact_id=artifact_id,
            task_id=task.task_id,
            dispatch_id=task.dispatch_id,
            plan_id=task.plan_id,
            runner_id=runner_id,
            environment_id=task.environment_id,
            type=artifact_type,
            uri=uri,
            mime_type=item.get("mime_type") or item.get("content_type"),
            label=str(item.get("label") or item.get("name") or artifact_type).strip(),
            created_at=str(item.get("created_at") or created_at),
            metadata={
                **metadata,
                "case_ids": task.case_ids,
            },
        )

    def _reported_artifact_metadata(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        server_controlled_keys = {
            "bucket",
            "local_path",
            "object_name",
            "original_filename",
            "size_bytes",
            "storage_backend",
        }
        return {key: metadata_value for key, metadata_value in value.items() if key not in server_controlled_keys}

    def _reported_task_metadata(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        server_controlled_keys = {
            "mode_calls_execution_completed_at",
            "mode_calls_execution_result_count",
            "mode_calls_execution_runner_id",
            "mode_calls_execution_started_at",
            "mode_calls_execution_status",
            "retry_count",
            "retry_history",
        }
        return {key: metadata_value for key, metadata_value in value.items() if key not in server_controlled_keys}

    def _environment_reports(self, tasks: list[CompatibilityQueuedTask]) -> list[CompatibilityEnvironmentReport]:
        grouped: dict[str, list[CompatibilityQueuedTask]] = {}
        for task in tasks:
            grouped.setdefault(task.environment_id, []).append(task)
        reports: list[CompatibilityEnvironmentReport] = []
        for environment_id, items in sorted(grouped.items()):
            completed = sum(1 for task in items if task.status == "completed")
            failed = sum(1 for task in items if task.status == "failed")
            cancelled = sum(1 for task in items if task.status == "cancelled")
            pending = sum(1 for task in items if task.status not in {"completed", "failed", "cancelled"})
            if failed:
                status = "failed"
            elif pending:
                status = "running"
            elif cancelled and completed:
                status = "partial"
            elif cancelled:
                status = "cancelled"
            else:
                status = "completed" if items else "running"
            reports.append(
                CompatibilityEnvironmentReport(
                    environment_id=environment_id,
                    total=len(items),
                    completed=completed,
                    failed=failed,
                    cancelled=cancelled,
                    pending=pending,
                    artifact_count=sum(len(task.artifacts) for task in items),
                    status=status,
                )
            )
        return reports

    def _is_recoverable_task(self, task: CompatibilityQueuedTask) -> bool:
        if task.status in {"failed", "cancelled"}:
            return True
        bridge_status = str(task.metadata.get("mode_calls_execution_status") or "").strip().lower()
        return task.status == "assigned" and bridge_status in {"failed", "partial"}

    def _recoverable_task_summary(self, task: CompatibilityQueuedTask) -> str:
        if task.result.get("summary") or task.error:
            return str(task.result.get("summary") or task.error)
        bridge_status = str(task.metadata.get("mode_calls_execution_status") or "").strip().lower()
        if task.status == "assigned" and bridge_status in {"failed", "partial"}:
            return f"Mode call bridge ended as {bridge_status}; task can be requeued."
        return "Runner task can be retried."

    def _report_summary(
        self,
        *,
        total: int,
        completed: int,
        failed: int,
        cancelled: int,
        pending: int,
        pass_rate: float,
    ) -> str:
        if total == 0:
            return "暂无兼容性测试执行任务。"
        return (
            f"兼容性执行报告：共 {total} 个环境任务，通过 {completed}，失败 {failed}，"
            f"取消 {cancelled}，待完成 {pending}，通过率 {pass_rate}%。"
        )

    def _report_markdown(self, report: CompatibilityExecutionReport, tasks: list[CompatibilityQueuedTask]) -> str:
        lines = [
            "# 兼容性测试执行报告",
            "",
            f"- 状态：{report.status}",
            f"- 总任务：{report.total_tasks}",
            f"- 通过：{report.completed_tasks}",
            f"- 失败：{report.failed_tasks}",
            f"- 取消：{report.cancelled_tasks}",
            f"- 待完成：{report.pending_tasks}",
            f"- 通过率：{report.pass_rate}%",
            f"- 证据数：{report.artifact_count}",
            "",
            "## 环境结果",
        ]
        if report.environments:
            for environment in report.environments:
                lines.append(
                    f"- {environment.environment_id}：{environment.status}，"
                    f"通过 {environment.completed} / 失败 {environment.failed} / "
                    f"取消 {environment.cancelled} / 待完成 {environment.pending}，"
                    f"证据 {environment.artifact_count}"
                )
        else:
            lines.append("- 暂无环境执行结果。")
        lines.extend(["", "## 失败摘要"])
        if report.failures:
            for failure in report.failures:
                artifacts = "，证据：" + ", ".join(failure.artifact_ids[:5]) if failure.artifact_ids else ""
                lines.append(f"- {failure.environment_id} / {failure.task_id}：{failure.summary}{artifacts}")
        else:
            lines.append("- 暂无失败任务。")
        lines.extend(["", "## 可重跑任务"])
        if report.recoverable_tasks:
            for task in report.recoverable_tasks[:20]:
                lines.append(
                    f"- [{task.status}] {task.environment_id} / {task.task_id}："
                    f"{task.summary or task.error or '可重新入队执行。'}"
                )
        else:
            lines.append("- 暂无可重跑任务。")
        lines.extend(["", "## 模式调用结果"])
        mode_call_lines = self._mode_call_markdown_lines(tasks)
        if mode_call_lines:
            lines.extend(mode_call_lines)
        else:
            lines.append("- 暂无模式调用结果。")
        lines.extend(["", "## 证据索引"])
        if report.artifacts:
            for artifact in report.artifacts[:20]:
                lines.append(f"- [{artifact.type}] {artifact.label or artifact.artifact_id}：{artifact.uri}")
        else:
            lines.append("- 暂无证据。")
        return "\n".join(lines)

    def _mode_call_markdown_lines(self, tasks: list[CompatibilityQueuedTask]) -> list[str]:
        lines: list[str] = []
        for task in tasks:
            raw_results = task.result.get("mode_call_results") if isinstance(task.result, dict) else None
            if not isinstance(raw_results, list) or not raw_results:
                continue
            labels: list[str] = []
            for item in raw_results[:6]:
                if not isinstance(item, dict):
                    continue
                tool_key = str(item.get("tool_key") or item.get("toolKey") or "mode-call").strip()
                status = str(item.get("status") or "unknown").strip()
                summary = str(item.get("summary") or "").strip()
                label = f"{tool_key}={status}"
                if summary:
                    label = f"{label}（{summary[:80]}）"
                labels.append(label)
            if labels:
                lines.append(f"- {task.environment_id} / {task.task_id}：{'; '.join(labels)}")
        return lines

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        runners = raw.get("runners") if isinstance(raw, dict) else None
        tasks = raw.get("tasks") if isinstance(raw, dict) else None
        artifacts = raw.get("artifacts") if isinstance(raw, dict) else None
        state_changed = False
        if isinstance(runners, list):
            for item in runners:
                if not isinstance(item, dict):
                    continue
                try:
                    record = CompatibilityRunnerRecord.model_validate(item)
                    self._runners[record.runner_id] = record
                except Exception:
                    continue
        if isinstance(tasks, list):
            for item in tasks:
                if not isinstance(item, dict):
                    continue
                try:
                    task = CompatibilityQueuedTask.model_validate(item)
                    if task.status == "assigned":
                        state_changed = True
                        retry_history = list(task.metadata.get("retry_history") or [])
                        retry_history.append(
                            {
                                "at": _utc_now(),
                                "reason": "runner_service_restart",
                                "previous_status": task.status,
                                "previous_runner_id": task.assigned_runner_id,
                                "previous_summary": task.result.get("summary") if isinstance(task.result, dict) else "",
                                "previous_error": task.error,
                                "previous_artifact_ids": [artifact.artifact_id for artifact in task.artifacts],
                            }
                        )
                        task = task.model_copy(
                            update={
                                "status": "queued",
                                "assigned_runner_id": None,
                                "result": {},
                                "artifacts": [],
                                "error": None,
                                "metadata": {
                                    **self._clear_mode_call_execution_metadata(task.metadata),
                                    "retry_count": int(task.metadata.get("retry_count") or 0) + 1,
                                    "retry_history": retry_history[-10:],
                                },
                            }
                        )
                    self._tasks[task.task_id] = task
                except Exception:
                    continue
        if isinstance(artifacts, list):
            for item in artifacts:
                if not isinstance(item, dict):
                    continue
                try:
                    artifact = CompatibilityArtifactRecord.model_validate(item)
                    self._artifacts[artifact.artifact_id] = artifact
                except Exception:
                    continue
        for runner_id, runner in list(self._runners.items()):
            active_task_ids = self._active_task_ids_for_runner(runner_id)
            if active_task_ids != runner.active_task_ids:
                state_changed = True
            self._runners[runner_id] = runner.model_copy(update={"active_task_ids": active_task_ids})
        if state_changed:
            self._save_state()

    def _save_state(self) -> None:
        payload = {
            "schema_version": 1,
            "updated_at": _utc_now(),
            "runners": [item.model_dump(mode="json") for item in self._runners.values()],
            "tasks": [item.model_dump(mode="json") for item in self._tasks.values()],
            "artifacts": [item.model_dump(mode="json") for item in self._artifacts.values()],
        }
        temp_path = self._state_path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self._state_path)
        except OSError:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _decode_base64(self, value: str) -> bytes:
        raw = str(value or "")
        if "," in raw and raw.strip().lower().startswith("data:"):
            raw = raw.split(",", 1)[1]
        return base64.b64decode(raw, validate=True)

    async def _store_uploaded_artifact(
        self,
        *,
        artifact_id: str,
        task: CompatibilityQueuedTask,
        payload: CompatibilityArtifactUploadRequest,
        content: bytes,
    ) -> tuple[str, dict[str, Any]]:
        filename = self._safe_filename(payload.filename or f"{artifact_id}.bin")
        content_type = payload.mime_type or self._content_type(filename)
        object_prefix = f"compatibility/{task.plan_id}/{task.dispatch_id}/{task.task_id}"
        if self._artifact_storage_service is not None and getattr(self._artifact_storage_service, "enabled", False):
            stored = await self._artifact_storage_service.store_uploaded_bytes(
                content=content,
                filename=filename,
                object_prefix=object_prefix,
                content_type=content_type,
            )
            return str(stored.get("uri") or stored.get("path")), {
                "storage_backend": stored.get("storage_backend", "rustfs"),
                "bucket": stored.get("bucket", ""),
                "object_name": stored.get("object_name", ""),
            }

        storage_key = self._safe_filename(artifact_id)
        directory = self._local_artifact_root / "compatibility" / storage_key[-2:]
        directory.mkdir(parents=True, exist_ok=True)
        path = (directory / storage_key).resolve()
        root = self._local_artifact_root.resolve()
        if root not in path.parents:
            raise ValueError("Artifact path escaped local artifact root.")
        path.write_bytes(content)
        return f"/api/v1/compatibility/artifacts/{artifact_id}/content", {
            "storage_backend": "local",
            "local_path": str(path),
        }

    def _safe_filename(self, value: str) -> str:
        normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value))
        return normalized.strip("._") or "artifact"

    def _content_type(self, filename: str) -> str:
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or "application/octet-stream"

    async def _delete_stored_artifact(self, uri: str, metadata: dict[str, Any]) -> None:
        try:
            if uri.startswith("rustfs://") and self._artifact_storage_service is not None:
                await self._artifact_storage_service.delete_object_uri(uri)
                return
            local_path = str(metadata.get("local_path") or "").strip()
            if local_path:
                path = Path(local_path).resolve()
                root = self._local_artifact_root.resolve()
                if path != root and root in path.parents:
                    path.unlink(missing_ok=True)
        except Exception:
            pass

    def _active_task_ids_for_runner(self, runner_id: str) -> list[str]:
        return sorted(
            task.task_id
            for task in self._tasks.values()
            if task.status == "assigned" and task.assigned_runner_id == runner_id
        )

    def _ensure_runner_can_write_task(self, runner_id: str, task: CompatibilityQueuedTask) -> None:
        if runner_id not in self._runners:
            raise CompatibilityRunnerNotFound(runner_id)
        if task.assigned_runner_id != runner_id:
            if task.assigned_runner_id:
                raise PermissionError(f"Task {task.task_id} is assigned to {task.assigned_runner_id}.")
            raise PermissionError(f"Task {task.task_id} is not assigned to runner {runner_id}.")

    def _ensure_task_can_be_reported(self, task: CompatibilityQueuedTask) -> None:
        if task.status != "assigned":
            raise PermissionError(f"Task {task.task_id} is already {task.status} and cannot be reported again.")

    def _normalize_report_status(self, value: str) -> str:
        status = str(value or "").strip().lower()
        aliases = {
            "passed": "completed",
            "pass": "completed",
            "success": "completed",
            "error": "failed",
            "failure": "failed",
            "canceled": "cancelled",
        }
        normalized = aliases.get(status, status)
        if normalized not in {"completed", "failed", "cancelled"}:
            raise ValueError("Runner task report status must be completed, failed, or cancelled.")
        return normalized
