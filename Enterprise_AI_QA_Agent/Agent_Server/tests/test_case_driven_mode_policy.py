from importlib import import_module

import pytest

from src.registry.modes import ModeRegistry
from src.registry.tools import ToolRegistry


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


def test_optional_mode_can_resolve_case_execution_entry_but_exempt_mode_cannot():
    module = import_module("src.application.test_runs.case_execution")
    resolver = getattr(module, "resolve_case_execution_tool")
    modes = ModeRegistry()
    tools = ToolRegistry()

    tool = resolver(modes, tools, "performance_testing")

    assert tool.key == "performance-test-runner"
    assert tool.owner_mode_key == "performance_testing"
    with pytest.raises(ValueError, match="does not provide a case execution entry"):
        resolver(modes, tools, "default")
    with pytest.raises(ValueError, match="does not have a case execution adapter"):
        resolver(modes, tools, "security_testing")
