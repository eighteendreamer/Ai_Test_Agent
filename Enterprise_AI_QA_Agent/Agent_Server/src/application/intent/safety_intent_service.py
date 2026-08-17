from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

from src.application.security.authorization import verified_grant_matches_target
from src.application.security.prompt_injection_policy import PromptInjectionPolicy
from src.schemas.intent import EffectLevel, IntentDecision, SafetyAssessment


class SafetyIntentService:
    """Extract risk signals; the result never acts as an authorization grant."""

    def assess(
        self,
        message: str,
        intent: IntentDecision,
        context: dict[str, Any] | None = None,
        trusted_context: dict[str, Any] | None = None,
    ) -> SafetyAssessment:
        text = str(message or "").lower()
        context = context or {}
        trusted_context = trusted_context or {}
        effects = self._effects(text, intent)
        environment = str(intent.parameters.get("environment_hint") or "unknown")
        target_url = str(intent.parameters.get("target_url") or "")
        authorization = self._authorization_status(text, effects, target_url, trusted_context)
        target_scope, target_reason = self._target_scope(target_url, authorization)
        content_safety = PromptInjectionPolicy().assess(message, "user")
        injection_signals = content_safety.direct_injection_signals
        risk = self._risk_level(effects, target_scope, environment)
        decision, approvals, restrictions, reasons = self._decision(
            effects=effects,
            environment=environment,
            target_scope=target_scope,
            authorization=authorization,
            injection_signals=injection_signals,
        )
        if target_reason:
            reasons.append(target_reason)

        return SafetyAssessment(
            effect_levels=effects,
            risk_level=risk,
            target_scope_status=target_scope,
            authorization_status=authorization,
            environment=environment,
            data_sensitivity=self._data_sensitivity(text, trusted_context),
            direct_injection_signals=injection_signals,
            decision=decision,
            required_approvals=approvals,
            restrictions=restrictions,
            reason_codes=list(dict.fromkeys(reasons)),
        )

    def _effects(self, text: str, intent: IntentDecision) -> list[EffectLevel]:
        effects: list[EffectLevel] = []
        method = str(intent.parameters.get("method") or "").upper()
        if method in {"GET", "HEAD", "OPTIONS"}:
            effects.append("external_read")
        if method in {"POST", "PUT", "PATCH"} or "write" in intent.requested_actions:
            effects.append("state_change")
        if method == "DELETE" or "delete" in intent.requested_actions:
            effects.append("destructive")
        if "performance" in intent.objectives:
            effects.append("high_load")
        if "security" in intent.objectives and not self._is_report_only(text):
            effects.append("security_probe")
        if any(token in text for token in ("密码", "凭据", "token", "api key", "secret", "密钥")):
            effects.append("credential_access")
        if any(token in text for token in ("执行命令", "shell", "powershell", "cmd.exe", "bash", "运行脚本")):
            effects.append("code_execution")
        if any(token in text for token in ("导出", "下载全部", "export", "exfiltrate")):
            effects.append("data_export")
        if any(token in text for token in ("付款", "支付", "下单", "purchase", "payment")):
            effects.append("financial_action")
        if "send" in intent.requested_actions:
            effects.append("communication_send")
        if not effects:
            effects.append("read_only")
        return list(dict.fromkeys(effects))

    def _target_scope(self, target_url: str, authorization: str) -> tuple[str, str]:
        if not target_url:
            return "unknown", "target_scope_unknown"
        try:
            host = (urlparse(target_url).hostname or "").strip().lower()
            if host in {"metadata.google.internal", "metadata.azure.internal"} or host == "169.254.169.254":
                return "blocked", "cloud_metadata_target_blocked"
            address = ipaddress.ip_address(host)
            if address.is_loopback or address.is_link_local or address.is_private:
                if authorization == "verified":
                    return "in_scope", "target_authorization_verified"
                return "restricted", "private_or_local_target_requires_scope_verification"
        except ValueError:
            pass
        if authorization == "verified":
            return "in_scope", "target_authorization_verified"
        return "unverified", "target_ownership_unverified"

    def _authorization_status(
        self,
        text: str,
        effects: list[EffectLevel],
        target_url: str,
        trusted_context: dict[str, Any],
    ) -> str:
        if "security_probe" not in effects:
            return "not_required"
        grant = trusted_context.get("security_authorization")
        if isinstance(grant, dict) and self._grant_matches_target(grant, target_url):
            return "verified"
        if (
            any(token in text for token in ("未经授权", "没有授权", "未授权"))
            or "not authorized" in text
            or re.search(r"\bunauthori[sz]ed\b", text)
        ):
            return "denied"
        if (
            any(token in text for token in ("已授权", "获得授权", "我自己的系统"))
            or re.search(r"\bauthori[sz]ed\b", text)
        ):
            return "claimed"
        return "unknown"

    def _grant_matches_target(self, grant: dict[str, Any], target_url: str) -> bool:
        return verified_grant_matches_target(grant, target_url)

    def _decision(
        self,
        *,
        effects: list[EffectLevel],
        environment: str,
        target_scope: str,
        authorization: str,
        injection_signals: list[str],
    ) -> tuple[str, list[str], list[str], list[str]]:
        approvals: list[str] = []
        restrictions: list[str] = []
        reasons: list[str] = []
        if injection_signals:
            restrictions.extend(["ignore_untrusted_instructions", "do_not_expand_tool_access"])
            reasons.append("prompt_injection_signals_detected")
        if target_scope == "blocked":
            return "deny", [], [*restrictions, "block_target"], [*reasons, "blocked_target"]
        if "high_load" in effects and environment == "production":
            return "deny", [], [*restrictions, "production_load_test_blocked"], [*reasons, "production_high_load_denied"]
        if "security_probe" in effects and authorization != "verified":
            return (
                "require_authorization",
                ["verified_target_authorization"],
                [*restrictions, "passive_analysis_only", "no_active_security_tools"],
                [*reasons, "security_authorization_not_verified"],
            )
        confirmation_effects = {
            "destructive", "high_load", "credential_access", "code_execution",
            "data_export", "financial_action", "communication_send",
        }
        matched_confirmation = [effect for effect in effects if effect in confirmation_effects]
        if matched_confirmation:
            approvals.extend(f"confirm:{effect}" for effect in matched_confirmation)
            reasons.append("side_effect_confirmation_required")
            return "require_confirmation", approvals, restrictions, reasons
        if "state_change" in effects:
            return "require_confirmation", ["confirm:state_change"], restrictions, ["state_change_confirmation_required"]
        if injection_signals:
            return "allow_with_limits", [], restrictions, reasons
        if target_scope == "restricted":
            return "clarify", [], ["read_only_until_scope_verified"], ["restricted_target_scope_unverified"]
        return "allow", [], [], ["low_risk_request"]

    def _risk_level(self, effects: list[EffectLevel], target_scope: str, environment: str) -> str:
        if target_scope == "blocked" or ("high_load" in effects and environment == "production"):
            return "critical"
        if any(effect in effects for effect in ("destructive", "security_probe", "credential_access", "financial_action")):
            return "high"
        if any(effect in effects for effect in ("high_load", "state_change", "code_execution", "data_export", "communication_send")):
            return "medium"
        return "low"

    def _data_sensitivity(self, text: str, trusted_context: dict[str, Any]) -> str:
        explicit = str(trusted_context.get("data_sensitivity") or "").strip().lower()
        if explicit in {"public", "internal", "confidential", "restricted"}:
            return explicit
        if any(token in text for token in ("身份证", "银行卡", "password", "secret", "密钥", "个人信息")):
            return "restricted"
        return "internal"

    def _is_report_only(self, text: str) -> bool:
        read_tokens = ("报告", "结果", "分析", "查看", "总结", "report", "findings")
        active_tokens = ("扫描", "探测", "攻击", "利用", "scan", "exploit", "probe")
        return any(token in text for token in read_tokens) and not any(token in text for token in active_tokens)
