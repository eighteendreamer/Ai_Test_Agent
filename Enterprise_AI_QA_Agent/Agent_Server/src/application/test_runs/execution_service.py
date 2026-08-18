from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
from datetime import datetime, timezone

from src.application.permissions.permission_service import PermissionService
from src.application.runtime.tool_job_service import ToolJobService
from src.application.security.approval_scope_service import ApprovalScopeService
from src.application.security.target_guard import SecurityTargetGuard
from src.application.test_cases.case_service import TestCaseService
from src.application.test_runs.case_execution import (
    CaseExecutionAdapter,
    CaseExecutionBlockedError,
    resolve_security_case_target,
)
from src.application.security.risk_policy import SecurityRiskPolicy
from src.application.test_runs.run_service import TestRunService
from src.runtime.store import SessionStore
from src.schemas.run_management import (
    RunItemApprovalDecisionRequest,
    RunItemCompleteRequest,
    RunItemApprovalPending,
    RunItemApprovalWaitRequest,
    RunItemExecuteRequest,
    RunItemHeartbeatRequest,
    RunItemLeaseRequest,
    TestCaseResultRecord,
)
from src.schemas.session import (
    ExecutionEvent,
    ToolApprovalRequest,
    ToolApprovalStatus,
)


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
        permission_service: PermissionService | None = None,
        tool_job_service: ToolJobService | None = None,
        security_settings: object | None = None,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self._runs = run_service
        self._cases = test_case_service
        self._adapter = adapter
        self._sessions = session_store
        self._permissions = permission_service
        self._jobs = tool_job_service
        self._security_risk_policy = SecurityRiskPolicy()
        self._security_target_guard = SecurityTargetGuard(security_settings)
        self._security_environment = str(
            getattr(security_settings, "app_env", "testing") or "testing"
        ).strip().lower()
        if self._security_environment == "prod":
            self._security_environment = "production"
        self._approval_scope = ApprovalScopeService()
        self._lease_heartbeat_interval_seconds = lease_heartbeat_interval_seconds

    async def execute_item(
        self,
        item_id: str,
        payload: RunItemExecuteRequest,
    ) -> TestCaseResultRecord | RunItemApprovalPending:
        item = await self._runs.start_item(
            item_id,
            RunItemLeaseRequest(lease_token=payload.lease_token),
        )
        run = await self._runs.get_record(item.run_id)
        case = await self._cases.get_case(item.case_id)
        version = await self._cases.get_version(item.case_version_id)
        heartbeat_errors: list[str] = []
        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._keep_item_lease_alive(
                item=item,
                lease_token=payload.lease_token,
                stop_event=heartbeat_stop,
                errors=heartbeat_errors,
            )
        )
        try:
            try:
                trusted_context_bundle = await self._trusted_case_context(
                    run=run,
                    version=version,
                )
                pending = await self._suspend_for_approval_if_required(
                    run=run,
                    case=case,
                    version=version,
                    item=item,
                    lease_token=payload.lease_token,
                    trusted_context_bundle=trusted_context_bundle,
                    approved_approval_id=payload.approval_id,
                )
                if pending is not None:
                    return pending
                if payload.approval_id:
                    await self._require_approved_item_scope(
                        run=run,
                        case=case,
                        version=version,
                        item=item,
                        approval_id=payload.approval_id,
                        trusted_context_bundle=trusted_context_bundle,
                    )
                    if self._jobs is None:
                        raise RuntimeError("Test run approval ToolJob service is not configured")
                    resumed_job = await self._jobs.request_resume(
                        str(item.tool_job_id or ""),
                        reason="Security test run item approval granted.",
                    )
                    if resumed_job is None:
                        raise RuntimeError(
                            f"Tool job not found for approved execution: {item.tool_job_id}"
                        )
                outcome = await self._adapter.execute(
                    case=case,
                    version=version,
                    run=run,
                    item=item,
                    trusted_context_bundle=trusted_context_bundle,
                    tool_job_id=str(item.tool_job_id or "") if payload.approval_id else "",
                    server_approval_granted=bool(payload.approval_id),
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

            if heartbeat_errors:
                heartbeat_detail = "; ".join(heartbeat_errors[-3:])
                completion = completion.model_copy(
                    update={
                        "status": "error",
                        "summary": "Test case lease heartbeat failed; result is not safe to pass.",
                        "error_message": heartbeat_detail,
                        "actual": {
                            **completion.actual,
                            "lease_heartbeat_error": heartbeat_detail,
                        },
                    }
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
        finally:
            heartbeat_stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def resolve_item_approval(
        self,
        item_id: str,
        payload: RunItemApprovalDecisionRequest,
    ) -> TestCaseResultRecord:
        if payload.decision not in {
            ToolApprovalStatus.approved,
            ToolApprovalStatus.denied,
        }:
            raise ValueError("Approval decision must be approved or denied.")
        if self._sessions is None or self._jobs is None:
            raise RuntimeError("Test run approval services are not configured")
        item = await self._runs.get_item(item_id)
        if not item.approval_id or not item.tool_job_id:
            raise ValueError(f"Run item has no approval binding: {item_id}")
        if payload.approval_id != item.approval_id:
            raise ValueError(
                f"Approval {payload.approval_id} does not match run item {item_id}."
            )
        run = await self._runs.get_record(item.run_id)
        if not run.session_id:
            raise ValueError(f"Waiting approval run has no bound session: {run.id}")
        approval = await self._find_item_approval(
            session_id=run.session_id,
            item=item,
            approval_id=payload.approval_id,
        )
        if approval.status != ToolApprovalStatus.pending and approval.status != payload.decision:
            raise ValueError(
                f"Run item approval already resolved as {approval.status.value}: {approval.id}"
            )
        if item.result_id:
            return await self._runs.get_result(item.result_id)
        if item.status not in {"waiting_approval", "claimed"}:
            raise ValueError(f"Run item cannot resume approval from {item.status}: {item_id}")
        if payload.decision == ToolApprovalStatus.approved:
            case = await self._cases.get_case(item.case_id)
            version = await self._cases.get_version(item.case_version_id)
            trusted_context_bundle = await self._trusted_case_context(run=run, version=version)
            await self._validate_approval_scope(
                run=run,
                case=case,
                version=version,
                item=item,
                approval=approval,
                trusted_context_bundle=trusted_context_bundle,
            )
        approval_job = await self._jobs.get_job(item.tool_job_id)
        if approval_job is None:
            raise RuntimeError(f"Tool job not found for run item approval: {item.tool_job_id}")
        resolved = approval
        if approval.status == ToolApprovalStatus.pending:
            resolved = await self._sessions.resolve_approval(
                run.session_id,
                approval.id,
                payload.decision,
                payload.reason,
            )
            if resolved.status != payload.decision:
                raise ValueError(
                    f"Run item approval resolved concurrently as {resolved.status.value}: "
                    f"{approval.id}"
                )
        resumed = await self._runs.resume_waiting_approval(
            item.id,
            approval.id,
        )
        if resolved.status == ToolApprovalStatus.denied:
            try:
                running = await self._runs.start_item(
                    item.id,
                    RunItemLeaseRequest(lease_token=str(resumed.lease_token or "")),
                )
            except ValueError as exc:
                latest = await self._runs.get_item(item.id)
                if latest.result_id:
                    return await self._runs.get_result(latest.result_id)
                raise exc
            denied_job = await self._jobs.mark_denied(
                item.tool_job_id,
                summary=payload.reason or "Security test run item approval denied.",
                output_payload={
                    "status": "denied",
                    "approval_id": approval.id,
                    "run_item_id": item.id,
                },
            )
            if denied_job is None:
                raise RuntimeError(f"Tool job not found for denied approval: {item.tool_job_id}")
            return await self._runs.complete_item(
                item.id,
                RunItemCompleteRequest(
                    lease_token=str(running.lease_token or ""),
                    status="blocked",
                    summary=payload.reason or "Security test run item approval denied.",
                    error_message=payload.reason or "approval_denied",
                    actual={
                        "approval_id": approval.id,
                        "approval_status": resolved.status.value,
                        "tool_job_id": item.tool_job_id,
                    },
                    tool_job_id=item.tool_job_id,
                ),
            )
        try:
            return await self.execute_item(
                item.id,
                RunItemExecuteRequest(
                    lease_token=str(resumed.lease_token or ""),
                    approval_id=approval.id,
                ),
            )
        except ValueError as exc:
            latest = await self._runs.get_item(item.id)
            if latest.result_id:
                return await self._runs.get_result(latest.result_id)
            raise exc

    async def _find_item_approval(
        self,
        *,
        session_id: str,
        item,
        approval_id: str,
    ) -> ToolApprovalRequest:
        if self._sessions is None:
            raise RuntimeError("Session approval store is not configured")
        approvals = await self._sessions.list_approvals(session_id)
        approval = next((value for value in approvals if value.id == approval_id), None)
        if approval is None:
            raise KeyError(f"Approval not found: {approval_id}")
        metadata = approval.metadata if isinstance(approval.metadata, dict) else {}
        if (
            str(metadata.get("run_item_id") or "") != item.id
            or str(metadata.get("tool_job_id") or "") != str(item.tool_job_id or "")
        ):
            raise ValueError(f"Approval does not belong to run item: {item.id}")
        return approval

    async def _require_approved_item_scope(
        self,
        *,
        run,
        case,
        version,
        item,
        approval_id: str,
        trusted_context_bundle: dict,
    ) -> None:
        if not run.session_id or item.approval_id != approval_id or not item.tool_job_id:
            raise CaseExecutionBlockedError("Security approval does not match this run item.")
        approval = await self._find_item_approval(
            session_id=run.session_id,
            item=item,
            approval_id=approval_id,
        )
        if approval.status != ToolApprovalStatus.approved:
            raise CaseExecutionBlockedError("Security approval has not been granted.")
        await self._validate_approval_scope(
            run=run,
            case=case,
            version=version,
            item=item,
            approval=approval,
            trusted_context_bundle=trusted_context_bundle,
        )

    async def _validate_approval_scope(
        self,
        *,
        run,
        case,
        version,
        item,
        approval: ToolApprovalRequest,
        trusted_context_bundle: dict,
    ) -> None:
        invocation = self._adapter.build_invocation(
            case=case,
            version=version,
            run=run,
            item=item,
            trusted_context_bundle=trusted_context_bundle,
        )
        actual_hash = self._approval_scope.build_hash(
            mode_key=run.mode_key,
            tool_key=invocation.tool.key,
            arguments=invocation.call.arguments,
            context=invocation.context.context_bundle,
        )
        expected_hash = str(approval.metadata.get("approval_scope_hash") or "")
        if not expected_hash or actual_hash != expected_hash:
            raise ValueError(f"Security approval scope changed for run item: {item.id}")

    async def _suspend_for_approval_if_required(
        self,
        *,
        run,
        case,
        version,
        item,
        lease_token: str,
        trusted_context_bundle: dict,
        approved_approval_id: str | None,
    ) -> RunItemApprovalPending | None:
        if run.mode_key != "security_testing" or approved_approval_id:
            return None
        runner_arguments = version.test_data.get("runner_arguments", {})
        profile_key = str(
            runner_arguments.get("command_profile")
            if isinstance(runner_arguments, dict)
            else ""
        ).strip()
        if not self._security_risk_policy.requires_approval(profile_key):
            return None
        if self._sessions is None or self._permissions is None or self._jobs is None:
            raise CaseExecutionBlockedError(
                "Security case approval services are not configured."
            )
        invocation = self._adapter.build_invocation(
            case=case,
            version=version,
            run=run,
            item=item,
            trusted_context_bundle=trusted_context_bundle,
        )
        reason = (
            f"Security profile {profile_key} requires explicit approval before "
            f"test run item execution."
        )
        job = await self._jobs.create_job(
            tool=invocation.tool,
            call_id=invocation.call.id,
            session_id=invocation.context.session_id,
            turn_id=invocation.context.turn_id,
            trace_id=invocation.context.trace_id,
            input_payload=invocation.call.arguments,
            metadata={
                "phase": "approval_pending",
                "source": "test_run_case_execution",
                "run_id": run.id,
                "run_item_id": item.id,
                "case_version_id": version.id,
            },
        )
        scope_hash = self._approval_scope.build_hash(
            mode_key=run.mode_key,
            tool_key=invocation.tool.key,
            arguments=invocation.call.arguments,
            context=invocation.context.context_bundle,
        )
        approval = self._permissions.create_approval_request(
            session_id=run.session_id,
            tool=invocation.tool,
            reason=reason,
            metadata={
                "source": "test_run_case_execution",
                "run_id": run.id,
                "run_item_id": item.id,
                "case_id": case.id,
                "case_version_id": version.id,
                "tool_job_id": job.id,
                "call_id": invocation.call.id,
                "approval_mode_key": run.mode_key,
                "approval_scope_hash": scope_hash,
            },
        )
        await self._sessions.save_approval(run.session_id, approval)
        await self._jobs.mark_waiting_approval(
            job.id,
            summary=reason,
            metadata={
                "approval_id": approval.id,
                "approval_scope_hash": scope_hash,
                "run_item_id": item.id,
            },
        )
        waiting = await self._runs.mark_waiting_approval(
            item.id,
            RunItemApprovalWaitRequest(
                lease_token=lease_token,
                approval_id=approval.id,
                tool_job_id=job.id,
                reason=reason,
                approval_scope_hash=scope_hash,
            ),
        )
        logger.info(
            "security_test_run_item_approval_created",
            extra={
                "project_id": run.project_id,
                "run_id": run.id,
                "run_item_id": item.id,
                "approval_id": approval.id,
                "tool_job_id": job.id,
                "profile_key": profile_key,
            },
        )
        return RunItemApprovalPending(
            run_id=run.id,
            run_item_id=item.id,
            attempt_no=waiting.attempt_no,
            approval_id=approval.id,
            tool_job_id=job.id,
            summary=reason,
        )

    async def _keep_item_lease_alive(
        self,
        *,
        item,
        lease_token: str,
        stop_event: asyncio.Event,
        errors: list[str],
    ) -> None:
        lease_seconds = _lease_seconds_from_item(item)
        interval = self._lease_heartbeat_interval_seconds or min(
            30.0,
            max(5.0, lease_seconds / 3),
        )
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
            if stop_event.is_set():
                return
            try:
                renewed = await self._runs.heartbeat_item(
                    item.id,
                    RunItemHeartbeatRequest(
                        lease_token=lease_token,
                        lease_seconds=lease_seconds,
                    ),
                )
                logger.debug(
                    "test_run_item_lease_heartbeat",
                    extra={
                        "run_id": item.run_id,
                        "run_item_id": item.id,
                        "lease_expires_at": renewed.lease_expires_at,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                detail = str(exc)
                errors.append(detail)
                logger.exception(
                    "test_run_item_lease_heartbeat_failed",
                    extra={
                        "run_id": item.run_id,
                        "run_item_id": item.id,
                        "lease_token_present": bool(lease_token),
                    },
                )

    async def _trusted_case_context(self, *, run, version) -> dict:
        if run.mode_key != "security_testing":
            return {}
        if self._sessions is None or not run.session_id:
            raise CaseExecutionBlockedError(
                "Security case execution requires a bound session with verified target authorization."
            )
        runner_arguments = version.test_data.get("runner_arguments", {})
        target = resolve_security_case_target(
            version,
            runner_arguments if isinstance(runner_arguments, dict) else {},
        )
        if not self._security_target_guard.has_allowlist:
            raise CaseExecutionBlockedError(
                "Security case execution requires a configured server security_target_allowlist."
            )
        target_decision = self._security_target_guard.evaluate_target(target)
        if not target_decision.ok or not target_decision.checked_hosts:
            raise CaseExecutionBlockedError(
                "Security case target is outside the server-configured authorization scope."
            )
        grant = {
            "status": "verified",
            "targets": [target],
            "source": "server_security_target_allowlist",
        }
        logger.info(
            "security_case_authorization_verified",
            extra={
                "run_id": run.id,
                "project_id": run.project_id,
                "session_id": run.session_id,
                "target": target,
            },
        )
        context = {
            "trusted_security_authorization": deepcopy(grant),
            "safety_assessment": {
                "authorization_status": "verified",
                "target_scope_status": "in_scope",
                "decision": "allow",
            },
        }
        context["environment"] = self._security_environment
        return context

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


def _lease_seconds_from_item(item) -> int:
    expires_at = getattr(item, "lease_expires_at", None)
    if not isinstance(expires_at, datetime):
        return 90
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return max(15, min(3600, int((expires_at - now).total_seconds())))
