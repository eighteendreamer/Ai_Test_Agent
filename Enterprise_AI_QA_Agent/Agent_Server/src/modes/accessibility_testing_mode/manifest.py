from __future__ import annotations

from .skills import ACCESSIBILITY_TESTING_SKILL_KEYS

MODE_MANIFEST = {
    "key": "accessibility_testing",
    "name": "无障碍测试模式",
    "summary": "规划自动与人工结合的 Web 无障碍测试和证据审查。",
    "description": "占位模式：仅注册无障碍与浏览器 Skills，暂不提供专用 Runner 或合规认证能力。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "coordinator",
    "allowed_agent_keys": ["coordinator"],
    "default_skill_keys": [],
    "on_demand_skill_keys": ACCESSIBILITY_TESTING_SKILL_KEYS,
    "registered_tool_keys": ["skill"],
    "harness_key": "placeholder_testing_harness",
    "activation_policy": "explicit_only",
    "core_capability_keys": [],
    "on_demand_capability_keys": [],
    "public_entry_tool_key": "skill",
    "allowed_subworkflow_keys": [],
    "placeholder": True,
    "tags": ["testing", "accessibility", "wcag", "placeholder"],
}
