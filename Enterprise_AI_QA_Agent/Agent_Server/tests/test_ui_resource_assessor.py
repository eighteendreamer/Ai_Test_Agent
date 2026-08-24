"""UIResourceAssessor 三源检索单测（方案 4.2 环节③，P0-8）。

不连 Memgraph / PG：图谱源用 Fake provider，用例库用 Fake service，
memory 用 Fake retrieve_for_turn。验证充分判定三选一、审计明细、
单源故障降级不阻塞。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from src.application.recorder.ui_resource_assessor import UIResourceAssessor


@dataclass
class _Context:
    session_id: str = "session-1"
    trace_id: str = "trace-1"
    context_bundle: dict = field(default_factory=dict)


class FakeMemgraphProvider:
    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.queries: list[tuple[str, dict]] = []

    def initialize(self) -> None:
        if self.error:
            raise self.error

    def execute(self, query: str, parameters: dict | None = None) -> list[dict]:
        if self.error:
            raise self.error
        self.queries.append((query, dict(parameters or {})))
        return self.rows


@dataclass
class _Page:
    items: list
    limit: int = 1
    offset: int = 0
    has_more: bool = False


class FakeCaseService:
    def __init__(self, items: list | None = None, error: Exception | None = None) -> None:
        self.items = items or []
        self.error = error
        self.calls: list[dict] = []

    async def list_cases(self, **kwargs) -> _Page:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return _Page(items=self.items, has_more=len(self.items) > 1)


@dataclass
class _Hit:
    score: float


@dataclass
class _MemoryResult:
    hits: list
    total_docs: int


class FakeMemoryService:
    def __init__(self, hits: list | None = None, total_docs: int = 0, error: Exception | None = None) -> None:
        self.hits = hits or []
        self.total_docs = total_docs
        self.error = error

    async def retrieve_for_turn(self, **kwargs) -> _MemoryResult:
        if self.error:
            raise self.error
        return _MemoryResult(hits=self.hits, total_docs=self.total_docs)


def _assessor(**overrides) -> UIResourceAssessor:
    defaults = dict(
        memgraph_provider=FakeMemgraphProvider(rows=[{"page_count": 0, "element_count": 0, "action_count": 0}]),
        test_case_service=FakeCaseService(),
        memory_runtime_service=FakeMemoryService(),
    )
    defaults.update(overrides)
    return UIResourceAssessor(**defaults)


def test_all_sources_empty_triggers_recording_decision():
    result = asyncio.run(_assessor().assess(
        project_id="proj-1",
        target_url="https://example.com",
        objective="测试登录流程",
        project_scope="example.com",
        context=_Context(),
    ))
    assert result["decision"] == "need_recording"
    assert result["sufficient"] is False
    assert result["reason"].startswith("insufficient:")
    # 审计明细：三源各自命中计数
    assert result["sources"]["graph"]["element_count"] == 0
    assert result["sources"]["cases"]["active_case_count"] == 0
    assert result["sources"]["memory"]["hit_count"] == 0


def test_graph_coverage_sufficient_when_pages_and_elements_meet_threshold():
    assessor = _assessor(
        memgraph_provider=FakeMemgraphProvider(
            rows=[{"page_count": 3, "element_count": 42, "action_count": 5}]
        )
    )

    import asyncio

    result = asyncio.run(assessor.assess(
        project_id="proj-1",
        target_url="https://example.com",
        objective="测试登录流程",
        project_scope="example.com",
        context=_Context(),
    ))
    assert result["decision"] == "task_generation_ready"
    assert result["reason"] == "graph_coverage_sufficient"
    assert result["sources"]["graph"]["action_count"] == 5


def test_graph_elements_below_threshold_not_sufficient():
    assessor = _assessor(
        memgraph_provider=FakeMemgraphProvider(
            rows=[{"page_count": 2, "element_count": 3, "action_count": 0}]
        )
    )

    import asyncio

    result = asyncio.run(assessor.assess(
        project_id="proj-1",
        target_url="https://example.com",
        objective="测试登录流程",
        project_scope="example.com",
        context=_Context(),
    ))
    assert result["decision"] == "need_recording"


def test_active_cases_available_is_sufficient():
    assessor = _assessor(test_case_service=FakeCaseService(items=[object()]))

    import asyncio

    result = asyncio.run(assessor.assess(
        project_id="proj-1",
        target_url="https://example.com",
        objective="测试登录流程",
        project_scope="example.com",
        context=_Context(),
    ))
    assert result["decision"] == "task_generation_ready"
    assert result["reason"] == "cases_available"
    # 用例检索按"已启用固定版本"过滤（status=active）
    assert assessor._test_case_service.calls[0]["status"] == "active"


def test_memory_hits_sufficient_keeps_legacy_threshold():
    # 既有阈值：hit_count >= 3 即充分
    assessor = _assessor(
        memory_runtime_service=FakeMemoryService(hits=[_Hit(0.5), _Hit(0.4), _Hit(0.45)], total_docs=3)
    )

    import asyncio

    result = asyncio.run(assessor.assess(
        project_id="proj-1",
        target_url="https://example.com",
        objective="测试登录流程",
        project_scope="example.com",
        context=_Context(),
    ))
    assert result["decision"] == "task_generation_ready"
    assert result["reason"] == "knowledge_sufficient"
    assert result["memory_hit_count"] == 3


def test_source_failures_degrade_to_zero_with_reason():
    assessor = _assessor(
        memgraph_provider=FakeMemgraphProvider(error=RuntimeError("memgraph down")),
        test_case_service=FakeCaseService(error=KeyError("project missing")),
        memory_runtime_service=FakeMemoryService(error=RuntimeError("vector store down")),
    )

    import asyncio

    result = asyncio.run(assessor.assess(
        project_id="proj-1",
        target_url="https://example.com",
        objective="测试登录流程",
        project_scope="example.com",
        context=_Context(),
    ))
    assert result["decision"] == "need_recording"
    assert result["sources"]["graph"]["status"] == "unavailable"
    assert "memgraph down" in result["sources"]["graph"]["reason"]
    assert result["sources"]["cases"]["status"] == "unavailable"
    assert result["sources"]["memory"]["status"] == "unavailable"


def test_missing_providers_reported_unavailable_not_crash():
    import asyncio

    result = asyncio.run(UIResourceAssessor().assess(
        project_id="proj-1",
        target_url="https://example.com",
        objective="测试登录流程",
        project_scope="example.com",
        context=_Context(),
    ))
    assert result["decision"] == "need_recording"
    assert result["sources"]["graph"]["status"] == "unavailable"
    assert result["sources"]["cases"]["status"] == "unavailable"
    assert result["sources"]["memory"]["status"] == "unavailable"
