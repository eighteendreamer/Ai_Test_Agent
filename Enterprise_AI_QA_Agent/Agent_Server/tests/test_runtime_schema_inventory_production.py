"""Fresh full-lifespan schema inventory and restart side-effect acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from uuid import uuid4

import httpx
import pytest
from psycopg import sql

from src.application.performance.perf_metrics_store import PerfMetricsStore
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.runtime.postgres_resource_cleanup_store import PostgresResourceCleanupStore
from test_execution_worker_cli import free_port, isolated_runtime, wait_api


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_RUNTIME_SCHEMA_INVENTORY") != "1",
        reason="fresh PostgreSQL database, isolated MySQL tables, Redis and full production API",
    ),
]


def _configured_tables(settings) -> tuple[set[str], set[str]]:
    database = settings.database.model_dump(mode="python")
    postgres = {
        value
        for key, value in database.items()
        if key.startswith("postgres_") and key.endswith("_table")
    }
    mysql = {
        value
        for key, value in database.items()
        if key == "llm_model_table" or key.endswith("_config_table")
    }
    return postgres, mysql


def _postgres_inventory(settings) -> dict:
    with postgres_connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename"
        )
        tables = [row["tablename"] for row in cursor.fetchall()]
        counts = {}
        for table in tables:
            cursor.execute(
                sql.SQL("SELECT count(*) AS count FROM {}").format(sql.Identifier(table))
            )
            counts[table] = cursor.fetchone()["count"]
    return {"tables": tables, "row_counts": counts}


def _postgres_schema(settings) -> dict:
    """Read a structural snapshot without reading application row values."""
    with postgres_connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            "SELECT current_database() AS database_name, current_schema() AS schema_name, "
            "current_setting('server_version') AS server_version"
        )
        identity = dict(cursor.fetchone())
        cursor.execute(
            "SELECT c.relname AS table_name "
            "FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=current_schema() AND c.relkind='r' "
            "ORDER BY c.relname"
        )
        tables = [row["table_name"] for row in cursor.fetchall()]
        structures = {}
        for table in tables:
            cursor.execute(
                "SELECT a.attname AS name, pg_catalog.format_type(a.atttypid,a.atttypmod) AS type, "
                "a.attnotnull AS not_null, a.attidentity AS identity, a.attgenerated AS generated, "
                "pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS default "
                "FROM pg_catalog.pg_attribute a "
                "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum "
                "WHERE n.nspname=current_schema() AND c.relname=%s "
                "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
                (table,),
            )
            columns = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT con.contype AS type, pg_catalog.pg_get_constraintdef(con.oid,true) AS definition "
                "FROM pg_catalog.pg_constraint con "
                "JOIN pg_catalog.pg_class c ON c.oid=con.conrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname=current_schema() AND c.relname=%s "
                "ORDER BY con.contype,pg_catalog.pg_get_constraintdef(con.oid,true)",
                (table,),
            )
            constraints = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT indexdef FROM pg_catalog.pg_indexes "
                "WHERE schemaname=current_schema() AND tablename=%s ORDER BY indexdef",
                (table,),
            )
            indexes = [row["indexdef"] for row in cursor.fetchall()]
            structures[table] = {
                "columns": columns,
                "constraints": constraints,
                "indexes": indexes,
            }
    return {"identity": identity, "tables": structures}


def _compare_postgres_schema(current: dict, fresh: dict) -> dict:
    current_tables = set(current["tables"])
    fresh_tables = set(fresh["tables"])
    changed = {}
    physical_only = []

    def normalized(structure: dict) -> dict:
        columns = {}
        for column in structure["columns"]:
            item = dict(column)
            item["type"] = {
                "timestamp(6) with time zone": "timestamp with time zone",
                "timestamp(6) without time zone": "timestamp without time zone",
            }.get(item["type"], item["type"])
            columns[item.pop("name")] = item
        return {
            "columns": columns,
            "constraints": sorted(
                (item["type"], item["definition"])
                for item in structure["constraints"]
            ),
            "indexes": sorted(structure["indexes"]),
        }

    for table in sorted(current_tables.intersection(fresh_tables)):
        current_table = current["tables"][table]
        fresh_table = fresh["tables"][table]
        current_normalized = normalized(current_table)
        fresh_normalized = normalized(fresh_table)
        if current_normalized == fresh_normalized:
            if current_table != fresh_table:
                physical_only.append(table)
            continue
        changed[table] = {
            key: {
                "current": current_normalized[key],
                "fresh": fresh_normalized[key],
            }
            for key in ("columns", "constraints", "indexes")
            if current_normalized[key] != fresh_normalized[key]
        }
    return {
        "missing_from_current": sorted(fresh_tables - current_tables),
        "only_in_current": sorted(current_tables - fresh_tables),
        "changed_common_tables": changed,
        "compatible_physical_only_differences": physical_only,
        "matching_common_table_count": len(current_tables.intersection(fresh_tables)) - len(changed),
    }


def _mysql_inventory(settings) -> dict:
    with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.tables "
            "WHERE table_schema=DATABASE() ORDER BY TABLE_NAME"
        )
        all_tables = [row["TABLE_NAME"] for row in cursor.fetchall()]
        configured = _configured_tables(settings)[1]
        tables = sorted(configured.intersection(all_tables))
        counts = {}
        for table in tables:
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", table)
            cursor.execute(f"SELECT count(*) AS count FROM `{table}`")
            counts[table] = cursor.fetchone()["count"]
        application_table = settings.database.application_config_table
        cursor.execute(
            f"SELECT scope_key, config_key, revision FROM `{application_table}` "
            "ORDER BY scope_key, config_key"
        )
        configuration_rows = [dict(row) for row in cursor.fetchall()]
    return {
        "configured_tables": tables,
        "configured_row_counts": counts,
        "application_config_records": configuration_rows,
    }


def _inventory(settings) -> dict:
    configured_postgres, configured_mysql = _configured_tables(settings)
    postgres = _postgres_inventory(settings)
    mysql = _mysql_inventory(settings)
    actual_postgres = set(postgres["tables"])
    actual_mysql = set(mysql["configured_tables"])
    return {
        "configured": {
            "postgres": sorted(configured_postgres),
            "mysql": sorted(configured_mysql),
        },
        "postgres": {
            **postgres,
            "missing_configured": sorted(configured_postgres - actual_postgres),
            "derived_or_legacy": sorted(actual_postgres - configured_postgres),
        },
        "mysql": {
            **mysql,
            "missing_configured": sorted(configured_mysql - actual_mysql),
        },
    }


async def test_fresh_full_api_initialization_and_restart_have_stable_schema_and_data(tmp_path):
    evidence = Path(
        os.getenv(
            "QA_RUNTIME_SCHEMA_INVENTORY_DIR",
            str(tmp_path / "runtime-schema-inventory"),
        )
    )
    evidence.mkdir(parents=True, exist_ok=False)
    current_schema = _postgres_schema(get_settings())

    async with isolated_runtime(tmp_path, seed_workspace_config=False) as (
        settings,
        processes,
        _,
        _,
        _,
    ):
        before = _inventory(settings)
        assert before["postgres"]["tables"] == []
        assert before["mysql"]["configured_tables"] == [
            settings.database.application_config_table
        ]
        assert before["mysql"]["application_config_records"] == []

        port = free_port()
        arguments = (
            "-m",
            "uvicorn",
            "src.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        )
        api = processes.start("runtime-schema-api-first", *arguments)
        base_url = f"http://127.0.0.1:{port}/api/v1"
        async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
            await wait_api(client, api)
            created = await client.post(
                "/projects",
                json={
                    "project_key": "schema-inventory-" + uuid4().hex[:12],
                    "name": "运行时结构初始化验收",
                },
            )
            assert created.status_code == 201, created.text
            project = created.json()
            saved = await client.put(
                "/settings/general",
                json={
                    "language": "en-US",
                    "modelOutputLanguage": "en-US",
                    "reduceMotion": True,
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["reduceMotion"] is True

        api_only = _inventory(settings)
        assert api_only["postgres"]["missing_configured"] == sorted(
            [
                settings.database.postgres_perf_runs_table,
                settings.database.postgres_resource_cleanup_table,
            ]
        )
        assert api_only["mysql"]["missing_configured"] == []
        assert "alembic_version" not in api_only["postgres"]["tables"]
        assert (
            api_only["postgres"]["row_counts"][settings.database.postgres_project_table]
            == 1
        )

        performance_metrics = PerfMetricsStore(settings)
        assert await performance_metrics.initialize()
        await PostgresResourceCleanupStore(settings).initialize()
        first = _inventory(settings)
        fresh_schema = _postgres_schema(settings)
        schema_comparison = _compare_postgres_schema(current_schema, fresh_schema)
        assert first["postgres"]["missing_configured"] == []
        first_keys = {
            (row["scope_key"], row["config_key"])
            for row in first["mysql"]["application_config_records"]
        }
        assert ("global", "workspace.general") in first_keys
        assert ("global", "channel.advanced") in first_keys
        assert first["mysql"]["configured_row_counts"][settings.database.sponsor_config_table] > 0

        await processes.stop(api)
        restarted = processes.start("runtime-schema-api-restarted", *arguments)
        async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
            await wait_api(client, restarted)
            project_response = await client.get(f"/projects/{project['id']}")
            assert project_response.status_code == 200, project_response.text
            assert project_response.json()["name"] == "运行时结构初始化验收"
            settings_response = await client.get("/settings/general")
            assert settings_response.status_code == 200, settings_response.text
            assert settings_response.json()["language"] == "en-US"
            assert settings_response.json()["reduceMotion"] is True

        second = _inventory(settings)
        assert second["postgres"]["tables"] == first["postgres"]["tables"]
        assert second["mysql"]["configured_tables"] == first["mysql"]["configured_tables"]
        assert second["postgres"]["row_counts"] == first["postgres"]["row_counts"]
        assert (
            second["mysql"]["configured_row_counts"]
            == first["mysql"]["configured_row_counts"]
        )
        assert (
            second["mysql"]["application_config_records"]
            == first["mysql"]["application_config_records"]
        )

        summary = {
            "scope": (
                "fresh isolated PostgreSQL database, five isolated MySQL configuration tables, "
                "full production API lifespan and restart"
            ),
            "alembic_run_by_api": False,
            "project_survived_restart": True,
            "mysql_setting_survived_restart": True,
            "current_to_fresh_schema": schema_comparison,
            "current_schema_identity": current_schema["identity"],
            "fresh_schema_identity": fresh_schema["identity"],
            "before": before,
            "api_start": api_only,
            "after_component_initializers": first,
            "second_start": second,
        }
        (evidence / "inventory.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
