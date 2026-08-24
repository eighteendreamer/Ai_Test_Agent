"""RecordingGraphStore 测试。

- 不连库单测：指纹去重 / alias 重映射 / 页面归一化 / 对账告警 / 脱敏 / Cypher 形状 / 删除范围；
- 连库集成：RUN_LIVE_RECORDING_MEMGRAPH=1 时运行，验证同一元素 10 次操作收敛
  1 节点、Action 流水不去重、finalize 幂等、delete 保留 Page/Element。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import pytest

from src.application.exploration.recording_graph_store import RecordingGraphStore
from src.schemas.recording import RecorderEvent, RecordingSession


class _FakeProvider:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    def initialize(self) -> None:
        return None

    def execute(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((query, parameters))
        return []

    def execute_write(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((query, parameters))
        return []


def _store(provider: _FakeProvider | None = None) -> RecordingGraphStore:
    store = object.__new__(RecordingGraphStore)
    store._settings = None  # type: ignore[assignment]
    store._provider = provider or _FakeProvider()
    return store


def _session(**overrides: Any) -> RecordingSession:
    defaults: dict[str, Any] = {
        "id": "rec-1",
        "project_id": "proj-1",
        "name": "登录流程录制",
        "entry_url": "https://app.example.com/login",
        "started_at": datetime(2026, 8, 24, 10, 0, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 8, 24, 10, 3, 0, tzinfo=timezone.utc),
        "step_count": 0,
    }
    defaults.update(overrides)
    return RecordingSession(**defaults)


def _click_target(name: str = "登 录") -> dict[str, Any]:
    return {
        "tag": "BUTTON",
        "role": "button",
        "locators": {
            "id": "login-submit",
            "testid": None,
            "role_name": {"role": "button", "name": name},
            "css": "form.login > button.primary",
            "xpath": "/html/body/div[1]/form/button[1]",
            "text": name,
        },
        "attributes": {"type": "submit", "class": "primary"},
    }


def _event(seq: int, **overrides: Any) -> RecorderEvent:
    defaults: dict[str, Any] = {
        "seq": seq,
        "type": "click",
        "page": {"url": "https://app.example.com/login", "title": "登录"},
        "target": _click_target(),
        "timestamp": datetime(2026, 8, 24, 10, 0, seq, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    for key in ("target",):
        if defaults.get(key) is None:
            defaults.pop(key)
    return RecorderEvent(**defaults)


# ---------------------------------------------------------------- 单测：prepare


def test_prepare_graph_converges_repeated_element_operations() -> None:
    store = _store()
    events = [_event(i) for i in range(10)]
    prepared = store._prepare_graph(_session(step_count=10), events)

    assert len(prepared["elements"]) == 1
    assert len(prepared["actions"]) == 10
    element_id = prepared["elements"][0]["id"]
    # alias 重映射：10 个事件的元素候选（rec-1:seq）全部收敛到同一内容寻址 id
    assert all(action["element_id"] == element_id for action in prepared["actions"])
    assert prepared["elements"][0]["merged_duplicate_count"] == 9
    assert len(prepared["contains_pairs"]) == 1
    metrics = prepared["metrics"]
    assert metrics["raw_element_refs"] == 10
    assert metrics["deduplicated_element_refs"] == 9


def test_prepare_graph_fingerprint_separates_pages_and_names() -> None:
    store = _store()
    events = [
        _event(0, page={"url": "https://app.example.com/login", "title": "登录"}),
        _event(1, page={"url": "https://app.example.com/home", "title": "首页"}),
        _event(2, target=_click_target("注 册")),
    ]
    prepared = store._prepare_graph(_session(), events)
    assert len(prepared["elements"]) == 3
    assert len(prepared["contains_pairs"]) == 3


def test_prepare_graph_normalizes_page_urls() -> None:
    store = _store()
    events = [
        _event(0, page={"url": "https://app.example.com/list", "title": "列表"}),
        _event(1, page={"url": "https://app.example.com/list/", "title": ""}),
        _event(2, page={"url": "https://app.example.com/list#section-2", "title": ""}),
    ]
    prepared = store._prepare_graph(_session(), events)
    assert len(prepared["pages"]) == 1
    assert prepared["pages"][0]["url"] == "https://app.example.com/list"
    assert prepared["pages"][0]["title"] == "列表"


def test_prepare_graph_navigate_event_builds_edges() -> None:
    store = _store()
    events = [
        _event(0),
        _event(
            1,
            type="navigate",
            target=None,
            page={"url": "https://app.example.com/home", "title": "首页"},
            page_effect={"navigated_to": "https://app.example.com/home"},
        ),
    ]
    prepared = store._prepare_graph(_session(), events)
    navigate_action = prepared["actions"][1]
    assert navigate_action["element_id"] == ""
    assert navigate_action["navigated_to_url"] == "https://app.example.com/home"
    assert navigate_action["navigated_to_page_id"]
    page_ids = {page["id"] for page in prepared["pages"]}
    assert navigate_action["navigated_to_page_id"] in page_ids


# --------------------------------------------------------------- 单测：对账


def test_check_integrity_flags_seq_gaps_and_step_mismatch() -> None:
    store = _store()
    events = [_event(0), _event(1), _event(5)]
    prepared = store._prepare_graph(_session(), events)
    integrity = store._check_integrity(_session(step_count=2), events, prepared)
    assert integrity["pg_event_count"] == 3
    assert integrity["step_count_mismatch"] is True
    assert integrity["seq_gaps"] == [{"from": 2, "to": 4, "count": 3}]
    assert integrity["degraded"] is True


def test_check_integrity_passes_on_contiguous_stream() -> None:
    store = _store()
    events = [_event(i) for i in range(5)]
    prepared = store._prepare_graph(_session(step_count=5), events)
    integrity = store._check_integrity(_session(step_count=5), events, prepared)
    assert integrity["seq_gaps"] == []
    assert integrity["degraded"] is False
    assert integrity["step_count_mismatch"] is False


# ------------------------------------------------------------ 单测：finalize


def test_finalize_sync_writes_subgraph_shapes() -> None:
    provider = _FakeProvider()
    store = _store(provider)
    events = [_event(i) for i in range(3)]
    result = asyncio.get_event_loop().run_until_complete(
        store.finalize(_session(step_count=3), events)
    )

    assert result["status"] == "success"
    assert result["integrity"]["degraded"] is False
    assert result["integrity"]["reconciled"] is True
    metrics = result["metrics"]
    assert metrics["recording_vertices"] == 1
    assert metrics["page_vertices"] == 1
    assert metrics["element_vertices"] == 1
    assert metrics["action_vertices"] == 3
    assert metrics["has_step_edges"] == 3
    assert metrics["on_page_edges"] == 3
    assert metrics["targets_edges"] == 3
    assert metrics["contains_edges"] == 1

    def count(fragment: str) -> int:
        return sum(1 for query, _ in provider.queries if fragment in query)

    assert count("MERGE (n:Recording") == 1
    assert count("MERGE (n:Page") == 1
    assert count("MERGE (n:Element") == 1
    assert count("MERGE (n:Action") == 3
    assert count("HAS_STEP") == 3
    assert count("ON_PAGE") == 3
    assert count("TARGETS") == 3
    assert count("CONTAINS") == 1


def test_finalize_sync_masks_password_value_defensively() -> None:
    provider = _FakeProvider()
    store = _store(provider)
    event = _event(
        0,
        target={
            "tag": "INPUT",
            "role": "textbox",
            "locators": {"id": "pwd"},
            "attributes": {"type": "password"},
        },
        value="secret123",
    )
    result = asyncio.get_event_loop().run_until_complete(store.finalize(_session(), [event]))
    assert result["status"] == "success"

    action_writes = [params for query, params in provider.queries if "MERGE (n:Action" in query]
    assert len(action_writes) == 1
    props = action_writes[0]["props"]
    assert props["value_masked"] == "masked"
    assert props["value_length"] == 9
    assert "secret123" not in props["payload_json"]


def test_finalize_sync_marks_degraded_when_locators_empty() -> None:
    provider = _FakeProvider()
    store = _store(provider)
    event = _event(0, target={"tag": "DIV", "role": "", "locators": {}, "attributes": {}})
    result = asyncio.get_event_loop().run_until_complete(store.finalize(_session(), [event]))
    assert result["integrity"]["degraded"] is True
    assert result["integrity"]["degraded_resolution_actions"] == 1
    action_writes = [params for query, params in provider.queries if "MERGE (n:Action" in query]
    assert action_writes[0]["props"]["resolution_status"] == "degraded"


def test_finalize_sync_blocked_without_project_id() -> None:
    provider = _FakeProvider()
    store = _store(provider)
    result = asyncio.get_event_loop().run_until_complete(
        store.finalize(_session(project_id="  "), [_event(0)])
    )
    assert result == {"status": "blocked", "reason": "project_id_required"}
    assert provider.queries == []


def test_delete_recording_scopes_to_recording_subgraph() -> None:
    class _CountingProvider(_FakeProvider):
        def execute_write(self, query, parameters=None):  # noqa: ANN001
            self.queries.append((query, parameters))
            if "RETURN count(a)" in query:
                return [{"deleted": 4}]
            return []

    provider = _CountingProvider()
    store = _store(provider)
    result = asyncio.get_event_loop().run_until_complete(
        store.delete_recording("rec-1", project_id="proj-1")
    )
    assert result["status"] == "success"
    assert result["deleted_action_vertices"] == 4
    delete_queries = [query for query, _ in provider.queries if "DETACH DELETE" in query]
    assert len(delete_queries) == 2
    assert any("Recording" in query for query in delete_queries)
    assert any("Action" in query for query in delete_queries)
    assert all("Page" not in query and "Element" not in query for query in delete_queries)


# ------------------------------------------------- 连库集成（RUN_LIVE_RECORDING_MEMGRAPH=1）

LIVE_MEMGRAPH = os.getenv("RUN_LIVE_RECORDING_MEMGRAPH") == "1"
live_memgraph = pytest.mark.skipif(
    not LIVE_MEMGRAPH,
    reason="set RUN_LIVE_RECORDING_MEMGRAPH=1 to run the live Memgraph recording graph tests",
)


@live_memgraph
def test_live_finalize_converges_element_and_keeps_action_stream() -> None:
    from src.core.config import Settings

    settings = Settings()
    store = RecordingGraphStore(settings)
    project_id = f"proj-rec-live-{os.urandom(4).hex()}"
    session = _session(
        id=f"rec-live-{os.urandom(4).hex()}",
        project_id=project_id,
        step_count=11,
    )
    events = [_event(i) for i in range(10)] + [
        _event(
            10,
            type="navigate",
            target=None,
            page={"url": "https://app.example.com/home", "title": "首页"},
            page_effect={"navigated_to": "https://app.example.com/home"},
        )
    ]
    try:
        result = asyncio.get_event_loop().run_until_complete(store.finalize(session, events))
        assert result["status"] == "success"
        metrics = result["metrics"]
        assert metrics["element_vertices"] == 1
        assert metrics["action_vertices"] == 11
        assert metrics["deduplicated_element_refs"] == 9
        assert result["integrity"]["reconciled"] is True

        # 幂等：重复 finalize 不产生重复节点/边
        result_again = asyncio.get_event_loop().run_until_complete(store.finalize(session, events))
        assert result_again["metrics"] == metrics

        def count(cypher: str) -> int:
            rows = store._provider.execute(cypher, {"project_id": project_id, "recording_id": session.id})
            return int(rows[0]["cnt"])

        assert count("MATCH (a:Action {project_id: $project_id, recording_id: $recording_id}) RETURN count(a) AS cnt") == 11
        assert count("MATCH (e:Element {project_id: $project_id}) RETURN count(e) AS cnt") == 1
        assert count("MATCH (r:Recording {project_id: $project_id, id: $recording_id}) RETURN count(r) AS cnt") == 1

        delete_result = asyncio.get_event_loop().run_until_complete(
            store.delete_recording(session.id, project_id=project_id)
        )
        assert delete_result["deleted_action_vertices"] == 11
        assert count("MATCH (a:Action {project_id: $project_id}) RETURN count(a) AS cnt") == 0
        assert count("MATCH (r:Recording {project_id: $project_id}) RETURN count(r) AS cnt") == 0
        # Page/Element 保留
        assert count("MATCH (e:Element {project_id: $project_id}) RETURN count(e) AS cnt") == 1
        assert count("MATCH (p:Page {project_id: $project_id}) RETURN count(p) AS cnt") == 2
    finally:
        store._provider.execute_write(
            "MATCH (n {project_id: $project_id}) DETACH DELETE n",
            {"project_id": project_id},
        )
