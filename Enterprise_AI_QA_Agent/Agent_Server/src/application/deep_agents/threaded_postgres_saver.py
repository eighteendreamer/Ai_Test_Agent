from __future__ import annotations

import asyncio

from langgraph.checkpoint.postgres import PostgresSaver


class ThreadedPostgresSaver(PostgresSaver):
    """Expose official sync persistence without changing Windows' event loop.

    Psycopg AsyncConnection rejects ProactorEventLoop. Match the existing
    PostgresSessionStore's asyncio.to_thread bridge; all storage semantics,
    schemas, serialization and versioning remain owned by PostgresSaver.
    """

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        iterator = self.list(config, filter=filter, before=before, limit=limit)
        try:
            while (item := await asyncio.to_thread(next, iterator, None)) is not None:
                yield item
        finally:
            await asyncio.to_thread(iterator.close)

    async def adelete_thread(self, thread_id):
        await asyncio.to_thread(self.delete_thread, thread_id)
