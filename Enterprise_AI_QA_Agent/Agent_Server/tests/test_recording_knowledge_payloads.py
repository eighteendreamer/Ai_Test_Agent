"""Replay actual recorded steps; partial/corrupt projections must stay explicit."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from test_recording_graph_store import _FakeProvider, _store
from test_case_planning import SequenceModel, environment, active_case, item, finish
from src.schemas.project import ProjectCreateRequest

DOCS = Path(__file__).resolve().parents[2] / "docs" / "upgrade_2026-10-03"


def actual_events():
    return json.loads((DOCS / "p0-recording-knowledge-reproduction-python" / "cdp-attach" /
        "nested-frame" / "pg-events.json").read_text(encoding="utf-8"))


def projection(events):
    class Provider(_FakeProvider):
        def execute(self, query, parameters=None):
            self.queries.append((query, parameters))
            return self.rows
    provider = Provider()
    provider.rows = [{"recording_id": "actual-replay", "recording_name": "实际录制",
        "step_count": len(events), "action_id": f"actual-replay:{event['seq']}",
        "seq": event["seq"], "action_type": event["type"],
        "event_payload_json": json.dumps(event, ensure_ascii=False)} for event in events]
    return _store(provider), provider


def read(store):
    return asyncio.run(store.get_recording_subgraph("actual-replay", project_id="actual-project"))


def test_actual_failed_query_now_reads_every_original_field_in_order():
    events = actual_events()
    store, provider = projection(events)
    result = read(store)
    assert [node["event"] for node in result["nodes"] if node["kind"] == "action"] == events
    assert result["event_payloads"] == {"protocol": "recording-event-payloads-v1", "status": "complete",
        "expected_count": len(events), "available_count": len(events), "warnings": []}
    assert provider.queries[0][1] == {"project_id": "actual-project", "recording_id": "actual-replay"}


@pytest.mark.parametrize("defect,reason", [("missing", "event_payload_missing"),
    ("json", "event_payload_invalid"), ("schema", "event_payload_invalid"),
    ("identity", "event_payload_identity_mismatch"), ("fields", "event_payload_fields_missing"),
    ("count", "recording_action_range_mismatch"), ("range", "recording_action_range_mismatch"),
    ("old-count", "recording_step_count_missing")])
def test_actual_projection_defects_report_partial_without_fake_events(defect, reason):
    store, provider = projection(actual_events())
    if defect == "missing": provider.rows[0]["event_payload_json"] = None
    elif defect == "json": provider.rows[0]["event_payload_json"] = "{broken"
    elif defect == "schema": provider.rows[0]["event_payload_json"] = "[]"
    elif defect in ("identity", "fields"):
        payload = json.loads(provider.rows[0]["event_payload_json"])
        if defect == "identity": payload["seq"] = 999
        else: payload.pop("page_effect")
        provider.rows[0]["event_payload_json"] = json.dumps(payload)
    elif defect == "count": provider.rows.pop()
    elif defect == "range": provider.rows.pop(1)
    else: provider.rows[0]["step_count"] = None
    result = read(store)
    assert result["status"] == "success" and result["event_payloads"]["status"] == "partial"
    assert any(warning["reason"] == reason for warning in result["event_payloads"]["warnings"])
    if defect in ("missing", "json", "schema", "identity", "fields"):
        assert next(node for node in result["nodes"] if node["kind"] == "action")["event"] is None


def test_long_values_unknown_nesting_and_password_mask_preserve_original_projection():
    events = copy.deepcopy(actual_events())
    fill = next(event for event in events if event["type"] == "fill")
    fill["value"] = "完整输入" * 200
    fill["page_effect"]["boundary"] = {"zero": 0, "false": False, "null": None, "nested": [["tail"]]}
    store, provider = projection(events)
    assert next(node["event"] for node in read(store)["nodes"] if node.get("action_type") == "fill") == fill
    fill["target"]["attributes"]["type"] = "password"
    original = copy.deepcopy(fill)
    row = next(row for row in provider.rows if row["seq"] == fill["seq"])
    row["event_payload_json"] = json.dumps(fill)
    masked = next(node["event"] for node in read(store)["nodes"] if node.get("action_type") == "fill")
    assert masked == {**original, "value": {"length": len(fill["value"])}}
    assert json.loads(row["event_payload_json"]) == original


def test_boolean_sequence_or_non_finite_payload_cannot_claim_complete():
    for field, value in (("seq", False), ("page_effect", {"unknown": float("nan")})):
        events = copy.deepcopy(actual_events())
        events[0][field] = value
        store, _ = projection(events)
        assert read(store)["event_payloads"]["status"] == "partial"


@pytest.mark.asyncio
async def test_actual_step_payload_reaches_planning_request_and_frozen_history(tmp_path):
    events = actual_events()
    graph, provider = projection(events)
    def inspect_request(context):
        recording = next(entry for entry in context["evidence"] if entry["kind"] == "recording")
        actions = [node for node in recording["payload"]["graph"]["nodes"] if node["kind"] == "action"]
        assert [node["event"] for node in actions] == events
        target = item(context, "recorded-target")
        target["evidence_ids"].append(recording["id"])
        return finish(target)
    model = SequenceModel({"action": "query", "summary": "读取完整录制步骤",
        "queries": [{"kind": "recording", "source_id": "actual-replay"}]}, inspect_request)
    async with environment(tmp_path, model=model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key="recording-evidence", name="录制依据"))
        case = await active_case(env, project, "recorded-target", {"objective": "依据完整录制操作",
            "steps": [{"action": "核对嵌套页面输入"}]}, mode="ui_automation")
        class Recordings:
            async def list_sessions(self, **options):
                return [await self.get_session("actual-replay")]
            async def get_session(self, key):
                return SimpleNamespace(id="actual-replay", project_id=project.id,
                    status=SimpleNamespace(value="completed"), name="录制依据", finalize_metrics={},
                    updated_at=datetime(2026, 10, 8, tzinfo=timezone.utc))
        env.sources.set_recording_sources(recording_store=Recordings(), recording_graph_store=graph)
        response = await env.client.post(f"/projects/{project.id}/case-plans", json={
            "objective": "根据已录制步骤进行测试", "candidate_case_ids": [case.case.id]})
        response.raise_for_status()
        plan = response.json()
        assert plan["status"] == "planned" and plan["prompt_version"] == "case-planning-v5"
        assert len(model.requests) == 2
        frozen = next(entry for entry in plan["evidence"] if entry["kind"] == "recording")
        assert frozen["payload"]["graph"]["event_payloads"]["status"] == "complete"
        provider.rows[0]["event_payload_json"] = None
        reread = await env.client.get(f"/projects/{project.id}/case-plans/{plan['id']}")
        reread.raise_for_status()
        assert next(entry for entry in reread.json()["evidence"] if entry["kind"] == "recording") == frozen
