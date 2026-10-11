from datetime import datetime, timedelta, timezone

from src.application.recorder.recording_quality import analyze_recording_quality
from src.schemas.recording import RecorderEvent


def _event(seq: int, event_type: str = "click", *, value=None, page_effect=None) -> RecorderEvent:
    return RecorderEvent(
        seq=seq,
        type=event_type,
        timestamp=datetime(2026, 10, 11, tzinfo=timezone.utc) + timedelta(seconds=seq),
        page={"url": "http://test.local/", "title": "test"},
        target={"tag": "button", "locators": {"id": "save", "text": "保存"}},
        value=value,
        page_effect=page_effect or {},
    )


def test_quality_keeps_real_repeated_actions_and_only_excludes_observation_noise():
    events = [
        _event(0, "page_scan"),
        _event(1),
        _event(2),
        _event(3, "page_response", page_effect={"response": {"action_local_seqs": [1, 2]}}),
    ]

    result = analyze_recording_quality(
        events,
        {"protocol": "recording-finish-v1", "pg_verified": True, "missing_seqs": []},
    )

    assert result["protocol"] == "recording-quality-v1"
    assert result["cleaned_event_sequences"] == [1, 2]
    assert result["noise"][0] == {"seq": 0, "type": "page_scan", "reason": "observation_only"}
    assert result["noise"][1]["type"] == "page_response"
    assert result["duplicate_candidates"] == [{"sequences": [1, 2], "reason": "same_action_payload"}]
    assert result["omission"]["status"] == "verified"


def test_quality_does_not_call_a_repeated_click_a_duplicate_to_remove():
    events = [_event(4), _event(5)]

    result = analyze_recording_quality(
        events,
        {"protocol": "recording-finish-v1", "pg_verified": True, "missing_seqs": []},
    )

    assert result["cleaned_event_sequences"] == [4, 5]
    assert result["duplicate_candidates"]
    assert result["removed_event_sequences"] == []


def test_quality_reports_sequence_gap_and_unverified_receipt_without_guessing():
    result = analyze_recording_quality(
        [_event(0), _event(2)],
        {"protocol": "recording-finish-v1", "pg_verified": False, "missing_seqs": [1]},
    )

    assert result["omission"]["status"] == "missing"
    assert result["omission"]["missing_sequences"] == [1]
    assert result["omission"]["sequence_gaps"] == [{"from": 1, "to": 1, "count": 1}]


def test_actual_capture_scope_changes_only_local_sequence_not_action_identity():
    events = [_event(0), _event(1)]
    for index, event in enumerate(events):
        event.page["recording_scope"] = {
            "page_id": "page-1", "frame_id": "frame-1", "document_id": "doc-1",
            "frame_path": [], "local_seq": index,
        }
    result = analyze_recording_quality(events)
    assert result["duplicate_candidates"] == [{"sequences": [0, 1], "reason": "same_action_payload"}]
    events[1].page["recording_scope"]["local_seq"] = 99
    assert analyze_recording_quality(events)["duplicate_candidates"] == [{"sequences": [0, 1], "reason": "same_action_payload"}]
    events[1].page["recording_scope"]["document_id"] = "another-document"
    assert analyze_recording_quality(events)["duplicate_candidates"] == []


def test_pending_or_loss_receipt_cannot_prove_delivery():
    for key in ("pending", "frames_unconfirmed", "transport_dropped", "channel_dropped"):
        result = analyze_recording_quality([_event(0)], {"pg_verified": True, key: 1})
        assert result["omission"]["status"] == "missing"
        assert result["omission"]["business_capture_complete"] == "unknown"


def test_absent_receipt_cannot_prove_delivery_and_payload_values_are_not_exposed():
    result = analyze_recording_quality([_event(0, "fill", value="private-test-value")])
    assert result["omission"]["status"] == "unknown"
    assert "private-test-value" not in str(result)

\n