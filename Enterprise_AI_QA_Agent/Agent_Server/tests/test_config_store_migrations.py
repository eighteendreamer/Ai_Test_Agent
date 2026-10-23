from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from uuid import uuid4

import pytest

from src.core.config import get_settings
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.infrastructure.sqlalchemy_runtime import SQLAlchemyCursor, mysql_raw_connection


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


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1",
    reason="requires configured MySQL; uses random isolated email config tables",
)
async def test_legacy_email_config_migration_handles_multiple_providers():
    settings = get_settings().model_copy(deep=True)
    table = f"up_email_multi_{uuid4().hex[:12]}"
    settings.database.email_config_table = table
    store = MySQLEmailConfigStore(settings)
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
            cursor.executemany(
                f"""INSERT INTO `{table}`
                (provider, enabled, is_default, from_email, config_json)
                VALUES (%s, 1, %s, %s, %s)""",
                [
                    ("agentmail", 1, "agent@example.invalid", '{"api_key":"agent-key"}'),
                    ("tencent_agently", 0, "tencent@example.invalid", '{"api_key":"tencent-key"}'),
                ],
            )
            connection.commit()

        store.initialize()
        migrated = store.list_all()
        assert {item.provider for item in migrated} == {"agentmail", "tencent_agently"}
        assert {item.sender_email for item in migrated} == {
            "agent@example.invalid", "tencent@example.invalid",
        }
        assert len({item.config_name for item in migrated}) == 2
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


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1",
    reason="requires configured MySQL; uses random isolated email config tables",
)
async def test_legacy_email_config_migration_recovers_after_rename_interrupt(monkeypatch):
    settings = get_settings().model_copy(deep=True)
    table = f"up_email_interrupt_{uuid4().hex[:12]}"
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
                f"""INSERT INTO `{table}` (provider, enabled, from_email, config_json)
                VALUES (%s, 1, %s, %s)""",
                ("agentmail", "sender@example.invalid", '{"api_key":"recovery-key"}'),
            )
            connection.commit()

        original_execute = SQLAlchemyCursor.execute
        renamed = False

        def interrupt_after_rename(cursor, statement, parameters=None):
            nonlocal renamed
            result = original_execute(cursor, statement, parameters)
            if statement.lstrip().upper().startswith("RENAME TABLE"):
                renamed = True
                raise RuntimeError("simulated process interruption after MySQL DDL rename")
            return result

        monkeypatch.setattr(SQLAlchemyCursor, "execute", interrupt_after_rename)
        with pytest.raises(RuntimeError, match="simulated process interruption"):
            store.initialize()
        assert renamed
        monkeypatch.setattr(SQLAlchemyCursor, "execute", original_execute)

        store.initialize()
        recovered = store.list_all()
        assert len(recovered) == 1
        assert recovered[0].provider == "agentmail"
        assert recovered[0].api_key == "recovery-key"
        assert recovered[0].sender_email == "sender@example.invalid"
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name AS legacy_table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name LIKE %s",
                (settings.database.mysql_database, table + "_legacy_%"),
            )
            backups = [row["legacy_table_name"] for row in cursor.fetchall()]
            assert len(backups) == 1
            backup = backups[0]
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


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1",
    reason="requires configured MySQL; uses random isolated email config tables",
)
async def test_legacy_email_config_migration_keeps_source_when_staging_fails(monkeypatch):
    settings = get_settings().model_copy(deep=True)
    table = f"up_email_stage_{uuid4().hex[:12]}"
    settings.database.email_config_table = table
    store = MySQLEmailConfigStore(settings)
    original_insert = MySQLEmailConfigStore._insert_record
    failed = False

    def fail_first_staging_insert(self, cursor, record, table_name=None):
        nonlocal failed
        if table_name is not None and not failed:
            failed = True
            raise RuntimeError("simulated staging population failure")
        return original_insert(self, cursor, record, table_name)

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
                f"""INSERT INTO `{table}` (provider, enabled, from_email, config_json)
                VALUES (%s, 1, %s, %s)""",
                ("agentmail", "sender@example.invalid", '{"api_key":"staging-key"}'),
            )
            connection.commit()

        monkeypatch.setattr(MySQLEmailConfigStore, "_insert_record", fail_first_staging_insert)
        with pytest.raises(RuntimeError, match="simulated staging population failure"):
            store.initialize()
        assert failed

        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s AND column_name='config_json'",
                (settings.database.mysql_database, table),
            )
            assert cursor.fetchone()["total"] == 1
            cursor.execute(f"SELECT config_json FROM `{table}` WHERE provider=%s", ("agentmail",))
            assert cursor.fetchone()["config_json"] == '{"api_key":"staging-key"}'
            cursor.execute(
                "SELECT COUNT(*) AS total FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name=%s",
                (settings.database.mysql_database, table + "_migration_stage"),
            )
            assert cursor.fetchone()["total"] == 0

        monkeypatch.setattr(MySQLEmailConfigStore, "_insert_record", original_insert)
        store.initialize()
        migrated = store.list_all()
        assert len(migrated) == 1
        assert migrated[0].api_key == "staging-key"
    finally:
        monkeypatch.setattr(MySQLEmailConfigStore, "_insert_record", original_insert)
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name AS owned_table FROM information_schema.tables "
                "WHERE table_schema=%s AND (table_name LIKE %s OR table_name=%s)",
                (settings.database.mysql_database, table + "_legacy_%", table + "_migration_stage"),
            )
            for row in cursor.fetchall():
                cursor.execute(f"DROP TABLE IF EXISTS `{row['owned_table']}`")
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            connection.commit()


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1",
    reason="requires configured MySQL; uses random isolated email config tables",
)
async def test_legacy_email_config_migration_serializes_concurrent_initializers(monkeypatch):
    settings = get_settings().model_copy(deep=True)
    table = f"up_email_race_{uuid4().hex[:12]}"
    settings.database.email_config_table = table
    barrier = Barrier(2)
    original_initialize = MySQLEmailConfigStore._initialize_legacy_table

    def synchronize_before_lock(self, connection, cursor):
        barrier.wait(timeout=10)
        return original_initialize(self, connection, cursor)

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
            cursor.executemany(
                f"""INSERT INTO `{table}`
                (provider, enabled, is_default, from_email, config_json)
                VALUES (%s, 1, %s, %s, %s)""",
                [
                    ("agentmail", 1, "agent@example.invalid", '{"api_key":"agent-key"}'),
                    ("openmail", 0, "open@example.invalid", '{"api_key":"open-key"}'),
                ],
            )
            connection.commit()

        monkeypatch.setattr(
            MySQLEmailConfigStore,
            "_initialize_legacy_table",
            synchronize_before_lock,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(MySQLEmailConfigStore(settings).initialize) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)

        migrated = MySQLEmailConfigStore(settings).list_all()
        assert {record.provider for record in migrated} == {"agentmail", "openmail"}
        assert len({record.config_name for record in migrated}) == 2
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name AS legacy_table_name FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name LIKE %s",
                (settings.database.mysql_database, table + "_legacy_%"),
            )
            assert len(cursor.fetchall()) == 1
    finally:
        monkeypatch.setattr(MySQLEmailConfigStore, "_initialize_legacy_table", original_initialize)
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name AS owned_table FROM information_schema.tables "
                "WHERE table_schema=%s AND (table_name LIKE %s OR table_name=%s)",
                (settings.database.mysql_database, table + "_legacy_%", table + "_migration_stage"),
            )
            for row in cursor.fetchall():
                cursor.execute(f"DROP TABLE IF EXISTS `{row['owned_table']}`")
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            connection.commit()
