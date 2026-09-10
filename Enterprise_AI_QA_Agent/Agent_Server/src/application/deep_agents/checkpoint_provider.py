from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from src.core.config import Settings


logger = logging.getLogger(__name__)


class DeepAgentCheckpointProvider:
    """Own the optional official PostgreSQL saver and its bounded connection pool.

    Business Session/Approval/TestRun tables remain authoritative. This schema
    stores only LangGraph execution checkpoints; credentials reuse DATABASE__*.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Any = None
        self._saver: Any = None
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def open(self) -> AsyncIterator[Any]:
        async with self._lock:
            if self._saver is None:
                from src.application.deep_agents.threaded_postgres_saver import ThreadedPostgresSaver
                from psycopg import sql
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
                saver = ThreadedPostgresSaver(pool)

                def initialize():
                    pool.open()
                    with pool.connection() as connection:
                        connection.execute(
                            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                                sql.Identifier(config.checkpoint_schema)
                            )
                        )
                    saver.setup()

                try:
                    await asyncio.to_thread(initialize)
                except BaseException:
                    await asyncio.to_thread(pool.close)
                    logger.exception("deep_agent_checkpoint_initialization_failed")
                    raise
                self._pool = pool
                self._saver = saver
                logger.info(
                    "deep_agent_checkpoint_ready",
                    extra={"schema": config.checkpoint_schema},
                )
        yield self._saver

    async def close(self) -> None:
        if self._pool is not None:
            await asyncio.to_thread(self._pool.close)
            self._pool = None
            self._saver = None
            logger.info("deep_agent_checkpoint_closed")
