from __future__ import annotations

import asyncio

from src.runtime.compaction_worker import CompactionWorker


class Hot:
    def __init__(self): self.cleared = False
    async def list_events(self, session_id, limit=10000): return [{"id": "e1", "type": "message", "payload": {"role": "user", "content": "goal"}}]
    async def acquire_compaction(self, session_id, key): return True
    async def complete_compaction(self, session_id, key): pass
    async def clear_session(self, session_id): self.cleared = True


class Memory:
    def __init__(self): self.args = None
    async def write_turn_memory(self, **kwargs): self.args = kwargs; return ["m1"]


def test_compaction_writes_durable_memory_then_clears_hot_data():
    hot, memory = Hot(), Memory()
    assert asyncio.run(CompactionWorker(hot_memory_store=hot, memory_runtime_service=memory).compact_session(session_id="s1", turn_id="t1", trace_id="tr1"))
    assert memory.args["user_message"] == "goal"
    assert hot.cleared is True


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

        async def acquire_compaction(self, session_id, key):
            self.key = key
            return True

        async def mark_compaction_pending(self, session_id, *, last_event_id):
            self.pending = last_event_id

        async def commit_compaction(self, session_id, *, key, last_event_id):
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

        async def acquire_compaction(self, session_id, key):
            assert key == "e2:v1"
            return True

        async def commit_compaction(self, session_id, *, key, last_event_id):
            assert (key, last_event_id) == ("e2:v1", "e2")
            return True

    hot, memory = PendingHot(), Memory()
    assert asyncio.run(
        CompactionWorker(hot_memory_store=hot, memory_runtime_service=memory).compact_session(
            session_id="s1", turn_id="t1", trace_id="tr1"
        )
    )
    assert memory.args["compaction_key"] == "s1:e2:v1"
