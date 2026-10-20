"""One read-only Step request to distinguish Schema expressiveness from planning."""
import asyncio
import json
from pathlib import Path
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Agent_Server"))
sys.path.insert(0, str(ROOT / "Agent_Server" / "tests"))

from default_model_probe import install
from src.application.model_clients.openai_client import OpenAIChatClient
from src.core.config import get_settings
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.schemas.case_planning import PlanInputBinding
from src.schemas.model_config import ModelInvocationRequest, model_invocation_parameters
from src.schemas.model_request_options import ModelOutputFormat, ModelRequestProfile


async def main():
    evidence = Path(__file__).resolve().parent
    directory = evidence / "p2-binding-source-schema-probe-20261020"
    directory.mkdir(exist_ok=False)
    source_request = evidence / "p2-bindings-r3-prompts-20261020.json"
    schema = json.loads(source_request.read_bytes())["binding_schema"]
    store = MySQLModelConfigStore(get_settings())
    original = store.get_default_active()
    original_parameters = model_invocation_parameters(original)
    config = original.model_copy(deep=True)
    config.request_profile = ModelRequestProfile.model_validate_json(
        (evidence / "p2-default-step-json-schema-low-profile.json").read_bytes())
    request = ModelInvocationRequest(
        system_prompt="Return only one JSON object matching the supplied Schema.",
        messages=[{"role": "user", "content": (
            "This is a read-only data-binding contract example, with no business execution. "
            "The input comes from the preceding item whose key is upstream_fixture. "
            "Set source_item to upstream_fixture and input_key to null. "
            "Use the entire preceding item output: output_path_tokens=[] and output_path=\"\". "
            "Set required=true. Do not invent any runtime input.") }],
        tools=[],
        output_format=ModelOutputFormat(kind="json_schema", schema_name="binding_source_probe",
            schema_definition=schema, strict=True, required=True),
    )
    original_factory = install(directory / "physical")
    facts = {"read_only": True, "business_HTTP": 0, "configuration_written": False,
             "schema_from_actual_failed_request": str(source_request.relative_to(evidence)),
             "configured_parameters": original_parameters,
             "profile_isolated_in_memory": True, "max_output_tokens": config.max_output_tokens}
    started = perf_counter()
    try:
        async def receive(delta):
            pass
        result = await asyncio.wait_for(OpenAIChatClient(timeout_seconds=90, max_retries=0).stream(
            config, config.api_key, request, receive), timeout=95)
        facts.update(finish_reason=result.get("finish_reason"), usage=result.get("usage"),
                     response_text=result.get("text"))
        binding = PlanInputBinding.model_validate_json(result["text"])
        facts["canonical_binding_valid"] = (binding.source_item == "upstream_fixture"
            and binding.input_key is None and binding.path_tokens == [] and binding.required)
    except Exception as exc:
        facts.update(error_type=type(exc).__name__, status_code=getattr(exc, "status_code", None),
                     canonical_binding_valid=False)
    finally:
        OpenAIChatClient._make_client = original_factory
        facts["elapsed_seconds"] = round(perf_counter() - started, 3)
        facts["global_model_unchanged"] = (
            model_invocation_parameters(store.get_default_active()) == original_parameters)
        (directory / "verification.json").write_text(
            json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(facts, ensure_ascii=False))
    return 0 if facts["canonical_binding_valid"] and facts["global_model_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
