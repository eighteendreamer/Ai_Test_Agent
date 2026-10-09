"""Regression contracts derived from the actual missing-step capture."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from test_knowledge_graph_service import _FakeProvider, _service, _FakeProjects

DOCS = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


def captured():
    return json.loads((DOCS / "p5-recording-knowledge-verified/formal-id/pg-recording-detail.json").read_text(encoding="utf-8"))


def test_formal_identity_without_legacy_mapping_filters_only_its_id():
    service = _service(_FakeProvider([]))
    service._projects = _FakeProjects(SimpleNamespace(id="formal", name="项目名称", graph_scope_key=None, status="active"))
    identity = asyncio.run(service._resolve_identity("formal", require_active=True))
    assert identity == {"project_id": "formal", "project_scope": "", "project_name": "项目名称"}
    assert service._project_filter("n", identity) == "n.project_id = $project_id"


def test_each_actual_action_metadata_remains_complete_after_reading():
    service = _service(_FakeProvider([]))
    original = captured()
    for event in original["events"]:
        record = {"id": original["id"] + ":" + str(event["seq"]), "label": event["type"] + " #" + str(event["seq"]),
            "action_type": event["type"], "payload_json": json.dumps(event, ensure_ascii=False)}
        node = service._node_from_record(record, "action")
        assert node.metadata == event
        assert node.kind == "action" and node.summary == event["type"]
    assert captured() == original


def test_historical_password_read_masks_the_value_without_rewriting_raw_projection():
    service = _service(_FakeProvider([]))
    event = copy.deepcopy(next(e for e in captured()["events"] if e["type"] == "fill"))
    event["target"].setdefault("attributes", {})["type"] = "password"
    record = {"id": "historical-action", "payload_json": json.dumps(event, ensure_ascii=False)}
    original = record["payload_json"]
    node = service._node_from_record(record, "action")
    assert node.metadata == {**event, "value": {"length": len(event["value"])}}
    assert record["payload_json"] == original


def test_corrupt_action_payload_is_unavailable_and_logs_only_identity(caplog):
    service = _service(_FakeProvider([]))
    node = service._node_from_record({"id": "corrupt-action", "payload_json": '{"value":"owned-sensitive-sentinel"}'}, "action")
    assert node.metadata == {}
    assert "knowledge_recording_event_unavailable" in caplog.text
    assert "owned-sensitive-sentinel" not in caplog.text


def test_historical_summary_defaults_keep_old_three_kind_shape_readable():
    from src.schemas.knowledge import KnowledgeProjectSummary, KnowledgeGraphSummary
    for schema in (KnowledgeProjectSummary, KnowledgeGraphSummary):
        summary = schema(project_scope="legacy", page_count=1, element_count=2, entity_count=3)
        assert summary.recording_count == summary.action_count == 0 and summary.project_name is None
