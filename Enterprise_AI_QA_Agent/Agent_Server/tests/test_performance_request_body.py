"""Regressions derived from actual pre-fix receiver bytes, not syntax-only checks."""
import base64
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pytest

from src.application.performance.jmeter_engine_adapter import JMeterEngineAdapter
from src.application.performance.k6_engine_adapter import K6EngineAdapter
from src.modes.performance_testing_mode.plan_state import PerfPlan, PerfTarget

BEFORE = Path(__file__).resolve().parents[2] / "docs/upgrade_2026-10-03/p5-perf-body-before"


@pytest.mark.parametrize("engine", ["k6", "jmeter"])
def test_actual_failed_body_input_is_preserved_by_compiler(engine):
    captured = json.loads((BEFORE / f"{engine}-plan.json").read_bytes())
    plan = PerfPlan.model_validate(captured)
    old = json.loads((BEFORE / "observations.json").read_bytes())
    facts = next(o for o in old if o["engine"] == engine)
    text = plan.targets[1].body_template
    assert facts["exit_code"] == 0 and not facts["passed"]
    assert any(e["path"] == "/text" and e["actual"] != text for e in facts["events"])
    script = (K6EngineAdapter() if engine == "k6" else JMeterEngineAdapter()).build_script(plan).script_content
    if engine == "k6":
        assert json.dumps(text) in script
        assert "`" + text + "`" not in script
    else:
        tree = ET.fromstring(script)
        bodies = [base64.b64decode(re.search(r"decode\('([^']*)'\)", p.text).group(1)).decode("utf-8")
            for p in tree.findall(".//JSR223PreProcessor/stringProp[@name='script']")]
        assert json.loads(bodies[0]) == plan.targets[0].body_template
        assert bodies[1] == text
        assert all(p.text == "true" for p in tree.findall(".//boolProp[@name='HTTPSampler.postBodyRaw']"))
    assert plan.model_dump(mode="json") == captured


@pytest.mark.parametrize("body", ["", "` ${1+1} ${__groovy(1+1)} \\x\r\n\t\x00中文😀",
    {"flag": False, "number": 0, "null": None, "nested": ["${...}", {"extra": "保留"}]}])
def test_literal_body_handles_empty_and_xml_control_bytes(body):
    plan = PerfPlan(targets=[PerfTarget(name="post", url="http://localhost/public", method="POST", body_template=body)])
    tree = ET.fromstring(JMeterEngineAdapter().build_script(plan).script_content)
    processor = tree.find(".//JSR223PreProcessor/stringProp[@name='script']").text
    actual = base64.b64decode(re.search(r"decode\('([^']*)'\)", processor).group(1)).decode("utf-8")
    assert (json.loads(actual) if isinstance(body, dict) else actual) == body
    assert tree.find(".//boolProp[@name='HTTPArgument.always_encode']").text == "false"
    assert tree.find(".//boolProp[@name='HTTPArgument.use_equals']").text == "false"
    assert tree.find(".//stringProp[@name='HTTPSampler.contentEncoding']").text == "UTF-8"


def test_no_body_does_not_add_body_processor_or_raw_arguments():
    plan = PerfPlan(targets=[PerfTarget(name="plain", url="http://localhost/public", method="POST")])
    tree = ET.fromstring(JMeterEngineAdapter().build_script(plan).script_content)
    assert tree.find(".//JSR223PreProcessor") is None
    assert tree.find(".//boolProp[@name='HTTPSampler.postBodyRaw']") is None
    assert K6EngineAdapter()._build_body_expr(plan.targets[0], plan) == "null"
