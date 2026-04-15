from __future__ import annotations

from src.schemas.agent import SkillDescriptor


class SkillRegistry:
    def __init__(self) -> None:
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

    def list(self) -> list[SkillDescriptor]:
        return list(self._skills.values())

    def get(self, key: str) -> SkillDescriptor:
        if key not in self._skills:
            raise KeyError(f"Unknown skill: {key}")
        return self._skills[key]

    def get_many(self, keys: list[str]) -> list[SkillDescriptor]:
        return [self._skills[key] for key in keys if key in self._skills]
