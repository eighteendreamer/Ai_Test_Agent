"""Validate original finalization intents within the existing application commit.

No upstream counterpart (checked: Session rebase/UPSERT, A1 application commits,
LangGraph persistence, plan v3 §12.1): hashing whatever rows happen to exist is
not proof those rows represent the frozen Runtime result. No new journal is
needed: these messages and verifications commit with the existing A1 receipt.
"""
from __future__ import annotations

from src.application.context.transcript_hygiene_service import TranscriptHygieneService
from src.runtime.framework_application_projection import COMMIT_KEY, _hash, _require
from src.runtime.framework_runtime_results import RESULT_KEY, lock_runtime_result_stage, validate_runtime_result


def original_messages(asset):
    messages = [item for item in asset.runtime_result.tool_messages
        if bool(item.metadata.get("persist_transcript", True))]
    if asset.runtime_result.output_text:
        messages.append(TranscriptHygieneService().finalization_assistant_message(
            message_id=asset.assistant_message_id, content=asset.runtime_result.output_text,
            created_at=asset.projection_at, state=asset.runtime_result.state))
    _require(len({item.id for item in messages}) == len(messages), "Original message identities are duplicated")
    _require(all(item.metadata.get("turn_id") == asset.binding.turn_id for item in messages),
        "Original message has a different Turn")
    return messages


def merge_original_verifications(existing, asset):
    _require(isinstance(existing, list), "Original verification container is not a list")
    originals = {item.id: item.model_dump(mode="python") for item in asset.verification_results}
    _require(len(originals) == len(asset.verification_results), "Original verification identities are duplicated")
    found = set()
    for item in existing:
        identity = item.get("id") if isinstance(item, dict) else None
        if identity in originals:
            _require(identity not in found and _hash(item) == _hash(originals[identity]),
                "Original verification differs from an existing result")
            found.add(identity)
    return [*existing, *(value for key, value in originals.items() if key not in found)]


def _message_value(message):
    return message.model_dump(mode="python")


def _validate_messages(messages, asset):
    for original in original_messages(asset):
        actual = [item for item in messages if item.id == original.id]
        _require(len(actual) == 1 and _hash(_message_value(actual[0])) == _hash(_message_value(original)),
            "Message differs from the original finalization result")


def _validate_verifications(metadata, asset):
    if not asset.verification_results:
        return
    existing = metadata.get("verification_results", [])
    merged = merge_original_verifications(existing, asset)
    _require(len(merged) == len(existing), "Original verification is missing from finalization")


def _existing_messages(cur, settings, asset):
    originals = original_messages(asset)
    cur.execute(f"SELECT id,session_id,role,content,created_at,metadata FROM {settings.database.postgres_message_table} "
        "WHERE id=ANY(%s) ORDER BY id FOR UPDATE", ([item.id for item in originals],))
    expected = {item.id: _message_value(item) for item in originals}
    for row in cur.fetchall():
        value = dict(row)
        _require(value.pop("session_id") == asset.binding.session_id and _hash(value) == _hash(expected[row["id"]]),
            "Message conflicts with an existing original identity")
    return originals


def require_partial_finalization(cur, settings, metadata, stage, asset):
    """Validate already-written parts; absent parts are work, not success proof.

    No upstream counterpart (checked: existing part receipts, message recovery,
    LangGraph persistence/interrupts, plan v3 §12.1): this authorizes only the
    original application finalizer, never graph/model/tool replay.
    """
    from src.runtime.framework_application_projection import (
        EVENT_COMMITS_KEY, _event_commits, _event_row, _require_event_intent,
        existing_snapshot_projection, require_event_projections,
    )
    from src.runtime.framework_observation_projection import ASSET_KEY, require_existing_observation_projections

    binding, manifest = asset.binding, stage.source_manifest or {}
    existing_snapshot_projection(cur, settings, stage, binding, asset)
    originals = _existing_messages(cur, settings, asset)
    _require(stage.user_message_id not in {item.id for item in originals},
        "Original message collides with the admitted input")
    merge_original_verifications(metadata.get("verification_results", []), asset)
    receipt = _event_commits(stage, binding, asset.asset_sha256)
    if EVENT_COMMITS_KEY in manifest:
        require_event_projections(cur, settings, stage, binding, asset.asset_sha256)
    for event in asset.runtime_result.events:
        row = _event_row(cur, settings, event.id)
        if row is not None:
            _require_event_intent(row, event)
    for name in ("snapshot.saved", "verification.completed", "observations.persisted",
        "assistant.stream.completed", "assistant.response_generated", "turn.completed"):
        row = _event_row(cur, settings, asset.event_id(name))
        _require(row is None or (receipt is not None and row["id"] in receipt.event_hashes),
            "Existing finalization event has no atomic projection receipt")
    if ASSET_KEY in manifest:
        require_existing_observation_projections(cur, settings, stage, binding, asset.asset_sha256)


def prepare_finalization_projection(cur, settings, write, candidate):
    """Before Session/message UPSERT, with the Session/owner/stage locks held."""
    stage, task = lock_runtime_result_stage(cur, settings, write.owner)
    manifest = stage.source_manifest or {}
    _require(COMMIT_KEY not in manifest, "Committed finalization must use ACK recovery")
    _require(RESULT_KEY in manifest, "Finalization has no original result asset")
    asset = validate_runtime_result(cur, settings, write.owner, stage, task, manifest[RESULT_KEY])
    _require(write.runtime_result_asset_sha256 == asset.asset_sha256
        and write.output_message_id == (asset.assistant_message_id if asset.runtime_result.output_text else None),
        "Finalization differs from its original output identity")
    _validate_messages(candidate.messages, asset)
    _validate_verifications(candidate.metadata, asset)
    originals = _existing_messages(cur, settings, asset)
    _require(stage.user_message_id not in {item.id for item in originals},
        "Original message collides with the admitted input")
    if asset.verification_results:
        cur.execute(f"SELECT metadata FROM {settings.database.postgres_session_table} WHERE id=%s", (asset.binding.session_id,))
        merge_original_verifications((cur.fetchone()["metadata"] or {}).get("verification_results", []), asset)
    # The admitted input is not a derived message and must not be repurposed.
    cur.execute(f"SELECT role,content,metadata FROM {settings.database.postgres_message_table} "
        "WHERE id=%s AND session_id=%s FOR UPDATE", (stage.user_message_id, asset.binding.session_id))
    user = cur.fetchone()
    # AgentTaskDispatcher and Session submission persist content.strip(); the
    # asset intentionally retains the raw admitted request for other consumers.
    _require(user is not None and user["role"] == "user" and user["content"] == asset.user_message.strip()
        and (user["metadata"] or {}).get("turn_id") == asset.binding.turn_id,
        "Admitted message differs from the original input")
    candidates = [item for item in candidate.messages if item.id == stage.user_message_id]
    _require(len(candidates) == 1 and candidates[0].role.value == "user"
        and candidates[0].content == asset.user_message.strip()
        and candidates[0].metadata.get("turn_id") == asset.binding.turn_id,
        "Admitted message is missing or changed in finalization")
    return {item.id for item in originals} | {stage.user_message_id}


def require_finalization_projection(cur, settings, session, asset, user_message_id):
    """After actual writes, before the existing A1 receipt is recorded."""
    from src.runtime.postgres_session_store import _message_from_row

    originals = original_messages(asset)
    cur.execute(f"SELECT * FROM {settings.database.postgres_message_table} WHERE id=ANY(%s) AND session_id=%s",
        ([item.id for item in originals], asset.binding.session_id))
    _validate_messages([_message_from_row(row) for row in cur.fetchall()], asset)
    cur.execute(f"SELECT role,content,metadata FROM {settings.database.postgres_message_table} "
        "WHERE id=%s AND session_id=%s", (user_message_id, asset.binding.session_id))
    user = cur.fetchone()
    _require(user is not None and user["role"] == "user" and user["content"] == asset.user_message.strip()
        and (user["metadata"] or {}).get("turn_id") == asset.binding.turn_id,
        "Admitted message differs from the original input after finalization")
    cur.execute(f"SELECT metadata FROM {settings.database.postgres_session_table} WHERE id=%s", (asset.binding.session_id,))
    metadata = cur.fetchone()["metadata"] or {}
    _validate_verifications(metadata, asset)
    _require(_hash(metadata.get("verification_results", [])) == _hash(session.metadata.get("verification_results", [])),
        "Verification storage differs from the submitted finalization")
