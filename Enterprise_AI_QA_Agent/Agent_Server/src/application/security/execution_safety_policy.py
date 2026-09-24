from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from src.application.security.command_profiles import get_profile_registry
from src.application.security.risk_policy import SecurityRiskPolicy
from src.schemas.agent import ToolDescriptor
from src.schemas.intent import ToolSafetyDecision


class ExecutionSafetyPolicy:
    """Re-evaluate concrete tool arguments immediately before execution."""

    TARGET_KEYS = {"target", "target_url", "url", "endpoint", "base_url", "host"}
    HIGH_LOAD_CAPABILITIES = {"performance.load_test"}
    EXECUTION_CATEGORIES = {
        "execution",
        "automation",
        "security",
        "orchestration",
        "communication",
        "review",
    }

    def __init__(self) -> None:
        self._security_profiles = get_profile_registry()
        self._security_risk_policy = SecurityRiskPolicy()

    def evaluate_tool_call(
        self,
        *,
        tool: ToolDescriptor,
        arguments: dict[str, Any],
        active_mode_key: str,
        context: dict[str, Any] | None = None,
    ) -> ToolSafetyDecision:
        context = context or {}
        if tool.exposure == "internal" and tool.owner_mode_key != active_mode_key:
            return ToolSafetyDecision(
                behavior="deny",
                reason=f"Internal tool '{tool.key}' is not available outside mode '{tool.owner_mode_key}'.",
                reason_code="cross_mode_internal_tool_denied",
            )
        blocked_target, target_reason_code = self._blocked_target(arguments, context)
        if blocked_target:
            return ToolSafetyDecision(
                behavior="deny",
                reason=f"Tool target '{blocked_target}' is blocked by the target safety policy.",
                reason_code=target_reason_code,
            )
        safety = context.get("safety_assessment") if isinstance(context.get("safety_assessment"), dict) else {}
        safety_decision = str(safety.get("decision") or "allow")
        if safety_decision == "deny" and tool.category in self.EXECUTION_CATEGORIES:
            return ToolSafetyDecision(
                behavior="deny",
                reason="The turn-level safety assessment denies execution tools for this request.",
                reason_code="turn_safety_denied",
            )
        if safety_decision == "require_authorization" and (
            "security.assessment" in tool.capability_keys or tool.owner_mode_key == "security_testing"
        ):
            return ToolSafetyDecision(
                behavior="deny",
                reason="Verified target authorization is required before active security tooling can run.",
                reason_code="security_authorization_required",
            )
        verified_low_risk_security_profile = False
        if active_mode_key == "security_testing" and tool.owner_mode_key == "security_testing":
            if tool.key == "security-tool-bootstrap":
                return ToolSafetyDecision(
                    behavior="ask",
                    reason=(
                        "P4 temporary security-tool readiness requires a dedicated approval "
                        "for its exact campaign, package, image, repository and target scope."
                    ),
                    reason_code="security_tool_bootstrap_approval_required",
                )
            task = arguments.get("task")
            task_data = task if isinstance(task, dict) else {}
            profile_key = str(
                arguments.get("command_profile")
                or task_data.get("command_profile")
                or ""
            ).strip()
            profile = self._security_profiles.get(profile_key)
            requires_profile_approval = (
                profile is None
                or self._security_risk_policy.requires_approval(profile_key)
                or profile.risk_level in {"high", "critical"}
            )
            if bool(task_data.get("requires_approval")) or requires_profile_approval:
                return ToolSafetyDecision(
                    behavior="ask",
                    reason=(
                        "The registered security command profile is high risk, unknown, "
                        "or explicitly requires approval."
                    ),
                    reason_code="security_task_risk_approval_required",
                )
            verified_low_risk_security_profile = safety.get("authorization_status") == "verified"
        environments = {
            str(value).strip().lower()
            for value in (
                context.get("trusted_environment"),
                context.get("environment"),
                safety.get("environment"),
                arguments.get("environment"),
            )
            if str(value or "").strip()
        }
        if environments.intersection({"production", "prod"}) and self.HIGH_LOAD_CAPABILITIES.intersection(tool.capability_keys):
            return ToolSafetyDecision(
                behavior="deny",
                reason="Performance load execution is blocked in production by default.",
                reason_code="production_high_load_denied",
            )
        if (
            safety_decision == "require_confirmation"
            and tool.category in self.EXECUTION_CATEGORIES
            and not verified_low_risk_security_profile
        ):
            return ToolSafetyDecision(
                behavior="ask",
                reason="This request has side effects and requires approval for the concrete tool arguments.",
                reason_code="turn_confirmation_required",
            )
        if tool.exposure == "workflow_entry" and tool.owner_mode_key != active_mode_key:
            return ToolSafetyDecision(
                behavior="ask",
                reason=f"Cross-mode workflow '{tool.owner_mode_key}' requires explicit approval.",
                reason_code="cross_mode_workflow_approval_required",
            )
        return ToolSafetyDecision()

    def _blocked_target(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str]:
        trusted_hosts = self._trusted_target_hosts(context)
        for key, raw_value in arguments.items():
            if key not in self.TARGET_KEYS or not isinstance(raw_value, str):
                continue
            host = (urlparse(raw_value).hostname or raw_value.split(":", 1)[0]).strip("[]").lower()
            if host in {"169.254.169.254", "metadata.google.internal", "metadata.azure.internal"}:
                return raw_value, "blocked_network_target"
            if host == "localhost" or host.endswith(".localhost"):
                if host not in trusted_hosts:
                    return raw_value, "untrusted_private_target"
                continue
            try:
                network = ipaddress.ip_network(host, strict=False)
            except ValueError:
                continue
            if (
                network.is_loopback
                or network.is_link_local
                or network.is_private
                or network.is_reserved
            ) and host not in trusted_hosts:
                return raw_value, "untrusted_private_target"
        return "", ""

    def _trusted_target_hosts(self, context: dict[str, Any]) -> set[str]:
        candidates = list(context.get("trusted_target_hosts") or [])
        resource_scope = context.get("trusted_resource_scope")
        if isinstance(resource_scope, dict):
            candidates.append(resource_scope.get("project_url"))
            candidates.extend(resource_scope.get("allowed_targets") or [])
        safety = context.get("safety_assessment")
        intent = context.get("intent_decision")
        if (
            isinstance(safety, dict)
            and safety.get("authorization_status") == "verified"
            and isinstance(intent, dict)
        ):
            parameters = intent.get("parameters")
            if isinstance(parameters, dict):
                candidates.append(parameters.get("target_url"))

        hosts: set[str] = set()
        for value in candidates:
            text = str(value or "").strip()
            if not text:
                continue
            host = (urlparse(text).hostname or text.split(":", 1)[0]).strip("[]").lower()
            if host:
                hosts.add(host)
        return hosts
