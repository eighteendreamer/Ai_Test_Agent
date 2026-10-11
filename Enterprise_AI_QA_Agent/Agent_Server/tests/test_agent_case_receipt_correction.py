"""Real HTTP and isolated stores: receipt correction must never rerun operations."""
import json
import os
from time import monotonic
from pathlib import Path

import pytest

from test_agent_case_assessment import business_http
from test_agent_case_execution import adapter_environment, approval_continuation
from test_agent_case_session import ControlledAgent
from test_case_plan_run_creation import LIVE
from src.application.test_runs.agent_case_execution import AgentCaseApprovalWait, AgentCaseRecoveryRequired
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.model_config import ModelConfigRecord, ModelInvocationResult, ModelTransportOptions, model_invocation_parameters
from src.schemas.tool_runtime import ModelToolCall

DOCS = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03"


class ReceiptAgent(ControlledAgent):
    def __init__(self, *, corrected="valid", original="captured", interrupt=False):
        super().__init__()
        self.corrected, self.original, self.interrupt = corrected, original, interrupt
        self.correction_requests = []
        self.good = None

    def get_model_config(self, key):
        return ModelConfigRecord(key=key, name="Controlled receipt model", provider="test",
            transport="openai_chat_completions", model_id="controlled", api_base_url="http://unused",
            api_key="DO-NOT-PERSIST-RECEIPT-CREDENTIAL")

    def get_default_model_config(self):
        return self.get_model_config("controlled-case-agent")

    def get_invocation_runtime_parameters(self, key, *, transport_options=None):
        data = {"fixture": "controlled-v1", "timeout_seconds": 60}
        if transport_options is not None:
            data["transport_options"] = transport_options.model_dump(mode="json")
        return data

    async def invoke(self, key, request, **kwargs):
        if request.trace_context.get("phase") == "case_receipt_correction":
            assert not request.tools and "Do not call tools or repeat operations" in request.system_prompt
            assert kwargs["expected_parameters"] == model_invocation_parameters(self.get_model_config(key))
            assert kwargs["expected_runtime_parameters"] == self.get_invocation_runtime_parameters(
                key, transport_options=kwargs.get("transport_options"))
            self.correction_requests.append(request)
            if self.interrupt:
                raise RuntimeError("controlled response lost after receipt admission")
            if self.corrected == "invalid":
                return ModelInvocationResult(text="Still not a receipt", response_summary={"mode": "ok", "finish_reason": "stop"})
            if self.corrected == "tool":
                return ModelInvocationResult(text=self.good.text, response_summary={"mode": "ok", "finish_reason": "stop"},
                    tool_calls=[ModelToolCall(id="forbidden", name="api-test-runner", arguments={})])
            data = json.loads(self.good.text)
            if self.corrected == "fake-reference":
                data["steps"][1]["checks"][0]["observations"][0]["call_id"] = "not-this-execution"
            return ModelInvocationResult(text=json.dumps(data, ensure_ascii=False),
                response_summary={"mode": "ok", "finish_reason": "stop"}, raw_response={"controlled": True})
        response = await super().invoke(key, request)
        if not response.tool_calls:
            self.good = response
            if self.original == "valid":
                return response
            if self.original == "truncated":
                return ModelInvocationResult(text=response.text[:30], response_summary={"mode": "ok", "finish_reason": "length"})
            if self.original == "captured-current":
                original = json.loads((DOCS / "p2-receipt-executor-before/PG-public-receipt-snapshots.json").read_bytes())[-1]["model_response_text"]
                return ModelInvocationResult(text=original, response_summary={"mode": "ok", "finish_reason": "stop"})
            tables = json.loads((DOCS / "p0-tool-evidence-production-final/trusted-parent/postgres-execution.json").read_bytes())
            original = tables["tables"]["postgres_snapshot_table"]["records"][-1]["graph_state"]["model_response_text"]
            return ModelInvocationResult(text=original, response_summary={"mode": "ok", "finish_reason": "stop"})
        return response


async def finish_graph(values):
    _, _, reader, _, runs, _, adapter, args, _, _ = values
    pending = await adapter().execute(**await args())
    for _ in range(2):
        assert isinstance(pending, AgentCaseApprovalWait)
        pending = await approval_continuation(runs, reader, adapter, args, pending)
    assert isinstance(pending, AgentCaseApprovalWait)
    return pending


async def export(values, *, name, effects):
    if not os.getenv("QA_RECEIPT_CORRECTION_DIR"):
        return
    env, _, reader, _, runs, agent, _, args, _, services = values
    item = (await args())["item"]
    attempt = await runs.get_latest_attempt(item.id)
    cp = attempt.checkpoint_payload
    evidence = {"checkpoint": cp, "graph_calls": len(agent.requests), "correction_calls": len(agent.correction_requests),
        "actual_effects": effects,
        "snapshots": [row.model_dump(mode="json") for row in await reader.list_snapshots(cp["session_id"], include_graph_state=True)]}
    evidence["jobs"] = [(await env[1].get_job_detail(row["job_id"])).model_dump(mode="json")
        for row in evidence["snapshots"][-1]["graph_state"]["tool_results"] if row.get("job_id")]
    correction = cp.get("receipt_correction")
    if correction:
        for key in ("request", "response"):
            if correction.get(key):
                content = (await services["environment"].objects.read_object_uri(correction[key]["uri"]))["content"]
                evidence[key + "_raw"] = json.loads(content)
    target = Path(os.environ["QA_RECEIPT_CORRECTION_DIR"])
    target.mkdir(parents=True, exist_ok=True)
    (target / (name + ".json")).write_text(json.dumps(evidence, ensure_ascii=False, default=str), encoding="utf-8")


async def cleanup(values):
    _, _, _, _, runs, _, _, args, _, services = values
    cp = (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload.get("receipt_correction")
    if cp:
        for key in ("request", "response"):
            if cp.get(key):
                await services["environment"].objects.delete_object_uri(cp[key]["uri"])


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("original", ["captured", "captured-current"])
async def test_actual_captured_mixed_report_corrects_from_owned_evidence_once_then_replays(tmp_path, business_http, live, original):
    agent = ReceiptAgent(original=original)
    async with adapter_environment(tmp_path, business_http, live, agent=agent,
        planning_policy=CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1")) as values:
        _, _, reader, _, runs, _, adapter, args, _, services = values
        try:
            pending = await finish_graph(values)
            outcome = await approval_continuation(runs, reader, adapter, args, pending)
            assert outcome.completion.status == "passed"
            assert len(agent.requests) == 4 and len(agent.correction_requests) == 1
            fixed_output = agent.correction_requests[0].output_format
            assert fixed_output.kind == "json_schema" and fixed_output.required is False
            assert fixed_output.schema_name == "agent_case_receipt"
            assert [method for method, _ in business_http[1]] == ["POST", "GET", "DELETE"]
            cp = (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload["receipt_correction"]
            assert cp["phase"] == "response" and cp["execution_model_calls"] == 4 and cp["total_model_calls"] == 5
            assert cp["max_iterations"] == 8 and "api_key" not in cp["model_parameters"]
            assert (await adapter().execute(**await args())).completion.status == "passed"
            assert len(agent.correction_requests) == 1 and len(business_http[1]) == 3
            for key in ("request", "response"):
                raw = (await services["environment"].objects.read_object_uri(cp[key]["uri"]))["content"]
                assert b"DO-NOT-PERSIST-RECEIPT-CREDENTIAL" not in raw
            await export(values, name=original + ("-passed-postgres" if live else "-passed-memory"), effects=business_http[1])
        finally:
            await cleanup(values)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("failure", ["invalid", "fake-reference", "tool", "truncated", "budget", "unknown", "disabled"])
async def test_rejects_bad_or_unknown_correction_without_repeating_business(tmp_path, business_http, failure, live):
    agent = ReceiptAgent(corrected=failure if failure in {"invalid", "fake-reference", "tool"} else "valid",
        original="truncated" if failure == "truncated" else "captured", interrupt=failure == "unknown")
    policy = CasePlanningPolicy(receipt_correction_protocol=None if failure == "disabled" else "case-receipt-correction-v1")
    async with adapter_environment(tmp_path, business_http, live, agent=agent, planning_policy=policy) as values:
        _, _, reader, sessions, runs, _, adapter, args, _, _ = values
        try:
            if failure == "budget":
                sessions._runtime_service._max_iterations = 4
            pending = await finish_graph(values)
            with pytest.raises((ValueError, RuntimeError)):
                await approval_continuation(runs, reader, adapter, args, pending)
            before = len(agent.requests), len(agent.correction_requests), list(business_http[1])
            with pytest.raises(AgentCaseRecoveryRequired if failure == "unknown" else ValueError):
                await adapter().execute(**await args())
            assert (len(agent.requests), len(agent.correction_requests), business_http[1]) == before
            assert len(business_http[1]) == 3
            assert len(agent.correction_requests) == (1 if failure in {"invalid", "fake-reference", "tool", "unknown"} else 0)
            await export(values, name=failure + ("-postgres" if live else "-memory"), effects=business_http[1])
        finally:
            await cleanup(values)


@pytest.mark.asyncio
async def test_legal_original_receipt_does_not_add_a_readonly_send(tmp_path, business_http):
    agent = ReceiptAgent(original="valid")
    async with adapter_environment(tmp_path, business_http, agent=agent,
        planning_policy=CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1")) as values:
        _, _, reader, _, runs, _, adapter, args, _, _ = values
        pending = await finish_graph(values)
        outcome = await approval_continuation(runs, reader, adapter, args, pending)
        assert outcome.completion.status == "passed" and not agent.correction_requests
        assert "receipt_correction" not in (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload
        assert (await adapter().execute(**await args())).completion.status == "passed"
        assert len(agent.requests) == 4 and len(business_http[1]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
async def test_foreign_owned_job_metadata_is_rejected_before_readonly_admission(tmp_path, business_http, live):
    agent = ReceiptAgent()
    async with adapter_environment(tmp_path, business_http, live, agent=agent,
        planning_policy=CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1")) as values:
        env, _, reader, _, runs, _, adapter, args, _, _ = values
        pending = await finish_graph(values)
        snapshot = await reader.get_latest_snapshot(pending.session_id)
        record = next(row for row in snapshot.graph_state["tool_results"] if row["call_id"] == "seed")
        job = await env[1]._store.get_job(record["job_id"])
        job.metadata["project_id"] = "different-project"
        await env[1]._store.save_job(job)
        with pytest.raises(ValueError, match="工具证据与本次项目"):
            await approval_continuation(runs, reader, adapter, args, pending)
        assert len(business_http[1]) == 3 and not agent.correction_requests
        assert "receipt_correction" not in (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload
        await export(values, name="foreign-job-" + ("postgres" if live else "memory"), effects=business_http[1])


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_RECEIPT_CORRECTION_MODEL") != "1", reason="requires actual configured model and isolated PG/MySQL/RustFS")
@pytest.mark.parametrize("explicit", [False, True], ids=["provider-defaults", "single-attempt"])
async def test_default_provider_readonly_correction_uses_owned_real_api_evidence(tmp_path, business_http, monkeypatch, explicit):
    import openai
    from src.application.models.model_runtime_service import ModelRuntimeService
    from src.infrastructure.model_config_store import MySQLModelConfigStore
    from src.registry.models import ModelRegistry
    from src.core.config import get_settings
    settings = get_settings().model_copy(deep=True)
    runtime = ModelRuntimeService(model_registry=ModelRegistry(MySQLModelConfigStore(settings)), settings=settings)
    model = runtime.get_default_model_config()
    assert model is not None
    attempts, clients = [], []
    original_sdk = openai.AsyncOpenAI.__init__
    def capture_sdk(client, *args, **kwargs):
        original_sdk(client, *args, **kwargs)
        clients.append({"max_retries": client.max_retries, "timeout_seconds": kwargs.get("timeout")})
        original_send = client._client.send
        async def send(request, *send_args, **send_kwargs):
            record = {"request": json.loads(request.content), "started": monotonic()}
            attempts.append(record)
            try:
                response = await original_send(request, *send_args, **send_kwargs)
                record["status_code"] = response.status_code
                return response
            except Exception as exc:
                record["error_type"] = type(exc).__name__
                raise
            finally:
                record["elapsed_seconds"] = monotonic() - record.pop("started")
        client._client.send = send
    monkeypatch.setattr(openai.AsyncOpenAI, "__init__", capture_sdk)
    class ActualReceiptAgent(ReceiptAgent):
        # The graph is controlled; only the one correction uses the real provider.
        def get_model_config(self, key): return runtime.get_model_config(model.key)
        def get_invocation_runtime_parameters(self, key, **kwargs): return runtime.get_invocation_runtime_parameters(model.key, **kwargs)
        async def invoke(self, key, request, **kwargs):
            if request.trace_context.get("phase") == "case_receipt_correction":
                self.correction_requests.append(request)
                return await runtime.invoke(model.key, request, **kwargs)
            return await super().invoke(key, request, **kwargs)
    agent = ActualReceiptAgent()
    async with adapter_environment(tmp_path, business_http, True, agent=agent,
        planning_policy=CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1",
            receipt_correction_transport_options=ModelTransportOptions(
                timeout_seconds=settings.model.llm_request_timeout_seconds, max_retries=0) if explicit else None)) as values:
        _, _, reader, _, runs, _, adapter, args, _, _ = values
        try:
            pending = await finish_graph(values)
            outcome = await approval_continuation(runs, reader, adapter, args, pending)
            assert outcome.completion.status == "passed"
            assert len(agent.correction_requests) == 1 and len(business_http[1]) == 3
            assert (await adapter().execute(**await args())).completion.status == "passed"
            assert len(agent.correction_requests) == 1 and len(business_http[1]) == 3
            if explicit:
                assert len(attempts) == 1 and clients[0]["max_retries"] == 0
        finally:
            await export(values, name="actual-default-provider", effects=business_http[1])
            if output := os.getenv("QA_RECEIPT_CORRECTION_DIR"):
                (Path(output) / "actual-default-transport.json").write_text(json.dumps({
                    "scope": "controlled graph; only correction uses actual default provider",
                    "physical_attempts": attempts, "sdk_clients": clients,
                    "logical_correction_calls": len(agent.correction_requests), "effects": business_http[1]},
                    ensure_ascii=False, indent=2), encoding="utf-8")
            await cleanup(values)


class InjectedReceiptCrash(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("phase", ["invoking", "response", "tampered"])
async def test_saved_receipt_intent_or_response_recovers_without_a_second_send(tmp_path, business_http, monkeypatch, live, phase):
    agent = ReceiptAgent()
    async with adapter_environment(tmp_path, business_http, live, agent=agent,
        planning_policy=CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1")) as values:
        _, _, reader, _, runs, _, adapter, args, _, _ = values
        try:
            pending = await finish_graph(values)
            original = runs.save_checkpoint
            async def crash_after_commit(item_id, payload):
                saved = await original(item_id, payload)
                receipt = payload.checkpoint_payload.get("receipt_correction")
                if receipt and receipt["phase"] == ("response" if phase == "tampered" else phase):
                    raise InjectedReceiptCrash("actual checkpoint committed before simulated process loss")
                return saved
            monkeypatch.setattr(runs, "save_checkpoint", crash_after_commit)
            with pytest.raises(InjectedReceiptCrash):
                await approval_continuation(runs, reader, adapter, args, pending)
            monkeypatch.setattr(runs, "save_checkpoint", original)
            assert len(agent.requests) == 4 and len(business_http[1]) == 3
            if phase == "invoking":
                with pytest.raises(AgentCaseRecoveryRequired, match="只读回执调用已有意图"):
                    await adapter().execute(**await args())
                assert not agent.correction_requests
            elif phase == "tampered":
                objects = values[-1]["environment"].objects
                cp = (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload["receipt_correction"]
                saved = await objects.read_object_uri(cp["response"]["uri"])
                if live:
                    await objects.store_uploaded_bytes(content=saved["content"] + b" ", filename="response.json",
                        object_prefix="", bucket_name=saved["bucket"], object_name=saved["object_name"], content_type="application/json")
                else:
                    objects.contents[cp["response"]["uri"]] = saved["content"] + b" "
                with pytest.raises(ValueError, match="大小或哈希"):
                    await adapter().execute(**await args())
                assert len(agent.correction_requests) == 1
            else:
                # Recovery of a saved response must not need today's provider config.
                monkeypatch.setattr(agent, "get_model_config", lambda key: None)
                outcome = await adapter().execute(**await args())
                assert outcome.completion.status == "passed" and len(agent.correction_requests) == 1
            await export(values, name="crash-" + phase + ("-postgres" if live else "-memory"), effects=business_http[1])
        finally:
            await cleanup(values)
