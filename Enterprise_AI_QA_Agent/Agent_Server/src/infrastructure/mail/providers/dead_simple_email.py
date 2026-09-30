"""Dead Simple Email adapter for the official v1 REST API."""

from __future__ import annotations

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


class DeadSimpleEmailAdapter(RestMailAdapterBase):
    provider_key = "dead_simple_email"
    display_name = "Dead Simple Email"
    auth_type = "api_key"
    default_base_url = "https://api.deadsimple.email"
    configuration_fields = ["api_key", "mailbox_id", "base_url"]

    def capabilities(self) -> set[MailCapability]:
        # The published API has no message delete/trash endpoint.
        return {
            MailCapability.SEND,
            MailCapability.LIST,
            MailCapability.READ,
            MailCapability.SEARCH,
            MailCapability.REPLY,
            MailCapability.FORWARD,
            MailCapability.ATTACHMENTS,
            MailCapability.PROVISION_INBOX,
        }

    def _messages_path(self, record: "EmailConfigRecord") -> str:
        return f"/v1/inboxes/{quote(self._mailbox_id(record), safe='')}/messages"

    @staticmethod
    def _send_body(request: MailSendRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "to": request.recipients,
            "subject": request.subject,
            "text_body": request.content,
            "html_body": request.content_html,
            "cc": request.cc,
            "bcc": request.bcc,
            "reply_to": request.reply_to,
            "attachments": request.attachments,
        }
        return {key: value for key, value in body.items() if value not in (None, "", [])}

    def send(self, record: "EmailConfigRecord", request: MailSendRequest) -> MailSendResult:
        data = self._request(
            "POST", record, self._messages_path(record), json_body=self._send_body(request)
        )
        return self._send_result(record, data, len(request.recipients))

    def list_messages(
        self, record: "EmailConfigRecord", options: dict[str, Any] | None = None
    ) -> list[MailMessage]:
        data = self._request("GET", record, self._messages_path(record), params=options or {})
        return [self._message(item) for item in self._items(data, "messages", "data", "items")]

    def read_message(self, record: "EmailConfigRecord", message_id: str) -> MailMessage:
        data = self._request(
            "GET", record, f"{self._messages_path(record)}/{quote(message_id, safe='')}"
        )
        raw = data.get("message", data.get("data", data)) if isinstance(data, dict) else {}
        return self._message(raw if isinstance(raw, dict) else {})

    def search_messages(
        self,
        record: "EmailConfigRecord",
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[MailMessage]:
        messages = self.list_messages(record, {"limit": (options or {}).get("limit", 100)})
        needle = query.casefold()
        return [
            item for item in messages
            if needle in "\n".join((item.subject, item.from_email, item.snippet, item.body_text)).casefold()
        ]

    def reply(
        self, record: "EmailConfigRecord", message_id: str, request: MailSendRequest
    ) -> MailSendResult:
        body = {
            key: value for key, value in {
                "text_body": request.content,
                "html_body": request.content_html,
            }.items() if value
        }
        data = self._request(
            "POST",
            record,
            f"{self._messages_path(record)}/{quote(message_id, safe='')}/reply",
            json_body=body,
        )
        return self._send_result(record, data, 0)

    def forward(
        self, record: "EmailConfigRecord", message_id: str, request: MailSendRequest
    ) -> MailSendResult:
        body = {
            key: value for key, value in {
                "to": request.recipients,
                "text_body": request.content,
                "html_body": request.content_html,
            }.items() if value not in ("", [])
        }
        data = self._request(
            "POST",
            record,
            f"{self._messages_path(record)}/{quote(message_id, safe='')}/forward",
            json_body=body,
        )
        return self._send_result(record, data, len(request.recipients))

    def download_attachment(
        self, record: "EmailConfigRecord", message_id: str, attachment_id: str
    ) -> dict[str, Any]:
        data = self._request(
            "GET",
            record,
            f"{self._messages_path(record)}/{quote(message_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
        )
        payload = data.get("attachment", data.get("data", data)) if isinstance(data, dict) else {}
        url = payload.get("url") or payload.get("download_url") if isinstance(payload, dict) else ""
        if url:
            return self._download_url(record, str(url))
        return {"ok": True, "raw": payload}

    def provision_inbox(
        self, record: "EmailConfigRecord", options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = {"display_name": record.config_name, **(options or {})}
        data = self._request("POST", record, "/v1/inboxes", json_body=body)
        payload = data.get("inbox", data.get("data", data)) if isinstance(data, dict) else {}
        return {
            "ok": True,
            "provider": self.provider_key,
            "mailbox_id": payload.get("inbox_id") if isinstance(payload, dict) else None,
            "email": payload.get("email") if isinstance(payload, dict) else None,
            "raw": payload,
        }

    def status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            data = self._request(
                "GET", record, f"/v1/inboxes/{quote(self._mailbox_id(record), safe='')}"
            )
            payload = data.get("inbox", data.get("data", data)) if isinstance(data, dict) else {}
            return {
                "ok": True,
                "configured": True,
                "provider": self.provider_key,
                "capabilities": sorted(cap.value for cap in self.capabilities()),
                "email": payload.get("email") if isinstance(payload, dict) else record.sender_email,
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
