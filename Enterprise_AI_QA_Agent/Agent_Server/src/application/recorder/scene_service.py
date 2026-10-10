"""Editable knowledge derived from fixed recording evidence, through the original case lifecycle."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import logging
from uuid import uuid4

from src.application.recorder.recording_case_draft_service import build_recording_draft_payload
from src.application.test_cases.case_store import DuplicateCaseKeyError
from src.application.test_cases.version_content import version_content_hash
from src.infrastructure.recording_scene_store import scene_hash
from src.schemas.case_management import TestCaseDraftCreateRequest, TestCaseSourceRef
from src.schemas.recording_scene import SceneContent, SceneStep, SceneVersion

logger = logging.getLogger(__name__)
RULE_VERSION = "recording-scene-v1"


class RecordingSceneService:
    def __init__(self, *, store, recorder_service, project_service, case_service):
        self._store, self._recorder, self._projects, self._cases = store, recorder_service, project_service, case_service

    async def create(self, project_id, request):
        await self._projects.require_active(project_id)
        request_hash = scene_hash(request.model_dump(mode="json"))
        original = await self._store.by_request(project_id, request.request_id)
        if original:
            if original.version.request_hash != request_hash: raise ValueError("scene request identity reused with different content")
            return original
        session = await self._recorder.get_session(request.recording_id)
        if session is None or session.project_id != project_id: raise KeyError("recording not found in project")
        if getattr(session.status, "value", session.status) != "completed": raise ValueError("only completed recordings can become scenes")
        events = await self._recorder.get_events(session.id)
        draft = build_recording_draft_payload(session, events)
        if not draft.steps: raise ValueError("recording has no action steps")
        content = SceneContent(name=draft.title, steps=[SceneStep(source_seq=step.data["seq"],action=step.action,expected=step.expected) for step in draft.steps],
            assertions=draft.assertions,preconditions=draft.preconditions,execution_notes=draft.execution_notes)
        source = {"recording_id":session.id,"original_draft":draft.model_dump(mode="json"),
            "original_hash":scene_hash(draft.model_dump(mode="json")),"finalize_metrics":deepcopy(session.finalize_metrics),
            "event_count":len(events),"excluded_sequences":[],"change_reason":"created from completed recording"}
        scene_id = str(uuid4())
        return await self._store.save(self._version(project_id,scene_id,1,None,request.request_id,request_hash,content,source),create=True)

    def _version(self, project_id, scene_id, number, parent, request_id, request_hash, content, source):
        return SceneVersion(id=str(uuid4()),scene_id=scene_id,project_id=project_id,version=number,parent_version_id=parent,
            request_id=request_id,request_hash=request_hash,created_at=datetime.now(timezone.utc),content=content,source=source,
            content_hash=scene_hash({"content":content.model_dump(mode="json"),"source":source}))

    async def get(self, project_id, scene_id, version_id=None):
        await self._projects.get(project_id)
        return await self._store.get(project_id,scene_id,version_id)

    async def list(self, project_id, limit, offset):
        await self._projects.get(project_id)
        return await self._store.list(project_id,limit,offset)

    async def versions(self, project_id, scene_id):
        await self._projects.get(project_id)
        return await self._store.versions(project_id,scene_id)

    async def by_request(self, project_id, request_id):
        await self._projects.get(project_id)
        return await self._store.by_request(project_id,request_id)

    async def version_by_request(self, project_id, scene_id, request_id):
        await self._projects.get(project_id)
        return await self._store.version_by_request(project_id,scene_id,request_id)

    async def update(self, project_id, scene_id, request):
        await self._projects.require_active(project_id)
        parent = (await self.get(project_id,scene_id,request.parent_version_id)).version
        source = deepcopy(parent.source)
        self._validate_content(request.content,source)
        selected = {s.source_seq for s in request.content.steps}
        source["excluded_sequences"] = [s["data"]["seq"] for s in source["original_draft"]["steps"] if s["data"]["seq"] not in selected]
        source["change_reason"] = request.change_reason
        return await self._store.save(self._version(project_id,scene_id,parent.version+1,parent.id,request.request_id,
            scene_hash(request.model_dump(mode="json")),request.content,source))

    def _validate_content(self, content, source):
        draft = source["original_draft"]
        if scene_hash(draft) != source["original_hash"]: raise ValueError("scene original recording snapshot hash mismatch")
        steps = {s["data"]["seq"]:s for s in draft["steps"]}
        aliases = {}
        for alias in content.aliases:
            if alias.name in aliases: raise ValueError("scene alias names must be unique")
            if alias.source_seq not in steps or not steps[alias.source_seq]["data"]["event"].get("target"):
                raise ValueError("scene alias requires an original recorded target")
            aliases[alias.name] = steps[alias.source_seq]["data"]["event"]["target"]
        for step in content.steps:
            if step.source_seq not in steps: raise ValueError("scene step must reference an original action sequence")
            if step.alias and (step.alias not in aliases or aliases[step.alias] != steps[step.source_seq]["data"]["event"].get("target")):
                raise ValueError("scene step alias does not identify its original target")

    def _case_key(self, version):
        return f"ui_scene_{version.scene_id.replace('-', '_')}_v{version.version}"

    async def get_draft(self, project_id, scene_id, version_id):
        version = (await self.get(project_id,scene_id,version_id)).version
        case = await self._cases.get_case_by_key(project_id,self._case_key(version))
        if case is None: return None
        versions = await self._cases.list_versions(case.id)
        original = next((v for v in versions if v.version==1),None)
        if (original is None or original.model_key != RULE_VERSION or original.content_hash != version_content_hash(original)
            or not any(r.source_type=="ui_recording_scene" and r.source_id==scene_id and r.version==version_id
                and r.metadata.get("content_hash")==version.content_hash for r in original.source_refs)):
            raise ValueError("scene draft identity or hash mismatch")
        logger.info("recording_scene_draft_recovered",extra={"scene_id":scene_id,"scene_version_id":version_id,"case_id":case.id})
        return {"case":case,"version":original,"scene_version_id":version_id}

    async def create_draft(self, project_id, scene_id, version_id):
        await self._projects.require_active(project_id)
        original = await self.get_draft(project_id,scene_id,version_id)
        if original: return original
        version = (await self.get(project_id,scene_id,version_id)).version
        if version.content.status=="archived": raise ValueError("archived scene version cannot create a new draft")
        self._validate_content(version.content,version.source)
        payload = deepcopy(version.source["original_draft"])
        originals = {s["data"]["seq"]:s for s in payload["steps"]}
        steps = []
        for index, edit in enumerate(version.content.steps,1):
            step = deepcopy(originals[edit.source_seq])
            step.update(order=index,action=edit.action,expected=edit.expected)
            step["data"]["business_alias"] = edit.alias
            steps.append(step)
        payload.update(case_key=self._case_key(version),title=version.content.name,steps=steps,
            assertions=[v.model_dump(mode="json") for v in version.content.assertions],
            preconditions=version.content.preconditions,execution_notes=version.content.execution_notes,
            model_key=RULE_VERSION,prompt_version=RULE_VERSION,skill_versions={"recorder":RULE_VERSION},
            generation_metadata={"scene_version_id":version.id,"scene_content_hash":version.content_hash})
        payload["test_data"]["scene"] = {"scene_id":scene_id,"version_id":version.id,"content_hash":version.content_hash,
            "description":version.content.description,
            "aliases":[{**a.model_dump(mode="json"),
                "target":deepcopy(originals[a.source_seq]["data"]["event"]["target"]),
                "page":deepcopy(originals[a.source_seq]["data"]["event"]["page"])} for a in version.content.aliases],
            "alias_usage":"业务别名引用固定录制定位和页面范围；执行步骤仍读取各自的完整data.event，不用别名覆盖原定位。",
            "excluded_sequences":version.source["excluded_sequences"]}
        payload["source_refs"].append(TestCaseSourceRef(source_type="ui_recording_scene",source_id=scene_id,version=version.id,
            label=version.content.name,metadata={"content_hash":version.content_hash,"original_hash":version.source["original_hash"]}).model_dump(mode="json"))
        try:
            bundle = await self._cases.create_draft(project_id=project_id,payload=TestCaseDraftCreateRequest.model_validate(payload))
        except DuplicateCaseKeyError:
            original = await self.get_draft(project_id,scene_id,version_id)
            if original is None: raise
            return original
        logger.info("recording_scene_draft_created",extra={"scene_id":scene_id,"scene_version_id":version_id,"project_id":project_id,"case_id":bundle.case.id})
        return {"case":bundle.case,"version":bundle.version,"scene_version_id":version_id}
