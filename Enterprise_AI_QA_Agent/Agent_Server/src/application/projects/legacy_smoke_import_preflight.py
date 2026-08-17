from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from src.application.projects.project_service import ProjectService
from src.application.projects.legacy_smoke_import_store import (
    LegacySmokeImportStore,
    build_legacy_smoke_import_bundle,
)
from src.modes.smoke_testing_mode.contracts import SmokeRunResult
from src.schemas.legacy_smoke_import import (
    LegacySmokeImportPreflightEntry,
    LegacySmokeImportApplyReport,
    LegacySmokeImportPreflightReport,
)


logger = logging.getLogger(__name__)


class LegacySmokeImportCatalog(Protocol):
    async def list_legacy_project_scopes(self) -> list[str]: ...
    async def list_legacy_runs(
        self,
        *,
        project_scopes: list[str],
        cursor_started_at: datetime | None,
        cursor_run_id: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]: ...


class LegacySmokeProjectionIndex(Protocol):
    async def find_projected_legacy_smoke_run_ids(self, run_ids: list[str]) -> set[str]: ...


class LegacySmokeImportPreflightService:
    """Read-only qualification before any legacy Smoke data is materialized.

    A successful entry only proves the source snapshot can be imported by a
    future audited importer. This service never writes canonical test assets.
    """

    def __init__(
        self,
        *,
        project_service: ProjectService,
        catalog: LegacySmokeImportCatalog,
        projection_index: LegacySmokeProjectionIndex,
        writer: LegacySmokeImportStore | None = None,
    ) -> None:
        self._projects = project_service
        self._catalog = catalog
        self._projection_index = projection_index
        self._writer = writer

    async def preflight(
        self,
        *,
        scope_to_project_id: dict[str, str],
        page_size: int = 200,
    ) -> LegacySmokeImportPreflightReport:
        mapping = await self._validated_mapping(scope_to_project_id)
        all_scopes = await self._catalog.list_legacy_project_scopes()
        report = LegacySmokeImportPreflightReport(
            unmapped_scopes=sorted(scope for scope in all_scopes if scope not in mapping),
        )
        if not all_scopes:
            return report

        cursor_started_at: datetime | None = None
        cursor_run_id: str | None = None
        while True:
            records, has_more = await self._catalog.list_legacy_runs(
                project_scopes=all_scopes,
                cursor_started_at=cursor_started_at,
                cursor_run_id=cursor_run_id,
                limit=max(1, min(page_size, 1000)),
            )
            if not records:
                break
            projected = await self._projection_index.find_projected_legacy_smoke_run_ids(
                [str(record.get("run_id") or "") for record in records]
            )
            for record in records:
                entry = self._evaluate_record(record, mapping, projected)
                report.entries.append(entry)
                report.read_count += 1
                if entry.decision == "importable":
                    report.importable_count += 1
                elif entry.decision == "unmapped":
                    report.unmapped_count += 1
                elif entry.decision == "already_projected":
                    report.already_projected_count += 1
                else:
                    report.invalid_count += 1
            if not has_more:
                break
            last = records[-1]
            cursor_started_at = last.get("started_at")
            cursor_run_id = str(last.get("run_id") or "")
            if not isinstance(cursor_started_at, datetime) or not cursor_run_id:
                raise ValueError("Legacy Smoke catalog returned an invalid pagination record")
        logger.info(
            "legacy_smoke_import_preflight_completed",
            extra={
                "read_count": report.read_count,
                "importable_count": report.importable_count,
                "unmapped_count": report.unmapped_count,
                "already_projected_count": report.already_projected_count,
                "invalid_count": report.invalid_count,
            },
        )
        return report

    async def apply(
        self,
        *,
        scope_to_project_id: dict[str, str],
        page_size: int = 200,
    ) -> LegacySmokeImportApplyReport:
        if self._writer is None:
            raise RuntimeError("Legacy Smoke import writer is not configured")
        preflight = await self.preflight(
            scope_to_project_id=scope_to_project_id,
            page_size=page_size,
        )
        if preflight.unmapped_count or preflight.invalid_count:
            raise ValueError(
                "Legacy Smoke apply requires zero unmapped and invalid records: "
                f"unmapped={preflight.unmapped_count}, invalid={preflight.invalid_count}"
            )
        await self._writer.initialize()
        processable_ids = {
            entry.legacy_run_id
            for entry in preflight.entries
            if entry.decision in {"importable", "already_projected"}
        }
        all_scopes = await self._catalog.list_legacy_project_scopes()
        mapping = {str(scope).strip(): str(project_id).strip() for scope, project_id in scope_to_project_id.items()}
        records = await self._collect_records(all_scopes, page_size=page_size)
        report = LegacySmokeImportApplyReport(preflight=preflight)
        for record in records:
            legacy_run_id = str(record.get("run_id") or "")
            if legacy_run_id not in processable_ids:
                continue
            try:
                snapshot = SmokeRunResult.model_validate(_as_mapping(record.get("metadata")))
                bundle = build_legacy_smoke_import_bundle(
                    project_id=mapping[str(record.get("project_scope") or "").strip()],
                    source_record=record,
                    snapshot=snapshot,
                )
                action, canonical_run_id = await self._writer.import_bundle(bundle)
                report.canonical_run_ids.append(canonical_run_id)
                if action == "imported":
                    report.imported_count += 1
                else:
                    report.already_imported_count += 1
            except Exception as exc:
                report.failed_count += 1
                report.errors.append(f"{legacy_run_id}: {exc}")
                logger.exception(
                    "legacy_smoke_import_failed",
                    extra={"legacy_run_id": legacy_run_id},
                )
        return report

    async def _collect_records(
        self,
        all_scopes: list[str],
        *,
        page_size: int,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        cursor_started_at: datetime | None = None
        cursor_run_id: str | None = None
        while all_scopes:
            page, has_more = await self._catalog.list_legacy_runs(
                project_scopes=all_scopes,
                cursor_started_at=cursor_started_at,
                cursor_run_id=cursor_run_id,
                limit=max(1, min(page_size, 1000)),
            )
            if not page:
                break
            records.extend(page)
            if not has_more:
                break
            last = page[-1]
            cursor_started_at = last.get("started_at")
            cursor_run_id = str(last.get("run_id") or "")
            if not isinstance(cursor_started_at, datetime) or not cursor_run_id:
                raise ValueError("Legacy Smoke catalog returned an invalid pagination record")
        return records

    async def _validated_mapping(self, raw_mapping: dict[str, str]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for raw_scope, raw_project_id in raw_mapping.items():
            scope = str(raw_scope or "").strip()
            project_id = str(raw_project_id or "").strip()
            if not scope or not project_id:
                raise ValueError("Scope mapping requires non-empty project_scope and project_id")
            try:
                UUID(project_id)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Scope mapping project_id must be a UUID: {scope}") from exc
            await self._projects.get(project_id)
            mapping[scope] = project_id
        return mapping

    @staticmethod
    def _evaluate_record(
        record: dict[str, Any],
        mapping: dict[str, str],
        projected: set[str],
    ) -> LegacySmokeImportPreflightEntry:
        run_id = str(record.get("run_id") or "").strip()
        plan_id = str(record.get("plan_id") or "").strip()
        scope = str(record.get("project_scope") or "").strip()
        project_id = mapping.get(scope)
        if not run_id or not plan_id or not scope:
            return LegacySmokeImportPreflightEntry(
                legacy_run_id=run_id,
                legacy_plan_id=plan_id,
                project_scope=scope,
                decision="invalid",
                reason="legacy_run_identity_incomplete",
            )
        if project_id is None:
            return LegacySmokeImportPreflightEntry(
                legacy_run_id=run_id,
                legacy_plan_id=plan_id,
                project_scope=scope,
                decision="unmapped",
                reason="project_scope_not_in_explicit_mapping",
            )
        if run_id in projected:
            return LegacySmokeImportPreflightEntry(
                legacy_run_id=run_id,
                legacy_plan_id=plan_id,
                project_scope=scope,
                project_id=project_id,
                decision="already_projected",
                reason="canonical_result_already_references_legacy_run",
            )
        try:
            result = SmokeRunResult.model_validate(_as_mapping(record.get("metadata")))
        except Exception:
            return LegacySmokeImportPreflightEntry(
                legacy_run_id=run_id,
                legacy_plan_id=plan_id,
                project_scope=scope,
                project_id=project_id,
                decision="invalid",
                reason="metadata_is_not_a_valid_smoke_run_snapshot",
            )
        if result.run_id != run_id or result.plan_id != plan_id:
            return LegacySmokeImportPreflightEntry(
                legacy_run_id=run_id,
                legacy_plan_id=plan_id,
                project_scope=scope,
                project_id=project_id,
                decision="invalid",
                reason="metadata_identity_does_not_match_catalog_row",
            )
        if result.project_scope and result.project_scope != scope:
            return LegacySmokeImportPreflightEntry(
                legacy_run_id=run_id,
                legacy_plan_id=plan_id,
                project_scope=scope,
                project_id=project_id,
                decision="invalid",
                reason="metadata_project_scope_does_not_match_catalog_row",
            )
        if not result.case_results:
            return LegacySmokeImportPreflightEntry(
                legacy_run_id=run_id,
                legacy_plan_id=plan_id,
                project_scope=scope,
                project_id=project_id,
                decision="invalid",
                reason="metadata_has_no_case_results",
            )
        mapped_statuses: dict[str, int] = {}
        for case in result.case_results:
            mapped = _map_case_status(case.status)
            mapped_statuses[mapped] = mapped_statuses.get(mapped, 0) + 1
        return LegacySmokeImportPreflightEntry(
            legacy_run_id=run_id,
            legacy_plan_id=plan_id,
            project_scope=scope,
            project_id=project_id,
            decision="importable",
            reason="read_only_preflight_passed",
            case_count=len(result.case_results),
            mapped_case_statuses=mapped_statuses,
        )


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _map_case_status(status: str) -> str:
    if status in {"passed", "failed", "blocked"}:
        return status
    if status == "not_run":
        return "skipped"
    return "blocked"
