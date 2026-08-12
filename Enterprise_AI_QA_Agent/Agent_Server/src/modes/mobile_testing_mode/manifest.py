from __future__ import annotations

from .skills import MOBILE_TESTING_SKILL_KEYS

MODE_MANIFEST = {
    "key": "mobile_testing",
    "name": "移动端测试模式",
    "summary": "规划 Android、iOS 与跨平台移动应用测试矩阵。",
    "description": "占位模式：仅注册移动端框架 Skills，暂不提供设备、模拟器或移动 Runner。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "coordinator",
    "allowed_agent_keys": ["coordinator"],
    "default_skill_keys": MOBILE_TESTING_SKILL_KEYS,
    "registered_tool_keys": ["skill"],
    "harness_key": "placeholder_testing_harness",
    "activation_policy": "explicit_only",
    "core_capability_keys": [],
    "on_demand_capability_keys": [],
    "public_entry_tool_key": "skill",
    "allowed_subworkflow_keys": [],
    "placeholder": True,
    "tags": ["testing", "mobile", "android", "ios", "placeholder"],
}
