"""Measure physical SDK attempts before changing receipt transport policy."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_document_impact_calls import provider
from test_model_clients import _config, _request
from src.application.models.model_runtime_service import ModelRuntimeService
from src.schemas.model_config import ModelTransportOptions
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.case_execution import CaseReceiptCorrectionCheckpoint
from test_agent_case_receipt_correction import ReceiptAgent, finish_graph, export, cleanup
from test_agent_case_execution import adapter_environment, approval_continuation
from test_agent_case_assessment import business_http
from test_case_plan_run_creation import LIVE
from pydantic import ValidationError


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_actual_connection_loss_counts_sdk_attempts(explicit):
    with provider(mode="connection_closed") as (endpoint, sends, _, _):
        config = _config(api_base_url=endpoint)
        settings = SimpleNamespace(model=SimpleNamespace(langchain_model_adapter_enabled=False,
            llm_request_timeout_seconds=.2))
        runtime = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config), settings)
        options = ModelTransportOptions(timeout_seconds=.2, max_retries=0) if explicit else None
        parameters = runtime.get_invocation_runtime_parameters(config.key, transport_options=options)
        response = await runtime.invoke(config.key, _request(), expected_runtime_parameters=parameters,
            transport_options=options)
        assert response.response_summary["mode"] == "http_error"
        assert len(sends) == (1 if explicit else 3)
        if output := os.getenv("QA_RECEIPT_TRANSPORT_DIR"):
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / ("explicit.json" if explicit else "provider-defaults.json")).write_text(
                json.dumps({"runtime_parameters": parameters, "physical_http_requests": sends,
                    "response": response.model_dump(mode="json", exclude={"request_payload"})},
                    ensure_ascii=False, indent=2), encoding="utf-8")


def test_receipt_settings_keep_old_serialization_and_require_enabled_protocol():
    old = CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1")
    assert "receipt_correction_transport_options" not in old.model_dump(mode="json")
    assert CasePlanningPolicy.model_validate(old.model_dump(mode="json")).model_dump(mode="json") == old.model_dump(mode="json")
    with pytest.raises(ValidationError, match="需要启用"):
        CasePlanningPolicy(receipt_correction_transport_options=ModelTransportOptions(timeout_seconds=60, max_retries=0))
    raw = json.loads((Path(__file__).resolve().parents[2] /
        "docs/upgrade_2026-10-03/p0-receipt-correction-plan-review/passed-postgres.json").read_bytes())["checkpoint"]["receipt_correction"]
    with pytest.raises(ValidationError, match="阶段不一致"):
        CaseReceiptCorrectionCheckpoint.model_validate({**raw, "phase": "invoking"})
    with pytest.raises(ValidationError, match="只消耗一次"):
        CaseReceiptCorrectionCheckpoint.model_validate({**raw, "total_model_calls": raw["total_model_calls"] + 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, LIVE])
@pytest.mark.parametrize("mode", ["slow", "connection_closed"])
async def test_frozen_receipt_transport_real_sdk_and_recovery(tmp_path, business_http, live, mode):
    holder = {}
    def reply(body):
        time.sleep(.3)
        assert not body.get("tools")
        assert "receipt_correction_transport_options" not in json.dumps(body)
        return holder["agent"].good.text
    with provider(mode="connection_closed" if mode == "connection_closed" else "ok", responder=reply) as (endpoint, sends, _, _):
        config = _config(key="controlled-case-agent", api_base_url=endpoint)
        runtime = ModelRuntimeService(SimpleNamespace(get_runtime_config=lambda key: config),
            SimpleNamespace(model=SimpleNamespace(langchain_model_adapter_enabled=False, llm_request_timeout_seconds=.1)))
        class ActualReceiptAgent(ReceiptAgent):
            def get_model_config(self, key): return config
            def get_invocation_runtime_parameters(self, key, **kwargs):
                return runtime.get_invocation_runtime_parameters(key, **kwargs)
            async def invoke(self, key, request, **kwargs):
                if request.trace_context.get("phase") == "case_receipt_correction":
                    self.correction_requests.append(request)
                    return await runtime.invoke(key, request, **kwargs)
                assert "receipt_correction_transport_options" not in request.system_prompt
                return await super().invoke(key, request, **kwargs)
        agent = ActualReceiptAgent()
        holder["agent"] = agent
        policy = CasePlanningPolicy(receipt_correction_protocol="case-receipt-correction-v1",
            receipt_correction_transport_options=ModelTransportOptions(timeout_seconds=1, max_retries=0))
        async with adapter_environment(tmp_path, business_http, live, agent=agent, planning_policy=policy) as values:
            _, _, reader, _, runs, _, adapter, args, _, _ = values
            try:
                pending = await finish_graph(values)
                if mode == "slow":
                    outcome = await approval_continuation(runs, reader, adapter, args, pending)
                    assert outcome.completion.status == "passed"
                else:
                    with pytest.raises(ValueError, match="模型调用未成功"):
                        await approval_continuation(runs, reader, adapter, args, pending)
                cp = (await runs.get_latest_attempt((await args())["item"].id)).checkpoint_payload["receipt_correction"]
                assert cp["runtime_parameters"]["transport_options"] == {"timeout_seconds": 1, "max_retries": 0}
                assert cp["total_model_calls"] == 5 and cp["max_iterations"] == 8
                assert len(sends) == len(agent.correction_requests) == 1 and len(business_http[1]) == 3
                runtime._settings.model.llm_request_timeout_seconds = 99
                if mode == "slow":
                    assert (await adapter().execute(**await args())).completion.status == "passed"
                else:
                    with pytest.raises(ValueError, match="模型调用未成功"):
                        await adapter().execute(**await args())
                assert len(sends) == 1 and len(business_http[1]) == 3
                await export(values, name="sdk-" + mode + ("-postgres" if live else "-memory"), effects=business_http[1])
                if output := os.getenv("QA_RECEIPT_TRANSPORT_DIR"):
                    directory = Path(output); directory.mkdir(parents=True, exist_ok=True)
                    (directory / ("sdk-" + mode + ("-postgres" if live else "-memory") + ".json")).write_text(
                        json.dumps({"physical_requests": sends, "checkpoint": cp, "effects": business_http[1]},
                            ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                await cleanup(values)
