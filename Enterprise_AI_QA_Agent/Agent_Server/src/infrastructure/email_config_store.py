from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from src.core.config import Settings
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.schemas.email_config import (
    EmailConfigCreateRequest,
    EmailConfigPublic,
    EmailConfigRecord,
    EmailConfigUpdateRequest,
)

AGENT_MAIL_PROVIDERS = (
    "tencent_agently",
    "agentmail",
    "robotomail",
    "openmail",
    "dead_simple_email",
    "agenticmail",
    "aws_agent_mailbox",
)
AGENT_MAIL_PROVIDER_SQL = ", ".join(f"'{key}'" for key in AGENT_MAIL_PROVIDERS)


class MySQLEmailConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if not self._table_exists(cur):
                    self._create_table(cur)
                    conn.commit()
                    return

                columns = self._list_columns(cur)
                if "config_json" in columns:
                    self._migrate_legacy_table(cur)
                    conn.commit()
                    return

                self._ensure_columns(cur, columns)
            conn.commit()

    def list_all(self) -> list[EmailConfigRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, config_name, provider, owner_agent_key, api_key, secret_key, sender_email, test_email,
                           test_mode, enabled, is_default, description, smtp_host, smtp_port,
                           smtp_username, extra_config_json, created_at, updated_at
                    FROM `{self._settings.database.email_config_table}`
                    WHERE provider IN ({AGENT_MAIL_PROVIDER_SQL})
                    ORDER BY is_default DESC, enabled DESC, id ASC
                    """
                )
                rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_by_id(self, config_id: int) -> EmailConfigRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, config_name, provider, owner_agent_key, api_key, secret_key, sender_email, test_email,
                           test_mode, enabled, is_default, description, smtp_host, smtp_port,
                           smtp_username, extra_config_json, created_at, updated_at
                    FROM `{self._settings.database.email_config_table}`
                    WHERE id=%s AND provider IN ({AGENT_MAIL_PROVIDER_SQL})
                    """,
                    (config_id,),
                )
                row = cur.fetchone()
        if not row:
            raise KeyError(config_id)
        return self._row_to_record(row)

    def create(self, payload: EmailConfigCreateRequest) -> EmailConfigRecord:
        values = self._payload_to_values(payload)
        with self._connect() as conn:
            with conn.cursor() as cur:
                if payload.is_default:
                    cur.execute(
                        f"""
                        UPDATE `{self._settings.database.email_config_table}`
                        SET enabled=0, is_default=0
                        WHERE provider IN ({AGENT_MAIL_PROVIDER_SQL})
                        """,
                    )
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.database.email_config_table}`
                    (config_name, provider, owner_agent_key, api_key, secret_key, sender_email, test_email, test_mode,
                     enabled, is_default, description, smtp_host, smtp_port, smtp_username, extra_config_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    values,
                )
                config_id = int(cur.lastrowid)
            conn.commit()
        if payload.provider == "tencent_agently":
            return self.ensure_tencent_credential_profile(config_id)
        return self.get_by_id(config_id)

    def ensure_tencent_credential_profile(self, config_id: int) -> EmailConfigRecord:
        """Assign a stable server-side CLI credential directory to one mailbox."""

        record = self.get_by_id(config_id)
        if record.provider != "tencent_agently":
            return record
        extra = dict(record.extra_config or {})
        if str(extra.get("config_dir") or "").strip():
            return record
        extra["config_dir"] = str(self._mailbox_config_dir(f"config-{config_id}"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE `{self._settings.database.email_config_table}` "
                    "SET extra_config_json=%s WHERE id=%s",
                    (json.dumps(extra, ensure_ascii=False), config_id),
                )
            conn.commit()
        return self.get_by_id(config_id)

    def update(self, config_id: int, payload: EmailConfigUpdateRequest) -> EmailConfigRecord:
        existing = self.get_by_id(config_id)
        merged = self._merge_update(existing, payload)

        with self._connect() as conn:
            with conn.cursor() as cur:
                if merged.is_default:
                    cur.execute(
                        f"""
                        UPDATE `{self._settings.database.email_config_table}`
                        SET enabled=0, is_default=0
                        WHERE provider IN ({AGENT_MAIL_PROVIDER_SQL}) AND id<>%s
                        """,
                        (config_id,),
                    )
                cur.execute(
                    f"""
                    UPDATE `{self._settings.database.email_config_table}`
                    SET config_name=%s,
                        provider=%s,
                        owner_agent_key=%s,
                        api_key=%s,
                        secret_key=%s,
                        sender_email=%s,
                        test_email=%s,
                        test_mode=%s,
                        enabled=%s,
                        is_default=%s,
                        description=%s,
                        smtp_host=%s,
                        smtp_port=%s,
                        smtp_username=%s,
                        extra_config_json=%s
                    WHERE id=%s
                    """,
                    (
                        merged.config_name,
                        merged.provider,
                        merged.owner_agent_key,
                        merged.api_key,
                        merged.secret_key,
                        merged.sender_email,
                        merged.test_email,
                        int(merged.test_mode),
                        int(merged.enabled),
                        int(merged.is_default),
                        merged.description,
                        merged.smtp_host,
                        merged.smtp_port,
                        merged.smtp_username,
                        json.dumps(merged.extra_config, ensure_ascii=False),
                        config_id,
                    ),
                )
            conn.commit()
        return self.get_by_id(config_id)

    def activate(self, config_id: int) -> EmailConfigRecord:
        self.get_by_id(config_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE `{self._settings.database.email_config_table}`
                    SET enabled=0, is_default=0
                    WHERE provider IN ({AGENT_MAIL_PROVIDER_SQL})
                    """,
                )
                cur.execute(
                    f"""
                    UPDATE `{self._settings.database.email_config_table}`
                    SET enabled=1, is_default=1
                    WHERE id=%s
                    """,
                    (config_id,),
                )
            conn.commit()
        return self.get_by_id(config_id)

    def delete(self, config_id: int) -> tuple[EmailConfigRecord, EmailConfigRecord | None]:
        deleted = self.get_by_id(config_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM `{self._settings.database.email_config_table}` WHERE id=%s",
                    (config_id,),
                )
            conn.commit()
        return deleted, None

    def to_public(self, record: EmailConfigRecord) -> EmailConfigPublic:
        public_extra = {
            key: value
            for key, value in (record.extra_config or {}).items()
            if key not in {"cli_path", "config_dir", "webhook_secret", "access_token", "refresh_token", "secret"}
        }
        public_extra["credential_scope"] = (
            "mailbox_isolated"
            if str((record.extra_config or {}).get("config_dir") or "").strip()
            else "server_default"
        )
        return EmailConfigPublic(
            id=int(record.id or 0),
            config_name=record.config_name,
            provider=record.provider,
            enabled=record.enabled,
            is_default=record.is_default,
            sender_email=record.sender_email,
            has_api_key=bool(record.api_key),
            has_secret_key=bool(record.secret_key),
            description=record.description,
            extra_config=public_extra,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _table_exists(self, cur) -> bool:
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.tables
            WHERE table_schema=%s AND table_name=%s
            """,
            (self._settings.database.mysql_database, self._settings.database.email_config_table),
        )
        return bool(cur.fetchone()["total"])

    def _list_columns(self, cur) -> set[str]:
        cur.execute(
            """
            SELECT COLUMN_NAME
            FROM information_schema.columns
            WHERE table_schema=%s AND table_name=%s
            """,
            (self._settings.database.mysql_database, self._settings.database.email_config_table),
        )
        return {row["COLUMN_NAME"] for row in cur.fetchall()}

    def _create_table(self, cur) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._settings.database.email_config_table}` (
                `id` BIGINT NOT NULL AUTO_INCREMENT,
                `config_name` VARCHAR(120) NOT NULL,
                `provider` VARCHAR(64) NOT NULL,
                `owner_agent_key` VARCHAR(120) NOT NULL DEFAULT 'global',
                `api_key` VARCHAR(255) NULL,
                `secret_key` VARCHAR(255) NULL,
                `sender_email` VARCHAR(255) NOT NULL,
                `test_email` VARCHAR(255) NULL,
                `test_mode` TINYINT(1) NOT NULL DEFAULT 0,
                `enabled` TINYINT(1) NOT NULL DEFAULT 0,
                `is_default` TINYINT(1) NOT NULL DEFAULT 0,
                `description` TEXT NULL,
                `smtp_host` VARCHAR(255) NULL,
                `smtp_port` INT NULL,
                `smtp_username` VARCHAR(255) NULL,
                `extra_config_json` TEXT NULL,
                `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`),
                UNIQUE KEY `uniq_email_config_name` (`config_name`)
            ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.database.mysql_charset}
            """
        )

    def _ensure_columns(self, cur, columns: set[str]) -> None:
        required_columns = {
            "id": "ALTER TABLE `{table}` ADD COLUMN `id` BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST",
            "config_name": "ALTER TABLE `{table}` ADD COLUMN `config_name` VARCHAR(120) NOT NULL DEFAULT '' AFTER `id`",
            "owner_agent_key": "ALTER TABLE `{table}` ADD COLUMN `owner_agent_key` VARCHAR(120) NOT NULL DEFAULT 'global' AFTER `provider`",
            "api_key": "ALTER TABLE `{table}` ADD COLUMN `api_key` VARCHAR(255) NULL AFTER `provider`",
            "secret_key": "ALTER TABLE `{table}` ADD COLUMN `secret_key` VARCHAR(255) NULL AFTER `api_key`",
            "sender_email": "ALTER TABLE `{table}` ADD COLUMN `sender_email` VARCHAR(255) NOT NULL DEFAULT '' AFTER `secret_key`",
            "test_email": "ALTER TABLE `{table}` ADD COLUMN `test_email` VARCHAR(255) NULL AFTER `sender_email`",
            "test_mode": "ALTER TABLE `{table}` ADD COLUMN `test_mode` TINYINT(1) NOT NULL DEFAULT 0 AFTER `test_email`",
            "enabled": "ALTER TABLE `{table}` ADD COLUMN `enabled` TINYINT(1) NOT NULL DEFAULT 0 AFTER `test_mode`",
            "is_default": "ALTER TABLE `{table}` ADD COLUMN `is_default` TINYINT(1) NOT NULL DEFAULT 0 AFTER `enabled`",
            "description": "ALTER TABLE `{table}` ADD COLUMN `description` TEXT NULL AFTER `is_default`",
            "smtp_host": "ALTER TABLE `{table}` ADD COLUMN `smtp_host` VARCHAR(255) NULL AFTER `description`",
            "smtp_port": "ALTER TABLE `{table}` ADD COLUMN `smtp_port` INT NULL AFTER `smtp_host`",
            "smtp_username": "ALTER TABLE `{table}` ADD COLUMN `smtp_username` VARCHAR(255) NULL AFTER `smtp_port`",
            "extra_config_json": "ALTER TABLE `{table}` ADD COLUMN `extra_config_json` TEXT NULL AFTER `smtp_username`",
        }
        for name, statement in required_columns.items():
            if name not in columns:
                cur.execute(statement.format(table=self._settings.database.email_config_table))
        cur.execute(
            f"""
            UPDATE `{self._settings.database.email_config_table}`
            SET config_name = CASE
                WHEN config_name IS NULL OR config_name = '' THEN CONCAT(provider, '-', id)
                ELSE config_name
            END
            """
        )
        cur.execute(
            f"""
            UPDATE `{self._settings.database.email_config_table}`
            SET enabled=0, is_default=0
            WHERE provider NOT IN ({AGENT_MAIL_PROVIDER_SQL})
            """
        )
        cur.execute(
            f"""
            UPDATE `{self._settings.database.email_config_table}`
            SET owner_agent_key='global'
            WHERE provider IN ({AGENT_MAIL_PROVIDER_SQL})
            """
        )
        cur.execute(
            f"""
            SELECT id
            FROM `{self._settings.database.email_config_table}`
            WHERE provider IN ({AGENT_MAIL_PROVIDER_SQL}) AND enabled=1
            ORDER BY is_default DESC, id ASC
            LIMIT 1
            """
        )
        active = cur.fetchone()
        if active:
            active_id = int(active["id"])
            cur.execute(
                f"""
                UPDATE `{self._settings.database.email_config_table}`
                SET enabled=CASE WHEN id=%s THEN 1 ELSE 0 END,
                    is_default=CASE WHEN id=%s THEN 1 ELSE 0 END
                WHERE provider IN ({AGENT_MAIL_PROVIDER_SQL})
                """,
                (active_id, active_id),
            )

    def _migrate_legacy_table(self, cur) -> None:
        legacy_rows = self._fetch_legacy_rows(cur)
        backup_name = (
            f"{self._settings.database.email_config_table}_legacy_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        )
        cur.execute(f"RENAME TABLE `{self._settings.database.email_config_table}` TO `{backup_name}`")
        self._create_table(cur)
        for row in legacy_rows:
            if str(row.get("provider") or "").strip().lower() in AGENT_MAIL_PROVIDERS:
                self._insert_record(cur, self._legacy_row_to_record(row))

    def _fetch_legacy_rows(self, cur) -> list[dict]:
        cur.execute(
            f"""
            SELECT provider, enabled, is_default, from_email, from_name, reply_to, config_json, created_at, updated_at
            FROM `{self._settings.database.email_config_table}`
            ORDER BY provider ASC
            """
        )
        return cur.fetchall()

    def _legacy_row_to_record(self, row: dict) -> EmailConfigRecord:
        config = json.loads(row.get("config_json") or "{}")
        provider = str(row["provider"]).strip().lower()
        extra_config = _clean_extra_config({
            key: value
            for key, value in config.items()
            if key not in {"api_key", "secret_key"}
        })
        extra_config.pop("cli_path", None)
        return EmailConfigRecord(
            config_name="Agent 原生邮箱",
            provider=provider,
            owner_agent_key="global",
            api_key=_clean_str(config.get("api_key")),
            secret_key=_clean_str(config.get("secret_key")),
            sender_email=row.get("from_email") or "",
            test_email=None,
            test_mode=False,
            enabled=bool(row.get("enabled")),
            is_default=bool(row.get("is_default")),
            description=None,
            smtp_host=None,
            smtp_port=None,
            smtp_username=None,
            extra_config=extra_config,
            created_at=_to_datetime(row.get("created_at")),
            updated_at=_to_datetime(row.get("updated_at")),
        )

    def _insert_record(self, cur, record: EmailConfigRecord) -> None:
        cur.execute(
            f"""
            INSERT INTO `{self._settings.database.email_config_table}`
            (config_name, provider, owner_agent_key, api_key, secret_key, sender_email, test_email, test_mode,
             enabled, is_default, description, smtp_host, smtp_port, smtp_username, extra_config_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.config_name,
                record.provider,
                record.owner_agent_key,
                record.api_key,
                record.secret_key,
                record.sender_email,
                record.test_email,
                int(record.test_mode),
                int(record.enabled),
                int(record.is_default),
                record.description,
                record.smtp_host,
                record.smtp_port,
                record.smtp_username,
                json.dumps(record.extra_config, ensure_ascii=False),
                record.created_at or datetime.utcnow(),
                record.updated_at or datetime.utcnow(),
            ),
        )

    def _merge_update(self, existing: EmailConfigRecord, payload: EmailConfigUpdateRequest) -> EmailConfigRecord:
        extra_config = _clean_extra_config(payload.extra_config)
        extra_config.pop("cli_path", None)
        if not extra_config.get("config_dir"):
            existing_config_dir = str((existing.extra_config or {}).get("config_dir") or "").strip()
            if existing_config_dir:
                extra_config["config_dir"] = existing_config_dir

        # Public update payloads cannot echo stored secrets back to the client.
        # Treat an omitted/None secret as "keep the current value" while the
        # provider is unchanged. A provider switch must supply fresh credentials
        # so a token is never carried across vendors accidentally.
        same_provider = payload.provider == existing.provider
        api_key = _clean_str(payload.api_key)
        secret_key = _clean_str(payload.secret_key)
        if same_provider:
            api_key = api_key or existing.api_key
            secret_key = secret_key or existing.secret_key

        return EmailConfigRecord(
            id=existing.id,
            config_name=payload.config_name.strip(),
            provider=payload.provider,
            owner_agent_key="global",
            api_key=api_key,
            secret_key=secret_key,
            sender_email=payload.sender_email.strip(),
            test_email=None,
            test_mode=False,
            enabled=bool(payload.enabled or payload.is_default),
            is_default=bool(payload.is_default),
            description=_clean_str(payload.description),
            smtp_host=None,
            smtp_port=None,
            smtp_username=None,
            extra_config=extra_config,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    def _payload_to_values(self, payload: EmailConfigCreateRequest):
        extra_config = _clean_extra_config(payload.extra_config)
        extra_config.pop("cli_path", None)
        enabled = bool(payload.enabled or payload.is_default)
        return (
            payload.config_name.strip(),
            payload.provider,
            "global",
            _clean_str(payload.api_key),
            _clean_str(payload.secret_key),
            payload.sender_email.strip(),
            None,
            0,
            int(enabled),
            int(payload.is_default),
            _clean_str(payload.description),
            None,
            None,
            None,
            json.dumps(extra_config, ensure_ascii=False),
        )

    def _mailbox_config_dir(self, profile_key: str) -> Path:
        root = Path(self._settings.mail.agently_cli_config_root).expanduser()
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[2] / root
        path = (root / profile_key).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _row_to_record(self, row: dict | None) -> EmailConfigRecord:
        if row is None:
            raise KeyError("email config row not found")

        provider = str(row["provider"]).strip().lower()
        return EmailConfigRecord(
            id=int(row["id"]),
            config_name=row.get("config_name") or "Agent 原生邮箱",
            provider=provider,
            owner_agent_key="global",
            api_key=_clean_str(row.get("api_key")),
            secret_key=_clean_str(row.get("secret_key")),
            sender_email=row.get("sender_email") or "",
            test_email=_clean_str(row.get("test_email")),
            test_mode=bool(row.get("test_mode")),
            enabled=bool(row.get("enabled")),
            is_default=bool(row.get("is_default")),
            description=_clean_str(row.get("description")),
            smtp_host=None,
            smtp_port=None,
            smtp_username=None,
            extra_config=_parse_extra_config(row.get("extra_config_json")),
            created_at=_to_datetime(row.get("created_at")),
            updated_at=_to_datetime(row.get("updated_at")),
        )

    def _connect(self):
        return mysql_raw_connection(self._settings)


def _clean_str(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_extra_config(value):
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _parse_extra_config(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return _clean_extra_config(value)
    try:
        return _clean_extra_config(json.loads(str(value)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _to_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
