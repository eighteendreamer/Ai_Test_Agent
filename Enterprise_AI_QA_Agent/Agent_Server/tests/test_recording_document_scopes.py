"""Replay actual saved scope inputs and verify identity/loss boundaries."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.recorder.drivers.document_scopes import RecordingDocumentScopes
from src.application.recorder.recorder_session_service import RecorderSessionService
from src.schemas.recording import RecorderEvent

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "docs/upgrade_2026-10-03/p0-recording-scopes-product-first/cdp-attach/nested-frame"


class NativeFrame:
    parent_frame = None
    url = "http://owned.test/original"


def original():
    event = json.loads((SAMPLE / "events.json").read_text(encoding="utf-8"))[0]
    event["seq"] = event["page"].pop("recording_scope")["local_seq"]
    return event


def test_actual_event_replay_preserves_identity_and_rejects_changed_content():
    scopes, page, frame = RecordingDocumentScopes(), object(), NativeFrame()
    identity = scopes.register(page, frame, [])
    raw = {**original(), "recording_document_id": identity}
    event = scopes.event(raw, page=page, frame=frame)
    assert event["seq"] == 0 and event["page"]["recording_scope"]["local_seq"] == raw["seq"]
    assert scopes.event(copy.deepcopy(raw), page=page, frame=frame) is None
    with pytest.raises(ValueError, match="cannot change"):
        scopes.event({**raw, "value": "changed-original"}, page=page, frame=frame)
    with pytest.raises(ValueError, match="native source mismatch"):
        scopes.event(raw, page=object(), frame=frame)
    scopes.confirm({"document_id": identity, "next_seq": 1, "emitted_count": 1, "pending": 0}, page=page, frame=frame)
    assert scopes.result()["frames_unconfirmed"] == 0


def test_old_document_without_departure_receipt_and_local_gap_remain_unconfirmed():
    scopes, page, frame = RecordingDocumentScopes(), object(), NativeFrame()
    identity = scopes.register(page, frame, [])
    scopes.event({**original(), "recording_document_id": identity}, page=page, frame=frame)
    scopes.confirm({"document_id": identity, "next_seq": 2, "emitted_count": 2, "pending": 0}, page=page, frame=frame)
    assert scopes.result()["frames_unconfirmed"] == 1
    scopes.register(page, frame, [])
    assert scopes.result()["scopes"][0]["ended"]
    assert not scopes.result()["scopes"][0]["confirmed"]


def test_actual_cached_document_reactivation_preserves_sequence_and_rejects_foreign_source():
    sample = ROOT / "docs/upgrade_2026-10-03/p0-recording-lifecycle-cache-storage/cdp-attach/cache-back"
    rows = json.loads((sample / "scoped-deliveries.json").read_text(encoding="utf-8"))
    raw = next(row for row in rows if (row.get("target") or {}).get("locators", {}).get("id") == "root")
    scopes, page, frame = RecordingDocumentScopes(), object(), NativeFrame()
    original_id = scopes.register(page, frame, [])
    raw = {**raw, "recording_document_id": original_id}
    first = scopes.event(raw, page=page, frame=frame)
    scopes.confirm({"document_id": original_id, "next_seq": 1, "emitted_count": 1, "pending": 0}, page=page, frame=frame, closed=True)
    intervening = scopes.register(page, frame, [])
    with pytest.raises(ValueError, match="native source mismatch"):
        scopes.activate(original_id, page=object(), frame=frame)
    assert scopes.current[frame] == intervening
    scopes.activate(original_id, page=page, frame=frame)
    assert scopes.current[frame] == original_id
    assert scopes.documents[intervening].ended
    assert scopes.event(copy.deepcopy(raw), page=page, frame=frame) is None
    assert scopes.next_seq == first["seq"] + 1
    assert scopes.documents[original_id].receipt is None
    assert not scopes.documents[original_id].ended


@pytest.mark.parametrize("fault", ["omitted_document", "duplicate_document", "native_metadata", "local_sequence"])
def test_actual_pg_receipt_rejects_scope_mismatch(fault):
    receipt = json.loads((SAMPLE / "receipt.json").read_text(encoding="utf-8"))
    events = [RecorderEvent.model_validate(row) for row in json.loads((SAMPLE / "pg-events.json").read_text(encoding="utf-8"))]
    runtime = SimpleNamespace(parse_failures=0, dropped_on_db_error=0)
    RecorderSessionService._verify_capture_receipt(runtime, receipt, events, operation="stop")
    if fault == "omitted_document": receipt["scopes"].pop(0)
    if fault == "duplicate_document": receipt["scopes"].append(copy.deepcopy(receipt["scopes"][0]))
    if fault == "native_metadata": events[0].page["recording_scope"]["frame_id"] = "foreign-frame"
    if fault == "local_sequence": events[0].page["recording_scope"]["local_seq"] += 1
    with pytest.raises(RuntimeError, match="could not be confirmed"):
        RecorderSessionService._verify_capture_receipt(runtime, receipt, events, operation="stop")
