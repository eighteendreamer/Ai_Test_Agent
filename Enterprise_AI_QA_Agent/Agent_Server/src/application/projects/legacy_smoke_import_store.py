from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import UUID, uuid5

from src.core.config import Settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.smoke_testing_mode.contracts import SmokeRunResult
from src.schemas.case_management import (
    TestCaseAssertion,
    TestCaseRecord,
    TestCaseSourceRef,
    TestCaseStep,
    TestCaseVersionRecord,
)
from src.schemas.run_management import (
    RunEvidenceRef,
    TestCaseResultRecord,
    TestRunAttemptRecord,
    TestRunItemRecord,
    TestRunRecord,
    TestRunStats,
)
from src.schemas.suite_management import TestSuiteItemRecord, TestSuiteRecord


IMPORT_SOURCE_SYSTEM = "legacy_smoke_catalog"
IMPORT_CREATED_BY = "legacy-smoke-import"
_IMPORT_NAMESPACE = UUID("9de866be-b0e9-5a0b-90d5-6dfde02b4bc1")


@dataclass(frozen=True)
class LegacySmokeImportedBundle:
    source_system: str
    legacy_run_id: str
    source_hash: str
    source_snapshot: dict[str, Any]
    cases: list[tuple[TestCaseRecord, TestCaseVersionRecord]]
    suite: TestSuiteRecord
    suite_items: list[TestSuiteItemRecord]
    run: TestRunRecord
    run_items: list[TestRunItemRecord]
    attempts: list[TestRunAttemptRecord]
    results: list[TestCaseResultRecord]


ImportAction = Literal["imported", "already_imported"]


class LegacySmokeImportStore(Protocol):
    async def initialize(self) -> None: ...
    async def import_bundle(self, bundle: LegacySmokeImportedBundle) -> tuple[ImportAction, str]: ...


class InMemoryLegacySmokeImportStore:
    """Test double that retains the canonical bundle produced by the importer."""

    def __init__(self) -> None:
        self.bundles: dict[str, LegacySmokeImportedBundle] = {}

    async def initialize(self) -> None:
        return None

    async def import_bundle(self, bundle: LegacySmokeImportedBundle) -> tuple[ImportAction, str]:
        existing = self.bundles.get(bundle.legacy_run_id)
        if existing is not None:
            if existing.source_hash != bundle.source_hash:
                raise ValueError(
                    "Legacy Smoke source snapshot changed after import: "
                    f"{bundle.legacy_run_id}"
                )
            return "already_imported", existing.run.id
        self.bundles[bundle.legacy_run_id] = bundle
        return "imported", bundle.run.id


class PostgresLegacySmokeImportStore:
    """One-transaction writer for immutable imported Smoke history snapshots."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def _ledger_table(self) -> str:
        return "agent_legacy_smoke_import_ledger"

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def import_bundle(self, bundle: LegacySmokeImportedBundle) -> tuple[ImportAction, str]:
        return await asyncio.to_thread(self._import_bundle_sync, bundle)

    def _initialize_sync(self) -> None:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._ledger_table} (
                        source_system TEXT NOT NULL,
                        legacy_run_id TEXT NOT NULL,
                        project_id UUID NOT NULL REFERENCES {self._settings.database.postgres_project_table}(id),
                        source_hash TEXT NOT NULL,
                        canonical_run_id UUID NOT NULL REFERENCES {self._settings.database.postgres_test_run_table}(id)
                            DEFERRABLE INITIALLY DEFERRED,
                        source_snapshot JSONB NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('imported')),
                        imported_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (source_system, legacy_run_id)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{self._ledger_table}_project_imported "
                    f"ON {self._ledger_table} (project_id, imported_at DESC)"
                )

    def _import_bundle_sync(self, bundle: LegacySmokeImportedBundle) -> tuple[ImportAction, str]:
        with postgres_connect(self._settings) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._ledger_table} ("
                    "source_system, legacy_run_id, project_id, source_hash, canonical_run_id, "
                    "source_snapshot, status, imported_at"
                    ") VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'imported', %s) "
                    "ON CONFLICT (source_system, legacy_run_id) DO NOTHING "
                    "RETURNING canonical_run_id",
                    (
                        bundle.source_system,
                        bundle.legacy_run_id,
                        bundle.run.project_id,
                        bundle.source_hash,
                        bundle.run.id,
                        _json(bundle.source_snapshot),
                        bundle.run.completed_at or bundle.run.updated_at,
                    ),
                )
                inserted_ledger = cur.fetchone()
                if inserted_ledger is None:
                    cur.execute(
                        f"SELECT source_hash, canonical_run_id FROM {self._ledger_table} "
                        "WHERE source_system = %s AND legacy_run_id = %s",
                        (bundle.source_system, bundle.legacy_run_id),
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise RuntimeError("Legacy Smoke import ledger conflict could not be resolved")
                    if str(existing["source_hash"]) != bundle.source_hash:
                        raise ValueError(
                            "Legacy Smoke source snapshot changed after import: "
                            f"{bundle.legacy_run_id}"
                        )
                    return "already_imported", str(existing["canonical_run_id"])

                self._write_bundle(cur, bundle)
        return "imported", bundle.run.id

    def _write_bundle(self, cur, bundle: LegacySmokeImportedBundle) -> None:
        case_table = self._settings.database.postgres_test_case_table
        version_table = self._settings.database.postgres_test_case_version_table
        suite_table = self._settings.database.postgres_test_suite_table
        suite_item_table = self._settings.database.postgres_test_suite_item_table
        run_table = self._settings.database.postgres_test_run_table
        item_table = self._settings.database.postgres_test_run_item_table
        attempt_table = self._settings.database.postgres_test_run_attempt_table
        result_table = self._settings.database.postgres_test_case_result_table
        cur.executemany(
            f"INSERT INTO {case_table} ("
            "id, project_id, case_key, lifecycle_status, mode_key, priority, "
            "active_version_id, latest_version, updated_at, record"
            ") VALUES (%s, %s, %s, %s, %s, %s, NULL, 1, %s, %s::jsonb)",
            [
                (
                    case.id,
                    case.project_id,
                    case.case_key,
                    case.lifecycle_status,
                    case.mode_key,
                    case.priority,
                    case.updated_at,
                    _json(case.model_dump(mode="json")),
                )
                for case, _ in bundle.cases
            ],
        )
        cur.executemany(
            f"INSERT INTO {version_table} (id, case_id, version, content_hash, created_at, record) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            [
                (
                    version.id,
                    version.case_id,
                    version.version,
                    version.content_hash,
                    version.created_at,
                    _json(version.model_dump(mode="json")),
                )
                for _, version in bundle.cases
            ],
        )
        cur.execute(
            f"INSERT INTO {suite_table} (id, project_id, status, updated_at, record) "
            "VALUES (%s, %s, %s, %s, %s::jsonb)",
            (
                bundle.suite.id,
                bundle.suite.project_id,
                bundle.suite.status,
                bundle.suite.updated_at,
                _json(bundle.suite.model_dump(mode="json")),
            ),
        )
        cur.executemany(
            f"INSERT INTO {suite_item_table} (id, suite_id, case_id, case_version_id, position, record) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            [
                (
                    item.id,
                    item.suite_id,
                    item.case_id,
                    item.case_version_id,
                    item.position,
                    _json(item.model_dump(mode="json")),
                )
                for item in bundle.suite_items
            ],
        )
        cur.execute(
            f"INSERT INTO {run_table} ("
            "id, project_id, suite_id, status, mode_key, session_id, parent_run_id, "
            "created_at, updated_at, record"
            ") VALUES (%s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s::jsonb)",
            (
                bundle.run.id,
                bundle.run.project_id,
                bundle.run.suite_id,
                bundle.run.status,
                bundle.run.mode_key,
                bundle.run.created_at,
                bundle.run.updated_at,
                _json(bundle.run.model_dump(mode="json")),
            ),
        )
        cur.executemany(
            f"INSERT INTO {item_table} ("
            "id, run_id, case_id, case_version_id, position, status, attempt_no, "
            "lease_owner, lease_token, lease_expires_at, result_id, regression_source_result_id, "
            "updated_at, record"
            ") VALUES (%s, %s, %s, %s, %s, %s, 1, NULL, NULL, NULL, %s, NULL, %s, %s::jsonb)",
            [
                (
                    item.id,
                    item.run_id,
                    item.case_id,
                    item.case_version_id,
                    item.position,
                    item.status,
                    item.result_id,
                    item.updated_at,
                    _json(item.model_dump(mode="json")),
                )
                for item in bundle.run_items
            ],
        )
        cur.executemany(
            f"INSERT INTO {attempt_table} ("
            "id, run_id, run_item_id, attempt_no, lease_token, status, record"
            ") VALUES (%s, %s, %s, 1, %s, %s, %s::jsonb)",
            [
                (
                    attempt.id,
                    attempt.run_id,
                    attempt.run_item_id,
                    attempt.lease_token,
                    attempt.status,
                    _json(attempt.model_dump(mode="json")),
                )
                for attempt in bundle.attempts
            ],
        )
        cur.executemany(
            f"INSERT INTO {result_table} ("
            "id, run_id, run_item_id, case_id, case_version_id, attempt_id, status, "
            "payload_hash, regression_source_result_id, created_at, record"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s::jsonb)",
            [
                (
                    result.id,
                    result.run_id,
                    result.run_item_id,
                    result.case_id,
                    result.case_version_id,
                    result.attempt_id,
                    result.status,
                    result.payload_hash,
                    result.created_at,
                    _json(result.model_dump(mode="json")),
                )
                for result in bundle.results
            ],
        )


def build_legacy_smoke_import_bundle(
    *,
    project_id: str,
    source_record: dict[str, Any],
    snapshot: SmokeRunResult,
) -> LegacySmokeImportedBundle:
    legacy_run_id = str(source_record["run_id"])
    legacy_plan_id = str(source_record["plan_id"])
    source_snapshot = snapshot.model_dump(mode="json")
    # Hash the catalog's persisted payload, not Pydantic defaults materialized
    # during validation (for example, a missing started_at gets a fresh now()).
    # This keeps retries idempotent while still storing the validated snapshot.
    source_hash_payload = {
        "source_system": IMPORT_SOURCE_SYSTEM,
        "row": {
            "run_id": legacy_run_id,
            "plan_id": legacy_plan_id,
            "plan_version": source_record.get("plan_version"),
            "project_scope": source_record.get("project_scope"),
            "started_at": source_record.get("started_at"),
            "completed_at": source_record.get("completed_at"),
        },
        "metadata": source_record.get("metadata"),
    }
    source_hash = _hash(
        source_hash_payload
    )
    started_at = _parse_time(snapshot.started_at) or _as_datetime(source_record.get("started_at"))
    if started_at is None:
        raise ValueError(f"Legacy Smoke snapshot has no valid started_at: {legacy_run_id}")
    completed_at = _parse_time(snapshot.completed_at) or _as_datetime(source_record.get("completed_at")) or started_at
    suite_id = _stable_id("suite", legacy_run_id)
    run_id = _stable_id("run", legacy_run_id)
    cases: list[tuple[TestCaseRecord, TestCaseVersionRecord]] = []
    suite_items: list[TestSuiteItemRecord] = []
    run_items: list[TestRunItemRecord] = []
    attempts: list[TestRunAttemptRecord] = []
    results: list[TestCaseResultRecord] = []
    status_counts = {"passed": 0, "failed": 0, "blocked": 0, "skipped": 0}
    for position, legacy_case in enumerate(snapshot.case_results, start=1):
        mapped_status = _mapped_status(legacy_case.status)
        status_counts[mapped_status] += 1
        case_id = _stable_id("case", legacy_run_id, legacy_case.case_id)
        version_id = _stable_id("version", legacy_run_id, legacy_case.case_id)
        suite_item_id = _stable_id("suite-item", legacy_run_id, legacy_case.case_id)
        run_item_id = _stable_id("run-item", legacy_run_id, legacy_case.case_id)
        attempt_id = _stable_id("attempt", legacy_run_id, legacy_case.case_id)
        result_id = _stable_id("result", legacy_run_id, legacy_case.case_id)
        title = legacy_case.title or f"历史冒烟用例 {legacy_case.case_id}"
        source_ref = TestCaseSourceRef(
            source_type=IMPORT_SOURCE_SYSTEM,
            source_id=f"{legacy_run_id}:{legacy_case.case_id}",
            version=str(snapshot.plan_version),
            label=title,
            metadata={
                "legacy_run_id": legacy_run_id,
                "legacy_plan_id": legacy_plan_id,
                "legacy_case_id": legacy_case.case_id,
                "project_scope": str(source_record.get("project_scope") or ""),
            },
        )
        step = TestCaseStep(
            order=1,
            kind="legacy_observation",
            action="导入的历史冒烟执行快照，不可作为新的执行步骤。",
            expected=legacy_case.status,
            data={"read_only": True, "legacy_case_id": legacy_case.case_id},
        )
        assertion = TestCaseAssertion(
            kind="legacy_observed_status",
            target="legacy.status",
            expected=legacy_case.status,
            description="历史 Smoke 快照中的原始执行状态。",
        )
        version_payload = {
            "case_id": case_id,
            "preconditions": [],
            "steps": [step.model_dump(mode="json")],
            "assertions": [assertion.model_dump(mode="json")],
            "test_data": {"read_only": True, "legacy_source": source_ref.model_dump(mode="json")},
            "cleanup": [],
            "source_refs": [source_ref.model_dump(mode="json")],
            "model_key": "legacy-import",
            "prompt_version": "legacy-smoke-snapshot",
            "skill_versions": {"legacy-smoke-import": "v1"},
        }
        version = TestCaseVersionRecord(
            id=version_id,
            case_id=case_id,
            version=1,
            preconditions=[],
            steps=[step],
            assertions=[assertion],
            test_data=version_payload["test_data"],
            cleanup=[],
            content_hash=_hash(version_payload),
            source_refs=[source_ref],
            model_key="legacy-import",
            prompt_version="legacy-smoke-snapshot",
            skill_versions={"legacy-smoke-import": "v1"},
            created_at=started_at,
        )
        case = TestCaseRecord(
            id=case_id,
            project_id=project_id,
            case_key=f"legacy-smoke-{_hash({'run': legacy_run_id, 'case': legacy_case.case_id})[:24]}",
            title=title,
            mode_key="smoke_testing",
            case_type="legacy_smoke_snapshot",
            lifecycle_status="archived",
            created_by=IMPORT_CREATED_BY,
            created_at=started_at,
            updated_at=completed_at,
            archived_at=completed_at,
        )
        actual = {
            "source_system": IMPORT_SOURCE_SYSTEM,
            "legacy_smoke_run_id": legacy_run_id,
            "legacy_smoke_plan_id": legacy_plan_id,
            "legacy_smoke_case_id": legacy_case.case_id,
            "legacy_status": legacy_case.status,
            "legacy_case_result": legacy_case.model_dump(mode="json"),
            "read_only": True,
        }
        evidence = RunEvidenceRef(
            evidence_type="legacy_smoke_snapshot",
            evidence_id=f"{legacy_run_id}:{legacy_case.case_id}",
            label=title,
            metadata={"legacy_status": legacy_case.status, "evidence_count": len(legacy_case.evidence)},
        )
        completion_payload = {
            "status": mapped_status,
            "summary": legacy_case.summary or f"Imported legacy Smoke status: {legacy_case.status}",
            "actual": actual,
            "evidence_refs": [evidence.model_dump(mode="json")],
            "artifact_ids": [],
            "verification_ids": [],
            "tool_job_id": None,
            "metrics": {"duration_ms": max(0, int(legacy_case.duration_ms or 0))},
            "error_message": legacy_case.summary if mapped_status in {"failed", "blocked"} else None,
        }
        result = TestCaseResultRecord(
            id=result_id,
            run_id=run_id,
            run_item_id=run_item_id,
            case_id=case_id,
            case_version_id=version_id,
            attempt_id=attempt_id,
            attempt_no=1,
            status=mapped_status,
            summary=completion_payload["summary"],
            actual=actual,
            evidence_refs=[evidence],
            metrics=completion_payload["metrics"],
            error_message=completion_payload["error_message"],
            payload_hash=_hash(completion_payload),
            created_at=completed_at,
        )
        cases.append((case, version))
        suite_items.append(
            TestSuiteItemRecord(
                id=suite_item_id,
                suite_id=suite_id,
                case_id=case_id,
                case_version_id=version_id,
                position=position,
            )
        )
        run_items.append(
            TestRunItemRecord(
                id=run_item_id,
                run_id=run_id,
                case_id=case_id,
                case_version_id=version_id,
                position=position,
                status=mapped_status,
                attempt_no=1,
                result_id=result_id,
                created_at=started_at,
                updated_at=completed_at,
                started_at=started_at,
                completed_at=completed_at,
            )
        )
        attempts.append(
            TestRunAttemptRecord(
                id=attempt_id,
                run_id=run_id,
                run_item_id=run_item_id,
                attempt_no=1,
                worker_id=IMPORT_CREATED_BY,
                lease_token=f"legacy-import:{attempt_id}",
                status=mapped_status,
                claimed_at=started_at,
                started_at=started_at,
                completed_at=completed_at,
            )
        )
        results.append(result)
    suite = TestSuiteRecord(
        id=suite_id,
        project_id=project_id,
        name=f"历史 Smoke 导入：{legacy_plan_id}",
        description="只读历史快照，不可用于创建新的执行运行。",
        status="archived",
        created_by=IMPORT_CREATED_BY,
        created_at=started_at,
        updated_at=completed_at,
        archived_at=completed_at,
    )
    run = TestRunRecord(
        id=run_id,
        project_id=project_id,
        suite_id=suite_id,
        origin="legacy_smoke_import",
        mode_key="smoke_testing",
        status="completed",
        stats=TestRunStats(total=len(results), **status_counts),
        created_by=IMPORT_CREATED_BY,
        created_at=started_at,
        updated_at=completed_at,
        started_at=started_at,
        completed_at=completed_at,
    )
    return LegacySmokeImportedBundle(
        source_system=IMPORT_SOURCE_SYSTEM,
        legacy_run_id=legacy_run_id,
        source_hash=source_hash,
        source_snapshot=source_snapshot,
        cases=cases,
        suite=suite,
        suite_items=suite_items,
        run=run,
        run_items=run_items,
        attempts=attempts,
        results=results,
    )


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(_IMPORT_NAMESPACE, ":".join((kind, *[str(part) for part in parts]))))


def _mapped_status(value: str) -> str:
    if value in {"passed", "failed", "blocked"}:
        return value
    if value == "not_run":
        return "skipped"
    return "blocked"


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        return _parse_time(value)
    return None


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
