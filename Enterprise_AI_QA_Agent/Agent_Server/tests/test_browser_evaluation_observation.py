"""Real browser observations retain values before lossy CLI string formatting."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.application.context.mcp_runtime_service import MCPRuntimeService
from src.core.config import get_settings


@pytest.mark.skipif(os.getenv("RUN_LIVE_BROWSER_TESTS") != "1", reason="requires actual Chromium")
def test_persistent_eval_exposes_exact_typed_value_and_unchanged_original(tmp_path):
    async def run():
        settings = get_settings().model_copy(deep=True)
        settings.orchestration.browser_headless = True
        registry = SimpleNamespace(get=lambda key: SimpleNamespace(
            enabled=True, capabilities=["browser-control"], name="browser"))
        service = MCPRuntimeService(registry, settings)
        service._artifact_root = tmp_path
        context = {"session_id": "s-observation", "turn_id": "t-observation"}
        async def command(args):
            return await service.call("browser", "browser-control",
                {"action": "command", "args": args}, context)
        try:
            captured_path = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-default-ui-live3/positive/tool-jobs.json"
            captured = next(job["output_payload"] for job in json.loads(captured_path.read_bytes())
                if job["tool_key"] == "browser-control" and '"count":"0"' in job["output_payload"].get("stdout", ""))
            value = json.loads(captured["stdout"])
            cases = [("(" + json.dumps(value) + ")", value), ("0", 0), ("false", False), ("null", None),
                ('"false"', "false"), ('"0"', "0"), ('"null"', "null"), ('""', ""),
                ('[0,false,null,"0"]', [0, False, None, "0"]),
                ("JSON.stringify(" + json.dumps(value) + ")", json.dumps(value, separators=(",", ":")))]
            for expression, expected in cases:
                output = await command(["--raw", "eval", expression])
                assert "result" in output, output
                assert type(output["result"]) is type(expected)
                assert output["result"] == expected
                transcript = json.loads(Path(output["artifacts"][-1]["path"]).read_bytes())[0]
                assert transcript["stdout"] == output["stdout"]
                assert transcript["result"] == expected
            await command(["eval", "window.marker = 11"])
            assert (await command(["eval", "window.marker"]))["result"] == 11
            await command(["open", "data:text/html,<output id=count>0</output>"])
            located = await command(["eval", "el => el.textContent", "#count"])
            assert located["result"] == "0" and type(located["result"]) is str
            from src.application.test_runs.literal_evidence import literal_value
            assert literal_value(located, ["result"]) == "0"
            with pytest.raises((KeyError, ValueError)):
                literal_value(located, ["output", "result"])
            close = await command(["close"])
            assert "result" not in close
        finally:
            await service.close()
        assert not service._playwright_cli._sessions
    asyncio.run(run())


def test_captured_scalar_check_rejects_action_evidence_and_schema_explains_why():
    from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService
    from src.schemas.model_config import ModelInvocationResult
    from src.schemas.case_execution import CaseExecutionCheck
    source = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-ui-observation-default5/positive/correction-request.json"
    request = json.loads(source.read_bytes())
    content = request["messages"][-1]["content"]
    payload = json.JSONDecoder().raw_decode(content.split("\n", 1)[1])[0]
    response = ModelInvocationResult(text=payload["validation_feedback"]["original_response"],
        response_summary={"mode": "ok", "finish_reason": "stop"})
    with pytest.raises(ValueError, match="确定性比较需要且只能引用一个实际值"):
        AgentCaseAssessmentService.parse_response(response)
    description = CaseExecutionCheck.model_json_schema()["properties"]["observations"]["description"]
    assert "exactly one actual value" in description and "Semantic may reference multiple" in description
