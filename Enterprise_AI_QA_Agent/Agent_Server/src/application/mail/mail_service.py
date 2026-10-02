"""Agent-native mailbox service.

Wraps the email config store and the provider registry so callers never need
to know which provider backs the active mailbox. This is the single seam the
tool runtime delegates to for both transactional send and Agent Mailbox
capabilities.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import TYPE_CHECKING, Any

from src.application.mail.contracts import (
    CapabilityNotSupported,
    MailboxNotActive,
    MailCapability,
    MailConfigStoreUnavailable,
    MailSendRequest,
    MailSendResult,
)
from src.application.mail.provider_registry import (
    MailProviderRegistry,
    build_default_mail_provider_registry,
)

if TYPE_CHECKING:
    from src.infrastructure.email_config_store import MySQLEmailConfigStore
    from src.schemas.email_config import EmailConfigRecord


CAPABILITY_NOT_SUPPORTED = {"ok": False, "error": "capability_not_supported"}


class MailService:
    """Front door for the single globally active Agent Mailbox."""

    def __init__(
        self,
        email_config_store: "MySQLEmailConfigStore | None",
        registry: MailProviderRegistry | None = None,
    ) -> None:
        self._email_config_store = email_config_store
        self._registry = registry or build_default_mail_provider_registry()
        self._pending_writes: dict[str, dict[str, Any]] = {}
        self._pending_lock = threading.Lock()

    # --- config resolution --------------------------------------------------

    def _resolve_active_record(self) -> "EmailConfigRecord":
        if self._email_config_store is None:
            raise MailConfigStoreUnavailable("Email config store is not available.")
        try:
            records = self._email_config_store.list_all()
            enabled = [item for item in records if item.enabled]
            if enabled:
                return next((item for item in enabled if item.is_default), enabled[0])
            raise KeyError("active")
        except KeyError as exc:
            raise MailboxNotActive(
                "No globally active Agent mailbox is available. "
                "Configure, authorize, and activate one in Email Settings first."
            ) from exc

    def _resolve_record_by_id(
        self,
        config_id: int | None,
    ) -> "EmailConfigRecord":
        if config_id is None:
            return self._resolve_active_record()
        if self._email_config_store is None:
            raise MailConfigStoreUnavailable("Email config store is not available.")
        record = self._email_config_store.get_by_id(config_id)
        if not record.enabled:
            raise MailboxNotActive(
                f"Mailbox config '{record.config_name}' is not active. "
                "Only the globally active mailbox may perform mail operations."
            )
        active = self._resolve_active_record()
        if record.id != active.id:
            raise MailboxNotActive(
                f"Mailbox config '{record.config_name}' is not active. "
                "Only the globally active mailbox may perform mail operations."
            )
        return record

    # --- transactional send -------------------------------------------------

    def send(
        self,
        recipients: list[str],
        subject: str,
        content: str,
        content_html: str,
        *,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        """Prepare an email through the globally active mailbox."""
        record = self._resolve_record_by_id(config_id)
        adapter = self._registry.resolve(record.provider)
        request = MailSendRequest(
            recipients=recipients,
            subject=subject,
            content=content,
            content_html=content_html,
        )
        if hasattr(adapter, "send_confirm"):
            return adapter.send(record, request).to_dict()
        return self._defer_write(record, "send", request, recipients=len(recipients), summary={
            "to": list(recipients), "subject": subject,
        })

    def confirm(
        self,
        operation: str,
        confirmation_token: str,
        *,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        """Commit a prepared Agent Mail write operation after user confirmation."""
        pending = self._take_pending_write(confirmation_token, operation)
        if pending is not None:
            record = self._resolve_active_record()
            if record.id != pending["record_id"]:
                raise MailboxNotActive("The globally active mailbox changed; prepare the operation again.")
            adapter = self._registry.resolve(record.provider)
            payload = pending["payload"]
            operation_key = pending["operation"]
            if operation_key == "send":
                result = adapter.send(record, payload)
            elif operation_key == "reply":
                result = adapter.reply(record, pending["message_id"], payload)
            elif operation_key == "forward":
                result = adapter.forward(record, pending["message_id"], payload)
            elif operation_key == "trash":
                result = adapter.trash(record, pending["message_id"])
            else:
                raise RuntimeError(f"Unknown pending mail operation '{operation_key}'.")
            return result.to_dict()

        record = self._resolve_record_by_id(config_id)
        adapter = self._registry.resolve(record.provider)
        operation_key = str(operation or "").strip().lower()
        method_name = {
            "send": "send_confirm",
            "reply": "reply_confirm",
            "forward": "forward_confirm",
            "trash": "trash_confirm",
        }.get(operation_key)
        if not method_name or not hasattr(adapter, method_name):
            raise RuntimeError(
                f"Provider '{record.provider}' does not support confirmation for operation '{operation}'."
            )
        result = getattr(adapter, method_name)(record, confirmation_token)
        return result.to_dict()

    # --- Agent Mailbox capabilities ----------------------------------------

    def status(
        self,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        record = self._resolve_record_by_id(config_id)
        adapter = self._registry.resolve(record.provider)
        return adapter.status(record)

    def list_messages(
        self,
        config_id: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._capability_call(
            MailCapability.LIST,
            config_id,
            lambda adapter, record: {
                "ok": True,
                "messages": [m.to_dict() for m in adapter.list_messages(record, options)],
            },
        )

    def read_message(
        self,
        message_id: str,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        return self._capability_call(
            MailCapability.READ,
            config_id,
            lambda adapter, record: {
                "ok": True,
                "message": adapter.read_message(record, message_id).to_dict(),
            },
        )

    def search_messages(
        self,
        query: str,
        config_id: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._capability_call(
            MailCapability.SEARCH,
            config_id,
            lambda adapter, record: {
                "ok": True,
                "messages": [
                    m.to_dict() for m in adapter.search_messages(record, query, options)
                ],
            },
        )

    def reply(
        self,
        message_id: str,
        request: MailSendRequest,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        record = self._resolve_record_by_id(config_id)
        adapter = self._registry.resolve(record.provider)
        if not adapter.supports(MailCapability.REPLY):
            return dict(CAPABILITY_NOT_SUPPORTED)
        if not hasattr(adapter, "reply_confirm"):
            return self._defer_write(record, "reply", request, message_id=message_id, summary={"message_id": message_id})
        return self._capability_call(
            MailCapability.REPLY,
            config_id,
            lambda adapter, record: adapter.reply(record, message_id, request).to_dict(),
        )

    def forward(
        self,
        message_id: str,
        request: MailSendRequest,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        record = self._resolve_record_by_id(config_id)
        adapter = self._registry.resolve(record.provider)
        if not adapter.supports(MailCapability.FORWARD):
            return dict(CAPABILITY_NOT_SUPPORTED)
        if not hasattr(adapter, "forward_confirm"):
            return self._defer_write(record, "forward", request, message_id=message_id, recipients=len(request.recipients), summary={"message_id": message_id, "to": request.recipients})
        return self._capability_call(
            MailCapability.FORWARD,
            config_id,
            lambda adapter, record: adapter.forward(record, message_id, request).to_dict(),
        )

    def trash(
        self,
        message_id: str,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        record = self._resolve_record_by_id(config_id)
        adapter = self._registry.resolve(record.provider)
        if not adapter.supports(MailCapability.TRASH):
            return dict(CAPABILITY_NOT_SUPPORTED)
        if not hasattr(adapter, "trash_confirm"):
            return self._defer_write(record, "trash", None, message_id=message_id, summary={"message_id": message_id})
        return self._capability_call(
            MailCapability.TRASH,
            config_id,
            lambda adapter, record: adapter.trash(record, message_id).to_dict(),
        )

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        config_id: int | None = None,
    ) -> dict[str, Any]:
        return self._capability_call(
            MailCapability.ATTACHMENTS,
            config_id,
            lambda adapter, record: adapter.download_attachment(
                record, message_id, attachment_id
            ),
        )

    # --- internals ----------------------------------------------------------

    def _defer_write(
        self,
        record: "EmailConfigRecord",
        operation: str,
        payload: MailSendRequest | None,
        *,
        message_id: str | None = None,
        recipients: int = 0,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = "sysmail_" + secrets.token_urlsafe(24)
        now = time.time()
        with self._pending_lock:
            self._pending_writes = {
                key: value
                for key, value in self._pending_writes.items()
                if now - float(value.get("created_at") or 0) <= 300
            }
            self._pending_writes[token] = {
                "record_id": record.id,
                "operation": operation,
                "payload": payload,
                "message_id": message_id,
                "created_at": now,
            }
        return MailSendResult(
            sent=False,
            provider=record.provider,
            from_email=record.sender_email,
            recipient_count=recipients,
            confirmation_required=True,
            confirmation_token=token,
            confirmation_summary=str(summary or {}),
        ).to_dict()

    def _take_pending_write(self, token: str, operation: str) -> dict[str, Any] | None:
        with self._pending_lock:
            pending = self._pending_writes.pop(token, None)
        if pending is None:
            return None
        if time.time() - float(pending.get("created_at") or 0) > 300:
            raise RuntimeError("Mail confirmation expired; prepare the operation again.")
        if pending.get("operation") != str(operation or "").strip().lower():
            raise RuntimeError("Mail confirmation operation does not match the preview.")
        return pending

    def _capability_call(
        self,
        capability,
        config_id,
        fn,
    ) -> dict[str, Any]:
        record = self._resolve_record_by_id(config_id)
        adapter = self._registry.resolve(record.provider)
        if not adapter.supports(capability):
            return dict(CAPABILITY_NOT_SUPPORTED)
        try:
            return fn(adapter, record)
        except CapabilityNotSupported:
            return dict(CAPABILITY_NOT_SUPPORTED)
