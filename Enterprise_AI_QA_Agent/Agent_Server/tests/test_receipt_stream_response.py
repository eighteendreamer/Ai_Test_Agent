"""Actual SDK streams at the durable receipt boundary, retaining old histories."""
import json
import os
from pathlib import Path

import pytest

from test_agent_case_receipt_correction import ReceiptAgent, finish_graph, cleanup, export
from test_agent_case_execution import adapter_environment, approval_continuation
from test_agent_case_assessment import business_http
from test_model_completion_http import provider, Registry
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
from src.core.config import get_settings
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.case_execution import CaseReceiptCorrectionCheckpoint
from src.schemas.model_config import ModelConfigRecord, ModelInvocationResult, ModelTransportOptions


def test_old_captured_response_checkpoint_is_byte_semantically_unchanged():
    path = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-ui-observation-default5/positive/final-run.json"
    raw = json.loads(path.read_bytes())["results"][0]["actual"]["execution_checkpoint"]["receipt_correction"]
    assert "response_transport_protocol" not in raw
    saved = CaseReceiptCorrectionCheckpoint.model_validate(raw)
    assert saved.response_transport_protocol is None
    assert saved.model_dump(mode="json") == raw


def test_captured_default_numeric_path_is_rejected_without_conversion():
    path = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p2-receipt-stream-default-fixed/correction-response.json"
    response = ModelInvocationResult.model_validate(json.loads(path.read_bytes()))
    raw = json.loads(response.text)
    assert type(raw["steps"][1]["checks"][0]["expected_path_tokens"][1]) is int
    with pytest.raises(ValueError, match="string"):
        AgentCaseAssessmentService.parse_response(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["openai_chat_completions", "openai_responses", "anthropic_messages",
    "google_gemini_generate_content", "langchain_chat"])
@pytest.mark.parametrize("variant", ["complete", "missing-terminal", "budget-limited"])
async def test_actual_stream_receipt_never_reexecutes_business(tmp_path, business_http, transport, variant):
    actual_transport = "openai_chat_completions" if transport == "langchain_chat" else transport
    holder = {}
    with provider(actual_transport, variant == "budget-limited", stream_variant=variant,
        content_supplier=lambda: json.loads(holder["agent"].good.text)) as (endpoint, sends, replies):
            config = ModelConfigRecord(key="controlled-case-agent", name="receipt stream fixture",
                provider="google" if transport.startswith("google") else "anthropic" if transport.startswith("anthropic") else "openai",
                transport=actual_transport, model_id="fixture-model", api_base_url=endpoint,
                api_key="local-fixture-marker", max_output_tokens=512)
            settings = get_settings().model_copy(deep=True)
            settings.model.langchain_model_adapter_enabled = transport == "langchain_chat"
            runtime = ModelRuntimeService(Registry(config), settings)
            class StreamReceiptAgent(ReceiptAgent):
                def get_model_config(self, key): return config
                def get_invocation_runtime_parameters(self, key, **kwargs):
                    return runtime.get_invocation_runtime_parameters(key, **kwargs)
                def stream_handler(self, handler): return runtime.stream_handler(handler)
                async def invoke(self, key, request, **kwargs):
                    if request.trace_context.get("phase") == "case_receipt_correction":
                        self.correction_requests.append(request)
                        return await runtime.invoke(key, request, **kwargs)
                    return await super().invoke(key, request, **kwargs)
            agent = StreamReceiptAgent()
            holder["agent"] = agent
            policy = CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1",
                receipt_correction_transport_options=None if transport.startswith("google") else
                    ModelTransportOptions(timeout_seconds=5, max_retries=0))
            async with adapter_environment(tmp_path, business_http, False, agent=agent, planning_policy=policy) as values:
                _, _, reader, _, runs, _, adapter, args, _, _ = values
                try:
                    pending = await finish_graph(values)
                    if variant == "complete":
                        assert (await approval_continuation(runs, reader, adapter, args, pending)).completion.status == "passed"
                    else:
                        with pytest.raises(ValueError):
                            await approval_continuation(runs, reader, adapter, args, pending)
                    checkpoint = (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload["receipt_correction"]
                    assert checkpoint["phase"] == "response"
                    assert checkpoint["response_transport_protocol"] == "case-receipt-stream-response-v1"
                    assert checkpoint["total_model_calls"] == 5 and checkpoint["max_iterations"] == 8
                    assert len(sends) == len(agent.correction_requests) == 1
                    assert [method for method, _ in business_http[1]] == ["POST", "GET", "DELETE"]
                    if variant == "complete":
                        assert (await adapter().execute(**await args())).completion.status == "passed"
                    else:
                        with pytest.raises(ValueError): await adapter().execute(**await args())
                    assert len(sends) == 1 and len(business_http[1]) == 3
                    await export(values, name="stream-" + transport + "-" + variant, effects=business_http[1])
                    if output := os.getenv("QA_RECEIPT_STREAM_DIR"):
                        directory = Path(output) / (transport + "-" + variant)
                        directory.mkdir(parents=True, exist_ok=False)
                        (directory / "physical-request.json").write_text(json.dumps(sends, ensure_ascii=False, indent=2), encoding="utf8")
                        (directory / "physical-response.bin").write_bytes(replies[0])
                        (directory / "checkpoint.json").write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf8")
                finally: await cleanup(values)
