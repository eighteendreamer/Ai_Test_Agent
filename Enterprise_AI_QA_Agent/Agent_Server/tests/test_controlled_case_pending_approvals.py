"""Exact native pending originals and dedicated formal scope, not parking/ACK."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import logging
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
from src.infrastructure.postgres_runtime import postgres_connect
from src.runtime.execution_context import framework_owner_scope
from src.runtime.framework_approval_journal import FrameworkApprovalRecoveryRequired
from src.runtime.postgres_agent_task_store import AgentSubmissionConflict
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, assert_runtime_owner
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.runtime_binding import RuntimeCheckpointSource
from src.schemas.session import ApprovalDecisionRequest, SessionStatus, ToolApprovalStatus
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain, database_digest
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_graph import business_bridge
from tests.test_controlled_case_runtime import assembly, prepare
from tests.test_framework_chat_models import _config, _provider, _tool_reply
from tests.test_framework_approval_projection import counts
from tests.test_framework_approval_submission import saved_facts
from tests.test_runtime_binding_persistence import make_command


@pytest_asyncio.fixture
async def pending_formal(formal_case, tmp_path, request):
    s = formal_case
    options = getattr(request, "param", {})
    if "deadline_seconds" in options:
        # Freeze the real policy before admission; never rewrite its proof to
        # manufacture an expired original after the graph has already parked.
        s.budget = s.budget.model_copy(update={"deadline_at": datetime.now(timezone.utc)
            + timedelta(seconds=options["deadline_seconds"])})
    reply = _tool_reply("openai_chat_completions")
    calls = reply["choices"][0]["message"]["tool_calls"]
    calls[0]["id"], calls[0]["function"]["arguments"] = "A", '{"path":"a.py"}'
    other = deepcopy(calls[0])
    other["id"], other["function"]["arguments"] = "B", '{"path":"b.py"}'
    if not options.get("single_tool"):
        calls.append(other)
    if options.get("third_tool"):
        third = deepcopy(other)
        third["id"], third["function"]["arguments"] = "C", '{"path":"c.py"}'
        calls.append(third)
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async def prepare_input(models):
            p = assembly(s, tmp_path / "assembly", models)
            tool = p.parts.tool_registry.get("inspect_source").model_copy(update={"permission_level": "ask"})
            p.parts.tool_registry.register_dynamic(tool, "inspect_source")
            return await prepare(s, p)

        config = _config("openai_chat_completions", url)
        async with execution(s, config, prepare_input=prepare_input, claim_task=not options.get("dispatch")) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=True) as bridge:
                ports = {}
                if options.get("runtime_wait"):
                    from src.runtime.controlled_case_approval_wait import ControlledCaseApprovalWaits

                    ports["approval_wait_service"] = ControlledCaseApprovalWaits(s.h.tasks, projection=bridge.projection)
                outcome, source = None, None
                if not options.get("dispatch"):
                    outcome = await ControlledCaseRuntime(runtime_service=bridge.runtime, **ports).execute(
                        h.task, executor=executor(s, h), resource_id="isolated-pending-originals", fencing_token=1)
                    assert outcome.graph_outcome.native_terminal is None
                    assert len(outcome.graph_outcome.native_interrupts) == len(calls) and len(requests) == 1 and effects == []
                    source = RuntimeCheckpointSource.model_validate(h.task.runtime_owner.model_dump(exclude={"owner_token"}))
                yield SimpleNamespace(s=s, h=h, bridge=bridge, outcome=outcome,
                    source=source, requests=requests, effects=effects, config=config)


def require_originals(p, cur, *, expected=None, source=None, graph_input=None):
    module = importlib.import_module("src.application.deep_agents.native_approval_batch")
    return module.require_pending_tool_interrupts_original(cur, p.h.settings, source or p.source,
        graph_input or p.s.asset.graph_input,
        p.outcome.graph_outcome.native_interrupts if expected is None else expected)


def native_originals(p):
    with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
        values = {}
        for table in ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            cur.execute(f"SELECT to_jsonb(row) AS original FROM {p.h.settings.deep_agents.checkpoint_schema}.{table} row "
                "ORDER BY to_jsonb(row)::text")
            values[table] = [row["original"] for row in cur.fetchall()]
        return values


@asynccontextmanager
async def projected(p):
    owner = p.h.task.runtime_owner
    with framework_owner_scope(owner):
        context = p.bridge.runtime._deep_agent_governance_context(p.outcome.state)
        proposals = [p.bridge.runtime._tool_governance_service.prepare_framework_approval(owner.binding, item, context)
            for item in p.outcome.graph_outcome.native_interrupts]
        yield await p.bridge.projection.project_many(owner, proposals)


@pytest.mark.asyncio
async def test_complete_pending_batch_is_rechecked_without_second_connection_or_execution(pending_formal, monkeypatch, caplog):
    p = pending_formal
    caplog.set_level(logging.INFO, logger="src.application.deep_agents.native_approval_batch")
    saved_native = native_originals(p)
    before = database_digest(p.s), counts(p.h), await p.s.calls.snapshot(p.s.binding)
    with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
        configure_runtime_owner_transaction(cur, p.h.settings)
        assert_runtime_owner(cur, p.h.settings, p.h.task.runtime_owner)
        cur.execute("SELECT current_setting('search_path') AS path, pg_backend_pid() AS pid")
        original = cur.fetchone()

        def forbidden(*args, **kwargs):
            pytest.fail("Pending original read tried to open another connection, deploy or execute")

        import src.infrastructure.postgres_runtime as pg
        from langgraph.checkpoint.postgres import PostgresSaver
        from src.runtime.framework_approval_journal import FrameworkApprovalJournal
        from src.graph import controlled_case
        read_task = FrameworkApprovalJournal.pending_task_in_transaction
        observed = []

        def observe(slf, task_cur, *args):
            task_cur.execute("SELECT pg_backend_pid() AS pid")
            observed.append(task_cur.fetchone()["pid"])
            return read_task(slf, task_cur, *args)

        monkeypatch.setattr(pg, "postgres_connect", forbidden)
        monkeypatch.setattr(PostgresSaver, "setup", forbidden)
        monkeypatch.setattr(controlled_case, "build_controlled_case_graph", forbidden)
        monkeypatch.setattr(FrameworkApprovalJournal, "pending_task_in_transaction", observe)
        # Reversing candidates is harmless; native order remains canonical.
        assert require_originals(p, cur, expected=list(reversed(p.outcome.graph_outcome.native_interrupts))) == (
            p.outcome.graph_outcome.native_interrupts)
        cur.execute("SELECT current_setting('search_path') AS path, pg_backend_pid() AS pid")
        assert cur.fetchone() == original and observed == [original["pid"]] * 2
    assert (database_digest(p.s), counts(p.h), await p.s.calls.snapshot(p.s.binding)) == before
    assert native_originals(p) == saved_native
    assert len(p.requests) == 1 and p.effects == []
    assert (await p.s.h.tasks.get(p.h.task.id)).status == "running"
    assert (await p.s.d.runs.get_item(p.s.item.id)).status == "running"
    log, = [item for item in caplog.records if item.message == "framework_pending_approval_originals_read"]
    assert log.scope == "native_pending_identities_only" and log.interrupt_count == 2 and log.automatic_replay is False


@pytest.mark.asyncio
async def test_pending_original_read_does_not_commit_the_callers_staged_business_event(pending_formal):
    from src.schemas.session import ExecutionEvent

    p = pending_formal
    before = counts(p.h), native_originals(p)

    class RollbackProbe(Exception):
        pass

    with pytest.raises(RollbackProbe):
        with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
            configure_runtime_owner_transaction(cur, p.h.settings)
            assert_runtime_owner(cur, p.h.settings, p.h.task.runtime_owner)
            cur.execute("SELECT clock_timestamp() AS observed_at")
            observed_at = cur.fetchone()["observed_at"]
            p.h.store.append_event_in_transaction(cur, p.h.session_id,
                ExecutionEvent(session_id=p.h.session_id, timestamp=observed_at,
                    type="test.pending_read_outer_probe", payload={}),
                turn_lease_token=p.h.task.runtime_owner.owner_token)
            assert require_originals(p, cur) == p.outcome.graph_outcome.native_interrupts
            assert counts(p.h) == before[0]  # Independent reader cannot see a hidden commit.
            raise RollbackProbe
    assert (counts(p.h), native_originals(p)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["empty", "omitted", "duplicate", "arguments", "checkpoint", "namespace",
    "source_message", "task_path", "graph_task", "resume_index", "binding", "input"])
async def test_incomplete_or_drifted_pending_candidates_cannot_pass_original_recheck(pending_formal, change, caplog):
    p = pending_formal
    expected = deepcopy(p.outcome.graph_outcome.native_interrupts)
    source, graph_input = p.source, p.s.asset.graph_input
    if change == "empty":
        expected = []
    elif change == "omitted":
        expected.pop()
    elif change == "duplicate":
        expected.append(expected[0])
    elif change in {"arguments", "checkpoint", "namespace", "source_message", "task_path", "graph_task", "resume_index"}:
        key, value = {"arguments": ("arguments", {"path": "foreign.py"}), "checkpoint": ("checkpoint_id", "foreign"),
            "namespace": ("checkpoint_namespace", "tools:foreign"), "source_message": ("source_message_id", "foreign"),
            "task_path": ("task_path", "foreign"), "graph_task": ("graph_task_id", "foreign"),
            "resume_index": ("resume_index", 1)}[change]
        expected[0] = expected[0].model_copy(update={key: value})
    elif change == "binding":
        source = source.model_copy(update={"binding": source.binding.model_copy(update={"tool_schema_version": "foreign"})})
    else:
        from src.schemas.controlled_case_graph_input import parse_controlled_graph_input
        from src.schemas.framework_approval import approval_value_hash

        raw = graph_input.model_dump(mode="json")
        raw["system_prompt"] += " foreign"
        raw["asset_sha256"] = approval_value_hash({key: value for key, value in raw.items() if key != "asset_sha256"})
        graph_input = parse_controlled_graph_input(raw)
    before = database_digest(p.s), counts(p.h)
    with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
        cur.execute("SELECT current_setting('search_path') AS path")
        path = cur.fetchone()["path"]
        with pytest.raises(FrameworkApprovalRecoveryRequired):
            require_originals(p, cur, expected=expected, source=source, graph_input=graph_input)
        cur.execute("SELECT current_setting('search_path') AS path")
        assert cur.fetchone()["path"] == path
    assert (database_digest(p.s), counts(p.h)) == before
    assert "framework_pending_approval_originals_rejected" in caplog.text
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_formal_projection_attaches_only_durable_original_business_scope(pending_formal):
    p = pending_formal
    async with projected(p) as approvals:
        for item in approvals:
            assert {key: item.approval.metadata.get(key) for key in (
                "source", "project_id", "run_id", "run_item_id", "attempt_id")} == {
                "source": "test_run_agent_execution", "project_id": p.s.detail.run.project_id,
                "run_id": p.s.detail.run.id, "run_item_id": p.s.item.id, "attempt_id": p.s.attempt.id}
            assert "lease_token" not in item.approval.metadata and "owner_token" not in item.approval.metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("port", ["owned", "caller_transaction"])
async def test_formal_projection_itself_refuses_a_subset_of_the_saved_pending_batch(pending_formal, port):
    p = pending_formal
    owner = p.h.task.runtime_owner
    before = database_digest(p.s), counts(p.h)
    with framework_owner_scope(owner):
        context = p.bridge.runtime._deep_agent_governance_context(p.outcome.state)
        candidate = p.bridge.runtime._tool_governance_service.prepare_framework_approval(
            owner.binding, p.outcome.graph_outcome.native_interrupts[0], context)
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="complete pending"):
            if port == "owned":
                await p.bridge.projection.project_many(owner, [candidate])
            else:
                with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
                    configure_runtime_owner_transaction(cur, p.h.settings)
                    p.bridge.projection.project_many_in_transaction(cur, owner, [candidate])
    assert (database_digest(p.s), counts(p.h)) == before
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing_interrupt", "task_path", "consumed_no_result", "payload", "root_source", "task_error"])
async def test_pending_recheck_sees_uncommitted_native_changes_and_does_not_commit_or_repair_them(pending_formal, change):
    from langgraph.checkpoint.base import WRITES_IDX_MAP
    from src.application.deep_agents.checkpoint_provider import checkpoint_serializer
    from langgraph.types import Interrupt

    p = pending_formal
    pending = p.outcome.graph_outcome.native_interrupts[0]
    schema = p.h.settings.deep_agents.checkpoint_schema
    location = (p.source.binding.thread_id, p.source.binding.checkpoint_namespace, pending.checkpoint_id, pending.graph_task_id)
    before = database_digest(p.s), counts(p.h)

    class RollbackProbe(Exception):
        pass

    with pytest.raises(RollbackProbe):
        with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
            cur.execute("SELECT current_setting('search_path') AS path")
            path = cur.fetchone()["path"]
            # These are ONLY this fixture's exact random checkpoint tables.
            # All corruption is rolled back; a second connection would miss it.
            if change == "root_source":
                cur.execute(f"UPDATE {schema}.checkpoints SET metadata=jsonb_set(metadata,'{{source}}','\"update\"') "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s", location[:3])
            elif change in {"consumed_no_result", "task_error"}:
                channel, value = ("__resume__", [{"type": "reject", "message": "fixture undecided gap"}]) if (
                    change == "consumed_no_result") else ("__error__", "isolated unresolved native task error")
                kind, blob = checkpoint_serializer().dumps_typed(value)
                cur.execute(f"INSERT INTO {schema}.checkpoint_writes "
                    "(thread_id,checkpoint_ns,checkpoint_id,task_id,idx,channel,type,blob,task_path) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (*location, WRITES_IDX_MAP[channel], channel, kind, blob, pending.task_path))
            elif change == "payload":
                kind, blob = checkpoint_serializer().dumps_typed((Interrupt(id=pending.interrupt_id, value={
                    "type": "tool_approval", "tool_call_id": pending.tool_call_id,
                    "name": pending.tool_name, "args": {"path": "foreign.py"}}),))
                cur.execute(f"UPDATE {schema}.checkpoint_writes SET type=%s,blob=%s "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s AND task_id=%s AND channel='__interrupt__'",
                    (kind, blob, *location))
            elif change == "missing_interrupt":
                cur.execute(f"DELETE FROM {schema}.checkpoint_writes "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s AND task_id=%s AND channel='__interrupt__'",
                    location)
            else:
                cur.execute(f"UPDATE {schema}.checkpoint_writes SET task_path='foreign' "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s AND task_id=%s AND channel='__interrupt__'",
                    location)
            assert cur.rowcount == 1
            with pytest.raises(FrameworkApprovalRecoveryRequired):
                require_originals(p, cur)
            cur.execute("SELECT current_setting('search_path') AS path")
            assert cur.fetchone()["path"] == path
            raise RollbackProbe
    with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
        assert require_originals(p, cur) == p.outcome.graph_outcome.native_interrupts
    assert (database_digest(p.s), counts(p.h)) == before
    assert len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_source_only_pending_read_after_expiry_grants_no_formal_projection(pending_formal):
    p = pending_formal
    before = counts(p.h)
    assert await p.h.store.renew_turn_execution(p.h.session_id, p.h.task.runtime_owner.owner_token, 0)
    with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
        assert require_originals(p, cur) == p.outcome.graph_outcome.native_interrupts
    with pytest.raises(ContinuationLeaseLostError):
        async with projected(p):
            pytest.fail("Read-only source was upgraded to active dual-owner authority")
    assert counts(p.h) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["job_project", "job_attempt", "approval_source", "approval_run"])
async def test_formal_scope_is_not_derived_from_a_candidate_or_the_agent_attempt(pending_formal, change):
    p = pending_formal
    owner = p.h.task.runtime_owner
    with framework_owner_scope(owner):
        context = p.bridge.runtime._deep_agent_governance_context(p.outcome.state)
        proposals = [p.bridge.runtime._tool_governance_service.prepare_framework_approval(owner.binding, item, context)
            for item in p.outcome.graph_outcome.native_interrupts]
        if change == "job_project":
            proposals[1].job.metadata["project_id"] = "foreign"
        elif change == "job_attempt":
            proposals[1].job.metadata["attempt_id"] = owner.attempt_id
        elif change == "approval_source":
            proposals[1].approval.metadata["source"] = "ordinary"
        else:
            proposals[1].approval.metadata["run_id"] = "foreign"
        original = [item.model_dump(mode="json") for item in proposals]
        before = database_digest(p.s), counts(p.h)
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="scope"):
            await p.bridge.projection.project_many(owner, proposals)
        assert [item.model_dump(mode="json") for item in proposals] == original
    assert (database_digest(p.s), counts(p.h)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
async def test_formal_reprojection_does_not_repair_an_unmarked_old_approval_or_erase_a_decision(pending_formal):
    p = pending_formal
    async with projected(p) as approvals:
        chosen = approvals[0].approval
    chosen.metadata.pop("source")
    chosen.status = ToolApprovalStatus.denied
    chosen.decision_note = "retained original decision"
    await p.h.store.save_approval(p.h.session_id, chosen)
    before = saved_facts(p.h), counts(p.h)
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="different native projection"):
        async with projected(p):
            pytest.fail("Unmarked formal approval was silently migrated")
    assert (saved_facts(p.h), counts(p.h)) == before and len(p.requests) == 1 and p.effects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["project_id", "run_id", "run_item_id", "attempt_id"])
async def test_formal_reprojection_checks_retained_tooljob_business_scope_without_rewriting_it(pending_formal, field):
    from psycopg.types.json import Jsonb

    p = pending_formal
    async with projected(p) as approvals:
        job = approvals[1].job
    with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
        cur.execute(f"UPDATE {p.h.settings.database.postgres_tool_job_table} "
            "SET metadata=jsonb_set(metadata,%s,%s::jsonb) WHERE id=%s", ([field], Jsonb("foreign"), job.id))
    before = database_digest(p.s), counts(p.h), (await p.bridge.jobs.get_job(job.id)).model_dump(mode="json")
    with pytest.raises(FrameworkApprovalRecoveryRequired, match="ToolJob"):
        async with projected(p):
            pytest.fail("Retained foreign formal scope was silently accepted")
    assert (database_digest(p.s), counts(p.h), (await p.bridge.jobs.get_job(job.id)).model_dump(mode="json")) == before
    assert p.effects == [] and len(p.requests) == 1


@pytest.mark.asyncio
async def test_latest_pending_read_excludes_settled_sibling_and_rejects_retained_history_after_terminal(pending_formal):
    p = pending_formal
    a, b = (next(item for item in p.outcome.graph_outcome.native_interrupts if item.tool_call_id == key)
        for key in ("A", "B"))
    with framework_owner_scope(p.h.task.runtime_owner):
        tools, _ = await p.bridge.runtime._build_deep_agent_business_tools(state=p.outcome.state, tool_records=[])
        # Explicit native-kernel operation only. This is NOT a formal business
        # decision endpoint, parked continuation or Redis dispatch acceptance.
        partial = await p.h.adapter.execute_frozen(p.s.asset.graph_input, business_tools=tools,
            resume={b.interrupt_id: {"type": "reject", "message": "fixture-only"}})
        assert partial.native_interrupts == [a] and partial.native_terminal is None
        assert p.effects == [] and len(p.requests) == 1
        with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
            assert require_originals(p, cur, expected=[a]) == [a]
            with pytest.raises(FrameworkApprovalRecoveryRequired, match="complete pending"):
                require_originals(p, cur)
        terminal = await p.h.adapter.execute_frozen(p.s.asset.graph_input, business_tools=tools,
            resume={a.interrupt_id: {"type": "reject", "message": "fixture-only"}})
    assert terminal.native_terminal is not None and terminal.native_interrupts == []
    with postgres_connect(p.h.settings) as connection, connection.cursor() as cur:
        with pytest.raises(FrameworkApprovalRecoveryRequired, match="complete pending"):
            require_originals(p, cur, expected=[a])
    assert p.effects == [] and len(p.requests) == 2
    assert (await p.s.calls.snapshot(p.s.binding)).reserved_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_scope", [False, True])
async def test_generic_decision_does_not_take_over_a_formal_native_approval_even_without_old_scope_marker(
        pending_formal, remove_scope):
    p = pending_formal
    async with projected(p) as approvals:
        chosen = approvals[0].approval
        if remove_scope:
            # Retained pre-§207 approval: authoritative formal task must still
            # keep the generic entry from accepting a decision/continuation.
            for key in ("source", "project_id", "run_id", "run_item_id", "attempt_id"):
                chosen.metadata.pop(key, None)
            await p.h.store.save_approval(p.h.session_id, chosen)

        def waiting(metadata, status):
            metadata["pending_turn"] = {"turn_id": p.s.binding.turn_id,
                "pending_approval_ids": [item.approval.id for item in approvals],
                "context_bundle": {"runtime_binding": p.s.binding.model_dump(mode="json")}}
            metadata["control"] = {"control_state": "waiting_approval"}

        assert await p.h.store.update_status_and_mutate_metadata(p.h.session_id,
            status=SessionStatus.waiting_approval, allowed_current_statuses={SessionStatus.running},
            mutate=waiting, turn_lease_token=p.h.task.runtime_owner.owner_token)
        command = make_command(p.h, p.s.binding).model_copy(update={"operation": "approval_resume",
            "reference_id": chosen.id, "project_id": p.s.detail.run.project_id})
        before = saved_facts(p.h)
        with pytest.raises(AgentSubmissionConflict, match="dedicated"):
            await p.s.h.tasks.submit_approval_v2(command, chosen.id, ApprovalDecisionRequest(decision="approved"))
        assert saved_facts(p.h) == before and len(p.requests) == 1 and p.effects == []
