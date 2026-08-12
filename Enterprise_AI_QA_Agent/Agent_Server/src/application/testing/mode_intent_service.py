from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from src.schemas.mode import ModeDescriptor


@dataclass(frozen=True)
class TestModeIntentState:
    mode_key: str
    intent_key: str
    confidence: float
    reasons: list[str] = field(default_factory=list)
    suggested_agent_key: str = ""
    recommended_skills: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)


class TestModeIntentService:
    REPORT_TOKENS = {
        "report",
        "summary",
        "summarize",
        "结论",
        "总结",
        "汇总",
        "报告",
        "分析结果",
    }
    CLI_TOKENS = {
        "cli",
        "terminal",
        "shell",
        "powershell",
        "bash",
        "cmd",
        "命令",
        "命令行",
        "终端",
        "控制台",
    }
    UI_BROWSER_TOKENS = {
        "browser",
        "page",
        "ui",
        "web",
        "playwright",
        "selenium",
        "页面",
        "浏览器",
        "前端",
        "界面",
    }
    UI_EXPLORE_TOKENS = {
        "explore",
        "map",
        "scan",
        "snapshot",
        "discover",
        "inspect",
        "探索",
        "采集",
        "梳理",
        "建模",
        "图谱",
        "结构",
        "登录",
    }
    UI_EXECUTE_TOKENS = {
        "execute",
        "run",
        "validate",
        "verify",
        "assert",
        "test",
        "scenario",
        "regression",
        "smoke",
        "执行",
        "验证",
        "断言",
        "测试",
        "回归",
        "冒烟",
    }
    API_CONTRACT_TOKENS = {"contract", "schema", "swagger", "openapi", "契约", "协议", "结构"}
    API_PAYLOAD_TOKENS = {"payload", "body", "json", "field", "字段", "报文", "响应体", "请求体"}
    API_STATUS_TOKENS = {"status", "status code", "response code", "状态码", "响应码"}
    SECURITY_FOCUS_TOKENS = {
        "xss": "xss",
        "csrf": "csrf",
        "sqli": "sql_injection",
        "sql injection": "sql_injection",
        "idor": "idor",
        "auth": "auth",
        "authentication": "auth",
        "authorization": "authorization",
        "越权": "authorization",
        "鉴权": "auth",
        "认证": "auth",
        "漏洞": "vulnerability_scan",
        "安全": "security_scan",
    }
    PERFORMANCE_FOCUS_TOKENS = {
        "latency": "latency",
        "response time": "latency",
        "throughput": "throughput",
        "qps": "throughput",
        "tps": "throughput",
        "load": "load",
        "stress": "stress",
        "concurrency": "concurrency",
        "并发": "concurrency",
        "压测": "stress",
        "负载": "load",
        "时延": "latency",
        "吞吐": "throughput",
    }
    SMOKE_FOCUS_TOKENS = {
        "login": "login",
        "payment": "payment",
        "checkout": "checkout",
        "order": "order",
        "dashboard": "dashboard",
        "登录": "login",
        "支付": "payment",
        "下单": "order",
        "首页": "homepage",
        "关键链路": "critical_path",
        "核心链路": "critical_path",
    }
    CODE_REVIEW_FOCUS_TOKENS = {
        "architecture": "architecture",
        "correctness": "correctness",
        "security": "security",
        "testability": "testability",
        "maintainability": "maintainability",
        "架构": "architecture",
        "正确性": "correctness",
        "安全": "security",
        "可测试": "testability",
        "可维护": "maintainability",
    }
    COMPATIBILITY_PRODUCT_TOKENS = {
        "web": "web",
        "网页": "web",
        "网站": "web",
        "h5": "h5",
        "android": "android_app",
        "安卓": "android_app",
        "apk": "android_app",
        "ios": "ios_app",
        "iphone": "ios_app",
        "ipa": "ios_app",
        "微信小程序": "wechat_mini_program",
        "wechat": "wechat_mini_program",
        "miniprogram": "wechat_mini_program",
        "支付宝小程序": "alipay_mini_program",
        "alipay": "alipay_mini_program",
        "linux": "linux_app",
        "桌面": "linux_app",
    }
    COMPATIBILITY_ACTION_TOKENS = {
        "确认": "execute_approved_plan",
        "执行": "execute_approved_plan",
        "生成调度": "execute_approved_plan",
        "runner": "execute_approved_plan",
        "计划": "draft_plan",
        "矩阵": "draft_plan",
        "用例": "draft_plan",
        "兼容": "draft_plan",
    }
    HTTP_METHOD_PATTERN = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\b", flags=re.IGNORECASE)
    URL_PATTERN = re.compile(r"https?://[^\s]+", flags=re.IGNORECASE)
    API_PATH_PATTERN = re.compile(r"(/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+)")

    def classify(
        self,
        *,
        mode: ModeDescriptor,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> TestModeIntentState:
        normalized_message = " ".join((message or "").split())
        context = context or {}
        combined = f"{normalized_message} {context}".lower()

        if mode.key == "ui_automation":
            base_state = self._classify_ui_automation(mode.key, normalized_message, combined, context)
        elif mode.key == "api_testing":
            base_state = self._classify_api_testing(mode.key, normalized_message, combined, context)
        elif mode.key == "security_testing":
            base_state = self._classify_security_testing(mode.key, normalized_message, combined, context)
        elif mode.key == "performance_testing":
            base_state = self._classify_performance_testing(mode.key, normalized_message, combined, context)
        elif mode.key == "smoke_testing":
            base_state = self._classify_smoke_testing(mode.key, normalized_message, combined, context)
        elif mode.key == "compatibility_testing":
            base_state = self._classify_compatibility_testing(mode.key, normalized_message, combined, context)
        elif mode.key == "code_review":
            base_state = self._classify_code_review(mode.key, normalized_message, combined, context)
        else:
            base_state = TestModeIntentState(
                mode_key=mode.key,
                intent_key="general_execution",
                confidence=0.55,
                reasons=[f"Fell back to the generic test-mode intent handler for '{mode.key}'."],
                parameters={"objective": normalized_message},
            )

        suggested_agent_key = self._suggest_agent(
            mode=mode,
            combined=combined,
            default_agent=base_state.suggested_agent_key,
        )
        recommended_skills = list(dict.fromkeys(base_state.recommended_skills + self._recommended_skills(combined)))
        return TestModeIntentState(
            mode_key=base_state.mode_key,
            intent_key=base_state.intent_key,
            confidence=base_state.confidence,
            reasons=base_state.reasons,
            suggested_agent_key=suggested_agent_key,
            recommended_skills=recommended_skills,
            parameters=base_state.parameters,
        )

    def _classify_ui_automation(
        self,
        mode_key: str,
        message: str,
        combined: str,
        context: dict[str, Any],
    ) -> TestModeIntentState:
        target_url = self._first_text(
            context.get("target_url"),
            self._extract_url(message),
        )
        direction = self._detect_ui_direction(combined, target_url)
        subdirection = self._detect_ui_subdirection(combined, target_url)
        intent_key = "reporting" if self._contains_any(combined, self.REPORT_TOKENS) else (
            "test_execution" if subdirection == "test_execution" else "information_exploration"
        )
        confidence = 0.62
        reasons = ["Resolved the request inside UI automation mode."]
        if direction:
            reasons.append(f"Detected UI direction '{direction}'.")
            confidence += 0.12
        if subdirection:
            reasons.append(f"Detected UI subdirection '{subdirection}'.")
            confidence += 0.12
        if target_url:
            reasons.append("Extracted target URL from the request.")
            confidence += 0.08
        parameters = {
            "objective": message,
            "target_url": target_url,
            "direction": direction,
            "subdirection": subdirection,
        }
        return TestModeIntentState(
            mode_key=mode_key,
            intent_key=intent_key,
            confidence=min(confidence, 0.95),
            reasons=reasons,
            parameters=parameters,
        )

    def _classify_api_testing(
        self,
        mode_key: str,
        message: str,
        combined: str,
        context: dict[str, Any],
    ) -> TestModeIntentState:
        endpoint = self._extract_endpoint(message, context)
        method = self._extract_method(message, context)
        verification_focus = "general"
        if self._contains_any(combined, self.API_CONTRACT_TOKENS):
            verification_focus = "contract_validation"
        elif self._contains_any(combined, self.API_PAYLOAD_TOKENS):
            verification_focus = "payload_validation"
        elif self._contains_any(combined, self.API_STATUS_TOKENS):
            verification_focus = "status_validation"
        intent_key = "reporting" if self._contains_any(combined, self.REPORT_TOKENS) else verification_focus
        confidence = 0.65 + (0.1 if endpoint else 0.0) + (0.05 if method else 0.0)
        reasons = [f"Resolved API testing focus as '{verification_focus}'."]
        if endpoint:
            reasons.append("Extracted endpoint from the request.")
        if method:
            reasons.append(f"Detected HTTP method '{method}'.")
        return TestModeIntentState(
            mode_key=mode_key,
            intent_key=intent_key,
            confidence=min(confidence, 0.94),
            reasons=reasons,
            parameters={
                "objective": message,
                "endpoint": endpoint,
                "method": method,
                "verification_focus": verification_focus,
            },
        )

    def _classify_security_testing(
        self,
        mode_key: str,
        message: str,
        combined: str,
        context: dict[str, Any],
    ) -> TestModeIntentState:
        risk_focus = self._first_match(combined, self.SECURITY_FOCUS_TOKENS, default="security_scan")
        intent_key = "reporting" if self._contains_any(combined, self.REPORT_TOKENS) else "security_scan"
        confidence = 0.66 if risk_focus != "security_scan" else 0.58
        reasons = [f"Resolved security testing focus as '{risk_focus}'."]
        return TestModeIntentState(
            mode_key=mode_key,
            intent_key=intent_key,
            confidence=confidence,
            reasons=reasons,
            parameters={
                "objective": message,
                "risk_focus": risk_focus,
                "target_url": self._first_text(context.get("target_url"), self._extract_url(message)),
            },
        )

    def _classify_performance_testing(
        self,
        mode_key: str,
        message: str,
        combined: str,
        context: dict[str, Any],
    ) -> TestModeIntentState:
        workload_profile = self._first_match(combined, self.PERFORMANCE_FOCUS_TOKENS, default="baseline")
        intent_key = "reporting" if self._contains_any(combined, self.REPORT_TOKENS) else workload_profile
        confidence = 0.66 if workload_profile != "baseline" else 0.57
        reasons = [f"Resolved performance testing focus as '{workload_profile}'."]
        return TestModeIntentState(
            mode_key=mode_key,
            intent_key=intent_key,
            confidence=confidence,
            reasons=reasons,
            parameters={
                "objective": message,
                "workload_profile": workload_profile,
                "target_url": self._first_text(context.get("target_url"), self._extract_url(message)),
            },
        )

    def _classify_smoke_testing(
        self,
        mode_key: str,
        message: str,
        combined: str,
        context: dict[str, Any],
    ) -> TestModeIntentState:
        suite_focus = self._first_match(combined, self.SMOKE_FOCUS_TOKENS, default="critical_path")
        intent_key = "reporting" if self._contains_any(combined, self.REPORT_TOKENS) else "smoke_validation"
        confidence = 0.64 if suite_focus != "critical_path" else 0.56
        reasons = [f"Resolved smoke-testing focus as '{suite_focus}'."]
        return TestModeIntentState(
            mode_key=mode_key,
            intent_key=intent_key,
            confidence=confidence,
            reasons=reasons,
            parameters={
                "objective": message,
                "suite_focus": suite_focus,
                "target_url": self._first_text(context.get("target_url"), self._extract_url(message)),
            },
        )

    def _classify_code_review(
        self,
        mode_key: str,
        message: str,
        combined: str,
        context: dict[str, Any],
    ) -> TestModeIntentState:
        review_focus = self._first_match(combined, self.CODE_REVIEW_FOCUS_TOKENS, default="general")
        intent_key = "reporting" if self._contains_any(combined, self.REPORT_TOKENS) else "code_review"
        confidence = 0.68 if review_focus != "general" else 0.58
        reasons = [f"Resolved code-review focus as '{review_focus}'."]
        return TestModeIntentState(
            mode_key=mode_key,
            intent_key=intent_key,
            confidence=confidence,
            reasons=reasons,
            parameters={
                "objective": message,
                "review_focus": review_focus,
                "project_scope": self._first_text(context.get("project_scope")),
            },
        )

    def _classify_compatibility_testing(
        self,
        mode_key: str,
        message: str,
        combined: str,
        context: dict[str, Any],
    ) -> TestModeIntentState:
        product_type = self._first_text(
            context.get("product_type"),
            self._first_match(combined, self.COMPATIBILITY_PRODUCT_TOKENS, default=""),
        )
        compatibility_action = self._first_text(
            context.get("compatibility_action"),
            self._first_match(combined, self.COMPATIBILITY_ACTION_TOKENS, default="draft_plan"),
        )
        target_url = self._first_text(context.get("target_url"), context.get("entrypoint"), self._extract_url(message))
        intent_key = "reporting" if self._contains_any(combined, self.REPORT_TOKENS) else compatibility_action
        confidence = 0.62
        reasons = ["Resolved compatibility-testing request."]
        if product_type:
            reasons.append(f"Detected product type '{product_type}'.")
            confidence += 0.12
        if target_url:
            reasons.append("Extracted target URL or entrypoint.")
            confidence += 0.08
        if compatibility_action == "execute_approved_plan":
            confidence += 0.08
        parameters = {
            "objective": message,
            "compatibility_action": compatibility_action,
            "product_type": product_type or "unknown",
            "target_url": target_url,
            "entrypoint": self._first_text(context.get("entrypoint"), target_url),
            "priority_flows": context.get("priority_flows") if isinstance(context.get("priority_flows"), list) else [],
            "plan": context.get("plan") if isinstance(context.get("plan"), dict) else None,
            "confirm_risks": bool(context.get("confirm_risks")),
            "product_access_manifest": context.get("product_access_manifest") if isinstance(context.get("product_access_manifest"), dict) else None,
            "access_manifest": context.get("access_manifest") if isinstance(context.get("access_manifest"), dict) else None,
            "product_name": self._first_text(context.get("product_name")),
            "product_version": self._first_text(context.get("product_version")),
            "artifact": self._first_text(context.get("artifact")),
            "auth_strategy": self._first_text(context.get("auth_strategy")),
            "test_scope": context.get("test_scope") if isinstance(context.get("test_scope"), list) else [],
            "forbidden_actions": context.get("forbidden_actions") if isinstance(context.get("forbidden_actions"), list) else [],
        }
        if "selected_case_ids" in context:
            parameters["selected_case_ids"] = (
                context.get("selected_case_ids") if isinstance(context.get("selected_case_ids"), list) else []
            )
        if "selected_environment_ids" in context:
            parameters["selected_environment_ids"] = (
                context.get("selected_environment_ids") if isinstance(context.get("selected_environment_ids"), list) else []
            )
        return TestModeIntentState(
            mode_key=mode_key,
            intent_key=intent_key,
            confidence=min(confidence, 0.95),
            reasons=reasons,
            parameters=parameters,
        )

    def _suggest_agent(
        self,
        *,
        mode: ModeDescriptor,
        combined: str,
        default_agent: str,
    ) -> str:
        allowed = set(mode.allowed_agent_keys or [])
        if self._contains_any(combined, self.REPORT_TOKENS) and "report-analyst" in allowed:
            return "report-analyst"
        if self._contains_any(combined, self.CLI_TOKENS) and "ops-executor" in allowed:
            return "ops-executor"
        if default_agent and default_agent in allowed:
            return default_agent
        return ""

    def _recommended_skills(self, combined: str) -> list[str]:
        return []

    def _detect_ui_direction(self, combined: str, target_url: str) -> str:
        if any(token in combined for token in {"mini program", "mini_program", "miniprogram", "小程序", "wechat"}):
            return "mini_program"
        if any(token in combined for token in {"android", "安卓"}):
            return "android"
        if any(token in combined for token in {"ios", "iphone", "ipad"}):
            return "ios"
        if any(token in combined for token in {"harmony", "harmonyos", "鸿蒙"}):
            return "harmonyos"
        if target_url or self._contains_any(combined, self.UI_BROWSER_TOKENS):
            return "browser"
        return ""

    def _detect_ui_subdirection(self, combined: str, target_url: str) -> str:
        if self._contains_any(combined, self.UI_EXECUTE_TOKENS):
            return "test_execution"
        if target_url or self._contains_any(combined, self.UI_EXPLORE_TOKENS):
            return "information_exploration"
        return ""

    def _extract_endpoint(self, message: str, context: dict[str, Any]) -> str:
        candidates = [
            context.get("endpoint"),
            context.get("url"),
            self._extract_url(message),
        ]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        path_match = self.API_PATH_PATTERN.search(message or "")
        if path_match and "/api/" in path_match.group(1).lower():
            return path_match.group(1).strip()
        return ""

    def _extract_method(self, message: str, context: dict[str, Any]) -> str:
        explicit = self._first_text(context.get("method"))
        if explicit:
            return explicit.upper()
        match = self.HTTP_METHOD_PATTERN.search(message or "")
        return match.group(1).upper() if match else ""

    def _extract_url(self, value: str) -> str:
        match = self.URL_PATTERN.search(value or "")
        return match.group(0).strip().rstrip(".,;:!?，。；：！？)]}") if match else ""

    def _first_match(self, text: str, mapping: dict[str, str], *, default: str) -> str:
        for token, resolved in mapping.items():
            if token in text:
                return resolved
        return default

    def _contains_any(self, text: str, tokens: set[str]) -> bool:
        return any(token in text for token in tokens)

    def _first_text(self, *values: object) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""
