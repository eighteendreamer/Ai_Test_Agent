"""Shared transport and normalization for HTTP Agent Mail providers."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import httpx

from src.application.mail.contracts import MailMessage, MailProviderAdapter, MailSendResult

if TYPE_CHECKING:
    from src.schemas.email_config import EmailConfigRecord


class MailProviderHTTPError(RuntimeError):
    def __init__(self, *, provider_key: str, provider_name: str, status_code: int, detail: str) -> None:
        self.provider_key = provider_key
        self.status_code = status_code
        super().__init__(f"{provider_name} API returned HTTP {status_code}: {detail}")


class RestMailAdapterBase(MailProviderAdapter):
    default_base_url = ""
    default_timeout = 30
    configuration_fields = ["api_key", "base_url", "mailbox_id"]

    def descriptor(self) -> dict[str, Any]:
        data = super().descriptor()
        data["configuration_fields"] = list(self.configuration_fields)
        data["default_base_url"] = self.default_base_url
        return data

    def _base_url(self, record: "EmailConfigRecord") -> str:
        value = str((record.extra_config or {}).get("base_url") or self.default_base_url).strip()
        if not value:
            raise RuntimeError(f"Provider '{self.provider_key}' requires extra_config.base_url.")
        return value.rstrip("/")

    @staticmethod
    def _mailbox_id(record: "EmailConfigRecord") -> str:
        value = str((record.extra_config or {}).get("mailbox_id") or "").strip()
        if not value:
            raise RuntimeError("The active mailbox config requires extra_config.mailbox_id.")
        return value

    def _headers(self, record: "EmailConfigRecord") -> dict[str, str]:
        if not record.api_key:
            raise RuntimeError(f"Provider '{self.provider_key}' requires an API key.")
        header_name = str((record.extra_config or {}).get("auth_header") or "Authorization")
        scheme = str((record.extra_config or {}).get("auth_scheme") or "Bearer").strip()
        token = f"{scheme} {record.api_key}".strip()
        return {"Content-Type": "application/json", header_name: token}

    def _request(
        self,
        method: str,
        record: "EmailConfigRecord",
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        headers = self._headers(record)
        if extra_headers:
            headers.update(extra_headers)
        response = httpx.request(
            method,
            self._base_url(record) + path,
            headers=headers,
            json=json_body,
            params=params,
            timeout=int((record.extra_config or {}).get("timeout_seconds") or self.default_timeout),
        )
        self._raise_provider_error(response)
        if not response.content:
            return {}
        return response.json()

    def _request_binary(self, record: "EmailConfigRecord", path: str) -> dict[str, Any]:
        response = httpx.get(
            self._base_url(record) + path,
            headers=self._headers(record),
            timeout=int((record.extra_config or {}).get("timeout_seconds") or self.default_timeout),
            follow_redirects=True,
        )
        self._raise_provider_error(response)
        return self._binary_result(response)

    def _download_url(self, record: "EmailConfigRecord", url: str) -> dict[str, Any]:
        """Download a provider-issued signed URL without forwarding API credentials."""
        if not str(url or "").startswith(("https://", "http://")):
            raise RuntimeError(f"Provider '{self.provider_key}' returned an invalid attachment URL.")
        response = httpx.get(
            url,
            timeout=int((record.extra_config or {}).get("timeout_seconds") or self.default_timeout),
            follow_redirects=True,
        )
        self._raise_provider_error(response)
        return self._binary_result(response)

    def _raise_provider_error(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                error = payload.get("error")
                message = payload.get("message") or payload.get("detail")
                if isinstance(error, dict):
                    error = error.get("message") or error.get("code")
                parts = [str(item).strip() for item in (error, message) if str(item or "").strip()]
                detail = " - ".join(dict.fromkeys(parts))
            if not detail:
                detail = response.text.strip()[:500] or response.reason_phrase
            provider = self.display_name or self.provider_key
            raise MailProviderHTTPError(
                provider_key=self.provider_key,
                provider_name=provider,
                status_code=response.status_code,
                detail=detail,
            ) from exc

    @staticmethod
    def _binary_result(response: httpx.Response) -> dict[str, Any]:
        disposition = response.headers.get("content-disposition", "")
        filename = ""
        if "filename=" in disposition:
            filename = disposition.split("filename=", 1)[1].strip().strip('"')
        return {
            "ok": True,
            "filename": filename,
            "content_type": response.headers.get("content-type", "application/octet-stream"),
            "content_base64": base64.b64encode(response.content).decode("ascii"),
            "size": len(response.content),
        }

    @staticmethod
    def _items(data: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _message(raw: dict[str, Any]) -> MailMessage:
        sender = (
            raw.get("from") or raw.get("from_email") or raw.get("fromAddress")
            or raw.get("fromAddr") or raw.get("sender") or ""
        )
        if isinstance(sender, dict):
            sender = sender.get("email") or sender.get("address") or ""
        recipients = (
            raw.get("to") or raw.get("recipients") or raw.get("toAddresses")
            or raw.get("toAddr") or []
        )
        if isinstance(recipients, str):
            recipients = [recipients]
        return MailMessage(
            message_id=str(raw.get("message_id") or raw.get("messageId") or raw.get("id") or ""),
            thread_id=str(raw.get("thread_id") or raw.get("threadId") or "") or None,
            subject=str(raw.get("subject") or ""),
            from_email=str(sender),
            to=[str(item.get("email") or item.get("address") or "") if isinstance(item, dict) else str(item) for item in recipients],
            snippet=str(
                raw.get("snippet") or raw.get("preview") or raw.get("text")
                or raw.get("body_text") or raw.get("bodyText") or raw.get("text_body") or ""
            )[:500],
            body_text=str(
                raw.get("text") or raw.get("body_text") or raw.get("bodyText")
                or raw.get("text_body") or raw.get("body") or ""
            ),
            body_html=str(raw.get("html") or raw.get("body_html") or raw.get("bodyHtml") or raw.get("html_body") or ""),
            received_at=str(
                raw.get("timestamp") or raw.get("received_at") or raw.get("receivedAt")
                or raw.get("created_at") or raw.get("createdAt") or ""
            ) or None,
            attachments=list(raw.get("attachments") or []),
            raw=raw,
        )

    def _send_result(self, record: "EmailConfigRecord", data: Any, recipients: int) -> MailSendResult:
        payload = data if isinstance(data, dict) else {}
        nested = payload.get("message")
        if isinstance(nested, dict):
            payload = nested
        return MailSendResult(
            sent=True,
            provider=self.provider_key,
            from_email=record.sender_email,
            recipient_count=recipients,
            message_id=str(payload.get("message_id") or payload.get("messageId") or payload.get("id") or "") or None,
        )

    def status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            self._base_url(record)
            self._headers(record)
            self._mailbox_id(record)
            configured = True
            error = ""
        except RuntimeError as exc:
            configured = False
            error = str(exc)
        return {
            "ok": configured,
            "configured": configured,
            "provider": self.provider_key,
            "capabilities": sorted(cap.value for cap in self.capabilities()),
            "error": error,
        }
