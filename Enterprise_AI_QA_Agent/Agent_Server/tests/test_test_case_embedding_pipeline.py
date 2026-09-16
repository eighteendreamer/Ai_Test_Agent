from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.application.context.embedding_runtime_service import EmbeddingBatchResult
from src.application.test_cases.embedding_service import (
    TestCaseEmbeddingOutcome as EmbeddingOutcome,
    TestCaseEmbeddingService as EmbeddingService,
)
from src.application.test_cases.case_service import TestCaseService as CaseService
from src.core.request_context import bind_request_context
from src.infrastructure.postgres_test_case_embedding_store import (
    PostgresTestCaseEmbeddingStore,
    TestCaseVectorHit as VectorHit,
)
from src.application.test_cases.case_store import (
    PostgresTestCaseStore as PostgresCaseStore,
)
from src.core.config import Settings
from src.infrastructure.redis_task_queue import QueuedTask
from src.graph.nodes.router import build_router_node
from src.registry.agents import AgentRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.schemas.agent import ModelDescriptor
from src.runtime.embedding_task_handler import EmbeddingTaskHandler
from src.runtime.postgres_embedding_job_store import EmbeddingJob
from src.schemas.case_management import (
    TestCaseRecord as CaseRecord,
    TestCaseVersionRecord as CaseVersionRecord,
)
from src.schemas.embedding_task import TestCaseEmbeddingTask as EmbeddingTask


NOW = datetime.now(timezone.utc)


def _case(*, active_version_id: str = "version-1") -> CaseRecord:
    return CaseRecord(
        id="case-1",
        project_id="project-1",
        case_key="login-valid",
        title="Valid login",
        mode_key="api_testing",
        case_type="api",
        lifecycle_status="active",
        active_version_id=active_version_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _version() -> CaseVersionRecord:
    return CaseVersionRecord(
        id="version-1",
        case_id="case-1",
        version=1,
        steps=[{"order": 1, "action": "Submit credentials", "expected": "200"}],
        assertions=[{"kind": "status_code", "expected": 200}],
        test_data={"environment": "qa", "password": "must-not-leak"},
        content_hash="a" * 64,
        source_refs=[{"source_type": "api_doc", "source_id": "doc-1"}],
        model_key="generator-1",
        prompt_version="v1",
        skill_versions={"generate": "v1"},
        created_at=NOW,
    )


def _task_payload(**updates):
    payload = {
        "task_id": "embedding_test_case_version-1",
        "source_id": "version-1",
        "source_version": "1",
        "content_hash": "a" * 64,
        "request_id": "req-1",
        "trace_id": "trace-1",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "project_id": "project-1",
        "run_id": "run-1",
        "run_item_id": "item-1",
        "attempt_id": "attempt-1",
        "worker_id": None,
        "resource_id": "browser-1",
        "case_id": "case-1",
        "case_version_id": "version-1",
    }
    payload.update(updates)
    return payload


def test_embedding_task_contract_preserves_harness_identifiers() -> None:
    task = EmbeddingTask.model_validate(_task_payload())

    assert task.task_type == "embedding_task"
    assert task.source_type == "test_case_version"
    assert task.model_dump(mode="json")["resource_id"] == "browser-1"
    with pytest.raises(ValidationError):
        EmbeddingTask.model_validate({**_task_payload(), "unknown": True})


@pytest.mark.asyncio
async def test_activation_writes_embedding_job_and_outbox_with_request_context() -> None:
    pending = _case().model_copy(
        update={"lifecycle_status": "pending_review", "active_version_id": None}
    )
    version = _version()

    async def activate_with_embedding_outbox(case, **kwargs):
        assert kwargs["embedding_job_table"] == "embedding_jobs"
        assert kwargs["outbox_table"] == "task_outbox"
        assert kwargs["stream"] == "qa:tasks:embedding"
        task = EmbeddingTask.model_validate(kwargs["task_payload"])
        assert task.request_id == "req-activation"
        assert task.trace_id == "trace-activation"
        assert task.session_id == "session-activation"
        assert task.turn_id == "turn-activation"
        assert task.task_id == f"embedding_test_case_{version.id}"
        return case

    store = SimpleNamespace(
        get_case=AsyncMock(return_value=pending),
        list_versions=AsyncMock(return_value=[version]),
        activate_with_embedding_outbox=AsyncMock(
            side_effect=activate_with_embedding_outbox
        ),
    )
    service = CaseService(
        store=store,
        project_service=SimpleNamespace(require_active=AsyncMock()),
    )
    service.set_embedding_task_outbox(
        embedding_job_table="embedding_jobs",
        outbox_table="task_outbox",
        stream="qa:tasks:embedding",
    )

    with bind_request_context(
        request_id="req-activation",
        trace_id="trace-activation",
        session_id="session-activation",
        turn_id="turn-activation",
    ):
        activated = await service.activate(pending.id)

    assert activated.lifecycle_status == "active"
    assert activated.active_version_id == version.id
    store.activate_with_embedding_outbox.assert_awaited_once()


def test_postgres_activation_updates_case_job_and_outbox_in_one_transaction(
    monkeypatch,
) -> None:
    statements: list[tuple[str, object]] = []

    class Cursor:
        rowcount = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, parameters=None):
            statements.append((statement, parameters))
            self.rowcount = 1 if "INSERT INTO embedding_jobs" in statement or "INSERT INTO task_outbox" in statement else 0

        def fetchone(self):
            return {"lifecycle_status": "pending_review"}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(
        "src.application.test_cases.case_store.postgres_connect",
        lambda settings: Connection(),
    )
    store = PostgresCaseStore(Settings())
    active = _case()
    task = EmbeddingTask.model_validate(_task_payload())

    result = store._activate_with_embedding_outbox_sync(
        active,
        {"pending_review"},
        "embedding_jobs",
        "task_outbox",
        "qa:tasks:embedding",
        task.model_dump(mode="json"),
    )

    assert result == active
    assert len(statements) == 4
    assert "UPDATE" in statements[1][0]
    assert "INSERT INTO embedding_jobs" in statements[2][0]
    assert "INSERT INTO task_outbox" in statements[3][0]


@pytest.mark.asyncio
async def test_active_version_is_persisted_without_embedding_secret_values() -> None:
    case = _case()
    version = _version()
    cases = SimpleNamespace(
        get_case=AsyncMock(return_value=case),
        get_version=AsyncMock(return_value=version),
    )
    runtime = SimpleNamespace(
        embed_texts=AsyncMock(
            return_value=EmbeddingBatchResult(
                vectors=[[1.0, 0.0, 0.0]],
                model_name="Embedding",
                provider="openai_compatible",
                adapter="openai",
                original_dimension=3,
                stored_dimension=3,
                latency_ms=1,
                embedding_version="emb_v1",
                model_key="embedding-key",
                model_id="embedding-id",
            )
        )
    )
    store = SimpleNamespace(initialize=AsyncMock(), upsert=AsyncMock())
    redis = SimpleNamespace(
        create_index=AsyncMock(),
        upsert=AsyncMock(return_value="redis-key"),
    )
    service = EmbeddingService(
        case_store=cases,
        embedding_store=store,
        embedding_runtime=runtime,
        redis_vector_store=redis,
    )

    outcome = await service.index_active_version(
        case_id=case.id,
        case_version_id=version.id,
        expected_content_hash=version.content_hash,
    )

    embedded_text = runtime.embed_texts.await_args.args[0][0]
    assert "must-not-leak" not in embedded_text
    assert '"password"' in embedded_text
    write = store.upsert.await_args.args[0]
    assert write.embedding == [1.0, 0.0, 0.0]
    assert write.embedding_dimension == 3
    assert write.embedding_model_key == "embedding-key"
    assert outcome.status == "completed" and outcome.redis_key == "redis-key"


@pytest.mark.asyncio
async def test_superseded_version_finishes_without_provider_call() -> None:
    runtime = SimpleNamespace(embed_texts=AsyncMock())
    service = EmbeddingService(
        case_store=SimpleNamespace(
            get_case=AsyncMock(return_value=_case(active_version_id="version-2")),
            get_version=AsyncMock(return_value=_version()),
        ),
        embedding_store=SimpleNamespace(upsert=AsyncMock()),
        embedding_runtime=runtime,
    )

    outcome = await service.index_active_version(
        case_id="case-1",
        case_version_id="version-1",
        expected_content_hash="a" * 64,
    )

    assert outcome.status == "superseded"
    runtime.embed_texts.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_candidates_are_hydrated_by_postgres() -> None:
    hit = VectorHit(
        case=_case(),
        version=_version(),
        score=0.99,
        embedding_version="emb_v1",
        embedding_dimension=3,
        environment="qa",
    )
    store = SimpleNamespace(
        search_candidates=AsyncMock(return_value=[hit]),
        search=AsyncMock(return_value=[]),
    )
    redis = SimpleNamespace(
        get_active_version=AsyncMock(return_value="emb_v1"),
        search=AsyncMock(
            return_value=[
                {"id": "qa:vector:doc:test_case:emb_v1:version-1"},
                {"id": "qa:vector:doc:test_case:emb_v1:foreign-version"},
            ]
        ),
        prefix=lambda entity, version: f"qa:vector:doc:{entity}:{version}:",
    )
    runtime = SimpleNamespace(
        embed_texts=AsyncMock(
            return_value=EmbeddingBatchResult(
                vectors=[[1.0, 0.0, 0.0]],
                model_name="Embedding",
                provider="provider",
                adapter="adapter",
                original_dimension=3,
                stored_dimension=3,
                latency_ms=1,
                embedding_version="emb_v1",
            )
        )
    )
    service = EmbeddingService(
        case_store=SimpleNamespace(),
        embedding_store=store,
        embedding_runtime=runtime,
        redis_vector_store=redis,
    )

    result = await service.retrieve(
        project_id="project-1",
        query="login",
        environment="qa",
        mode_key="api_testing",
        top_k=1,
    )

    assert result.hits == [hit]
    assert store.search_candidates.await_args.kwargs["case_version_ids"] == [
        "version-1",
        "foreign-version",
    ]
    store.search.assert_not_awaited()


def _job(status: str) -> EmbeddingJob:
    return EmbeddingJob(
        task_id="embedding_test_case_version-1",
        source_type="test_case_version",
        source_id="version-1",
        source_version="1",
        project_id="project-1",
        case_id="case-1",
        case_version_id="version-1",
        content_hash="a" * 64,
        status=status,
        embedding_version=None,
        embedding_dimension=None,
        redis_key=None,
        attempts=1,
        worker_id=None,
        lease_expires_at=None,
        last_error_type=None,
        created_at=None,
        updated_at=None,
        completed_at=None,
    )


@pytest.mark.asyncio
async def test_embedding_worker_persists_progress_before_terminal_ack() -> None:
    async def index_active_version(**kwargs):
        await kwargs["progress"](
            "persisted",
            {"embedding_version": "emb_v1", "dimension": 4096},
        )
        return EmbeddingOutcome(
            status="completed",
            case_id="case-1",
            case_version_id="version-1",
            embedding_version="emb_v1",
            embedding_dimension=4096,
            redis_key="redis-key",
        )

    service = SimpleNamespace(
        index_active_version=AsyncMock(side_effect=index_active_version)
    )
    jobs = SimpleNamespace(
        begin=AsyncMock(return_value=True),
        get=AsyncMock(),
        progress=AsyncMock(return_value=True),
        complete=AsyncMock(return_value=True),
        fail=AsyncMock(return_value=True),
    )
    handler = EmbeddingTaskHandler(service, jobs, worker_id="worker-1")

    await handler(QueuedTask("1-0", _task_payload()))

    assert jobs.progress.await_args.kwargs["embedding_dimension"] == 4096
    assert jobs.complete.await_args.kwargs["status"] == "completed"
    jobs.fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_embedding_worker_records_provider_failure_for_retry() -> None:
    service = SimpleNamespace(
        index_active_version=AsyncMock(side_effect=ConnectionError("private"))
    )
    jobs = SimpleNamespace(
        begin=AsyncMock(return_value=True),
        get=AsyncMock(),
        progress=AsyncMock(return_value=True),
        complete=AsyncMock(return_value=True),
        fail=AsyncMock(return_value=True),
    )
    handler = EmbeddingTaskHandler(service, jobs, worker_id="worker-2")

    with pytest.raises(ConnectionError):
        await handler(QueuedTask("1-0", _task_payload()))

    assert jobs.fail.await_args.kwargs["error_type"] == "ConnectionError"
    jobs.complete.assert_not_awaited()


def test_pgvector_ann_strategy_preserves_complete_4096_dimension_vectors() -> None:
    assert PostgresTestCaseEmbeddingStore._index_strategy(1536) == "vector"
    assert PostgresTestCaseEmbeddingStore._index_strategy(3000) == "halfvec"
    assert PostgresTestCaseEmbeddingStore._index_strategy(4096) == "binary"
    expression = PostgresTestCaseEmbeddingStore._candidate_order_expression(4096)
    assert "binary_quantize" in expression
    assert "bit(4096)" in expression
    assert PostgresTestCaseEmbeddingStore._validate_vector(
        [1.0, *([0.0] * 4095)],
        expected_dimension=4096,
    ) == 4096


@pytest.mark.asyncio
async def test_router_injects_project_scoped_test_cases_as_untrusted_context() -> None:
    retrieval = SimpleNamespace(
        hits=[
            SimpleNamespace(
                case=_case(),
                version=_version(),
                score=0.9,
                embedding_version="emb_v1",
            )
        ],
        prompt_blocks=["active test case evidence"],
    )
    test_cases = SimpleNamespace(retrieve=AsyncMock(return_value=retrieval))

    class Models:
        def resolve_for_agent(self, requested_key, supported_model_keys):
            return ModelDescriptor(
                key="fake-model",
                name="Fake",
                provider="fake",
                summary="Fake",
            )

    class Mcp:
        def list_active_servers(self):
            return []

        def build_prompt_blocks(self, active_servers):
            return []

    skills = SkillRegistry()
    router = build_router_node(
        AgentRegistry(),
        ToolRegistry(),
        Models(),
        skills,
        SkillRuntimeService(skills),
        Mcp(),
        None,
        test_cases,
    )
    state = {
        "session_id": "session-1",
        "turn_id": "turn-1",
        "trace_id": "trace-1",
        "user_message": "run login tests",
        "normalized_input": "run login tests",
        "mode_key": "api_testing",
        "preferred_model": "",
        "selected_agent_key": "api-testing-agent",
        "selected_model_key": "",
        "requested_skill_keys": [],
        "observation_prompt_blocks": [],
        "context_bundle": {
            "project_id": "project-1",
            "environment": "qa",
            "selected_mode": {},
        },
        "event_log": [],
    }

    routed = await router(state)

    assert routed["test_case_hits"][0]["case_version_id"] == "version-1"
    assert routed["test_case_prompt_blocks"] == ["active test case evidence"]
    prompt = PromptAssemblyService().build_for_turn(routed, [])
    section = next(item for item in prompt.system_sections if item.key == "active_test_cases")
    assert section.metadata["trusted"] is False
    assert "active test case evidence" in section.content
    assert test_cases.retrieve.await_args.kwargs == {
        "project_id": "project-1",
        "query": "run login tests",
        "environment": "qa",
        "mode_key": "api_testing",
    }
