from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class GuardianVerdict:
    risk_level: Literal["low", "medium", "high", "critical"]
    authorized: bool
    rationale: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def authorized_low(rationale: str) -> GuardianVerdict:
        return GuardianVerdict(risk_level="low", authorized=True, rationale=rationale)

    @staticmethod
    def denied_high(rationale: str) -> GuardianVerdict:
        return GuardianVerdict(risk_level="high", authorized=False, rationale=rationale)


class CircuitBreakerTripped(Exception):
    def __init__(self, message: str, denial_count: int) -> None:
        super().__init__(message)
        self.denial_count = denial_count


class GuardianReviewer:
    def __init__(
        self,
        max_consecutive_denials: int = 3,
        review_model: Any = None,
    ) -> None:
        self._max_consecutive_denials = max_consecutive_denials
        self._review_model = review_model
        self._consecutive_denials: int = 0
        self._total_reviews: int = 0

    @property
    def consecutive_denials(self) -> int:
        return self._consecutive_denials

    @property
    def total_reviews(self) -> int:
        return self._total_reviews

    async def review(
        self,
        action: dict[str, Any],
        transcript: list[dict[str, Any]] | None = None,
    ) -> GuardianVerdict:
        self._total_reviews += 1

        compact = self._build_compact_transcript(transcript or [], action)
        assessment = await self._assess(compact)

        if assessment["risk_level"] in ("high", "critical"):
            self._consecutive_denials += 1
            if self._consecutive_denials >= self._max_consecutive_denials:
                raise CircuitBreakerTripped(
                    f"Guardian 连续拒绝 {self._consecutive_denials} 次，中断 turn",
                    denial_count=self._consecutive_denials,
                )
            return GuardianVerdict(
                risk_level=assessment["risk_level"],
                authorized=False,
                rationale=assessment.get("rationale", "高风险操作被 Guardian 拒绝"),
                metadata={"assessment": assessment},
            )

        self._consecutive_denials = 0
        return GuardianVerdict(
            risk_level=assessment["risk_level"],
            authorized=True,
            rationale=assessment.get("rationale", "Guardian 评审通过"),
            metadata={"assessment": assessment},
        )

    def reset(self) -> None:
        self._consecutive_denials = 0

    def _build_compact_transcript(
        self,
        transcript: list[dict[str, Any]],
        action: dict[str, Any],
    ) -> dict[str, Any]:
        tail = transcript[-10:] if len(transcript) > 10 else transcript
        return {
            "recent_messages": [
                {"role": m.get("role", ""), "content": str(m.get("content", ""))[:200]}
                for m in tail
            ],
            "action": action,
        }

    async def _assess(self, compact: dict[str, Any]) -> dict[str, Any]:
        action = compact.get("action", {})
        tool_name = action.get("tool_name", "")
        args_str = str(action.get("arguments", ""))

        if any(p in args_str for p in ("rm -rf", "DROP TABLE", "DELETE FROM", "sudo ")):
            return {"risk_level": "critical", "rationale": f"检测到危险操作模式: {tool_name}"}

        if any(p in args_str for p in ("rm ", "DROP ", "DELETE ", "chmod ", "chown ")):
            return {"risk_level": "high", "rationale": f"检测到潜在破坏性操作: {tool_name}"}

        if any(p in tool_name for p in ("shell", "bash", "exec", "terminal", "cli")):
            return {"risk_level": "medium", "rationale": f"命令执行类工具: {tool_name}"}

        return {"risk_level": "low", "rationale": "操作风险评估为低"}
