from __future__ import annotations

from src.application.prompting.prompt_assembly_service import PromptAssemblyService


def test_prompt_assembly_directs_models_to_unified_skill_loader():
    service = PromptAssemblyService()

    result = service.build_for_turn(
        state={
            "selected_agent_name": "Coordinator",
            "selected_agent_key": "coordinator",
            "selected_model_key": "gpt-5",
            "session_mode": "normal",
            "runtime_mode": "interactive",
            "resolved_skill_keys": ["agently-mail"],
            "runtime_messages": [],
            "user_message": "发一个测试邮件",
            "normalized_input": "发一个测试邮件",
            "model_visible_tool_keys": ["mail-status", "mail-send"],
            "allowed_tool_keys": ["mail-status"],
            "approval_required_tool_keys": ["mail-send"],
            "denied_tool_keys": [],
            "plan_steps": [],
            "context_bundle": {},
        },
        available_agent_keys=["coordinator"],
    )

    system_prompt = result.system_prompt
    runtime_prompt = "\n".join(
        section.content for section in result.runtime_message_sections
    )

    assert "Skill keys are not callable tool names." in system_prompt
    assert "registered `skill` tool" in system_prompt
    assert "Use `skill` to load additional capability tools on demand" in runtime_prompt
    assert "never emit a Skill key itself as a tool call" in runtime_prompt
