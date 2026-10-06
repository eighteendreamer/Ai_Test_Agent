"""Production API and separate impact CLI over actual documents, HTTP and stores."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.generation_runtime import GENERATION_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_management import TestCaseBundle as CaseBundle
from src.schemas.document_impact_calls import DocumentImpactUnit
from src.schemas.document_impact_views import DocumentImpactSnapshotView
from src.schemas.document_parsing import DocumentParseSnapshotView
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_differences import write_pair
from test_document_impact_calls import provider
from test_document_impact_sources import source
from test_document_impact_workflow import responder
from test_document_impact_grouped_workflow import grouped_responder
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_IMPACT_PRODUCTION") != "1",
    reason="production API, independent CLI, isolated MySQL/PG/Redis and actual RustFS/HTTP")]


def collect_objects(settings, owned):
    """Collect only references from this fixture's newly owned database."""
    def visit(value):
        if isinstance(value, dict):
            if value.get("storage_uri") and value["storage_uri"] not in owned:
                owned.append(value["storage_uri"])
            for item in value.values(): visit(item)
        elif isinstance(value, list):
            for item in value: visit(item)
    with postgres_connect(settings) as conn, conn.cursor() as cur:
        for suffix, fields in [("assets", "record"), ("parse_tasks", "checkpoint,failure_artifact"),
            ("diff_tasks", "checkpoint,failure_artifact"), ("impact_tasks", "input_checkpoint,checkpoint,failure_artifact"),
            ("impact_manifests", "record"), ("impact_calls", "record"), ("impact_response_checks", "record"),
            ("impact_reasoning_plans", "record"), ("case_gen_groups", "record"), ("case_gen_tasks", "checkpoint")]:
            table = settings.database.postgres_api_doc_table + "_" + suffix
            cur.execute("SELECT to_regclass(%s) AS existing", (table,))
            if cur.fetchone()["existing"]:
                cur.execute(f"SELECT {fields} FROM {table}")
                for row in cur.fetchall():
                    for value in row.values(): visit(value)


@pytest.mark.parametrize("extension,adapter,interrupt", [
    ("md", False, False), ("docx", False, False), ("pdf", False, False),
    ("docx", True, False), ("md", False, True),
])
async def test_production_fixed_analysis_history_and_process_recovery(tmp_path, extension, adapter, interrupt,
    verify_requirements=False, verify_generation=False, verify_retry=False, verify_coverage=False, verify_execution=None,
    verify_applicability=None, verify_environment_planning=False, verify_environment_restrictions=False,
    verify_environment_admission=False):
    name = f"{extension}-{'langchain' if adapter else 'native'}-{'restart' if interrupt else 'finish'}"
    evidence = Path(os.getenv("QA_IMPACT_PRODUCTION_EVIDENCE_DIR", str(tmp_path / "evidence"))) / name
    evidence.mkdir(parents=True, exist_ok=True)
    paths = write_pair(tmp_path, extension)
    for path in paths: (evidence / path.name).write_bytes(path.read_bytes())
    holder, views = {}, []
    def respond(body):
        payload = json.loads(body["messages"][-1]["content"])
        if verify_environment_planning and "business_environment" in payload and "catalog" in payload:
            from business_planning_production_scenario import planning_response
            return planning_response(payload)
        if verify_generation and "incremental_generation" in payload.get("evidence_context", {}):
            from test_incremental_generation_sources import responder as generation_responder
            return generation_responder(body)
        holder.setdefault("first", DocumentImpactUnit.model_validate(payload["unit"]))
        holder["manifest"] = SimpleNamespace(sources=[SimpleNamespace(**item) for item in payload["fixed_source_catalog"]])
        factory = grouped_responder if payload["analysis_protocol"]["version"] in {"impact-call-v6", "impact-call-v7"} else responder
        return factory(holder, views, holder["case"])(body)
    try:
        if verify_environment_admission:
            from business_environment_admission_production_scenario import admission_message
        with provider(gated=interrupt, responder=respond,
            message_responder=admission_message if verify_environment_admission else None) as (endpoint, sends, arrived, release):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.model.langchain_model_adapter_enabled = adapter
                settings.api_auth_token = SecretStr(uuid4().hex)
                settings.orchestration.redis_task_outbox_streams = ",".join(
                    [DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM,
                        *([GENERATION_STREAM] if verify_generation else []),
                        *([settings.orchestration.case_planning_task_stream] if verify_environment_planning else [])])
                settings.orchestration.task_outbox_lease_seconds = 6
                models = MySQLModelConfigStore(settings); models.initialize()
                model = models.upsert(ModelConfigUpdateRequest(model_name="controlled-impact", provider="openai",
                    transport="openai_chat_completions", base_url=endpoint, api_key=uuid4().hex, is_active=True,
                    use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=False),
                    context_window=128000, max_output_tokens=1024))
                processes.env = child_environment(settings)
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("impact-api-first", *args)
                queues = {}
                try:
                    for stream, task_type in [(DOCUMENT_PARSE_STREAM, "document_parsing"),
                        (DOCUMENT_DIFFERENCE_STREAM, "document_difference"), (DOCUMENT_IMPACT_STREAM, "document_impact"),
                        *([(GENERATION_STREAM, "case_generation")] if verify_generation else [])]:
                        queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
                            group=settings.orchestration.redis_task_consumer_group + ":" + task_type)
                        await queue.connect(); queues[task_type] = queue
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50,
                        headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                        await wait_api(client, api)
                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload)
                            assert response.status_code == status, (response.status_code, response.text)
                            return response.json()
                        async def get(path, **params):
                            response = await client.get(path, params=params)
                            assert response.status_code == 200, (response.status_code, response.text)
                            return response.json()
                        async def worker(command, worker_name, task_type):
                            assert await PostgresTaskOutbox(settings).relay_once(queues[task_type], limit=1) == 1
                            child = processes.start(worker_name, "-m", "src.cli." + command, "--once", "--worker-id", worker_name)
                            assert await processes.wait(child) == 0
                            return child
                        project = await post("/projects", {"project_key": "impact-" + uuid4().hex, "name": "影响分析实测"}, 201)
                        other = await post("/projects", {"project_key": "other-" + uuid4().hex, "name": "隔离项目"}, 201)
                        root = f"/projects/{project['id']}"
                        unauthorized = await client.get(root + "/document-impact-settings", headers={"Authorization": "Bearer invalid-fixture"})
                        assert unauthorized.status_code == 401
                        settings_paths = [("document-impact-settings", "document.impact_settings"),
                            ("document-impact-analysis-settings", "document.impact_analysis_settings"),
                            ("document-impact-batch-settings", "document.impact_batch_settings"),
                            ("document-impact-invocation-settings", "document.impact_invocation_settings"),
                            ("document-impact-reasoning-settings", "document.impact_reasoning_settings")]
                        mysql, frozen = MySQLApplicationConfigStore(settings), {}
                        for suffix, key in settings_paths:
                            initial = await get(root + "/" + suffix)
                            payload = initial["payload"]
                            if suffix == "document-impact-settings": payload = {**payload, "timeout_seconds": 300}
                            if suffix == "document-impact-analysis-settings":
                                payload = {**payload, "model_key": model.key, "analysis_guidance": "实测固定完整来源"}
                            if suffix == "document-impact-invocation-settings":
                                payload = {**payload, "transport_policy": "single_attempt"}
                            response = await client.put(root + "/" + suffix, json=payload)
                            assert response.status_code == 200, response.text
                            frozen[suffix] = response.json()
                            assert mysql.get(key, scope_key=project["id"]).payload == payload
                        document = await post("/registry/api-docs/upload", {"project_id": project["id"],
                            "filename": paths[0].name, "request_id": "original-v1",
                            "content_base64": base64.b64encode(paths[0].read_bytes()).decode()})
                        base = root + f"/documents/{document['id']}"
                        versions = []
                        for number, path in enumerate(paths, 1):
                            if number == 2:
                                result = await post(f"/registry/api-docs/{document['id']}/versions", {
                                    "project_id": project["id"], "filename": path.name, "request_id": "original-v2",
                                    "expected_parent_version_id": document["current_version_id"],
                                    "content_base64": base64.b64encode(path.read_bytes()).decode()})
                                document = result["document"]
                            versions.append(document["current_version_id"])
                            task = await post(base + "/parse-tasks", {"creation_key": f"parse-{number}", "version_id": versions[-1]}, 202)
                            await worker("run_document_parse_worker", f"parse-{number}", "document_parsing")
                            parsed = await get(base + f"/parse-tasks/{task['id']}")
                            assert parsed["status"] == "completed"
                            view = DocumentParseSnapshotView.model_validate(await get(base + f"/parse-snapshots/{parsed['snapshot_id']}"))
                            views.append(view)
                            (evidence / f"source-{number}.json").write_text(view.model_dump_json(indent=2), encoding="utf-8")
                        comparison = await post(base + "/difference-tasks", {"creation_key": "comparison",
                            "old_snapshot_id": views[0].snapshot.id, "new_snapshot_id": views[1].snapshot.id}, 202)
                        await worker("run_document_difference_worker", "comparison", "document_difference")
                        diff = await get(base + f"/difference-tasks/{comparison['id']}")
                        assert diff["status"] == "completed"
                        case_payload = {"case_key": "retention", "title": "验证保留期限", "case_type": "json", "mode_key": "ui_testing",
                            "content_json": {"目标": "验证订单保留7天", "steps": [{"操作": f"完整步骤{i}"} for i in range(12)],
                                "深层扩展": {"零": 0, "空": None, "布尔": False, "未知字段": "不能丢失"}},
                            "execution_notes": "完整用例执行说明", "source_refs": [source(SimpleNamespace(id=document['id']), views[0])],
                            "model_key": "manual", "prompt_version": "manual-v1", "skill_versions": {"manual": "v1"}}
                        case = CaseBundle.model_validate(await post(root + "/test-cases", case_payload, 201))
                        holder["case"] = case
                        foreign_case = await post(f"/projects/{other['id']}/test-cases", {**case_payload,
                            "source_refs": [{"source_type": "manual", "source_id": "foreign-project-fixture"}]}, 201)
                        request = {"creation_key": "fixed-impact", "difference_snapshot_id": diff["snapshot_id"],
                            "environment_key": "qa", "target_release": "declared-v2"}
                        submitted = await post(base + "/impact-tasks", request, 202)
                        assert submitted["status"] == "queued" and not sends
                        assert submitted["version_id"] == versions[1]
                        assert (await client.post(base + "/impact-tasks", json={**request, "environment_key": "changed"})).status_code == 409
                        assert (await client.post(base + "/impact-tasks", json={**request, "creation_key": "foreign-case",
                            "case_version_ids": [foreign_case["version"]["id"]]})).status_code == 404
                        assert (await client.post(base + "/impact-tasks", json={**request, "template_id": "forbidden"})).status_code == 422
                        assert (await client.get(base + "/impact-tasks/by-request", params={"creation_key": ""})).status_code == 422
                        assert (await client.get(base + "/impact-tasks/by-request", params={"creation_key": "missing"})).status_code == 404
                        assert (await client.get(base + f"/impact-tasks/{submitted['id']}/result")).status_code == 404
                        assert (await client.get(base + "/impact-snapshots", params={"limit": 0})).status_code == 422
                        assert (await client.put(root + "/document-impact-invocation-settings", json={
                            **frozen["document-impact-invocation-settings"]["payload"], "token_method": "tiktoken_content"})).status_code == 422
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT seed FROM {settings.database.postgres_api_doc_table}_impact_tasks WHERE id=%s", (submitted["id"],))
                            seed = cur.fetchone()["seed"]
                            assert [item["version_id"] for item in seed["cases"]] == [case.version.id]
                            assert seed["analysis_context"]["config"] == frozen["document-impact-analysis-settings"]
                            assert seed["workflow_protocol"]["version"] == "impact-coordinator-v2"
                            assert seed["response_protocol"]["version"] == "impact-call-v7"
                            assert seed["reasoning_settings"] == frozen["document-impact-reasoning-settings"]
                            cur.execute(f"SELECT count(*) AS n FROM {settings.database.postgres_task_outbox_table} WHERE event_key=%s",
                                ("document-impact:" + submitted["id"],))
                            assert cur.fetchone()["n"] == 1
                        # Change current config and draft after submission; replay and Worker must retain the old facts.
                        assert (await client.put(root + "/document-impact-analysis-settings", json={
                            **frozen["document-impact-analysis-settings"]["payload"], "analysis_guidance": "后续配置不能替换旧提交"})).status_code == 200
                        assert (await client.put(root + "/document-impact-reasoning-settings", json={
                            **frozen["document-impact-reasoning-settings"]["payload"], "max_group_units": 1})).status_code == 200
                        later = await post(f"/test-cases/{case.case.id}/versions", {key: value for key, value in case_payload.items()
                            if key not in {"case_key", "title", "case_type", "mode_key"}}, 201)
                        await processes.stop(api)
                        api = processes.start("impact-api-restarted", *args)
                        await wait_api(client, api)
                        assert await post(base + "/impact-tasks", request, 202) == submitted
                        assert await get(base + "/impact-tasks/by-request", creation_key=request["creation_key"]) == submitted
                        relay = processes.start("impact-outbox-relay", "-m", "src.cli.run_task_outbox_relay", "--once", "--batch-size", "1")
                        assert await processes.wait(relay) == 0
                        assert "task_outbox_published=1" in (tmp_path / "impact-outbox-relay.stdout").read_text(encoding="utf-8")
                        first_worker = processes.start("impact-worker-first", "-m", "src.cli.run_document_impact_worker",
                            "--once", "--worker-id", "impact-first")
                        if interrupt:
                            assert await asyncio.to_thread(arrived.wait, 25), "independent Worker made no actual HTTP request"
                            await processes.stop(first_worker)
                            release.set()
                            with postgres_connect(settings) as conn, conn.cursor() as cur:
                                cur.execute(f"UPDATE {settings.database.postgres_api_doc_table}_impact_tasks "
                                    "SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (submitted["id"],))
                            restarted = processes.start("impact-worker-restarted", "-m", "src.cli.run_document_impact_worker",
                                "--once", "--worker-id", "impact-recovered")
                            assert await processes.wait(restarted) == 0
                        else:
                            assert await processes.wait(first_worker) == 0
                        task = await get(base + f"/impact-tasks/{submitted['id']}")
                        assert task["status"] == "completed" and task["attempt_no"] == (2 if interrupt else 1)
                        view = await get(base + f"/impact-snapshots/{task['snapshot_id']}")
                        typed = DocumentImpactSnapshotView.model_validate(view)
                        result = typed.result
                        (evidence / "snapshot-view.json").write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
                        assert result.semantic_status == "pending_review" and result.deployment_status == "unknown"
                        # Coordinate/layout caveats are real evidence limitations. Completed
                        # declared units cannot establish complete original document semantics.
                        assert result.status == "partial"
                        assert "difference_warning:extracted_text_positions" in result.basis_limitations
                        extra_code = {"docx": "docx_layout_scope", "pdf": "pdf_structure_unavailable"}.get(extension)
                        if extra_code:
                            assert any(item.endswith(":" + extra_code) for item in result.basis_limitations)
                        else:
                            assert result.basis_limitations == ["difference_warning:extracted_text_positions"]
                        assert view["snapshot"]["seed"] == seed
                        assert result.units[-1].unit.id == case.version.id and later["id"] != case.version.id
                        first_sends = [body for body in sends if json.loads(body["messages"][-1]["content"])["unit"] == result.units[0].unit.model_dump()]
                        if interrupt:
                            assert result.units[0].status == "unknown" and len(first_sends) == 1
                            assert not result.rules
                        else:
                            assert all(item.status == "analyzed" and not item.reason_codes and not item.unresolved for item in result.units)
                            assert len(result.rules) == 1 and len(first_sends) == (1 if
                                result.implementation_version == "impact-coordinator-v2" else 2)
                            assert len(sends) == (len(result.discovery_tasks) + len([item for item in result.units if
                                item.unit.kind == "case"]) if result.implementation_version == "impact-coordinator-v2" else len(result.units) + 1)
                            assert result.units[-1].candidates[0].case_impacts[0].citations[-1].value is None
                        assert await get(base + f"/impact-tasks/{task['id']}/result") == view
                        assert (await get(base + "/impact-snapshots", version_id=versions[1]))["items"] == [view["snapshot"]]
                        assert not (await get(base + "/impact-snapshots", version_id=versions[0]))["items"]
                        assert (await get(base + "/impact-tasks", limit=1))["items"] == [task]
                        for suffix in ("/impact-tasks", "/impact-snapshots", f"/impact-tasks/{task['id']}",
                            f"/impact-tasks/{task['id']}/result", f"/impact-snapshots/{task['snapshot_id']}"):
                            assert (await client.get(base.replace(project["id"], other["id"]) + suffix)).status_code == 404
                        stored = (await objects.read_object_uri(typed.snapshot.storage_uri))["content"]
                        assert hashlib.sha256(stored).hexdigest() == typed.snapshot.sha256
                        assert json.loads(stored) == view["result"]
                        (evidence / "result.json").write_bytes(stored)
                        requirement_review = None
                        if verify_requirements:
                            assert not interrupt and result.rules
                            review_request = {"request_id": "production-requirement-review", "rule_id": result.rules[0].id,
                                "decision": "accept", "note": "仅确认来源及设计关联，运行和部署另验",
                                "change": "modified", "title": "订单保留期", "statement": "订单保留九天",
                                "points": [{"key": "retention", "description": "验证九天保留期"}],
                                "case_links": [{"case_version_id": case.version.id, "point_keys": ["retention"]}]}
                            if verify_coverage:
                                review_request["points"].append({"key":"boundary","description":"验证九天保留边界"})
                            requirement_review = await post(base + f"/impact-snapshots/{task['snapshot_id']}/reviews", review_request, 201)
                            requirement_id = requirement_review["requirement_version"]["requirement_id"]
                            matrix = await get(base + f"/requirements/{requirement_id}/matrix")
                            assert matrix["points"][0]["design_status"] == "design_covered"
                            assert matrix["points"][0]["execution_status"] == "not_assessed"
                            assert matrix["deployment_status"] == "unknown"
                            assert requirement_review["source"]["snapshot_sha256"] == typed.snapshot.sha256
                            assert len(requirement_review["source"]["case_checkpoints"]) == 1
                            assert len(sends) == 2 and (await objects.read_object_uri(typed.snapshot.storage_uri))["content"] == stored
                            (evidence / "requirement-review.json").write_text(json.dumps(requirement_review,ensure_ascii=False,indent=2),encoding="utf-8")
                            (evidence / "requirement-matrix.json").write_text(json.dumps(matrix,ensure_ascii=False,indent=2),encoding="utf-8")
                        generation = None
                        if verify_generation:
                            assert requirement_review and not interrupt
                            generation_settings = (await get(root+"/case-generation-settings"))["payload"]
                            generation_settings.update(transport_policy="single_attempt",call_timeout_seconds=30)
                            saved = await client.put(root+"/case-generation-settings",json=generation_settings)
                            assert saved.status_code == 200
                            assert mysql.get("test_case.incremental_generation_settings",scope_key=project["id"]).payload == generation_settings
                            generation_request = {"creation_key":"production-incremental", "requirement_id":requirement_id,
                                "requirement_version_id":requirement_review["requirement_version"]["id"],
                                "target_case_version_ids":[case.version.id], "include_new_cases":True,
                                "new_case_mode_key":"api_testing", "model_key":model.key}
                            group = await post(base+"/case-generations",generation_request,202)
                            assert await get(base+"/case-generations/by-request",creation_key=generation_request["creation_key"]) == group
                            generation_scope = await get(base+f"/case-generations/{group['id']}/scope")
                            queued = await get(base+f"/case-generations/{group['id']}")
                            assert len(queued["modules"]) == 2 and all(t["status"] == "queued" for t in queued["modules"])
                            changed = await client.put(root+"/case-generation-settings",json={**generation_settings,"max_input_bytes":1})
                            assert changed.status_code == 200
                            for i in range(2):
                                await worker("run_case_generation_worker",f"generation-{i}","case_generation")
                            generation = await get(base+f"/case-generations/{group['id']}")
                            assert all(t["status"] == "completed" for t in generation["modules"]), generation
                            assert len(sends) == 4
                            update = generation["modules"][0]["checkpoint"]["receipt"]["versions"][0]
                            created = generation["modules"][1]["checkpoint"]["receipt"]["versions"][0]
                            assert update["case_id"] == case.case.id and update["version"] == later["version"]+1
                            updated_case = await get(f"/test-cases/{case.case.id}")
                            case_versions = await get(f"/test-cases/{case.case.id}/versions")
                            draft = next(version for version in case_versions if version["id"] == update["version_id"])
                            assert next(version for version in case_versions if version["id"] == case.version.id) == case.version.model_dump(mode="json")
                            new_case = await get(f"/test-cases/{created['case_id']}")
                            assert updated_case["active_version_id"] == case.case.active_version_id
                            assert new_case["active_version_id"] is None and new_case["lifecycle_status"] == "draft"
                            assert len(draft["content_json"]["steps"]) == 12 and draft["content_json"]["深层扩展"] == case.version.content_json["深层扩展"]
                            assert draft["generation_metadata"]["incremental_generation"]["requirement_version_id"] == generation_request["requirement_version_id"]
                            generation_responses=[]
                            for module in generation["modules"]:
                                response = await get(base+f"/case-generation-tasks/{module['id']}/response")
                                generation_responses.append(response)
                                assert await get(base+f"/case-generation-tasks/{module['id']}") == module
                                await queues["case_generation"].enqueue({"task_type":"case_generation","project_id":project["id"],"task_id":module["id"]})
                                terminal_gen = processes.start("generation-terminal-"+module["module_id"],"-m","src.cli.run_case_generation_worker",
                                    "--once","--worker-id","generation-terminal")
                                assert await processes.wait(terminal_gen) == 0
                            assert len(sends) == 4 and await get(base+f"/case-generations/{group['id']}") == generation
                            refs_gen=[group["scope"],*[t["checkpoint"]["response"] for t in generation["modules"]]]
                            for reference in refs_gen:
                                raw=(await objects.read_object_uri(reference["storage_uri"]))["content"]
                                assert len(raw)==reference["size_bytes"] and hashlib.sha256(raw).hexdigest()==reference["sha256"]
                                (evidence/(reference["sha256"]+".json")).write_bytes(raw)
                            (evidence/"generation.json").write_text(json.dumps({"report":generation,"scope":generation_scope,
                                "responses":generation_responses,"draft":draft,"case":updated_case,"new_case":new_case,
                                "mysql_policy":saved.json(),"independent_generation_workers":True,"terminal_redelivery_no_send":True},
                                ensure_ascii=False,indent=2),encoding="utf-8")
                        retry_state = None
                        if verify_retry:
                            from generation_retry_production_scenario import retry_from_production
                            retry_state = await retry_from_production(base=base, root=root, request=generation_request,
                                settings=settings, policy=generation_settings, get=get, post=post, client=client,
                                worker=worker, queues=queues, processes=processes, objects=objects, evidence=evidence, sends=sends)
                        coverage_state = None
                        if verify_coverage:
                            from requirement_coverage_production_scenario import coverage_from_production
                            coverage_state = await coverage_from_production(base=base,review=requirement_review,
                                generation=generation,retry_state=retry_state,get=get,post=post,client=client,
                                settings=settings,evidence=evidence,sends=sends)
                            matrix = coverage_state["matrix"]
                        sent = len(sends)
                        execution_state = None
                        if verify_execution:
                            from requirement_execution_production_scenario import execution_from_production
                            execution_state = await execution_from_production(base=base, root=root, review=requirement_review,
                                settings=settings, get=get, post=post, client=client, evidence=evidence, sends=sends,
                                business_http=verify_execution, source_ref=source(SimpleNamespace(id=document['id']), views[1]))
                            matrix = execution_state["matrix"]
                        applicability_state = None
                        if verify_applicability:
                            from business_applicability_production_scenario import decisions_from_production
                            applicability_state = await decisions_from_production(root=root, review=requirement_review,
                                get=get, post=post, client=client, settings=settings, evidence=evidence,
                                targets=verify_applicability, sends=sends)
                        environment_planning_state = None
                        if verify_environment_planning:
                            from business_planning_production_scenario import planning_from_production
                            environment_planning_state = await planning_from_production(root=root, state=applicability_state,
                                case=case, model_key=model.key, settings=settings, processes=processes,
                                get=get, post=post, client=client, evidence=evidence, sends=sends)
                            sent += environment_planning_state["physical_planning_calls"]
                        environment_restrictions_state = None
                        if verify_environment_restrictions:
                            from business_environment_restrictions_production_scenario import restrictions_from_production
                            environment_restrictions_state = await restrictions_from_production(root=root,state=applicability_state,
                                case=case,model_key=model.key,settings=settings,processes=processes,
                                get=get,post=post,client=client,evidence=evidence,sends=sends)
                            sent += environment_restrictions_state["physical_planning_calls"]
                        environment_admission_state = None
                        if verify_environment_admission:
                            from business_environment_admission_production_scenario import admission_from_production
                            environment_admission_state = await admission_from_production(root=root,state=applicability_state,
                                case=case,model_key=model.key,settings=settings,processes=processes,
                                get=get,post=post,client=client,evidence=evidence,sends=sends)
                            sent += environment_admission_state["physical_model_calls"]
                        await queues["document_impact"].enqueue({"schema_version": 1, "task_type": "document_impact",
                            "project_id": project["id"], "task_id": task["id"]})
                        terminal = processes.start("impact-worker-terminal", "-m", "src.cli.run_document_impact_worker",
                            "--once", "--worker-id", "impact-terminal")
                        assert await processes.wait(terminal) == 0
                        assert len(sends) == sent and await get(base + f"/impact-tasks/{task['id']}") == task
                        assert await get(base + f"/impact-snapshots/{task['snapshot_id']}") == view
                        await processes.stop(api)
                        disabled = settings.model_copy(deep=True); disabled.orchestration.redis_task_dispatch_enabled = False
                        processes.env = child_environment(disabled)
                        api = processes.start("impact-api-disabled", *args)
                        await wait_api(client, api)
                        assert await post(base + "/impact-tasks", request, 202) == task
                        assert await get(base + f"/impact-tasks/{task['id']}/result") == view
                        assert (await client.post(base + "/impact-tasks", json={**request, "creation_key": "disabled-new"})).status_code == 503
                        if requirement_review:
                            assert await post(base + f"/impact-snapshots/{task['snapshot_id']}/reviews", review_request, 201) == requirement_review
                            assert await get(base + "/requirement-reviews/by-request", request_id=review_request["request_id"]) == requirement_review
                            assert await get(base + f"/requirements/{requirement_id}/matrix") == matrix
                            assert (await get(base + "/requirements"))["items"][0]["version"] == requirement_review["requirement_version"]
                            assert (await get(base + f"/requirements/{requirement_id}/versions")) == [requirement_review["requirement_version"]]
                        if generation:
                            assert await post(base+"/case-generations",generation_request,202) == generation["group"]
                            assert await get(base+"/case-generations/by-request",creation_key=generation_request["creation_key"]) == generation["group"]
                            assert await get(base+f"/case-generations/{generation['group']['id']}") == generation
                            assert await get(base+f"/case-generations/{generation['group']['id']}/scope") == generation_scope
                            assert (await client.post(base+"/case-generations",json={**generation_request,"creation_key":"disabled-new"})).status_code == 503
                        if retry_state:
                            from generation_retry_production_scenario import retry_after_restart
                            await retry_after_restart(base=base,state=retry_state,get=get,post=post,client=client,evidence=evidence)
                        if coverage_state:
                            from requirement_coverage_production_scenario import coverage_after_restart
                            await coverage_after_restart(state=coverage_state,get=get,post=post,client=client,
                                evidence=evidence,project_id=project["id"])
                        if execution_state:
                            from requirement_execution_production_scenario import execution_after_restart
                            await execution_after_restart(state=execution_state, get=get, post=post, client=client,
                                settings=settings, evidence=evidence, project_id=project["id"], sends=sends)
                        if applicability_state:
                            from business_applicability_production_scenario import decisions_after_restart
                            await decisions_after_restart(state=applicability_state, get=get, post=post, client=client,
                                settings=settings, evidence=evidence, root=root, sends=sends,
                                extra_reviews=(environment_restrictions_state["additional_reviews"] if environment_restrictions_state else [])
                                    +(environment_admission_state["additional_reviews"] if environment_admission_state else []),
                                allowed_HTTP_counts=(environment_admission_state["actual_HTTP_counts"] if environment_admission_state else None))
                        if environment_planning_state:
                            from business_planning_production_scenario import planning_after_restart
                            await planning_after_restart(state=environment_planning_state, root=root,
                                get=get, post=post, client=client, evidence=evidence, settings=settings, sends=sends)
                        if environment_restrictions_state:
                            from business_environment_restrictions_production_scenario import restrictions_after_restart
                            await restrictions_after_restart(state=environment_restrictions_state,root=root,get=get,post=post,
                                evidence=evidence,sends=sends)
                        if environment_admission_state:
                            from business_environment_admission_production_scenario import admission_after_restart
                            await admission_after_restart(state=environment_admission_state,root=root,get=get,post=post,
                                evidence=evidence,sends=sends)
                        assert len(sends) == sent
                        for path, parse in zip(paths, views):
                            raw = (await objects.read_object_uri(parse.snapshot.seed.original_asset.storage_uri))["content"]
                            assert raw == path.read_bytes()
                        for suffix, _ in settings_paths:
                            assert (await get(root + "/" + suffix))["revision"] >= frozen[suffix]["revision"]
                        (evidence / "physical-requests.json").write_text(json.dumps(sends, ensure_ascii=False, indent=2), encoding="utf-8")
                        # Save actual durable call evidence before fixture cleanup. A green
                        # assertion or HTTP count alone must not replace the original objects.
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_impact_calls WHERE task_id=%s ORDER BY id", (task["id"],))
                            call_records = [row["record"] for row in cur.fetchall()]
                            cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_impact_response_checks WHERE call_id=ANY(%s::uuid[])",
                                ([call["id"] for call in call_records],))
                            checks = [row["record"] for row in cur.fetchall()]
                            cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_impact_manifests WHERE task_id=%s", (task["id"],))
                            manifest = cur.fetchone()["record"]
                        record = {"calls": call_records, "checks": checks, "manifest": manifest,
                            "input_checkpoint": task["input_checkpoint"]}
                        (evidence / "checkpoints.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                        refs = [task["input_checkpoint"], manifest, *[check["result"] for check in checks]]
                        if result.implementation_version == "impact-coordinator-v2":
                            record["reasoning_checkpoint"] = result.reasoning_checkpoint.model_dump(mode="json")
                            refs.append(record["reasoning_checkpoint"])
                            (evidence / "checkpoints.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                        refs += [call[field] for call in call_records for field in ("request", "response") if call.get(field)]
                        for reference in refs:
                            raw = (await objects.read_object_uri(reference["storage_uri"]))["content"]
                            assert len(raw) == reference["size_bytes"] and hashlib.sha256(raw).hexdigest() == reference["sha256"]
                            (evidence / (reference["sha256"] + ".json")).write_bytes(raw)
                        (evidence / "verification.json").write_text(json.dumps({"scope": "production API + independent parsing/comparison/impact CLI + actual stores/HTTP",
                            "task": task, "snapshot": view["snapshot"], "http_calls": len(sends), "adapter": adapter,
                            "actual_process_killed": interrupt, "unknown_call_not_resent": interrupt,
                            "independent_outbox_relay": True, "bearer_denied": True,
                            "all_originals_unchanged": True, "default_model_judgement": False,
                            "terminal_redelivery_no_send": True, "disabled_dispatch_history_readable": True,
                            "production_incremental_generation_used": verify_generation}, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    for queue in queues.values(): await queue.close()
                    collect_objects(settings, owned)
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name == "docker.json":
                (evidence / path.name).write_bytes(path.read_bytes())
