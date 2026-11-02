"""Formal native wait -> shared approval batch only, not business parking/resume."""
from copy import deepcopy

import pytest

from src.application.deep_agents import DeepAgentRuntimeResult
from src.application.runtime.controlled_case_runtime import ControlledCaseRuntime
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.storage_utils import make_json_safe
from src.runtime.execution_context import framework_owner_scope
from src.runtime.postgres_runtime_owner import configure_runtime_owner_transaction, runtime_owner_table
from src.runtime.store import ContinuationLeaseLostError
from tests.test_agent_task_dispatch import harness
from tests.test_controlled_case_admission import formal_case, pg_domain, prepare_domain
from tests.test_controlled_case_executor import execution, executor
from tests.test_controlled_case_graph import business_bridge
from tests.test_controlled_case_runtime import assembly, prepare
from tests.test_framework_approval_projection import counts
from tests.test_framework_chat_models import _config, _provider, _tool_reply


@pytest.mark.asyncio
@pytest.mark.parametrize("port", ["runtime", "caller_transaction"])
@pytest.mark.parametrize("authority", ["live", "agent_expired", "case_expired", "stripped", "late_case_expired"])
async def test_formal_native_siblings_use_one_projection_and_both_original_leases(
        formal_case, tmp_path, monkeypatch, port, authority):
    s = formal_case
    reply = _tool_reply("openai_chat_completions")
    calls = reply["choices"][0]["message"]["tool_calls"]
    calls[0]["id"], calls[0]["function"]["arguments"] = "A", '{"path":"a.py"}'
    other = deepcopy(calls[0])
    other["id"], other["function"]["arguments"] = "B", '{"path":"b.py"}'
    calls.append(other)
    with _provider("openai_chat_completions", first_reply=reply) as (url, requests):
        async def prepare_input(models):
            p = assembly(s, tmp_path / "assembly", models)
            descriptor = p.parts.tool_registry.get("inspect_source").model_copy(update={"permission_level": "ask"})
            p.parts.tool_registry.register_dynamic(descriptor, "inspect_source")
            return await prepare(s, p)
        async with execution(s, _config("openai_chat_completions", url), prepare_input=prepare_input) as h:
            effects = []
            async with business_bridge(h, effects, approval_required=True) as bridge:
                outcome = await ControlledCaseRuntime(runtime_service=bridge.runtime).execute(
                    h.task, executor=executor(s, h), resource_id="isolated-approval-batch", fencing_token=1)
                graph = outcome.graph_outcome
                assert graph.native_terminal is None and len(graph.native_interrupts) == 2
                assert outcome.runtime_result is None and outcome.tool_records == []
                assert len(requests) == 1 and effects == []
                assert await bridge.jobs.list_jobs(session_id=h.session_id) == []
                owner = h.task.runtime_owner
                assert owner.controlled_case_lease.run_item_execution_attempt_id == s.attempt.id
                state = deepcopy(s.asset.runtime_preparation.initial_state)
                attempt_before = await s.d.runs.get_execution_detail(s.detail.run.id, s.item.id, s.attempt.id)
                before = counts(h)
                if authority in {"agent_expired", "case_expired"}:
                    with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                        if authority == "agent_expired":
                            cur.execute(f"UPDATE {runtime_owner_table(h.settings)} SET expires_at=clock_timestamp()-interval '1 second' "
                                "WHERE binding_id=%s", (s.binding.binding_id,))
                        else:
                            cur.execute("SELECT clock_timestamp()-interval '1 second' AS expired")
                            s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": cur.fetchone()["expired"]}))
                elif authority == "stripped":
                    owner = owner.model_copy(update={"controlled_case_lease": None})
                elif authority == "late_case_expired":
                    append = h.store.append_event_in_transaction
                    seen = []
                    def expire_last(cur, session_id, event, **kwargs):
                        result = append(cur, session_id, event, **kwargs)
                        if event.type == "approval.created":
                            seen.append(event.payload["approval_id"])
                            if len(seen) == 2:
                                cur.execute("SELECT clock_timestamp()-interval '1 second' AS expired")
                                s.d.runs._write_item(cur, s.item.model_copy(update={"lease_expires_at": cur.fetchone()["expired"]}))
                        return result
                    monkeypatch.setattr(h.store, "append_event_in_transaction", expire_last)

                async def publish():
                    with framework_owner_scope(owner):
                        if port == "runtime":
                            result = DeepAgentRuntimeResult(output_text="", messages=[],
                                native_interrupts=graph.native_interrupts,
                                interrupts=[{"id": item.id, "value": item.value} for item in graph.values["__interrupt__"]])
                            return await bridge.runtime._record_deep_agent_interrupts(state, result)
                        proposals = [bridge.runtime._tool_governance_service.prepare_framework_approval(
                            s.binding, native, bridge.runtime._deep_agent_governance_context(state))
                            for native in graph.native_interrupts]
                        with postgres_connect(h.settings) as connection, connection.cursor() as cur:
                            configure_runtime_owner_transaction(cur, h.settings)
                            return [item.approval.model_dump(mode="python") for item in
                                bridge.projection.project_many_in_transaction(cur, owner, proposals)]
                if authority == "live":
                    projected = await publish()
                    assert counts(h) == {key: value+2 for key, value in before.items()}
                    assert {item["metadata"]["call_id"] for item in projected} == {"A", "B"}
                    stored_jobs = await bridge.jobs.list_jobs(session_id=h.session_id)
                    assert len(stored_jobs) == 2
                    for job in stored_jobs:
                        assert job.status.value == "waiting_approval" and job.started_at is None
                        assert job.metadata["framework_binding_id"] == s.binding.binding_id
                        assert job.metadata["project_id"] == s.detail.run.project_id
                        assert job.metadata["run_id"] == s.detail.run.id and job.metadata["run_item_id"] == s.item.id
                        assert job.metadata["attempt_id"] == s.attempt.id != h.task.attempt_id
                        assert job.metadata["framework_native_identity"] in [item.model_dump(mode="json")
                            for item in graph.native_interrupts]
                    assert make_json_safe(await publish()) == make_json_safe(projected)
                    assert counts(h) == {key: value+2 for key, value in before.items()}
                else:
                    with pytest.raises(ContinuationLeaseLostError):
                        await publish()
                    assert counts(h) == before
                    assert "deep_agent_approval_batch" not in state["context_bundle"]
                    assert "_deep_agent_waiting_records" not in state
                # Projection never parks/finalizes the original business item,
                # acknowledges delivery, delivers a decision or resumes a graph.
                current = await s.d.runs.get_execution_detail(s.detail.run.id, s.item.id, s.attempt.id)
                assert current.attempts == attempt_before.attempts and current.results == attempt_before.results
                assert (await s.h.tasks.get(h.task.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).status == "running"
                assert (await s.d.runs.get_item(s.item.id)).result_id is None
                assert effects == [] and len(requests) == 1
                assert (await s.calls.snapshot(s.binding)).reserved_calls == 1
