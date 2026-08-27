from __future__ import annotations

from dataclasses import dataclass

from src.modes.compatibility_testing_mode.skills import COMPATIBILITY_TESTING_AGENT_SKILLS
from src.schemas.agent import AgentDescriptor


@dataclass(frozen=True)
class AgentModule:
    descriptor: AgentDescriptor


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentModule] = {
            "coordinator": AgentModule(
                descriptor=AgentDescriptor(
                    key="coordinator",
                    name="Coordinator",
                    role="coordinator",
                    summary="Coordinate planning, context assembly, and execution routing.",
                    description="Acts like the Claude Code coordinator: chooses the next execution path, synthesizes context, and orchestrates work.",
                    supported_tools=[
                        "workflow-router",
                        "subagent-dispatch",
                        "knowledge-rag",
                        "web_search",
                        "attachment-reader",
                        "session-history",
                        "session-timeline",
                        "observation-search",
                        "test-case-generator",
                        "cli-executor",
                        "report-writer",
                        "mail-status",
                        "mail-send",
                        "mail-list",
                        "mail-read",
                        "mail-search",
                        "mail-reply",
                        "mail-forward",
                        "mail-download-attachment",
                    ],
                    supported_capabilities=["general.assistance", "web.search", "mcp.external"],
                    supported_skills=["generate-test-cases", "agently-mail"],
                    tags=["core", "orchestration"],
                )
            ),
            "qa-planner": AgentModule(
                descriptor=AgentDescriptor(
                    key="qa-planner",
                    name="QA Planner",
                    role="planner",
                    summary="Break requirements into test coverage, assertions, and risks.",
                    description="Transforms user intent into executable QA plans, test cases, and verification boundaries.",
                    supported_tools=[
                        "attachment-reader",
                        "session-history",
                        "session-timeline",
                        "observation-search",
                        "knowledge-rag",
                        "test-case-generator",
                        "test-case-xlsx-exporter",
                        "report-writer",
                    ],
                    supported_skills=["generate-test-cases"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["planning", "qa"],
                )
            ),
            "ops-executor": AgentModule(
                descriptor=AgentDescriptor(
                    key="ops-executor",
                    name="Ops Executor",
                    role="worker",
                    summary="Execute terminal commands, environment diagnostics, and workspace-level operational checks.",
                    description=(
                        "Runs CLI commands inside the workspace with approval gating, captures terminal evidence, "
                        "and can package the results as QA-ready artifacts."
                    ),
                    supported_tools=[
                        "cli-executor",
                        "file-artifact-manager",
                        "report-writer",
                        "session-history",
                        "session-timeline",
                        "observation-search",
                        "mail-status",
                        "mail-send",
                        "mail-list",
                        "mail-read",
                        "mail-search",
                        "mail-reply",
                        "mail-forward",
                        "mail-download-attachment",
                    ],
                    supported_skills=["agently-mail"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "qwen-max"],
                    default_model="claude-sonnet-4",
                    tags=["ops", "cli", "execution"],
                )
            ),
            "ui-executor": AgentModule(
                descriptor=AgentDescriptor(
                    key="ui-executor",
                    name="UI Explorer",
                    role="worker",
                    summary="Understand page structure and build semantic UI graphs from ARIA snapshots.",
                    description="Runs UI exploration only: captures accessibility-tree structure, extracts business context, and writes page/entity/element graph knowledge without tests, assertions, or validation verdicts.",
                    supported_tools=[
                        "ui-page-explorer",
                        "browser-automation",
                        "browser-control",
                        "dom-inspector",
                        "file-artifact-manager",
                        "report-writer",
                    ],
                    supported_skills=["playwright-e2e-testing", "playwright-cli"],
                    supported_models=["claude-sonnet-4", "qwen-max"],
                    default_model="claude-sonnet-4",
                    tags=["ui", "exploration", "aria", "graph"],
                )
            ),
            "api-verifier": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-verifier",
                    name="API Verifier",
                    role="verifier",
                    summary="Validate APIs, payloads, and structured assertions.",
                    description="Runs API checks and produces assertion-ready verification summaries.",
                    supported_tools=["api-tester", "knowledge-rag", "report-writer"],
                    supported_skills=["api-contract-testing", "api-test-generation", "test-data-strategy"],
                    supported_models=["gpt-5.4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["api", "verification"],
                )
            ),
            "report-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="report-analyst",
                    name="Report Analyst",
                    role="reporter",
                    summary="Turn runtime evidence into structured QA output.",
                    description="Produces final findings, summaries, and delivery-ready reports.",
                    supported_tools=[
                        "report-writer",
                        "attachment-reader",
                        "knowledge-rag",
                        "session-history",
                        "session-timeline",
                        "observation-search",
                    ],
                    supported_skills=[],
                    supported_models=["claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["reporting"],
                )
            ),
            "code-review-agent": AgentModule(
                descriptor=AgentDescriptor(
                    key="code-review-agent",
                    name="Code Review Agent",
                    role="coordinator",
                    summary="Coordinate time-budgeted code review debates, launch reviewer agents, and produce approval-ready output.",
                    description="Dedicated mode coordinator for code review workflows. It should launch the code-review-orchestrator first, then supervise reviewer debate progress instead of reading repository files directly.",
                    supported_tools=[
                        "code-review-orchestrator",
                        "code-governance-runner",
                        "session-history",
                        "observation-search",
                        "report-writer",
                    ],
                    supported_skills=["ci-pipeline-review", "test-coverage-review", "flaky-test-analysis", "tdd-review", "test-data-strategy", "owasp-security-testing"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "code-review", "approval"],
                )
            ),
            "code-architecture-reviewer": AgentModule(
                descriptor=AgentDescriptor(
                    key="code-architecture-reviewer",
                    name="Code Architecture Reviewer",
                    role="reviewer",
                    summary="Review module boundaries, dependency flow, and extensibility risks.",
                    description="Specialized code review worker focused on architecture decisions, layering, ownership boundaries, and long-term maintainability tradeoffs.",
                    supported_tools=[
                        "project-source-loader",
                        "project-tree-scanner",
                        "project-file-reader",
                        "project-diff-reader",
                        "session-history",
                        "observation-search",
                        "knowledge-rag",
                        "report-writer",
                    ],
                    supported_skills=["ci-pipeline-review"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "code-review", "architecture"],
                )
            ),
            "code-correctness-reviewer": AgentModule(
                descriptor=AgentDescriptor(
                    key="code-correctness-reviewer",
                    name="Code Correctness Reviewer",
                    role="reviewer",
                    summary="Review logic, state transitions, and edge-case correctness.",
                    description="Specialized code review worker focused on functional correctness, failure handling, and invalid state transitions.",
                    supported_tools=[
                        "project-source-loader",
                        "project-tree-scanner",
                        "project-file-reader",
                        "project-diff-reader",
                        "session-history",
                        "observation-search",
                        "knowledge-rag",
                        "report-writer",
                    ],
                    supported_skills=["tdd-review"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "code-review", "correctness"],
                )
            ),
            "code-security-reviewer": AgentModule(
                descriptor=AgentDescriptor(
                    key="code-security-reviewer",
                    name="Code Security Reviewer",
                    role="reviewer",
                    summary="Review command execution, credentials, unsafe input handling, and privilege boundaries.",
                    description="Specialized code review worker focused on exploit paths, trust boundaries, secret handling, and containment measures.",
                    supported_tools=[
                        "project-source-loader",
                        "project-tree-scanner",
                        "project-file-reader",
                        "project-diff-reader",
                        "session-history",
                        "observation-search",
                        "knowledge-rag",
                        "report-writer",
                    ],
                    supported_skills=["owasp-security-testing"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "code-review", "security"],
                )
            ),
            "code-testability-reviewer": AgentModule(
                descriptor=AgentDescriptor(
                    key="code-testability-reviewer",
                    name="Code Testability Reviewer",
                    role="reviewer",
                    summary="Review coverage gaps, replayability, observability, and harness fit.",
                    description="Specialized code review worker focused on missing tests, weak evidence capture, and harness improvements for reliable verification.",
                    supported_tools=[
                        "project-source-loader",
                        "project-tree-scanner",
                        "project-file-reader",
                        "project-diff-reader",
                        "session-history",
                        "observation-search",
                        "knowledge-rag",
                        "report-writer",
                    ],
                    supported_skills=["test-coverage-review", "flaky-test-analysis", "test-data-strategy"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "code-review", "testability"],
                )
            ),
            "code-maintainability-reviewer": AgentModule(
                descriptor=AgentDescriptor(
                    key="code-maintainability-reviewer",
                    name="Code Maintainability Reviewer",
                    role="reviewer",
                    summary="Review readability, duplication, complexity, and configuration hygiene.",
                    description="Specialized code review worker focused on code clarity, repeated patterns, maintainability debt, and operational simplicity.",
                    supported_tools=[
                        "project-source-loader",
                        "project-tree-scanner",
                        "project-file-reader",
                        "project-diff-reader",
                        "session-history",
                        "observation-search",
                        "knowledge-rag",
                        "report-writer",
                    ],
                    supported_skills=["ci-pipeline-review"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "code-review", "maintainability"],
                )
            ),
            "code-review-synthesizer": AgentModule(
                descriptor=AgentDescriptor(
                    key="code-review-synthesizer",
                    name="Code Review Synthesizer",
                    role="summarizer",
                    summary="Aggregate reviewer debate results into an approval-ready report.",
                    description="Specialized code review worker focused on merging reviewer findings, preserving proposer/support/challenge attribution, and writing the final debate report.",
                    supported_tools=[
                        "project-source-loader",
                        "project-tree-scanner",
                        "session-history",
                        "observation-search",
                        "knowledge-rag",
                        "report-writer",
                    ],
                    supported_skills=[],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "code-review", "summary"],
                )
            ),
            "ui-automation-agent": AgentModule(
                descriptor=AgentDescriptor(
                    key="ui-automation-agent",
                    name="UI Automation Agent",
                    role="tester",
                    summary="Execute UI automation workflows with page exploration and evidence capture.",
                    description="Dedicated mode agent for UI automation tasks, browser evidence, and repeatable UI execution.",
                    supported_tools=[
                        "ui-automation-runner",
                        "ui-page-explorer",
                        "browser-automation",
                        "browser-control",
                        "dom-inspector",
                        "file-artifact-manager",
                        "report-writer",
                    ],
                    supported_skills=["playwright-e2e-testing", "playwright-cli"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "qwen-max"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "ui", "automation"],
                )
            ),
            "api-testing-agent": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-testing-agent",
                    name="API Testing Agent",
                    role="tester",
                    summary="Run API contract, status, and payload validation workflows.",
                    description="Dedicated mode agent for API interface testing with focused verification output.",
                    supported_tools=["api-test-runner", "api-docs-library", "api-tester", "knowledge-rag", "report-writer"],
                    supported_capabilities=[
                        "api.validation",
                        "api.documentation.read",
                        "performance.load_test",
                        "security.assessment",
                        "report.generate",
                    ],
                    supported_skills=["api-contract-testing", "api-test-generation", "test-data-strategy"],
                    supported_models=["gpt-5.4", "deepseek-reasoner", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "api"],
                )
            ),
            "compatibility-testing-agent": AgentModule(
                descriptor=AgentDescriptor(
                    key="compatibility-testing-agent",
                    name="Compatibility Testing Agent",
                    role="coordinator",
                    summary="编排多产品、多环境、多平台兼容性测试计划与执行。",
                    description=(
                        "兼容性测试模式主控智能体。负责产品接入信息整理、可测性探测、"
                        "环境矩阵规划、用例生成、风险审批、Runner/Provider 调度、"
                        "调用已有测试模式并汇总图片、日志、视频和 trace 证据。"
                    ),
                    supported_tools=[
                        "compatibility-test-runner",
                        "smoke-suite-runner",
                        "ui-automation-runner",
                        "api-test-runner",
                        "performance-test-runner",
                        "subagent-dispatch",
                        "knowledge-rag",
                        "api-docs-library",
                        "report-writer",
                        "file-artifact-manager",
                        "observation-search",
                        "session-history",
                    ],
                    supported_skills=COMPATIBILITY_TESTING_AGENT_SKILLS["compatibility-testing-agent"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "deepseek-reasoner"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "compatibility", "orchestration", "runner"],
                )
            ),
            "api-executor-worker": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-executor-worker",
                    name="API Executor Worker",
                    role="worker",
                    summary="Execute ready API test tasks from the task pool with HTTP calls and assertion evaluation.",
                    description="Parallel execution worker for API testing campaigns. Receives dispatched tasks, sends HTTP requests, evaluates assertions, and returns structured results.",
                    supported_tools=["api-test-runner", "api-tester", "api-docs-library", "knowledge-rag", "session-history", "observation-search", "report-writer"],
                    supported_skills=["api-contract-testing", "test-data-strategy"],
                    supported_models=["gpt-5.4", "deepseek-reasoner", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "api", "worker", "execution"],
                )
            ),
            "api-project-clarifier": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-project-clarifier",
                    name="API Project Clarifier",
                    role="planner",
                    summary="Explain and rank candidate projects when API testing scope is ambiguous.",
                    description="Presents project candidates with rationale, helps the user pick the correct project scope before any test execution begins.",
                    supported_tools=["api-docs-library", "knowledge-rag", "session-history"],
                    supported_skills=["api-contract-testing"],
                    supported_models=["gpt-5.4", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "api", "clarification"],
                )
            ),
            "api-doc-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-doc-analyst",
                    name="API Doc Analyst",
                    role="planner",
                    summary="Analyze document coverage and endpoint grouping after project selection.",
                    description="Reads API documentation structure, identifies endpoint groups, auth requirements, and coverage gaps to inform campaign planning.",
                    supported_tools=["api-docs-library", "knowledge-rag", "session-history", "report-writer"],
                    supported_skills=["api-contract-testing", "api-test-generation"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "api", "analysis"],
                )
            ),
            "api-suite-planner": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-suite-planner",
                    name="API Suite Planner",
                    role="planner",
                    summary="Convert user objectives into campaign scope and assertion strategy.",
                    description="Transforms high-level testing goals into concrete endpoint selections, assertion types, and execution ordering for the campaign.",
                    supported_tools=["api-docs-library", "api-test-runner", "knowledge-rag", "session-history", "report-writer"],
                    supported_skills=["api-contract-testing", "api-test-generation", "test-data-strategy"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "api", "planning"],
                )
            ),
            "api-precondition-planner": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-precondition-planner",
                    name="API Precondition Planner",
                    role="planner",
                    summary="Plan auth, preconditions, data dependencies, and readiness checks.",
                    description="Identifies authentication requirements, test data seeds, environment readiness, and dependency chains before campaign execution.",
                    supported_tools=["api-docs-library", "knowledge-rag", "session-history"],
                    supported_skills=["api-contract-testing", "test-data-strategy"],
                    supported_models=["gpt-5.4", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "api", "precondition"],
                )
            ),
            "api-failure-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-failure-analyst",
                    name="API Failure Analyst",
                    role="verifier",
                    summary="Analyze API test failures, determine retryability, and identify root causes.",
                    description="Reviews failed API test tasks, classifies failure types (auth/timeout/server/assertion), suggests fixes, and determines if failures are transient or permanent.",
                    supported_tools=["api-docs-library", "knowledge-rag", "session-history", "observation-search", "report-writer"],
                    supported_skills=["api-contract-testing", "test-data-strategy"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "api", "failure-analysis"],
                )
            ),
            "security-testing-agent": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-testing-agent",
                    name="Security Testing Agent",
                    role="tester",
                    summary="主控安全测试智能体，负责规划 Campaign、调度 worker、汇总结果并生成报告。",
                    description=(
                        "安全测试模式主控智能体，复刻 PentAGI 的 Primary Agent 能力。"
                        "负责解析安全测试请求、建立 Campaign、拆解任务、调度专家 worker、"
                        "聚合 Finding、计算严重级别、生成结构化报告并按需发送邮件。"
                    ),
                    supported_tools=[
                        "subagent-dispatch",
                        "knowledge-rag",
                        "report-writer",
                        "observation-search",
                        "session-history",
                    ],
                    supported_capabilities=[
                        "security.assessment",
                        "api.documentation.read",
                        "report.generate",
                    ],
                    supported_skills=["owasp-security-testing", "auth-security-testing", "input-validation-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4", "deepseek-reasoner"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "penetration", "orchestration"],
                )
            ),
            "security-doc-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-doc-analyst",
                    name="Security Doc Analyst",
                    role="analyst",
                    summary="分析安全测试文档、API 规范和目标描述，提取测试范围和关键信息。",
                    description="解析用户提供的安全测试文档、API 规范、架构图等，提取目标、端点、认证方式和测试范围。",
                    supported_tools=["knowledge-rag", "attachment-reader", "observation-search"],
                    supported_skills=["owasp-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "analysis"],
                )
            ),
            "attack-surface-planner": AgentModule(
                descriptor=AgentDescriptor(
                    key="attack-surface-planner",
                    name="Attack Surface Planner",
                    role="planner",
                    summary="根据资产信息规划攻击面和测试任务树。",
                    description="分析已发现的资产、端口、服务和 Web 应用，生成结构化的安全测试任务树和依赖关系。",
                    supported_tools=["knowledge-rag", "observation-search", "session-history"],
                    supported_skills=["owasp-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "planning"],
                )
            ),
            "security-recon-worker": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-recon-worker",
                    name="Security Recon Worker",
                    role="worker",
                    summary="执行网络侦察任务：端口扫描、服务探测、资产发现。",
                    description="专注于网络层侦察，使用 nmap、httpx、whatweb 等工具发现开放端口、服务版本和技术栈。",
                    supported_tools=[
                        "network-recon-runner",
                        "security-scan-runner",
                        "knowledge-rag",
                        "observation-search",
                    ],
                    supported_skills=[],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "recon", "worker"],
                )
            ),
            "security-auth-worker": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-auth-worker",
                    name="Security Auth Worker",
                    role="worker",
                    summary="处理认证、会话、凭证相关的安全测试任务。",
                    description="执行登录测试、会话管理验证、凭证爆破（需审批）等认证相关安全检测。",
                    supported_tools=[
                        "credential-attack-runner",
                        "web-scan-runner",
                        "knowledge-rag",
                    ],
                    supported_skills=["auth-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "auth", "worker"],
                )
            ),
            "security-web-verifier": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-web-verifier",
                    name="Security Web Verifier",
                    role="worker",
                    summary="执行 Web 应用安全验证：目录扫描、漏洞扫描、注入检测。",
                    description="使用 ffuf、nikto、nuclei、sqlmap 等工具对 Web 应用进行安全验证，发现 XSS、SQL 注入、目录遍历等漏洞。",
                    supported_tools=[
                        "web-scan-runner",
                        "security-scan-runner",
                        "knowledge-rag",
                        "observation-search",
                    ],
                    supported_skills=["owasp-security-testing", "input-validation-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "web", "worker"],
                )
            ),
            "security-api-verifier": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-api-verifier",
                    name="Security API Verifier",
                    role="worker",
                    summary="执行 API 安全验证：越权、注入、认证绕过检测。",
                    description="针对 REST/GraphQL API 进行安全验证，检测越权访问、参数注入、认证绕过等 API 安全问题。",
                    supported_tools=[
                        "web-scan-runner",
                        "security-scan-runner",
                        "knowledge-rag",
                    ],
                    supported_skills=["owasp-security-testing", "auth-security-testing", "input-validation-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "api", "worker"],
                )
            ),
            "security-host-verifier": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-host-verifier",
                    name="Security Host Verifier",
                    role="worker",
                    summary="执行主机和服务安全验证：配置审计、服务漏洞检测。",
                    description="对主机和网络服务进行安全验证，检测配置错误、弱加密、已知漏洞等主机层安全问题。",
                    supported_tools=[
                        "service-audit-runner",
                        "network-recon-runner",
                        "traffic-analysis-runner",
                        "knowledge-rag",
                    ],
                    supported_skills=["owasp-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "host", "worker"],
                )
            ),
            "security-exploit-coder": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-exploit-coder",
                    name="Security Exploit Coder",
                    role="worker",
                    summary="编写和定制漏洞利用代码（需审批）。",
                    description="根据发现的漏洞编写 PoC 代码或定制利用脚本，用于验证漏洞的可利用性。高风险操作需要明确授权。",
                    supported_tools=[
                        "exploit-workbench-runner",
                        "knowledge-rag",
                    ],
                    supported_skills=["owasp-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "exploit", "worker"],
                )
            ),
            "security-failure-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="security-failure-analyst",
                    name="Security Failure Analyst",
                    role="analyst",
                    summary="分析失败的安全测试任务，提供修复建议和替代方案。",
                    description="当安全测试任务失败时，分析失败原因、判断是否可重试、建议替代工具或参数修复方案。",
                    supported_tools=["knowledge-rag", "observation-search", "session-history"],
                    supported_skills=["owasp-security-testing"],
                    supported_models=["qwen3.6-plus", "claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "security", "failure-analysis"],
                )
            ),
            "performance-testing-agent": AgentModule(
                descriptor=AgentDescriptor(
                    key="performance-testing-agent",
                    name="Performance Testing Agent",
                    role="coordinator",
                    summary="主控性能测试智能体，负责意图采集、计划编排、执行调度和报告生成。",
                    description=(
                        "性能测试模式主控智能体。通过对话式槽位采集理解用户意图，"
                        "驱动负载建模、脚本生成、冒烟验证、正式压测、结果分析和报告投递全流程。"
                        "遵循先澄清后执行原则，目标未确认前不发起压测。"
                    ),
                    supported_tools=[
                        "performance-test-runner",
                        "perf-plan-compiler",
                        "perf-engine-select",
                        "http-probe-runner",
                        "mock-target-runner",
                        "performance-engine-runner",
                        "perf-container-manager",
                        "subagent-dispatch",
                        "knowledge-rag",
                        "api-docs-library",
                        "api-docs-ingest",
                        "report-writer",
                        "file-artifact-manager",
                        "observation-search",
                        "session-history",
                    ],
                    supported_skills=["k6-load-testing", "jmeter-load-testing"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "deepseek-reasoner"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "performance", "orchestration"],
                )
            ),
            "perf-planner": AgentModule(
                descriptor=AgentDescriptor(
                    key="perf-planner",
                    name="Perf Planner",
                    role="planner",
                    summary="将用户意图和负载模型转化为结构化 PerfPlan。",
                    description=(
                        "根据采集到的槽位信息（目标、负载参数、SLA）和负载模型，"
                        "生成完整的 PerfPlan，包含 target、workload config、smoke config、"
                        "data params、correlations 和 assertions。"
                    ),
                    supported_tools=["perf-plan-compiler", "knowledge-rag", "api-docs-library"],
                    supported_skills=["k6-load-testing", "jmeter-load-testing"],
                    supported_models=["claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "performance", "planning"],
                )
            ),
            "perf-script-builder": AgentModule(
                descriptor=AgentDescriptor(
                    key="perf-script-builder",
                    name="Perf Script Builder",
                    role="worker",
                    summary="将 PerfPlan 编译为引擎可执行脚本。",
                    description="调用 perf-plan-compiler 工具将计划转为 k6 脚本，处理数据参数和关联提取。",
                    supported_tools=["perf-plan-compiler", "knowledge-rag"],
                    supported_skills=["k6-load-testing", "jmeter-load-testing"],
                    supported_models=["claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "performance", "script"],
                )
            ),
            "perf-runner": AgentModule(
                descriptor=AgentDescriptor(
                    key="perf-runner",
                    name="Perf Runner",
                    role="worker",
                    summary="执行压测并返回原始指标，不做判定。",
                    description=(
                        "负责冒烟验证和正式压测执行。输出仅包含原始数据和引擎退出码，"
                        "不做 pass/fail 判定（判定由独立的 perf-analyst 完成）。"
                    ),
                    supported_tools=["performance-test-runner", "cli-executor"],
                    supported_skills=["k6-load-testing", "jmeter-load-testing"],
                    supported_models=["claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "performance", "execution"],
                )
            ),
            "perf-container-operator": AgentModule(
                descriptor=AgentDescriptor(
                    key="perf-container-operator",
                    name="Perf Container Operator",
                    role="worker",
                    summary="启动、检查和销毁 k6/JMeter 压测引擎容器。",
                    description=(
                        "在性能测试编排完成后负责压测引擎容器生命周期："
                        "启动 k6/JMeter Docker 容器、确认状态、测试完成后销毁或清理容器。"
                    ),
                    supported_tools=["perf-container-manager", "cli-executor"],
                    supported_skills=["k6-load-testing", "jmeter-load-testing"],
                    supported_models=["claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "performance", "container", "docker"],
                )
            ),
            "perf-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="perf-analyst",
                    name="Perf Analyst",
                    role="verifier",
                    summary="独立分析压测结果并做出 verdict 判定。",
                    description=(
                        "接收 runner 的原始指标，独立计算 SLA 达标情况、错误分类、"
                        "基线对比和拐点检测，输出结构化 PerfReport 含 verdict。"
                        "与 runner 分离确保判定独立性。"
                    ),
                    supported_tools=["perf-result-analyzer", "report-writer", "observation-search"],
                    supported_skills=["k6-load-testing", "jmeter-load-testing"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "deepseek-reasoner"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "performance", "analysis"],
                )
            ),
            "perf-failure-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="perf-failure-analyst",
                    name="Perf Failure Analyst",
                    role="analyst",
                    summary="分析性能测试失败原因并给出可重试/降级/修复建议。",
                    description=(
                        "当性能测试脚本生成、冒烟验证、正式压测或结果解析失败时，"
                        "根据 stdout/stderr、退出码、原始指标和上下文判断失败类型，"
                        "输出结构化 failure_category/root_cause/retryable/suggested_fix。"
                    ),
                    supported_tools=["knowledge-rag", "observation-search", "session-history"],
                    supported_skills=["k6-load-testing", "jmeter-load-testing"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "deepseek-reasoner"],
                    default_model="claude-sonnet-4",
                    tags=["testing", "performance", "failure-analysis"],
                )
            ),
            "smoke-testing-agent": AgentModule(
                descriptor=AgentDescriptor(
                    key="smoke-testing-agent",
                    name="Smoke Testing Agent",
                    role="coordinator",
                    summary="Coordinate smoke plan drafting, user confirmation, selected-case execution, and regression candidate capture.",
                    description="Main smoke testing coordinator. It must draft a detailed executable plan first, wait for user confirmation or revisions, and only execute the frozen approved plan.",
                    supported_tools=["smoke-suite-runner", "knowledge-rag", "report-writer", "subagent-dispatch"],
                    supported_skills=["smoke-test-planning", "api-contract-testing", "playwright-e2e-testing"],
                    supported_models=["gpt-5.4", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "smoke", "orchestration"],
                )
            ),
            "smoke-source-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="smoke-source-analyst",
                    name="Smoke Source Analyst",
                    role="analyst",
                    summary="Analyze uploaded documents, API docs, UI graph knowledge, and project credentials for smoke planning.",
                    description="Collects smoke test sources from chat attachments, RustFS API documents, UI graph knowledge, memory credentials, and future project case providers.",
                    supported_tools=["smoke-suite-runner", "knowledge-rag", "api-docs-library", "attachment-reader", "observation-search"],
                    supported_skills=["smoke-test-planning", "api-contract-testing", "playwright-e2e-testing"],
                    supported_models=["gpt-5.4", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "smoke", "source-analysis"],
                )
            ),
            "smoke-plan-designer": AgentModule(
                descriptor=AgentDescriptor(
                    key="smoke-plan-designer",
                    name="Smoke Plan Designer",
                    role="planner",
                    summary="Design detailed API/UI/health smoke execution plans for user approval.",
                    description="Produces reviewable smoke plans with methods, URLs, parameters, expected responses, UI actions, assertions, evidence requirements, and risk labels.",
                    supported_tools=["smoke-suite-runner", "knowledge-rag", "api-docs-library", "report-writer"],
                    supported_skills=["smoke-test-planning", "api-contract-testing", "playwright-e2e-testing", "test-data-strategy"],
                    supported_models=["gpt-5.4", "claude-sonnet-4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["testing", "smoke", "planning"],
                )
            ),
            "smoke-plan-reviewer": AgentModule(
                descriptor=AgentDescriptor(
                    key="smoke-plan-reviewer",
                    name="Smoke Plan Reviewer",
                    role="reviewer",
                    summary="Review smoke plans for risk, executable assertions, credential safety, and destructive operations.",
                    description="Checks generated smoke plans before execution and flags missing assertions, unclear expectations, write-operation risk, and credential leakage.",
                    supported_tools=["smoke-suite-runner", "knowledge-rag", "report-writer"],
                    supported_skills=["smoke-test-planning", "test-data-strategy"],
                    supported_models=["gpt-5.4", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "smoke", "review"],
                )
            ),
            "smoke-executor": AgentModule(
                descriptor=AgentDescriptor(
                    key="smoke-executor",
                    name="Smoke Executor",
                    role="worker",
                    summary="Execute only user-approved selected smoke cases and collect evidence.",
                    description="Runs the deterministic smoke runner against the frozen approved plan; it must not add unapproved steps or expose plaintext credentials.",
                    supported_tools=["smoke-suite-runner", "report-writer"],
                    supported_skills=["smoke-test-planning"],
                    supported_models=["gpt-5.4", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "smoke", "execution"],
                )
            ),
            "smoke-result-analyst": AgentModule(
                descriptor=AgentDescriptor(
                    key="smoke-result-analyst",
                    name="Smoke Result Analyst",
                    role="summarizer",
                    summary="Classify smoke failures, produce the readiness verdict, and promote stable cases to regression candidates.",
                    description="Analyzes smoke run outputs, writes delivery-ready summaries, and marks stable passing cases for future regression suites.",
                    supported_tools=["smoke-suite-runner", "knowledge-rag", "report-writer"],
                    supported_skills=["smoke-test-planning"],
                    supported_models=["gpt-5.4", "claude-sonnet-4"],
                    default_model="gpt-5.4",
                    tags=["testing", "smoke", "analysis", "regression"],
                )
            ),
        }

    def list(self) -> list[AgentDescriptor]:
        return [module.descriptor for module in self._agents.values()]

    def get(self, key: str) -> AgentDescriptor:
        if key not in self._agents:
            raise KeyError(f"Unknown agent: {key}")
        return self._agents[key].descriptor

    def resolve_for_message(
        self,
        message: str,
        explicit_key: str | None = None,
    ) -> AgentDescriptor:
        if explicit_key:
            return self.get(explicit_key)

        lowered = (message or "").lower()
        if any(token in lowered for token in ["cli", "terminal", "shell", "powershell", "command", "bash", "cmd"]):
            return self.get("ops-executor")
        if any(token in lowered for token in ["api", "interface", "payload", "response"]):
            return self.get("api-verifier")
        if any(token in lowered for token in ["page", "browser", "ui", "selenium", "playwright"]):
            return self.get("ui-executor")
        if any(token in lowered for token in ["history", "session report", "conversation report", "previous questions"]):
            return self.get("report-analyst")
        if any(token in lowered for token in ["report", "summary", "analysis"]):
            return self.get("report-analyst")
        if any(token in lowered for token in ["case", "plan", "scenario", "test plan"]):
            return self.get("qa-planner")
        return self.get("coordinator")
