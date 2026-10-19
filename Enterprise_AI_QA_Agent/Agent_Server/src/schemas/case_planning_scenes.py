"""Opt-in scene query wire; historical stage schemas remain unchanged."""
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from src.schemas.case_planning import PlanningQuery
from src.schemas.case_planning_stages import LogicalPlanningDecisionV3, LogicalPlanningDecisionV4, EvidencePlanningDecisionV3
from src.schemas.case_planning_references import (
    ReferencedEvidenceDecision, ReferencedEvidenceDecisionV2,
)

SCENE_QUERY_PROTOCOL = "case-planning-scene-query-v1"
SCENE_QUERY_PROMPT_VERSION = "case-planning-scene-query-v13"


class ScenePlanningQuery(PlanningQuery):
    kind: Literal["cases", "documents", "document", "recordings", "recording", "tools", "tool", "scenes", "scene"]
    version_id: str | None = Field(default=None, min_length=1, max_length=160)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_fixed_scene(self):
        if self.kind == "scene" and not (self.source_id and self.version_id and self.content_hash):
            raise ValueError("场景正文读取需要scene source_id、version_id和完整content_hash")
        if self.kind != "scene" and (self.version_id is not None or self.content_hash is not None):
            raise ValueError("场景版本和哈希仅用于固定场景正文读取")
        if self.kind == "scenes" and self.source_id is not None:
            raise ValueError("场景目录查询不能冒充固定正文读取")
        return self

    @model_serializer(mode="wrap")
    def preserve_optional_scene_identity(self, handler):
        data = handler(self)
        for key in ("operation", "version_id", "content_hash"):
            if getattr(self, key) is None:
                data.pop(key, None)
        return data


class SceneBusinessPlanningQuery(ScenePlanningQuery):
    kind: Literal["cases", "documents", "document", "recordings", "recording", "scenes", "scene"]
    operation: None = None


class SceneLogicalPlanningDecisionV3(LogicalPlanningDecisionV3):
    queries: list[SceneBusinessPlanningQuery] = Field(default_factory=list, max_length=8)


class SceneLogicalPlanningDecisionV4(LogicalPlanningDecisionV4):
    queries: list[SceneBusinessPlanningQuery] = Field(default_factory=list, max_length=8)


class SceneEvidencePlanningDecision(EvidencePlanningDecisionV3):
    queries: list[ScenePlanningQuery] = Field(default_factory=list, max_length=8)


class SceneReferencedEvidenceDecision(ReferencedEvidenceDecision):
    queries: list[ScenePlanningQuery] = Field(default_factory=list, max_length=8)


class SceneReferencedEvidenceDecisionV2(ReferencedEvidenceDecisionV2):
    queries: list[ScenePlanningQuery] = Field(default_factory=list, max_length=8)


def scene_decision_types(item_operations, references):
    effects_type = (SceneReferencedEvidenceDecisionV2 if references == "case-planning-evidence-references-v2"
        else SceneReferencedEvidenceDecision if references else SceneEvidencePlanningDecision)
    return (SceneLogicalPlanningDecisionV4 if item_operations else SceneLogicalPlanningDecisionV3,
        effects_type)
