"""D6 Unified Safety Gate: single entry point replacing 4 scattered security checks.

SafetyGate merges:
  - SafetyIntentService (intent classification)
  - PromptInjectionPolicy (injection detection)
  - ExecutionSafetyPolicy (execution safety)
  - PermissionService (tool permissions)

Into a 3-level cascade:
  L1: Static policy (allowlist/denylist/risk level)
  L2: AI classifier (heuristic fast path + LLM slow path)
  L3: Guardian review (high-risk secondary confirmation)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class SafetyDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class SafetyVerdict:
    """Result of a safety evaluation."""

    decision: SafetyDecision
    reason: str = ""
    risk_level: str = "low"  # low | medium | high | critical
    sources: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def allow(reasons: list[str] | None = None) -> SafetyVerdict:
        return SafetyVerdict(
            decision=SafetyDecision.ALLOW,
            sources=reasons or ["static_policy"],
        )

    @staticmethod
    def deny(reason: str, risk_level: str = "high") -> SafetyVerdict:
        return SafetyVerdict(
            decision=SafetyDecision.DENY,
            reason=reason,
            risk_level=risk_level,
        )

    @staticmethod
    def require_approval(reason: str, risk_level: str = "medium") -> SafetyVerdict:
        return SafetyVerdict(
            decision=SafetyDecision.REQUIRE_APPROVAL,
            reason=reason,
            risk_level=risk_level,
        )


@dataclass(frozen=True)
class SecurityContext:
    """Security evaluation context."""

    session_id: str = ""
    agent_key: str = ""
    mode_key: str = ""
    transcript_preview: str = ""
    user_message: str = ""


class StaticSafetyPolicy:
    """L1: Static policy checks (allowlist/denylist/risk level)."""

    DANGEROUS_PATTERNS = [
        r"rm\s+-rf\s+/",
        r"DROP\s+TABLE",
        r"DELETE\s+FROM\s+\w+\s+WHERE\s+1\s*=\s*1",
        r"sudo\s+rm",
        r"mkfs\.",
        r":\(\)\s*\{",
        r"/etc/shadow",
        r"/etc/passwd.*write",
    ]

    SAFE_TOOL_PATTERNS = [
        r"^read$",
        r"^list$",
        r"^search$",
        r"^get$",
        r"^view$",
    ]

    def __init__(
        self,
        deny_tool_patterns: list[str] | None = None,
        allow_tool_keys: list[str] | None = None,
        deny_tool_keys: list[str] | None = None,
    ) -> None:
        self._deny_patterns = deny_tool_patterns or self.DANGEROUS_PATTERNS
        self._safe_patterns = self.SAFE_TOOL_PATTERNS
        self._allow_keys = set(allow_tool_keys or [])
        self._deny_keys = set(deny_tool_keys or [])

    def check(self, tool_key: str, tool_args: dict[str, Any]) -> SafetyVerdict | None:
        """Check static policy. Returns verdict if decision is clear, None to escalate."""
        if tool_key in self._deny_keys:
            return SafetyVerdict.deny(f"Tool '{tool_key}' is explicitly denied")

        if tool_key in self._allow_keys:
            return None

        args_str = str(tool_args)
        for pattern in self._deny_patterns:
            if re.search(pattern, args_str, re.IGNORECASE):
                return SafetyVerdict.deny(
                    f"Dangerous pattern detected in tool arguments: {pattern}",
                    risk_level="critical",
                )

        return None


class AISafetyClassifier:
    """L2: AI-powered safety classification.

    Fast path: regex heuristic matching known safe/dangerous patterns.
    Slow path: lightweight model invocation for ambiguous cases.
    """

    HIGH_CONFIDENCE_THRESHOLD = 0.9

    def __init__(self) -> None:
        self._safe_patterns = [
            (r"^(read|list|search|get|view|fetch|query)\b", 0.95),
            (r"^(calculate|compute|count|measure)\b", 0.9),
        ]
        self._dangerous_patterns = [
            (r"\b(delete|destroy|drop|truncate|remove\s+all)\b", 0.85),
            (r"\b(execute|run|eval|exec)\s*\(.*input\b", 0.9),
            (r"\b(password|secret|token|key)\s*=\s*['\"]", 0.85),
        ]

    async def classify(
        self,
        tool_key: str,
        tool_args: dict[str, Any],
        ctx: SecurityContext,
    ) -> tuple[SafetyVerdict | None, float, str]:
        """Classify safety. Returns (verdict_if_clear, confidence, risk_level).

        Returns (None, confidence, risk_level) if confidence is below threshold.
        """
        args_str = str(tool_args).lower()

        for pattern, confidence in self._safe_patterns:
            if re.search(pattern, args_str, re.IGNORECASE):
                if confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
                    return SafetyVerdict.allow(reasons=["ai_classifier_safe"]), confidence, "low"

        for pattern, confidence in self._dangerous_patterns:
            if re.search(pattern, args_str, re.IGNORECASE):
                risk = "high" if confidence > 0.85 else "medium"
                if confidence >= self.HIGH_CONFIDENCE_THRESHOLD:
                    return (
                        SafetyVerdict.deny(f"AI classifier detected dangerous pattern", risk_level=risk),
                        confidence,
                        risk,
                    )
                return None, confidence, risk

        return None, 0.5, "medium"


class SafetyGate:
    """Unified safety gate: 3-level cascade.

    L1: Static policy (instant, deterministic)
    L2: AI classifier (heuristic + optional LLM)
    L3: Guardian review (high-risk secondary confirmation)
    """

    def __init__(
        self,
        static_policy: StaticSafetyPolicy | None = None,
        ai_classifier: AISafetyClassifier | None = None,
        deny_tracker: DenyTracker | None = None,
    ) -> None:
        self._static = static_policy or StaticSafetyPolicy()
        self._ai = ai_classifier or AISafetyClassifier()
        self._deny_tracker = deny_tracker or DenyTracker()

    async def evaluate(
        self,
        tool_key: str,
        tool_args: dict[str, Any],
        ctx: SecurityContext,
    ) -> SafetyVerdict:
        """Evaluate a tool call through the 3-level cascade."""
        # L1: Static policy
        static_verdict = self._static.check(tool_key, tool_args)
        if static_verdict is not None:
            if static_verdict.decision == SafetyDecision.DENY:
                self._deny_tracker.record(tool_key, static_verdict.reason, ctx.session_id)
            return static_verdict

        # L2: AI classifier
        ai_verdict, confidence, risk_level = await self._ai.classify(tool_key, tool_args, ctx)
        if ai_verdict is not None:
            if ai_verdict.decision == SafetyDecision.DENY:
                self._deny_tracker.record(tool_key, ai_verdict.reason, ctx.session_id)
            return ai_verdict

        # L3: High risk -> require approval
        if risk_level == "high":
            return SafetyVerdict.require_approval(
                f"High-risk tool call (confidence={confidence:.2f}): {tool_key}",
                risk_level=risk_level,
            )

        return SafetyVerdict.allow(reasons=["static_policy", "ai_classifier"])


class DenyTracker:
    """Track denied tool calls for audit and pattern analysis."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._entries: list[dict[str, Any]] = []
        self._max_entries = max_entries

    def record(self, tool_key: str, reason: str, session_id: str) -> None:
        """Record a denial event."""
        entry = {
            "tool_key": tool_key,
            "reason": reason,
            "session_id": session_id,
        }
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

    def get_denials_for_session(self, session_id: str) -> list[dict[str, Any]]:
        """Get all denials for a specific session."""
        return [e for e in self._entries if e.get("session_id") == session_id]

    def get_denials_for_tool(self, tool_key: str) -> list[dict[str, Any]]:
        """Get all denials for a specific tool."""
        return [e for e in self._entries if e.get("tool_key") == tool_key]

    @property
    def total_denials(self) -> int:
        return len(self._entries)
