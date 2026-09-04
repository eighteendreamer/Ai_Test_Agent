from __future__ import annotations

import argparse
import asyncio
import json

from src.application.context.embedding_runtime_service import EmbeddingRuntimeService
from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.models.oauth_token_service import OAuthTokenService
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_vector_memory_store import PostgresVectorMemoryStore


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    model_store = MySQLModelConfigStore(settings)
    model_store.initialize()
    oauth_service = OAuthTokenService(
        settings=settings,
        request_timeout=settings.model.llm_request_timeout_seconds,
    )
    embedding_service = EmbeddingRuntimeService(
        model_config_store=model_store,
        settings=settings,
        oauth_token_service=oauth_service,
    )
    memory_service = MemoryRuntimeService(
        memory_store=PostgresVectorMemoryStore(settings),
        top_k=settings.orchestration.memory_top_k,
        embedding_runtime_service=embedding_service,
    )
    await memory_service.initialize()
    result = await memory_service.backfill_missing_embeddings(
        limit=args.limit,
        batch_size=args.batch_size,
        execute=args.execute,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or explicitly execute pgvector embedding backfill. "
            "Without --execute this command never calls the embedding provider."
        )
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Call the configured embedding provider and update missing vectors.",
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
