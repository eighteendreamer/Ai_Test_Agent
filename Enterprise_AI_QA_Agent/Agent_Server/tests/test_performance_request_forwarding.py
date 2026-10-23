"""The public runner's HTTP inputs survive plan construction and snapshots."""
from src.modes.performance_testing_mode.plan_state import PerformanceTestingState
from src.modes.performance_testing_mode.runtime import PerformanceTestingModeRuntime
from src.registry.tools import ToolRegistry


def test_direct_target_method_headers_and_json_body_reach_plan_and_snapshot():
    state = PerformanceTestingState.model_validate({
        "request": {
            "target": "http://127.0.0.1:8080/orders",
            "method": "post",
            "headers": {"Content-Type": "application/json", "X-Test": "marker"},
            "body_template": {"count": 0, "enabled": False, "nullable": None},
            "workload_vus": 1,
            "duration_seconds": 2,
            "run_intent": "regression",
        },
    })

    plan = PerformanceTestingModeRuntime()._build_plan_from_slots(state)
    restored = PerformanceTestingState.model_validate(state.model_dump(mode="json"))

    assert plan.targets[0].method == "POST"
    assert plan.targets[0].headers == {"Content-Type": "application/json", "X-Test": "marker"}
    assert plan.targets[0].body_template == {"count": 0, "enabled": False, "nullable": None}
    assert restored.request.method == "post"
    assert restored.request.headers == plan.targets[0].headers
    assert restored.request.body_template == plan.targets[0].body_template


def test_document_endpoint_defaults_http_fields_without_overriding_explicit_values():
    state = PerformanceTestingState.model_validate({
        "request": {"target": "http://127.0.0.1:8080/from-docs", "workload_vus": 1},
        "resolved_endpoint": {"method": "put", "headers": {"X-Document": "sample"},
            "body_template": "from document"},
    })

    plan = PerformanceTestingModeRuntime()._build_plan_from_slots(state)
    assert (plan.targets[0].method, plan.targets[0].headers, plan.targets[0].body_template) == (
        "PUT", {"X-Document": "sample"}, "from document")

    state.request.method = "PATCH"
    state.request.headers = {"X-Explicit": "wins"}
    state.request.body_template = "explicit body"
    explicit_plan = PerformanceTestingModeRuntime()._build_plan_from_slots(state)
    assert (explicit_plan.targets[0].method, explicit_plan.targets[0].headers,
        explicit_plan.targets[0].body_template) == ("PATCH", {"X-Explicit": "wins"}, "explicit body")


def test_existing_public_schema_exposes_the_forwarded_http_fields():
    fields = ToolRegistry().get("performance-test-runner").input_schema["properties"]
    assert {"method", "headers", "body_template"} <= fields.keys()


def test_legacy_url_request_keeps_get_and_no_body_defaults():
    state = PerformanceTestingState.model_validate({"request": {"target": "http://127.0.0.1:8080/health"}})
    target = PerformanceTestingModeRuntime()._build_plan_from_slots(state).targets[0]
    assert (target.method, target.headers, target.body_template) == ("GET", {}, None)
