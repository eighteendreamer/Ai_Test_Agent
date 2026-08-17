from __future__ import annotations

# The script adds Agent_Server to sys.path before importing the application package.
# This mirrors the existing migration script entrypoint.
# ruff: noqa: E402

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.projects.legacy_smoke_import_preflight import (
    LegacySmokeImportPreflightService,
)
from src.application.projects.legacy_smoke_import_store import PostgresLegacySmokeImportStore
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.application.test_cases.case_store import PostgresTestCaseStore
from src.application.test_runs.run_store import PostgresTestRunStore
from src.application.test_suites.suite_store import PostgresTestSuiteStore
from src.core.config import get_settings
from src.modes.smoke_testing_mode.catalog_store import SmokeCatalogStore


def load_scope_map(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Scope mapping JSON must be an object of project_scope to project_id")
    return {str(scope): str(project_id) for scope, project_id in payload.items()}


async def preflight(scope_map_path: Path, *, page_size: int, apply: bool = False) -> dict:
    settings = get_settings()
    projects = ProjectService(store=PostgresProjectStore(settings))
    catalog = SmokeCatalogStore(settings)
    runs = PostgresTestRunStore(settings)
    writer = PostgresLegacySmokeImportStore(settings) if apply else None
    service = LegacySmokeImportPreflightService(
        project_service=projects,
        catalog=catalog,
        projection_index=runs,
        writer=writer,
    )
    scope_to_project_id = load_scope_map(scope_map_path)
    if apply:
        # Explicit apply may initialize the canonical schemas and import ledger.
        await catalog.initialize()
        await PostgresTestCaseStore(settings).initialize()
        await PostgresTestSuiteStore(settings).initialize()
        await runs.initialize()
        report = await service.apply(
            scope_to_project_id=scope_to_project_id,
            page_size=page_size,
        )
    else:
        report = await service.preflight(
            scope_to_project_id=scope_to_project_id,
            page_size=page_size,
        )
    return report.model_dump(mode="json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only, dry-run preflight for legacy Smoke catalog import."
    )
    parser.add_argument(
        "--scope-project-map",
        required=True,
        type=Path,
        help="JSON object mapping an explicitly approved legacy project_scope to an existing project UUID.",
    )
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="物化预检通过的历史快照；默认不写入任何 canonical 表。",
    )
    args = parser.parse_args()
    result = asyncio.run(
        preflight(
            args.scope_project_map.resolve(),
            page_size=args.page_size,
            apply=args.apply,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
