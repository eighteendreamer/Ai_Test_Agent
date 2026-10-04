from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from src.application.projects.project_service import ProjectService
from src.application.test_cases.case_store import TestCaseStore
from src.application.test_cases.template_service import TestCaseTemplateService
from src.application.test_cases.version_reviews import add_draft_review
from src.core.request_context import get_request_context, new_id
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
    TestCaseReviewSubmitRequest,
    TestCaseRecord,
    TestCaseVersionCreateRequest,
    TestCaseVersionRecord,
)
from src.schemas.embedding_task import TestCaseEmbeddingTask


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
        template_service: TestCaseTemplateService | None = None,
    ) -> None:
        self._store = store
        self._project_service = project_service
        self._context_provider = context_provider
        self._generator = generator
        self._templates = template_service
        self._embedding_task_outbox: dict[str, str] | None = None

    def set_embedding_task_outbox(
        self,
        *,
        embedding_job_table: str,
        outbox_table: str,
        stream: str,
    ) -> None:
        self._embedding_task_outbox = {
            "embedding_job_table": embedding_job_table,
            "outbox_table": outbox_table,
            "stream": stream,
        }

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
        template = await self._templates.get(project_id) if self._templates is not None else None
        if template is not None:
            context = {**context, "generation_template": template.model_dump(mode="json")}
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
                        content_json=generated.content_json,
                        execution_notes=generated.execution_notes or (template.payload.execution_notes if template else ""),
                        generation_metadata={**generated.generation_metadata, **({"template": template.model_dump(mode="json")} if template else {})},
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

    async def prepare_drafts(
        self, *, project_id: str, payloads: list[TestCaseDraftCreateRequest], created_by: str | None = None,
    ) -> list[tuple[TestCaseRecord, TestCaseVersionRecord]]:
        """Build immutable drafts for a caller-owned bulk transaction, without writing."""
        await self._project_service.require_active(project_id)
        return [self._build_draft(project_id=project_id, payload=payload, created_by=created_by) for payload in payloads]

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
        return add_draft_review(case, version), version

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

    async def list_project_current_versions(self, project_id: str, limit: int):
        await self._project_service.get(project_id)
        if limit < 1:
            raise ValueError("Current case version limit must be positive")
        return await self._store.list_project_current_versions(project_id, limit)

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

    async def submit_review(self, case_id: str, payload: TestCaseReviewSubmitRequest | None = None) -> TestCaseRecord:
        case = await self.get_case(case_id)
        await self._project_service.require_active(case.project_id)
        versions = await self._store.list_versions(case_id)
        selected = payload.version_id if payload and payload.version_id else versions[-1].id if versions else None
        if selected is None:
            raise ValueError("没有可提交评审的用例版本")
        stored = await self._store.transition_version_review(case_id, selected, "submitted", _utc_now(),
            request_id=payload.request_id if payload else None)
        logger.info("test_case_submitted_for_review", extra={"case_id": case_id, "version_id": selected,
            "request_id": payload.request_id if payload else None, "active_version_id": stored.active_version_id})
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
        if not requested_id:
            pending = [review.version_id for review in case.version_reviews.values() if review.status == "pending_review"]
            if len(pending) > 1:
                raise ValueError("多个版本等待评审，请明确指定启用版本")
            requested_id = pending[0] if pending else (versions[-1].id if versions and
                not case.version_reviews and case.lifecycle_status == "pending_review" else case.active_version_id)
        selected = next((version for version in versions if version.id == requested_id), None)
        if selected is None:
            raise ValueError(f"Test case version does not belong to case: {requested_id}")
        embedding = None
        if self._embedding_task_outbox is not None:
            context = get_request_context()
            task = TestCaseEmbeddingTask(
                task_id=f"embedding_test_case_{selected.id}_{uuid4().hex}",
                source_id=selected.id,
                source_version=str(selected.version),
                content_hash=selected.content_hash,
                request_id=(
                    context.request_id if context is not None else new_id("req")
                ),
                trace_id=(
                    context.trace_id if context is not None else new_id("trace")
                ),
                session_id=context.session_id if context is not None else None,
                turn_id=context.turn_id if context is not None else None,
                project_id=case.project_id,
                run_id=context.run_id if context is not None else None,
                run_item_id=context.run_item_id if context is not None else None,
                attempt_id=context.attempt_id if context is not None else None,
                worker_id=context.worker_id if context is not None else None,
                resource_id=context.resource_id if context is not None else None,
                case_id=case.id,
                case_version_id=selected.id,
            )
            embedding = {**self._embedding_task_outbox, "task_payload": task.model_dump(mode="json")}
        stored = await self._store.transition_version_review(case_id, selected.id, "activated", _utc_now(),
            request_id=payload.request_id if payload else None,
            expected_active=payload.expected_active_version_id if payload else None,
            check_active=bool(payload and "expected_active_version_id" in payload.model_fields_set), embedding=embedding)
        logger.info(
            "test_case_activation_processed",
            extra={"case_id": case_id, "version_id": selected.id, "active_version_id": stored.active_version_id,
                "request_id": payload.request_id if payload else None},
        )
        return stored

    async def migrate_legacy_reviews(self, project_id: str, *, dry_run=True):
        await self._project_service.get(project_id)
        if not dry_run:
            await self._project_service.require_active(project_id)
        changes = await self._store.migrate_legacy_reviews(project_id, _utc_now(), dry_run=dry_run)
        logger.info("test_case_review_migration_checked", extra={"project_id": project_id,
            "dry_run": dry_run, "case_count": len(changes)})
        return changes

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
        # Keep the exact legacy canonical shape for old-style draft requests.
        # New JSON and its own instructions are frozen with the version.
        if payload.content_json is not None:
            content["content_json"] = payload.content_json
        if payload.execution_notes:
            content["execution_notes"] = payload.execution_notes
        if payload.generation_metadata:
            content["generation_metadata"] = payload.generation_metadata
        canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return TestCaseVersionRecord(
            id=str(uuid4()),
            case_id=case_id,
            version=version_number,
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            created_at=created_at,
            content_format="json-v1" if payload.content_json is not None else "legacy-v1",
            **content,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
