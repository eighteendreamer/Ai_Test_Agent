from __future__ import annotations

from datetime import datetime
import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

from src.core.config import Settings
from src.domain.channel_strategies import channel_strategy_factory
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.schemas.channel_config import (
    ChannelConfigCreateRequest,
    ChannelConfigPublic,
    ChannelConfigRecord,
    ChannelConfigUpdateRequest,
    clean_credentials,
    compute_channel_status,
)


class ChannelCredentialCodec:
    def __init__(self, key: str) -> None:
        key = key.strip()
        if not key:
            self._fernet: Fernet | None = None
            return
        self._fernet = self._build_fernet(key)

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, credentials: dict[str, str]) -> str:
        if not self._fernet:
            raise ValueError("CHANNEL_CREDENTIAL_ENCRYPTION_KEY is required before saving channel credentials.")
        payload = json.dumps(credentials, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> dict[str, str]:
        if not ciphertext:
            return {}
        if not self._fernet:
            return {}
        try:
            payload = self._fernet.decrypt(ciphertext.encode("ascii"))
            data = json.loads(payload.decode("utf-8"))
        except Exception:
            return {}
        return {str(key): str(value) for key, value in data.items() if str(value).strip()}

    @staticmethod
    def _build_fernet(raw_key: str) -> Fernet:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception:
            digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))


class MySQLChannelConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._codec = ChannelCredentialCodec(settings.database.channel_credential_encryption_key)

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._table_exists(cur):
                    self._create_table(cur)
                    conn.commit()
                    return
                self._ensure_columns(cur, self._list_columns(cur))
                self._ensure_unique_index(cur)
            conn.commit()

    def list_all(self) -> list[ChannelConfigRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, config_name, provider, domain, enabled, public_config_json,
                           credential_ciphertext, credential_version, description, created_at, updated_at
                    FROM `{self._settings.database.channel_config_table}`
                    ORDER BY FIELD(domain, 'qq', 'feishu', 'lark', 'weixin'), id ASC
                    """
                )
                rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_by_id(self, config_id: int) -> ChannelConfigRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, config_name, provider, domain, enabled, public_config_json,
                           credential_ciphertext, credential_version, description, created_at, updated_at
                    FROM `{self._settings.database.channel_config_table}`
                    WHERE id=%s
                    """,
                    (config_id,),
                )
                row = cur.fetchone()
        if not row:
            raise KeyError(config_id)
        return self._row_to_record(row)

    def create(self, payload: ChannelConfigCreateRequest) -> ChannelConfigRecord:
        credentials = clean_credentials(payload.domain, payload.credentials)
        ciphertext = self._credentials_to_ciphertext(credentials)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO `{self._settings.database.channel_config_table}`
                        (config_name, provider, domain, enabled, public_config_json,
                         credential_ciphertext, credential_version, description)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            payload.config_name.strip(),
                            payload.provider,
                            payload.domain,
                            int(payload.enabled),
                            json.dumps(payload.public_config, ensure_ascii=False),
                            ciphertext,
                            1,
                            payload.description,
                        ),
                    )
                    config_id = int(cur.lastrowid)
                conn.commit()
        except IntegrityError as exc:
            raise ValueError(f"Communication channel '{payload.domain}' already exists.") from exc
        return self.get_by_id(config_id)

    def update(self, config_id: int, payload: ChannelConfigUpdateRequest) -> ChannelConfigRecord:
        existing = self.get_by_id(config_id)
        credentials = clean_credentials(payload.domain, payload.credentials)
        if payload.clear_credentials:
            ciphertext = None
        elif credentials:
            ciphertext = self._credentials_to_ciphertext(credentials)
        elif existing.provider == payload.provider and existing.domain == payload.domain:
            ciphertext = existing.credential_ciphertext
        else:
            ciphertext = None

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE `{self._settings.database.channel_config_table}`
                        SET config_name=%s,
                            provider=%s,
                            domain=%s,
                            enabled=%s,
                            public_config_json=%s,
                            credential_ciphertext=%s,
                            credential_version=%s,
                            description=%s
                        WHERE id=%s
                        """,
                        (
                            payload.config_name.strip(),
                            payload.provider,
                            payload.domain,
                            int(payload.enabled),
                            json.dumps(payload.public_config, ensure_ascii=False),
                            ciphertext,
                            1,
                            payload.description,
                            config_id,
                        ),
                    )
                conn.commit()
        except IntegrityError as exc:
            raise ValueError(f"Communication channel '{payload.domain}' already exists.") from exc
        return self.get_by_id(config_id)

    def delete(self, config_id: int) -> ChannelConfigRecord:
        deleted = self.get_by_id(config_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM `{self._settings.database.channel_config_table}` WHERE id=%s",
                    (config_id,),
                )
            conn.commit()
        return deleted

    def credentials_for(self, record: ChannelConfigRecord) -> dict[str, str]:
        return self._codec.decrypt(record.credential_ciphertext)

    def to_public(self, record: ChannelConfigRecord) -> ChannelConfigPublic:
        credential_flags = self._credential_flags(record)
        status = compute_channel_status(
            domain=record.domain,
            enabled=record.enabled,
            public_config=record.public_config,
            credential_flags=credential_flags,
        )
        return ChannelConfigPublic(
            id=int(record.id or 0),
            config_name=record.config_name,
            provider=record.provider,
            domain=record.domain,
            enabled=record.enabled,
            status=status,
            public_config=record.public_config,
            credential_fields=credential_flags,
            has_credentials=any(credential_flags.values()),
            description=record.description,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _credentials_to_ciphertext(self, credentials: dict[str, str]) -> str | None:
        if not credentials:
            return None
        return self._codec.encrypt(credentials)

    def _credential_flags(self, record: ChannelConfigRecord) -> dict[str, bool]:
        fields = channel_strategy_factory.get(record.domain).definition.credential_fields
        decrypted = self._codec.decrypt(record.credential_ciphertext)
        if decrypted:
            return {field: bool(decrypted.get(field)) for field in fields}
        has_ciphertext = bool(record.credential_ciphertext)
        if has_ciphertext:
            return {field: True for field in fields}
        return {field: False for field in fields}

    def _table_exists(self, cur) -> bool:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.tables
            WHERE table_schema=%s AND table_name=%s
            """,
            (self._settings.database.mysql_database, self._settings.database.channel_config_table),
        )
        return bool(cur.fetchone()["total"])

    def _list_columns(self, cur) -> set[str]:
        cur.execute(
            """
            SELECT COLUMN_NAME
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s
            """,
            (self._settings.database.mysql_database, self._settings.database.channel_config_table),
        )
        return {row["COLUMN_NAME"] for row in cur.fetchall()}

    def _create_table(self, cur) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._settings.database.channel_config_table}` (
                `id` BIGINT NOT NULL AUTO_INCREMENT,
                `config_name` VARCHAR(120) NOT NULL,
                `provider` VARCHAR(32) NOT NULL,
                `domain` VARCHAR(32) NOT NULL,
                `enabled` TINYINT(1) NOT NULL DEFAULT 0,
                `public_config_json` TEXT NULL,
                `credential_ciphertext` TEXT NULL,
                `credential_version` INT NOT NULL DEFAULT 1,
                `description` TEXT NULL,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uniq_channel_provider_domain` (`provider`, `domain`)
            ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.database.mysql_charset}
            """
        )

    def _ensure_columns(self, cur, columns: set[str]) -> None:
        required_columns = {
            "id": "ALTER TABLE `{table}` ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST",
            "config_name": "ALTER TABLE `{table}` ADD COLUMN `config_name` VARCHAR(120) NOT NULL DEFAULT '' AFTER `id`",
            "provider": "ALTER TABLE `{table}` ADD COLUMN `provider` VARCHAR(32) NOT NULL DEFAULT 'qq' AFTER `config_name`",
            "domain": "ALTER TABLE `{table}` ADD COLUMN `domain` VARCHAR(32) NOT NULL DEFAULT 'qq' AFTER `provider`",
            "enabled": "ALTER TABLE `{table}` ADD COLUMN `enabled` TINYINT(1) NOT NULL DEFAULT 0 AFTER `domain`",
            "public_config_json": "ALTER TABLE `{table}` ADD COLUMN `public_config_json` TEXT NULL AFTER `enabled`",
            "credential_ciphertext": "ALTER TABLE `{table}` ADD COLUMN `credential_ciphertext` TEXT NULL AFTER `public_config_json`",
            "credential_version": "ALTER TABLE `{table}` ADD COLUMN `credential_version` INT NOT NULL DEFAULT 1 AFTER `credential_ciphertext`",
            "description": "ALTER TABLE `{table}` ADD COLUMN `description` TEXT NULL AFTER `credential_version`",
            "created_at": "ALTER TABLE `{table}` ADD COLUMN `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER `description`",
            "updated_at": "ALTER TABLE `{table}` ADD COLUMN `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER `created_at`",
        }
        for name, statement in required_columns.items():
            if name not in columns:
                cur.execute(statement.format(table=self._settings.database.channel_config_table))

    def _ensure_unique_index(self, cur) -> None:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.statistics
            WHERE table_schema=%s AND table_name=%s AND index_name='uniq_channel_provider_domain'
            """,
            (self._settings.database.mysql_database, self._settings.database.channel_config_table),
        )
        if not bool(cur.fetchone()["total"]):
            cur.execute(
                f"""
                ALTER TABLE `{self._settings.database.channel_config_table}`
                ADD UNIQUE KEY `uniq_channel_provider_domain` (`provider`, `domain`)
                """
            )

    def _row_to_record(self, row: dict | None) -> ChannelConfigRecord:
        if row is None:
            raise KeyError("channel config row not found")
        return ChannelConfigRecord(
            id=int(row["id"]),
            config_name=row.get("config_name") or str(row.get("domain") or "channel"),
            provider=str(row["provider"]).strip().lower(),
            domain=str(row["domain"]).strip().lower(),
            enabled=bool(row.get("enabled")),
            public_config=_parse_json_object(row.get("public_config_json")),
            credential_ciphertext=_clean_optional(row.get("credential_ciphertext")),
            credential_version=int(row.get("credential_version") or 1),
            description=_clean_optional(row.get("description")),
            created_at=_to_datetime(row.get("created_at")),
            updated_at=_to_datetime(row.get("updated_at")),
        )

    def _connect(self):
        return mysql_raw_connection(self._settings)


def _parse_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
