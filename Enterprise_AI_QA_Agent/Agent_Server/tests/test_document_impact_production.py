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
            ("impact_reasoning_plans", "record")]:
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
async def test_production_fixed_analysis_history_and_process_recovery(tmp_path, extension, adapter, interrupt):
    name = f"{extension}-{'langchain' if adapter else 'native'}-{'restart' if interrupt else 'finish'}"
    evidence = Path(os.getenv("QA_IMPACT_PRODUCTION_EVIDENCE_DIR", str(tmp_path / "evidence"))) / name
    evidence.mkdir(parents=True, exist_ok=True)
    paths = write_pair(tmp_path, extension)
    for path in paths: (evidence / path.name).write_bytes(path.read_bytes())
    holder, views = {}, []
    def respond(body):
        payload = json.loads(body["messages"][-1]["content"])
        holder.setdefault("first", DocumentImpactUnit.model_validate(payload["unit"]))
        holder["manifest"] = SimpleNamespace(sources=[SimpleNamespace(**item) for item in payload["fixed_source_catalog"]])
        factory = grouped_responder if payload["analysis_protocol"]["version"] in {"impact-call-v6", "impact-call-v7"} else responder
        return factory(holder, views, holder["case"])(body)
    try:
        with provider(gated=interrupt, responder=respond) as (endpoint, sends, arrived, release):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.model.langchain_model_adapter_enabled = adapter
                settings.api_auth_token = SecretStr(uuid4().hex)
                settings.orchestration.redis_task_outbox_streams = ",".join(
                    [DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM])
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
                        (DOCUMENT_DIFFERENCE_STREAM, "document_difference"), (DOCUMENT_IMPACT_STREAM, "document_impact")]:
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
                        sent = len(sends)
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
                            "terminal_redelivery_no_send": True, "disabled_dispatch_history_readable": True}, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    for queue in queues.values(): await queue.close()
                    collect_objects(settings, owned)
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"} or path.name == "docker.json":
                (evidence / path.name).write_bytes(path.read_bytes())
