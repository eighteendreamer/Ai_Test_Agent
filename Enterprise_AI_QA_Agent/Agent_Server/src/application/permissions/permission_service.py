from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4

from src.schemas.agent import ToolDescriptor
from src.schemas.session import MessageKind, RuntimeMode, SessionMode, ToolApprovalRequest


PermissionBehavior = Literal["allow", "ask", "deny"]
ToolVisibility = Literal["visible", "hidden"]


@dataclass(frozen=True)
class PermissionPolicyContext:
    session_mode: SessionMode
    runtime_mode: RuntimeMode
    selected_agent_key: str
    message_kind: MessageKind
    submit_mode: str
    execution_lane: str
    source: str = "session.send_message"
    active_mode_key: str = "default"
    workflow_mode_key: str = "default"
    safety_decision: str = "allow"
    safety_risk_level: str = "low"
    authorization_status: str = "not_required"
    environment: str = "unknown"


@dataclass
class ToolPermissionDecision:
    tool_key: str
    tool_name: str
    behavior: PermissionBehavior
    visibility: ToolVisibility
    reason: str
    reason_code: str
    source: str
    policy_key: str
    permission_level: str
    category: str

    def to_payload(self) -> dict[str, str]:
        return {
            "tool_key": self.tool_key,
            "tool_name": self.tool_name,
            "behavior": self.behavior,
            "visibility": self.visibility,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "source": self.source,
            "policy_key": self.policy_key,
            "permission_level": self.permission_level,
            "category": self.category,
        }


@dataclass
class PermissionEvaluation:
    allowed_tool_keys: list[str]
    approval_required_tool_keys: list[str]
    denied_tool_keys: list[str]
    model_visible_tool_keys: list[str]
    hidden_tool_keys: list[str]
    decisions: list[ToolPermissionDecision]


class PermissionService:
    SIDE_EFFECT_CATEGORIES = {
        "execution",
        "automation",
        "security",
        "orchestration",
        "communication",
        "review",
    }

    def evaluate(
        self,
        policy_context: PermissionPolicyContext,
        tools: list[ToolDescriptor],
    ) -> PermissionEvaluation:
        allowed: list[str] = []
        approval_required: list[str] = []
        denied: list[str] = []
        model_visible: list[str] = []
        hidden: list[str] = []
        decisions: list[ToolPermissionDecision] = []

        for tool in tools:
            decision = self._decide_tool_behavior(policy_context=policy_context, tool=tool)
            decisions.append(decision)
            if decision.behavior == "allow":
                allowed.append(tool.key)
            elif decision.behavior == "deny":
                denied.append(tool.key)
            else:
                approval_required.append(tool.key)

            if decision.visibility == "visible":
                model_visible.append(tool.key)
            else:
                hidden.append(tool.key)

        return PermissionEvaluation(
            allowed_tool_keys=allowed,
            approval_required_tool_keys=approval_required,
            denied_tool_keys=denied,
            model_visible_tool_keys=model_visible,
            hidden_tool_keys=hidden,
            decisions=decisions,
        )

    def create_approval_request(
        self,
        session_id: str,
        tool: ToolDescriptor,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> ToolApprovalRequest:
        metadata = dict(metadata or {})
        return ToolApprovalRequest(
            id=str(uuid4()),
            session_id=session_id,
            tool_key=tool.key,
            tool_name=tool.name,
            reason=reason,
            created_at=datetime.utcnow(),
            metadata={
                "permission_level": tool.permission_level,
                "category": tool.category,
                "permission_behavior": metadata.get("permission_behavior", "ask"),
                "permission_source": metadata.get("permission_source", "policy_engine"),
                "permission_reason": metadata.get("permission_reason", reason),
                "permission_visibility": metadata.get("permission_visibility", "visible"),
                "permission_reason_code": metadata.get("permission_reason_code", "approval_required_default"),
                "permission_policy_key": metadata.get("permission_policy_key", "permission_level.ask"),
                **metadata,
            },
        )

    def get_tool_decision(
        self,
        evaluation: PermissionEvaluation,
        tool_key: str,
    ) -> ToolPermissionDecision | None:
        for item in evaluation.decisions:
            if item.tool_key == tool_key:
                return item
        return None

    def _decide_tool_behavior(
        self,
        policy_context: PermissionPolicyContext,
        tool: ToolDescriptor,
    ) -> ToolPermissionDecision:
        if tool.exposure == "internal" and tool.owner_mode_key != policy_context.active_mode_key:
            return self._decision(
                tool=tool,
                behavior="deny",
                visibility="hidden",
                reason=f"Internal tool '{tool.name}' belongs to mode '{tool.owner_mode_key}'.",
                reason_code="cross_mode_internal_tool_denied",
                policy_key="tool.exposure.internal",
            )

        if policy_context.safety_decision == "deny" and tool.category in self.SIDE_EFFECT_CATEGORIES:
            return self._decision(
                tool=tool,
                behavior="deny",
                visibility="hidden",
                reason=f"Tool '{tool.name}' is blocked by the turn-level safety assessment.",
                reason_code="turn_safety_denied",
                policy_key="turn.safety.decision",
            )

        if (
            policy_context.authorization_status != "verified"
            and ("security.assessment" in tool.capability_keys or tool.owner_mode_key == "security_testing")
        ):
            return self._decision(
                tool=tool,
                behavior="deny",
                visibility="hidden",
                reason="Verified target authorization is required before active security tooling can run.",
                reason_code="security_authorization_required",
                policy_key="turn.safety.authorization",
            )

        if (
            tool.exposure == "workflow_entry"
            and tool.owner_mode_key != policy_context.active_mode_key
        ):
            return self._decision(
                tool=tool,
                behavior="ask",
                visibility="visible",
                reason=f"Cross-mode workflow '{tool.owner_mode_key}' requires explicit approval.",
                reason_code="cross_mode_workflow_approval_required",
                policy_key="tool.exposure.workflow_entry",
            )

        if (
            policy_context.active_mode_key == "security_testing"
            and tool.owner_mode_key == "security_testing"
            and policy_context.authorization_status == "verified"
            and policy_context.safety_decision in {"allow", "allow_with_limits", "require_confirmation"}
            and tool.permission_level == "ask"
        ):
            return self._decision(
                tool=tool,
                behavior="allow",
                visibility="visible",
                reason=(
                    "Verified security-mode runner is visible; concrete task risk "
                    "is re-evaluated immediately before execution."
                ),
                reason_code="verified_security_runner_deferred_risk_gate",
                policy_key="security_testing.verified_runner",
            )

        if (
            policy_context.safety_decision == "require_confirmation"
            and tool.category in self.SIDE_EFFECT_CATEGORIES
        ):
            return self._decision(
                tool=tool,
                behavior="ask",
                visibility="visible",
                reason=f"Tool '{tool.name}' requires confirmation for this request's side effects.",
                reason_code="turn_confirmation_required",
                policy_key="turn.safety.confirmation",
            )

        if not tool.enabled_by_default:
            return self._decision(
                tool=tool,
                behavior="deny",
                visibility="hidden",
                reason=(
                    f"Tool '{tool.name}' is disabled in the registry and cannot be exposed "
                    "to the runtime."
                ),
                reason_code="tool_disabled",
                policy_key="registry.enabled_by_default",
            )

        if (
            policy_context.session_mode == SessionMode.assistant_viewer
            and tool.category not in {"system", "knowledge", "reporting"}
        ):
            return self._decision(
                tool=tool,
                behavior="deny",
                visibility="hidden",
                reason=(
                    f"Tool '{tool.name}' is hidden in assistant viewer mode because "
                    "that mode is read-only."
                ),
                reason_code="viewer_readonly_mode",
                policy_key="session_mode.assistant_viewer.readonly",
            )

        if tool.permission_level == "restricted":
            return self._decision(
                tool=tool,
                behavior="deny",
                visibility="hidden",
                reason=(
                    f"Tool '{tool.name}' is restricted and is denied by default "
                    f"in {policy_context.session_mode.value} mode."
                ),
                reason_code="restricted_default_deny",
                policy_key="permission_level.restricted",
            )

        if tool.permission_level == "safe":
            return self._decision(
                tool=tool,
                behavior="allow",
                visibility="visible",
                reason=f"Tool '{tool.name}' is marked safe and can run without approval.",
                reason_code="safe_default_allow",
                policy_key="permission_level.safe",
            )

        return self._decision(
            tool=tool,
            behavior="ask",
            visibility="visible",
            reason=(
                f"Tool '{tool.name}' requires explicit approval before execution "
                f"in {policy_context.session_mode.value} mode."
            ),
            reason_code="approval_required_default",
            policy_key="permission_level.ask",
        )

    def _decision(
        self,
        tool: ToolDescriptor,
        behavior: PermissionBehavior,
        visibility: ToolVisibility,
        reason: str,
        reason_code: str,
        policy_key: str,
    ) -> ToolPermissionDecision:
        return ToolPermissionDecision(
            tool_key=tool.key,
            tool_name=tool.name,
            behavior=behavior,
            visibility=visibility,
            reason=reason,
            reason_code=reason_code,
            source="policy_engine",
            policy_key=policy_key,
            permission_level=tool.permission_level,
            category=tool.category,
        )
