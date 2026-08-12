from __future__ import annotations

from .skills import RELIABILITY_TESTING_SKILL_KEYS

MODE_MANIFEST = {
    "key": "reliability_testing",
    "name": "测试可靠性与韧性模式",
    "summary": "识别不稳定测试并规划系统与测试交互韧性验证。",
    "description": "占位模式：仅注册可靠性与韧性 Skills，暂不提供专用执行编排。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "coordinator",
    "allowed_agent_keys": ["coordinator"],
    "default_skill_keys": RELIABILITY_TESTING_SKILL_KEYS,
    "registered_tool_keys": ["skill"],
    "harness_key": "placeholder_testing_harness",
    "activation_policy": "explicit_only",
    "core_capability_keys": [],
    "on_demand_capability_keys": [],
    "public_entry_tool_key": "skill",
    "allowed_subworkflow_keys": [],
    "placeholder": True,
    "tags": ["testing", "reliability", "resilience", "placeholder"],
}
