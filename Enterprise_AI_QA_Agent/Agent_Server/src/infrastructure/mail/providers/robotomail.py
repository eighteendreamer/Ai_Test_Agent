"""Robotomail adapter for the official v1 REST API."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from src.application.mail.contracts import (
    MailCapability,
    MailMessage,
    MailSendRequest,
    MailSendResult,
)
from src.infrastructure.mail.providers.rest_base import RestMailAdapterBase

if TYPE_CHECKING:
    from src.schemas.email_config import EmailConfigRecord


class RobotomailAdapter(RestMailAdapterBase):
    provider_key = "robotomail"
    display_name = "Robotomail"
    auth_type = "api_key"
    default_base_url = "https://api.robotomail.com"
    configuration_fields = ["api_key", "mailbox_id", "base_url"]

    def capabilities(self) -> set[MailCapability]:
        # Reply is supported through inReplyTo. The public API documents neither
        # forwarding nor deleting stored messages.
        return {
            MailCapability.SEND,
            MailCapability.LIST,
            MailCapability.READ,
            MailCapability.SEARCH,
            MailCapability.REPLY,
            MailCapability.ATTACHMENTS,
            MailCapability.PROVISION_INBOX,
        }

    def _messages_path(self, record: "EmailConfigRecord") -> str:
        return f"/v1/mailboxes/{quote(self._mailbox_id(record), safe='')}/messages"

    @staticmethod
    def _attachment_ids(items: list[dict[str, Any]]) -> list[str]:
        return [
            str(item.get("id") or item.get("attachment_id") or "")
            for item in items
            if str(item.get("id") or item.get("attachment_id") or "")
        ]

    def _send_body(self, request: MailSendRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "to": request.recipients,
            "cc": request.cc,
            "subject": request.subject,
            "bodyText": request.content,
            "bodyHtml": request.content_html,
            "attachments": self._attachment_ids(request.attachments),
        }
        return {key: value for key, value in body.items() if value not in (None, "", [])}

    def send(self, record: "EmailConfigRecord", request: MailSendRequest) -> MailSendResult:
        if request.bcc:
            raise RuntimeError("Robotomail's documented send API does not support BCC.")
        data = self._request(
            "POST", record, self._messages_path(record), json_body=self._send_body(request)
        )
        return self._send_result(record, data, len(request.recipients))

    def list_messages(
        self, record: "EmailConfigRecord", options: dict[str, Any] | None = None
    ) -> list[MailMessage]:
        data = self._request("GET", record, self._messages_path(record), params=options or {})
        return [self._message(item) for item in self._items(data, "messages")]

    def read_message(self, record: "EmailConfigRecord", message_id: str) -> MailMessage:
        data = self._request(
            "GET", record, f"{self._messages_path(record)}/{quote(message_id, safe='')}"
        )
        raw = data.get("message", data) if isinstance(data, dict) else {}
        return self._message(raw if isinstance(raw, dict) else {})

    def search_messages(
        self,
        record: "EmailConfigRecord",
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[MailMessage]:
        params = {**(options or {}), "limit": min(int((options or {}).get("limit") or 100), 100)}
        messages = self.list_messages(record, params)
        needle = query.casefold()
        return [
            item for item in messages
            if needle in "\n".join((item.subject, item.from_email, item.snippet, item.body_text)).casefold()
        ]

    def reply(
        self, record: "EmailConfigRecord", message_id: str, request: MailSendRequest
    ) -> MailSendResult:
        original = self.read_message(record, message_id)
        raw_rfc_message_id = str(original.raw.get("messageId") or "").strip()
        if not raw_rfc_message_id:
            raise RuntimeError("Robotomail reply requires the original RFC Message-ID.")
        recipients = request.recipients or ([original.from_email] if original.from_email else [])
        body = self._send_body(MailSendRequest(
            recipients=recipients,
            subject=request.subject or f"Re: {original.subject}",
            content=request.content,
            content_html=request.content_html,
            cc=request.cc,
            attachments=request.attachments,
        ))
        body["inReplyTo"] = raw_rfc_message_id
        data = self._request("POST", record, self._messages_path(record), json_body=body)
        return self._send_result(record, data, len(recipients))

    def download_attachment(
        self, record: "EmailConfigRecord", message_id: str, attachment_id: str
    ) -> dict[str, Any]:
        del message_id  # Robotomail addresses attachments globally by attachment UUID.
        data = self._request(
            "GET", record, f"/v1/attachments/{quote(attachment_id, safe='')}"
        )
        payload = data.get("attachment", data) if isinstance(data, dict) else {}
        url = payload.get("url") if isinstance(payload, dict) else ""
        if not url:
            raise RuntimeError("Robotomail did not return an attachment download URL.")
        return self._download_url(record, str(url))

    def provision_inbox(
        self, record: "EmailConfigRecord", options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        values = dict(options or {})
        existing_email = str(values.pop("existing_email", "") or "").strip().casefold()
        if existing_email:
            data = self._request("GET", record, "/v1/mailboxes")
            matches = [
                item
                for item in self._items(data, "mailboxes")
                if str(item.get("fullAddress") or "").strip().casefold()
                == existing_email
            ]
            if not matches:
                raise RuntimeError(
                    f"Robotomail mailbox '{existing_email}' was not found or is not "
                    "accessible with this API key."
                )
            payload = matches[0]
            return {
                "ok": True,
                "provider": self.provider_key,
                "mailbox_id": payload.get("id"),
                "email": payload.get("fullAddress"),
                "raw": payload,
                "bound_existing": True,
            }
        if not values.get("address"):
            local_part = str(record.sender_email or "").partition("@")[0].strip()
            values["address"] = local_part or f"agent-{uuid.uuid4().hex[:10]}"
        values.setdefault("displayName", record.config_name)
        try:
            data = self._request("POST", record, "/v1/mailboxes", json_body=values)
        except RuntimeError as exc:
            if "HTTP 403" in str(exc) and "full-access API key" in str(exc):
                raise RuntimeError(
                    "创建 Robotomail Mailbox 需要 full-access API Key；"
                    "如使用 mailbox-scoped Key，请选择“绑定已有 Mailbox”。"
                ) from None
            raise
        payload = data.get("mailbox", data) if isinstance(data, dict) else {}
        return {
            "ok": True,
            "provider": self.provider_key,
            "mailbox_id": payload.get("id") if isinstance(payload, dict) else None,
            "email": payload.get("fullAddress") if isinstance(payload, dict) else None,
            "raw": payload,
        }

    def status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            data = self._request(
                "GET", record, f"/v1/mailboxes/{quote(self._mailbox_id(record), safe='')}"
            )
            payload = data.get("mailbox", data) if isinstance(data, dict) else {}
            return {
                "ok": True,
                "configured": True,
                "provider": self.provider_key,
                "capabilities": sorted(cap.value for cap in self.capabilities()),
                "email": payload.get("fullAddress") if isinstance(payload, dict) else record.sender_email,
                "error": "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "configured": False,
                "provider": self.provider_key,
                "capabilities": sorted(cap.value for cap in self.capabilities()),
                "error": str(exc),
            }
