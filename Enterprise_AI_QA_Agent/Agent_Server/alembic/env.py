import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool, text

from alembic import context

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import get_settings
from src.infrastructure.sqlalchemy_runtime import postgres_url

config = context.config

if config.config_file_name is not None:
    # Alembic commands may share a process with application services. Preserve
    # their existing loggers instead of silently disabling failure visibility.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Stores use explicit SQL, and OrmBase has no mapped tables. An empty metadata
# must not drive autogenerate against a populated database (it would propose drops).
target_metadata = None

settings = config.attributes.get("settings") or get_settings()
config.attributes["settings"] = settings


def run_migrations_offline() -> None:
    context.configure(
        url=postgres_url(settings),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_on_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        # Transaction-scoped lock serializes version checks and DDL, including
        # concurrent first creation of alembic_version within this schema.
        connection.execute(text(
            "SELECT pg_advisory_xact_lock(hashtext("
            ":namespace || current_database() || '/' || current_schema()))"
        ), {"namespace": "enterprise_qa_alembic/"})
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        # Official Alembic connection-sharing contract; the caller owns disposal.
        _run_on_connection(connection)
        return
    engine = create_engine(
        postgres_url(settings), poolclass=pool.NullPool,
        connect_args={
            "connect_timeout": settings.database.postgres_connect_timeout_seconds,
            "options": "-c timezone=UTC",
        },
    )
    try:
        with engine.connect() as connection:
            _run_on_connection(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
