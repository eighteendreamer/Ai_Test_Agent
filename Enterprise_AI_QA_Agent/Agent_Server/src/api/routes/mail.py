"""Unified Mail API routes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, NoReturn

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.application.mail.contracts import (
    MailboxNotActive,
    MailCapability,
    MailConfigNotFoundError,
    MailConfigStoreUnavailable,
    MailOperationValidationError,
    MailProviderExecutionError,
    MailProviderUnavailableError,
)
from src.infrastructure.mail.providers.rest_base import MailProviderHTTPError
from src.schemas.email_config import EmailConfigUpdateRequest

router = APIRouter(prefix="/mail", tags=["mail"])
_PROVIDER_CALL_LIMIT = asyncio.Semaphore(4)
logger = logging.getLogger(__name__)


async def _run_provider_call(func, *args, **kwargs):
    """Keep slow vendor CLI/HTTP calls off the event loop and cap thread usage."""
    async with _PROVIDER_CALL_LIMIT:
        return await asyncio.to_thread(func, *args, **kwargs)


async def _run_mail_write(action: str, func, *args, config_id: int | None):
    return await _run_mail_operation(action, func, *args, operation_config_id=config_id, config_id=config_id)


async def _run_mail_operation(action: str, func, *args, operation_config_id: int | None, **kwargs):
    """Classify execution failures without confusing them with readable status DTOs."""
    context = {"action": action, "config_id": operation_config_id}
    try:
        return await _run_provider_call(func, *args, **kwargs)
    except MailConfigStoreUnavailable as exc:
        logger.error("mail_write_config_store_unavailable", extra=context)
        raise HTTPException(status_code=503, detail="Email configuration service unavailable.") from exc
    except MailboxNotActive as exc:
        logger.warning("mail_write_mailbox_inactive", extra=context)
        raise HTTPException(
            status_code=409,
            detail="Mailbox configuration is not active or has changed; activate a mailbox and prepare again.",
        ) from exc
    except MailProviderHTTPError as exc:
        logger.warning(
            "mail_write_provider_http_failed",
            extra={**context, "provider": exc.provider_key, "upstream_status": exc.status_code},
        )
        raise HTTPException(
            status_code=502,
            detail=f"Mail provider operation failed (upstream HTTP {exc.status_code}).",
        ) from exc
    except httpx.RequestError as exc:
        logger.warning(
            "mail_write_provider_transport_failed",
            extra={**context, "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=502, detail="Mail provider transport failed.") from exc
    except MailProviderUnavailableError as exc:
        logger.error("mail_write_provider_unavailable", extra={**context, "error_type": type(exc).__name__})
        raise HTTPException(status_code=503, detail="Mail provider execution dependency unavailable.") from exc
    except MailProviderExecutionError as exc:
        logger.warning(
            "mail_write_provider_execution_failed",
            extra={**context, "error_type": type(exc).__name__, "exit_code": exc.exit_code, "timeout_seconds": exc.timeout_seconds},
        )
        raise HTTPException(status_code=502, detail="Mail provider execution failed.") from exc
    except MailOperationValidationError as exc:
        logger.warning("mail_write_rejected", extra={**context, "error_type": type(exc).__name__})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MailConfigNotFoundError as exc:
        logger.info("mail_write_config_not_found", extra=context)
        raise HTTPException(status_code=404, detail=f"Email config '{operation_config_id}' not found.") from exc
    except Exception as exc:
        logger.error("mail_write_operation_failed", extra={**context, "error_type": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Mail operation failed.") from exc


def _reject_unsupported_setup_action(provider: str, action: str) -> NoReturn:
    logger.info("mail_setup_action_unsupported", extra={"provider": provider, "action": action})
    raise HTTPException(status_code=501, detail="This setup action is not supported by the mail provider.")


class ProviderSetupActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SendPrepareRequest(BaseModel):
    recipients: list[str]
    subject: str
    content: str = ""
    content_html: str = ""
    config_id: int | None = None


class SendConfirmRequest(BaseModel):
    confirmation_token: str
    config_id: int | None = None


def _resolve_provider_record(mail_service, provider: str, config_id: int | None = None):
    store = mail_service._email_config_store
    if store is None:
        raise HTTPException(status_code=500, detail="Email config store unavailable.")

    if config_id is not None:
        try:
            record = store.get_by_id(config_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Email config '{config_id}' not found.") from exc
        if record.provider != provider:
            raise HTTPException(
                status_code=400,
                detail=f"Config {config_id} does not belong to provider {provider}.",
            )
        return record

    records = store.list_all()
    record = next((r for r in records if r.provider == provider and r.enabled), None)
    if record is None:
        return None
    return record


@router.get("/providers")
async def list_providers(request: Request):
    registry = request.app.state.mail_service._registry
    providers = []
    for key in registry.registered_keys():
        adapter = registry.resolve(key)
        providers.append(adapter.descriptor())
    return {"providers": providers}


@router.post("/providers/{provider}/status")
async def provider_status(provider: str, request: Request):
    mail_service = request.app.state.mail_service
    registry = mail_service._registry
    adapter = registry.get(provider)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Unknown provider: " + provider)
    record = await asyncio.to_thread(_resolve_provider_record, mail_service, provider)
    if record is None:
        return {"ok": False, "error": "no_enabled_config", "provider": provider}
    return await _run_provider_call(adapter.status, record)


@router.post("/providers/{provider}/setup-action")
async def provider_setup_action(
    provider: str, body: ProviderSetupActionRequest, request: Request
):
    mail_service = request.app.state.mail_service
    registry = mail_service._registry
    adapter = registry.get(provider)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Unknown provider: " + provider)

    if body.action in {
        "status",
        "auth_status",
        "auth_login",
        "auth_login_status",
        "whoami",
        "provision_inbox",
    }:
        config_id = body.payload.get("config_id")
        if config_id is not None and not isinstance(config_id, int):
            try:
                config_id = int(config_id)
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Invalid config_id.") from exc

        record = await asyncio.to_thread(
            _resolve_provider_record,
            mail_service,
            provider,
            config_id,
        )
        if record is None:
            if body.action == "provision_inbox":
                logger.warning("mail_inbox_provision_config_missing", extra={"provider": provider})
                raise HTTPException(status_code=409, detail="No enabled mailbox configuration for provisioning.")
            if body.action == "auth_login":
                logger.warning("mail_auth_config_missing", extra={"provider": provider, "action": body.action})
                raise HTTPException(status_code=409, detail="No enabled mailbox configuration for authorization.")
            return {"ok": False, "error": "no_enabled_config", "provider": provider}

        # Existing Tencent records created before mailbox-isolated credentials
        # were introduced are migrated lazily when the user authorizes again.
        if (
            body.action == "auth_login"
            and provider == "tencent_agently"
            and config_id is not None
        ):
            record = await asyncio.to_thread(
                mail_service._email_config_store.ensure_tencent_credential_profile,
                config_id,
            )

        if body.action == "status":
            return await _run_provider_call(adapter.status, record)

        if body.action == "provision_inbox":
            if not adapter.supports(MailCapability.PROVISION_INBOX):
                logger.info("mail_inbox_provision_unsupported", extra={"provider": provider})
                raise HTTPException(status_code=501, detail="Inbox provisioning is not supported by this provider.")
            try:
                result = await _run_provider_call(
                    adapter.provision_inbox,
                    record,
                    body.payload.get("options") or {},
                )
            except MailProviderHTTPError as exc:
                logger.warning(
                    "mail_provider_provision_failed",
                    extra={"provider": provider, "upstream_status": exc.status_code},
                )
                raise HTTPException(
                    status_code=502,
                    detail=f"Mail provider inbox provisioning failed (upstream HTTP {exc.status_code}).",
                ) from exc
            except httpx.RequestError as exc:
                logger.warning(
                    "mail_provider_provision_transport_failed",
                    extra={"provider": provider, "error_type": type(exc).__name__},
                )
                raise HTTPException(status_code=502, detail="Mail provider inbox provisioning failed.") from exc
            except RuntimeError as exc:
                logger.error(
                    "mail_inbox_provision_failed",
                    extra={"provider": provider, "error_type": type(exc).__name__},
                )
                raise HTTPException(status_code=500, detail="Inbox provisioning failed.") from exc
            if result.get("ok") is False:
                logger.warning("mail_provider_provision_rejected", extra={"provider": provider})
                raise HTTPException(status_code=502, detail="Mail provider inbox provisioning failed.")
            mailbox_id = str(result.get("mailbox_id") or "").strip()
            email = str(result.get("email") or record.sender_email or "").strip()
            if mailbox_id:
                extra = dict(record.extra_config or {})
                extra["mailbox_id"] = mailbox_id
                await asyncio.to_thread(
                    mail_service._email_config_store.update,
                    record.id,
                    EmailConfigUpdateRequest(
                        config_name=record.config_name,
                        provider=record.provider,
                        sender_email=email,
                        enabled=record.enabled,
                        is_default=record.is_default,
                        description=record.description,
                        extra_config=extra,
                    ),
                )
            return result

        if body.action == "auth_status":
            if hasattr(adapter, "auth_status"):
                return await _run_provider_call(adapter.auth_status, record)
            return await _run_provider_call(adapter.status, record)

        if body.action == "auth_login":
            if hasattr(adapter, "auth_login"):
                return await _run_mail_operation("auth_login", adapter.auth_login, record, operation_config_id=record.id)
            _reject_unsupported_setup_action(provider, body.action)

        if body.action == "auth_login_status":
            session_id = str(body.payload.get("session_id") or "").strip()
            if not session_id:
                raise HTTPException(status_code=400, detail="session_id is required.")
            if hasattr(adapter, "auth_login_session_status"):
                return await _run_provider_call(
                    adapter.auth_login_session_status,
                    record,
                    session_id,
                )
            _reject_unsupported_setup_action(provider, body.action)

        if body.action == "whoami":
            if hasattr(adapter, "whoami"):
                return await _run_provider_call(adapter.whoami, record)
            _reject_unsupported_setup_action(provider, body.action)

    raise HTTPException(status_code=400, detail=f"Unsupported setup action: {body.action}")


@router.post("/test-send/prepare")
async def test_send_prepare(body: SendPrepareRequest, request: Request):
    mail_service = request.app.state.mail_service
    return await _run_mail_write(
        "prepare_send",
        mail_service.send,
        body.recipients,
        body.subject,
        body.content,
        body.content_html,
        config_id=body.config_id,
    )


@router.post("/test-send/confirm")
async def test_send_confirm(body: SendConfirmRequest, request: Request):
    mail_service = request.app.state.mail_service
    return await _run_mail_write(
        "confirm_send",
        mail_service.confirm,
        "send",
        body.confirmation_token,
        config_id=body.config_id,
    )


@router.post("/webhooks/{provider}")
async def handle_webhook(provider: str, request: Request):
    mail_service = request.app.state.mail_service
    registry = mail_service._registry
    adapter = registry.get(provider)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Unknown provider: " + provider)

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning(
            "mail_webhook_invalid_json",
            extra={"provider": provider, "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=400, detail="Invalid JSON webhook payload.") from exc

    handler = getattr(adapter, "handle_webhook", None)
    capability_declared = adapter.supports(MailCapability.WEBHOOK)
    if not capability_declared or not callable(handler):
        logger.warning(
            "mail_webhook_handler_unavailable",
            extra={
                "provider": provider,
                "capability_declared": capability_declared,
                "handler_available": callable(handler),
            },
        )
        raise HTTPException(
            status_code=501,
            detail=f"Webhook handling is not implemented for provider '{provider}'.",
        )

    try:
        result = await _run_provider_call(handler, body)
    except Exception as exc:
        logger.error(
            "mail_webhook_handler_failed",
            extra={"provider": provider, "error_type": type(exc).__name__},
        )
        raise

    logger.info("mail_webhook_handled", extra={"provider": provider})
    return result
