from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.documents.api_doc_store import PostgresApiDocStore
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.core.config import get_settings


async def migrate(catalog_path: Path) -> dict[str, int]:
    settings = get_settings()
    projects = ProjectService(store=PostgresProjectStore(settings))
    await projects.initialize()
    store = PostgresApiDocStore(settings)
    await store.initialize()
    return await store.migrate_legacy_catalog(catalog_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Explicitly migrate the legacy API document catalog into PostgreSQL."
    )
    parser.add_argument(
        "catalog",
        nargs="?",
        type=Path,
        default=PROJECT_ROOT / "data" / "api_docs" / "catalog.json",
    )
    args = parser.parse_args()
    result = asyncio.run(migrate(args.catalog.resolve()))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
