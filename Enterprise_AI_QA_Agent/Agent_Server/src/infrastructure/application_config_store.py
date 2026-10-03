"""Versioned, non-secret application configuration in the existing MySQL runtime."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Any, Protocol

from src.core.config import Settings
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplicationConfigRecord:
    config_key: str
    scope_key: str
    payload: dict[str, Any]
    revision: int


class ApplicationConfigStore(Protocol):
    def initialize(self) -> None: ...
    def get(self, config_key: str, *, scope_key: str = "global") -> ApplicationConfigRecord | None: ...
    def save(self, config_key: str, payload: dict[str, Any], *, scope_key: str = "global", only_if_missing: bool = False) -> ApplicationConfigRecord: ...


class MySQLApplicationConfigStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._table = settings.database.application_config_table
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", self._table):
            raise ValueError("Invalid application configuration table identifier.")

    def initialize(self) -> None:
        with mysql_raw_connection(self._settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS `{self._table}` (
                        scope_key VARCHAR(64) NOT NULL,
                        config_key VARCHAR(128) NOT NULL,
                        payload_json LONGTEXT NOT NULL,
                        revision BIGINT NOT NULL DEFAULT 1,
                        updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                        PRIMARY KEY (scope_key, config_key)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
                """)
            connection.commit()

    def get(self, config_key: str, *, scope_key: str = "global") -> ApplicationConfigRecord | None:
        try:
            with mysql_raw_connection(self._settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT config_key, scope_key, payload_json, revision FROM `{self._table}` "
                        "WHERE scope_key=%s AND config_key=%s", (scope_key, config_key),
                    )
                    row = cursor.fetchone()
        except Exception as exc:
            logger.error("application_config_read_failed", extra={
                "config_key": config_key, "scope_key": scope_key, "error_type": type(exc).__name__,
            })
            raise RuntimeError(f"Cannot read application configuration: {config_key}") from None
        return self._record(row) if row else None

    def save(self, config_key: str, payload: dict[str, Any], *, scope_key: str = "global", only_if_missing: bool = False) -> ApplicationConfigRecord:
        if not config_key or len(config_key) > 128 or not scope_key or len(scope_key) > 64:
            raise ValueError("Application configuration key or scope is invalid.")
        if not isinstance(payload, dict):
            raise ValueError("Application configuration must be an object.")
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        update = ("config_key=config_key" if only_if_missing else
                  "payload_json=VALUES(payload_json), revision=revision+1, updated_at=CURRENT_TIMESTAMP(6)")
        try:
            with mysql_raw_connection(self._settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"INSERT INTO `{self._table}` (scope_key, config_key, payload_json) VALUES (%s, %s, %s) "
                        f"ON DUPLICATE KEY UPDATE {update}", (scope_key, config_key, encoded),
                    )
                    cursor.execute(
                        f"SELECT config_key, scope_key, payload_json, revision FROM `{self._table}` "
                        "WHERE scope_key=%s AND config_key=%s", (scope_key, config_key),
                    )
                    record = self._record(cursor.fetchone())
                connection.commit()
        except Exception as exc:
            # Database exception text can contain SQL parameter values. Record
            # context without exposing configuration contents in the log.
            logger.error("application_config_save_failed", extra={
                "config_key": config_key, "scope_key": scope_key, "error_type": type(exc).__name__,
            })
            raise RuntimeError(f"Cannot save application configuration: {config_key}") from None
        logger.info("application_config_saved", extra={
            "config_key": config_key, "scope_key": scope_key, "revision": record.revision,
            "only_if_missing": only_if_missing,
        })
        return record

    @staticmethod
    def _record(row) -> ApplicationConfigRecord:
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise TypeError("Configuration must be an object.")
        except (ValueError, TypeError) as exc:
            logger.error("application_config_invalid_payload", extra={
                "config_key": row["config_key"], "error_type": type(exc).__name__,
            })
            raise ValueError(f"Invalid stored application configuration: {row['config_key']}") from None
        return ApplicationConfigRecord(row["config_key"], row["scope_key"], payload, int(row["revision"]))
