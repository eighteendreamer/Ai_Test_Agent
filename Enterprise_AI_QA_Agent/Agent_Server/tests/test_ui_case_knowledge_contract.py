"""Actual UI registry/exposure and prompt boundaries for frozen case execution."""
from src.application.capabilities.capability_resolver import CapabilityResolver
from src.application.capabilities.tool_exposure_policy import ToolExposurePolicy
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.registry.agents import AgentRegistry
from src.registry.modes import ModeRegistry
from src.registry.skills import SkillRegistry
from src.registry.tools import ToolRegistry
from src.modes.ui_automation_mode.prompt_contract import UI_AUTOMATION_PROMPT_CONTRACT


def test_ui_scene_reader_survives_actual_mode_and_agent_exposure():
    mode = ModeRegistry().get("ui_automation")
    agent = AgentRegistry().get(mode.default_agent_key)
    keys = list(mode.registered_tool_keys)
    for skill in SkillRegistry().get_many(agent.supported_skills):
        keys.extend(skill.tool_keys)
    eligible = CapabilityResolver().eligible_tools(
        tools=ToolRegistry().get_many(keys), active_mode_key=mode.key,
        allowed_capabilities=mode.core_capability_keys + mode.on_demand_capability_keys,
    )
    exposed = ToolExposurePolicy().filter_supported(tools=eligible, agent=agent)
    assert "knowledge-rag" in {tool.key for tool in exposed}
    assert ToolRegistry().get("knowledge-rag").permission_level == "safe"


def ui_contract(basis):
    context = {"execution_basis": basis, "project_id": "project", "run_id": "run", "run_item_id": "item"}
    if basis == "cases":
        context["case_execution"] = {
            "project_id": "project", "run_id": "run", "run_item_id": "item", "plan_id": "plan",
            "objective": "验证录制场景", "target_key": "target",
            "steps": [{"phase": "case", "item": {"kind": "target", "key": "target",
                "case_id": "case", "case_version_id": "version", "reason": "用户目标",
                "evidence_ids": ["case-evidence"]}, "content_json": {"expected": "2"}}],
        }
    assembled = PromptAssemblyService().build_for_turn(
        state={"selected_agent_key": "ui-automation-agent", "mode_key": "ui_automation",
               "user_message": "按固定用例测试", "context_bundle": context},
        available_agent_keys=["ui-automation-agent"],
    )
    return next(section.content for section in assembled.system_sections if section.key == "ui_automation_contract")


def test_frozen_ui_cases_do_not_receive_exploration_only_entry_contract():
    contract = ui_contract("cases")
    assert "Always call `ui-automation-runner` first" not in contract
    assert "knowledge-rag" in contract
    assert "actual" in contract and "receipt" in contract


def test_free_ui_keeps_original_entry_contract():
    assert ui_contract("free") == UI_AUTOMATION_PROMPT_CONTRACT
