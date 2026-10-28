from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from threading import Lock
from time import monotonic
from typing import Any, Literal, Self

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ResourceClosedError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.core.config import Settings

logger = logging.getLogger(__name__)


DatabaseKind = Literal["mysql", "postgres"]


class OrmBase(DeclarativeBase):
    """Base class for future SQLAlchemy ORM models."""


_ENGINE_LOCK = Lock()
_ENGINES: dict[tuple[Any, ...], Engine] = {}
_SESSION_FACTORY_LOCK = Lock()
_SESSION_FACTORIES: dict[tuple[Any, ...], sessionmaker[Session]] = {}
_ENGINE_OWNERS: dict[tuple[Any, ...], int] = {}


def mysql_url(settings: Settings) -> URL:
    return URL.create(
        "mysql+pymysql",
        username=settings.database.mysql_user,
        password=settings.database.mysql_password,
        host=settings.database.mysql_host,
        port=settings.database.mysql_port,
        database=settings.database.mysql_database,
    )


def postgres_url(settings: Settings) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.database.postgres_user,
        password=settings.database.postgres_password,
        host=settings.database.postgres_host,
        port=settings.database.postgres_port,
        database=settings.database.postgres_database,
    )


def mysql_database_url(settings: Settings) -> str:
    return mysql_url(settings).render_as_string(hide_password=True)


def postgres_database_url(settings: Settings) -> str:
    return postgres_url(settings).render_as_string(hide_password=True)


def mysql_engine(settings: Settings) -> Engine:
    key = _mysql_engine_key(settings)
    return _get_or_create_engine(
        key,
        lambda: create_engine(
            mysql_url(settings),
            pool_pre_ping=True,
            connect_args={
                "charset": settings.database.mysql_charset,
            },
        ),
    )


def postgres_engine(settings: Settings) -> Engine:
    key = _postgres_engine_key(settings)
    return _get_or_create_engine(
        key,
        lambda: create_engine(
            postgres_url(settings),
            pool_pre_ping=True,
            pool_size=settings.database.postgres_pool_size,
            max_overflow=0,
            connect_args={
                "connect_timeout": settings.database.postgres_connect_timeout_seconds,
                # Legacy stores write naive UTC; TIMESTAMPTZ must interpret it
                # consistently rather than inherit the server/role timezone.
                "options": "-c timezone=UTC",
            },
        ),
    )


def mysql_sessionmaker(settings: Settings) -> sessionmaker[Session]:
    return _get_or_create_session_factory(_mysql_engine_key(settings), mysql_engine(settings))


def postgres_sessionmaker(settings: Settings) -> sessionmaker[Session]:
    return _get_or_create_session_factory(_postgres_engine_key(settings), postgres_engine(settings))


@contextmanager
def mysql_session(settings: Settings) -> Iterator[Session]:
    with _session_scope(mysql_sessionmaker(settings)) as session:
        yield session


@contextmanager
def postgres_session(settings: Settings) -> Iterator[Session]:
    with _session_scope(postgres_sessionmaker(settings)) as session:
        yield session


@contextmanager
def mysql_raw_connection(settings: Settings) -> Iterator[SQLAlchemyCursorConnection]:
    with _cursor_connection(mysql_engine(settings)) as conn:
        yield conn


@contextmanager
def postgres_raw_connection(settings: Settings) -> Iterator[SQLAlchemyCursorConnection]:
    with _cursor_connection(postgres_engine(settings)) as conn:
        yield conn


class SQLAlchemyCursorConnection:
    def __init__(self, connection, database: DatabaseKind) -> None:
        self._connection = connection
        self._database = database

    def cursor(self) -> SQLAlchemyCursor:
        return SQLAlchemyCursor(self._connection, self._database)

    def commit(self) -> None:
        _commit(self._connection, self._database)

    def rollback(self) -> None:
        self._connection.rollback()


class SQLAlchemyCursor:
    def __init__(self, connection, database: DatabaseKind) -> None:
        self._connection = connection
        self._database = database
        self._result = None
        self._mappings = None
        self.lastrowid: int | None = None
        self.rowcount: int = -1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def execute(self, statement: str, parameters: Any = None) -> SQLAlchemyCursor:
        self.close()
        if parameters is None:
            result = self._connection.exec_driver_sql(statement)
        else:
            result = self._connection.exec_driver_sql(statement, _normalize_execute_parameters(parameters))
        self._set_result(result)
        return self

    def executemany(self, statement: str, seq_of_parameters: Any) -> SQLAlchemyCursor:
        self.close()
        result = self._connection.exec_driver_sql(statement, seq_of_parameters)
        self._set_result(result)
        return self

    @contextmanager
    def postgres_driver_connection(self):
        """Lend the current driver in a SQLAlchemy-managed savepoint, no commit.

        SQLAlchemy 2.x Connection.connection.driver_connection and
        begin_nested keep SDK reads on the caller's existing transaction.
        The borrower must not commit/rollback/close the outer connection.
        """
        if self._database != "postgres":
            raise ValueError("PostgreSQL SDK reads require a PostgreSQL cursor")
        self.close()
        with self._connection.begin_nested():
            yield self._connection.connection.driver_connection

    def fetchone(self):
        if self._mappings is None:
            return None
        try:
            return self._mappings.fetchone()
        except Exception:
            logger.exception("sqlalchemy_cursor_fetch_failed", extra={"operation": "fetchone"})
            raise

    def fetchall(self):
        if self._mappings is None:
            return []
        try:
            return list(self._mappings.fetchall())
        except Exception:
            logger.exception("sqlalchemy_cursor_fetch_failed", extra={"operation": "fetchall"})
            raise

    def close(self) -> None:
        if self._result is not None:
            self._result.close()
            self._result = None
        self._mappings = None

    def _set_result(self, result) -> None:
        self._result = result
        self._mappings = result.mappings() if result.returns_rows else None
        self.rowcount = result.rowcount
        self.lastrowid = None
        if self._database == "mysql":
            try:
                self.lastrowid = result.lastrowid
            except ResourceClosedError:
                pass


@asynccontextmanager
async def sqlalchemy_engine_lifespan(settings: Settings) -> AsyncIterator[None]:
    """Retain this application's configured pools until its last lifespan exits."""
    keys = (_mysql_engine_key(settings), _postgres_engine_key(settings))
    # No upstream counterpart (checked: postgres_runtime.py, SQLAlchemy Engine Disposal):
    # shared process caches need application ownership in addition to Engine.dispose().
    with _ENGINE_LOCK:
        for key in keys:
            _ENGINE_OWNERS[key] = _ENGINE_OWNERS.get(key, 0) + 1
    try:
        yield
    finally:
        # Follow SessionService._settle_execution_tasks: let synchronous pool
        # shutdown finish before propagating cancellation of its async waiter.
        closing = asyncio.create_task(asyncio.to_thread(_release_engine_owners, keys))
        cancelled = False
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                cancelled = True
        closing.result()
        if cancelled:
            raise asyncio.CancelledError


def _release_engine_owners(keys: tuple[tuple[Any, ...], ...]) -> None:
    items = []
    with _ENGINE_LOCK, _SESSION_FACTORY_LOCK:
        for key in keys:
            remaining = _ENGINE_OWNERS[key] - 1
            if remaining:
                _ENGINE_OWNERS[key] = remaining
                continue
            _ENGINE_OWNERS.pop(key)
            engine = _ENGINES.pop(key, None)
            _SESSION_FACTORIES.pop(key, None)
            if engine is not None:
                items.append((key, engine))
    _dispose_engine_items(items)
    logger.info("sqlalchemy_application_pools_released", extra={"disposed_pool_count": len(items)})


def _dispose_engine_items(items: list[tuple[tuple[Any, ...], Engine]]) -> None:
    error = None
    for key, engine in items:
        try:
            engine.dispose()
            logger.info("sqlalchemy_engine_disposed", extra={"database": key[0]})
        except Exception as exc:
            logger.error("sqlalchemy_engine_disposal_failed", extra={
                "database": key[0], "error_type": type(exc).__name__,
            })
            if error is None:
                error = exc
    if error is not None:
        raise error


def dispose_sqlalchemy_engines(database: DatabaseKind | None = None) -> None:
    with _ENGINE_LOCK:
        items = list(_ENGINES.items())
        if database is None:
            _ENGINES.clear()
        else:
            _ENGINES.clear()
            _ENGINES.update({key: engine for key, engine in items if key[0] != database})

    with _SESSION_FACTORY_LOCK:
        if database is None:
            _SESSION_FACTORIES.clear()
        else:
            keep = {key: factory for key, factory in _SESSION_FACTORIES.items() if key[0] != database}
            _SESSION_FACTORIES.clear()
            _SESSION_FACTORIES.update(keep)

    _dispose_engine_items([(key, engine) for key, engine in items if database is None or key[0] == database])


def iter_engines(database: DatabaseKind | None = None) -> list[Engine]:
    """Snapshot cached engines for read-only pool observability.

    The engine cache stays owned by this module; callers such as the metrics
    collector must never add to or dispose from it.
    """
    with _ENGINE_LOCK:
        return [
            engine
            for key, engine in _ENGINES.items()
            if database is None or key[0] == database
        ]


def _get_or_create_engine(key: tuple[Any, ...], factory) -> Engine:
    with _ENGINE_LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = factory()
            _ENGINES[key] = engine
            logger.info("sqlalchemy_engine_created", extra={
                "database": key[0], "session_timezone": "UTC" if key[0] == "postgres" else None,
            })
        return engine


def _get_or_create_session_factory(
    key: tuple[Any, ...],
    engine: Engine,
) -> sessionmaker[Session]:
    with _SESSION_FACTORY_LOCK:
        factory = _SESSION_FACTORIES.get(key)
        if factory is None:
            factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
            _SESSION_FACTORIES[key] = factory
        return factory


@contextmanager
def _session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        _commit(session, _database_kind(session.get_bind()))
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def _cursor_connection(engine: Engine) -> Iterator[SQLAlchemyCursorConnection]:
    conn = engine.connect()
    database = _database_kind(engine)
    try:
        yield SQLAlchemyCursorConnection(conn, database)
        _commit(conn, database)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _normalize_execute_parameters(parameters: Any) -> Any:
    if isinstance(parameters, list):
        return tuple(parameters)
    return parameters


def _mysql_engine_key(settings: Settings) -> tuple[Any, ...]:
    return (
        "mysql",
        settings.database.mysql_host,
        settings.database.mysql_port,
        settings.database.mysql_user,
        settings.database.mysql_password,
        settings.database.mysql_database,
        settings.database.mysql_charset,
    )


def _postgres_engine_key(settings: Settings) -> tuple[Any, ...]:
    return (
        "postgres",
        settings.database.postgres_host,
        settings.database.postgres_port,
        settings.database.postgres_user,
        settings.database.postgres_password,
        settings.database.postgres_database,
        settings.database.postgres_pool_size,
        settings.database.postgres_connect_timeout_seconds,
    )


def _database_kind(bind: Engine) -> DatabaseKind:
    """Label a commit with the same two-value key this module caches engines under."""
    return "mysql" if bind.url.drivername.startswith("mysql") else "postgres"


def _commit(connection: Any, database: DatabaseKind) -> None:
    """Commit every store transaction through one observable chokepoint.

    ``in_transaction`` gates the export because ``_cursor_connection`` also commits on
    exit after a store has already committed explicitly; that second call is a no-op
    and must not be reported as a durable write.
    """
    # Lazy import: the observability package __init__ pulls in the LangSmith adapter,
    # which must not end up on the configuration import path.
    from src.application.observability.business_metrics import (
        db_commit_errors_total,
        db_commit_seconds,
        db_commits_total,
    )

    if not connection.in_transaction():
        connection.commit()
        return
    started = monotonic()
    try:
        connection.commit()
    except BaseException:
        db_commit_errors_total().labels(database=database).inc()
        db_commit_seconds().labels(database=database).observe(monotonic() - started)
        raise
    db_commits_total().labels(database=database).inc()
    db_commit_seconds().labels(database=database).observe(monotonic() - started)
