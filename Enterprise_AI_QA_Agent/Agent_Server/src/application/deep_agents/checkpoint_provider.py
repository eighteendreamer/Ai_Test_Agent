from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from src.core.config import Settings
from src.runtime.execution_context import current_framework_owner


logger = logging.getLogger(__name__)


def checkpoint_serializer():
    """One pinned codec policy for pooled reads and atomic source rechecks."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from src.schemas.framework_cognitive_result import FrameworkCognitiveResult

    # Honor the official strict/permissive deployment policy while explicitly
    # supporting this fixed output type, without wildcard/pickle fallback.
    return JsonPlusSerializer().with_msgpack_allowlist([FrameworkCognitiveResult])


class DeepAgentCheckpointProvider:
    """Own the optional official PostgreSQL saver and its bounded connection pool.

    Business Session/Approval/TestRun tables remain authoritative. This schema
    stores only LangGraph execution checkpoints; credentials reuse DATABASE__*.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Any = None
        self._saver: Any = None
        self._initialized = False
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def open(self, *, owner=None, source=None) -> AsyncIterator[Any]:
        if source is not None:
            from src.schemas.runtime_binding import RuntimeCheckpointSource

            if owner is not None:
                raise ValueError("A checkpoint reader cannot also request execution ownership")
            source = RuntimeCheckpointSource.model_validate(source)
        async with self._lock:
            if self._saver is None:
                from src.application.deep_agents.threaded_postgres_saver import ThreadedPostgresSaver
                from psycopg.conninfo import make_conninfo
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool

                db = self._settings.database
                config = self._settings.deep_agents
                pool = ConnectionPool(
                    conninfo=make_conninfo(
                        host=db.postgres_host,
                        port=db.postgres_port,
                        dbname=db.postgres_database,
                        user=db.postgres_user,
                        password=db.postgres_password,
                        connect_timeout=max(1, int(db.postgres_connect_timeout_seconds)),
                    ),
                    kwargs={
                        "autocommit": True,
                        "row_factory": dict_row,
                        "prepare_threshold": 0,
                        "options": f"-c search_path={config.checkpoint_schema}",
                    },
                    min_size=0,
                    max_size=config.checkpoint_pool_size,
                    open=False,
                )
                saver = ThreadedPostgresSaver(pool, serde=checkpoint_serializer())

                def initialize():
                    pool.open()

                try:
                    await asyncio.to_thread(initialize)
                except BaseException:
                    await asyncio.to_thread(pool.close)
                    logger.exception("deep_agent_checkpoint_initialization_failed")
                    raise
                self._pool = pool
                self._saver = saver
            if source is None and not self._initialized:
                from psycopg import sql

                def setup():
                    with self._pool.connection() as connection:
                        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                            sql.Identifier(self._settings.deep_agents.checkpoint_schema)))
                    self._saver.setup()

                try:
                    await asyncio.to_thread(setup)
                except BaseException:
                    await asyncio.to_thread(self._pool.close)
                    self._pool = self._saver = None
                    logger.exception("deep_agent_checkpoint_initialization_failed")
                    raise
                self._initialized = True
                logger.info(
                    "deep_agent_checkpoint_ready",
                    extra={"schema": self._settings.deep_agents.checkpoint_schema,
                        "delta_replay_order": "task_path_task_id_idx",
                        "delta_history_reader": "official_base_ancestor_walk"},
                )
        if source is not None:
            from src.application.deep_agents.fenced_postgres_saver import BoundPostgresReader

            logger.info("framework_checkpoint_readonly_opened", extra={"binding_id": source.binding.binding_id,
                "task_id": source.task_id, "owner_epoch": source.owner_epoch})
            yield BoundPostgresReader(self._pool, source=source, serde=self._saver.serde)
            return
        execution_owner = owner if owner is not None else current_framework_owner()
        if execution_owner is None:
            yield self._saver
        else:
            from src.application.deep_agents.fenced_postgres_saver import FencedPostgresSaver
            from src.runtime.execution_context import current_graph_event_sink
            from src.runtime.session_event_sink import SessionEventSink

            sink = current_graph_event_sink()
            prefix = (sink.checkpoint_event_prefix if isinstance(sink, SessionEventSink)
                and sink.runtime_owner == execution_owner else None)
            yield FencedPostgresSaver(self._pool, settings=self._settings,
                owner=execution_owner, serde=self._saver.serde, event_prefix=prefix)

    async def close(self) -> None:
        if self._pool is not None:
            await asyncio.to_thread(self._pool.close)
            self._pool = None
            self._saver = None
            self._initialized = False
            logger.info("deep_agent_checkpoint_closed")
