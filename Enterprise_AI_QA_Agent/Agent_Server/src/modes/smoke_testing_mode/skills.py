from __future__ import annotations

SMOKE_TESTING_SKILL_KEYS = ["smoke-test-planning"]

SMOKE_TESTING_AGENT_SKILLS = {
    "smoke-testing-agent": ["smoke-test-planning", "api-contract-testing", "playwright-e2e-testing"],
    "smoke-source-analyst": ["smoke-test-planning", "api-contract-testing", "playwright-e2e-testing"],
    "smoke-plan-designer": ["smoke-test-planning", "api-contract-testing", "playwright-e2e-testing", "test-data-strategy"],
    "smoke-plan-reviewer": ["smoke-test-planning", "test-data-strategy"],
    "smoke-executor": ["smoke-test-planning"],
    "smoke-result-analyst": ["smoke-test-planning"],
}
