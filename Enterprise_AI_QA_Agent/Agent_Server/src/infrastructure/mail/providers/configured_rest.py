"""Profile-driven adapter for vendors whose deployments expose different REST routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.application.mail.contracts import (
    MailCapability,
    MailMessage,
    MailOperationValidationError,
    MailSendRequest,
    MailSendResult,
)
from src.infrastructure.mail.providers.rest_base import RestMailAdapterBase

if TYPE_CHECKING:
    from src.schemas.email_config import EmailConfigRecord


class ConfiguredRestMailAdapter(RestMailAdapterBase):
    """Normalizes a vendor REST API through an explicit per-provider route profile."""

    default_base_url = ""
    configuration_fields = ["api_key", "base_url", "mailbox_id", "routes"]

    def capabilities(self) -> set[MailCapability]:
        return {
            MailCapability.SEND, MailCapability.LIST, MailCapability.READ,
            MailCapability.SEARCH, MailCapability.REPLY, MailCapability.FORWARD,
            MailCapability.TRASH, MailCapability.ATTACHMENTS,
        }

    def status(self, record: "EmailConfigRecord") -> dict[str, Any]:
        result = super().status(record)
        if not result["ok"]:
            return result
        try:
            for operation in ("send", "list", "read", "search", "reply", "forward", "trash", "attachment"):
                values = {"message_id": "message_id", "attachment_id": "attachment_id"}
                self._route(record, operation, **values)
        except (KeyError, RuntimeError, ValueError) as exc:
            result["ok"] = result["configured"] = False
            result["error"] = str(exc)
        return result

    def _route(self, record: "EmailConfigRecord", operation: str, **values: str) -> str:
        routes = (record.extra_config or {}).get("routes")
        template = routes.get(operation) if isinstance(routes, dict) else None
        if not isinstance(template, str) or not template.strip():
            raise MailOperationValidationError(f"Provider '{self.provider_key}' requires extra_config.routes.{operation}.")
        values.setdefault("mailbox_id", self._mailbox_id(record))
        return template.format(**values)

    @staticmethod
    def _send_body(request: MailSendRequest) -> dict[str, Any]:
        return {key: value for key, value in {
            "to": request.recipients, "subject": request.subject,
            "text": request.content, "html": request.content_html,
            "cc": request.cc, "bcc": request.bcc, "attachments": request.attachments,
        }.items() if value not in (None, "", [])}

    def send(self, record: "EmailConfigRecord", request: MailSendRequest) -> MailSendResult:
        data = self._request("POST", record, self._route(record, "send"), json_body=self._send_body(request))
        return self._send_result(record, data, len(request.recipients))

    def list_messages(self, record: "EmailConfigRecord", options: dict[str, Any] | None = None) -> list[MailMessage]:
        data = self._request("GET", record, self._route(record, "list"), params=options or {})
        return [self._message(item) for item in self._items(data, "messages", "items", "data")]

    def read_message(self, record: "EmailConfigRecord", message_id: str) -> MailMessage:
        data = self._request("GET", record, self._route(record, "read", message_id=message_id))
        raw = data.get("message", data) if isinstance(data, dict) else {}
        return self._message(raw)

    def search_messages(self, record: "EmailConfigRecord", query: str, options: dict[str, Any] | None = None) -> list[MailMessage]:
        data = self._request("GET", record, self._route(record, "search"), params={**(options or {}), "query": query})
        return [self._message(item) for item in self._items(data, "messages", "items", "data")]

    def reply(self, record: "EmailConfigRecord", message_id: str, request: MailSendRequest) -> MailSendResult:
        data = self._request("POST", record, self._route(record, "reply", message_id=message_id), json_body=self._send_body(request))
        return self._send_result(record, data, 0)

    def forward(self, record: "EmailConfigRecord", message_id: str, request: MailSendRequest) -> MailSendResult:
        data = self._request("POST", record, self._route(record, "forward", message_id=message_id), json_body=self._send_body(request))
        return self._send_result(record, data, len(request.recipients))

    def trash(self, record: "EmailConfigRecord", message_id: str) -> MailSendResult:
        self._request("DELETE", record, self._route(record, "trash", message_id=message_id))
        return MailSendResult(sent=True, provider=self.provider_key, from_email=record.sender_email, recipient_count=0, message_id=message_id)

    def download_attachment(self, record: "EmailConfigRecord", message_id: str, attachment_id: str) -> dict[str, Any]:
        return self._request_binary(record, self._route(record, "attachment", message_id=message_id, attachment_id=attachment_id))
