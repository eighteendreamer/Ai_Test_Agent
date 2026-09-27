from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from scripts import rotate_channel_credentials as rotation
from src.core.config import Settings
from src.infrastructure.channel_config_store import ChannelCredentialCodec


class _Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        if sql.startswith("SELECT"):
            return
        assert sql.startswith("UPDATE") and params is not None
        new_token, config_id, old_token = params
        matches = [row for row in self.rows if row["id"] == config_id and row["credential_ciphertext"] == old_token]
        self.rowcount = len(matches)
        for row in matches:
            row["credential_ciphertext"] = new_token

    def fetchall(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.rows]


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._cursor = _Cursor(rows)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True


def _settings_with_key(key: str) -> Settings:
    settings = Settings()
    return settings.model_copy(update={
        "database": settings.database.model_copy(update={"channel_credential_encryption_key": key})
    })


def test_channel_credential_rotation_checks_then_reencrypts(monkeypatch) -> None:
    old_key = "legacy-passphrase-for-test"
    old_codec = rotation._legacy_codec(old_key)
    settings = _settings_with_key(Fernet.generate_key().decode("ascii"))
    new_codec = ChannelCredentialCodec(settings.database.channel_credential_encryption_key)
    old_token = old_codec.encrypt({"app_secret": "marker"})
    current_token = new_codec.encrypt({"app_secret": "current"})
    rows = [
        {"id": 1, "credential_ciphertext": old_token},
        {"id": 2, "credential_ciphertext": current_token},
    ]
    conn = _Connection(rows)
    monkeypatch.setattr(rotation, "mysql_raw_connection", lambda _: conn)

    assert rotation.rotate_channel_credentials(settings, old_key, apply=False) == {
        "encrypted_rows": 2, "already_current": 1, "to_rotate": 1, "rotated": 0,
    }
    assert rows[0]["credential_ciphertext"] == old_token
    assert not conn.committed

    assert rotation.rotate_channel_credentials(settings, old_key, apply=True) == {
        "encrypted_rows": 2, "already_current": 1, "to_rotate": 1, "rotated": 1,
    }
    assert conn.committed
    assert new_codec.decrypt(rows[0]["credential_ciphertext"]) == {"app_secret": "marker"}
    with pytest.raises(ValueError, match="decrypt"):
        old_codec.decrypt(rows[0]["credential_ciphertext"])


def test_channel_credential_rotation_rejects_unknown_ciphertext_before_writes(monkeypatch) -> None:
    old_key = "legacy-passphrase-for-test"
    old_token = rotation._legacy_codec(old_key).encrypt({"token": "marker"})
    rows = [
        {"id": 1, "credential_ciphertext": old_token},
        {"id": 2, "credential_ciphertext": "invalid-token"},
    ]
    conn = _Connection(rows)
    monkeypatch.setattr(rotation, "mysql_raw_connection", lambda _: conn)

    with pytest.raises(ValueError, match="Channel config 2"):
        rotation.rotate_channel_credentials(
            _settings_with_key(Fernet.generate_key().decode("ascii")), old_key, apply=True,
        )
    assert rows[0]["credential_ciphertext"] == old_token
    assert not conn.committed
