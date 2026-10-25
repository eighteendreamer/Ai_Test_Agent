from __future__ import annotations

from dataclasses import dataclass, field, replace
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
from src.schemas.framework_approval import (
    FrameworkInterruptLink, NativeToolInterrupt, PreparedFrameworkApproval,
    framework_approval_id, framework_tool_call_id,
)


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
        context = replace(context, execution=context.execution.bind_call(call))
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
                    metadata=context.execution.job_metadata(),
                )
            if (
                job.session_id != context.execution.session_id
                or job.turn_id != context.execution.turn_id
                or job.call_id != call.id or job.tool_key != call.name
                or job.input_payload != original_arguments
                or (context.execution.case_action_binding is not None
                    and job.metadata.get("case_action_binding") != context.execution.case_action_binding.model_dump(mode="json"))
            ):
                raise RuntimeError("ToolJob identity or arguments changed during checkpoint recovery.")
            context.execution.tool_job_id = job.id
            claimed = await self._tool_job_service.claim_execution(job.id, context_bundle=context.execution.context_bundle)
            if claimed is not None and claimed.status in {
                ToolJobStatus.completed, ToolJobStatus.partial, ToolJobStatus.failed,
                ToolJobStatus.denied, ToolJobStatus.cancelled,
            }:
                return GovernedToolCallResult(record=await self._tool_runtime_service.record_from_job(claimed),
                    replayed=claimed.status != ToolJobStatus.denied)
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
        execution_context = replace(
            context.execution,
            claimed_job=claimed if self._tool_job_service is not None else None,
            server_approval_granted=approval is not None,
            approval_scope_hash=(
                str(approval.get("metadata", {}).get("approval_scope_hash") or "")
                if approval is not None and tool.key == "security-tool-bootstrap"
                else ""
            ),
        )
        return GovernedToolCallResult(
            record=await self._tool_runtime_service.execute(
                tool=tool, call=call, context=execution_context,
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
            context.execution.bind_call(call)
        except ValueError:
            frozen = context.execution.context_bundle.get("case_execution")
            operation_note = (" 新逻辑操作协议还要求稳定的_case_action.operation_key。"
                if isinstance(frozen,dict) and frozen.get("operation_protocol") else "")
            return self._terminal_record(call=call, status="failed",
                summary="工具调用必须用_case_action.item_key选择本条目所属的固定执行项。" + operation_note,
                output={"error": "invalid_case_action_binding"})
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
        reason, permission_source, permission_reason_code, permission_policy_key = self._approval_policy(
            decision, execution_safety)
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
                    **context.execution.job_metadata(),
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

        return self._build_approval_result(tool=tool, call=call, context=context,
            decision=decision, execution_safety=execution_safety, approval_job_id=approval_job_id)

    @staticmethod
    def _approval_policy(decision, execution_safety):
        safety_requires_approval = execution_safety.behavior == "ask"
        return (str(execution_safety.reason or decision.reason),
            "execution_safety_policy" if safety_requires_approval else decision.source,
            execution_safety.reason_code if safety_requires_approval else decision.reason_code,
            "execution_safety_policy.tool_call" if safety_requires_approval else decision.policy_key)

    def _build_approval_result(self, *, tool, call, context, decision, execution_safety, approval_job_id):
        """Construct the common approval payload without publishing business rows."""
        reason, permission_source, permission_reason_code, permission_policy_key = self._approval_policy(
            decision, execution_safety)
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
                **({"case_action_binding": context.execution.case_action_binding.model_dump(mode="json")}
                    if context.execution.case_action_binding is not None else {}),
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

    def prepare_framework_approval(self, binding, native, context) -> PreparedFrameworkApproval:
        """Apply existing policy, but defer all business writes to one projection."""
        from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
        from src.schemas.session import ToolApprovalRequest

        native = NativeToolInterrupt.model_validate(native.model_dump(mode="json"))
        if (context.execution.session_id != binding.session_id or context.execution.turn_id != binding.turn_id
            or self._tool_job_service is None):
            raise FrameworkApprovalRecoveryRequired("Native approval preparation has no matching business runtime")
        call = ModelToolCall(id=framework_tool_call_id(binding, native), name=native.tool_name,
            arguments=native.arguments)
        assessment = self._assess(call, context)
        if isinstance(assessment, GovernedToolCallResult):
            raise FrameworkApprovalRecoveryRequired("Native approval cannot be prepared under current governance")
        tool, decision, execution_safety = assessment
        if decision.behavior != "ask" and execution_safety.behavior != "ask":
            raise FrameworkApprovalRecoveryRequired("Native approval gate changed before business projection")
        context = replace(context, execution=context.execution.bind_call(call))
        reason, source, reason_code, policy_key = self._approval_policy(decision, execution_safety)
        job = self._tool_job_service.build_job(tool=tool, call_id=call.id,
            session_id=binding.session_id, turn_id=binding.turn_id, trace_id=context.execution.trace_id,
            input_payload=call.arguments, once_per_call=True, metadata={
                **context.execution.job_metadata(), "phase": "approval_pending",
                "selected_agent_key": context.execution.selected_agent_key,
                "selected_model_key": context.execution.selected_model_key,
                "permission_behavior": "ask", "permission_reason": reason, "permission_source": source,
                "permission_visibility": decision.visibility, "permission_reason_code": reason_code,
                "permission_policy_key": policy_key, "framework_binding_id": binding.binding_id,
                "framework_native_identity": native.model_dump(mode="json")})
        job.status, job.summary = ToolJobStatus.waiting_approval, reason
        built = self._build_approval_result(tool=tool, call=call, context=context,
            decision=decision, execution_safety=execution_safety, approval_job_id=job.id)
        approval = ToolApprovalRequest.model_validate(built.approval)
        approval.id = framework_approval_id(binding, native)
        approval.metadata.update({"call_id": native.tool_call_id, "framework_call_id": call.id,
            "framework_binding_id": binding.binding_id, "framework_native_identity": native.model_dump(mode="json"),
            "deep_agent_interrupt_id": native.interrupt_id, "deep_agent_action_index": 0,
            "deep_agent_interrupt_mode": "per_call"})
        link = FrameworkInterruptLink(**native.model_dump(mode="json"), approval_id=approval.id,
            binding_id=binding.binding_id, tool_key=tool.key, tool_job_id=job.id,
            scope_hash=approval.metadata["approval_scope_hash"], tool_schema_version=binding.tool_schema_version,
            business_call_id=call.id)
        return PreparedFrameworkApproval(approval=approval, job=job, link=link)

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
