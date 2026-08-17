from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.application.projects.legacy_smoke_import_preflight import (
    LegacySmokeImportPreflightService,
)
from src.application.projects.legacy_smoke_import_store import (
    InMemoryLegacySmokeImportStore,
)
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.schemas.project import ProjectCreateRequest
from scripts.preflight_smoke_catalog_import import load_scope_map


class _Catalog:
    def __init__(self, records: list[dict]) -> None:
        self._records = records

    async def list_legacy_project_scopes(self) -> list[str]:
        return sorted({item["project_scope"] for item in self._records})

    async def list_legacy_runs(
        self,
        *,
        project_scopes: list[str],
        cursor_started_at: datetime | None,
        cursor_run_id: str | None,
        limit: int,
    ) -> tuple[list[dict], bool]:
        records = sorted(
            (item for item in self._records if item["project_scope"] in project_scopes),
            key=lambda item: (item["started_at"], item["run_id"]),
            reverse=True,
        )
        if cursor_started_at is not None and cursor_run_id is not None:
            records = [
                item
                for item in records
                if (item["started_at"], item["run_id"]) < (cursor_started_at, cursor_run_id)
            ]
        return records[:limit], len(records) > limit


class _ProjectionIndex:
    def __init__(self, projected: set[str]) -> None:
        self._projected = projected

    async def find_projected_legacy_smoke_run_ids(self, run_ids: list[str]) -> set[str]:
        return self._projected.intersection(run_ids)


def _record(
    run_id: str,
    started_at: datetime,
    *,
    scope: str = "orders-v1",
    metadata: dict | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "plan_id": "legacy-plan",
        "plan_version": 1,
        "project_scope": scope,
        "status": "partial",
        "verdict": "partial",
        "total_cases": 2,
        "passed_cases": 1,
        "failed_cases": 0,
        "blocked_cases": 1,
        "started_at": started_at,
        "completed_at": started_at + timedelta(seconds=3),
        "metadata": metadata
        if metadata is not None
        else {
            "run_id": run_id,
            "plan_id": "legacy-plan",
            "plan_version": 1,
            "project_scope": scope,
            "status": "partial",
            "verdict": "partial",
            "case_results": [
                {"case_id": "case-1", "title": "核心 API", "case_type": "api", "status": "passed", "summary": "ok"},
                {"case_id": "case-2", "title": "后台 UI", "case_type": "ui", "status": "not_run", "summary": "skip"},
            ],
        },
    }


async def _project_service() -> tuple[ProjectService, str]:
    projects = ProjectService(store=InMemoryProjectStore())
    await projects.initialize()
    project = await projects.create(ProjectCreateRequest(project_key="orders", name="Orders"))
    return projects, project.id


def test_preflight_classifies_importable_unmapped_projected_and_invalid_records():
    async def scenario():
        projects, project_id = await _project_service()
        now = datetime(2026, 8, 18, tzinfo=timezone.utc)
        invalid = _record("run-invalid", now - timedelta(days=3), metadata={"run_id": "run-invalid"})
        records = [
            _record("run-importable", now),
            _record("run-projected", now - timedelta(days=1)),
            _record("run-unmapped", now - timedelta(days=2), scope="payments-v1"),
            invalid,
        ]
        service = LegacySmokeImportPreflightService(
            project_service=projects,
            catalog=_Catalog(records),
            projection_index=_ProjectionIndex({"run-projected"}),
        )
        return await service.preflight(
            scope_to_project_id={"orders-v1": project_id},
            page_size=2,
        )

    report = asyncio.run(scenario())

    assert report.dry_run is True
    assert report.read_count == 4
    assert report.importable_count == 1
    assert report.unmapped_count == 1
    assert report.already_projected_count == 1
    assert report.invalid_count == 1
    assert report.unmapped_scopes == ["payments-v1"]
    by_id = {entry.legacy_run_id: entry for entry in report.entries}
    assert by_id["run-importable"].mapped_case_statuses == {"passed": 1, "skipped": 1}
    assert by_id["run-projected"].reason == "canonical_result_already_references_legacy_run"
    assert by_id["run-unmapped"].reason == "project_scope_not_in_explicit_mapping"
    assert by_id["run-invalid"].reason == "metadata_is_not_a_valid_smoke_run_snapshot"


def test_preflight_rejects_unknown_or_non_uuid_scope_mapping():
    async def scenario():
        projects, _ = await _project_service()
        service = LegacySmokeImportPreflightService(
            project_service=projects,
            catalog=_Catalog([]),
            projection_index=_ProjectionIndex(set()),
        )
        with pytest.raises(ValueError, match="must be a UUID"):
            await service.preflight(scope_to_project_id={"orders-v1": "not-a-uuid"})
        with pytest.raises(KeyError, match="Project not found"):
            await service.preflight(scope_to_project_id={"orders-v1": "11111111-1111-1111-1111-111111111111"})

    asyncio.run(scenario())


def test_scope_map_loader_requires_json_object(tmp_path):
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"orders-v1": "project-id"}), encoding="utf-8")
    invalid = tmp_path / "invalid.json"
    invalid.write_text("[]", encoding="utf-8")

    assert load_scope_map(valid) == {"orders-v1": "project-id"}
    with pytest.raises(ValueError, match="must be an object"):
        load_scope_map(invalid)


def test_apply_materializes_immutable_bundle_and_is_idempotent():
    async def scenario():
        projects, project_id = await _project_service()
        now = datetime(2026, 8, 18, tzinfo=timezone.utc)
        records = [_record("run-importable", now)]
        writer = InMemoryLegacySmokeImportStore()
        service = LegacySmokeImportPreflightService(
            project_service=projects,
            catalog=_Catalog(records),
            projection_index=_ProjectionIndex(set()),
            writer=writer,
        )
        first = await service.apply(
            scope_to_project_id={"orders-v1": project_id},
            page_size=1,
        )
        second = await service.apply(
            scope_to_project_id={"orders-v1": project_id},
            page_size=1,
        )
        return first, second, writer

    first, second, writer = asyncio.run(scenario())

    assert first.imported_count == 1
    assert first.already_imported_count == 0
    assert first.failed_count == 0
    assert second.imported_count == 0
    assert second.already_imported_count == 1
    bundle = writer.bundles["run-importable"]
    assert bundle.run.origin == "legacy_smoke_import"
    assert bundle.run.status == "completed"
    assert bundle.suite.status == "archived"
    assert all(case.lifecycle_status == "archived" for case, _ in bundle.cases)
    assert all(item.lease_token is None for item in bundle.run_items)
    assert all(result.actual["read_only"] is True for result in bundle.results)


def test_apply_refuses_unmapped_history_before_writing():
    async def scenario():
        projects, project_id = await _project_service()
        records = [_record("run-importable", datetime(2026, 8, 18, tzinfo=timezone.utc))]
        writer = InMemoryLegacySmokeImportStore()
        service = LegacySmokeImportPreflightService(
            project_service=projects,
            catalog=_Catalog([*records, _record("run-unmapped", datetime(2026, 8, 17, tzinfo=timezone.utc), scope="other")]),
            projection_index=_ProjectionIndex(set()),
            writer=writer,
        )
        with pytest.raises(ValueError, match="zero unmapped and invalid"):
            await service.apply(
                scope_to_project_id={"orders-v1": project_id},
                page_size=10,
            )
        return writer

    writer = asyncio.run(scenario())
    assert writer.bundles == {}
