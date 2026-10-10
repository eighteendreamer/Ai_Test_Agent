"""Profile freezes in real PG; source/prepared request/response bytes survive RustFS reload."""
import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from test_case_planning_tasks import tasks_environment
from test_document_impact_calls import provider
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.application.test_cases.generation_pipeline import ModelTestCaseGenerator
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.models import ModelRegistry
from src.registry.skills import SkillRegistry
from src.schemas.case_generation import PreparedCaseGeneration
from src.schemas.case_management import TestCaseGenerateRequest as GenerateRequest, TestCaseDraftCreateRequest as DraftRequest
from src.schemas.model_config import ModelInvocationRequest, ModelInvocationResult
from src.schemas.project import ProjectCreateRequest
from src.schemas.settings import ModelConfigUpdateRequest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_MODEL_REQUEST_PROFILE") != "1",
    reason="isolated actual MySQL, PG, RustFS and SDK HTTP")]


async def test_actual_project_source_generation_and_planning_profile_freeze(tmp_path):
    evidence = Path(os.environ["QA_MODEL_REQUEST_PROFILE_STORAGE_DIR"])
    evidence.mkdir(parents=True, exist_ok=False)
    stored = []
    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    generated = {"cases": [{"case_key": "profile-source", "title": "查询缺失订单返回404", "case_type": "json",
        "content_json": {"目标": "查询缺失订单", "expected_status": 404, "扩展": [0, False, None, "中文😀"],
            "steps": [{"order": i, "action": "读取返回证据" if i < 8 else "核对404负向条件"} for i in range(1, 9)]},
        "execution_notes": "保留负向条件，不改成查询成功。"}], "warnings": []}
    with provider(responder=lambda body: json.dumps(generated, ensure_ascii=False)) as (endpoint, sends, _, _):
        async with tasks_environment(tmp_path) as env:
            table = "up_profile_" + uuid4().hex[:12]
            env.settings.database.llm_model_table = table
            with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
                cur.execute("SHOW TABLES LIKE %s", (table,)); assert cur.fetchone() is None
            store = MySQLModelConfigStore(env.settings)
            try:
                store.initialize()
                payload = {"model_name": "owned-profile-generation", "provider": "openai", "transport": "openai_chat_completions",
                    "base_url": endpoint, "is_active": True, "max_output_tokens": 4096}
                profile = {"revision": 1, "output_formats": ["json_object"], "chat_token_parameter": "max_tokens"}
                store.upsert(ModelConfigUpdateRequest(**payload, api_key="local-fixture-marker", request_profile=profile))
                runtime = ModelRuntimeService(ModelRegistry(store), env.settings)
                env.service._models = runtime
                project = await env.projects.create(ProjectCreateRequest(project_key="model-profile", name="固定请求配置"))
                source = tmp_path / "requirements.md"
                source.write_text("# 订单查询\nGET /orders/{id}：不存在的订单返回404。\n不得创建订单将负向测试改成成功查询。\n", encoding="utf-8")
                (evidence / source.name).write_bytes(source.read_bytes())
                doc = await env.docs.upload_document(filename=source.name, content_base64=base64.b64encode(source.read_bytes()).decode(), project_id=project.id)
                source_text = await env.docs.read_document_content(doc.id, max_chars=10000)
                root = f"/projects/{project.id}/case-planning-tasks"
                response = await env.task_client.post(root, json={"creation_key": "profile-frozen", "objective": "按项目用例测试查询"})
                assert response.status_code == 202, response.text
                claim = await env.jobs.claim(project.id, response.json()["id"], worker_id="profile-audit")
                assert claim.seed.model_parameters["request_profile"]["revision"] == 1
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT seed,checkpoint FROM {env.settings.database.postgres_case_planning_task_table} WHERE id=%s", (claim.task.id,))
                    frozen_row = dict(cur.fetchone())
                save("pg-frozen-task.json", frozen_row)
                skill_file = tmp_path / "skills" / "generate-test-cases" / "SKILL.md"
                skill_file.parent.mkdir(parents=True)
                skill_file.write_bytes((Path(__file__).resolve().parents[1] / "src/SKILLS/generate-test-cases/SKILL.md").read_bytes())
                skills = SkillRuntimeService(SkillRegistry(skill_file.parents[1]))
                generator = ModelTestCaseGenerator(model_runtime_service=runtime, skill_runtime_service=skills)
                request = GenerateRequest(objective="依据文档生成404查询草稿", mode_key="api_testing", api_doc_ids=[doc.id], max_cases=1)
                prepared = await generator.prepare_frozen(request=request, context={"project_id": project.id,
                    "api_documents": [{"id": doc.id, "content": source_text["content"]}]})
                raw = prepared.model_dump_json().encode()
                uploaded = await env.objects.store_uploaded_bytes(content=raw, filename="prepared.json", object_prefix="model-profiles/"+claim.task.id)
                stored.append(uploaded["uri"])
                reread = (await env.objects.read_object_uri(uploaded["uri"]))["content"]
                assert reread == raw
                (evidence / "rustfs-prepared.json").write_bytes(reread)
                reloaded = PreparedCaseGeneration.model_validate_json(reread)
                assert reloaded.authoring.model_parameters == claim.seed.model_parameters
                result = await generator.invoke_frozen(reloaded)
                result_raw = result.model_dump_json().encode()
                response_object = await env.objects.store_uploaded_bytes(content=result_raw, filename="response.json", object_prefix="model-profiles/"+claim.task.id)
                stored.append(response_object["uri"])
                result_reload = (await env.objects.read_object_uri(response_object["uri"]))["content"]
                assert result_reload == result_raw
                (evidence / "rustfs-response.json").write_bytes(result_reload)
                batch = generator.parse_frozen(reloaded, ModelInvocationResult.model_validate_json(result_reload))
                assert len(batch.cases[0].content_json["steps"]) == 8 and batch.cases[0].content_json["expected_status"] == 404
                case = batch.cases[0]
                bundle = await env.cases.create_draft(project_id=project.id, payload=DraftRequest(**case.model_dump(exclude={"generation_metadata"}),
                    mode_key="api_testing", source_refs=[{"source_type": "api_doc", "source_id": doc.id}],
                    model_key=batch.model_key, prompt_version=batch.prompt_version, skill_versions=batch.skill_versions,
                    generation_metadata={"fixed_request_sha256": prepared.sha256, "request_mapping": result.response_summary["request_mapping"]}))
                assert (await env.cases.get_version(bundle.version.id)).content_json == generated["cases"][0]["content_json"]
                assert len(sends) == 1 and sends[0]["response_format"] == {"type": "json_object"}
                store.upsert(ModelConfigUpdateRequest(**payload, request_profile={**profile, "revision": 2}))
                with pytest.raises(ValueError, match="模型参数已变化"):
                    await generator.invoke_frozen(reloaded)
                with pytest.raises(ValueError, match="模型参数已变化"):
                    await runtime.invoke(claim.seed.plan.model_key, ModelInvocationRequest(system_prompt="Return JSON.", messages=[]),
                        expected_parameters=claim.seed.model_parameters)
                assert len(sends) == 1
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"SELECT seed FROM {env.settings.database.postgres_case_planning_task_table} WHERE id=%s", (claim.task.id,))
                    assert cur.fetchone()["seed"] == frozen_row["seed"]
                save("facts.json", {"actual_sdk_http_calls": 1, "guard_additional_sends": 0, "profile_revision": 1,
                    "draft": bundle.model_dump(mode="json"), "requests": sends, "prepared_uri": uploaded["uri"],
                    "request_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(source.read_bytes()),
                    "response_uri": response_object["uri"], "response_sha256": hashlib.sha256(result_raw).hexdigest(),
                    "full_default_agent_proven": False, "controlled_model": True})
            finally:
                for uri in stored: await env.objects.delete_object_uri(uri)
                with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"DROP TABLE `{table}`"); conn.commit()
