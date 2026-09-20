"""Gate-the-gate: the rollback preflight must see DDL split across f-string chunks."""
from __future__ import annotations

import textwrap
from pathlib import Path

from scripts.preflight_release_rollback_safety import scan


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


def test_f_string_split_ddl_and_banlist_strings_are_classified_correctly(tmp_path: Path) -> None:
    _write(tmp_path, "additive_store.py", '''
        class Store:
            async def initialize(self):
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table} (id TEXT)
                """)
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS fencing_token BIGINT")
        ''')
    _write(tmp_path, "destructive_store.py", '''
        class Store:
            async def initialize(self):
                cur.execute(
                    f"""
                    ALTER TABLE {table}
                        DROP COLUMN IF EXISTS transport,
                        DROP COLUMN IF EXISTS args
                    """
                )
        ''')
    _write(tmp_path, "command_guard.py", '''
        BANNED = ("rm -rf", "DROP TABLE", "DELETE FROM")
        ''')

    report = scan(tmp_path)

    blockers = {(item["file"], item["statement"]) for item in report["blockers"]}
    assert blockers == {
        ("destructive_store.py", "ALTER TABLE DROP COLUMN"),
    }, blockers
    additive = {item["file"] for item in report["additive"]}
    assert additive == {"additive_store.py"}
