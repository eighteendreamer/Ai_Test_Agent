from src.registry.modes import ModeRegistry


def test_mode_registry_exposes_case_driven_policy_from_plan():
    registry = ModeRegistry()

    assert registry.get("api_testing").case_driven_policy == "required"
    assert registry.get("ui_automation").case_driven_policy == "required"
    assert registry.get("smoke_testing").case_driven_policy == "required"
    assert registry.get("compatibility_testing").case_driven_policy == "required"
    assert registry.get("performance_testing").case_driven_policy == "optional"
    assert registry.get("security_testing").case_driven_policy == "optional"
    assert registry.get("default").case_driven_policy == "exempt"
    assert registry.get("code_review").case_driven_policy == "exempt"
