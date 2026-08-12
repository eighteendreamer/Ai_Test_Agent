from __future__ import annotations

from .skills import INTEGRATION_TESTING_SKILL_KEYS

MODE_MANIFEST = {
    "key": "integration_testing",
    "name": "集成与依赖测试模式",
    "summary": "规划服务契约、真实依赖、网络模拟和数据访问集成测试。",
    "description": "占位模式：仅注册集成测试 Skills，暂不提供专用 Runner 或依赖环境编排。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "coordinator",
    "allowed_agent_keys": ["coordinator"],
    "default_skill_keys": INTEGRATION_TESTING_SKILL_KEYS,
    "registered_tool_keys": ["skill"],
    "harness_key": "placeholder_testing_harness",
    "activation_policy": "explicit_only",
    "core_capability_keys": [],
    "on_demand_capability_keys": [],
    "public_entry_tool_key": "skill",
    "allowed_subworkflow_keys": [],
    "placeholder": True,
    "tags": ["testing", "integration", "contract", "placeholder"],
}
