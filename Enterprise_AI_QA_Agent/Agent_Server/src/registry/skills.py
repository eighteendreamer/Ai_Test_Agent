from __future__ import annotations

import re
from pathlib import Path

from src.schemas.agent import SkillDescriptor


class SkillRegistry:
    def __init__(self, skills_root: Path | None = None) -> None:
        self._skills_root = skills_root or (Path(__file__).resolve().parents[1] / "SKILLS")
        self._base_skills: dict[str, SkillDescriptor] = {
            "requirements-analysis": SkillDescriptor(
                key="requirements-analysis",
                name="Requirements Analysis",
                summary="Extract business goals, acceptance criteria, and testing boundaries.",
                description="Normalizes user intent into structured requirements and constraints.",
                recommended_agents=["coordinator", "qa-planner"],
                tags=["planning"],
                tool_keys=["knowledge-rag", "api-docs-library", "attachment-reader"],
            ),
            "risk-scoping": SkillDescriptor(
                key="risk-scoping",
                name="Risk Scoping",
                summary="Identify functional, UI, API, and regression risks.",
                description="Prioritizes what to validate first for a given task or release scope.",
                recommended_agents=["coordinator", "qa-planner"],
                tags=["risk", "planning"],
                tool_keys=["knowledge-rag", "observation-search", "session-history"],
            ),
            "case-design": SkillDescriptor(
                key="case-design",
                name="Case Design",
                summary="Generate executable test cases and assertions.",
                description="Transforms scenarios into structured QA cases with expected outcomes.",
                recommended_agents=["qa-planner"],
                tags=["qa"],
                tool_keys=["knowledge-rag", "test-case-generator", "test-case-xlsx-exporter", "report-writer"],
            ),
            "ui-exploration": SkillDescriptor(
                key="ui-exploration",
                name="UI Exploration",
                summary="Explore page state, selectors, and interactive behaviors.",
                description="Guides the runtime while inspecting or traversing browser interfaces.",
                recommended_agents=["ui-executor"],
                tags=["ui", "automation"],
                tool_keys=["ui-page-explorer", "browser-control", "dom-inspector"],
            ),
            "playwright-cli": SkillDescriptor(
                key="playwright-cli",
                name="playwright-cli",
                summary="Use CLI-shaped browser automation commands for UI exploration and testing.",
                description="Loads the local SKILLS/playwright-cli/SKILL.md instructions and maps commands to the Agent_Server Python Playwright runtime.",
                recommended_agents=["ui-executor"],
                tags=["ui", "automation", "playwright", "skill-file"],
                tool_keys=["browser-automation", "browser-control"],
            ),
            "artifact-collection": SkillDescriptor(
                key="artifact-collection",
                name="Artifact Collection",
                summary="Persist screenshots, traces, logs, and execution evidence.",
                description="Collects QA artifacts in a structured way for later replay or reporting.",
                recommended_agents=["ui-executor", "report-analyst"],
                tags=["artifact"],
                tool_keys=["file-artifact-manager", "attachment-reader"],
            ),
            "api-validation": SkillDescriptor(
                key="api-validation",
                name="API Validation",
                summary="Validate contracts, payloads, and response assertions.",
                description="Shapes API checks into reproducible verification steps.",
                recommended_agents=["api-verifier"],
                tags=["api", "verification"],
                tool_keys=["api-test-runner", "api-tester", "api-docs-library"],
            ),
            "assertion-design": SkillDescriptor(
                key="assertion-design",
                name="Assertion Design",
                summary="Formalize pass/fail expectations for QA checks.",
                description="Defines structured assertions for UI, API, and report outputs.",
                recommended_agents=["api-verifier", "qa-planner"],
                tags=["verification"],
                tool_keys=["api-tester", "observation-search"],
            ),
            "report-synthesis": SkillDescriptor(
                key="report-synthesis",
                name="Report Synthesis",
                summary="Summarize evidence into delivery-ready findings.",
                description="Converts runtime evidence into human-readable reports and conclusions.",
                recommended_agents=["coordinator", "report-analyst"],
                tags=["reporting"],
                tool_keys=["report-writer", "session-history", "session-timeline", "observation-search"],
            ),
            "mail-capability": SkillDescriptor(
                key="mail-capability",
                name="Mail Capability",
                summary="Safety skill for all Agent Mailbox tool invocations.",
                description="Provides one provider-neutral API and enforces confirmation, credential hygiene, and attachment safety for the globally active mailbox.",
                recommended_agents=["coordinator", "ops-executor"],
                tags=["mail", "safety", "communication"],
                tool_keys=[
                    "mail-status", "mail-send", "mail-confirm", "mail-list", "mail-read", "mail-search",
                    "mail-reply", "mail-forward", "mail-trash", "mail-download-attachment",
                ],
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
        }
        self._skills: dict[str, SkillDescriptor] = {
            "requirements-analysis": SkillDescriptor(
                key="requirements-analysis",
                name="Requirements Analysis",
                summary="Extract business goals, acceptance criteria, and testing boundaries.",
                description="Normalizes user intent into structured requirements and constraints.",
                recommended_agents=["coordinator", "qa-planner"],
                tags=["planning"],
            ),
            "risk-scoping": SkillDescriptor(
                key="risk-scoping",
                name="Risk Scoping",
                summary="Identify functional, UI, API, and regression risks.",
                description="Prioritizes what to validate first for a given task or release scope.",
                recommended_agents=["coordinator", "qa-planner"],
                tags=["risk", "planning"],
            ),
            "case-design": SkillDescriptor(
                key="case-design",
                name="Case Design",
                summary="Generate executable test cases and assertions.",
                description="Transforms scenarios into structured QA cases with expected outcomes.",
                recommended_agents=["qa-planner"],
                tags=["qa"],
            ),
            "ui-exploration": SkillDescriptor(
                key="ui-exploration",
                name="UI Exploration",
                summary="Explore page state, selectors, and interactive behaviors.",
                description="Guides the runtime while inspecting or traversing browser interfaces.",
                recommended_agents=["ui-executor"],
                tags=["ui", "automation"],
            ),
            "playwright-cli": SkillDescriptor(
                key="playwright-cli",
                name="playwright-cli",
                summary="Use CLI-shaped browser automation commands for UI exploration and testing.",
                description="Loads the local SKILLS/playwright-cli/SKILL.md instructions and maps commands to the Agent_Server Python Playwright runtime.",
                recommended_agents=["ui-executor"],
                tags=["ui", "automation", "playwright", "skill-file"],
            ),
            "artifact-collection": SkillDescriptor(
                key="artifact-collection",
                name="Artifact Collection",
                summary="Persist screenshots, traces, logs, and execution evidence.",
                description="Collects QA artifacts in a structured way for later replay or reporting.",
                recommended_agents=["ui-executor", "report-analyst"],
                tags=["artifact"],
            ),
            "api-validation": SkillDescriptor(
                key="api-validation",
                name="API Validation",
                summary="Validate contracts, payloads, and response assertions.",
                description="Shapes API checks into reproducible verification steps.",
                recommended_agents=["api-verifier"],
                tags=["api", "verification"],
            ),
            "assertion-design": SkillDescriptor(
                key="assertion-design",
                name="Assertion Design",
                summary="Formalize pass/fail expectations for QA checks.",
                description="Defines structured assertions for UI, API, and report outputs.",
                recommended_agents=["api-verifier", "qa-planner"],
                tags=["verification"],
            ),
            "report-synthesis": SkillDescriptor(
                key="report-synthesis",
                name="Report Synthesis",
                summary="Summarize evidence into delivery-ready findings.",
                description="Converts runtime evidence into human-readable reports and conclusions.",
                recommended_agents=["coordinator", "report-analyst"],
                tags=["reporting"],
            ),
        }
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
