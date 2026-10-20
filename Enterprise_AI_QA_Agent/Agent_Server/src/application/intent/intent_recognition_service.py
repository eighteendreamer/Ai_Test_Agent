from __future__ import annotations

import re
from typing import Any

from src.application.intent.url_text import HTTP_URL_PATTERN
from src.schemas.intent import IntentDecision


class IntentRecognitionService:
    """Extract a stable task frame without granting execution authority."""

    URL_PATTERN = HTTP_URL_PATTERN

    MODE_TOKENS: dict[str, set[str]] = {
        "security_testing": {"security", "安全", "漏洞", "渗透", "越权", "xss", "csrf", "sql注入", "扫描"},
        "performance_testing": {
            "performance", "性能", "压测", "压力", "并发", "qps", "tps", "吞吐", "latency", "p95", "p99", "k6", "jmeter", "load test",
        },
        "compatibility_testing": {"兼容", "兼容性", "chrome", "safari", "firefox", "ios", "android", "浏览器矩阵", "环境矩阵"},
        "smoke_testing": {"冒烟", "核心链路", "准入", "smoke", "主流程"},
        "code_review": {"code review", "代码审查", "代码审批", "review", "架构风险", "可维护性"},
        "ui_automation": {"ui", "界面", "页面", "浏览器", "playwright", "selenium", "点击", "截图", "h5"},
        "api_testing": {"api", "接口", "http", "endpoint", "请求", "响应", "状态码", "契约", "payload"},
    }
    CAPABILITIES: dict[str, list[str]] = {
        "security_testing": ["security.assessment"],
        "performance_testing": ["performance.load_test"],
        "compatibility_testing": ["compatibility.matrix_test"],
        "smoke_testing": ["smoke.validation"],
        "code_review": ["code.review"],
        "ui_automation": ["ui.automation"],
        "api_testing": ["api.validation", "api.documentation.read"],
    }
    ACTIVE_SECURITY_TOKENS = {
        "安全测试", "漏洞扫描", "渗透", "扫描", "探测", "攻击", "漏洞利用",
        "xss", "csrf", "sql注入", "scan", "probe", "exploit", "pentest",
    }

    def recognize(self, message: str, context: dict[str, Any] | None = None) -> IntentDecision:
        normalized = " ".join(str(message or "").split())
        text = normalized.lower()
        scores = {
            mode_key: sum(1 for token in tokens if self._token_present(text, token))
            for mode_key, tokens in self.MODE_TOKENS.items()
        }
        routing_scores = dict(scores)
        active_security_intent = any(token in text for token in self.ACTIVE_SECURITY_TOKENS)
        if routing_scores["security_testing"] and not active_security_intent:
            routing_scores["security_testing"] = 0
        matched = [key for key, score in routing_scores.items() if score > 0]
        candidate = self._select_candidate(routing_scores)
        target_kind = self._target_kind(routing_scores)
        objectives = self._objectives(routing_scores)
        if scores["security_testing"] and routing_scores["code_review"]:
            objectives.append("security_review")
        capabilities = self._capabilities(matched, target_kind)
        text_without_urls = self.URL_PATTERN.sub(" ", normalized)
        actions = self._requested_actions(text_without_urls.lower())
        target_url = self._extract_url(normalized)
        endpoint = self._extract_endpoint(text_without_urls)
        evidence = [f"{key}:{scores[key]}" for key, score in scores.items() if score > 0]
        best_score = routing_scores.get(candidate or "", 0)
        second_score = sorted(routing_scores.values(), reverse=True)[1] if len(routing_scores) > 1 else 0
        ambiguous = bool(candidate and second_score == best_score and best_score > 0)
        confidence = 0.0 if not candidate else min(0.96, 0.56 + best_score * 0.12 - (0.12 if ambiguous else 0.0))

        return IntentDecision(
            target_kind=target_kind,
            objectives=objectives or (["general_assistance"] if normalized else []),
            requested_actions=actions,
            required_capabilities=capabilities,
            candidate_mode_key=candidate,
            confidence=confidence,
            needs_clarification=ambiguous or (bool(matched) and confidence < 0.55),
            evidence=evidence,
            parameters={
                "target_url": target_url,
                "endpoint": endpoint,
                "method": self._extract_method(text),
                "environment_hint": self._environment_hint(text, context or {}),
            },
        )

    def _select_candidate(self, scores: dict[str, int]) -> str | None:
        if not any(scores.values()):
            return None
        # Objective-oriented workflows take precedence over the target shape.
        priority = [
            "security_testing", "performance_testing", "compatibility_testing", "smoke_testing",
            "code_review", "ui_automation", "api_testing",
        ]
        return max(priority, key=lambda key: (scores[key], -priority.index(key)))

    def _target_kind(self, scores: dict[str, int]) -> str:
        if scores["api_testing"]:
            return "api"
        if scores["ui_automation"] or scores["compatibility_testing"]:
            return "ui"
        if scores["code_review"]:
            return "code"
        return "service" if scores["performance_testing"] or scores["security_testing"] else "general"

    def _objectives(self, scores: dict[str, int]) -> list[str]:
        mapping = {
            "api_testing": "functional",
            "ui_automation": "ui_automation",
            "performance_testing": "performance",
            "security_testing": "security",
            "compatibility_testing": "compatibility",
            "smoke_testing": "smoke",
            "code_review": "code_review",
        }
        return [value for key, value in mapping.items() if scores[key] > 0]

    def _capabilities(self, matched: list[str], target_kind: str) -> list[str]:
        result = [capability for mode_key in matched for capability in self.CAPABILITIES[mode_key]]
        if target_kind == "api" and "api.documentation.read" not in result:
            result.append("api.documentation.read")
        return list(dict.fromkeys(result))

    def _requested_actions(self, text: str) -> list[str]:
        actions: list[str] = []
        checks = {
            "read": ("读取", "查看", "查询", "分析", "read", "list", "search"),
            "execute": ("执行", "运行", "测试", "压测", "扫描", "run", "test"),
            "write": ("创建", "修改", "更新", "上传", "create", "update", "upload"),
            "delete": ("删除", "清空", "销毁", "delete", "drop", "destroy"),
            "send": ("发送", "发邮件", "send", "publish"),
        }
        for action, tokens in checks.items():
            if any(self._token_present(text, token) for token in tokens):
                actions.append(action)
        return actions or ["respond"]

    def _extract_url(self, message: str) -> str:
        match = self.URL_PATTERN.search(message)
        return match.group(0).rstrip(".,;，。；") if match else ""

    def _extract_endpoint(self, message: str) -> str:
        match = re.search(r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)?\s*(/[^\s,，。]+)", message, re.IGNORECASE)
        return match.group(1) if match else ""

    def _extract_method(self, text: str) -> str:
        match = re.search(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", text, re.IGNORECASE)
        return match.group(1).upper() if match else ""

    def _environment_hint(self, text: str, context: dict[str, Any]) -> str:
        if re.search(r"\b(?:production|prod)\b", text) or any(token in text for token in ("生产环境", "线上环境")):
            return "production"
        if re.search(r"\b(?:staging|stage)\b", text) or "预发" in text:
            return "staging"
        if re.search(r"\btesting\b|\btest\s+env\b", text) or "测试环境" in text:
            return "testing"
        if re.search(r"\b(?:development|dev)\b", text) or any(token in text for token in ("开发环境", "本地")):
            return "development"
        explicit = str(context.get("environment") or "").strip().lower()
        if explicit:
            return explicit
        return "unknown"

    def _token_present(self, text: str, token: str) -> bool:
        if token == "并发":
            return re.search(r"并发(?!送)", text) is not None
        if token == "压力":
            return "压力" in text and any(signal in text for signal in ("测试", "接口", "并发", "性能", "qps", "tps"))
        if token in {"请求", "响应"}:
            api_context = any(
                signal in text
                for signal in ("api", "接口", "http", "endpoint", "状态码", "/api", "发送请求", "请求体", "响应体")
            ) or re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", text, re.IGNORECASE)
            return token in text and bool(api_context)
        if token.isascii() and " " not in token:
            return re.search(rf"\b{re.escape(token)}\b", text) is not None
        return token in text
