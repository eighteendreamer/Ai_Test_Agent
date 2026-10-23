"""Actual MySQL legacy migration with competing and forcibly interrupted processes."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from test_execution_worker_cli import Processes
from src.application.settings.workspace_settings_service import CHANNEL_ADVANCED_KEY, GENERAL_SETTINGS_KEY, WorkspaceSettingsService
from src.core.config import get_settings
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.schemas.settings import GeneralSettingsPayload


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_CONFIG_TESTS") != "1",
    reason="requires actual MySQL; creates only one random owned configuration table per test")]


INITIALIZER = '''
import json
from pathlib import Path
import sys
import time

from src.application.settings.workspace_settings_service import WorkspaceSettingsService, GENERAL_SETTINGS_KEY, CHANNEL_ADVANCED_KEY
from src.core.config import get_settings
from src.infrastructure.application_config_store import MySQLApplicationConfigStore

legacy, control, worker, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]

def pause(marker, release):
    (control / marker).write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 30
    while not (control / release).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("migration fixture barrier expired: " + marker)
        time.sleep(.02)

class PausedStore(MySQLApplicationConfigStore):
    def get(self, key, **kwargs):
        record = super().get(key, **kwargs)
        if mode == "race" and record is None and key in {GENERAL_SETTINGS_KEY, CHANNEL_ADVANCED_KEY}:
            pause(worker + "-" + key, "release-" + key)
        return record

    def save(self, key, payload, **kwargs):
        record = super().save(key, payload, **kwargs)
        if mode == "interrupt" and key == GENERAL_SETTINGS_KEY:
            pause(worker + "-committed", "never-release")
        return record

store = PausedStore(get_settings())
WorkspaceSettingsService(store, legacy).initialize()
print(json.dumps({key: store.get(key).revision for key in (GENERAL_SETTINGS_KEY, CHANNEL_ADVANCED_KEY)}))
'''


@asynccontextmanager
async def migration_runtime(tmp_path, name):
    evidence = Path(os.getenv("QA_WORKSPACE_MIGRATION_PROCESS_DIR", str(tmp_path / "evidence"))) / name
    evidence.mkdir(parents=True, exist_ok=False)
    legacy, control = tmp_path / "legacy", tmp_path / "control"
    legacy.mkdir()
    control.mkdir()
    originals = {"general_settings.json": {"language": "ja-JP", "reduceMotion": False},
        "channel_advanced_settings.json": {"max_steps": 9, "allowlist": {"qq_users": ["owned-fixture-user"]}}}
    for filename, payload in originals.items():
        (legacy / filename).write_text(json.dumps(payload), encoding="utf-8")
    script = evidence / "initializer.py"
    script.write_text(INITIALIZER, encoding="utf-8")
    settings = get_settings().model_copy(deep=True)
    table = "up_workspace_migration_" + uuid4().hex[:12]
    settings.database.application_config_table = table
    store = MySQLApplicationConfigStore(settings)
    with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (table,))
        assert cursor.fetchone() is None
    processes = Processes(evidence, settings)
    try:
        yield evidence, legacy, control, store, processes, script
    finally:
        await processes.close()
        with mysql_raw_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
            connection.commit()
            cursor.execute("SHOW TABLES LIKE %s", (table,))
            removed = cursor.fetchone() is None
        (evidence / "cleanup.json").write_text(json.dumps({"mysql_table": table, "table_removed": removed,
            "all_children_stopped": all(child.poll() is not None for child, _, _ in processes.children),
            "created_containers": 0}, indent=2), encoding="utf-8")
        assert removed


async def wait_markers(control, names, children):
    async with asyncio.timeout(25):
        while not all((control / name).exists() for name in names):
            assert all(child.poll() is None for child in children), "initializer exited; stdout/stderr preserved"
            await asyncio.sleep(.02)


@pytest.mark.parametrize("database_saved_during_migration", [False, True])
async def test_two_process_legacy_migration_keeps_one_revision_and_database_priority(tmp_path, database_saved_during_migration):
    name = "concurrent-with-save" if database_saved_during_migration else "concurrent"
    async with migration_runtime(tmp_path, name) as (evidence, legacy, control, store, processes, script):
        original_bytes = {path.name: path.read_bytes() for path in legacy.iterdir()}
        children = [processes.start(worker, str(script), str(legacy), str(control), worker, "race") for worker in ("a", "b")]
        await wait_markers(control, [worker + "-" + GENERAL_SETTINGS_KEY for worker in ("a", "b")], children)
        # Both processes have observed a missing row; a saved database value wins.
        if database_saved_during_migration:
            WorkspaceSettingsService(store, legacy).update_general_settings(GeneralSettingsPayload(language="en-US", reduceMotion=True))
        (control / ("release-" + GENERAL_SETTINGS_KEY)).touch()
        await wait_markers(control, [worker + "-" + CHANNEL_ADVANCED_KEY for worker in ("a", "b")], children)
        (control / ("release-" + CHANNEL_ADVANCED_KEY)).touch()
        assert await processes.wait(children[0], timeout=20) == 0
        assert await processes.wait(children[1], timeout=20) == 0
        general, channel = store.get(GENERAL_SETTINGS_KEY), store.get(CHANNEL_ADVANCED_KEY)
        assert general.payload["language"] == ("en-US" if database_saved_during_migration else "ja-JP")
        assert general.payload["reduceMotion"] is database_saved_during_migration
        assert general.revision == channel.revision == 1
        assert channel.payload["max_steps"] == 9
        assert channel.payload["allowlist"]["qq_users"] == ["owned-fixture-user"]
        assert {path.name: path.read_bytes() for path in legacy.iterdir()} == original_bytes
        (evidence / "verification.json").write_text(json.dumps({"both_observed_missing": True,
            "database_saved_during_migration": database_saved_during_migration,
            "general_revision": general.revision, "channel_revision": channel.revision,
            "general": general.payload, "channel": channel.payload, "legacy_files_unchanged": True}, indent=2), encoding="utf-8")


async def test_process_kill_after_first_commit_resumes_without_overwriting_saved_configuration(tmp_path):
    async with migration_runtime(tmp_path, "process-kill") as (evidence, legacy, control, store, processes, script):
        first = processes.start("interrupted", str(script), str(legacy), str(control), "a", "interrupt")
        await wait_markers(control, ["a-committed"], [first])
        assert store.get(GENERAL_SETTINGS_KEY).revision == 1
        assert store.get(GENERAL_SETTINGS_KEY).payload["language"] == "ja-JP"
        assert store.get(CHANNEL_ADVANCED_KEY) is None
        first.kill()
        killed_code = await processes.wait(first, timeout=10)
        assert killed_code != 0
        # A database edit and a broken old file must survive partial-migration recovery.
        WorkspaceSettingsService(store, legacy).update_general_settings(GeneralSettingsPayload(language="en-US", reduceMotion=True))
        (legacy / "general_settings.json").write_text("{", encoding="utf-8")
        for worker in ("resumed", "repeated"):
            child = processes.start(worker, str(script), str(legacy), str(control), worker, "resume")
            assert await processes.wait(child, timeout=20) == 0
        general, channel = store.get(GENERAL_SETTINGS_KEY), store.get(CHANNEL_ADVANCED_KEY)
        assert general.revision == 2 and general.payload["language"] == "en-US"
        assert general.payload["reduceMotion"] is True
        assert channel.revision == 1 and channel.payload["max_steps"] == 9
        assert channel.payload["allowlist"]["qq_users"] == ["owned-fixture-user"]
        assert (legacy / "general_settings.json").read_text(encoding="utf-8") == "{"
        (evidence / "verification.json").write_text(json.dumps({"killed_process_exit_code": killed_code,
            "first_key_committed_before_kill": True, "second_key_absent_before_kill": True,
            "restart_count": 2, "general_revision": general.revision, "channel_revision": channel.revision,
            "general": general.payload, "channel": channel.payload, "broken_legacy_file_not_read": True}, indent=2), encoding="utf-8")
