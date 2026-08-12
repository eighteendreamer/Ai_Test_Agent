from __future__ import annotations

SECURITY_TESTING_SKILL_KEYS = [
    "owasp-security-testing",
    "auth-security-testing",
    "input-validation-security-testing",
]

SECURITY_TESTING_AGENT_SKILLS = {
    "security-testing-agent": SECURITY_TESTING_SKILL_KEYS,
    "security-doc-analyst": ["owasp-security-testing"],
    "attack-surface-planner": ["owasp-security-testing"],
    "security-recon-worker": [],
    "security-auth-worker": ["auth-security-testing"],
    "security-web-verifier": ["owasp-security-testing", "input-validation-security-testing"],
    "security-api-verifier": ["owasp-security-testing", "auth-security-testing", "input-validation-security-testing"],
    "security-host-verifier": ["owasp-security-testing"],
    "security-exploit-coder": ["owasp-security-testing"],
    "security-failure-analyst": ["owasp-security-testing"],
}
