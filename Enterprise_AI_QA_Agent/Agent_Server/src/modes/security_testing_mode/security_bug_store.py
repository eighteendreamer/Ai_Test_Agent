"""Persistent registry for reproducible Security Bug records."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Protocol

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import make_json_safe
from src.modes.security_testing_mode.campaign_state import SecurityBugRecord
from src.modes.security_testing_mode.contracts import SEVERITY_ORDER


class SecurityBugStore(Protocol):
    async def initialize(self) -> None: ...
    async def upsert_observation(
        self,
        candidate: SecurityBugRecord,
    ) -> tuple[SecurityBugRecord, bool]: ...
    async def get(self, bug_id: str) -> SecurityBugRecord | None: ...
    async def list(
        self,
        *,
        target_fingerprint: str = "",
        status: str = "",
    ) -> list[SecurityBugRecord]: ...
    async def save(self, bug: SecurityBugRecord) -> SecurityBugRecord: ...


class InMemorySecurityBugStore:
    """Deterministic test/development implementation of the Bug registry."""

    def __init__(self) -> None:
        self._bugs_by_id: dict[str, SecurityBugRecord] = {}
        self._bug_id_by_fingerprint: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def upsert_observation(
        self,
        candidate: SecurityBugRecord,
    ) -> tuple[SecurityBugRecord, bool]:
        async with self._lock:
            bug_id = self._bug_id_by_fingerprint.get(candidate.fingerprint)
            if not bug_id:
                stored = candidate.model_copy(deep=True)
                self._bugs_by_id[stored.bug_id] = stored
                self._bug_id_by_fingerprint[stored.fingerprint] = stored.bug_id
                return stored.model_copy(deep=True), True
            merged = merge_security_bug_records(self._bugs_by_id[bug_id], candidate)
            self._bugs_by_id[bug_id] = merged
            return merged.model_copy(deep=True), False

    async def get(self, bug_id: str) -> SecurityBugRecord | None:
        item = self._bugs_by_id.get(bug_id)
        return item.model_copy(deep=True) if item is not None else None

    async def list(
        self,
        *,
        target_fingerprint: str = "",
        status: str = "",
    ) -> list[SecurityBugRecord]:
        items = list(self._bugs_by_id.values())
        if target_fingerprint:
            items = [item for item in items if item.target_fingerprint == target_fingerprint]
        if status:
            items = [item for item in items if item.status == status]
        return [item.model_copy(deep=True) for item in sorted(items, key=lambda item: item.bug_id)]

    async def save(self, bug: SecurityBugRecord) -> SecurityBugRecord:
        async with self._lock:
            stored = bug.model_copy(deep=True)
            self._bugs_by_id[stored.bug_id] = stored
            self._bug_id_by_fingerprint[stored.fingerprint] = stored.bug_id
            return stored.model_copy(deep=True)


class PostgresSecurityBugStore:
    """Postgres-backed registry with atomic fingerprint deduplication."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def upsert_observation(
        self,
        candidate: SecurityBugRecord,
    ) -> tuple[SecurityBugRecord, bool]:
        return await asyncio.to_thread(self._upsert_observation_sync, candidate)

    async def get(self, bug_id: str) -> SecurityBugRecord | None:
        return await asyncio.to_thread(self._get_sync, bug_id)

    async def list(
        self,
        *,
        target_fingerprint: str = "",
        status: str = "",
    ) -> list[SecurityBugRecord]:
        return await asyncio.to_thread(self._list_sync, target_fingerprint, status)

    async def save(self, bug: SecurityBugRecord) -> SecurityBugRecord:
        return await asyncio.to_thread(self._save_sync, bug)

    @property
    def _table(self) -> str:
        return self._settings.database.postgres_security_bug_table

    def _initialize_sync(self) -> None:
        table = self._table
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        bug_id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL UNIQUE,
                        target_fingerprint TEXT NOT NULL DEFAULT '',
                        affected_target TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        verification_level TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        first_seen_at TIMESTAMPTZ NOT NULL,
                        last_seen_at TIMESTAMPTZ NOT NULL,
                        occurrence_count INTEGER NOT NULL DEFAULT 1,
                        record JSONB NOT NULL
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{table}_target_status "
                    f"ON {table} (target_fingerprint, status, last_seen_at DESC)"
                )

    def _upsert_observation_sync(
        self,
        candidate: SecurityBugRecord,
    ) -> tuple[SecurityBugRecord, bool]:
        table = self._table
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {table} (
                        bug_id, fingerprint, target_fingerprint, affected_target,
                        status, verification_level, severity, first_seen_at,
                        last_seen_at, occurrence_count, record
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (fingerprint) DO NOTHING
                    RETURNING record
                    """,
                    self._record_parameters(candidate),
                )
                inserted = cur.fetchone()
                if inserted:
                    return self._record_from_value(inserted["record"]), True
                cur.execute(
                    f"SELECT record FROM {table} WHERE fingerprint = %s FOR UPDATE",
                    (candidate.fingerprint,),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(
                        f"Security Bug fingerprint disappeared during upsert: {candidate.fingerprint}"
                    )
                existing = self._record_from_value(row["record"])
                merged = merge_security_bug_records(existing, candidate)
                cur.execute(
                    f"""
                    UPDATE {table}
                    SET target_fingerprint = %s,
                        affected_target = %s,
                        status = %s,
                        verification_level = %s,
                        severity = %s,
                        first_seen_at = %s,
                        last_seen_at = %s,
                        occurrence_count = %s,
                        record = %s::jsonb
                    WHERE fingerprint = %s
                    """,
                    (
                        merged.target_fingerprint,
                        merged.affected_target,
                        merged.status,
                        merged.verification_level,
                        merged.severity,
                        merged.first_seen_at,
                        merged.last_seen_at,
                        merged.occurrence_count,
                        self._record_json(merged),
                        merged.fingerprint,
                    ),
                )
                return merged, False

    def _get_sync(self, bug_id: str) -> SecurityBugRecord | None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {self._table} WHERE bug_id = %s", (bug_id,))
                row = cur.fetchone()
        return self._record_from_value(row["record"]) if row else None

    def _list_sync(self, target_fingerprint: str, status: str) -> list[SecurityBugRecord]:
        clauses: list[str] = []
        parameters: list[str] = []
        if target_fingerprint:
            clauses.append("target_fingerprint = %s")
            parameters.append(target_fingerprint)
        if status:
            clauses.append("status = %s")
            parameters.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT record FROM {self._table}{where} ORDER BY last_seen_at DESC, bug_id ASC",
                    tuple(parameters),
                )
                rows = cur.fetchall() or []
        return [self._record_from_value(row["record"]) for row in rows]

    def _save_sync(self, bug: SecurityBugRecord) -> SecurityBugRecord:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET target_fingerprint = %s,
                        affected_target = %s,
                        status = %s,
                        verification_level = %s,
                        severity = %s,
                        first_seen_at = %s,
                        last_seen_at = %s,
                        occurrence_count = %s,
                        record = %s::jsonb
                    WHERE bug_id = %s
                    """,
                    (
                        bug.target_fingerprint,
                        bug.affected_target,
                        bug.status,
                        bug.verification_level,
                        bug.severity,
                        bug.first_seen_at,
                        bug.last_seen_at,
                        bug.occurrence_count,
                        self._record_json(bug),
                        bug.bug_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise KeyError(f"Security Bug not found: {bug.bug_id}")
        return bug

    def _record_parameters(self, bug: SecurityBugRecord) -> tuple[object, ...]:
        return (
            bug.bug_id,
            bug.fingerprint,
            bug.target_fingerprint,
            bug.affected_target,
            bug.status,
            bug.verification_level,
            bug.severity,
            bug.first_seen_at,
            bug.last_seen_at,
            bug.occurrence_count,
            self._record_json(bug),
        )

    def _record_json(self, bug: SecurityBugRecord) -> str:
        return json.dumps(make_json_safe(bug), ensure_ascii=False, separators=(",", ":"))

    def _record_from_value(self, value: object) -> SecurityBugRecord:
        if isinstance(value, str):
            value = json.loads(value)
        return SecurityBugRecord.model_validate(value)


def merge_security_bug_records(
    existing: SecurityBugRecord,
    candidate: SecurityBugRecord,
) -> SecurityBugRecord:
    """Merge one reproduced occurrence without losing lifecycle history."""
    merged = existing.model_copy(deep=True)
    merged.title = candidate.title or merged.title
    merged.verification_level = _stronger_verification_level(
        merged.verification_level,
        candidate.verification_level,
    )
    if SEVERITY_ORDER.get(candidate.severity, 0) > SEVERITY_ORDER.get(merged.severity, 0):
        merged.severity = candidate.severity
    if merged.status in {"fixed", "closed", "false_positive"}:
        merged.status = "retest_failed"
    elif merged.status not in {"confirmed", "retest_failed"}:
        merged.status = candidate.status or "confirmed"
    for field_name in (
        "cvss_vector",
        "cvss_rationale",
        "affected_target",
        "affected_component",
        "reproduction_request",
        "expected_result",
        "actual_result",
        "business_impact",
        "remediation",
        "regression_case_id",
        "regression_profile",
    ):
        value = getattr(candidate, field_name)
        if value not in {None, ""}:
            setattr(merged, field_name, value)
    if candidate.cvss_score is not None:
        merged.cvss_score = candidate.cvss_score
    merged.last_seen_at = candidate.last_seen_at or _utc_now()
    merged.cve_ids = _unique([*merged.cve_ids, *candidate.cve_ids])
    merged.cwe_ids = _unique([*merged.cwe_ids, *candidate.cwe_ids])
    merged.owasp_categories = _unique([*merged.owasp_categories, *candidate.owasp_categories])
    merged.affected_versions = _unique([*merged.affected_versions, *candidate.affected_versions])
    merged.preconditions = _unique([*merged.preconditions, *candidate.preconditions])
    merged.reproduction_steps = _unique([*merged.reproduction_steps, *candidate.reproduction_steps])
    merged.evidence_ids = _unique([*merged.evidence_ids, *candidate.evidence_ids])
    merged.exposed_data_types = _unique([*merged.exposed_data_types, *candidate.exposed_data_types])
    if candidate.exposed_record_estimate is not None:
        merged.exposed_record_estimate = max(
            merged.exposed_record_estimate or 0,
            candidate.exposed_record_estimate,
        )
    for field_name in (
        "confidentiality_impact",
        "integrity_impact",
        "availability_impact",
    ):
        current_impact = getattr(merged, field_name)
        candidate_impact = getattr(candidate, field_name)
        if _IMPACT_ORDER.get(candidate_impact, 0) > _IMPACT_ORDER.get(current_impact, 0):
            setattr(merged, field_name, candidate_impact)
    merged.campaign_ids = _unique([*merged.campaign_ids, *candidate.campaign_ids])
    merged.finding_ids = _unique([*merged.finding_ids, *candidate.finding_ids])
    merged.attempt_ids = _unique([*merged.attempt_ids, *candidate.attempt_ids])
    evidence_keys = {
        (item.campaign_id, item.artifact_id, item.attempt_id)
        for item in merged.evidence_refs
    }
    for item in candidate.evidence_refs:
        key = (item.campaign_id, item.artifact_id, item.attempt_id)
        if key not in evidence_keys:
            merged.evidence_refs.append(item)
            evidence_keys.add(key)
    retest_ids = {item.retest_id for item in merged.retest_history}
    for item in candidate.retest_history:
        if item.retest_id and item.retest_id not in retest_ids:
            merged.retest_history.append(item)
            retest_ids.add(item.retest_id)
    merged.occurrence_count = sum(
        1 for item in merged.retest_history if item.outcome == "reproduced"
    )
    return merged


def _stronger_verification_level(current: str, candidate: str) -> str:
    order = {"observed": 0, "confirmed": 1, "exploitable": 2, "impact_verified": 3}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


_IMPACT_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "SecurityBugStore",
    "InMemorySecurityBugStore",
    "PostgresSecurityBugStore",
    "merge_security_bug_records",
]
