from __future__ import annotations

import asyncio

from src.runtime.compaction_worker import CompactionWorker


class Hot:
    def __init__(self): self.cleared = False
    async def list_events(self, session_id, *, after_id=None, limit=10000):
        return [
            {
                "id": "e1",
                "type": "message",
                "payload": {"role": "user", "content": "goal"},
            }
        ]
    async def acquire_compaction(self, session_id, key, *, owner_id, lease_seconds): return True
    async def renew_compaction(self, session_id, key, *, owner_id, lease_seconds): return True
    async def complete_compaction(self, session_id, key, *, owner_id): pass
    async def clear_session(self, session_id): self.cleared = True


class Memory:
    def __init__(self): self.args = None
    async def write_turn_memory(self, **kwargs): self.args = kwargs; return ["m1"]


def test_compaction_writes_durable_memory_then_clears_hot_data():
    hot, memory = Hot(), Memory()
    assert asyncio.run(CompactionWorker(hot_memory_store=hot, memory_runtime_service=memory).compact_session(session_id="s1", turn_id="t1", trace_id="tr1"))
    assert memory.args["user_message"] == "goal"
    assert hot.cleared is True


def test_fallback_marker_rejects_old_owner_before_hot_data_clear():
    class LostHot(Hot):
        async def complete_compaction(self, session_id, key, *, owner_id):
            return False

    hot = LostHot()
    assert not asyncio.run(CompactionWorker(hot_memory_store=hot, memory_runtime_service=Memory()).compact_session(
        session_id="s1", turn_id="t1", trace_id="tr1",
    ))
    assert hot.cleared is False


def test_rejected_initial_renewal_does_not_start_memory_write(caplog):
    class LostHot(Hot):
        async def renew_compaction(self, session_id, key, *, owner_id, lease_seconds):
            return False

    hot, memory = LostHot(), Memory()
    assert not asyncio.run(CompactionWorker(hot_memory_store=hot, memory_runtime_service=memory).compact_session(
        session_id="s1", turn_id="t1", trace_id="tr1",
    ))
    assert memory.args is None and hot.cleared is False
    assert any(r.getMessage() == "session_hot_memory_compaction_failed" for r in caplog.records)


def test_compaction_uses_cursor_commit_and_stable_key_for_idempotency():
    class CursorHot:
        def __init__(self):
            self.pending = None
            self.committed = None

        async def get_compaction_state(self, session_id):
            return None

        async def list_events(self, session_id, *, after_id=None, limit=10000):
            return [
                {"id": "e1", "type": "message", "payload": {"role": "user", "content": "goal"}},
                {"id": "e2", "type": "message", "payload": {"role": "assistant", "content": "done"}},
                {"id": "e3", "type": "tool.result", "payload": {"status": "completed"}},
            ]

        async def acquire_compaction(self, session_id, key, *, owner_id, lease_seconds):
            self.key = key
            return True

        async def renew_compaction(self, session_id, key, *, owner_id, lease_seconds):
            return True

        async def mark_compaction_pending(self, session_id, *, last_event_id, key, owner_id):
            self.pending = last_event_id

        async def commit_compaction(self, session_id, *, key, last_event_id, owner_id, allow_missing_cursor):
            self.committed = (key, last_event_id)
            return True

    hot, memory = CursorHot(), Memory()
    assert asyncio.run(
        CompactionWorker(hot_memory_store=hot, memory_runtime_service=memory).compact_session(
            session_id="s1", turn_id="t1", trace_id="tr1"
        )
    )
    assert hot.pending == "e3"
    assert hot.committed == ("e3:v1", "e3")
    assert memory.args["compaction_key"] == "s1:e3:v1"
    assert memory.args["context_bundle"]["compaction_key"] == "s1:e3:v1"


def test_failed_compaction_retries_the_same_pending_prefix():
    class PendingHot:
        async def get_compaction_state(self, session_id):
            return {"status": "pending", "last_event_id": "e2", "key": "e2:v1"}

        async def list_events(self, session_id, *, after_id=None, limit=10000):
            return [
                {"id": "e1", "type": "message", "payload": {"role": "user", "content": "goal"}},
                {"id": "e2", "type": "message", "payload": {"role": "assistant", "content": "done"}},
                {"id": "e3", "type": "message", "payload": {"role": "user", "content": "new"}},
            ]

        async def acquire_compaction(self, session_id, key, *, owner_id, lease_seconds):
            assert key == "e2:v1"
            return True

        async def renew_compaction(self, session_id, key, *, owner_id, lease_seconds):
            return True

        async def commit_compaction(self, session_id, *, key, last_event_id, owner_id, allow_missing_cursor):
            assert (key, last_event_id) == ("e2:v1", "e2")
            return True

    hot, memory = PendingHot(), Memory()
    assert asyncio.run(
        CompactionWorker(hot_memory_store=hot, memory_runtime_service=memory).compact_session(
            session_id="s1", turn_id="t1", trace_id="tr1"
        )
    )
    assert memory.args["compaction_key"] == "s1:e2:v1"


def test_compaction_falls_back_to_durable_events_when_redis_hot_memory_is_unavailable():
    class UnavailableHot:
        async def get_compaction_state(self, session_id):
            return None

        async def list_events(self, session_id, *, after_id=None, limit=10000):
            raise ConnectionError("redis unavailable")

        async def acquire_compaction(self, session_id, key, *, owner_id, lease_seconds):
            return True

        async def renew_compaction(self, session_id, key, *, owner_id, lease_seconds):
            return True

        async def mark_compaction_pending(self, session_id, *, last_event_id, key, owner_id):
            self.pending = last_event_id

        async def commit_compaction(self, session_id, *, key, last_event_id, owner_id, allow_missing_cursor):
            self.committed = (key, last_event_id)
            return True

    class DurableEvents:
        async def read_event_page(self, session_id, *, limit, after_event_id=None):
            if after_event_id:
                return []
            return [
                {"id": "e1", "type": "message", "payload": {"role": "user", "content": "goal"}},
                {"id": "e2", "type": "turn.completed", "payload": {"role": "assistant", "content": "done"}},
            ]

    hot, memory = UnavailableHot(), Memory()
    assert asyncio.run(
        CompactionWorker(
            hot_memory_store=hot,
            memory_runtime_service=memory,
            durable_event_store=DurableEvents(),
        ).compact_session(session_id="s1", turn_id="t1", trace_id="tr1")
    )
    assert hot.pending == "e2"
    assert hot.committed == ("e2:v1", "e2")
    assert memory.args["user_message"] == "goal"
    assert memory.args["assistant_message"] == "done"


def test_hot_memory_type_error_is_logged_and_uses_durable_fallback(caplog):
    class BrokenHot:
        def __init__(self):
            self.calls = 0

        async def list_events(self, session_id, *, after_id=None, limit=10000):
            self.calls += 1
            raise TypeError("invalid event payload shape")

    class DurableEvents:
        def __init__(self):
            self.calls = 0

        async def read_event_page(self, session_id, *, limit, after_event_id=None):
            self.calls += 1
            return []

    hot = BrokenHot()
    durable = DurableEvents()
    result = asyncio.run(
        CompactionWorker(
            hot_memory_store=hot,
            memory_runtime_service=Memory(),
            durable_event_store=durable,
        ).compact_session(session_id="s1", turn_id="t1", trace_id="tr1")
    )

    assert result is False
    assert hot.calls == 1
    assert durable.calls == 1
    failure = next(
        (
            record
            for record in caplog.records
            if record.name == "src.runtime.compaction_worker"
            and record.getMessage()
            == "session_hot_memory_read_failed_using_durable_events"
        ),
        None,
    )
    assert failure is not None
    assert failure.session_id == "s1"
    assert failure.error_type == "TypeError"


def test_concurrent_compaction_writes_one_summary_per_session():
    """Session switch racing a compaction pass must still fold a session once."""

    class Contended:
        def __init__(self):
            self.holders = {}
            self.cleared = []

        async def get_compaction_state(self, session_id):
            return None

        async def list_events(self, session_id, *, after_id=None, limit=10000):
            return [{
                "id": f"{session_id}-e1",
                "type": "message",
                "payload": {"role": "user", "content": f"goal {session_id}"},
            }]

        async def acquire_compaction(self, session_id, key, *, owner_id, lease_seconds):
            # Redis SETNX semantics: the key, not the session, is the lock identity.
            if key in self.holders:
                return False
            self.holders[key] = session_id
            return True

        async def renew_compaction(self, session_id, key, *, owner_id, lease_seconds):
            return True

        async def mark_compaction_pending(self, session_id, *, last_event_id, key, owner_id):
            return None

        async def commit_compaction(self, session_id, *, key, last_event_id, owner_id, allow_missing_cursor):
            return True

        async def clear_session(self, session_id):
            self.cleared.append(session_id)

    class WritingMemory:
        def __init__(self):
            self.keys = []

        async def write_turn_memory(self, **kwargs):
            # Yield so the second caller of the same session really races the first.
            await asyncio.sleep(0)
            self.keys.append(kwargs["compaction_key"])
            return ["m1"]

    store, memory = Contended(), WritingMemory()
    worker = CompactionWorker(hot_memory_store=store, memory_runtime_service=memory)

    async def race():
        return await asyncio.gather(
            worker.compact_session(session_id="s1", turn_id="t1", trace_id="tr-a"),
            worker.compact_session(session_id="s1", turn_id="t2", trace_id="tr-b"),
            worker.compact_session(session_id="s2", turn_id="t3", trace_id="tr-c"),
        )

    results = asyncio.run(race())
    assert results.count(True) == 2
    assert len(memory.keys) == 2
    assert sorted({key.split(":", 1)[0] for key in memory.keys}) == ["s1", "s2"]
    assert len(store.holders) == 2
