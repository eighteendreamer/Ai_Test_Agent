"""Same-transaction original waiting material. No owner adoption or execution."""
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import TypeAdapter

from src.application.deep_agents.framework_transcript import serialize_messages
from src.application.runtime.controlled_case_wait_projection import project_controlled_case_wait, wait_native_messages
from src.application.runtime.tool_messages import waiting_tool_record
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.framework_event_completion import _event_references, _require_ordered_log
from src.runtime.framework_tool_materials import read_projected_job_outputs, read_unapproved_tool_materials
from src.runtime.postgres_tool_job_store import _job_from_row
from src.schemas.controlled_case_admission import ControlledCaseApprovalStateCheckpoint, read_wait_checkpoint
from src.schemas.controlled_case_approval_state import ControlledCaseApprovalStateAsset
from src.schemas.controlled_case_task_input import parse_controlled_case_task_input
from src.schemas.framework_approval import NativeToolInterrupt, approval_value_hash
from src.schemas.session import ToolApprovalRequest


def _require(condition, message):
    if not condition:
        raise FrameworkApprovalRecoveryRequired(message)


def _events(cur, settings, binding, log, *, before_sequence=None):
    _require_ordered_log(binding, log, 0)
    references = _event_references(cur, settings, binding, log)
    sequences = [item['event_sequence'] for item in references]
    _require(all(a < b for a, b in zip(sequences, sequences[1:])),
        "Controlled waiting events lost their original durable order")
    condition, params = ('', []) if before_sequence is None else (' AND event_sequence<%s', [before_sequence])
    cur.execute(f"SELECT id FROM {settings.database.postgres_event_table} WHERE session_id=%s "
        "AND payload->>'turn_id'=%s AND payload ? 'step'" + condition + " ORDER BY event_sequence FOR SHARE",
        (binding.session_id, binding.turn_id, *params))
    _require([row['id'] for row in cur.fetchall()] == [item['id'] for item in log],
        "Controlled waiting events omit or add original acknowledged intents")
    return references


def _terminal_materials(cur, settings, original, messages, event_log, jobs):
    return read_unapproved_tool_materials(original.graph_input, original.graph_input.binding, messages, jobs,
        event_log=event_log, projected_outputs=read_projected_job_outputs(cur, settings, jobs))


def build_wait_state_in_transaction(cur, settings, owner, case, task, projected, snapshot, event_log,
                                    retained_at, hygiene_metadata):
    from src.application.deep_agents.native_approval_batch import read_pending_tool_snapshot_original
    from src.application.models.framework_model_budget_projection import capture_model_budget_material
    from src.schemas.runtime_binding import RuntimeCheckpointSource

    original = parse_controlled_case_task_input(task['input_payload']['case_execution_input'])
    natives = [NativeToolInterrupt.model_validate(p.link.model_dump(mode='json', include=set(NativeToolInterrupt.model_fields)))
        for p in projected]
    source = RuntimeCheckpointSource.model_validate(owner.model_dump(exclude={'owner_token'}))
    _, native = read_pending_tool_snapshot_original(cur, settings, source, original.graph_input, natives)
    messages = wait_native_messages(native)
    cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE session_id=%s AND turn_id=%s "
        "ORDER BY id FOR SHARE", (owner.binding.session_id, owner.binding.turn_id))
    batch_ids = {p.job.id for p in projected}
    jobs = [_job_from_row(row) for row in cur.fetchall() if row['id'] not in batch_ids]
    materials = _terminal_materials(cur, settings, original, messages, event_log, jobs)
    # The application intent may use naive UTC; TIMESTAMPTZ has an actual
    # aware row header. Freeze the row readback, not an intent pretending to
    # be the database receipt (same rule as finalization part receipts).
    approvals, waiting = [], []
    for p in projected:
        cur.execute(f"SELECT * FROM {settings.database.postgres_approval_table} WHERE id=%s FOR SHARE", (p.approval.id,))
        approval = ToolApprovalRequest.model_validate(cur.fetchone())
        approvals.append(approval)
        waiting.append(waiting_tool_record(approval, p.job))
    root = capture_model_budget_material(owner.binding, snapshot)
    state = project_controlled_case_wait(original, messages, natives, approvals, waiting, materials, event_log, root)
    values = TypeAdapter(Any).dump_python(dict(schema_version=1, scope='controlled_case_wait_state_material_only',
        binding=owner.binding, task_id=owner.task_id, attempt_id=owner.attempt_id, owner_epoch=owner.owner_epoch,
        run_item_execution_attempt_id=case.attempt.id, input_sha256=case.checkpoint.input_sha256,
        admission_sha256=approval_value_hash(case.checkpoint.model_dump(mode='json')),
        model_ledger_sha256=approval_value_hash(snapshot.model_dump(mode='json')), root_model_budget=root,
        native_messages=serialize_messages(native.values.get('messages', [])), natives=natives, approvals=approvals,
        tool_materials=materials, event_references=_events(cur, settings, owner.binding, event_log),
        hygiene_metadata=hygiene_metadata, state=state, retained_at=retained_at), mode='json')
    asset = ControlledCaseApprovalStateAsset(**values, asset_sha256=approval_value_hash(values))
    checkpoint = ControlledCaseApprovalStateCheckpoint(schema_version=7, admission=case.checkpoint,
        state_asset=asset, retained_at=retained_at)
    _require(case.attempt.checkpoint_version == 1, "Controlled waiting state cannot overwrite another original checkpoint")
    case.store._write_attempt(cur, case.attempt.model_copy(update={
        'checkpoint_version': 7, 'checkpoint_payload': checkpoint.model_dump(mode='json')}))
    return asset


def require_wait_state_in_transaction(cur, settings, case, task, receipt, original, snapshots, natives):
    """Read historical material without current Session/Job status reconstruction."""
    from langchain_core.messages import AIMessage, ToolMessage
    from src.application.models.framework_model_budget_projection import read_model_budget_material

    _require(receipt.schema_version == 2 and case.attempt.checkpoint_version in {7, 8, 9},
        "Controlled waiting state has no original retained material")
    checkpoint = read_wait_checkpoint(case.attempt)
    asset = checkpoint.state_asset
    _require((asset.asset_sha256, asset.binding, asset.task_id, asset.attempt_id, asset.owner_epoch,
        asset.retained_at, asset.model_ledger_sha256) == (receipt.state_asset_sha256, original.graph_input.binding,
        task['id'], receipt.attempt_id, receipt.owner_epoch, receipt.parked_at, receipt.model_ledger_sha256)
        and {n.interrupt_id: n for n in asset.natives} == {n.interrupt_id: n for n in natives},
        "Controlled waiting state differs from its original receipt/producer")
    budget = read_model_budget_material(asset.binding, asset.root_model_budget)
    _require(approval_value_hash(budget.model_dump(mode='json')) == receipt.model_ledger_sha256,
        "Controlled waiting state changed its original model ledger")
    _require(len(snapshots) == 1, "Controlled waiting state has no single direct native root")
    native = next(iter(snapshots.values()))
    _require(serialize_messages(native.values.get('messages', [])) == asset.native_messages,
        "Controlled waiting state changed its original native messages")
    # Later consumed siblings add task results to this historical snapshot.
    # Select only material actually retained at parking, never project the new
    # result into an old state or require the old wait to remain pending.
    allowed = {(m.source.source_message_id, m.source.tool_call_id) for m in asset.tool_materials}
    messages, current = [], None
    for message in wait_native_messages(native, retained_sources=allowed):
        if isinstance(message, AIMessage):
            current = message
        if isinstance(message, ToolMessage) and (current.id if current else None, message.tool_call_id) not in allowed:
            continue
        messages.append(message)
    cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE id=ANY(%s) ORDER BY id FOR SHARE",
        ([m.record.job_id for m in asset.tool_materials if m.record.job_id is not None],))
    jobs = [_job_from_row(row) for row in cur.fetchall()]
    materials = _terminal_materials(cur, settings, original, messages, asset.state['event_log'], jobs)
    _require(tuple(materials) == asset.tool_materials, "Controlled waiting state changed its settled tool originals")
    waiting = []
    for saved in asset.approvals:
        cur.execute(f"SELECT * FROM {settings.database.postgres_approval_table} WHERE id=%s FOR SHARE", (saved.id,))
        approval = ToolApprovalRequest.model_validate(cur.fetchone())
        _require(saved.model_dump(mode='json', exclude={'status', 'resolved_at', 'decision_note'})
            == approval.model_dump(mode='json', exclude={'status', 'resolved_at', 'decision_note'}),
            "Controlled waiting state changed its original approval material")
        cur.execute(f"SELECT * FROM {settings.database.postgres_tool_job_table} WHERE id=%s FOR SHARE",
            (saved.metadata['tool_job_id'],))
        waiting.append(waiting_tool_record(saved, _job_from_row(cur.fetchone())))
    expected = project_controlled_case_wait(original, messages, asset.natives, asset.approvals, waiting,
        materials, asset.state['event_log'], asset.root_model_budget)
    cur.execute(f"SELECT event_sequence FROM {settings.database.postgres_event_table} WHERE id=%s",
        (str(uuid5(NAMESPACE_URL, 'controlled-case-approval-wait:' + receipt.task_id)),))
    audit = cur.fetchone()
    _require(audit is not None, "Controlled waiting state lost its original atomic wait audit")
    references = _events(cur, settings, asset.binding, asset.state['event_log'], before_sequence=audit['event_sequence'])
    expected = TypeAdapter(Any).dump_python(expected, mode='json')
    _require(references == asset.event_references and expected == asset.state,
        "Controlled waiting state differs from its deterministic original projection")
    return asset
