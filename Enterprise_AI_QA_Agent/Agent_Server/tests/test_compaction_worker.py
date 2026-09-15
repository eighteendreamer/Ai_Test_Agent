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
