from __future__ import annotations

import json
from datetime import datetime

import pymysql

from src.core.config import Settings
from src.schemas.email_config import EmailConfigPublic, EmailConfigRecord, EmailConfigUpdateRequest


class MySQLEmailConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def initialize(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self._settings.email_config_table}` (
                        `provider` VARCHAR(64) NOT NULL PRIMARY KEY,
                        `enabled` TINYINT(1) NOT NULL DEFAULT 0,
                        `is_default` TINYINT(1) NOT NULL DEFAULT 0,
                        `from_email` VARCHAR(255) NULL,
                        `from_name` VARCHAR(255) NULL,
                        `reply_to` VARCHAR(255) NULL,
                        `config_json` LONGTEXT NULL,
                        `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET={self._settings.mysql_charset}
                    """
                )
                cur.execute(f"SELECT COUNT(*) AS total FROM `{self._settings.email_config_table}`")
                total = int(cur.fetchone()["total"] or 0)
                if total == 0:
                    cur.executemany(
                        f"""
                        INSERT INTO `{self._settings.email_config_table}`
                        (`provider`, `enabled`, `is_default`, `from_email`, `from_name`, `reply_to`, `config_json`)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            ("aliyun", 0, 0, "", "", "", json.dumps({"region": "cn-hangzhou"}, ensure_ascii=False)),
                            ("cybermail", 0, 0, "", "", "", json.dumps({"smtp_port": 465, "use_tls": True}, ensure_ascii=False)),
                        ],
                    )
            conn.commit()

    def list_all(self) -> list[EmailConfigRecord]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT provider, enabled, is_default, from_email, from_name, reply_to, config_json, created_at, updated_at
                    FROM `{self._settings.email_config_table}`
                    ORDER BY provider ASC
                    """
                )
                rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def update(self, payload: EmailConfigUpdateRequest) -> EmailConfigRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT provider, enabled, is_default, from_email, from_name, reply_to, config_json, created_at, updated_at
                    FROM `{self._settings.email_config_table}`
                    WHERE provider=%s
                    """,
                    (payload.provider,),
                )
                existing_row = cur.fetchone()
                existing = self._row_to_record(existing_row) if existing_row else None
                if payload.is_default:
                    cur.execute(
                        f"UPDATE `{self._settings.email_config_table}` SET `is_default`=0"
                    )
                cur.execute(
                    f"""
                    INSERT INTO `{self._settings.email_config_table}`
                    (`provider`, `enabled`, `is_default`, `from_email`, `from_name`, `reply_to`, `config_json`)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `enabled`=VALUES(`enabled`),
                        `is_default`=VALUES(`is_default`),
                        `from_email`=VALUES(`from_email`),
                        `from_name`=VALUES(`from_name`),
                        `reply_to`=VALUES(`reply_to`),
                        `config_json`=VALUES(`config_json`)
                    """,
                    (
                        payload.provider,
                        int(payload.enabled),
                        int(payload.is_default),
                        payload.from_email,
                        payload.from_name,
                        payload.reply_to,
                        json.dumps(
                            {
                                "access_key_id": payload.access_key_id,
                                "access_key_secret": payload.access_key_secret
                                if payload.access_key_secret
                                else (existing.access_key_secret if existing else None),
                                "account_name": payload.account_name,
                                "region": payload.region,
                                "smtp_host": payload.smtp_host,
                                "smtp_port": payload.smtp_port,
                                "smtp_username": payload.smtp_username,
                                "smtp_password": payload.smtp_password
                                if payload.smtp_password
                                else (existing.smtp_password if existing else None),
                                "use_tls": payload.use_tls,
                            },
                            ensure_ascii=False,
                        ),
                    ),
                )
                cur.execute(
                    f"""
                    SELECT provider, enabled, is_default, from_email, from_name, reply_to, config_json, created_at, updated_at
                    FROM `{self._settings.email_config_table}`
                    WHERE provider=%s
                    """,
                    (payload.provider,),
                )
                row = cur.fetchone()
            conn.commit()
        return self._row_to_record(row)

    def to_public(self, record: EmailConfigRecord) -> EmailConfigPublic:
        return EmailConfigPublic(
            provider=record.provider,
            enabled=record.enabled,
            is_default=record.is_default,
            from_email=record.from_email,
            from_name=record.from_name,
            reply_to=record.reply_to,
            access_key_id=record.access_key_id,
            account_name=record.account_name,
            region=record.region,
            smtp_host=record.smtp_host,
            smtp_port=record.smtp_port,
            smtp_username=record.smtp_username,
            use_tls=record.use_tls,
            has_access_key_secret=bool(record.access_key_secret),
            has_smtp_password=bool(record.smtp_password),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _row_to_record(self, row: dict) -> EmailConfigRecord:
        config = _json_loads(row.get("config_json"))
        return EmailConfigRecord(
            provider=row["provider"],
            enabled=bool(row.get("enabled")),
            is_default=bool(row.get("is_default")),
            from_email=row.get("from_email") or "",
            from_name=row.get("from_name") or "",
            reply_to=row.get("reply_to") or "",
            access_key_id=config.get("access_key_id"),
            access_key_secret=config.get("access_key_secret"),
            account_name=config.get("account_name"),
            region=config.get("region"),
            smtp_host=config.get("smtp_host"),
            smtp_port=config.get("smtp_port"),
            smtp_username=config.get("smtp_username"),
            smtp_password=config.get("smtp_password"),
            use_tls=bool(config.get("use_tls", True)),
            created_at=_to_datetime(row.get("created_at")),
            updated_at=_to_datetime(row.get("updated_at")),
        )

    def _connect(self):
        return pymysql.connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password,
            database=self._settings.mysql_database,
            charset=self._settings.mysql_charset,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )


def _json_loads(value: str | None) -> dict:
    if not value:
        return {}
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


def _to_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
