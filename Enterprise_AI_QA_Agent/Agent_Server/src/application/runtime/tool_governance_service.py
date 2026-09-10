from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from src.application.permissions.permission_service import (
    PermissionPolicyContext,
    PermissionService,
    ToolPermissionDecision,
)
from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_input_validation import validate_tool_input
from src.application.runtime.tool_runtime_service import (
    ToolExecutionContext,
    ToolRuntimeService,
)
from src.application.security.approval_scope_service import ApprovalScopeService
from src.application.security.execution_safety_policy import ExecutionSafetyPolicy
from src.registry.tools import ToolRegistry
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord
from src.schemas.intent import ToolSafetyDecision
from src.schemas.tool_job import ToolJobStatus


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GovernedToolCallContext:
    execution: ToolExecutionContext
    policy: PermissionPolicyContext
    active_mode_key: str
    available_tool_keys: frozenset[str]
    permission_decisions: dict[str, ToolPermissionDecision] = field(default_factory=dict)


@dataclass(frozen=True)
class GovernedToolCallResult:
    record: ToolExecutionRecord
    approval: dict[str, Any] | None = None
    replayed: bool = False


class ToolGovernanceService:
    """One application boundary for Registry tool authorization and execution.

    Deep Agents framework adapters enter here before a business tool reaches
    ``ToolRuntimeService``. Framework-native tools such as Deep Agents'
    read-only filesystem remain outside this boundary; the legacy graph keeps
    its established equivalent path for backward compatibility.
    """

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        permission_service: PermissionService,
        tool_runtime_service: ToolRuntimeService,
        tool_job_service: ToolJobService | None = None,
        execution_safety_policy: ExecutionSafetyPolicy | None = None,
        approval_scope_service: ApprovalScopeService | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._permission_service = permission_service
        self._tool_runtime_service = tool_runtime_service
        self._tool_job_service = tool_job_service
        self._execution_safety_policy = execution_safety_policy or ExecutionSafetyPolicy()
        self._approval_scope_service = approval_scope_service or ApprovalScopeService()

    async def execute(
        self,
        call: ModelToolCall,
        context: GovernedToolCallContext,
        *,
        approval: dict[str, Any] | None = None,
        prepare_approval: bool = False,
    ) -> GovernedToolCallResult:
        original_arguments = dict(call.arguments)
        assessment = self._assess(call, context)
        if isinstance(assessment, GovernedToolCallResult):
            return assessment
        tool, decision, execution_safety = assessment
        if prepare_approval and not (
            decision.behavior == "ask" or execution_safety.behavior == "ask"
        ):
            return self._terminal_record(
                call=call, tool=tool, status="denied",
                summary="HITL preparation requires an approval-gated call.",
                output={"error": "approval_policy_changed"},
            )
        if approval is not None:
            scope_matches = self._approval_scope_service.matches(
                str(approval.get("metadata", {}).get("approval_scope_hash") or ""),
                mode_key=context.active_mode_key,
                tool_key=tool.key,
                arguments=call.arguments,
                context=context.execution.context_bundle,
            )
            if approval.get("status") != "approved" or not scope_matches:
                return self._terminal_record(
                    call=call, tool=tool, status="denied",
                    summary="Approval denied or its concrete scope changed before execution.",
                    output={"error": "approval_scope_mismatch"},
                )
            context.execution.tool_job_id = str(
                approval.get("metadata", {}).get("tool_job_id") or ""
            )
            call = call.model_copy(
                update={"arguments": {**call.arguments, "_server_approval_granted": True}}
            )
        elif decision.behavior == "ask" or execution_safety.behavior == "ask":
            if prepare_approval:
                return await self._create_approval(
                    tool=tool, call=call, context=context, decision=decision,
                    execution_safety=execution_safety,
                )
            return self._terminal_record(
                call=call, tool=tool, status="denied",
                summary="Concrete tool call requires an approved HITL decision.",
                output={"error": "approval_required"},
            )
        if self._tool_job_service is not None:
            if context.execution.tool_job_id:
                job = await self._tool_job_service.get_job(context.execution.tool_job_id)
                if job is None:
                    raise RuntimeError("Persisted approval ToolJob is missing; reconcile before recovery.")
            else:
                job = await self._tool_job_service.create_job(
                    tool=tool, call_id=call.id, session_id=context.execution.session_id,
                    turn_id=context.execution.turn_id, trace_id=context.execution.trace_id,
                    input_payload=original_arguments, once_per_call=True,
                )
            if (
                job.session_id != context.execution.session_id
                or job.turn_id != context.execution.turn_id
                or job.call_id != call.id or job.tool_key != call.name
                or job.input_payload != original_arguments
            ):
                raise RuntimeError("ToolJob identity or arguments changed during checkpoint recovery.")
            context.execution.tool_job_id = job.id
            claimed = await self._tool_job_service.claim_execution(job.id)
            if claimed is None:
                latest = await self._tool_job_service.get_job(job.id)
                if latest is not None and latest.status in {
                    ToolJobStatus.completed, ToolJobStatus.partial, ToolJobStatus.failed,
                    ToolJobStatus.denied, ToolJobStatus.cancelled,
                }:
                    logger.info(
                        "deep_agent_tool_result_replayed",
                        extra={"tool_job_id": job.id, "call_id": call.id},
                    )
                    return GovernedToolCallResult(
                        record=await self._tool_runtime_service.record_from_job(latest), replayed=True,
                    )
                logger.warning(
                    "deep_agent_tool_execution_requires_reconciliation",
                    extra={
                        "tool_job_id": job.id,
                        "call_id": call.id,
                        "status": str(latest.status) if latest else "missing",
                    },
                )
                raise RuntimeError(
                    f"ToolJob {job.id} has started without a terminal result; "
                    "the prior execution may still be active or have an unknown outcome. "
                    "Reconcile its evidence before any retry."
                )
        return GovernedToolCallResult(
            record=await self._tool_runtime_service.execute(
                tool=tool, call=call, context=context.execution,
            )
        )

    def requires_approval(
        self, call: ModelToolCall, context: GovernedToolCallContext
    ) -> bool:
        """Pure predicate for official HumanInTheLoopMiddleware.when."""
        assessment = self._assess(call, context)
        if isinstance(assessment, GovernedToolCallResult):
            return False
        _, decision, execution_safety = assessment
        return decision.behavior == "ask" or execution_safety.behavior == "ask"

    def _assess(
        self, call: ModelToolCall, context: GovernedToolCallContext
    ) -> GovernedToolCallResult | tuple[ToolDescriptor, ToolPermissionDecision, ToolSafetyDecision]:
        try:
            tool = self._tool_registry.get(call.name)
        except KeyError:
            return self._terminal_record(
                call=call,
                status="failed",
                summary=f"Model requested unknown tool '{call.name}'.",
                output={"error": "unknown_tool", "requested_tool": call.name},
            )

        if tool.key not in context.available_tool_keys:
            return self._terminal_record(
                call=call,
                tool=tool,
                status="denied",
                summary=f"Tool '{tool.name}' was not exposed to the selected agent for this turn.",
                output={
                    "error": "tool_not_exposed",
                    "permission_reason_code": "tool_not_exposed_to_agent",
                },
            )

        decision = context.permission_decisions.get(tool.key)
        if decision is None:
            evaluation = self._permission_service.evaluate(
                policy_context=context.policy,
                tools=[tool],
            )
            decision = self._permission_service.get_tool_decision(evaluation, tool.key)
        if decision is None:
            return self._terminal_record(
                call=call,
                tool=tool,
                status="denied",
                summary=f"Tool '{tool.name}' has no permission decision.",
                output={"error": "permission_decision_missing"},
            )
        if decision.behavior == "deny":
            return self._terminal_record(
                call=call,
                tool=tool,
                status="denied",
                summary=decision.reason,
                output={
                    "error": "permission_denied",
                    "permission_behavior": "deny",
                    "permission_source": decision.source,
                    "permission_reason": decision.reason,
                    "permission_visibility": decision.visibility,
                    "permission_reason_code": decision.reason_code,
                    "permission_policy_key": decision.policy_key,
                },
            )

        validation_errors = validate_tool_input(tool.input_schema, call.arguments)
        if validation_errors:
            return self._terminal_record(
                call=call,
                tool=tool,
                status="failed",
                summary=(
                    f"Tool '{tool.key}' arguments are invalid. Ask the user for missing or "
                    "invalid values instead of retrying with guessed data."
                ),
                output={
                    "error": "invalid_tool_arguments",
                    "validation_errors": validation_errors,
                },
            )

        execution_safety = self._execution_safety_policy.evaluate_tool_call(
            tool=tool,
            arguments=call.arguments,
            active_mode_key=context.active_mode_key,
            context=context.execution.context_bundle,
        )
        if execution_safety.behavior == "deny":
            return self._terminal_record(
                call=call,
                tool=tool,
                status="denied",
                summary=execution_safety.reason,
                output={
                    "error": "execution_safety_denied",
                    "permission_reason": execution_safety.reason,
                    "permission_reason_code": execution_safety.reason_code,
                },
            )

        if not self._tool_registry.has_handler_binding(tool.key):
            return self._terminal_record(
                call=call,
                tool=tool,
                status="failed",
                summary=f"Tool '{tool.key}' is registered but has no runtime handler binding yet.",
                output={"error": "missing_handler_binding"},
            )

        return tool, decision, execution_safety

    async def _create_approval(
        self,
        *,
        tool: ToolDescriptor,
        call: ModelToolCall,
        context: GovernedToolCallContext,
        decision: ToolPermissionDecision,
        execution_safety: Any,
    ) -> GovernedToolCallResult:
        safety_requires_approval = execution_safety.behavior == "ask"
        reason = str(execution_safety.reason or decision.reason)
        permission_source = "execution_safety_policy" if safety_requires_approval else decision.source
        permission_reason_code = (
            execution_safety.reason_code if safety_requires_approval else decision.reason_code
        )
        permission_policy_key = (
            "execution_safety_policy.tool_call" if safety_requires_approval else decision.policy_key
        )
        approval_job_id = None
        if self._tool_job_service is not None:
            job = await self._tool_job_service.create_job(
                tool=tool,
                call_id=call.id,
                session_id=context.execution.session_id,
                turn_id=context.execution.turn_id,
                trace_id=context.execution.trace_id,
                input_payload=call.arguments,
                once_per_call=True,
                metadata={
                    "phase": "approval_pending",
                    "selected_agent_key": context.execution.selected_agent_key,
                    "selected_model_key": context.execution.selected_model_key,
                    "permission_behavior": "ask",
                    "permission_source": permission_source,
                    "permission_reason": reason,
                    "permission_visibility": decision.visibility,
                    "permission_reason_code": permission_reason_code,
                    "permission_policy_key": permission_policy_key,
                },
            )
            approval_job_id = job.id
            if job.status == ToolJobStatus.queued:
                await self._tool_job_service.mark_waiting_approval(job.id, summary=reason)
            elif job.status != ToolJobStatus.waiting_approval:
                raise RuntimeError(
                    f"ToolJob {job.id} is already {job.status.value}; "
                    "a new approval cannot replace persisted execution state."
                )

        approval_mode_key = context.active_mode_key
        if tool.key == "security-tool-bootstrap":
            approval_mode_key = "security_tool_bootstrap"
        approval = self._permission_service.create_approval_request(
            session_id=context.execution.session_id,
            tool=tool,
            reason=reason,
            metadata={
                "turn_id": context.execution.turn_id,
                "call_id": call.id,
                "arguments": call.arguments,
                "selected_agent_key": context.execution.selected_agent_key,
                "selected_model_key": context.execution.selected_model_key,
                "tool_job_id": approval_job_id,
                "permission_behavior": "ask",
                "permission_source": permission_source,
                "permission_reason": reason,
                "permission_visibility": decision.visibility,
                "permission_reason_code": permission_reason_code,
                "permission_policy_key": permission_policy_key,
                "approval_mode_key": approval_mode_key,
                "approval_scope_hash": self._approval_scope_service.build_hash(
                    mode_key=approval_mode_key,
                    tool_key=tool.key,
                    arguments=call.arguments,
                    context=context.execution.context_bundle,
                ),
            },
        )
        record = ToolExecutionRecord(
            call_id=call.id,
            job_id=approval_job_id,
            tool_key=tool.key,
            tool_name=tool.name,
            status="waiting_approval",
            summary=reason,
            trace_id=context.execution.trace_id,
            input=call.arguments,
            output={
                "status": "waiting_approval",
                "approval_id": approval.id,
                "instruction": "Do not retry this tool call while human approval is pending.",
            },
            approval_id=approval.id,
        )
        return GovernedToolCallResult(
            record=record,
            approval=approval.model_dump(mode="python"),
        )

    @staticmethod
    def _terminal_record(
        *,
        call: ModelToolCall,
        status: str,
        summary: str,
        output: dict[str, Any],
        tool: ToolDescriptor | None = None,
    ) -> GovernedToolCallResult:
        return GovernedToolCallResult(
            record=ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key if tool is not None else call.name,
                tool_name=tool.name if tool is not None else call.name,
                status=status,
                summary=summary,
                input=call.arguments,
                output=output,
            )
        )
