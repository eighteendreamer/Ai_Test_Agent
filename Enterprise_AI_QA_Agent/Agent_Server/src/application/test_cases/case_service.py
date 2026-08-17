from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from src.application.projects.project_service import ProjectService
from src.application.test_cases.case_store import TestCaseStore
from src.schemas.case_management import (
    GeneratedTestCaseBatch,
    TestCaseActivateRequest,
    TestCaseBundle,
    TestCaseDraftCreateRequest,
    TestCaseGenerateRequest,
    TestCaseGenerationResponse,
    TestCaseLifecycleStatus,
    TestCasePage,
    TestCasePriority,
    TestCaseRecord,
    TestCaseVersionCreateRequest,
    TestCaseVersionRecord,
)


logger = logging.getLogger(__name__)


class TestCaseContextProvider(Protocol):
    async def collect(self, *, project, request: TestCaseGenerateRequest) -> dict[str, Any]: ...


class TestCaseGenerator(Protocol):
    async def generate(
        self,
        *,
        request: TestCaseGenerateRequest,
        context: dict[str, Any],
    ) -> GeneratedTestCaseBatch | dict[str, Any]: ...


class TestCaseService:
    def __init__(
        self,
        *,
        store: TestCaseStore,
        project_service: ProjectService,
        context_provider: TestCaseContextProvider | None = None,
        generator: TestCaseGenerator | None = None,
    ) -> None:
        self._store = store
        self._project_service = project_service
        self._context_provider = context_provider
        self._generator = generator

    async def initialize(self) -> None:
        await self._store.initialize()

    async def generate(
        self,
        project_id: str,
        request: TestCaseGenerateRequest,
    ) -> TestCaseGenerationResponse:
        project = await self._project_service.require_active(project_id)
        if self._context_provider is None or self._generator is None:
            raise RuntimeError("Test case generation dependencies are not configured")
        context = await self._context_provider.collect(project=project, request=request)
        batch = GeneratedTestCaseBatch.model_validate(
            await self._generator.generate(request=request, context=context)
        )
        source_refs = context.get("source_refs") or []
        if not source_refs:
            raise ValueError("Test case generation requires at least one traceable source")
        draft_entries: list[tuple[TestCaseRecord, TestCaseVersionRecord]] = []
        for generated in batch.cases[: request.max_cases]:
            draft_entries.append(
                self._build_draft(
                    project_id=project_id,
                    payload=TestCaseDraftCreateRequest(
                        case_key=generated.case_key,
                        title=generated.title,
                        mode_key=request.mode_key,
                        case_type=generated.case_type,
                        priority=generated.priority,
                        preconditions=generated.preconditions,
                        steps=generated.steps,
                        assertions=generated.assertions,
                        test_data=generated.test_data,
                        cleanup=generated.cleanup,
                        source_refs=source_refs,
                        model_key=batch.model_key,
                        prompt_version=batch.prompt_version,
                        skill_versions=batch.skill_versions,
                    )
                )
            )
        stored_entries = await self._store.create_many(draft_entries)
        bundles = [
            TestCaseBundle(case=case, version=version)
            for case, version in stored_entries
        ]
        logger.info(
            "test_cases_generated",
            extra={
                "project_id": project_id,
                "mode_key": request.mode_key,
                "case_count": len(bundles),
                "source_count": len(source_refs),
            },
        )
        return TestCaseGenerationResponse(
            items=bundles,
            warnings=[*list(context.get("warnings") or []), *batch.warnings],
        )

    async def create_draft(
        self,
        *,
        project_id: str,
        payload: TestCaseDraftCreateRequest,
        created_by: str | None = None,
    ) -> TestCaseBundle:
        await self._project_service.require_active(project_id)
        case, version = self._build_draft(
            project_id=project_id,
            payload=payload,
            created_by=created_by,
        )
        stored_case, stored_version = await self._store.create(case, version)
        logger.info(
            "test_case_draft_created",
            extra={"project_id": project_id, "case_id": case.id, "version_id": version.id},
        )
        return TestCaseBundle(case=stored_case, version=stored_version)

    def _build_draft(
        self,
        *,
        project_id: str,
        payload: TestCaseDraftCreateRequest,
        created_by: str | None = None,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]:
        now = _utc_now()
        case = TestCaseRecord(
            id=str(uuid4()),
            project_id=project_id,
            case_key=payload.case_key.strip().lower(),
            title=payload.title.strip(),
            mode_key=payload.mode_key.strip(),
            case_type=payload.case_type.strip(),
            priority=payload.priority,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        version = self._build_version(
            case_id=case.id,
            version_number=1,
            payload=payload,
            created_at=now,
        )
        return case, version

    async def get_case(self, case_id: str) -> TestCaseRecord:
        case = await self._store.get_case(case_id)
        if case is None:
            raise KeyError(f"Test case not found: {case_id}")
        return case

    async def get_cases(self, case_ids: list[str]) -> dict[str, TestCaseRecord]:
        cases = await self._store.get_cases(case_ids)
        missing = [case_id for case_id in dict.fromkeys(case_ids) if case_id not in cases]
        if missing:
            raise KeyError("Test cases not found: " + ", ".join(missing))
        return cases

    async def list_cases(
        self,
        *,
        project_id: str,
        status: TestCaseLifecycleStatus | None,
        mode_key: str | None,
        priority: TestCasePriority | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> TestCasePage:
        await self._project_service.get(project_id)
        items, has_more = await self._store.list_cases(
            project_id=project_id,
            status=status,
            mode_key=(mode_key or "").strip() or None,
            priority=priority,
            query=(query or "").strip() or None,
            limit=limit,
            offset=offset,
        )
        return TestCasePage(items=items, limit=limit, offset=offset, has_more=has_more)

    async def list_versions(self, case_id: str) -> list[TestCaseVersionRecord]:
        await self.get_case(case_id)
        return await self._store.list_versions(case_id)

    async def get_version(self, version_id: str) -> TestCaseVersionRecord:
        version = await self._store.get_version(version_id)
        if version is None:
            raise KeyError(f"Test case version not found: {version_id}")
        return version

    async def get_versions(
        self,
        version_ids: list[str],
    ) -> dict[str, TestCaseVersionRecord]:
        versions = await self._store.get_versions(version_ids)
        missing = [
            version_id
            for version_id in dict.fromkeys(version_ids)
            if version_id not in versions
        ]
        if missing:
            raise KeyError("Test case versions not found: " + ", ".join(missing))
        return versions

    async def create_version(
        self,
        case_id: str,
        payload: TestCaseVersionCreateRequest,
    ) -> TestCaseVersionRecord:
        case = await self.get_case(case_id)
        await self._project_service.require_active(case.project_id)
        version = self._build_version(
            case_id=case_id,
            version_number=1,
            payload=payload,
            created_at=_utc_now(),
        )
        updated_case, stored_version = await self._store.append_version(case_id, version)
        logger.info(
            "test_case_version_created",
            extra={
                "case_id": case_id,
                "version_id": stored_version.id,
                "version": stored_version.version,
                "active_version_id": updated_case.active_version_id,
            },
        )
        return stored_version

    async def submit_review(self, case_id: str) -> TestCaseRecord:
        case = await self.get_case(case_id)
        await self._project_service.require_active(case.project_id)
        updated = case.model_copy(
            update={"lifecycle_status": "pending_review", "updated_at": _utc_now()}
        )
        stored = await self._store.replace_case(updated, expected_statuses={"draft"})
        logger.info("test_case_submitted_for_review", extra={"case_id": case_id})
        return stored

    async def activate(
        self,
        case_id: str,
        payload: TestCaseActivateRequest | None = None,
    ) -> TestCaseRecord:
        case = await self.get_case(case_id)
        await self._project_service.require_active(case.project_id)
        versions = await self._store.list_versions(case_id)
        requested_id = payload.version_id if payload else None
        selected = next(
            (version for version in versions if version.id == requested_id),
            versions[-1] if versions and not requested_id else None,
        )
        if selected is None:
            raise ValueError(f"Test case version does not belong to case: {requested_id}")
        updated = case.model_copy(
            update={
                "lifecycle_status": "active",
                "active_version_id": selected.id,
                "updated_at": _utc_now(),
            }
        )
        stored = await self._store.replace_case(updated, expected_statuses={"pending_review"})
        logger.info(
            "test_case_activated",
            extra={"case_id": case_id, "version_id": selected.id},
        )
        return stored

    async def archive(self, case_id: str) -> TestCaseRecord:
        case = await self.get_case(case_id)
        if case.lifecycle_status == "archived":
            return case
        now = _utc_now()
        updated = case.model_copy(
            update={"lifecycle_status": "archived", "updated_at": now, "archived_at": now}
        )
        return await self._store.replace_case(
            updated,
            expected_statuses={"draft", "pending_review", "active", "disabled"},
        )

    async def require_active_version(
        self,
        case_id: str,
        version_id: str,
    ) -> tuple[TestCaseRecord, TestCaseVersionRecord]:
        case = await self.get_case(case_id)
        if case.active_version_id != version_id:
            raise ValueError(
                f"Suite item must reference the active version for test case {case_id}"
            )
        version = await self.get_version(version_id)
        if version.case_id != case_id:
            raise ValueError(f"Test case version belongs to another case: {version_id}")
        return case, version

    async def require_active_versions(
        self,
        pairs: list[tuple[str, str]],
    ) -> dict[str, tuple[TestCaseRecord, TestCaseVersionRecord]]:
        by_case_id = await self._store.get_active_case_versions(
            [case_id for case_id, _ in pairs]
        )
        for case_id, version_id in pairs:
            pair = by_case_id.get(case_id)
            if pair is None:
                raise ValueError(f"Test case has no active version: {case_id}")
            case, version = pair
            if case.active_version_id != version_id or version.id != version_id:
                raise ValueError(
                    f"Suite item must reference the active version for test case {case_id}"
                )
        return by_case_id

    def _build_version(
        self,
        *,
        case_id: str,
        version_number: int,
        payload: TestCaseDraftCreateRequest | TestCaseVersionCreateRequest,
        created_at: datetime,
    ) -> TestCaseVersionRecord:
        content = {
            "preconditions": payload.preconditions,
            "steps": [item.model_dump(mode="json") for item in payload.steps],
            "assertions": [item.model_dump(mode="json") for item in payload.assertions],
            "test_data": payload.test_data,
            "cleanup": payload.cleanup,
            "source_refs": [item.model_dump(mode="json") for item in payload.source_refs],
            "model_key": payload.model_key,
            "prompt_version": payload.prompt_version,
            "skill_versions": payload.skill_versions,
        }
        canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return TestCaseVersionRecord(
            id=str(uuid4()),
            case_id=case_id,
            version=version_number,
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            created_at=created_at,
            **content,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
