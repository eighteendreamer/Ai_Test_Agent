from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib import import_module

import pytest

from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore
from src.schemas.project import ProjectCreateRequest


def _components():
    try:
        case_service_module = import_module("src.application.test_cases.case_service")
        case_store_module = import_module("src.application.test_cases.case_store")
        suite_service_module = import_module("src.application.test_suites.suite_service")
        suite_store_module = import_module("src.application.test_suites.suite_store")
        schemas = import_module("src.schemas.case_management")
        suite_schemas = import_module("src.schemas.suite_management")
    except ModuleNotFoundError as exc:
        pytest.fail(f"test suite version-freeze module is not implemented: {exc}")
    return (
        case_service_module.TestCaseService,
        case_store_module.InMemoryTestCaseStore,
        suite_service_module.TestSuiteService,
        suite_store_module.InMemoryTestSuiteStore,
        schemas,
        suite_schemas,
    )


def test_suite_freezes_only_the_explicit_active_case_version():
    async def scenario():
        case_service_type, case_store_type, suite_service_type, suite_store_type, schemas, suite_schemas = _components()
        projects = ProjectService(store=InMemoryProjectStore())
        await projects.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="orders", name="Orders"))
        cases = case_service_type(store=case_store_type(), project_service=projects)
        await cases.initialize()
        generated = await cases.create_draft(
            project_id=project.id,
            payload=schemas.TestCaseDraftCreateRequest(
                case_key="orders-create",
                title="创建订单",
                mode_key="api_testing",
                case_type="api",
                priority="P0",
                preconditions=["API 可访问"],
                steps=[{"order": 1, "action": "提交订单", "expected": "创建成功"}],
                assertions=[{"kind": "status_code", "operator": "equals", "expected": 201}],
                source_refs=[{"source_type": "api_doc", "source_id": "doc-1", "label": "Orders API"}],
                model_key="qa-model",
                prompt_version="v1",
                skill_versions={"generate-test-cases": "sha256:v1"},
            ),
        )
        await cases.submit_review(generated.case.id)
        active_case = await cases.activate(generated.case.id)
        version_one_id = active_case.active_version_id

        version_two = await cases.create_version(
            generated.case.id,
            schemas.TestCaseVersionCreateRequest(
                preconditions=["API 可访问"],
                steps=[{"order": 1, "action": "提交边界订单", "expected": "创建成功"}],
                assertions=[{"kind": "status_code", "operator": "equals", "expected": 201}],
                source_refs=[{"source_type": "api_doc", "source_id": "doc-1", "label": "Orders API"}],
                model_key="qa-model",
                prompt_version="v2",
                skill_versions={"generate-test-cases": "sha256:v1"},
            ),
        )
        suites = suite_service_type(
            store=suite_store_type(),
            project_service=projects,
            test_case_service=cases,
        )
        await suites.initialize()

        suite = await suites.create(
            project.id,
            suite_schemas.TestSuiteCreateRequest(
                name="订单核心回归",
                items=[{"case_id": generated.case.id, "case_version_id": version_one_id}],
            ),
        )

        assert suite.items[0].case_id == generated.case.id
        assert suite.items[0].case_version_id == version_one_id
        assert suite.items[0].case_version_id != version_two.id
        with pytest.raises(ValueError, match="active version"):
            await suites.create(
                project.id,
                suite_schemas.TestSuiteCreateRequest(
                    name="非法候选版本套件",
                    items=[{"case_id": generated.case.id, "case_version_id": version_two.id}],
                ),
            )

    asyncio.run(scenario())


def test_suite_rejects_case_from_another_project():
    async def scenario():
        case_service_type, case_store_type, suite_service_type, suite_store_type, schemas, suite_schemas = _components()
        projects = ProjectService(store=InMemoryProjectStore())
        await projects.initialize()
        first = await projects.create(ProjectCreateRequest(project_key="first", name="First"))
        second = await projects.create(ProjectCreateRequest(project_key="second", name="Second"))
        cases = case_service_type(store=case_store_type(), project_service=projects)
        await cases.initialize()
        generated = await cases.create_draft(
            project_id=first.id,
            payload=schemas.TestCaseDraftCreateRequest(
                case_key="first-case",
                title="First case",
                mode_key="api_testing",
                case_type="api",
                steps=[{"order": 1, "action": "Run", "expected": "Done"}],
                assertions=[{"kind": "result", "operator": "equals", "expected": "done"}],
                source_refs=[{"source_type": "api_doc", "source_id": "doc-1", "label": "First API"}],
                model_key="qa-model",
                prompt_version="v1",
                skill_versions={"generate-test-cases": "sha256:v1"},
            ),
        )
        await cases.submit_review(generated.case.id)
        active = await cases.activate(generated.case.id)
        suites = suite_service_type(
            store=suite_store_type(),
            project_service=projects,
            test_case_service=cases,
        )
        await suites.initialize()

        with pytest.raises(ValueError, match="another project"):
            await suites.create(
                second.id,
                suite_schemas.TestSuiteCreateRequest(
                    name="Cross project",
                    items=[{"case_id": generated.case.id, "case_version_id": active.active_version_id}],
                ),
            )

    asyncio.run(scenario())


def test_large_suite_uses_one_batch_active_version_lookup():
    async def scenario():
        case_service_type, case_store_type, suite_service_type, suite_store_type, schemas, suite_schemas = _components()

        class CountingStore(case_store_type):
            def __init__(self):
                super().__init__()
                self.batch_lookup_calls = 0
                self.single_case_reads = 0
                self.single_version_reads = 0

            async def get_case(self, case_id):
                self.single_case_reads += 1
                return await super().get_case(case_id)

            async def get_version(self, version_id):
                self.single_version_reads += 1
                return await super().get_version(version_id)

            async def get_active_case_versions(self, case_ids):
                self.batch_lookup_calls += 1
                return await super().get_active_case_versions(case_ids)

        projects = ProjectService(store=InMemoryProjectStore())
        await projects.initialize()
        project = await projects.create(ProjectCreateRequest(project_key="large", name="Large"))
        store = CountingStore()
        cases = case_service_type(store=store, project_service=projects)
        await cases.initialize()
        items = []
        for index in range(200):
            created = await cases.create_draft(
                project_id=project.id,
                payload=schemas.TestCaseDraftCreateRequest(
                    case_key=f"large-case-{index}",
                    title=f"Case {index}",
                    mode_key="api_testing",
                    case_type="api",
                    steps=[{"order": 1, "action": "Run", "expected": "Done"}],
                    assertions=[{"kind": "result", "operator": "equals", "expected": "done"}],
                    source_refs=[{"source_type": "api_doc", "source_id": "doc-1"}],
                    model_key="qa-model",
                    prompt_version="v1",
                    skill_versions={"generate-test-cases": "sha256:test-skill"},
                ),
            )
            await cases.submit_review(created.case.id)
            active = await cases.activate(created.case.id)
            items.append(
                {"case_id": active.id, "case_version_id": active.active_version_id}
            )

        store.single_case_reads = 0
        store.single_version_reads = 0
        suites = suite_service_type(
            store=suite_store_type(),
            project_service=projects,
            test_case_service=cases,
        )
        await suites.initialize()
        created_suite = await suites.create(
            project.id,
            suite_schemas.TestSuiteCreateRequest(name="Large suite", items=items),
        )

        assert len(created_suite.items) == 200
        assert store.batch_lookup_calls == 1
        assert store.single_case_reads == 0
        assert store.single_version_reads == 0

    asyncio.run(scenario())


def test_postgres_suite_store_bulk_inserts_items(monkeypatch):
    suite_store_module = import_module("src.application.test_suites.suite_store")
    suite_schemas = import_module("src.schemas.suite_management")
    settings_type = import_module("src.core.config").Settings

    class FakeCursor:
        def __init__(self):
            self.execute_calls = []
            self.executemany_calls = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, parameters=None):
            self.execute_calls.append((statement, parameters))

        def executemany(self, statement, parameters):
            self.executemany_calls.append((statement, list(parameters)))

    class FakeConnection:
        def __init__(self, cursor):
            self._cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return self._cursor

    cursor = FakeCursor()
    monkeypatch.setattr(
        suite_store_module,
        "postgres_connect",
        lambda settings: FakeConnection(cursor),
    )
    store = suite_store_module.PostgresTestSuiteStore(settings_type())
    now = datetime.now(timezone.utc)
    suite = suite_schemas.TestSuiteRecord(
        id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000002",
        name="Bulk",
        created_at=now,
        updated_at=now,
    )
    items = [
        suite_schemas.TestSuiteItemRecord(
            id=f"00000000-0000-0000-0000-{index:012d}",
            suite_id=suite.id,
            case_id=f"10000000-0000-0000-0000-{index:012d}",
            case_version_id=f"20000000-0000-0000-0000-{index:012d}",
            position=index,
        )
        for index in range(1, 201)
    ]

    stored = store._create_sync(suite, items)

    assert len(stored.items) == 200
    assert len(cursor.execute_calls) == 1
    assert len(cursor.executemany_calls) == 1
    assert len(cursor.executemany_calls[0][1]) == 200
