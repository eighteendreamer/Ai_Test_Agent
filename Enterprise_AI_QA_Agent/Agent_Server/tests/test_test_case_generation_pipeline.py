from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace

import pytest

from src.schemas.api_docs import ApiDocRecord
from src.schemas.project import ProjectRecord
from src.schemas.case_management import TestCaseGenerateRequest as GenerateRequest


class _ApiDocs:
    def __init__(self, project_id: str):
        now = datetime.now(timezone.utc)
        self.records = [
            ApiDocRecord(
                id="doc-orders",
                title="Orders API",
                filename="orders.json",
                project_id=project_id,
                storage_uri="memory://orders.json",
                uploaded_at=now,
                updated_at=now,
            )
        ]
        self.list_calls = []

    async def list_documents(self, **kwargs):
        self.list_calls.append(kwargs)
        return self.records

    async def read_document_content(self, doc_id: str, *, max_chars: int):
        return {"content": "POST /orders\nGET /orders/{id}", "full_length": 33, "truncated": False}


class _Graph:
    async def get_generation_context(self, project_scope: str, *, node_limit: int, edge_limit: int):
        return {
            "project_scope": project_scope,
            "summary": {"page_count": 1, "element_count": 2, "entity_count": 1, "edge_count": 2},
            "nodes": [{"id": "orders-page", "kind": "page", "label": "Orders"}],
            "edges": [{"source_id": "orders-page", "target_id": "create-button", "relation": "CONTAINS"}],
            "latest_updated_at": "2026-08-17T00:00:00Z",
        }


class _Sessions:
    async def list_project_history_context(self, project_id: str, *, limit: int):
        return [
            {
                "session_id": "session-failed",
                "title": "Order regression",
                "mode_key": "api_testing",
                "status": "failed",
                "updated_at": "2026-08-17T01:00:00Z",
                "failure_summary": "duplicate idempotency key accepted",
            }
        ]


class _SkillRuntime:
    def __init__(self):
        self.calls = []

    def build_prompt_blocks(self, skill_keys, *, include_content=False):
        self.calls.append((skill_keys, include_content))
        return ["generate-test-cases skill instructions"]


class _ModelRuntime:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def get_default_model_config(self):
        return SimpleNamespace(key="qa-default")

    async def invoke(self, model_key, request):
        self.calls.append((model_key, request))
        return SimpleNamespace(text=self.text)


def _pipeline_components():
    try:
        module = import_module("src.application.test_cases.generation_pipeline")
    except ModuleNotFoundError as exc:
        pytest.fail(f"test case generation pipeline is not implemented: {exc}")
    return module.ProjectTestCaseContextProvider, module.ModelTestCaseGenerator


def _project() -> ProjectRecord:
    now = datetime.now(timezone.utc)
    return ProjectRecord(
        id="2a6a8a26-060d-41f5-a1d4-da4b0c995ac0",
        project_key="orders",
        name="Orders",
        graph_scope_key="orders-graph",
        created_at=now,
        updated_at=now,
    )


def test_context_provider_reads_only_formally_bound_project_resources():
    async def scenario():
        context_type, _ = _pipeline_components()
        project = _project()
        api_docs = _ApiDocs(project.id)
        provider = context_type(
            api_docs_service=api_docs,
            knowledge_graph_service=_Graph(),
            session_store=_Sessions(),
        )

        context = await provider.collect(
            project=project,
            request=GenerateRequest(
                objective="Order API regression",
                mode_key="api_testing",
                api_doc_ids=["doc-orders"],
            ),
        )

        assert api_docs.list_calls == [{"project_id": project.id}]
        assert context["api_documents"][0]["id"] == "doc-orders"
        assert context["knowledge_graph"]["project_scope"] == "orders-graph"
        assert context["history"][0]["session_id"] == "session-failed"
        assert {item["source_type"] for item in context["source_refs"]} == {
            "api_doc",
            "knowledge_graph",
            "session_history",
        }

    asyncio.run(scenario())


def test_context_provider_rejects_requested_document_outside_project():
    async def scenario():
        context_type, _ = _pipeline_components()
        project = _project()
        provider = context_type(
            api_docs_service=_ApiDocs(project.id),
            knowledge_graph_service=_Graph(),
            session_store=_Sessions(),
        )

        with pytest.raises(ValueError, match="not bound to project"):
            await provider.collect(
                project=project,
                request=GenerateRequest(
                    objective="Cross-project read",
                    mode_key="api_testing",
                    api_doc_ids=["doc-other-project"],
                ),
            )

    asyncio.run(scenario())


def test_model_generator_loads_skill_and_returns_validated_cases_without_fallback():
    async def scenario():
        _, generator_type = _pipeline_components()
        model = _ModelRuntime(
            """{
              "cases": [{
                "case_key": "orders-idempotency",
                "title": "重复幂等键只创建一个订单",
                "case_type": "api",
                "priority": "P0",
                "preconditions": ["订单 API 可访问"],
                "steps": [{"order": 1, "action": "重复提交相同幂等键", "expected": "仅创建一个订单"}],
                "assertions": [{"kind": "order_count", "operator": "equals", "expected": 1}],
                "test_data": {"idempotency_key": "fixed-test-key"},
                "cleanup": ["删除测试订单"]
              }]
            }"""
        )
        skills = _SkillRuntime()
        generator = generator_type(model_runtime_service=model, skill_runtime_service=skills)
        context = {
            "project": _project().model_dump(mode="json"),
            "api_documents": [{"id": "doc-orders", "content": "POST /orders"}],
            "knowledge_graph": {},
            "history": [],
            "source_refs": [{"source_type": "api_doc", "source_id": "doc-orders"}],
        }

        batch = await generator.generate(
            request=GenerateRequest(
                objective="Order idempotency",
                mode_key="api_testing",
            ),
            context=context,
        )

        assert batch.model_key == "qa-default"
        assert batch.prompt_version == "test-case-generation-v1"
        assert batch.cases[0].case_key == "orders-idempotency"
        assert skills.calls == [(["generate-test-cases"], True)]
        assert model.calls[0][0] == "qa-default"
        assert "Orders" in model.calls[0][1].messages[0]["content"]
        assert batch.skill_versions["generate-test-cases"].startswith("sha256:")

        invalid = generator_type(
            model_runtime_service=_ModelRuntime("not-json"),
            skill_runtime_service=skills,
        )
        with pytest.raises(ValueError, match="valid JSON"):
            await invalid.generate(
                request=GenerateRequest(
                    objective="Do not fabricate fallback cases",
                    mode_key="api_testing",
                ),
                context=context,
            )

    asyncio.run(scenario())
