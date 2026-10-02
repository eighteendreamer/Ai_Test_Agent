"""Local Runner phases; SQLite commits precede execution and delivery.

APIs: https://docs.python.org/3.11/library/sqlite3.html
Ownership: https://www.sqlite.org/lang_transaction.html
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class RunnerJournal:
    def __init__(self, path: str | Path | None, *, base_url: str, runner_id: str) -> None:
        self.durable = path is not None
        self._owner: sqlite3.Connection | None = None
        self._db: sqlite3.Connection | None = None
        try:
            if path is not None:
                target = Path(path).expanduser().resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                # No upstream counterpart (checked: Runner JSON store, task
                # outbox): hold ownership separately so phases can still commit.
                self._owner = sqlite3.connect(str(target) + ".lock", timeout=0)
                self._owner.execute("BEGIN EXCLUSIVE")
                database = str(target)
            else:
                database = ":memory:"
            self._db = sqlite3.connect(database)
            self._db.row_factory = sqlite3.Row
            with self._db:
                self._db.execute("CREATE TABLE IF NOT EXISTS identity (value TEXT NOT NULL)")
                identity = json.dumps([1, base_url.rstrip("/"), runner_id], ensure_ascii=False)
                row = self._db.execute("SELECT value FROM identity").fetchone()
                if row is None:
                    self._db.execute("INSERT INTO identity VALUES (?)", (identity,))
                elif row["value"] != identity:
                    raise RuntimeError("Runner journal identity/version does not match this client.")
                self._db.execute("""CREATE TABLE IF NOT EXISTS entries (
                    assignment TEXT PRIMARY KEY, task TEXT NOT NULL, phase TEXT NOT NULL,
                    payload TEXT, reason TEXT
                )""")
            self.entries()  # Fail closed on an invalid existing phase/payload.
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        try:
            if self._db is not None:
                self._db.close()
        finally:
            if self._owner is not None:
                self._owner.close()
            self._db = self._owner = None

    def enqueue(self, tasks: list[dict[str, Any]]) -> None:
        unresolved = {entry["task"]["task_id"] for entry in self.entries() if entry["phase"] == "review"}
        with self._db:
            for task in tasks:
                task_id = str(task.get("task_id") or "")
                assignment = (task.get("metadata") or {}).get("assignment_id")
                if not task_id or (self.durable and not assignment):
                    raise ValueError("Durable Runner batch requires task_id and assignment_id.")
                key = assignment or task_id  # Legacy in-process SDK compatibility.
                serialized = json.dumps(task, ensure_ascii=False, allow_nan=False)
                existing = self._db.execute("SELECT task FROM entries WHERE assignment = ?", (key,)).fetchone()
                if existing is not None:
                    if existing["task"] != serialized:
                        raise ValueError("Runner batch reused an assignment with different task data.")
                    continue
                self._db.execute(
                    "INSERT INTO entries (assignment, task, phase, reason) VALUES (?, ?, ?, ?)",
                    (key, serialized, "review" if task_id in unresolved else "queued",
                     "unresolved_prior_attempt" if task_id in unresolved else None),
                )
                print(f"runner_journal_received task={task_id} requires_review={task_id in unresolved}")

    def entries(self) -> list[dict[str, Any]]:
        entries = [
            {**dict(row), "task": json.loads(row["task"]),
             "payload": json.loads(row["payload"]) if row["payload"] is not None else None}
            for row in self._db.execute("SELECT * FROM entries ORDER BY rowid")
        ]
        for entry in entries:
            task, payload = entry["task"], entry["payload"]
            if (
                entry["phase"] not in {"queued", "executing", "executed", "uploading", "uploading_idempotent", "report_ready", "review", "conflict"}
                or not isinstance(task, dict) or not task.get("task_id")
                or (self.durable and (task.get("metadata") or {}).get("assignment_id") != entry["assignment"])
                or (entry["phase"] in {"executed", "uploading", "uploading_idempotent", "report_ready"} and not isinstance(payload, dict))
            ):
                raise RuntimeError("Runner journal contains an invalid entry; preserve it for recovery review.")
        return entries

    def update(self, assignment: str, phase: str, *, payload: dict[str, Any] | None = None,
               reason: str | None = None) -> None:
        with self._db:
            if payload is None:
                self._db.execute("UPDATE entries SET phase = ?, reason = ? WHERE assignment = ?",
                                 (phase, reason, assignment))
            else:
                serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                self._db.execute("UPDATE entries SET phase = ?, payload = ?, reason = ? WHERE assignment = ?",
                                 (phase, serialized, reason, assignment))
        print(f"runner_journal_phase phase={phase} assignment={assignment} reason={reason or 'none'}")

    def acknowledge(self, assignment: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM entries WHERE assignment = ?", (assignment,))
