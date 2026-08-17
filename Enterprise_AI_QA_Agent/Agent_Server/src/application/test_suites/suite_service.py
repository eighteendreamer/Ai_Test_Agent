from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from src.application.projects.project_service import ProjectService
from src.application.test_cases.case_service import TestCaseService
from src.application.test_suites.suite_store import TestSuiteStore
from src.schemas.suite_management import (
    TestSuiteBundle,
    TestSuiteCreateRequest,
    TestSuiteItemRecord,
    TestSuitePage,
    TestSuiteRecord,
)


logger = logging.getLogger(__name__)


class TestSuiteService:
    def __init__(
        self,
        *,
        store: TestSuiteStore,
        project_service: ProjectService,
        test_case_service: TestCaseService,
    ) -> None:
        self._store = store
        self._projects = project_service
        self._cases = test_case_service

    async def initialize(self) -> None:
        await self._store.initialize()

    async def create(
        self,
        project_id: str,
        payload: TestSuiteCreateRequest,
        *,
        created_by: str | None = None,
    ) -> TestSuiteBundle:
        await self._projects.require_active(project_id)
        pairs = [(item.case_id, item.case_version_id) for item in payload.items]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Test suite contains duplicate case-version items")
        active_versions = await self._cases.require_active_versions(pairs)
        for case_id, _ in pairs:
            case, _ = active_versions[case_id]
            if case.project_id != project_id:
                raise ValueError(f"Test case belongs to another project: {case_id}")
        now = datetime.now(timezone.utc)
        suite = TestSuiteRecord(
            id=str(uuid4()),
            project_id=project_id,
            name=payload.name.strip(),
            description=(payload.description or "").strip() or None,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        items = [
            TestSuiteItemRecord(
                id=str(uuid4()),
                suite_id=suite.id,
                case_id=item.case_id,
                case_version_id=item.case_version_id,
                position=index,
            )
            for index, item in enumerate(payload.items, start=1)
        ]
        stored = await self._store.create(suite, items)
        logger.info(
            "test_suite_created",
            extra={"project_id": project_id, "suite_id": suite.id, "item_count": len(items)},
        )
        return stored

    async def get(self, suite_id: str) -> TestSuiteBundle:
        suite = await self._store.get(suite_id)
        if suite is None:
            raise KeyError(f"Test suite not found: {suite_id}")
        return suite

    async def list(self, project_id: str, *, limit: int, offset: int) -> TestSuitePage:
        await self._projects.get(project_id)
        items, has_more = await self._store.list(
            project_id=project_id,
            limit=limit,
            offset=offset,
        )
        return TestSuitePage(items=items, limit=limit, offset=offset, has_more=has_more)

    async def archive(self, suite_id: str) -> TestSuiteRecord:
        bundle = await self.get(suite_id)
        if bundle.suite.status == "archived":
            return bundle.suite
        now = datetime.now(timezone.utc)
        return await self._store.replace(
            bundle.suite.model_copy(
                update={"status": "archived", "updated_at": now, "archived_at": now}
            )
        )
