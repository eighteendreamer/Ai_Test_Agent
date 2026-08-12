from __future__ import annotations

MODE_MANIFEST = {
    "key": "ui_automation",
    "name": "UI自动化模式",
    "summary": "面向页面探索、浏览器自动化、UI证据采集与结果输出。",
    "description": "UI 自动化模式使用专属 Agent 和工具集执行页面探索、浏览器操作与 UI 证据采集。",
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "ui-automation-agent",
    "allowed_agent_keys": ["ui-automation-agent", "report-analyst"],
    "default_skill_keys": ["playwright-e2e-testing", "playwright-cli"],
    "registered_tool_keys": [
        "ui-automation-runner",
        "ui-page-explorer",
        "browser-automation",
        "browser-control",
        "dom-inspector",
        "file-artifact-manager",
        "report-writer",
    ],
    "harness_key": "ui_automation_harness",
    "activation_policy": "confirm",
    "core_capability_keys": ["ui.automation"],
    "on_demand_capability_keys": ["api.documentation.read", "report.generate"],
    "public_entry_tool_key": "ui-automation-runner",
    "placeholder": False,
    "tags": ["testing", "ui", "automation"],
}
