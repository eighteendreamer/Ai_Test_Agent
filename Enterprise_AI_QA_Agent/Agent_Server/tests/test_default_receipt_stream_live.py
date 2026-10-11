"""Real default correction after controlled graph operations and actual HTTP/PG."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from test_agent_case_receipt_correction import ReceiptAgent, finish_graph, cleanup
from test_agent_case_execution import adapter_environment, approval_continuation
from test_agent_case_assessment import business_http
from test_model_completion_http import Registry
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.model_clients.openai_client import OpenAIChatClient
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.model_config import model_invocation_parameters
from tests.default_model_probe import install


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_DEFAULT_RECEIPT_STREAM") != "1",
    reason="requires authenticated default provider, actual MySQL/PG/RustFS and owned HTTP")
async def test_default_correction_uses_frozen_stream_and_saved_response_once(tmp_path, business_http, monkeypatch):
    output = Path(os.environ["QA_RECEIPT_STREAM_DEFAULT_DIR"])
    output.mkdir(parents=True, exist_ok=False)
    settings = get_settings().model_copy(deep=True)
    original = MySQLModelConfigStore(settings).get_default_active()
    assert original and original.capabilities.streaming
    # The controlled graph uses this local registry alias. Provider/model/profile,
    # capability and all invocation settings remain the actual default values.
    config = original.model_copy(update={"key": "controlled-case-agent"})
    assert {k:v for k,v in model_invocation_parameters(config).items() if k != "key"} == {
        k:v for k,v in model_invocation_parameters(original).items() if k != "key"}
    (output / "model-parameters.json").write_text(json.dumps(model_invocation_parameters(original),
        ensure_ascii=False, indent=2), encoding="utf8")
    monkeypatch.setattr(OpenAIChatClient, "_make_client", OpenAIChatClient._make_client)
    install(output / "physical")
    runtime = ModelRuntimeService(Registry(config), settings)
    class DefaultReceiptAgent(ReceiptAgent):
        def get_model_config(self, key): return config
        def get_invocation_runtime_parameters(self, key, **kwargs):
            return runtime.get_invocation_runtime_parameters(key, **kwargs)
        def stream_handler(self, handler): return runtime.stream_handler(handler)
        async def invoke(self, key, request, **kwargs):
            if request.trace_context.get("phase") == "case_receipt_correction":
                self.correction_requests.append(request)
                return await runtime.invoke(key, request, **kwargs)
            return await super().invoke(key, request, **kwargs)
    agent = DefaultReceiptAgent(original="captured-current")
    policy = CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1")
    async with adapter_environment(tmp_path, business_http, True, agent=agent, planning_policy=policy) as values:
        _, _, reader, _, runs, _, adapter, args, _, services = values
        try:
            pending = await finish_graph(values)
            outcome = await approval_continuation(runs, reader, adapter, args, pending)
            assert outcome.completion.status == "passed"
            checkpoint = (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload["receipt_correction"]
            assert checkpoint["phase"] == "response" and checkpoint["response_transport_protocol"] == "case-receipt-stream-response-v1"
            assert checkpoint["total_model_calls"] == 5 and checkpoint["max_iterations"] == 8
            assert len(agent.correction_requests) == 1 and len(business_http[1]) == 3
            physical = list((output / "physical").glob("*.request.json"))
            assert len(physical) == 1
            body = json.loads(physical[0].read_bytes())
            assert body["stream"] is True and not body.get("tools")
            assert (await adapter().execute(**await args())).completion.status == "passed"
            assert len(list((output / "physical").glob("*.request.json"))) == 1 and len(business_http[1]) == 3
            (output / "verification.json").write_text(json.dumps({
                "scope": "controlled original graph; actual HTTP operations, default correction, PG/RustFS; no independent process or full default planner",
                "default_correction": True, "status": "passed", "physical_model_requests": 1,
                "terminal_extra_business_requests": 0, "terminal_extra_model_requests": 0,
                "effects": business_http[1]}, ensure_ascii=False, indent=2), encoding="utf8")
        finally:
            checkpoint = (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload
            (output / "checkpoint.json").write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf8")
            references = []
            for kind, ref in checkpoint.get("receipt_correction", {}).items():
                if kind not in {"request", "response"} or not ref: continue
                raw = (await services["environment"].objects.read_object_uri(ref["uri"]))["content"]
                assert hashlib.sha256(raw).hexdigest() == ref["sha256"] and len(raw) == ref["size_bytes"]
                p = output / ("correction-" + kind + ".json")
                if kind == "response":
                    response = json.loads(raw)
                    p.write_text(json.dumps({k:response[k] for k in ("text", "tool_calls", "response_summary")},
                        ensure_ascii=False, indent=2), encoding="utf8")
                else: p.write_bytes(raw)
                references.append({**ref, "kind": kind, "original_hash_verified": True,
                    "saved_relative_path": p.name, "response_public_projection": kind == "response"})
            (output / "downloads.json").write_text(json.dumps(references, ensure_ascii=False, indent=2), encoding="utf8")
            await cleanup(values)
