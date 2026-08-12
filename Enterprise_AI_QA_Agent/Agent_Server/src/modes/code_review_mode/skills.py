from __future__ import annotations

CODE_REVIEW_SKILL_KEYS = [
    "ci-pipeline-review",
    "test-coverage-review",
    "flaky-test-analysis",
    "tdd-review",
    "test-data-strategy",
    "owasp-security-testing",
]

CODE_REVIEW_REVIEWER_SKILLS = {
    "architecture": ["ci-pipeline-review"],
    "correctness": ["tdd-review"],
    "security": ["owasp-security-testing"],
    "testability": ["test-coverage-review", "flaky-test-analysis", "test-data-strategy"],
    "maintainability": ["ci-pipeline-review"],
}
