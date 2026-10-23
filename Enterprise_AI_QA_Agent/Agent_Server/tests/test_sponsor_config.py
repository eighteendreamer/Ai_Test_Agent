from __future__ import annotations

import asyncio
import os
from threading import Barrier
from uuid import uuid4

import pytest

from src.infrastructure.sponsor_config_store import (
    DEFAULT_SPONSORS,
    MySQLSponsorConfigStore,
)
from src.infrastructure.sqlalchemy_runtime import SQLAlchemyCursor, mysql_raw_connection
from src.core.config import get_settings
from src.schemas.sponsor import SponsorRecord


def test_to_public_prefixes_local_logo_file() -> None:
    record = SponsorRecord(
        id=1,
        name="E-API",
        logo_file="e-api.png",
        website_url="https://api.ewo.so/",
        sponsor_type="中转站",
        sort_order=0,
        enabled=True,
    )
    public = MySQLSponsorConfigStore._to_public(record)
    assert public.logo_url == "/sponsors/e-api.png"
    assert public.name == "E-API"
    assert public.website_url == "https://api.ewo.so/"
    assert public.sponsor_type == "中转站"


def test_to_public_keeps_remote_logo_url() -> None:
    record = SponsorRecord(
        id=2,
        name="Remote",
        logo_file="https://cdn.example.com/logo.png",
        website_url="https://example.com/",
    )
    public = MySQLSponsorConfigStore._to_public(record)
    assert public.logo_url == "https://cdn.example.com/logo.png"


def test_to_public_normalizes_leading_slash() -> None:
    record = SponsorRecord(
        id=3,
        name="Slash",
        logo_file="/e-api.png",
        website_url="https://example.com/",
    )
    public = MySQLSponsorConfigStore._to_public(record)
    assert public.logo_url == "/sponsors/e-api.png"


def test_default_sponsors_seed_is_complete() -> None:
    assert len(DEFAULT_SPONSORS) >= 1
    for sponsor in DEFAULT_SPONSORS:
        assert sponsor.name.strip()
        assert sponsor.logo_file.strip()
        assert sponsor.website_url.startswith("https://")
        assert sponsor.sponsor_type.strip()
        assert sponsor.enabled


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1",
    reason="requires configured MySQL; uses a random isolated sponsor table",
)
async def test_concurrent_mysql_sponsor_initialization_seeds_once(monkeypatch):
    settings = get_settings().model_copy(deep=True)
    table = f"up_sponsor_{uuid4().hex[:12]}"
    settings.database.sponsor_config_table = table
    store = MySQLSponsorConfigStore(settings)
    other_store = MySQLSponsorConfigStore(settings)
    with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (table,))
        assert cursor.fetchone() is None
        store._create_table(cursor)
        connection.commit()

    count_barrier = Barrier(2)
    original_execute = SQLAlchemyCursor.execute
    original_fetchone = SQLAlchemyCursor.fetchone

    def observe_execute(cursor, statement, parameters=None):
        cursor._sponsor_test_statement = statement
        return original_execute(cursor, statement, parameters)

    def synchronize_empty_seed_count(cursor):
        row = original_fetchone(cursor)
        statement = getattr(cursor, "_sponsor_test_statement", "")
        if "SELECT COUNT(*) AS total" in statement and f"FROM `{table}`" in statement and row and int(row["total"]) == 0:
            count_barrier.wait(timeout=10)
        return row

    monkeypatch.setattr(SQLAlchemyCursor, "execute", observe_execute)
    monkeypatch.setattr(SQLAlchemyCursor, "fetchone", synchronize_empty_seed_count)
    try:
        outcomes = await asyncio.gather(
            asyncio.to_thread(store.initialize),
            asyncio.to_thread(other_store.initialize),
            return_exceptions=True,
        )
        assert outcomes == [None, None]
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT name FROM `{table}` ORDER BY id")
            assert [row["name"] for row in cursor.fetchall()] == [s.name for s in DEFAULT_SPONSORS]
            cursor.execute(f"UPDATE `{table}` SET logo_file=%s WHERE name=%s", ("custom-logo.svg", "E-API"))
            connection.commit()
        await asyncio.to_thread(store.initialize)
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"SELECT logo_file FROM `{table}` WHERE name=%s", ("E-API",))
            assert cursor.fetchone()["logo_file"] == "custom-logo.svg"
    finally:
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            connection.commit()
