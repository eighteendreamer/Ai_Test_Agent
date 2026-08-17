from __future__ import annotations

from src.modes.compatibility_testing_mode.skills import COMPATIBILITY_TESTING_SKILL_KEYS

MODE_MANIFEST = {
    "key": "compatibility_testing",
    "name": "兼容性测试模式",
    "summary": "面向多产品、多环境、多平台的兼容性测试编排。",
    "description": (
        "兼容性测试模式负责产品接入、环境矩阵生成、用例生成、用户确认、"
        "Runner/Provider 调度、已有测试模式调用、证据聚合和兼容性报告输出。"
    ),
    "category": "testing",
    "is_test_mode": True,
    "case_driven_policy": "required",
    "default_agent_key": "compatibility-testing-agent",
    "allowed_agent_keys": [
        "compatibility-testing-agent",
        "report-analyst",
    ],
    "default_skill_keys": COMPATIBILITY_TESTING_SKILL_KEYS,
    "registered_tool_keys": [
        "compatibility-test-runner",
        "smoke-suite-runner",
        "ui-automation-runner",
        "api-test-runner",
        "performance-test-runner",
        "knowledge-rag",
        "api-docs-library",
        "report-writer",
        "file-artifact-manager",
        "subagent-dispatch",
        "observation-search",
        "session-history",
    ],
    "harness_key": "compatibility_testing_harness",
    "activation_policy": "confirm",
    "core_capability_keys": ["compatibility.matrix_test"],
    "on_demand_capability_keys": ["api.documentation.read", "performance.load_test", "ui.automation", "smoke.validation"],
    "public_entry_tool_key": "compatibility-test-runner",
    "allowed_subworkflow_keys": ["api_testing", "performance_testing", "ui_automation", "smoke_testing"],
    "placeholder": False,
    "tags": ["testing", "compatibility", "matrix", "runner", "orchestration"],
}
