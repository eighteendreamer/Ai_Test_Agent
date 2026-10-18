"""Actual new capture and PG ToolJob, through existing safe Agent tool; no model."""
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.runtime.tool_job_service import ToolJobService
from src.application.runtime.tool_runtime_service import ToolExecutionContext, ToolRuntimeService
from src.application.recorder.scene_knowledge import RecordingSceneKnowledge
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import PostgresProjectStore
from src.infrastructure.recording_scene_store import PostgresRecordingSceneStore
from src.registry.tools import ToolRegistry
from src.runtime.postgres_session_store import PostgresSessionStore
from src.runtime.postgres_tool_job_store import PostgresToolJobStore
from src.schemas.session import CreateSessionRequest
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.session import MessageKind, RuntimeMode, SessionMode
from src.application.permissions.permission_service import PermissionPolicyContext, PermissionService
from src.application.runtime.tool_governance_service import GovernedToolCallContext, ToolGovernanceService
from src.application.model_adapters.tool_adapter import LangChainToolAdapter
from test_recording_scene_product import actual_recording_scene_management

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SCENE_KNOWLEDGE") != "1", reason="new Chromium/production API/PG/MySQL/Redis/App")]


async def test_new_scene_is_read_by_the_actual_knowledge_tool(tmp_path):
    state = {}

    async def probe(*, stage, settings, client, evidence, project_ids, version):
        def save(name, value):
            (evidence/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        jobs = ToolJobService(PostgresToolJobStore(settings))
        await jobs.initialize()
        sessions = PostgresSessionStore(settings)
        tools = ToolRuntimeService(settings=settings, tool_job_service=jobs, session_store=sessions)
        scene_store = PostgresRecordingSceneStore(settings)
        projects = ProjectService(store=PostgresProjectStore(settings))
        tools.set_recording_scene_knowledge(RecordingSceneKnowledge(
            scenes=scene_store, projects=projects, sessions=sessions))
        tool = ToolRegistry().get("knowledge-rag")
        governance = ToolGovernanceService(tool_registry=ToolRegistry(), permission_service=PermissionService(),
            tool_runtime_service=tools, tool_job_service=jobs)

        async def governed(call, context):
            boundary = GovernedToolCallContext(execution=context, policy=PermissionPolicyContext(
                session_mode=SessionMode.normal, runtime_mode=RuntimeMode.interactive,
                selected_agent_key="coordinator", message_kind=MessageKind.user_input,
                submit_mode="immediate", execution_lane="conversation_turn"),
                active_mode_key="code_review", available_tool_keys=frozenset({tool.key}))
            captured = []
            async def invoke(descriptor, call_id, arguments):
                assert descriptor.key == tool.key and arguments == call.arguments
                result = await governance.execute(ModelToolCall(id=call_id, name=tool.key, arguments=arguments), boundary)
                captured.append(result)
                return json.dumps(result.record.model_dump(mode="json"), ensure_ascii=False)
            registered = LangChainToolAdapter().to_governed_tools([tool], invoke=invoke)[0]
            # The installed JSON-schema StructuredTool keeps supplied arguments;
            # it does not materialize top_k defaults. Runtime is framework-owned.
            await registered.ainvoke({"type":"tool_call", "name":tool.key, "id":call.id,
                "args":{**call.arguments, "runtime":SimpleNamespace(tool_call_id=call.id)}})
            assert len(captured) == 1
            return captured[0]
        if stage == "initial":
            response = await client.post("/sessions", json=CreateSessionRequest(project_id=project_ids[0]).model_dump(mode="json"))
            response.raise_for_status()
            state["session"] = response.json()
            context = ToolExecutionContext(session_id=state["session"]["id"], turn_id="scene-read", trace_id="scene-read",
                user_message="读取本项目固定场景", normalized_input="场景", context_bundle={"project_id":project_ids[0]})
            search_call = ModelToolCall(id=str(uuid4()), name=tool.key, arguments={"query":"两次点击", "operation":"scene_search", "limit":2})
            search_record = await tools.execute(tool, search_call, context)
            save("scene-knowledge-catalog.json", search_record.model_dump(mode="json"))
            assert search_record.status == "completed"
            catalog = search_record.output["scene_catalog"]
            assert catalog["items"] and catalog["items"][0]["scene_id"] == version["scene_id"]
            assert catalog["items"][0]["content_hash"] == version["version"]["content_hash"]
            other_session = await client.post("/sessions", json=CreateSessionRequest(project_id=project_ids[1]).model_dump(mode="json"))
            other_session.raise_for_status()
            other_context = ToolExecutionContext(session_id=other_session.json()["id"], turn_id="scene-other", trace_id="scene-other",
                user_message="读取其他项目", normalized_input="场景", context_bundle={"project_id":project_ids[1]})
            other_record = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key,
                arguments={"query":"两次点击", "operation":"scene_search", "limit":2}), other_context)
            assert other_record.status == "completed" and not other_record.output["scene_catalog"]["items"]
            call = ModelToolCall(id=str(uuid4()), name=tool.key, arguments={"query":"读取固定场景", "operation":"scene_read",
                "scene_id":version["scene_id"], "version_id":version["version"]["id"], "content_hash":version["version"]["content_hash"]})
            record = await tools.execute(tool, call, context)
            save("scene-knowledge-before-or-fixed.json",record.model_dump(mode="json"))
            assert record.status == "completed" and record.output.get("scene_knowledge"), record.output
            knowledge = record.output["scene_knowledge"]
            assert knowledge["content"] == version["version"]["content"]
            assert knowledge["source"] == version["version"]["source"]
            for alias in knowledge["aliases"]:
                original = next(step for step in knowledge["source"]["original_draft"]["steps"]
                    if step["data"]["seq"] == alias["source_seq"])
                assert alias["target"] == original["data"]["event"]["target"]
                assert alias["page"] == original["data"]["event"]["page"]
            no_query = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key,
                arguments={"operation":"scene_read", "scene_id":version["scene_id"],
                    "version_id":version["version"]["id"], "content_hash":version["version"]["content_hash"]}), context)
            assert no_query.status == "completed" and no_query.output["scene_knowledge"] == knowledge
            bad = await tools.execute(tool, ModelToolCall(id=str(uuid4()), name=tool.key, arguments={"query":"", "operation":"scene_read",
                "scene_id":version["scene_id"], "version_id":version["version"]["id"], "content_hash":"0" * 64}), context)
            assert bad.status == "failed" and bad.output.get("error") == "scene_knowledge_rejected"
            jobs_after = await jobs.list_jobs(session_id=state["session"]["id"])
            save("scene-knowledge-tool-jobs.json", [job.model_dump(mode="json") for job in jobs_after])
            assert len(jobs_after) == 4
            assert sum(job.status.value == "completed" for job in jobs_after) == 3
            assert all(job.output_payload.get("protocol") == "recording-scene-knowledge-v1"
                for job in jobs_after if job.status.value == "completed")
            state.update(call=call, context=context, record=record)
            registered_call = call.model_copy(update={"id":str(uuid4())})
            registered_result = await governed(registered_call, context)
            assert registered_result.record.status == "completed", registered_result.record.output
            assert registered_result.record.output == record.output
            denied = await governed(call.model_copy(update={"id":str(uuid4())}), other_context)
            assert denied.record.status == "failed" and denied.record.output["error"] == "scene_knowledge_rejected"
            state.update(registered_call=registered_call, registered_result=registered_result)
            save("scene-knowledge-registered.json", registered_result.record.model_dump(mode="json"))
            save("scene-knowledge-cross-project.json", denied.record.model_dump(mode="json"))
        else:
            # Recreate the runtime/stores after the API process restart and after
            # archival. The current scene head has advanced; V2 remains fixed.
            replay = await governed(state["registered_call"], state["context"])
            assert replay.replayed and replay.record.job_id == state["registered_result"].record.job_id
            assert replay.record.output == state["record"].output
            fresh = await governed(state["call"].model_copy(update={"id":str(uuid4())}), state["context"])
            assert fresh.record.status == "completed" and fresh.record.output == state["record"].output
            save(f"scene-knowledge-{stage}.json", {"replayed":replay.replayed,
                "replay":replay.record.model_dump(mode="json"), "fresh":fresh.record.model_dump(mode="json"),
                "jobs":[job.model_dump(mode="json") for job in await jobs.list_jobs(session_id=state["session"]["id"])]})

    await actual_recording_scene_management(tmp_path, scene_probe=probe)
