from __future__ import annotations

from .skills import UNIT_COMPONENT_TESTING_SKILL_KEYS

MODE_MANIFEST = {
    "key": "unit_component_testing",
    "name": "单元与组件测试模式",
    "summary": "按项目实际语言和框架规划单元、组件及测试质量验证。",
    "description": "占位模式：仅注册单元与组件测试 Skills，暂不提供专用 Agent、Runner 或执行工具。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "coordinator",
    "allowed_agent_keys": ["coordinator"],
    "default_skill_keys": [],
    "on_demand_skill_keys": UNIT_COMPONENT_TESTING_SKILL_KEYS,
    "registered_tool_keys": ["skill"],
    "harness_key": "placeholder_testing_harness",
    "activation_policy": "explicit_only",
    "core_capability_keys": [],
    "on_demand_capability_keys": [],
    "public_entry_tool_key": "skill",
    "allowed_subworkflow_keys": [],
    "placeholder": True,
    "tags": ["testing", "unit", "component", "placeholder"],
}
