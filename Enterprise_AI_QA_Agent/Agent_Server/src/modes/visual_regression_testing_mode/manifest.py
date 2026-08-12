from __future__ import annotations

from .skills import VISUAL_REGRESSION_TESTING_SKILL_KEYS

MODE_MANIFEST = {
    "key": "visual_regression_testing",
    "name": "视觉回归测试模式",
    "summary": "规划确定性截图基线、视觉差异审查和受控更新。",
    "description": "占位模式：仅注册视觉回归与现有 Playwright Skills，暂不提供专用视觉差异 Runner。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "coordinator",
    "allowed_agent_keys": ["coordinator"],
    "default_skill_keys": [],
    "on_demand_skill_keys": VISUAL_REGRESSION_TESTING_SKILL_KEYS,
    "registered_tool_keys": ["skill"],
    "harness_key": "placeholder_testing_harness",
    "activation_policy": "explicit_only",
    "core_capability_keys": [],
    "on_demand_capability_keys": [],
    "public_entry_tool_key": "skill",
    "allowed_subworkflow_keys": [],
    "placeholder": True,
    "tags": ["testing", "visual", "regression", "placeholder"],
}
