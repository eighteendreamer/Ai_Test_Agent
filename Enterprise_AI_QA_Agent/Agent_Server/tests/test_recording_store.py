"""P0-2 PostgresRecordingStore 测试。

- 不连库单测：SQL 组装与行映射、批量幂等语义（批内去重 + ON CONFLICT）；
- 连库集成：RUN_LIVE_RECORDING_PG=1 时运行，覆盖重复批次重试不产生重复行。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import src.infrastructure.recording_store as recording_store_module
from src.core.config import Settings
from src.infrastructure.recording_store import PostgresRecordingStore
from src.schemas.recording import (
    RecordingSession,
    RecordingStatus,
    RecorderEvent,
)


LIVE_PG = pytest.mark.skipif(
    __import__("os").getenv("RUN_LIVE_RECORDING_PG") != "1",
    reason="set RUN_LIVE_RECORDING_PG=1 to run the live PostgreSQL recording store tests",
)


def _event(seq: int, event_type: str = "click") -> RecorderEvent:
    return RecorderEvent(
        seq=seq,
        type=event_type,
        timestamp=datetime(2026, 8, 24, 10, 0, seq, tzinfo=timezone.utc),
        page={"url": "https://example.com/login", "title": "Login"},
        target={"locators": {"id": "login-submit"}, "tag": "BUTTON"},
        pixel={"viewport_point": {"x": 712, "y": 503}},
        screenshot_ref=f"artifacts/rec_{seq}.png",
    )


class FakeCursor:
    def __init__(self, script: list[tuple[str, object]] | None = None):
        self.calls: list[tuple[str, tuple | None]] = []
        self._script = list(script or [])
        self.rowcount = 0

    def _consume(self, kind: str):
        """仅当队首匹配 kind 时消费（execute 与 fetchone 各取所需）。"""
        if self._script and self._script[0][0] == kind:
            return self._script.pop(0)
        return None

    def execute(self, statement: str, parameters: tuple | None = None) -> None:
        self.calls.append((" ".join(statement.split()), parameters))
        self.rowcount = 0
        step = self._consume("rowcount")
        if step is not None:
            self.rowcount = step[1]

    def fetchone(self):
        step = self._consume("fetchone")
        return step[1] if step is not None else None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self) -> FakeCursor:
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_connect(cursor: FakeCursor) -> None:
    recording_store_module.postgres_connect = lambda settings: FakeConnection(cursor)


def _restore_connect(original) -> None:
    recording_store_module.postgres_connect = original


def test_initialize_creates_tables_with_idempotent_constraints() -> None:
    cursor = FakeCursor()
    store = PostgresRecordingStore(Settings())
    original_connect = recording_store_module.postgres_connect
    _patch_connect(cursor)
    try:
        store._initialize_sync()
    finally:
        _restore_connect(original_connect)
    statements = [statement for statement, _ in cursor.calls]
    assert any(
        "CREATE TABLE IF NOT EXISTS ui_recording (" in statement for statement in statements
    )
    event_ddl = next(s for s in statements if "CREATE TABLE IF NOT EXISTS ui_recording_event" in s)
    assert "UNIQUE(recording_id, seq)" in event_ddl


def test_create_session_inserts_with_upsert() -> None:
    cursor = FakeCursor()
    store = PostgresRecordingStore(Settings())
    session = RecordingSession(project_id="proj-1", entry_url="https://example.com")
    original_connect = recording_store_module.postgres_connect
    _patch_connect(cursor)
    try:
        store._create_session_sync(session)
    finally:
        _restore_connect(original_connect)
    statement, params = cursor.calls[0]
    assert statement.startswith("INSERT INTO ui_recording")
    assert "ON CONFLICT (id) DO UPDATE" in statement
    assert params[0] == session.id
    assert params[5] == "launching"


def test_append_events_counts_and_idempotent_conflict() -> None:
    store = PostgresRecordingStore(Settings())
    events = [_event(0), _event(1), _event(2)]
    original_connect = recording_store_module.postgres_connect

    # 第一次提交：库中 0 行，三条全部插入
    cursor = FakeCursor(
        script=[("fetchone", {"cnt": 0}), ("rowcount", 1), ("rowcount", 1), ("rowcount", 1)]
    )
    _patch_connect(cursor)
    try:
        ack = store._append_events_sync("rec-1", events)
    finally:
        _restore_connect(original_connect)

    assert ack.accepted == 3
    assert ack.duplicates == 0

    insert_calls = [c for c in cursor.calls if c[0].startswith("INSERT INTO ui_recording_event")]
    assert len(insert_calls) == 3
    for statement, _ in insert_calls:
        assert "ON CONFLICT (recording_id, seq) DO NOTHING" in statement
        # 流水不可变：只 INSERT，不 UPDATE 事件行
        assert "UPDATE" not in statement.split("ON CONFLICT")[0]

    # 批内重复 + 重试同批次：库中已有 3 行，全部冲突
    cursor2 = FakeCursor(
        script=[("fetchone", {"cnt": 3}), ("rowcount", 0), ("rowcount", 0), ("rowcount", 0)]
    )
    _patch_connect(cursor2)
    try:
        ack2 = store._append_events_sync("rec-1", events + [_event(1)])
    finally:
        _restore_connect(original_connect)

    # 4 条提交 → 批内去重 3 条 → 全部与库冲突
    assert ack2.accepted == 0
    assert ack2.duplicates == 4

    # step_count 更新为库中总数（对账口径）
    update_calls = [c for c in cursor2.calls if c[0].startswith("UPDATE ui_recording")]
    assert update_calls and update_calls[0][1][0] == 3


def test_append_empty_batch_is_noop() -> None:
    cursor = FakeCursor()
    store = PostgresRecordingStore(Settings())
    ack = store._append_events_sync("rec-1", [])
    assert ack.accepted == 0 and ack.duplicates == 0
    assert cursor.calls == []


def test_update_status_builds_dynamic_set() -> None:
    cursor = FakeCursor(
        script=[("fetchone", None)]  # _get_session_sync 回查返回 None
    )
    store = PostgresRecordingStore(Settings())
    original_connect = recording_store_module.postgres_connect
    _patch_connect(cursor)
    try:
        result = store._update_status_sync(
            "rec-1",
            RecordingStatus.active,
            started_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            ended_at=None,
            finalize_metrics={"actions": 3},
        )
    finally:
        _restore_connect(original_connect)
    assert result is None  # 回查不存在 → None（此处只验证 SQL 组装）
    update = next(c for c in cursor.calls if c[0].startswith("UPDATE ui_recording SET"))
    assert "status = %s" in update[0]
    assert "started_at = %s" in update[0]
    assert "ended_at" not in update[0]
    assert "finalize_metrics = %s::jsonb" in update[0]


def test_row_mapping_session_roundtrip() -> None:
    now = datetime(2026, 8, 24, 12, 0, 0)
    row = {
        "id": "rec-9",
        "project_id": "proj-1",
        "name": "支付流程",
        "entry_url": "https://example.com/pay",
        "driver_kind": "cdp-attach",
        "status": "completed",
        "session_id": "sess-1",
        "approval_id": "appr-1",
        "step_count": 12,
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "ended_at": now,
        "finalize_metrics": {"actions": 12, "degraded": False},
        "metadata": {"k": "v"},
    }
    session = PostgresRecordingStore(Settings())._row_to_session(row)
    assert session.id == "rec-9"
    assert session.driver_kind.value == "cdp-attach"
    assert session.status is RecordingStatus.completed
    assert session.step_count == 12
    assert session.finalize_metrics == {"actions": 12, "degraded": False}


def test_row_mapping_event_prefers_index_columns() -> None:
    payload = _event(5).model_dump(mode="json")
    # payload 与索引列不一致时以索引列为准
    payload["seq"] = 999
    row = {
        "id": "evt-1",
        "recording_id": "rec-9",
        "seq": 5,
        "type": "fill",
        "timestamp": datetime(2026, 8, 24, 12, 0, 5),
        "payload": payload,
        "screenshot_ref": "artifacts/rec_5.png",
    }
    event = PostgresRecordingStore(Settings())._row_to_event(row)
    assert event.seq == 5
    assert event.type == "fill"
    assert event.screenshot_ref == "artifacts/rec_5.png"
    assert event.target["locators"]["id"] == "login-submit"


def test_discard_marks_discarded_keeps_rows() -> None:
    now = datetime(2026, 8, 24, 12, 0, 0)
    session_row = {
        "id": "rec-9",
        "project_id": "proj-1",
        "name": "",
        "entry_url": "https://example.com",
        "driver_kind": "embedded",
        "status": "active",
        "session_id": None,
        "approval_id": None,
        "step_count": 3,
        "created_at": now,
        "updated_at": now,
        "started_at": now,
        "ended_at": None,
        "finalize_metrics": {},
        "metadata": {},
    }
    cursor = FakeCursor(
        script=[
            ("fetchone", dict(session_row)),  # _discard 内首次回查
            ("fetchone", {**session_row, "status": "discarded", "ended_at": now}),  # update_status 末尾回查
        ]
    )
    store = PostgresRecordingStore(Settings())
    original_connect = recording_store_module.postgres_connect
    _patch_connect(cursor)
    try:
        discarded = store._discard_session_sync("rec-9")
    finally:
        _restore_connect(original_connect)

    assert discarded is not None
    assert discarded.status is RecordingStatus.discarded
    update = next(c for c in cursor.calls if c[0].startswith("UPDATE ui_recording SET"))
    assert update[1][0] == "discarded"
    # 销毁不删除事件流水（保留审计）
    assert not any("DELETE FROM ui_recording_event" in c[0] for c in cursor.calls)


# ----------------------------------------------------------------------
# 连库集成（RUN_LIVE_RECORDING_PG=1）
# ----------------------------------------------------------------------
@LIVE_PG
def test_live_append_events_batch_retry_is_idempotent() -> None:
    settings = Settings()
    store = PostgresRecordingStore(settings)
    store._initialize_sync()

    session = RecordingSession(project_id="proj-live", entry_url="https://example.com")
    store._create_session_sync(session)
    try:
        events = [_event(i) for i in range(10)]
        ack1 = store._append_events_sync(session.id, events)
        assert ack1.accepted == 10 and ack1.duplicates == 0

        # 网络重试：同批次重复提交 + 批内重复
        ack2 = store._append_events_sync(session.id, events + [_event(3)])
        assert ack2.accepted == 0
        assert ack2.duplicates == 11

        stored = store._get_events_sync(session.id)
        assert len(stored) == 10
        assert [e.seq for e in stored] == list(range(10))

        refreshed = store._get_session_sync(session.id)
        assert refreshed is not None and refreshed.step_count == 10
    finally:
        from src.infrastructure.postgres_runtime import postgres_connect

        with postgres_connect(settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {settings.postgres_recording_event_table} WHERE recording_id = %s",
                    (session.id,),
                )
                cur.execute(
                    f"DELETE FROM {settings.postgres_recording_table} WHERE id = %s",
                    (session.id,),
                )
