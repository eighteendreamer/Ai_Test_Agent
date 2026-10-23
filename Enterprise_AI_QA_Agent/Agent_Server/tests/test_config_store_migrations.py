from __future__ import annotations

import os
from uuid import uuid4

import pytest

from src.core.config import get_settings
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1",
    reason="requires configured MySQL; uses random isolated email config tables",
)
async def test_legacy_email_config_migration_keeps_sender_metadata_and_backup():
    settings = get_settings().model_copy(deep=True)
    table = f"up_email_legacy_{uuid4().hex[:12]}"
    settings.database.email_config_table = table
    store = MySQLEmailConfigStore(settings)
    backup = None
    try:
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"""CREATE TABLE `{table}` (
                provider VARCHAR(64) NOT NULL PRIMARY KEY,
                enabled TINYINT(1) NOT NULL DEFAULT 0,
                is_default TINYINT(1) NOT NULL DEFAULT 0,
                from_email VARCHAR(255) NULL,
                from_name VARCHAR(255) NULL,
                reply_to VARCHAR(255) NULL,
                config_json LONGTEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cursor.execute(
                f"""INSERT INTO `{table}`
                (provider, enabled, is_default, from_email, from_name, reply_to, config_json)
                VALUES (%s, 1, 1, %s, %s, %s, %s)""",
                ("tencent_agently", "sender@example.invalid", "Legacy Sender", "reply@example.invalid",
                 '{"api_key":"api-marker","secret_key":"secret-marker",'
                 '"region":"test-region","cli_path":"remove-me"}'),
            )
            connection.commit()

        store.initialize()
        rows = store.list_all()
        assert len(rows) == 1
        record = rows[0]
        assert record.provider == "tencent_agently"
        assert record.sender_email == "sender@example.invalid"
        assert record.api_key == "api-marker"
        assert record.secret_key == "secret-marker"
        assert record.extra_config == {
            "region": "test-region",
            "from_name": "Legacy Sender",
            "reply_to": "reply@example.invalid",
        }

        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name AS legacy_table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name LIKE %s",
                (settings.database.mysql_database, table + "_legacy_%"),
            )
            backups = [row["legacy_table_name"] for row in cursor.fetchall()]
            assert len(backups) == 1
            backup = backups[0]
            cursor.execute(f"SELECT from_name, reply_to, config_json FROM `{backup}` WHERE provider=%s",
                           ("tencent_agently",))
            old_record = cursor.fetchone()
            assert old_record["from_name"] == "Legacy Sender"
            assert old_record["reply_to"] == "reply@example.invalid"

        store.initialize()
        assert len(store.list_all()) == 1
    finally:
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name AS legacy_table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name LIKE %s",
                (settings.database.mysql_database, table + "_legacy_%"),
            )
            backups = [row["legacy_table_name"] for row in cursor.fetchall()]
            for legacy_table in backups:
                cursor.execute(f"DROP TABLE IF EXISTS `{legacy_table}`")
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            connection.commit()
