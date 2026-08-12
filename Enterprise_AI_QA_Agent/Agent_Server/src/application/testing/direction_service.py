from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QATaskState:
    is_test_task: bool
    direction: str
    confidence: float
    needs_direction_selection: bool
    reasons: list[str] = field(default_factory=list)
    recommended_skills: list[str] = field(default_factory=list)


class QATaskDirectionService:
    """Classify incoming work into QA test directions."""

    UI_TOKENS = {
        "ui",
        "界面",
        "页面",
        "浏览器",
        "前端",
        "playwright",
        "selenium",
        "点击",
        "截图",
        "探索",
    }
    API_TOKENS = {"api", "接口", "请求", "响应", "http", "payload", "contract"}
    SECURITY_TOKENS = {"security", "安全", "漏洞", "越权", "xss", "csrf", "sql注入"}
    PERFORMANCE_TOKENS = {"performance", "性能", "压测", "压力", "并发", "latency", "load"}
    TEST_TOKENS = {"test", "测试", "验证", "自动化", "qa", "用例", "检查", "回归"}

    def classify(self, message: str, context: dict | None = None) -> QATaskState:
        text = f"{message or ''} {context or ''}".lower()
        reasons: list[str] = []
        is_test_task = any(token in text for token in self.TEST_TOKENS | self.UI_TOKENS | self.API_TOKENS)
        scores = {
            "ui": self._score(text, self.UI_TOKENS),
            "api": self._score(text, self.API_TOKENS),
            "security": self._score(text, self.SECURITY_TOKENS),
            "performance": self._score(text, self.PERFORMANCE_TOKENS),
        }
        best_direction = max(scores, key=scores.get)
        best_score = scores[best_direction]
        matched_directions = [key for key, value in scores.items() if value > 0]

        if not is_test_task:
            return QATaskState(
                is_test_task=False,
                direction="none",
                confidence=0.0,
                needs_direction_selection=False,
                reasons=["No explicit QA/testing intent was detected."],
            )

        if len(matched_directions) > 1 and best_score == sorted(scores.values(), reverse=True)[1]:
            return QATaskState(
                is_test_task=True,
                direction="mixed",
                confidence=0.55,
                needs_direction_selection=False,
                reasons=["Multiple test directions were detected with similar strength."],
            )

        if best_score == 0:
            return QATaskState(
                is_test_task=True,
                direction="unknown",
                confidence=0.35,
                needs_direction_selection=True,
                reasons=["Testing intent is present but the direction is not clear."],
            )

        reasons.append(f"Detected {best_direction} test direction from user input.")
        skills = ["playwright-e2e-testing", "playwright-cli"] if best_direction == "ui" else []
        return QATaskState(
            is_test_task=True,
            direction=best_direction,
            confidence=min(0.95, 0.55 + best_score * 0.15),
            needs_direction_selection=False,
            reasons=reasons,
            recommended_skills=skills,
        )

    def _score(self, text: str, tokens: set[str]) -> int:
        return sum(1 for token in tokens if token in text)
