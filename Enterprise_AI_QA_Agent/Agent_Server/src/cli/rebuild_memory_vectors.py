"""Preview/rebuild one Redis memory replica version without changing PostgreSQL."""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

from src.application.context.memory_vector_rebuild_service import MemoryVectorRebuildService
from src.core.config import get_settings
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore
from src.infrastructure.redis_vector_store import RedisVectorStore


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    redis = RedisVectorStore(
        settings.database.redis_url,
        socket_timeout_seconds=settings.orchestration.redis_vector_socket_timeout_seconds,
    )
    service = MemoryVectorRebuildService(
        PostgresVectorMemoryStore(settings), redis,
        batch_size=settings.orchestration.redis_vector_rebuild_batch_size,
        validation_timeout_seconds=(
            settings.orchestration.redis_vector_validation_timeout_seconds
        ),
        validation_poll_interval_seconds=(
            settings.orchestration.redis_vector_validation_poll_interval_seconds
        ),
    )
    try:
        # Do not initialize the PG store here: recovery is read-only in the fact DB.
        result = await service.rebuild(
            args.embedding_version,
            execute=args.execute,
            activate=args.activate,
        )
        print(json.dumps(asdict(result), ensure_ascii=False))
    finally:
        await redis.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=(
        "Preview Redis memory replica recovery from PostgreSQL for one embedding version. "
        "--execute writes Redis only; no model calls, SQL schema changes or database writes. "
        "A failed pass can safely restart. --activate switches the online pointer only after "
        "document-count and recall validation pass."
    ))
    parser.add_argument("--embedding-version", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Atomically activate the validated index; requires --execute.",
    )
    args = parser.parse_args()
    if args.activate and not args.execute:
        parser.error("--activate requires --execute")
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        # Driver exception messages can contain DB URLs or vector payloads.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
