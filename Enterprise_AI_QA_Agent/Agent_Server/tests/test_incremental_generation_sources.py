"""Actual confirmed versions/files/storage and HTTP; no durable generation task yet."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from src.application.skills.skill_runtime_service import SkillRuntimeService
from src.application.test_cases.generation_pipeline import ModelTestCaseGenerator
from src.application.test_cases.incremental_generation_sources import IncrementalCaseGenerationSources
from src.application.test_cases.template_service import TestCaseTemplateService as TemplateService
from src.infrastructure.postgres_runtime import postgres_connect
from src.registry.skills import SkillRegistry
from src.schemas.api_docs import ApiDocVersionUploadRequest
from src.schemas.case_generation import IncrementalGenerationRequest, IncrementalGenerationScope
from src.schemas.case_management import TestCaseVersionCreateRequest as VersionRequest
from src.schemas.model_config import ModelInvocationResult, ModelTransportOptions
from src.schemas.project import ProjectCreateRequest
from test_document_impact_analysis_context import configure
from test_document_impact_calls import provider
from test_requirement_reviews import LIVE, environment, payload


def components(env, service, tmp_path):
    skill_file = tmp_path / "generation-skills" / "generate-test-cases" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_bytes((Path(__file__).resolve().parents[1] / "src" / "SKILLS" /
        "generate-test-cases" / "SKILL.md").read_bytes())
    skills = SkillRuntimeService(SkillRegistry(skill_file.parents[1]))
    generator = ModelTestCaseGenerator(model_runtime_service=env.impact_runtime, skill_runtime_service=skills)
    templates = TemplateService(store=env.configs, project_service=env.projects)
    sources = IncrementalCaseGenerationSources(requirements=service, impact_tasks=service._workflow._tasks,
        projects=env.projects, cases=env.cases, templates=templates, generator=generator)
    return sources, generator, templates, skills, skill_file


def responder(body):
    context = json.loads(body["messages"][-1]["content"])["evidence_context"]["incremental_generation"]
    target = context["target_case"]
    business = deepcopy(target["version"]["content_json"]) if target else {"目标": "九天边界", "steps": []}
    business["确认验证点"] = context["requirement"]["points"]
    business["目标"] = "验证订单保留9天"
    return json.dumps({"cases": [{"case_key": target["case"]["case_key"] if target else "retention-boundary-new",
        "title": "九天保留期草稿", "case_type": "json", "content_json": business,
        "execution_notes": target["version"]["execution_notes"] if target else "按完整JSON核对边界并收集证据"}],
        "warnings": context["basis_limitations"]}, ensure_ascii=False)


async def stored_json(env, value, filename, task_id):
    raw = value.model_dump_json().encode("utf-8")
    saved = await env.objects.store_uploaded_bytes(content=raw, filename=filename,
        content_type="application/json", object_prefix=f"document-impacts/{task_id}/incremental-foundation")
    actual = (await env.objects.read_object_uri(saved["uri"]))["content"]
    assert actual == raw
    return raw, saved["uri"]


def evidence(name, paths, files, **values):
    if directory := os.getenv("QA_INCREMENTAL_SOURCES_EVIDENCE_DIR"):
        target = Path(directory) / name
        target.mkdir(parents=True, exist_ok=True)
        for path in paths:
            (target / path.name).write_bytes(path.read_bytes())
        hashes = {}
        for filename, raw in files.items():
            (target / filename).write_bytes(raw)
            hashes[filename] = {"size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        (target / "verification.json").write_text(json.dumps({**values, "files": hashes,
            "durable_generation_task_connected": False, "drafts_committed": False,
            "controlled_model_semantics": True}, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")


async def accepted(values, **changes):
    env, project, doc, paths, case, store, service, snapshot, result, sends, client, base = values
    reply = await client.post(f"{base}/impact-snapshots/{snapshot.id}/reviews",
        json=payload(result.rules[0].id, case.version.id, **changes))
    assert reply.status_code == 201, reply.text
    return reply.json()


def request_for(review, case, **changes):
    return IncrementalGenerationRequest(requirement_id=review["requirement_version"]["requirement_id"],
        requirement_version_id=review["requirement_version"]["id"],
        target_case_version_ids=[case.version.id], **changes)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("extension,adapter", [("md", False), ("docx", False), ("pdf", False), ("md", True)])
async def test_actual_confirmed_scope_fixed_sources_and_saved_request_http(tmp_path, monkeypatch, extension, adapter):
    async with environment(tmp_path, extension) as values:
        env, project, doc, paths, case, store, service, snapshot, result, impact_sends, client, base = values
        first = await accepted(values)
        sources, generator, templates, skills, skill_file = components(env, service, tmp_path)
        await templates.initialize()
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            env.settings.model.langchain_model_adapter_enabled = adapter
            request = request_for(first, case, include_new_cases=True, new_case_mode_key="api_testing")
            scope = await sources.capture(project.id, doc.id, request,
                transport_options=ModelTransportOptions(timeout_seconds=15, max_retries=0))
            assert [module.kind for module in scope.modules] == ["update", "create"]
            original = scope.modules[0]
            context = json.loads(original.generation.invocation.messages[0]["content"])["evidence_context"]["incremental_generation"]
            assert context["requirement"] == first["requirement_version"]
            assert context["target_case"]["version"] == case.version.model_dump(mode="json")
            assert len(context["target_case"]["version"]["content_json"]["steps"]) == 12
            assert context["target_case"]["version"]["content_json"]["深层扩展"] == {
                "零": 0, "空": None, "布尔": False, "未知字段": "不能丢失"}
            assert context["old"] and context["new"] and context["difference"] and context["originals"]
            assert context["deployment_status"] == "unknown" and context["basis_limitations"] == result.basis_limitations
            raw_scope, uri = await stored_json(env, scope, "scope.json", snapshot.task_id)
            assert endpoint.encode() not in raw_scope and b"local-fixture-marker" not in raw_scope
            changed_template = scope.template.payload.model_copy(update={"writing_guidance": "新指导不应进入原请求"})
            await templates.save(project.id, changed_template)
            skill_file.write_text(skill_file.read_text(encoding="utf-8")+"\nNEW-SKILL-MUST-NOT-REPLACE\n", encoding="utf-8")
            await env.docs.upload_version(doc.id, ApiDocVersionUploadRequest(project_id=project.id,
                request_id="after-frozen-scope", filename=paths[0].name, title="当前文件恢复旧原件",
                expected_parent_version_id=(await env.docs.get_document(doc.id)).current_version_id,
                content_base64=base64.b64encode(paths[0].read_bytes()).decode()))
            later_case = await env.cases.create_version(case.case.id, VersionRequest(**case.version.model_dump(include={
                "content_json", "execution_notes", "source_refs", "model_key", "prompt_version", "skill_versions"})))
            second = await accepted(values, request_id="later-confirmation", expected_review_id=first["id"],
                requirement_id=first["requirement_version"]["requirement_id"],
                expected_version_id=first["requirement_version"]["id"], statement="后来的确认不可替换原版本", case_links=[])
            basis, review = await service.read_generation_basis(project.id, doc.id,
                request.requirement_id, request.requirement_version_id)
            assert basis.id == request.requirement_version_id and review.id == first["id"]
            recovered = IncrementalGenerationScope.model_validate_json((await env.objects.read_object_uri(uri))["content"])
            restarted = ModelTestCaseGenerator(model_runtime_service=env.impact_runtime, skill_runtime_service=skills)
            async def forbidden(*args, **kwargs): raise AssertionError("frozen send must not reload current sources")
            def forbidden_sync(*args, **kwargs): raise AssertionError("frozen send must not reload current Skill")
            saved_files = {"scope.json": raw_scope}
            with monkeypatch.context() as patch:
                patch.setattr(templates, "get", forbidden)
                patch.setattr(skills, "capture_prompt_blocks", forbidden_sync)
                patch.setattr(service, "read_generation_basis", forbidden)
                for index, module in enumerate(recovered.modules):
                    response = await restarted.invoke_frozen(module.generation)
                    raw_response, response_uri = await stored_json(env, response, f"response-{index}.json", snapshot.task_id)
                    saved_files[f"response-{index}.json"] = raw_response
                    reloaded_response = ModelInvocationResult.model_validate_json((await env.objects.read_object_uri(response_uri))["content"])
                    batch = sources.parse_module_response(module, reloaded_response)
                    saved_files[f"candidates-{index}.json"] = batch.model_dump_json().encode("utf-8")
                    assert batch.prompt_version == "test-case-generation-incremental-v2"
                    assert batch.skill_versions == {skill.key: f"sha256:{skill.sha256}" for skill in recovered.authoring.skills}
                    assert batch.cases[0].content_json["确认验证点"] == basis.model_dump(mode="json")["points"]
                    assert sends[index]["messages"][0]["content"] == module.generation.invocation.system_prompt
                    assert sends[index]["messages"][-1]["content"] == module.generation.invocation.messages[-1]["content"]
                    if index == 0:
                        assert batch.cases[0].content_json["深层扩展"] == case.version.content_json["深层扩展"]
            assert len(sends) == 2 and len(impact_sends) == 2
            assert await env.cases.get_version(case.version.id) == case.version
            current = await env.cases.get_case(case.case.id)
            assert current.active_version_id == case.version.id and current.latest_version == later_case.version
            assert (await env.objects.read_object_uri(uri))["content"] == raw_scope
            evidence(f"{extension}-{'langchain' if adapter else 'native'}", paths, saved_files, scope_uri=uri, physical_requests=sends,
                impact_physical_requests=impact_sends, actual_generation_http_calls=len(sends),
                confirmed_v1=first, later_v2=second, active_case_version=case.version.id,
                expected_latest_version=original.expected_latest_version, later_case= current.model_dump(mode="json"))


@pytest.mark.asyncio
@LIVE
async def test_actual_fixed_config_guards_and_source_corruption_no_send(tmp_path):
    async with environment(tmp_path) as values:
        env, project, doc, paths, case, store, service, snapshot, result, impact_sends, client, base = values
        first = await accepted(values)
        sources, generator, templates, _, _ = components(env, service, tmp_path)
        await templates.initialize()
        with provider(responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            scope = await sources.capture(project.id, doc.id, request_for(first, case))
            prepared_request = scope.modules[0].generation
            await configure(env, endpoint, max_output_tokens=1025)
            assert env.impact_runtime.get_model_config(scope.authoring.model_key).max_tokens == 1025
            with pytest.raises(ValueError, match="模型参数已变化"):
                await generator.invoke_frozen(prepared_request)
            await configure(env, endpoint)
            old_adapter = env.settings.model.langchain_model_adapter_enabled
            env.settings.model.langchain_model_adapter_enabled = not old_adapter
            try:
                with pytest.raises(ValueError, match="适配器、SDK或超时设置已变化"):
                    await generator.invoke_frozen(prepared_request)
            finally:
                env.settings.model.langchain_model_adapter_enabled = old_adapter
            altered = scope.model_dump(mode="json")
            altered["modules"][0]["generation"]["invocation"]["messages"][0]["content"] += "changed"
            with pytest.raises(ValueError, match="哈希"):
                IncrementalGenerationScope.model_validate(altered)
            foreign = await env.projects.create(ProjectCreateRequest(project_key="foreign-generation", name="外项目"))
            with pytest.raises(KeyError):
                await sources.capture(foreign.id, doc.id, request_for(first, case))
            with pytest.raises(ValueError, match="固定用例范围"):
                await sources.capture(project.id, doc.id, request_for(first, case).model_copy(update={"target_case_version_ids": [str(uuid4())]}))
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT record FROM {store._reviews} WHERE id=%s", (first["id"],))
                saved = cur.fetchone()["record"]
                cur.execute(f"UPDATE {store._reviews} SET record=jsonb_set(record,'{{request,note}}','\"corrupted\"'::jsonb) WHERE id=%s", (first["id"],))
            try:
                with pytest.raises(ValueError, match="请求正文与固定哈希"):
                    await sources.capture(project.id, doc.id, request_for(first, case))
            finally:
                with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                    cur.execute(f"UPDATE {store._reviews} SET record=%s::jsonb WHERE id=%s", (json.dumps(saved, ensure_ascii=False), first["id"]))
            assert not sends
            response = await generator.invoke_frozen(prepared_request)
            assert len(sends) == 1
            captured_summaries = [json.loads((Path(__file__).resolve().parents[2] /
                'docs/upgrade_2026-10-03/p0-model-completion-before2' /
                f'{transport}-native-limited/result.json').read_bytes())['response']['response_summary']
                for transport in ['openai_responses', 'google_gemini_generate_content']]
            for summary in [{"mode": "http_error"}, {"finish_reason": "length"}, {"stop_reason": "max_tokens"}, *captured_summaries]:
                bad = response.model_copy(update={"response_summary": summary})
                with pytest.raises(ValueError): sources.parse_module_response(scope.modules[0], bad)
            parsed = json.loads(response.text)
            parsed["cases"].append(deepcopy(parsed["cases"][0]))
            with pytest.raises(ValueError, match="超过固定范围"):
                sources.parse_module_response(scope.modules[0], response.model_copy(update={"text": json.dumps(parsed)}))
            parsed["cases"] = parsed["cases"][:1]
            parsed["cases"][0]["case_key"] = "wrong-replacement"
            with pytest.raises(ValueError, match="保持原用例身份"):
                sources.parse_module_response(scope.modules[0], response.model_copy(update={"text": json.dumps(parsed)}))
            evidence("guards", paths, {"scope.json": scope.model_dump_json().encode(), "response.json": response.model_dump_json().encode()},
                actual_generation_http_calls=len(sends), physical_requests=sends, guard_send_count=0,
                pg_corruption_detected_and_restored=True, malformed_response_checks_use_saved_actual_response=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_removed_requirement_only_retirement_candidates(tmp_path, monkeypatch):
    async with environment(tmp_path) as values:
        env, project, doc, paths, case, store, service, snapshot, result, sends, client, base = values
        removed = await accepted(values, change="removed", points=[], case_links=[])
        before = await env.cases.get_case(case.case.id)
        sources, generator, templates, _, _ = components(env, service, tmp_path)
        async def forbidden(*args, **kwargs): raise AssertionError("removed scope cannot read authoring or generate")
        with monkeypatch.context() as patch:
            patch.setattr(generator, "capture_authoring", forbidden)
            patch.setattr(generator, "invoke_frozen", forbidden)
            patch.setattr(templates, "get", forbidden)
            scope = await sources.capture(project.id, doc.id, request_for(removed, case))
            assert scope.template is None and scope.authoring is None
            assert len(scope.modules) == 1 and scope.modules[0].kind == "retirement_candidate"
            assert scope.modules[0].generation is None
            with pytest.raises(ValueError, match="不能生成新覆盖"):
                await sources.capture(project.id, doc.id, request_for(removed, case, include_new_cases=True, new_case_mode_key="api_testing"))
        assert await env.cases.get_case(case.case.id) == before
        assert await env.cases.get_version(case.version.id) == case.version
        evidence("removed", paths, {"scope.json": scope.model_dump_json().encode()},
            actual_generation_http_calls=0, actual_impact_http_calls=len(sends), active_case_unchanged=True)


@pytest.mark.parametrize("changes", [
    {"target_case_version_ids": []}, {"target_case_version_ids": ["not-uuid"]},
    {"include_new_cases": True}, {"max_new_cases": 201},
])
def test_explicit_incremental_request_scope(changes):
    with pytest.raises(ValueError):
        IncrementalGenerationRequest.model_validate({"requirement_id": str(uuid4()),
            "requirement_version_id": str(uuid4()), "target_case_version_ids": [str(uuid4())], **changes})


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize("mode", ["tool", "length", "empty", "http_failure"])
async def test_actual_failed_http_response_saved_before_candidate_validation(tmp_path, mode):
    async with environment(tmp_path) as values:
        env, project, doc, paths, case, store, service, snapshot, result, impact_sends, client, base = values
        first = await accepted(values)
        sources, generator, templates, _, _ = components(env, service, tmp_path)
        await templates.initialize()
        before = await env.cases.get_case(case.case.id)
        with provider(mode=mode, responder=responder) as (endpoint, sends, _, _):
            await configure(env, endpoint)
            scope = await sources.capture(project.id, doc.id, request_for(first, case),
                transport_options=ModelTransportOptions(timeout_seconds=15, max_retries=0))
            response = await generator.invoke_frozen(scope.modules[0].generation)
            # Store the entire actual runtime response before attempting validation.
            raw, uri = await stored_json(env, response, "failed-response.json", snapshot.task_id)
            reloaded = ModelInvocationResult.model_validate_json((await env.objects.read_object_uri(uri))["content"])
            with pytest.raises(ValueError):
                sources.parse_module_response(scope.modules[0], reloaded)
            assert len(sends) == 1
            assert await env.cases.get_case(case.case.id) == before
            assert await env.cases.get_version(case.version.id) == case.version
            evidence(f"response-{mode}", paths, {"response.json": raw, "scope.json": scope.model_dump_json().encode()},
                physical_requests=sends, actual_generation_http_calls=1, actual_impact_http_calls=len(impact_sends),
                response_saved_before_validation=True, response_mode=mode, active_case_unchanged=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_pg_basis_and_rustfs_input_corruption_restored(tmp_path):
    async with environment(tmp_path) as values:
        env, project, doc, paths, case, store, service, snapshot, result, impact_sends, client, base = values
        first = await accepted(values)
        sources, generator, templates, _, _ = components(env, service, tmp_path)
        request = request_for(first, case)
        with postgres_connect(env.settings) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT record FROM {store._reviews} WHERE id=%s", (first["id"],))
            original_review = cur.fetchone()["record"]
            cur.execute(f"UPDATE {store._reviews} SET record=jsonb_set(record,'{{source,basis_limitations}}','[\"invented\"]'::jsonb) WHERE id=%s", (first["id"],))
        try:
            with pytest.raises(ValueError, match="来源与固定影响结果不同"):
                await sources.capture(project.id, doc.id, request)
        finally:
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {store._reviews} SET record=%s::jsonb WHERE id=%s", (json.dumps(original_review, ensure_ascii=False), first["id"]))
        checkpoint, _ = await service._workflow._tasks.read_input(project.id, doc.id, snapshot.task_id)
        raw = (await env.objects.read_object_uri(checkpoint.storage_uri))["content"]
        assert checkpoint.storage_uri.startswith("rustfs://")
        bucket, object_name = checkpoint.storage_uri.removeprefix("rustfs://").split("/", 1)
        assert object_name.startswith("document-impacts/") and snapshot.task_id in object_name
        async def overwrite(content):
            await env.objects.store_uploaded_bytes(content=content, filename="owned-input-fault.json",
                object_prefix=f"document-impacts/{snapshot.task_id}", object_name=object_name,
                bucket_name=bucket, content_type="application/json")
        try:
            await overwrite(raw+b"corruption")
            assert (await env.objects.read_object_uri(checkpoint.storage_uri))["content"] == raw+b"corruption"
            with pytest.raises(ValueError, match="对象大小或哈希不一致"):
                await sources.capture(project.id, doc.id, request)
        finally:
            await overwrite(raw)
        assert (await env.objects.read_object_uri(checkpoint.storage_uri))["content"] == raw
        evidence("actual-corruption", paths, {"restored-input.json": raw},
            actual_generation_http_calls=0, actual_impact_http_calls=len(impact_sends),
            original_input_sha256=checkpoint.sha256, actual_pg_source_and_rustfs_input_corrupted_and_restored=True)
