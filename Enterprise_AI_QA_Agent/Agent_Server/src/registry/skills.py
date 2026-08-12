from __future__ import annotations

import re
from pathlib import Path

from src.schemas.agent import SkillDescriptor


class SkillRegistry:
    def __init__(self, skills_root: Path | None = None) -> None:
        self._skills_root = skills_root or (Path(__file__).resolve().parents[1] / "SKILLS")
        self._base_skills: dict[str, SkillDescriptor] = {
            "playwright-cli": SkillDescriptor(
                key="playwright-cli",
                name="playwright-cli",
                summary="Use CLI-shaped browser automation commands for UI exploration and testing.",
                description="Loads the local SKILLS/playwright-cli/SKILL.md instructions and maps commands to the Agent_Server Python Playwright runtime.",
                recommended_agents=["ui-executor"],
                tags=["ui", "automation", "playwright", "skill-file"],
                tool_keys=["browser-automation", "browser-control"],
            ),
            "ci-pipeline-review": SkillDescriptor(
                key="ci-pipeline-review",
                name="CI Pipeline Review",
                summary="Review CI test stages, caching, parallelism, selective execution, and failure visibility.",
                description="审查 CI/CD 测试流水线的并行化、缓存、选择性执行、阶段依赖和可靠性。",
                recommended_agents=["code-review-agent", "code-architecture-reviewer", "code-maintainability-reviewer"],
                tags=["code-review", "ci", "quality"],
                tool_keys=["project-diff-reader", "project-file-reader", "knowledge-rag", "report-writer"],
            ),
            "test-coverage-review": SkillDescriptor(
                key="test-coverage-review",
                name="Test Coverage Review",
                summary="Map changed code paths to tests and identify unverified branches and failure paths.",
                description="根据 diff、覆盖率配置和风险路径识别未覆盖分支、异常路径与缺失测试。",
                recommended_agents=["code-review-agent", "code-testability-reviewer"],
                tags=["code-review", "coverage", "testability"],
                tool_keys=["project-diff-reader", "project-file-reader", "observation-search", "report-writer"],
            ),
            "flaky-test-analysis": SkillDescriptor(
                key="flaky-test-analysis",
                name="Flaky Test Analysis",
                summary="Diagnose time, order, environment, isolation, and retry-related test instability.",
                description="识别和分析由时间、顺序、环境、共享状态、外部依赖或重试造成的不稳定测试。",
                recommended_agents=["code-testability-reviewer"],
                tags=["code-review", "reliability", "testability"],
                tool_keys=["project-diff-reader", "project-file-reader", "observation-search", "session-history", "report-writer"],
            ),
            "tdd-review": SkillDescriptor(
                key="tdd-review",
                name="TDD Review",
                summary="Review red-green-refactor evidence and regression tests for behavior changes.",
                description="审查红-绿-重构纪律、真实失败用例和行为变更测试证据。",
                recommended_agents=["code-correctness-reviewer"],
                tags=["code-review", "tdd", "correctness"],
                tool_keys=["project-diff-reader", "project-file-reader", "report-writer"],
            ),
            "test-data-strategy": SkillDescriptor(
                key="test-data-strategy",
                name="Test Data Strategy",
                summary="Review deterministic, isolated, private, and cleanable test data strategies.",
                description="审查测试数据工厂、Builder、fixture、数据库播种、随机种子、隐私保护和清理策略。",
                recommended_agents=["code-testability-reviewer", "api-testing-agent", "api-suite-planner"],
                tags=["testing", "data", "reliability"],
                tool_keys=["project-file-reader", "project-diff-reader", "api-docs-library", "report-writer"],
            ),
            "owasp-security-testing": SkillDescriptor(
                key="owasp-security-testing",
                name="OWASP Security Testing",
                summary="Review OWASP Top 10 risks at trust boundaries without executing attack payloads.",
                description="按 OWASP Top 10 审查注入、访问控制、认证、安全配置、SSRF、敏感数据和依赖风险。",
                recommended_agents=["code-security-reviewer"],
                tags=["code-review", "security", "owasp"],
                tool_keys=["project-file-reader", "project-diff-reader", "knowledge-rag", "report-writer"],
            ),
            "api-contract-testing": SkillDescriptor(
                key="api-contract-testing",
                name="API Contract Testing",
                summary="Validate API requests and responses against OpenAPI, JSON Schema, and consumer contracts.",
                description="根据 OpenAPI、Swagger、JSON Schema 和消费者契约审查 API 请求/响应与版本兼容性。",
                recommended_agents=["api-testing-agent", "api-executor-worker", "api-doc-analyst", "api-suite-planner"],
                tags=["api", "contract", "verification"],
                tool_keys=["api-docs-library", "api-tester", "api-test-runner", "report-writer"],
            ),
            "api-test-generation": SkillDescriptor(
                key="api-test-generation",
                name="API Test Generation",
                summary="Generate reviewable API scenarios for CRUD, auth, boundaries, errors, pagination, and idempotency.",
                description="从 OpenAPI 或接口文档生成可审查的 API 测试场景矩阵。",
                recommended_agents=["api-testing-agent", "api-doc-analyst", "api-suite-planner"],
                tags=["api", "planning", "generation"],
                tool_keys=["api-docs-library", "api-test-runner", "api-tester", "report-writer"],
            ),
            "playwright-e2e-testing": SkillDescriptor(
                key="playwright-e2e-testing",
                name="Playwright E2E Testing",
                summary="Plan and execute user-centered Web UI exploration, scenarios, assertions, and evidence collection.",
                description="使用 Playwright 规划和执行基于用户行为的 Web UI 探索、端到端场景、断言与证据采集。",
                recommended_agents=["ui-automation-agent", "ui-executor"],
                tags=["ui", "e2e", "playwright", "automation"],
                tool_keys=[
                    "ui-automation-runner",
                    "ui-page-explorer",
                    "browser-automation",
                    "browser-control",
                    "dom-inspector",
                    "file-artifact-manager",
                    "report-writer",
                ],
            ),
            "k6-load-testing": SkillDescriptor(
                key="k6-load-testing",
                name="k6 Load Testing",
                summary="Plan guarded k6 workloads, thresholds, smoke gates, execution, and metric analysis.",
                description="使用系统现有 k6 适配器规划负载模型、阈值、冒烟闸门和指标分析。",
                recommended_agents=["performance-testing-agent", "perf-planner", "perf-runner", "perf-analyst"],
                tags=["performance", "load", "k6"],
                tool_keys=["perf-plan-compiler", "perf-engine-select", "performance-test-runner", "performance-engine-runner", "perf-result-analyzer", "perf-container-manager"],
            ),
            "jmeter-load-testing": SkillDescriptor(
                key="jmeter-load-testing",
                name="JMeter Load Testing",
                summary="Plan guarded JMeter flows, non-GUI execution, smoke gates, and statistical analysis.",
                description="使用系统现有 JMeter 适配器规划复杂流程负载、非 GUI 执行和统计分析。",
                recommended_agents=["performance-testing-agent", "perf-planner", "perf-runner", "perf-analyst"],
                tags=["performance", "load", "jmeter"],
                tool_keys=["perf-plan-compiler", "perf-engine-select", "performance-test-runner", "performance-engine-runner", "perf-result-analyzer", "perf-container-manager"],
            ),
            "auth-security-testing": SkillDescriptor(
                key="auth-security-testing", name="Auth Security Testing",
                summary="Review and test authentication, sessions, tokens, OAuth, and authorization boundaries.",
                description="审查和规划 JWT、Session、OAuth、对象级授权、权限提升和认证绕过测试。",
                recommended_agents=["security-testing-agent", "security-auth-worker", "security-api-verifier"],
                tags=["security", "auth", "authorization"],
                tool_keys=["security-scan-runner", "credential-attack-runner", "web-scan-runner", "report-writer"],
            ),
            "input-validation-security-testing": SkillDescriptor(
                key="input-validation-security-testing", name="Input Validation Security Testing",
                summary="Review and test validation, injection, upload, encoding, CSRF, and boundary weaknesses.",
                description="审查和规划参数、表单、文件上传、编码、边界值、注入与校验绕过测试。",
                recommended_agents=["security-testing-agent", "security-web-verifier", "security-api-verifier"],
                tags=["security", "validation", "injection"],
                tool_keys=["security-scan-runner", "web-scan-runner", "report-writer"],
            ),
            "smoke-test-planning": SkillDescriptor(
                key="smoke-test-planning",
                name="Smoke Test Planning",
                summary="Plan confirmed, evidence-backed core-path smoke tests with explicit safety and readiness gates.",
                description="规划和审查核心链路冒烟测试、准入门槛、可逆性、环境护栏、用户确认和证据沉淀。",
                recommended_agents=["smoke-testing-agent", "smoke-plan-designer", "smoke-plan-reviewer", "smoke-executor", "smoke-result-analyst"],
                tags=["smoke", "approval", "readiness"],
                tool_keys=["smoke-suite-runner", "report-writer", "knowledge-rag"],
            ),
        }
        self._skills: dict[str, SkillDescriptor] = {}
        self.reload()

    def reload(self) -> None:
        self._skills = dict(self._base_skills)
        self._load_filesystem_skills()

    def list(self) -> list[SkillDescriptor]:
        return list(self._skills.values())

    def get(self, key: str) -> SkillDescriptor:
        if key not in self._skills:
            raise KeyError(f"Unknown skill: {key}")
        return self._skills[key]

    def get_many(self, keys: list[str]) -> list[SkillDescriptor]:
        return [self._skills[key] for key in keys if key in self._skills]

    @property
    def skills_root(self) -> Path:
        return self._skills_root

    def _load_filesystem_skills(self) -> None:
        skills_root = self._skills_root
        if not skills_root.exists():
            return
        for skill_file in sorted(skills_root.glob("*/SKILL.md")):
            key = skill_file.parent.name
            frontmatter, body = self._parse_skill_file(skill_file)
            name = str(frontmatter.get("name") or key)
            description = str(frontmatter.get("description") or self._first_sentence(body) or "Filesystem skill.")
            existing = self._skills.get(key)
            self._skills[key] = SkillDescriptor(
                key=key,
                name=name,
                summary=existing.summary if existing else description,
                description=description,
                recommended_agents=existing.recommended_agents if existing else ["coordinator"],
                tags=list(dict.fromkeys([*(existing.tags if existing else []), "skill-file"])),
                tool_keys=self._parse_list(frontmatter.get("tools")) or (existing.tool_keys if existing else []),
            )

    def _parse_skill_file(self, path: Path) -> tuple[dict[str, str], str]:
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}, content
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.S)
        if not match:
            return {}, content
        frontmatter: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, sep, value = line.partition(":")
            if sep:
                frontmatter[key.strip()] = value.strip().strip('"').strip("'")
        return frontmatter, match.group(2)

    def _first_sentence(self, text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip(" #")
            if stripped:
                return stripped[:180]
        return ""

    @staticmethod
    def _parse_list(value: str | None) -> list[str]:
        raw = str(value or "").strip().strip("[]")
        return [item.strip().strip('"').strip("'") for item in raw.split(",") if item.strip()]
