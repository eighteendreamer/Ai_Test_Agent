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
from collections import Counter, defaultdict
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


def _response_action_associations(events: list[RecorderEvent]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project explicit recorder action references without claiming causality."""
    action_index: dict[tuple[str, str, str, int], list[RecorderEvent]] = defaultdict(list)
    for event in events:
        if event.type not in _REPLAYABLE:
            continue
        scope = event.page.get("recording_scope")
        if not isinstance(scope, dict):
            continue
        page_id, frame_id, document_id = (scope.get(key) for key in ("page_id", "frame_id", "document_id"))
        local_seq = scope.get("local_seq")
        if (not all(isinstance(value, str) and value for value in (page_id, frame_id, document_id))
                or type(local_seq) is not int or local_seq < 0):
            continue
        action_index[(page_id, frame_id, document_id, local_seq)].append(event)

    associations: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for event in events:
        if event.type != "page_response":
            continue
        effect = event.page_effect if isinstance(event.page_effect, dict) else {}
        response = effect.get("response")
        if not isinstance(response, dict):
            continue
        local_seqs = response.get("action_local_seqs")
        if not isinstance(local_seqs, list) or not local_seqs:
            continue
        reason = None
        valid_local_seqs = all(type(value) is int and value >= 0 for value in local_seqs)
        if not valid_local_seqs or len(set(local_seqs)) != len(local_seqs):
            reason = "invalid_action_references"
        scope = event.page.get("recording_scope")
        if reason is None and not isinstance(scope, dict):
            reason = "response_scope_missing"
        related: list[RecorderEvent] = []
        if reason is None:
            page_id, frame_id, document_id = (scope.get(key) for key in ("page_id", "frame_id", "document_id"))
            if not all(isinstance(value, str) and value for value in (page_id, frame_id, document_id)):
                reason = "response_scope_incomplete"
            else:
                for local_seq in local_seqs:
                    candidates = action_index.get((page_id, frame_id, document_id, local_seq), [])
                    if not candidates:
                        reason = "action_reference_not_found"
                        break
                    if len(candidates) != 1:
                        reason = "action_reference_ambiguous"
                        break
                    action = candidates[0]
                    if action.seq >= event.seq:
                        reason = "action_not_before_response"
                        break
                    related.append(action)
        if reason is not None:
            unresolved.append({"response_event_sequence": event.seq, "reason": reason})
            continue

        network = response.get("network")
        outcomes: Counter[str] = Counter()
        if isinstance(network, list):
            for item in network:
                outcome = item.get("outcome") if isinstance(item, dict) else None
                outcomes[outcome if isinstance(outcome, str) and outcome in {"response", "error", "aborted", "timeout"} else "other"] += 1
        network_status = response.get("network_observation_status")
        if not isinstance(network_status, str) or network_status not in {"observed", "no_requests_observed", "unavailable"}:
            network_status = "unknown"
        association = response.get("association")
        if association != "temporal_after_actions":
            association = "recorder_action_reference"
        associations.append({
            "response_event_sequence": event.seq,
            "action_event_sequences": [action.seq for action in related],
            "association": association,
            "network_correlation": (
                "instrumented_fetch_xhr_completion"
                if response.get("network_correlation") == "instrumented_fetch_xhr_completion"
                else "unavailable"
            ),
            "network_observation_status": network_status,
            "network_outcome_counts": dict(sorted(outcomes.items())),
        })
    return associations, unresolved


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

    response_associations, unresolved_response_associations = _response_action_associations(ordered)

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
        "response_action_associations": response_associations,
        "unresolved_response_associations": unresolved_response_associations,
        "omission": _omission(ordered, finish_receipt),
    }

