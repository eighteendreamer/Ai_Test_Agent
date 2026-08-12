from __future__ import annotations

MODE_MANIFEST = {
    "key": "security_testing",
    "name": "安全测试模式",
    "summary": "面向 Web/API、主机、端口、网络侦察的多智能体安全测试。",
    "description": (
        "安全测试模式提供完整的渗透测试能力，包括资产发现、端口扫描、服务指纹识别、"
        "Web 漏洞扫描、凭证验证、漏洞利用验证等，支持多 worker 智能体协同执行，"
        "自动生成结构化安全测试报告并通过邮件投递。"
    ),
    "category": "testing",
    "is_test_mode": True,
    "default_agent_key": "security-testing-agent",
    "allowed_agent_keys": [
        "security-testing-agent",
        "security-doc-analyst",
        "attack-surface-planner",
        "security-recon-worker",
        "security-auth-worker",
        "security-web-verifier",
        "security-api-verifier",
        "security-host-verifier",
        "security-exploit-coder",
        "security-failure-analyst",
        "report-analyst",
    ],
    "default_skill_keys": [
        "owasp-security-testing",
        "auth-security-testing",
        "input-validation-security-testing",
    ],
    "registered_tool_keys": [
        "security-scan-runner",
        "network-recon-runner",
        "web-scan-runner",
        "service-audit-runner",
        "credential-attack-runner",
        "traffic-analysis-runner",
        "exploit-workbench-runner",
        "knowledge-rag",
        "report-writer",
        "observation-search",
        "session-history",
    ],
    "harness_key": "security_testing_harness",
    "activation_policy": "explicit_only",
    "minimum_authorization": "verified_target_authorization",
    "maximum_auto_risk_level": "low",
    "core_capability_keys": ["security.assessment"],
    "on_demand_capability_keys": ["api.documentation.read", "report.generate"],
    "public_entry_tool_key": "security-scan-runner",
    "placeholder": False,
    "tags": ["testing", "security", "penetration", "vulnerability"],
}
