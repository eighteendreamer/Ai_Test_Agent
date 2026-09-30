"""AgentMail adapter using the official v0 inbox-scoped HTTP API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from src.application.mail.contracts import MailCapability, MailMessage, MailSendRequest, MailSendResult
from src.infrastructure.mail.providers.rest_base import RestMailAdapterBase

if TYPE_CHECKING:
    from src.schemas.email_config import EmailConfigRecord


class AgentMailAdapter(RestMailAdapterBase):
    provider_key = "agentmail"
    display_name = "AgentMail"
    auth_type = "api_key"
    default_base_url = "https://api.agentmail.to"
    configuration_fields = ["api_key", "mailbox_id", "base_url"]

    def capabilities(self) -> set[MailCapability]:
        return {
            MailCapability.SEND, MailCapability.LIST, MailCapability.READ,
            MailCapability.SEARCH, MailCapability.REPLY, MailCapability.FORWARD,
            MailCapability.TRASH, MailCapability.ATTACHMENTS,
            MailCapability.PROVISION_INBOX,
        }

    def _messages_path(self, record: "EmailConfigRecord") -> str:
        return f"/v0/inboxes/{quote(self._mailbox_id(record), safe='')}/messages"

    def send(self, record: "EmailConfigRecord", request: MailSendRequest) -> MailSendResult:
        body: dict[str, Any] = {
            "to": request.recipients,
            "subject": request.subject,
            "text": request.content,
            "html": request.content_html,
            "cc": request.cc,
            "bcc": request.bcc,
            "attachments": request.attachments,
        }
        if request.reply_to:
            body["reply_to"] = request.reply_to
        body = {key: value for key, value in body.items() if value not in (None, "", [])}
        data = self._request("POST", record, self._messages_path(record) + "/send", json_body=body)
        return self._send_result(record, data, len(request.recipients))

    def list_messages(self, record: "EmailConfigRecord", options: dict[str, Any] | None = None) -> list[MailMessage]:
        data = self._request("GET", record, self._messages_path(record), params=options or {})
        return [self._message(item) for item in self._items(data, "messages")]

    def read_message(self, record: "EmailConfigRecord", message_id: str) -> MailMessage:
        data = self._request("GET", record, f"{self._messages_path(record)}/{message_id}")
        raw = data.get("message", data) if isinstance(data, dict) else {}
        return self._message(raw)

    def search_messages(self, record: "EmailConfigRecord", query: str, options: dict[str, Any] | None = None) -> list[MailMessage]:
        params = {**(options or {}), "q": query}
        data = self._request("GET", record, self._messages_path(record) + "/search", params=params)
        return [self._message(item) for item in self._items(data, "messages")]

    def reply(self, record: "EmailConfigRecord", message_id: str, request: MailSendRequest) -> MailSendResult:
        body = {"text": request.content, "html": request.content_html, "cc": request.cc}
        data = self._request("POST", record, f"{self._messages_path(record)}/{message_id}/reply", json_body={k: v for k, v in body.items() if v not in ("", [])})
        return self._send_result(record, data, 0)

    def forward(self, record: "EmailConfigRecord", message_id: str, request: MailSendRequest) -> MailSendResult:
        body = {"to": request.recipients, "text": request.content, "html": request.content_html}
        data = self._request("POST", record, f"{self._messages_path(record)}/{message_id}/forward", json_body={k: v for k, v in body.items() if v not in ("", [])})
        return self._send_result(record, data, len(request.recipients))

    def trash(self, record: "EmailConfigRecord", message_id: str) -> MailSendResult:
        self._request("DELETE", record, f"{self._messages_path(record)}/{message_id}")
        return MailSendResult(sent=True, provider=self.provider_key, from_email=record.sender_email, recipient_count=0, message_id=message_id)

    def download_attachment(self, record: "EmailConfigRecord", message_id: str, attachment_id: str) -> dict[str, Any]:
        data = self._request(
            "GET",
            record,
            f"{self._messages_path(record)}/{quote(message_id, safe='')}/attachments/{quote(attachment_id, safe='')}",
        )
        payload = data.get("attachment", data) if isinstance(data, dict) else {}
        url = payload.get("download_url") if isinstance(payload, dict) else ""
        if not url:
            raise RuntimeError("AgentMail did not return an attachment download URL.")
        return self._download_url(record, str(url))

    def provision_inbox(self, record: "EmailConfigRecord", options: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {"display_name": record.config_name, **(options or {})}
        data = self._request("POST", record, "/v0/inboxes", json_body=body)
        payload = data.get("inbox", data) if isinstance(data, dict) else {}
        return {
            "ok": True,
            "provider": self.provider_key,
            "mailbox_id": payload.get("inbox_id") or payload.get("id"),
            "email": payload.get("email") or payload.get("address"),
            "raw": payload,
        }

    def status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            data = self._request(
                "GET",
                record,
                f"/v0/inboxes/{quote(self._mailbox_id(record), safe='')}",
            )
            payload = data.get("inbox", data) if isinstance(data, dict) else {}
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
