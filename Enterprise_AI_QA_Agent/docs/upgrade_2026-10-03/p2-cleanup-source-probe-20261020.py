"""Counterfactual read-only replay; never repair or resume the original plan."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Agent_Server"))
sys.path.insert(0, str(ROOT / "Agent_Server" / "tests"))

from default_model_probe import install
from src.application.model_clients.openai_client import OpenAIChatClient
from src.application.test_cases.planning_effect_tasks import (
    accept_task_decision, decisions_for_protocol, task_roles,
)
from src.application.test_cases.planning_stages import seal_logical_decision
from src.application.test_cases.planning_sources import json_text
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.case_planning_effect_tasks import EffectTaskCheckpoint
from src.schemas.case_planning_stages import PlanningStageCheckpoint
from src.schemas.case_planning_task import PlanningTaskCheckpoint, PlanningTaskSeed
from src.schemas.model_config import ModelInvocationRequest, model_invocation_parameters


async def main():
    evidence = Path(__file__).resolve().parent
    source = evidence / "p2-default-multi-case-r4-20261020"
    directory = evidence / "p2-cleanup-source-probe-20261020"
    directory.mkdir(exist_ok=False)
    request_path = source / "physical/6d9ed4b5f44846c187f65e03df443606.request.json"
    checkpoint_path = source / "PG-planning.json"
    raw_request = json.loads(request_path.read_bytes())
    raw_checkpoint = json.loads(checkpoint_path.read_bytes())
    seed = PlanningTaskSeed.model_validate(raw_checkpoint["seed"])
    checkpoint = PlanningTaskCheckpoint.model_validate(raw_checkpoint["checkpoint"])
    original_stages = PlanningStageCheckpoint.model_validate(
        checkpoint.stages.model_dump(mode="json"))
    original_state = EffectTaskCheckpoint.model_validate(
        checkpoint.effect_tasks.model_dump(mode="json"))
    decision = original_stages.logical_decision.model_copy(deep=True)
    target = next(item for item in decision.items if item.key == "query-order-target")
    assert target.cleanup_sources == [target.key]
    target.cleanup_sources = ["create-order-dependency"]
    corrected = seal_logical_decision(decision, original_stages.operation_selections)
    old_scope = task_roles(original_stages, original_state)["produced"]
    new_scope = task_roles(corrected, original_state)["produced"]
    assert old_scope == ["create-order-dependency", "query-order-target"]
    assert new_scope == ["create-order-dependency"]
    payload = json.loads(raw_request["messages"][-1]["content"])
    changed_payload = deepcopy(payload)
    changed_payload["logical_plan"] = corrected.logical_decision.model_dump(mode="json")
    changed_payload["logical_hash"] = corrected.logical_hash
    changed_payload["evidence_task"]["scope"] = new_scope
    changed_keys = [key for key in payload if payload[key] != changed_payload[key]]
    assert changed_keys == ["evidence_task", "logical_hash", "logical_plan"]
    assert raw_request["messages"][0]["content"] == seed.stage_prompts.effect_tasks["produced"]
    (directory / "counterfactual-payload.json").write_text(
        json.dumps(changed_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    store = MySQLModelConfigStore(get_settings())
    config = store.get_default_active()
    parameters = model_invocation_parameters(config)
    assert config.transport == "openai_chat_completions"
    assert urlsplit(config.api_base_url).hostname == "api.stepfun.com"
    assert config.max_output_tokens == raw_request["max_tokens"]
    request = ModelInvocationRequest(
        system_prompt=raw_request["messages"][0]["content"],
        messages=[{"role": "user", "content": json_text(changed_payload)}],
        tools=[],
    )
    facts = {
        "read_only": True, "business_HTTP": 0, "configuration_written": False,
        "original_plan_resumed_or_modified": False,
        "counterfactual_only": True,
        "source_sha256": {str(path.relative_to(evidence)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (request_path, checkpoint_path)},
        "changed_payload_keys": changed_keys,
        "old_produced_scope": old_scope, "corrected_produced_scope": new_scope,
        "configured_parameters": parameters,
        "accepted_by_canonical_validator": False,
    }
    original_factory = install(directory / "physical")
    started = perf_counter()
    try:
        async def receive(_delta):
            pass
        result = await asyncio.wait_for(OpenAIChatClient(timeout_seconds=240, max_retries=0).stream(
            config, config.api_key, request, receive), timeout=245)
        facts.update(finish_reason=result.get("finish_reason"), usage=result.get("usage"),
                     content_chars=len(result.get("text", "")))
        (directory / "response-content.txt").write_text(result.get("text", ""), encoding="utf-8")
        if result.get("finish_reason") == "stop":
            wire = decisions_for_protocol(seed.plan.config_snapshot.policy.evidence_reference_protocol)[
                "produced"].model_validate_json(result["text"])
            facts["action"] = wire.action
            if wire.action == "finish":
                state, merged = accept_task_decision(corrected, original_state, wire,
                    payload["evidence_reference_catalog"], checkpoint.plan.evidence,
                    seed.plan.config_snapshot.policy.evidence_reference_protocol)
                facts["accepted_by_canonical_validator"] = True
                facts["declaration_item_keys"] = [b.item_key for b in state.completed[-1].bindings]
                (directory / "counterfactual-accepted-receipt.json").write_text(
                    state.completed[-1].model_dump_json(indent=2), encoding="utf-8")
    except Exception as exc:
        facts.update(error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None))
        if isinstance(exc, ValueError):
            facts["canonical_error"] = str(exc)
    finally:
        OpenAIChatClient._make_client = original_factory
        facts["elapsed_seconds"] = round(perf_counter() - started, 3)
        facts["global_model_unchanged"] = (
            model_invocation_parameters(store.get_default_active()) == parameters)
        facts["source_files_unchanged"] = all(
            hashlib.sha256((evidence / path).read_bytes()).hexdigest() == digest
            for path, digest in facts["source_sha256"].items())
        (directory / "verification.json").write_text(
            json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(facts, ensure_ascii=False))
    return 0 if facts["accepted_by_canonical_validator"] and facts["global_model_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
