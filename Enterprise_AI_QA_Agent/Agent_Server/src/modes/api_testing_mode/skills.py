from __future__ import annotations

API_TESTING_SKILL_KEYS = [
    "api-contract-testing",
    "api-test-generation",
    "test-data-strategy",
]

API_TESTING_AGENT_SKILLS = {
    "api-testing-agent": [
        "api-contract-testing",
        "api-test-generation",
        "test-data-strategy",
    ],
    "api-executor-worker": ["api-contract-testing", "test-data-strategy"],
    "api-project-clarifier": ["api-contract-testing"],
    "api-doc-analyst": ["api-contract-testing", "api-test-generation"],
    "api-suite-planner": ["api-contract-testing", "api-test-generation", "test-data-strategy"],
    "api-precondition-planner": ["api-contract-testing", "test-data-strategy"],
    "api-failure-analyst": ["api-contract-testing", "test-data-strategy"],
}
