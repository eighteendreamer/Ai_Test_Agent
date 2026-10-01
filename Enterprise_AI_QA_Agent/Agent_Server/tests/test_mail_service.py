"""Regression tests for the global multi-provider Agent mailbox."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.api.routes.mail import ProviderSetupActionRequest, provider_setup_action, router as mail_router
from src.application.mail import MailService, build_default_mail_provider_registry
from src.application.mail.contracts import (
    CapabilityNotSupported,
    MailCapability,
    MailProviderAdapter,
    MailSendRequest,
    MailSendResult,
)
from src.application.mail.provider_registry import MailProviderRegistry
from src.infrastructure.mail.providers.tencent_agently import TencentAgentlyMailAdapter
from src.infrastructure.mail.providers.agentmail import AgentMailAdapter
from src.infrastructure.mail.providers.dead_simple_email import DeadSimpleEmailAdapter
from src.infrastructure.mail.providers.openmail import OpenMailAdapter
from src.infrastructure.mail.providers.rest_base import MailProviderHTTPError
from src.infrastructure.mail.providers.robotomail import RobotomailAdapter
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.schemas.email_config import EmailConfigRecord, EmailConfigUpdateRequest


def _record(**overrides) -> EmailConfigRecord:
    base = dict(
        config_name="cfg",
        provider="tencent_agently",
        owner_agent_key="global",
        sender_email="agent@example.com",
        enabled=True,
        is_default=True,
    )
    base.update(overrides)
    return EmailConfigRecord(**base)


class _StubStore:
    def __init__(self, records):
        self._records = records

    def list_all(self):
        return list(self._records)

    def get_by_id(self, config_id):
        return next(r for r in self._records if r.id == config_id)


class _CapturingAdapter(MailProviderAdapter):
    provider_key = "capture"

    def __init__(self):
        self.sent = None

    def send(self, record, request):
        self.sent = (record, request)
        return MailSendResult(
            sent=True,
            provider=record.provider,
            from_email=record.sender_email,
            recipient_count=len(request.recipients),
        )


# --- native provider routing ---------------------------------------------------


def test_registry_contains_all_agent_mail_providers():
    r = build_default_mail_provider_registry()
    assert r.registered_keys() == [
        "agenticmail", "agentmail", "aws_agent_mailbox", "dead_simple_email",
        "openmail", "robotomail", "tencent_agently",
    ]
    for key in ("aliyun", "sendgrid", "smtp", "unknown"):
        with pytest.raises(ValueError, match="Unknown mail provider"):
            r.resolve(key)


def test_rest_provider_send_uses_system_two_phase_confirmation():
    adapter = _CapturingAdapter()
    registry = MailProviderRegistry([adapter], send_fallback=adapter)
    store = _StubStore([_record(provider="capture")])
    svc = MailService(store, registry)

    result = svc.send(["a@x.com", "b@x.com"], "Subj", "text", "<p>html</p>")

    assert result["sent"] is False
    assert result["confirmation_required"] is True
    assert result["provider"] == "capture"
    assert adapter.sent is None

    committed = svc.confirm("send", result["confirmation_token"])

    assert committed["sent"] is True
    assert committed["recipient_count"] == 2
    _, request = adapter.sent
    assert isinstance(request, MailSendRequest)
    assert request.content_html == "<p>html</p>"


def test_active_record_prefers_default_then_enabled():
    store = _StubStore(
        [
            _record(id=1, enabled=True, is_default=False),
            _record(id=2, enabled=True, is_default=True),
        ]
    )
    svc = MailService(store, build_default_mail_provider_registry())
    assert svc._resolve_active_record().id == 2


def test_send_without_store_raises():
    svc = MailService(None, build_default_mail_provider_registry())
    with pytest.raises(RuntimeError, match="Email config store is not available"):
        svc.send(["a@x.com"], "s", "c", "")


def test_no_enabled_config_raises():
    store = _StubStore([_record(enabled=False, is_default=False)])
    svc = MailService(store, build_default_mail_provider_registry())
    with pytest.raises(RuntimeError, match="No globally active Agent mailbox"):
        svc.send(["a@x.com"], "s", "c", "")


def test_inactive_mailbox_cannot_be_used_by_config_id():
    adapter = _CapturingAdapter()
    store = _StubStore([_record(id=7, provider="capture", enabled=False, is_default=False)])
    svc = MailService(store, MailProviderRegistry([adapter]))
    with pytest.raises(RuntimeError, match="is not active"):
        svc.send(["a@x.com"], "s", "c", "", config_id=7)


def test_non_default_enabled_record_cannot_bypass_global_active_mailbox():
    adapter = _CapturingAdapter()
    active = _record(id=1, provider="capture", enabled=True, is_default=True)
    stray = _record(id=2, provider="capture", enabled=True, is_default=False)
    svc = MailService(_StubStore([active, stray]), MailProviderRegistry([adapter]))
    with pytest.raises(RuntimeError, match="is not active"):
        svc.send(["a@x.com"], "s", "c", "", config_id=2)


def test_confirmation_is_invalid_after_global_mailbox_switch():
    adapter = _CapturingAdapter()
    first = _record(id=1, provider="capture", enabled=True, is_default=True)
    second = _record(id=2, provider="capture", enabled=False, is_default=False)
    store = _StubStore([first, second])
    svc = MailService(store, MailProviderRegistry([adapter]))
    preview = svc.send(["a@x.com"], "s", "c", "")
    first.enabled = first.is_default = False
    second.enabled = second.is_default = True
    with pytest.raises(RuntimeError, match="active mailbox changed"):
        svc.confirm("send", preview["confirmation_token"])


def test_agentmail_uses_official_inbox_scoped_v0_send_route(monkeypatch: pytest.MonkeyPatch):
    adapter = AgentMailAdapter()
    record = _record(
        provider="agentmail",
        api_key="secret",
        extra_config={"mailbox_id": "inbox_123"},
    )
    captured = {}

    def fake_request(method, current, path, *, json_body=None, params=None):
        captured.update(method=method, path=path, body=json_body)
        return {"message_id": "msg_123", "thread_id": "thr_123"}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.send(record, MailSendRequest(
        recipients=["to@example.com"], subject="Hello", content="text", content_html="<p>html</p>"
    ))

    assert captured["method"] == "POST"
    assert captured["path"] == "/v0/inboxes/inbox_123/messages/send"
    assert captured["body"]["text"] == "text"
    assert captured["body"]["html"] == "<p>html</p>"
    assert result.message_id == "msg_123"


# --- capabilities --------------------------------------------------------------


def test_capability_not_supported_default_methods_raise():
    adapter = _CapturingAdapter()
    with pytest.raises(CapabilityNotSupported):
        adapter.list_messages(_record())


def test_status_reports_capabilities():
    adapter = _CapturingAdapter()
    registry = MailProviderRegistry([adapter])
    store = _StubStore([_record(provider="capture")])
    svc = MailService(store, registry)
    status = svc.status()
    assert status["ok"] is True
    assert status["capabilities"] == [MailCapability.SEND.value]


def test_tencent_agently_prefers_windows_cmd_wrapper(monkeypatch: pytest.MonkeyPatch):
    adapter = TencentAgentlyMailAdapter()
    calls: list[str] = []

    def fake_which(name: str):
        calls.append(name)
        if name == "agently-cli.cmd":
            return r"E:\Nvm\Node_Config\node_global\agently-cli.cmd"
        return None

    monkeypatch.setattr("src.infrastructure.mail.providers.tencent_agently.shutil.which", fake_which)
    monkeypatch.setattr("src.infrastructure.mail.providers.tencent_agently.os.name", "nt")

    path = adapter._cli_path(_record(provider="tencent_agently", extra_config={}))

    assert path == r"E:\Nvm\Node_Config\node_global\agently-cli.cmd"
    assert calls[0] == "agently-cli.cmd"


def test_tencent_agently_ignores_workstation_specific_cli_path(monkeypatch: pytest.MonkeyPatch):
    adapter = TencentAgentlyMailAdapter()
    record = _record(
        provider="tencent_agently",
        extra_config={"cli_path": r"E:\custom\agently-cli.cmd"},
    )
    monkeypatch.setattr(adapter, "_resolve_cli_from_path", lambda _: None)

    assert adapter._cli_path(record) == "agently-cli"


def test_tencent_agently_auth_status_reads_nested_data(monkeypatch: pytest.MonkeyPatch):
    adapter = TencentAgentlyMailAdapter()

    def fake_run_cli(record, args, *, timeout=30):
        assert args == ["auth", "status"]
        return {
            "ok": True,
            "data": {
                "logged_in": True,
                "status": "logged_in",
                "token_status": "valid",
                "storage": "Windows DPAPI",
            },
        }

    monkeypatch.setattr(adapter, "_run_cli", fake_run_cli)

    result = adapter.auth_status(_record(provider="tencent_agently"))

    assert result["ok"] is True
    assert result["logged_in"] is True
    assert result["auth_status"] == "logged_in"
    assert result["token_status"] == "valid"
    assert result["storage"] == "Windows DPAPI"


def test_tencent_agently_whoami_uses_primary_alias_email(monkeypatch: pytest.MonkeyPatch):
    adapter = TencentAgentlyMailAdapter()

    def fake_run_cli(record, args, *, timeout=30):
        assert args == ["+me"]
        return {
            "ok": True,
            "data": {
                "aliases": [
                    {"email": "secondary@agent.qq.com", "is_primary": False},
                    {"email": "primary@agent.qq.com", "is_primary": True},
                ],
                "scopes": ["mail:read"],
            },
        }

    monkeypatch.setattr(adapter, "_run_cli", fake_run_cli)

    result = adapter.whoami(_record(provider="tencent_agently"))

    assert result["ok"] is True
    assert result["email"] == "primary@agent.qq.com"
    assert len(result["aliases"]) == 2
    assert result["scopes"] == ["mail:read"]


def test_tencent_agently_extracts_auth_url_verbatim():
    adapter = TencentAgentlyMailAdapter()
    url = (
        "https://agent.qq.com/page/oauth?oauth_type=device"
        "&user_code=uc_akJ7d7s1TmygdmNFNPnZ8AAA"
    )

    assert adapter._extract_auth_url(f"请点击以下链接登录并授权邮箱：\n{url}\n") == url


# --- documented REST provider contracts --------------------------------------


def _rest_record(provider: str, mailbox_id: str = "mbx_123") -> EmailConfigRecord:
    return _record(
        provider=provider,
        config_name="Agent Inbox",
        sender_email="agent@example.com",
        api_key="secret",
        extra_config={"mailbox_id": mailbox_id},
    )


def _rest_request(**overrides) -> MailSendRequest:
    values = {
        "recipients": ["to@example.com"],
        "subject": "Hello",
        "content": "plain",
        "content_html": "<p>html</p>",
    }
    values.update(overrides)
    return MailSendRequest(**values)


@pytest.mark.parametrize(
    ("adapter", "base_url", "unsupported"),
    [
        (AgentMailAdapter(), "https://api.agentmail.to", set()),
        (OpenMailAdapter(), "https://api.openmail.sh", {MailCapability.FORWARD, MailCapability.TRASH}),
        (DeadSimpleEmailAdapter(), "https://api.deadsimple.email", {MailCapability.TRASH}),
        (RobotomailAdapter(), "https://api.robotomail.com", {MailCapability.FORWARD, MailCapability.TRASH}),
    ],
)
def test_documented_provider_descriptors_and_capabilities(adapter, base_url, unsupported):
    descriptor = adapter.descriptor()
    assert descriptor["default_base_url"] == base_url
    assert descriptor["configuration_fields"] == ["api_key", "mailbox_id", "base_url"]
    assert "routes" not in descriptor["configuration_fields"]
    for capability in unsupported:
        assert capability not in adapter.capabilities()


def test_agentmail_documented_search_status_and_attachment_contract(monkeypatch):
    adapter = AgentMailAdapter()
    record = _rest_record("agentmail", "inbox_123")
    calls = []

    def fake_request(method, current, path, *, json_body=None, params=None, extra_headers=None):
        calls.append((method, path, params))
        if path.endswith("/search"):
            return {"messages": [{"message_id": "msg_1", "subject": "Found"}]}
        if path.endswith("/attachments/att_1"):
            return {"download_url": "https://files.example/attachment"}
        return {"inbox_id": "inbox_123", "email": "real@agentmail.to"}

    monkeypatch.setattr(adapter, "_request", fake_request)
    monkeypatch.setattr(adapter, "_download_url", lambda current, url: {"ok": True, "url": url})

    messages = adapter.search_messages(record, "needle", {"limit": 5})
    attachment = adapter.download_attachment(record, "msg_1", "att_1")
    status = adapter.status(record)

    assert messages[0].message_id == "msg_1"
    assert calls[0] == (
        "GET",
        "/v0/inboxes/inbox_123/messages/search",
        {"limit": 5, "q": "needle"},
    )
    assert attachment["url"] == "https://files.example/attachment"
    assert status["email"] == "real@agentmail.to"


def test_openmail_documented_send_and_message_contract(monkeypatch):
    adapter = OpenMailAdapter()
    record = _rest_record("openmail", "inbox_123")
    calls = []

    def fake_request(method, current, path, *, json_body=None, params=None, extra_headers=None):
        calls.append((method, path, json_body, extra_headers))
        if method == "GET":
            return {"data": [{
                "id": "msg_1", "threadId": "thr_1",
                "fromAddr": "sender@example.com", "toAddr": "agent@example.com",
                "subject": "Hi", "bodyText": "Body", "bodyHtml": "<p>Body</p>",
                "createdAt": "2026-01-01T00:00:00Z",
            }]}
        return {"messageId": "msg_123", "threadId": "thr_123"}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.send(record, _rest_request())
    message = adapter.list_messages(record)[0]

    method, path, body, headers = calls[0]
    assert method == "POST"
    assert path == "/v1/inboxes/inbox_123/send"
    assert body == {
        "to": "to@example.com", "subject": "Hello", "body": "plain",
        "bodyHtml": "<p>html</p>",
    }
    assert headers and headers["Idempotency-Key"]
    assert result.message_id == "msg_123"
    assert message.message_id == "msg_1"
    assert message.thread_id == "thr_1"
    assert message.from_email == "sender@example.com"
    assert message.to == ["agent@example.com"]

    with pytest.raises(RuntimeError, match="exactly one recipient"):
        adapter.send(record, _rest_request(recipients=["one@example.com", "two@example.com"]))


def test_openmail_can_bind_existing_inbox_by_email(monkeypatch):
    adapter = OpenMailAdapter()
    record = _rest_record("openmail", mailbox_id="")
    calls = []

    def fake_request(method, current, path, *, json_body=None, params=None, extra_headers=None):
        calls.append((method, path, params))
        return {
            "data": [
                {"id": "inbox_1", "address": "first@openmail.sh"},
                {"id": "inbox_2", "address": "gleamopal4609@openmail.sh"},
            ]
        }

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.provision_inbox(
        record, {"existing_email": "GleamOpal4609@openmail.sh"}
    )

    assert calls == [("GET", "/v1/inboxes", {"limit": 100})]
    assert result["mailbox_id"] == "inbox_2"
    assert result["email"] == "gleamopal4609@openmail.sh"
    assert result["bound_existing"] is True


def test_rest_provider_http_error_includes_vendor_response():
    adapter = OpenMailAdapter()
    response = httpx.Response(
        422,
        request=httpx.Request("POST", "https://api.openmail.sh/v1/inboxes"),
        json={"error": "inbox_limit_reached", "message": "Upgrade to create more."},
    )

    with pytest.raises(MailProviderHTTPError, match="HTTP 422: inbox_limit_reached - Upgrade") as caught:
        adapter._raise_provider_error(response)
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_provider_setup_action_reports_local_provision_failure_as_server_error():
    class FailingProvisionAdapter(_CapturingAdapter):
        provider_key = "openmail"

        def capabilities(self):
            return {MailCapability.SEND, MailCapability.PROVISION_INBOX}

        def provision_inbox(self, record, options=None):
            raise RuntimeError("OpenMail API returned HTTP 422: inbox_limit_reached")

    record = _record(
        id=8,
        provider="openmail",
        api_key="example-key",
        enabled=False,
        is_default=False,
    )
    service = MailService(_StubStore([record]), MailProviderRegistry([FailingProvisionAdapter()]))
    app = FastAPI()
    app.state.mail_service = service
    app.include_router(mail_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/providers/openmail/setup-action",
            json={"action": "provision_inbox", "payload": {"config_id": 8, "options": {}}},
        )

    assert response.status_code == 500
    assert "inbox_limit_reached" not in response.text


@pytest.mark.asyncio
async def test_provider_setup_action_reports_upstream_provision_failure_as_gateway_error(monkeypatch):
    record = _record(id=8, provider="openmail", api_key="example-key", enabled=False, is_default=False)
    service = MailService(_StubStore([record]), MailProviderRegistry([OpenMailAdapter()]))
    app = FastAPI()
    app.state.mail_service = service
    app.include_router(mail_router, prefix="/api/v1")

    def fail_upstream_request(_method, _url, **_kwargs):
        return httpx.Response(
            422,
            request=httpx.Request("POST", "https://api.openmail.sh/v1/inboxes"),
            json={"error": "inbox_limit_reached", "message": "Upgrade to create more."},
        )

    monkeypatch.setattr("src.infrastructure.mail.providers.rest_base.httpx.request", fail_upstream_request)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/providers/openmail/setup-action",
            json={"action": "provision_inbox", "payload": {"config_id": 8, "options": {}}},
        )

    assert response.status_code == 502
    assert "upstream HTTP 422" in response.text
    assert "inbox_limit_reached" not in response.text


@pytest.mark.asyncio
async def test_provider_provision_transport_failure_is_a_gateway_error(monkeypatch):
    record = _record(id=8, provider="openmail", api_key="example-key", enabled=False, is_default=False)
    service = MailService(_StubStore([record]), MailProviderRegistry([OpenMailAdapter()]))
    app = FastAPI()
    app.state.mail_service = service
    app.include_router(mail_router, prefix="/api/v1")

    def fail_upstream_request(_method, _url, **_kwargs):
        request = httpx.Request("POST", "https://api.openmail.sh/v1/inboxes")
        raise httpx.ReadTimeout("private transport error", request=request)

    monkeypatch.setattr("src.infrastructure.mail.providers.rest_base.httpx.request", fail_upstream_request)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/providers/openmail/setup-action",
            json={"action": "provision_inbox", "payload": {"config_id": 8}},
        )

    assert response.status_code == 502
    assert "private transport error" not in response.text


@pytest.mark.asyncio
async def test_status_without_enabled_config_remains_a_readable_state():
    service = MailService(_StubStore([]), MailProviderRegistry([_CapturingAdapter()]))
    app = FastAPI()
    app.state.mail_service = service
    app.include_router(mail_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        status = await client.post(
            "/api/v1/mail/providers/capture/setup-action",
            json={"action": "status", "payload": {}},
        )
        provision = await client.post(
            "/api/v1/mail/providers/capture/setup-action",
            json={"action": "provision_inbox", "payload": {}},
        )

    assert status.status_code == 200
    assert status.json()["ok"] is False
    assert provision.status_code == 409


@pytest.mark.asyncio
async def test_provision_without_provider_capability_returns_not_implemented():
    record = _record(id=8, provider="capture")
    service = MailService(_StubStore([record]), MailProviderRegistry([_CapturingAdapter()]))
    app = FastAPI()
    app.state.mail_service = service
    app.include_router(mail_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/providers/capture/setup-action",
            json={"action": "provision_inbox", "payload": {"config_id": 8}},
        )

    assert response.status_code == 501


@pytest.mark.asyncio
async def test_provider_provision_rejection_is_not_reported_as_success():
    class RejectingProvisionAdapter(_CapturingAdapter):
        def capabilities(self):
            return {MailCapability.SEND, MailCapability.PROVISION_INBOX}

        def provision_inbox(self, record, options=None):
            return {"ok": False, "error": "private provider payload"}

    record = _record(id=8, provider="capture")
    service = MailService(_StubStore([record]), MailProviderRegistry([RejectingProvisionAdapter()]))
    app = FastAPI()
    app.state.mail_service = service
    app.include_router(mail_router, prefix="/api/v1")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/mail/providers/capture/setup-action",
            json={"action": "provision_inbox", "payload": {"config_id": 8}},
        )

    assert response.status_code == 502
    assert "private provider payload" not in response.text


def test_provider_status_does_not_block_the_async_event_loop():
    class SlowStatusAdapter(_CapturingAdapter):
        provider_key = "openmail"

        def status(self, record):
            time.sleep(0.15)
            return {"ok": True, "provider": self.provider_key}

    record = _record(
        id=9,
        provider="openmail",
        api_key="secret",
        enabled=False,
        is_default=False,
    )
    service = MailService(
        _StubStore([record]),
        MailProviderRegistry([SlowStatusAdapter()]),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(mail_service=service))
    )

    async def exercise():
        started_at = time.perf_counter()
        status_task = asyncio.create_task(
            provider_setup_action(
                "openmail",
                ProviderSetupActionRequest(
                    action="status",
                    payload={"config_id": 9},
                ),
                request,
            )
        )
        await asyncio.sleep(0.02)
        event_loop_delay = time.perf_counter() - started_at
        result = await status_task
        return event_loop_delay, result

    event_loop_delay, result = asyncio.run(exercise())
    assert event_loop_delay < 0.08
    assert result == {"ok": True, "provider": "openmail"}


def test_dead_simple_email_uses_documented_snake_case_operations(monkeypatch):
    adapter = DeadSimpleEmailAdapter()
    record = _rest_record("dead_simple_email")
    calls = []

    def fake_request(method, current, path, *, json_body=None, params=None, extra_headers=None):
        calls.append((method, path, json_body))
        return {"message_id": "msg_1"}

    monkeypatch.setattr(adapter, "_request", fake_request)
    adapter.send(record, _rest_request(cc=["cc@example.com"]))
    adapter.reply(record, "msg_0", _rest_request(recipients=[]))
    adapter.forward(record, "msg_0", _rest_request())

    assert calls[0] == (
        "POST", "/v1/inboxes/mbx_123/messages",
        {
            "to": ["to@example.com"], "subject": "Hello", "text_body": "plain",
            "html_body": "<p>html</p>", "cc": ["cc@example.com"],
        },
    )
    assert calls[1][1].endswith("/msg_0/reply")
    assert calls[1][2] == {"text_body": "plain", "html_body": "<p>html</p>"}
    assert calls[2][1].endswith("/msg_0/forward")
    assert calls[2][2]["to"] == ["to@example.com"]


def test_robotomail_uses_documented_camel_case_and_rfc_reply(monkeypatch):
    adapter = RobotomailAdapter()
    record = _rest_record("robotomail")
    calls = []

    def fake_request(method, current, path, *, json_body=None, params=None, extra_headers=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"message": {
                "id": "msg_0", "messageId": "<rfc-5322@example.com>",
                "fromAddress": "sender@example.com", "toAddresses": ["agent@example.com"],
                "subject": "Original",
            }}
        return {"message": {"id": "msg_1"}}

    monkeypatch.setattr(adapter, "_request", fake_request)
    adapter.send(record, _rest_request(attachments=[{"id": "att_1"}]))
    reply = adapter.reply(record, "msg_0", _rest_request(recipients=[]))

    assert calls[0][1] == "/v1/mailboxes/mbx_123/messages"
    assert calls[0][2]["bodyText"] == "plain"
    assert calls[0][2]["bodyHtml"] == "<p>html</p>"
    assert calls[0][2]["attachments"] == ["att_1"]
    assert calls[2][2]["inReplyTo"] == "<rfc-5322@example.com>"
    assert calls[2][2]["to"] == ["sender@example.com"]
    assert reply.message_id == "msg_1"


def test_robotomail_can_bind_existing_mailbox_with_scoped_key(monkeypatch):
    adapter = RobotomailAdapter()
    record = _rest_record("robotomail", mailbox_id="")
    calls = []

    def fake_request(method, current, path, *, json_body=None, params=None, extra_headers=None):
        calls.append((method, path, json_body))
        return {
            "mailboxes": [
                {"id": "mailbox_1", "fullAddress": "first@robotomail.co"},
                {"id": "mailbox_2", "fullAddress": "eighteen@robotomail.co"},
            ]
        }

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.provision_inbox(
        record, {"existing_email": "Eighteen@robotomail.co"}
    )

    assert calls == [("GET", "/v1/mailboxes", None)]
    assert result["mailbox_id"] == "mailbox_2"
    assert result["email"] == "eighteen@robotomail.co"
    assert result["bound_existing"] is True


def test_robotomail_scoped_key_create_error_is_actionable(monkeypatch):
    adapter = RobotomailAdapter()
    record = _rest_record("robotomail", mailbox_id="")

    def fake_request(*args, **kwargs):
        raise RuntimeError(
            "Robotomail API returned HTTP 403: This operation requires a full-access API key"
        )

    monkeypatch.setattr(adapter, "_request", fake_request)

    with pytest.raises(RuntimeError, match="绑定已有 Mailbox"):
        adapter.provision_inbox(record)


@pytest.mark.parametrize(
    ("adapter", "record", "expected_path", "response", "email"),
    [
        (OpenMailAdapter(), _rest_record("openmail"), "/v1/inboxes/mbx_123", {"address": "agent@openmail.sh"}, "agent@openmail.sh"),
        (DeadSimpleEmailAdapter(), _rest_record("dead_simple_email"), "/v1/inboxes/mbx_123", {"inbox": {"email": "agent@deadsimple.email"}}, "agent@deadsimple.email"),
        (RobotomailAdapter(), _rest_record("robotomail"), "/v1/mailboxes/mbx_123", {"mailbox": {"fullAddress": "agent@robotomail.co"}}, "agent@robotomail.co"),
    ],
)
def test_rest_provider_status_uses_real_mailbox_lookup(
    monkeypatch, adapter, record, expected_path, response, email
):
    captured = {}

    def fake_request(method, current, path, *, json_body=None, params=None, extra_headers=None):
        captured.update(method=method, path=path)
        return response

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.status(record)

    assert captured == {"method": "GET", "path": expected_path}
    assert result["ok"] is True
    assert result["email"] == email


def test_email_config_internal_update_preserves_provider_credentials():
    store = object.__new__(MySQLEmailConfigStore)
    existing = _rest_record("openmail")
    existing.secret_key = "secondary-secret"

    merged = store._merge_update(existing, EmailConfigUpdateRequest(
        config_name=existing.config_name,
        provider=existing.provider,
        sender_email="new@openmail.sh",
        enabled=False,
        is_default=False,
        extra_config={"mailbox_id": "new_mailbox"},
    ))

    assert merged.api_key == "secret"
    assert merged.secret_key == "secondary-secret"
    assert merged.sender_email == "new@openmail.sh"
    assert merged.extra_config["mailbox_id"] == "new_mailbox"


def test_email_config_provider_switch_does_not_reuse_old_credentials():
    store = object.__new__(MySQLEmailConfigStore)
    existing = _rest_record("openmail")

    merged = store._merge_update(existing, EmailConfigUpdateRequest(
        config_name=existing.config_name,
        provider="agentmail",
        sender_email="",
        enabled=False,
        is_default=False,
        extra_config={},
    ))

    assert merged.api_key is None
