from __future__ import annotations

import logging
from copy import deepcopy
from urllib.parse import quote
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute

from src.application.compatibility.runner_service import (
    CompatibilityArtifactContentUnavailable,
    CompatibilityModeExecutionAuthorityLost,
    CompatibilityRunnerNotFound,
    CompatibilityRunnerReportObserved,
    CompatibilityRunnerStateError,
)
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.modes.compatibility_testing_mode.runtime import CompatibilityTestingModeRuntime
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.compatibility_runner import (
    CompatibilityArtifactRecord,
    CompatibilityArtifactUploadRequest,
    CompatibilityExecutionReport,
    CompatibilityModeExecutionReference,
    CompatibilityModeExecutionQuery,
    CompatibilityPlanActionRequest,
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
    CompatibilityQueuedTask,
    runner_mode_response,
)


logger = logging.getLogger(__name__)


class _RunnerStateErrorRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def state_error_handler(request: Request):
            try:
                return await original_handler(request)
            except CompatibilityRunnerStateError as exc:
                logger.error("compatibility_runner_state_request_failed", extra={
                    "request_method": request.method,
                    "route_path": request.scope["route"].path,
                })
                raise HTTPException(status_code=503, detail="Compatibility runner state is unavailable.") from exc

        return state_error_handler


router = APIRouter(prefix="/compatibility", tags=["compatibility"], route_class=_RunnerStateErrorRoute)


def _content_disposition_inline(filename: str) -> str:
    safe_filename = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\", ";"} else "_"
        for ch in str(filename or "artifact")
    ).strip(" ._")
    safe_filename = safe_filename or "artifact"
    encoded = quote(str(filename or safe_filename), safe="")
    return f'inline; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded}'


def _runtime_for_request(request: Request) -> CompatibilityTestingModeRuntime:
    return CompatibilityTestingModeRuntime(
        settings=getattr(request.app.state, "settings", None),
        runner_service=request.app.state.compatibility_runner_service,
        mode_call_bridge_enabled=(
            getattr(request.app.state, "tool_runtime_service", None) is not None
            and getattr(request.app.state, "tool_registry", None) is not None
        ),
    )


async def _handle_plan_action(
    request: Request,
    payload: CompatibilityPlanActionRequest,
    *,
    default_action: str,
) -> dict[str, Any]:
    arguments = payload.model_dump(
        mode="python",
        exclude={"context_bundle"},
        exclude_none=True,
    )
    arguments["action"] = str(arguments.get("action") or default_action)
    context = SimpleNamespace(
        session_id="",
        user_message=str(arguments.get("objective") or ""),
        context_bundle=payload.context_bundle,
    )
    result = await _runtime_for_request(request).handle(arguments, context)
    if result.get("ok") is False:
        raise HTTPException(
            status_code=400,
            detail=result.get("summary") or result.get("error") or "Compatibility plan request failed.",
        )
    return result


@router.post("/plans/draft", response_model=dict[str, Any])
async def draft_plan(payload: CompatibilityPlanActionRequest, request: Request):
    return await _handle_plan_action(request, payload, default_action="draft_plan")


@router.post("/plans/dispatch", response_model=dict[str, Any])
async def dispatch_plan(payload: CompatibilityPlanActionRequest, request: Request):
    return await _handle_plan_action(request, payload, default_action="execute_approved_plan")


@router.get("/runners", response_model=list[CompatibilityRunnerRecord])
async def list_runners(request: Request):
    return await request.app.state.compatibility_runner_service.list_runners()


@router.post("/runners/register", response_model=CompatibilityRunnerRecord)
async def register_runner(payload: CompatibilityRunnerRegistrationRequest, request: Request):
    return await request.app.state.compatibility_runner_service.register_runner(payload)


@router.post("/runners/cleanup", response_model=CompatibilityRunnerCleanupResponse)
async def cleanup_runners(payload: CompatibilityRunnerCleanupRequest, request: Request):
    return await request.app.state.compatibility_runner_service.cleanup_offline_runners(payload)


@router.post("/runners/{runner_id}/heartbeat", response_model=CompatibilityRunnerRecord)
async def heartbeat_runner(
    runner_id: str,
    payload: CompatibilityRunnerHeartbeatRequest,
    request: Request,
):
    try:
        return await request.app.state.compatibility_runner_service.heartbeat(runner_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runner not found") from exc


@router.post("/runners/{runner_id}/tasks/poll", response_model=CompatibilityRunnerPollResponse)
async def poll_runner_tasks(
    runner_id: str,
    request: Request,
    limit: int = Query(1, ge=1, le=20),
    request_id: str | None = Query(None, min_length=1, max_length=128),
    sequence: int | None = Query(None, ge=1),
    epoch: str | None = Query(None, min_length=1, max_length=128),
):
    try:
        return await request.app.state.compatibility_runner_service.poll_tasks(
            runner_id, limit=limit, request_id=request_id, sequence=sequence, epoch=epoch,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Runner not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runners/{runner_id}/tasks/{task_id}/report", response_model=CompatibilityQueuedTask)
async def report_runner_task(
    runner_id: str,
    task_id: str,
    payload: CompatibilityRunnerTaskReportRequest,
    request: Request,
):
    try:
        return await request.app.state.compatibility_runner_service.report_task(runner_id, task_id, payload)
    except CompatibilityRunnerReportObserved as exc:
        return JSONResponse(status_code=exc.status_code, content={
            "detail": str(exc), "observation": exc.observation.model_dump(mode="json"),
        })
    except CompatibilityRunnerNotFound as exc:
        raise HTTPException(status_code=404, detail="Runner not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runners/{runner_id}/tasks/{task_id}/artifacts/upload", response_model=CompatibilityArtifactRecord)
async def upload_runner_task_artifact(
    runner_id: str,
    task_id: str,
    payload: CompatibilityArtifactUploadRequest,
    request: Request,
):
    try:
        return await request.app.state.compatibility_runner_service.upload_task_artifact(
            runner_id,
            task_id,
            payload,
        )
    except CompatibilityRunnerNotFound as exc:
        raise HTTPException(status_code=404, detail="Runner not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CompatibilityRunnerStateError:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runners/{runner_id}/tasks/{task_id}/mode-calls/execute", response_model=dict[str, Any])
async def execute_runner_task_mode_calls(
    runner_id: str,
    task_id: str,
    request: Request,
    assignment_id: str | None = Query(None),
):
    tool_runtime_service = getattr(request.app.state, "tool_runtime_service", None)
    tool_registry = getattr(request.app.state, "tool_registry", None)
    if tool_runtime_service is None or tool_registry is None:
        raise HTTPException(status_code=503, detail="Mode invocation runtime is not configured.")

    try:
        task = await request.app.state.compatibility_runner_service.begin_mode_call_execution(
            runner_id, task_id, assignment_id=assignment_id,
        )
    except CompatibilityRunnerNotFound as exc:
        raise HTTPException(status_code=404, detail="Runner not found") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    mode_calls = [item for item in task.mode_calls if isinstance(item, dict)]
    reference = CompatibilityModeExecutionReference.model_validate(task.metadata["mode_calls_execution_reference"])
    results: list[dict[str, Any]] = []
    error_type: str | None = None
    authority_error: CompatibilityModeExecutionAuthorityLost | None = None
    try:
        for index, mode_call in enumerate(mode_calls):
            await request.app.state.compatibility_runner_service.assert_mode_call_execution(reference)
            tool_key = str(mode_call.get("tool_key") or "").strip()
            arguments = deepcopy(mode_call.get("arguments")) if isinstance(mode_call.get("arguments"), dict) else {}
            if not tool_key:
                results.append(
                    {
                        "call_index": index,
                        "tool_key": "",
                        "status": "failed",
                        "summary": "Compatibility mode call is missing a tool_key.",
                        "input": arguments,
                        "output": {"error": "missing_tool_key"},
                    }
                )
                continue
            try:
                tool = tool_registry.get(tool_key)
            except KeyError:
                results.append(
                    {
                        "call_index": index,
                        "tool_key": tool_key,
                        "status": "failed",
                        "summary": f"No registered tool descriptor found for compatibility mode call '{tool_key}'.",
                        "input": arguments,
                        "output": {"error": "unknown_tool"},
                    }
                )
                continue
            context = ToolExecutionContext(
                session_id=str(task.metadata.get("session_id") or ""),
                turn_id=str(task.metadata.get("turn_id") or task.dispatch_id),
                trace_id=str(task.metadata.get("trace_id") or task.task_id),
                user_message=str(task.metadata.get("objective") or f"Execute compatibility task {task.task_id}."),
                normalized_input=str(task.metadata.get("objective") or ""),
                context_bundle={
                    "compatibility_task": task.model_dump(mode="python"),
                    "compatibility_runner_id": runner_id,
                    "compatibility_mode_call_index": index,
                },
                selected_agent_key="compatibility-testing-agent",
                tool_key=tool_key,
                compatibility_mode_execution=reference,
                compatibility_mode_call_index=index,
            )
            record = await tool_runtime_service.execute(
                tool,
                ModelToolCall(
                    id=f"{task.task_id}-{reference.execution_id}-mode-call-{index + 1}",
                    name=tool_key,
                    arguments=arguments,
                ),
                context,
            )
            result = record.model_dump(mode="json")
            result["call_index"] = index
            results.append(result)
    except CompatibilityRunnerStateError:
        raise
    except CompatibilityModeExecutionAuthorityLost as exc:
        authority_error = exc
    except Exception as exc:
        error_type = type(exc).__name__
        logger.error("compatibility_mode_execution_failed", extra={"task_id": task_id, "runner_id": runner_id,
            "execution_id": reference.execution_id, "error_type": error_type})

    response = runner_mode_response(task_id=task.task_id, runner_id=runner_id,
        mode_call_count=len(mode_calls), results=results, error_type=error_type)
    try:
        response = await request.app.state.compatibility_runner_service.complete_mode_call_execution(
            task, response, error_type=error_type, authority_error=authority_error,
        )
    except CompatibilityModeExecutionAuthorityLost as exc:
        return JSONResponse(status_code=exc.status_code, content={
            "detail": str(exc), "observation": exc.observation.model_dump(mode="json"),
        })
    if error_type is not None:
        raise HTTPException(status_code=500, detail="Mode call execution failed unexpectedly.")
    return response


@router.get("/runners/{runner_id}/tasks/{task_id}/mode-calls/execution", response_model=CompatibilityModeExecutionQuery)
async def query_runner_task_mode_execution(
    runner_id: str, task_id: str, request: Request, assignment_id: str = Query(..., min_length=1),
):
    try:
        return await request.app.state.compatibility_runner_service.query_mode_call_execution(
            runner_id, task_id, assignment_id=assignment_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("/tasks", response_model=list[CompatibilityQueuedTask])
async def list_runner_tasks(
    request: Request,
    dispatch_id: str | None = None,
    runner_id: str | None = None,
):
    return await request.app.state.compatibility_runner_service.list_tasks(
        dispatch_id=dispatch_id,
        runner_id=runner_id,
    )


@router.post("/tasks/requeue", response_model=CompatibilityTaskRequeueResponse)
async def requeue_tasks(payload: CompatibilityTaskRequeueRequest, request: Request):
    return await request.app.state.compatibility_runner_service.requeue_tasks(payload)


@router.get("/tasks/{task_id}/snapshot", response_model=CompatibilityQueuedTask)
async def read_task_snapshot(task_id: str, request: Request):
    """Diagnostic read only; unlike the task list it never reaps assignments."""
    try:
        return await request.app.state.compatibility_runner_service.read_task_snapshot(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.get("/artifacts", response_model=list[CompatibilityArtifactRecord])
async def list_artifacts(
    request: Request,
    task_id: str | None = None,
    dispatch_id: str | None = None,
    runner_id: str | None = None,
    artifact_type: str | None = None,
):
    return await request.app.state.compatibility_runner_service.list_artifacts(
        task_id=task_id,
        dispatch_id=dispatch_id,
        runner_id=runner_id,
        artifact_type=artifact_type,
    )


@router.get("/artifacts/{artifact_id}/content")
async def get_artifact_content(artifact_id: str, request: Request):
    try:
        content = await request.app.state.compatibility_runner_service.read_artifact_content(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    except CompatibilityArtifactContentUnavailable as exc:
        logger.warning("compatibility_artifact_content_missing", extra={"artifact_id": artifact_id})
        raise HTTPException(status_code=404, detail="Artifact content not found") from exc
    except CompatibilityRunnerStateError:
        raise
    except Exception as exc:
        logger.error(
            "compatibility_artifact_content_read_failed",
            extra={"artifact_id": artifact_id, "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=500, detail="Artifact content unavailable") from exc
    if content.get("path"):
        return FileResponse(
            path=content["path"],
            media_type=content.get("content_type") or "application/octet-stream",
            filename=str(content.get("filename") or artifact_id),
        )
    return Response(
        content=content.get("content") or b"",
        media_type=content.get("content_type") or "application/octet-stream",
        headers={"Content-Disposition": _content_disposition_inline(str(content.get("filename") or artifact_id))},
    )


@router.get("/summary", response_model=CompatibilityRunnerTaskSummary)
async def get_summary(
    request: Request,
    dispatch_id: str | None = None,
    runner_id: str | None = None,
):
    return await request.app.state.compatibility_runner_service.summarize_tasks(
        dispatch_id=dispatch_id,
        runner_id=runner_id,
    )


@router.get("/report", response_model=CompatibilityExecutionReport)
async def get_report(
    request: Request,
    dispatch_id: str | None = None,
    runner_id: str | None = None,
):
    return await request.app.state.compatibility_runner_service.build_report(
        dispatch_id=dispatch_id,
        runner_id=runner_id,
    )
