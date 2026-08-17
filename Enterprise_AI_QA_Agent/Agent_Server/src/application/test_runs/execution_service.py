from __future__ import annotations

import logging
from datetime import datetime

from src.application.test_cases.case_service import TestCaseService
from src.application.test_runs.case_execution import (
    CaseExecutionAdapter,
    CaseExecutionBlockedError,
)
from src.application.test_runs.run_service import TestRunService
from src.runtime.store import SessionStore
from src.schemas.run_management import (
    RunItemCompleteRequest,
    RunItemExecuteRequest,
    RunItemLeaseRequest,
    TestCaseResultRecord,
)
from src.schemas.session import ExecutionEvent


logger = logging.getLogger(__name__)


class TestRunExecutionService:
    """协调 RunItem 租约、现有 Runner 和结果落库。"""

    def __init__(
        self,
        *,
        run_service: TestRunService,
        test_case_service: TestCaseService,
        adapter: CaseExecutionAdapter,
        session_store: SessionStore | None = None,
    ) -> None:
        self._runs = run_service
        self._cases = test_case_service
        self._adapter = adapter
        self._sessions = session_store

    async def execute_item(
        self,
        item_id: str,
        payload: RunItemExecuteRequest,
    ) -> TestCaseResultRecord:
        item = await self._runs.start_item(
            item_id,
            RunItemLeaseRequest(lease_token=payload.lease_token),
        )
        run = await self._runs.get_record(item.run_id)
        case = await self._cases.get_case(item.case_id)
        version = await self._cases.get_version(item.case_version_id)
        try:
            outcome = await self._adapter.execute(
                case=case,
                version=version,
                run=run,
                item=item,
            )
            completion = outcome.completion.model_copy(
                update={"lease_token": payload.lease_token}
            )
        except CaseExecutionBlockedError as exc:
            logger.warning(
                "test_run_case_execution_blocked",
                extra={
                    "project_id": run.project_id,
                    "run_id": run.id,
                    "run_item_id": item.id,
                    "case_version_id": version.id,
                },
            )
            completion = RunItemCompleteRequest(
                lease_token=payload.lease_token,
                status="blocked",
                summary=f"Test case execution blocked: {exc}",
                error_message=str(exc),
                actual={"blocked_reason": str(exc)},
            )
            outcome = None
        except Exception as exc:
            logger.exception(
                "test_run_case_execution_failed",
                extra={
                    "project_id": run.project_id,
                    "run_id": run.id,
                    "run_item_id": item.id,
                    "case_version_id": version.id,
                },
            )
            completion = RunItemCompleteRequest(
                lease_token=payload.lease_token,
                status="error",
                summary=f"Test case runner failed: {exc}",
                error_message=str(exc),
                actual={"runner_error": str(exc)},
            )
            outcome = None

        result = await self._runs.complete_item(item.id, completion)
        if outcome is not None and outcome.verification_results:
            await self._persist_verifications(
                run=run,
                result=result,
                verifications=outcome.verification_results,
            )
        logger.info(
            "test_run_case_execution_completed",
            extra={
                "project_id": run.project_id,
                "run_id": run.id,
                "run_item_id": item.id,
                "result_id": result.id,
                "status": result.status,
                "tool_job_id": result.tool_job_id,
            },
        )
        return result

    async def _persist_verifications(self, *, run, result, verifications) -> None:
        """Verification 同时嵌入 Result，并在真实 Session 存在时写入既有会话存储。"""
        if self._sessions is None or not run.session_id:
            return
        try:
            session = await self._sessions.get_session(run.session_id)
            if session is None:
                raise KeyError(f"Session not found: {run.session_id}")
            existing = session.metadata.get("verification_results", [])
            if not isinstance(existing, list):
                existing = []
            serialized = [item.model_dump(mode="python") for item in verifications]
            session.metadata["verification_results"] = [*existing, *serialized]
            await self._sessions.save_session(session)
            await self._sessions.append_event(
                run.session_id,
                ExecutionEvent(
                    type="verification.completed",
                    session_id=run.session_id,
                    timestamp=datetime.utcnow(),
                    payload={
                        "run_id": run.id,
                        "run_item_id": result.run_item_id,
                        "result_id": result.id,
                        "verification_ids": [item.id for item in verifications],
                    },
                ),
            )
        except Exception:
            # Result.actual 已持久化完整 Verification；会话投影失败必须留日志供补偿任务处理。
            logger.exception(
                "test_run_verification_session_projection_failed",
                extra={
                    "run_id": run.id,
                    "run_item_id": result.run_item_id,
                    "result_id": result.id,
                    "session_id": run.session_id,
                },
            )
