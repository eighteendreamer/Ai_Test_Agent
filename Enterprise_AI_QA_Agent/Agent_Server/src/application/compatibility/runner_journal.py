"""Local Runner phases; SQLite commits precede execution and delivery.

APIs: https://docs.python.org/3.11/library/sqlite3.html
Ownership: https://www.sqlite.org/lang_transaction.html
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4


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
                self._db.execute("""CREATE TABLE IF NOT EXISTS pending_poll (
                    request_id TEXT PRIMARY KEY, poll_limit INTEGER NOT NULL, sequence INTEGER NOT NULL, epoch TEXT NOT NULL
                )""")
            self.entries()  # Fail closed on an invalid existing phase/payload.
            self.pending_poll()
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
        with self._db:
            self._enqueue(tasks)
        print(f"runner_journal_batch_received count={len(tasks)}")

    def _enqueue(self, tasks: list[dict[str, Any]]) -> None:
        unresolved = {entry["task"]["task_id"] for entry in self.entries() if entry["phase"] == "review"}
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

    def pending_poll(self) -> dict[str, Any] | None:
        rows = self._db.execute("SELECT * FROM pending_poll").fetchall()
        if (len(rows) > 1 or (rows and (not rows[0]["request_id"] or not rows[0]["epoch"]
                or rows[0]["poll_limit"] < 1 or rows[0]["sequence"] < 1))):
            raise RuntimeError("Runner journal contains an invalid pending poll; preserve it for review.")
        return dict(rows[0]) if rows else None

    def begin_poll(self, *, limit: int, sequence: int, epoch: str) -> dict[str, Any]:
        existing = self.pending_poll()
        if existing is not None:
            return existing
        if not 1 <= limit <= 20 or sequence < 1 or not epoch:
            raise ValueError("Identified poll requires limit 1..20, a positive sequence and an epoch.")
        request_id = uuid4().hex
        with self._db:
            self._db.execute("INSERT INTO pending_poll VALUES (?, ?, ?, ?)", (request_id, limit, sequence, epoch))
        print(f"runner_journal_poll_started sequence={sequence} limit={limit}")
        return {"request_id": request_id, "poll_limit": limit, "sequence": sequence, "epoch": epoch}

    def complete_poll(self, request_id: str, tasks: list[dict[str, Any]]) -> None:
        pending = self.pending_poll()
        if pending is None or pending["request_id"] != request_id:
            raise RuntimeError("Runner journal poll acknowledgement does not match its pending request.")
        with self._db:
            self._enqueue(tasks)
            self._db.execute("DELETE FROM pending_poll WHERE request_id = ?", (request_id,))
        print(f"runner_journal_poll_received count={len(tasks)}")

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
