"""Read fixed scene assets through the existing project-bound knowledge tool."""
from copy import deepcopy
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)
SCENE_KNOWLEDGE_PROTOCOL = "recording-scene-knowledge-v1"


class SceneKnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["scene_search", "scene_read"]
    query: str = Field(default="", max_length=1000)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=100000)
    scene_id: str | None = Field(default=None, min_length=1, max_length=160)
    version_id: str | None = Field(default=None, min_length=1, max_length=160)
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_fixed_identity(self):
        if self.operation == "scene_read" and not (self.scene_id and self.version_id and self.content_hash):
            raise ValueError("场景正文读取需要scene_id、version_id和content_hash")
        if self.operation == "scene_search" and any((self.scene_id, self.version_id, self.content_hash)):
            raise ValueError("场景目录查询不能冒充固定版本正文")
        return self


def scene_alias_evidence(version):
    original = {step["data"]["seq"]: step for step in version.source["original_draft"]["steps"]}
    return [{**alias.model_dump(mode="json"),
        "target": deepcopy(original[alias.source_seq]["data"]["event"]["target"]),
        "page": deepcopy(original[alias.source_seq]["data"]["event"]["page"])} for alias in version.content.aliases]


class RecordingSceneKnowledge:
    def __init__(self, *, scenes, projects, sessions):
        self._scenes, self._projects, self._sessions = scenes, projects, sessions

    async def query(self, request, context):
        session = await self._sessions.get_session(context.session_id)
        if session is None or not session.project_id:
            raise ValueError("场景查询需要已绑定正式项目的持久会话")
        project_id = session.project_id
        supplied_project = context.context_bundle.get("project_id")
        if supplied_project and supplied_project != project_id:
            raise ValueError("场景查询上下文与持久会话项目不一致")
        await self._projects.get(project_id)
        if request.operation == "scene_search":
            catalog = await self._scenes.search(project_id, request.query, request.limit, request.offset)
            logger.info("recording_scene_catalog_read", extra={"session_id":session.id,"project_id":project_id,
                "offset":request.offset,"item_count":len(catalog["items"]),"has_more":catalog["has_more"]})
            return {"status":"completed","ok":True,"summary":"场景目录已读取；请用版本和哈希读取完整正文。",
                "protocol":SCENE_KNOWLEDGE_PROTOCOL,"project_id":project_id,"scene_catalog":catalog,
                "chunks":[],"verification_status":"not_executed"}
        bundle = await self._scenes.get(project_id, request.scene_id, request.version_id)
        version = bundle.version
        if version.content_hash != request.content_hash:
            raise ValueError("场景固定版本内容hash不一致")
        knowledge = {"evidence_id":f"ui_recording_scene:{version.scene_id}:{version.id}:{version.content_hash}",
            "project_id":project_id,"scene_id":version.scene_id,"version_id":version.id,"version":version.version,
            "content_hash":version.content_hash,"content":version.content.model_dump(mode="json"),
            "source":deepcopy(version.source),"aliases":scene_alias_evidence(version),"verification_status":"not_executed"}
        logger.info("recording_scene_knowledge_read", extra={"session_id":session.id,"project_id":project_id,
            "scene_id":version.scene_id,"scene_version_id":version.id,"content_hash":version.content_hash})
        return {"status":"completed","ok":True,"summary":"固定场景完整知识已读取；整理状态不代表测试通过。",
            "protocol":SCENE_KNOWLEDGE_PROTOCOL,"project_id":project_id,"scene_knowledge":knowledge,
            "chunks":[],"verification_status":"not_executed"}
