"""OpenMail adapter for the official v1 REST API."""

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


class OpenMailAdapter(RestMailAdapterBase):
    provider_key = "openmail"
    display_name = "OpenMail"
    auth_type = "api_key"
    default_base_url = "https://api.openmail.sh"
    configuration_fields = ["api_key", "mailbox_id", "base_url"]

    def capabilities(self) -> set[MailCapability]:
        # The public API has no forward or message-delete endpoint.
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
        return f"/v1/inboxes/{quote(self._mailbox_id(record), safe='')}/messages"

    @staticmethod
    def _send_body(request: MailSendRequest, recipient: str) -> dict[str, Any]:
        body = request.content or request.content_html
        if not body:
            raise RuntimeError("OpenMail requires a non-empty email body.")
        payload: dict[str, Any] = {
            "to": recipient,
            "subject": request.subject,
            "body": body,
            "cc": request.cc,
        }
        if request.content and request.content_html:
            payload["bodyHtml"] = request.content_html
        if request.reply_to:
            payload["replyTo"] = request.reply_to
        return {key: value for key, value in payload.items() if value not in (None, "", [])}

    def send(self, record: "EmailConfigRecord", request: MailSendRequest) -> MailSendResult:
        if len(request.recipients) != 1:
            raise RuntimeError("OpenMail's documented v1 send API requires exactly one recipient.")
        if request.bcc:
            raise RuntimeError("OpenMail's documented v1 send API does not support BCC.")
        if request.attachments:
            raise RuntimeError("OpenMail outbound attachments require multipart upload, which is not configured by this adapter.")
        data = self._request(
            "POST",
            record,
            f"/v1/inboxes/{quote(self._mailbox_id(record), safe='')}/send",
            json_body=self._send_body(request, request.recipients[0]),
            extra_headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        return self._send_result(record, data, 1)

    def list_messages(
        self, record: "EmailConfigRecord", options: dict[str, Any] | None = None
    ) -> list[MailMessage]:
        data = self._request("GET", record, self._messages_path(record), params=options or {})
        return [self._message(item) for item in self._items(data, "data")]

    def read_message(self, record: "EmailConfigRecord", message_id: str) -> MailMessage:
        data = self._request(
            "GET", record, self._messages_path(record), params={"limit": 100, "offset": 0}
        )
        for item in self._items(data, "data"):
            if str(item.get("id") or "") == message_id:
                return self._message(item)
        raise RuntimeError(f"OpenMail message '{message_id}' was not found in the inbox.")

    def search_messages(
        self,
        record: "EmailConfigRecord",
        query: str,
        options: dict[str, Any] | None = None,
    ) -> list[MailMessage]:
        params = {"limit": min(int((options or {}).get("limit") or 100), 100), "offset": 0}
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
        recipient = request.recipients[0] if request.recipients else original.from_email
        payload = self._send_body(request, recipient)
        if original.thread_id:
            payload["threadId"] = original.thread_id
        data = self._request(
            "POST",
            record,
            f"/v1/inboxes/{quote(self._mailbox_id(record), safe='')}/send",
            json_body=payload,
            extra_headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        return self._send_result(record, data, 1)

    def download_attachment(
        self, record: "EmailConfigRecord", message_id: str, attachment_id: str
    ) -> dict[str, Any]:
        path = f"/v1/attachments/{quote(message_id, safe='')}/{quote(attachment_id, safe='')}"
        return self._request_binary(record, path)

    def provision_inbox(
        self, record: "EmailConfigRecord", options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        values = dict(options or {})
        existing_email = str(values.pop("existing_email", "") or "").strip().casefold()
        if existing_email:
            data = self._request("GET", record, "/v1/inboxes", params={"limit": 100})
            inboxes = self._items(data, "data", "inboxes", "items")
            payload = next(
                (
                    item for item in inboxes
                    if str(item.get("address") or "").strip().casefold() == existing_email
                ),
                None,
            )
            if payload is None:
                raise RuntimeError(f"OpenMail inbox '{existing_email}' was not found.")
            return {
                "ok": True,
                "provider": self.provider_key,
                "mailbox_id": payload.get("id"),
                "email": payload.get("address"),
                "bound_existing": True,
                "raw": payload,
            }

        body = {"displayName": record.config_name, **values}
        data = self._request("POST", record, "/v1/inboxes", json_body=body)
        payload = data if isinstance(data, dict) else {}
        return {
            "ok": True,
            "provider": self.provider_key,
            "mailbox_id": payload.get("id"),
            "email": payload.get("address"),
            "raw": payload,
        }

    def status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        try:
            data = self._request(
                "GET",
                record,
                f"/v1/inboxes/{quote(self._mailbox_id(record), safe='')}",
            )
            return {
                "ok": True,
                "configured": True,
                "provider": self.provider_key,
                "capabilities": sorted(cap.value for cap in self.capabilities()),
                "email": data.get("address") if isinstance(data, dict) else record.sender_email,
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
