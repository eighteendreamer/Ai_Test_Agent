from __future__ import annotations

from dataclasses import dataclass

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
                    description="Acts like the Claude Code coordinator: chooses the next execution path, synthesizes context, and orchestrates test work.",
                    supported_tools=[
                        "workflow-router",
                        "subagent-dispatch",
                        "knowledge-rag",
                        "test-case-generator",
                        "report-writer",
                    ],
                    supported_skills=[
                        "requirements-analysis",
                        "risk-scoping",
                        "report-synthesis",
                    ],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "qwen-max"],
                    default_model="claude-sonnet-4",
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
                        "knowledge-rag",
                        "test-case-generator",
                        "report-writer",
                    ],
                    supported_skills=["requirements-analysis", "case-design"],
                    supported_models=["claude-sonnet-4", "gpt-5.4", "deepseek-reasoner"],
                    default_model="gpt-5.4",
                    tags=["planning", "qa"],
                )
            ),
            "ui-executor": AgentModule(
                descriptor=AgentDescriptor(
                    key="ui-executor",
                    name="UI Executor",
                    role="worker",
                    summary="Execute browser flows, inspect pages, and collect UI evidence.",
                    description="Runs browser automation, DOM inspection, and evidence capture for UI testing.",
                    supported_tools=[
                        "browser-automation",
                        "dom-inspector",
                        "file-artifact-manager",
                        "report-writer",
                    ],
                    supported_skills=["ui-exploration", "artifact-collection"],
                    supported_models=["claude-sonnet-4", "qwen-max"],
                    default_model="claude-sonnet-4",
                    tags=["ui", "execution"],
                )
            ),
            "api-verifier": AgentModule(
                descriptor=AgentDescriptor(
                    key="api-verifier",
                    name="API Verifier",
                    role="verifier",
                    summary="Validate APIs, payloads, and structured assertions.",
                    description="Runs API checks and produces assertion-ready verification summaries.",
                    supported_tools=[
                        "api-tester",
                        "knowledge-rag",
                        "report-writer",
                    ],
                    supported_skills=["api-validation", "assertion-design"],
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
                    supported_tools=["report-writer", "knowledge-rag"],
                    supported_skills=["report-synthesis"],
                    supported_models=["claude-sonnet-4", "gpt-5.4"],
                    default_model="claude-sonnet-4",
                    tags=["reporting"],
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

        lowered = message.lower()
        if any(token in lowered for token in ["api", "interface", "payload", "response"]):
            return self.get("api-verifier")
        if any(token in lowered for token in ["page", "browser", "ui", "selenium", "playwright"]):
            return self.get("ui-executor")
        if any(token in lowered for token in ["report", "summary", "analysis", "结论", "报告"]):
            return self.get("report-analyst")
        if any(token in lowered for token in ["case", "plan", "scenario", "用例", "测试点"]):
            return self.get("qa-planner")
        return self.get("coordinator")
