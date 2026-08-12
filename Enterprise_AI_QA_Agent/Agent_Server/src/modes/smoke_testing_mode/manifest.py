from __future__ import annotations

MODE_MANIFEST = {
    "key": "smoke_testing",
    "name": "冒烟测试模式",
    "summary": "面向核心链路快速准入、方案确认和基础可用性验证。",
    "description": "冒烟测试模式通过多智能体生成可审查测试方案，用户确认所选用例后自动执行，并将方案、证据和回归候选沉淀到 MinIO 与 PostgreSQL catalog。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "smoke-testing-agent",
    "allowed_agent_keys": [
        "smoke-testing-agent",
        "smoke-source-analyst",
        "smoke-plan-designer",
        "smoke-plan-reviewer",
        "smoke-executor",
        "smoke-result-analyst",
        "report-analyst",
    ],
    "default_skill_keys": ["smoke-test-planning"],
    "registered_tool_keys": [
        "smoke-suite-runner",
        "report-writer",
        "knowledge-rag",
    ],
    "harness_key": "smoke_testing_harness",
    "activation_policy": "confirm",
    "core_capability_keys": ["smoke.validation"],
    "on_demand_capability_keys": ["api.documentation.read", "report.generate"],
    "public_entry_tool_key": "smoke-suite-runner",
    "placeholder": False,
    "tags": ["testing", "smoke", "approval", "regression-candidate"],
}
