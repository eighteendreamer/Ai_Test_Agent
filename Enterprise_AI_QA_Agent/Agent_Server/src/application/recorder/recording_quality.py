"""Derive reviewable recording quality facts without changing raw events.

The recorder event table is the evidence source and remains append-only.  This
module only creates a deterministic projection for review and case generation:
observation events are excluded from replay steps, possible semantic repeats
are reported as candidates, and delivery gaps are reported from the finish
receipt and sequence numbers.  A candidate is never removed automatically;
two real clicks can have the same payload and are both valid actions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.schemas.recording import RecorderEvent

QUALITY_PROTOCOL = "recording-quality-v1"
_REPLAYABLE = {"click", "dblclick", "fill", "key", "submit", "scroll", "navigate", "file_change"}
_OBSERVATION_ONLY = {"page_scan", "page_response"}


def _stable_payload(event: RecorderEvent) -> dict[str, Any]:
    """Return only fields that identify an action, excluding delivery identity."""
    target = event.target if isinstance(event.target, dict) else {}
    locators = target.get("locators") if isinstance(target.get("locators"), dict) else {}
    stable_target = {
        "tag": target.get("tag"),
        "role": target.get("role"),
        "locators": {
            key: locators.get(key)
            for key in ("id", "testid", "css", "xpath", "role_name")
            if locators.get(key)
        },
        "shadow_path": target.get("shadow_path"),
    }
    scope = event.page.get("recording_scope")
    scope = scope if isinstance(scope, dict) else {}
    return {
        "type": event.type,
        "page_scope": {"url": event.page.get("url"), "identity": {
            key: scope.get(key) for key in ("page_id", "frame_id", "document_id", "frame_path")}},
        "target": stable_target,
        "pixel": event.pixel if not any(locators.values()) else None,
        "value": event.value,
    }


def _payload_hash(event: RecorderEvent) -> str:
    encoded = json.dumps(_stable_payload(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sequence_gaps(sequences: list[int]) -> list[dict[str, int]]:
    if not sequences:
        return []
    ordered = sorted(set(sequences))
    gaps: list[dict[str, int]] = []
    if ordered[0] > 0:
        gaps.append({"from": 0, "to": ordered[0] - 1, "count": ordered[0]})
    for previous, current in zip(ordered, ordered[1:]):
        if current > previous + 1:
            gaps.append({"from": previous + 1, "to": current - 1, "count": current - previous - 1})
    return gaps


def _omission(events: list[RecorderEvent], receipt: dict[str, Any] | None) -> dict[str, Any]:
    receipt = receipt if isinstance(receipt, dict) else {}
    missing = sorted({int(value) for value in receipt.get("missing_seqs", []) if isinstance(value, int) or str(value).isdigit()})
    gaps = _sequence_gaps([int(event.seq) for event in events])
    loss_counters = {key: receipt.get(key, 0) for key in (
        "pending", "frames_unconfirmed", "transport_dropped", "channel_dropped")}
    loss = any(value != 0 for value in loss_counters.values())
    verified = receipt.get("pg_verified") is True and not missing and not gaps and not loss
    status = "verified" if verified else ("missing" if missing or gaps or receipt.get("pg_verified") is False else "unknown")
    if loss:
        status = "missing"
    return {"status": status, "missing_sequences": missing, "sequence_gaps": gaps,
        "scope": "published_event_delivery", "business_capture_complete": "unknown",
        "unconfirmed_delivery": loss_counters}


def analyze_recording_quality(
    events: list[RecorderEvent],
    finish_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable, secret-free diagnostic projection for a recording."""
    ordered = sorted(events, key=lambda event: event.seq)
    noise: list[dict[str, Any]] = []
    cleaned: list[int] = []
    duplicate_candidates: list[dict[str, Any]] = []
    previous_action: tuple[str, str, int] | None = None

    for event in ordered:
        if event.type in _REPLAYABLE:
            cleaned.append(event.seq)
            current = (event.type, _payload_hash(event), event.seq)
            if previous_action is not None and current[0] == previous_action[0] and current[1] == previous_action[1]:
                duplicate_candidates.append({"sequences": [previous_action[2], event.seq], "reason": "same_action_payload"})
            previous_action = current
            continue
        if event.type in _OBSERVATION_ONLY:
            reason = "observation_only"
        else:
            reason = "unsupported_event_type"
        noise.append({"seq": event.seq, "type": event.type, "reason": reason})

    return {
        "protocol": QUALITY_PROTOCOL,
        "raw_event_count": len(ordered),
        "replayable_event_count": len(cleaned),
        "observation_event_count": len(noise),
        "cleaned_event_sequences": cleaned,
        "removed_event_sequences": [],
        "noise": noise,
        "observation_policy": "retained_as_evidence_not_replayed",
        "duplicate_candidates": duplicate_candidates,
        "omission": _omission(ordered, finish_receipt),
    }

