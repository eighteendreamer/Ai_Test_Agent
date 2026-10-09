"""Manage credential sessions for API testing campaigns.

First version: in-memory per-session credential store. Credentials are
provided by the user (token / username+password) or obtained via a dynamic
login flow executed by the executor.
"""
from __future__ import annotations

from datetime import datetime, timezone
from http.cookies import SimpleCookie
from typing import Any
from uuid import uuid4

from src.modes.api_testing_mode.campaign_state import CredentialSession
from src.modes.api_testing_mode.contracts import (
    AUTH_BEARER,
    AUTH_BASIC,
    AUTH_COOKIE,
    AUTH_CUSTOM,
    AUTH_NONE,
)


class CredentialManager:
    """Create, store, and resolve credential sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, CredentialSession] = {}

    def create_from_user_input(
        self,
        *,
        extracted: dict[str, Any],
        source: str = "user_input",
    ) -> CredentialSession:
        auth_type = str(extracted.get("auth_type") or AUTH_BEARER)
        token = str(extracted.get("token") or "")
        cookie = str(extracted.get("cookie") or "")
        username = str(extracted.get("username") or "")
        password = str(extracted.get("password") or "")

        headers: dict[str, str] = {}
        cookie_jar: dict[str, str] = {}

        if auth_type == AUTH_BEARER and token:
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api_key" and token:
            header_name = self._safe_header_name(extracted.get("header_name")) or "Authorization"
            headers[header_name] = token
        elif auth_type == AUTH_BASIC and username:
            import base64
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif auth_type == AUTH_COOKIE:
            cookie_jar.update(self._parse_cookie_input(extracted.get("cookie")))
        elif auth_type == AUTH_CUSTOM:
            headers.update(self._normalize_headers(extracted.get("headers")))
            header_name = self._safe_header_name(extracted.get("header_name"))
            if header_name and token:
                headers[header_name] = token
            cookie_jar.update(self._parse_cookie_input(extracted.get("cookie")))

        session = CredentialSession(
            credential_session_id=str(uuid4()),
            auth_type=auth_type,
            token=token,
            cookie_jar=cookie_jar,
            headers=headers,
            expires_at="",
            source=source,
            notes=f"Created from {source} at {datetime.now(timezone.utc).isoformat()}",
        )
        self._sessions[session.credential_session_id] = session
        return session

    @staticmethod
    def _safe_header_name(value: Any) -> str:
        """Return a usable header name without allowing line injection."""

        name = str(value or "").strip()
        if not name or "\r" in name or "\n" in name or ":" in name:
            return ""
        return name

    @classmethod
    def _normalize_headers(cls, value: Any) -> dict[str, str]:
        """Keep only explicit scalar custom headers supplied by the user."""

        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_name, raw_value in value.items():
            name = cls._safe_header_name(raw_name)
            if not name or raw_value is None:
                continue
            text = str(raw_value)
            if "\r" in text or "\n" in text:
                continue
            normalized[name] = text
        return normalized

    @staticmethod
    def _parse_cookie_input(value: Any) -> dict[str, str]:
        """Parse a Cookie header into name/value pairs without persisting values in logs."""

        if isinstance(value, dict):
            return {
                str(name).strip(): str(cookie_value)
                for name, cookie_value in value.items()
                if str(name).strip() and cookie_value is not None
            }
        raw = str(value or "").strip()
        if not raw:
            return {}

        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except (TypeError, ValueError):
            parsed = SimpleCookie()
        if parsed:
            return {name: morsel.value for name, morsel in parsed.items()}

        # Keep malformed but unambiguous pairs usable; never turn the whole
        # header into a synthetic ``session=...`` cookie.
        result: dict[str, str] = {}
        for part in raw.split(";"):
            name, separator, cookie_value = part.strip().partition("=")
            if separator and name.strip() and "\r" not in name and "\n" not in name:
                result[name.strip()] = cookie_value.strip()
        return result

    def create_from_login_response(
        self,
        *,
        response_body: dict[str, Any],
        token_field: str = "access_token",
        login_endpoint: str = "",
    ) -> CredentialSession:
        token = str(response_body.get(token_field) or response_body.get("token") or "")
        if not token:
            for key, value in response_body.items():
                if "token" in key.lower() and isinstance(value, str) and len(value) > 10:
                    token = value
                    token_field = key
                    break

        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        session = CredentialSession(
            credential_session_id=str(uuid4()),
            auth_type=AUTH_BEARER,
            token=token,
            headers=headers,
            expires_at="",
            source="dynamic_login",
            login_endpoint=login_endpoint,
            notes=f"Obtained via {login_endpoint} field={token_field}",
        )
        self._sessions[session.credential_session_id] = session
        return session

    def get(self, credential_session_id: str) -> CredentialSession | None:
        return self._sessions.get(credential_session_id)

    def restore_session(self, session: CredentialSession) -> CredentialSession:
        restored = session.model_copy(deep=True)
        if not restored.credential_session_id:
            restored.credential_session_id = str(uuid4())
        self._sessions[restored.credential_session_id] = restored
        return restored

    def has_valid_session(self) -> bool:
        for session in self._sessions.values():
            if session.token or session.headers or session.cookie_jar:
                return True
        return False

    def get_latest(self) -> CredentialSession | None:
        if not self._sessions:
            return None
        return list(self._sessions.values())[-1]

    def build_request_headers(self, credential_session_id: str) -> dict[str, str]:
        session = self._sessions.get(credential_session_id)
        if session is None:
            return {}
        headers = dict(session.headers)
        if session.cookie_jar:
            cookie_str = "; ".join(f"{k}={v}" for k, v in session.cookie_jar.items())
            headers["Cookie"] = cookie_str
        return headers

    def clear(self) -> None:
        self._sessions.clear()


__all__ = ["CredentialManager"]
