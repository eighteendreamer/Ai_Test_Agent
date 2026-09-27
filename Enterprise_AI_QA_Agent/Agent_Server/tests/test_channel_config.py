from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from src.domain.channel_strategies.feishu import FeishuChannelStrategy
from src.domain.channel_strategies.weixin import WeixinChannelStrategy
from src.infrastructure.channel_config_store import (
    ChannelCredentialCodec,
    MySQLChannelConfigStore,
)
from src.schemas.channel_config import (
    ChannelAdvancedSettings,
    ChannelConfigCreateRequest,
    ChannelConfigRecord,
    clean_public_config,
    compute_channel_status,
)


def test_channel_status_uses_required_public_fields_and_credentials() -> None:
    assert compute_channel_status(
        domain="qq",
        enabled=True,
        public_config={},
        credential_flags={"app_secret": True},
    ) == "unconfigured"
    assert compute_channel_status(
        domain="qq",
        enabled=True,
        public_config={"app_id": "123"},
        credential_flags={"app_secret": False},
    ) == "unconfigured"
    assert compute_channel_status(
        domain="qq",
        enabled=False,
        public_config={"app_id": "123"},
        credential_flags={"app_secret": True},
    ) == "disabled"
    assert compute_channel_status(
        domain="qq",
        enabled=True,
        public_config={"app_id": "123"},
        credential_flags={"app_secret": True},
    ) == "configured"


def test_channel_status_accepts_qr_pairing_without_manual_secret() -> None:
    assert compute_channel_status(
        domain="weixin",
        enabled=True,
        public_config={
            "auth_method": "qr_pairing",
            "paired_at": "2026-07-14T10:30:00+00:00",
            "paired_device": "iPhone",
        },
        credential_flags={"token": False},
    ) == "configured"


def test_qq_channel_status_does_not_accept_qr_pairing() -> None:
    assert compute_channel_status(
        domain="qq",
        enabled=True,
        public_config={
            "auth_method": "qr_pairing",
            "paired_at": "2026-07-14T10:30:00+00:00",
            "paired_device": "iPhone",
        },
        credential_flags={"app_secret": False},
    ) == "unconfigured"


def test_feishu_and_lark_share_provider_but_keep_domains() -> None:
    payload = ChannelConfigCreateRequest(
        config_name="Lark Config",
        provider="feishu",
        domain="lark",
        public_config={"app_id": "cli_x", "ignored": "nope"},
        credentials={"app_secret": "secret", "ignored": "nope"},
    )

    assert payload.provider == "feishu"
    assert payload.domain == "lark"
    assert payload.public_config == {"app_id": "cli_x", "connection_mode": "event_callback"}
    assert payload.credentials == {"app_secret": "secret"}


def test_channel_strategy_normalizes_platform_specific_public_config() -> None:
    assert clean_public_config("qq", {"app_id": " 1024 ", "sandbox_mode": "true"}) == {
        "app_id": "1024",
        "sandbox_mode": False,
    }
    assert clean_public_config("feishu", {"app_id": " cli_x ", "connection_mode": "bad"}) == {
        "app_id": "cli_x",
        "connection_mode": "event_callback",
    }
    assert clean_public_config("weixin", {"account_id": " gh_demo ", "app_id": "ignored"}) == {
        "account_id": "gh_demo",
        "api_base": "",
    }


def test_feishu_strategy_starts_official_registration_qr(monkeypatch) -> None:
    calls = {}

    def fake_post(self, base_url, body):
        calls["base_url"] = base_url
        calls["body"] = body
        return {
            "device_code": "device-1",
            "verification_uri_complete": "https://accounts.feishu.cn/open-apis/authen/v1/index?x=1",
            "user_code": "ABCD",
            "interval": 7,
            "expire_in": 300,
        }, 200

    monkeypatch.setattr(FeishuChannelStrategy, "_post_registration_result", fake_post)

    result = FeishuChannelStrategy(domain="feishu").start_pairing(session_id="session-1")

    assert calls["base_url"] == "https://accounts.feishu.cn"
    assert calls["body"]["action"] == "begin"
    assert result["device_code"] == "device-1"
    assert result["interval"] == 7
    assert result["qr_payload"].startswith("https://accounts.feishu.cn/")
    assert "from=sdk" in result["qr_payload"]
    assert "source=go-sdk" in result["qr_payload"]


def test_weixin_strategy_starts_official_ilink_qr(monkeypatch) -> None:
    calls = {}

    def fake_get(self, base_url, endpoint):
        calls["base_url"] = base_url
        calls["endpoint"] = endpoint
        return {
            "qrcode": "wx-qr-token",
            "qrcode_img_content": "data:image/png;base64,abc",
        }

    monkeypatch.setattr(WeixinChannelStrategy, "_ilink_get", fake_get)

    result = WeixinChannelStrategy().start_pairing(session_id="session-1")

    assert calls["base_url"] == "https://ilinkai.weixin.qq.com"
    assert calls["endpoint"] == "/ilink/bot/get_bot_qrcode?bot_type=3"
    assert result["qr_payload"] == "data:image/png;base64,abc"
    assert result["qrcode"] == "wx-qr-token"


def test_channel_pair_validation_rejects_wrong_provider() -> None:
    with pytest.raises(ValueError):
        ChannelConfigCreateRequest(
            config_name="Wrong Provider",
            provider="qq",
            domain="lark",
        )


def test_channel_credentials_require_server_key() -> None:
    codec = ChannelCredentialCodec("")

    with pytest.raises(ValueError, match="CHANNEL_CREDENTIAL_ENCRYPTION_KEY"):
        codec.encrypt({"app_secret": "secret"})


def test_channel_credentials_require_fernet_key() -> None:
    with pytest.raises(ValueError, match="Fernet key"):
        ChannelCredentialCodec("local-dev-passphrase")


def test_channel_credentials_round_trip_with_fernet_key() -> None:
    codec = ChannelCredentialCodec(Fernet.generate_key().decode("ascii"))
    encrypted = codec.encrypt({"token": "secret-token"})

    assert encrypted != "secret-token"
    assert codec.decrypt(encrypted) == {"token": "secret-token"}


def test_channel_credentials_fail_closed_on_wrong_or_missing_key() -> None:
    encrypted = ChannelCredentialCodec(Fernet.generate_key().decode("ascii")).encrypt({"app_secret": "secret"})
    wrong_key = ChannelCredentialCodec(Fernet.generate_key().decode("ascii"))

    with pytest.raises(ValueError, match="decrypt"):
        wrong_key.decrypt(encrypted)
    with pytest.raises(ValueError, match="KEY"):
        ChannelCredentialCodec("").decrypt(encrypted)

    store = MySQLChannelConfigStore.__new__(MySQLChannelConfigStore)
    store._codec = wrong_key
    record = ChannelConfigRecord(
        config_name="QQ", provider="qq", domain="qq", enabled=True,
        public_config={"app_id": "123"}, credential_ciphertext=encrypted,
    )
    with pytest.raises(ValueError, match="decrypt"):
        store.to_public(record)


def test_channel_advanced_settings_round_trip(monkeypatch) -> None:
    from src.application.settings import settings_service as settings_service_module
    from src.application.settings.settings_service import SettingsService

    store_path = Path(__file__).resolve().parents[1] / "src" / "data" / f"channel_advanced_settings_test_{uuid.uuid4().hex}.json"
    monkeypatch.setattr(settings_service_module, "_CHANNEL_ADVANCED_SETTINGS_FILE", store_path)
    service = SettingsService.__new__(SettingsService)

    try:
        saved = service.update_channel_advanced_settings(
            ChannelAdvancedSettings(
                max_steps=7,
                debounce_ms=888,
                queue_mode="interrupt",
                queue_cap=4,
                queue_drop="old",
                allowlist={
                    "allow_all": True,
                    "qq_users": ["u1", "u1", " u2 "],
                },
                self_user_ids={"qq": "bot-1, bot-2"},
                routes=[
                    {
                        "platform": "qq",
                        "chat_type": "group",
                        "chat_id": "group-1",
                        "workspace_root": "G:/demo",
                    }
                ],
            )
        )
        loaded = service.get_channel_advanced_settings()

        assert saved.max_steps == 7
        assert loaded.max_steps == 7
        assert loaded.debounce_ms == 888
        assert loaded.queue_mode == "interrupt"
        assert loaded.queue_drop == "old"
        assert loaded.allowlist.allow_all is True
        assert loaded.allowlist.qq_users == ["u1", "u2"]
        assert loaded.self_user_ids.qq == ["bot-1", "bot-2"]
        assert loaded.routes[0].chat_id == "group-1"
    finally:
        store_path.unlink(missing_ok=True)
