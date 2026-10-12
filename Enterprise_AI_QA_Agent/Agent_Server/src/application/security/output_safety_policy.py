from __future__ import annotations

import re
from typing import Any

from src.application.security.prompt_injection_policy import PromptInjectionPolicy
from src.schemas.intent import ContentSafetyAssessment


class OutputSafetyPolicy:
    """Redact credential material and label injection-like tool content before reuse."""

    REDACTED = "[REDACTED]"
    SENSITIVE_KEYS = {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "password",
        "passwd",
        "secret",
        "secret_key",
        "client_secret",
        "private_key",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "credential_session",
    }
    TEXT_PATTERNS = (
        re.compile(r"(?i)\b(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+"),
        re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\s*[:=]\s*['\"]?([^\s,'\";}{]+)"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    )

    def __init__(self, injection_policy: PromptInjectionPolicy | None = None) -> None:
        self._injection_policy = injection_policy or PromptInjectionPolicy()

    def sanitize_tool_output(self, output: dict[str, Any]) -> tuple[dict[str, Any], ContentSafetyAssessment, list[str]]:
        redacted_paths: list[str] = []
        sanitized = self._sanitize_value(output, path="output", redacted_paths=redacted_paths)
        assessment = self._injection_policy.assess(sanitized, "tool_output")
        if assessment.has_injection_signals or redacted_paths:
            sanitized = dict(sanitized)
            sanitized["_security"] = {
                "untrusted_content": True,
                "indirect_injection_signals": assessment.indirect_injection_signals,
                "redacted_field_count": len(redacted_paths),
            }
        return sanitized, assessment, redacted_paths

    def sanitize_text(self, text: str) -> tuple[str, bool]:
        sanitized = str(text or "")
        for pattern in self.TEXT_PATTERNS:
            if pattern.groups >= 2:
                sanitized = pattern.sub(lambda match: f"{match.group(1)}={self.REDACTED}", sanitized)
            elif pattern.groups == 1:
                sanitized = pattern.sub(lambda match: f"{match.group(1)}{self.REDACTED}", sanitized)
            else:
                sanitized = pattern.sub(self.REDACTED, sanitized)
        return sanitized, sanitized != str(text or "")

    def sanitize_for_audit(self, value: Any) -> Any:
        return self._sanitize_value(value, path="audit", redacted_paths=[])

    def _sanitize_value(self, value: Any, *, path: str, redacted_paths: list[str]) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
                current_path = f"{path}.{key}"
                is_schema_description = isinstance(item, dict) and bool(
                    {"type", "properties", "description", "$ref"}.intersection(item)
                )
                if normalized_key in self.SENSITIVE_KEYS and not is_schema_description:
                    sanitized[key] = self.REDACTED
                    redacted_paths.append(current_path)
                elif normalized_key in self.SENSITIVE_KEYS and isinstance(item, dict):
                    sanitized[key] = self._sanitize_sensitive_schema(
                        item,
                        path=current_path,
                        redacted_paths=redacted_paths,
                    )
                else:
                    sanitized[key] = self._sanitize_value(item, path=current_path, redacted_paths=redacted_paths)
            return sanitized
        if isinstance(value, list):
            return [
                self._sanitize_value(item, path=f"{path}[{index}]", redacted_paths=redacted_paths)
                for index, item in enumerate(value)
            ]
        if isinstance(value, tuple):
            return tuple(
                self._sanitize_value(item, path=f"{path}[{index}]", redacted_paths=redacted_paths)
                for index, item in enumerate(value)
            )
        if isinstance(value, str):
            sanitized, changed = self.sanitize_text(value)
            if changed:
                redacted_paths.append(path)
            return sanitized
        return value

    def _sanitize_sensitive_schema(
        self,
        value: dict[str, Any],
        *,
        path: str,
        redacted_paths: list[str],
    ) -> dict[str, Any]:
        sanitized = self._sanitize_value(value, path=path, redacted_paths=redacted_paths)
        for key in ("example", "examples", "default", "enum", "const"):
            if key in sanitized:
                sanitized[key] = self.REDACTED
                redacted_paths.append(f"{path}.{key}")
        return sanitized
