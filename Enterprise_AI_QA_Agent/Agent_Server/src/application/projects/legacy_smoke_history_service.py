from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime
from typing import Any, Protocol

from src.application.projects.project_service import ProjectService
from src.schemas.legacy_smoke_history import (
    LegacySmokeCaseSummary,
    LegacySmokeRunPage,
    LegacySmokeRunSummary,
    LegacySmokeScopeBinding,
)


logger = logging.getLogger(__name__)


class LegacySmokeCatalog(Protocol):
    async def initialize(self) -> None: ...
    async def bind_project_scope(
        self,
        *,
        project_id: str,
        project_scope: str,
    ) -> dict[str, Any]: ...
    async def unbind_project_scope(self, *, project_id: str, project_scope: str) -> bool: ...
    async def list_project_scope_bindings(self, project_id: str) -> list[dict[str, Any]]: ...
    async def list_legacy_runs(
        self,
        *,
        project_scopes: list[str],
        cursor_started_at: datetime | None,
        cursor_run_id: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], bool]: ...


class LegacySmokeHistoryService:
    """Read-only projection for pre-canonical Smoke catalog history.

    It deliberately does not manufacture TestRun/TestCase/Attempt entities from
    legacy TEXT identifiers. A scope must be explicitly bound to a project first.
    """

    def __init__(self, *, project_service: ProjectService, catalog: LegacySmokeCatalog) -> None:
        self._projects = project_service
        self._catalog = catalog

    async def initialize(self) -> None:
        await self._catalog.initialize()

    async def bind_scope(self, project_id: str, project_scope: str) -> LegacySmokeScopeBinding:
        await self._projects.get(project_id)
        record = await self._catalog.bind_project_scope(
            project_id=project_id,
            project_scope=_normalize_scope(project_scope),
        )
        binding = LegacySmokeScopeBinding.model_validate(record)
        logger.info(
            "legacy_smoke_scope_bound",
            extra={"project_id": project_id, "project_scope": binding.project_scope},
        )
        return binding

    async def unbind_scope(self, project_id: str, project_scope: str) -> None:
        await self._projects.get(project_id)
        removed = await self._catalog.unbind_project_scope(
            project_id=project_id,
            project_scope=_normalize_scope(project_scope),
        )
        if not removed:
            raise KeyError(f"Legacy Smoke scope binding not found: {project_scope}")
        logger.info(
            "legacy_smoke_scope_unbound",
            extra={"project_id": project_id, "project_scope": project_scope},
        )

    async def list_runs(
        self,
        project_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> LegacySmokeRunPage:
        await self._projects.get(project_id)
        bindings = [
            LegacySmokeScopeBinding.model_validate(item)
            for item in await self._catalog.list_project_scope_bindings(project_id)
        ]
        if not bindings:
            logger.info(
                "legacy_smoke_history_unbound_project_requested",
                extra={"project_id": project_id},
            )
            return LegacySmokeRunPage(project_id=project_id, bindings=[], limit=limit)
        cursor_started_at, cursor_run_id = _decode_cursor(cursor)
        records, has_more = await self._catalog.list_legacy_runs(
            project_scopes=[item.project_scope for item in bindings],
            cursor_started_at=cursor_started_at,
            cursor_run_id=cursor_run_id,
            limit=limit,
        )
        items = [_to_run_summary(record) for record in records]
        next_cursor = (
            _encode_cursor(items[-1].started_at, items[-1].legacy_run_id)
            if has_more and items
            else None
        )
        logger.info(
            "legacy_smoke_history_listed",
            extra={
                "project_id": project_id,
                "binding_count": len(bindings),
                "item_count": len(items),
                "has_more": has_more,
            },
        )
        return LegacySmokeRunPage(
            project_id=project_id,
            bindings=bindings,
            items=items,
            limit=limit,
            next_cursor=next_cursor,
            has_more=has_more,
        )


def _to_run_summary(record: dict[str, Any]) -> LegacySmokeRunSummary:
    metadata = _as_mapping(record.get("metadata"))
    case_results = metadata.get("case_results")
    if not isinstance(case_results, list):
        case_results = []
    return LegacySmokeRunSummary(
        legacy_run_id=str(record.get("run_id") or ""),
        legacy_plan_id=str(record.get("plan_id") or ""),
        legacy_plan_version=_non_negative_int(record.get("plan_version"), minimum=1),
        project_scope=str(record.get("project_scope") or ""),
        legacy_status=str(record.get("status") or metadata.get("status") or ""),
        legacy_verdict=str(record.get("verdict") or metadata.get("verdict") or ""),
        total_cases=_non_negative_int(record.get("total_cases")),
        passed_cases=_non_negative_int(record.get("passed_cases")),
        failed_cases=_non_negative_int(record.get("failed_cases")),
        blocked_cases=_non_negative_int(record.get("blocked_cases")),
        started_at=record["started_at"],
        completed_at=record.get("completed_at"),
        summary=str(metadata.get("summary") or ""),
        case_results=[_to_case_summary(item) for item in case_results if isinstance(item, dict)],
    )


def _to_case_summary(record: dict[str, Any]) -> LegacySmokeCaseSummary:
    legacy_status = str(record.get("status") or "").strip().lower()
    evidence = record.get("evidence")
    return LegacySmokeCaseSummary(
        legacy_case_id=str(record.get("case_id") or ""),
        title=str(record.get("title") or ""),
        case_type=str(record.get("case_type") or ""),
        legacy_status=legacy_status,
        mapped_status=_map_status(legacy_status),
        summary=str(record.get("summary") or ""),
        assertion_count=_non_negative_int(record.get("assertion_count")),
        passed_count=_non_negative_int(record.get("passed_count")),
        failed_count=_non_negative_int(record.get("failed_count")),
        duration_ms=_non_negative_int(record.get("duration_ms")),
        evidence_count=len(evidence) if isinstance(evidence, list) else 0,
    )


def _map_status(value: str) -> str:
    if value in {"passed", "failed", "blocked"}:
        return value
    if value == "not_run":
        return "skipped"
    return "blocked"


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _non_negative_int(value: Any, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value or 0))
    except (TypeError, ValueError):
        return minimum


def _normalize_scope(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("project_scope cannot be empty")
    return normalized


def _encode_cursor(started_at: datetime, run_id: str) -> str:
    payload = json.dumps(
        {"started_at": started_at.isoformat(), "run_id": run_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if not cursor:
        return None, None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
        )
        started_at = datetime.fromisoformat(str(payload["started_at"]))
        run_id = str(payload["run_id"]).strip()
        if started_at.tzinfo is None or not run_id:
            raise ValueError("cursor fields are incomplete")
        return started_at, run_id
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ) as exc:
        raise ValueError("Invalid legacy Smoke pagination cursor") from exc
