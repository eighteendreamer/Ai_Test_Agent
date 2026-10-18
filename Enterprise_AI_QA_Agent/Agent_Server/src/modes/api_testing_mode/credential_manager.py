"""Manage credential sessions for API testing campaigns.

First version: in-memory per-session credential store. Credentials are
provided by the user (token / username+password) or obtained via a dynamic
login flow executed by the executor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.cookiejar import Cookie, CookieJar
from http.cookies import SimpleCookie
import logging
from typing import Any
from uuid import uuid4

import httpx

from src.modes.api_testing_mode.campaign_state import ApiCookieRecord, CredentialSession
from src.modes.api_testing_mode.input_binding import extract_path_value
from src.modes.api_testing_mode.contracts import (
    AUTH_BEARER,
    AUTH_BASIC,
    AUTH_COOKIE,
    AUTH_CUSTOM,
    AUTH_NONE,
)

logger = logging.getLogger("uvicorn.error.api_testing_mode.credentials")


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
            expires_at=str(extracted.get("expires_at") or ""),
            source=source,
            notes=f"Created from {source} at {datetime.now(timezone.utc).isoformat()}",
        )
        self._sessions[session.credential_session_id] = session
        return session

    @staticmethod
    def _response_expiry(response_body: dict[str, Any]) -> str:
        """Record explicit OAuth expires_in seconds once; missing means unknown."""
        if "expires_in" not in response_body:
            return ""
        raw = response_body["expires_in"]
        if isinstance(raw, str) and raw.isascii() and raw.isdecimal():
            try:
                raw = int(raw)
            except ValueError:
                raw = None
        if type(raw) is int and raw >= 0:
            try:
                return (datetime.now(timezone.utc) + timedelta(seconds=raw)).isoformat()
            except OverflowError:
                pass
        logger.warning("api_credential_expiry_invalid", extra={
            "source": "login_response", "field": "expires_in", "value_type": type(raw).__name__,
        })
        return ""

    @staticmethod
    def _absolute_expiry(session: CredentialSession) -> datetime | None:
        if not session.expires_at:
            return None
        try:
            expiry = datetime.fromisoformat(session.expires_at.replace("Z", "+00:00"))
            if expiry.tzinfo is not None:
                return expiry
        except ValueError:
            pass
        # No seconds/milliseconds or local-time-zone guesses for old snapshots.
        logger.warning("api_credential_expiry_invalid", extra={
            "credential_ref": session.credential_session_id, "field": "expires_at",
        })
        return None

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
        cookie_records: list[ApiCookieRecord] | None = None,
        prior_credential_session_id: str = "",
    ) -> CredentialSession:
        token, token_field = self._login_token(response_body, token_field)

        prior = self.get(prior_credential_session_id)
        headers = dict(prior.headers) if prior is not None else {}
        token_from_response = bool(token)
        expires_at = self._response_expiry(response_body) if token_from_response else (prior.expires_at if prior is not None else "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif prior is not None:
            token = prior.token
        if cookie_records is not None:
            # Scoped cookies are emitted by the HTTP client, never a flat header.
            headers = {name: value for name, value in headers.items() if name.casefold() != "cookie"}

        session = CredentialSession(
            credential_session_id=str(uuid4()),
            auth_type=AUTH_BEARER if token else (AUTH_COOKIE if cookie_records is not None else AUTH_BEARER),
            token=token,
            headers=headers,
            cookie_records=cookie_records,
            expires_at=expires_at,
            source="dynamic_login",
            login_endpoint=login_endpoint,
            notes=f"Obtained via {login_endpoint} field={token_field}",
        )
        self._sessions[session.credential_session_id] = session
        logger.info("api_credential_lifetime_recorded", extra={
            "credential_ref": session.credential_session_id, "expiry_known": bool(expires_at),
            "token_from_response": token_from_response,
        })
        return session

    @staticmethod
    def _login_token(response_body: dict[str, Any], token_field: str) -> tuple[str, str]:
        """Honor the declared field; never infer access from refresh/id tokens."""
        # Existing default access_token/token compatibility, with accessToken
        # already recognized by ApiDocParser. Explicit project paths are exact.
        fields = ("access_token", "accessToken", "token") if token_field == "access_token" else (token_field,)
        selected = next((field for field in fields if field in response_body), token_field)
        value = response_body[selected] if selected in response_body else extract_path_value(response_body, selected)
        if isinstance(value, str) and value.strip() and "\r" not in value and "\n" not in value:
            return value, selected
        logger.warning("api_login_token_unavailable", extra={
            "source": "login_response", "field": selected,
            "reason": "missing_or_invalid_string", "fallback_to_unrelated_field": False,
        })
        return "", selected

    def get(self, credential_session_id: str) -> CredentialSession | None:
        return self._sessions.get(credential_session_id)

    def restore_session(self, session: CredentialSession) -> CredentialSession:
        restored = session.model_copy(deep=True)
        if not restored.credential_session_id:
            restored.credential_session_id = str(uuid4())
        self._sessions[restored.credential_session_id] = restored
        return restored

    def has_valid_session(self) -> bool:
        now = datetime.now(timezone.utc)
        for session in self._sessions.values():
            expiry = self._absolute_expiry(session)
            if expiry is not None and expiry <= now:
                logger.info("api_credential_expired_for_precondition", extra={
                    "credential_ref": session.credential_session_id,
                })
                continue
            if session.token or session.headers or (session.cookie_jar and session.cookie_records is None):
                return True
            if any(record.expires is None or record.expires > datetime.now(timezone.utc).timestamp()
                   for record in session.cookie_records or []):
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
        if session.cookie_jar and session.cookie_records is None:
            cookie_str = "; ".join(f"{k}={v}" for k, v in session.cookie_jar.items())
            headers["Cookie"] = cookie_str
        return headers

    def build_request_cookies(self, credential_session_id: str) -> httpx.Cookies:
        """Restore the private scoped jar; HTTPX applies it to each request."""
        jar = CookieJar()
        session = self.get(credential_session_id)
        records = session.cookie_records if session is not None else None
        for record in records or []:
            jar.set_cookie(Cookie(**record.model_dump()))
        return httpx.Cookies(jar)

    @staticmethod
    def capture_cookie_records(cookies: httpx.Cookies) -> list[ApiCookieRecord]:
        records = []
        for cookie in cookies.jar:
            attributes = {name: getattr(cookie, name) for name in ApiCookieRecord.model_fields if name != "rest"}
            attributes["rest"] = {name: cookie.get_nonstandard_attr(name) for name in ("HttpOnly", "SameSite")
                if cookie.has_nonstandard_attr(name)}
            records.append(ApiCookieRecord(**attributes))
        return records

    def clear(self) -> None:
        self._sessions.clear()


__all__ = ["CredentialManager"]
