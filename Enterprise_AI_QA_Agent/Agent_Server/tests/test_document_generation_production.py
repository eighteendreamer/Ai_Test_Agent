"""Ordinary document generation: real files/API/CLI/stores, no impact review fixture."""
import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.generation_runtime import GENERATION_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_document_impact_calls import provider
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_DOCUMENT_GENERATION") != "1",
    reason="actual isolated MySQL/PostgreSQL/RustFS/Redis and production API/CLI")]


@pytest.mark.parametrize("streaming", [False, True])
async def test_ordinary_document_modules_and_independent_saved_response_recovery(tmp_path, streaming):
    evidence = Path(os.getenv("QA_DOCUMENT_GENERATION_EVIDENCE_DIR", str(tmp_path)))
    if streaming:
        evidence = evidence / "stream"
    evidence.mkdir(parents=True, exist_ok=True)
    original = "# 商城需求 😀\n所有接口均需登录，退款不能超过实付金额。\n\n## 注册\n手机号必须唯一，验证码五分钟有效。\n\n## 订单\n### 创建\n库存不足不能下单。\n### 退款\n退款金额上限为实付金额。\n\n## 查询\n仅能查询自己的订单，分页每页最多20条。\n"
    source = evidence / "ordinary-requirements.md"
    source.write_text(original, encoding="utf-8")
    def responder(body):
        context = json.loads(body["messages"][-1]["content"])["evidence_context"]["document_generation"]
        segment = context["segment"]
        content = {"目标": segment["title"], "依据": context["text"][segment["start_char"]:segment["end_char"]],
            "全局规则": "所有接口均需登录，退款不能超过实付金额。", "嵌套": {"零": 0, "布尔": False, "空": None}}
        if segment["index"] == 1: content["完整长字段"] = "保留长响应原件" * 10000
        return json.dumps({"cases": [{"case_key": "ordinary-module-" + segment["module_id"],
            "title": segment["title"], "case_type": "json", "content_json": content,
            "execution_notes": "根据完整JSON执行，核对业务结果并保留证据"}], "warnings": []}, ensure_ascii=False)
    try:
        with provider(responder=responder, message_responder=
                (lambda body: {"role": "assistant", "content": responder(body)}) if streaming else None) as (endpoint, calls, _, _):
            async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
                settings.orchestration.redis_task_outbox_streams = ",".join([DOCUMENT_PARSE_STREAM, GENERATION_STREAM])
                models = MySQLModelConfigStore(settings); models.initialize()
                model = models.upsert(ModelConfigUpdateRequest(model_name="ordinary-document-controlled", provider="openai",
                    transport="openai_chat_completions", base_url=endpoint, api_key=uuid4().hex, is_active=True,
                    use_provider_defaults=False, capability_overrides=ModelCapabilitiesOverride(streaming=streaming),
                    context_window=128000, max_output_tokens=1024))
                processes.env = child_environment(settings)
                port = free_port()
                args = ("-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(port))
                api = processes.start("ordinary-api", *args)
                queues = {}
                try:
                    for stream, suffix in [(DOCUMENT_PARSE_STREAM, "document_parsing"), (GENERATION_STREAM, "case_generation")]:
                        queues[stream] = RedisTaskQueue(settings.database.redis_url, stream=stream,
                            group=settings.orchestration.redis_task_consumer_group + ":" + suffix)
                        await queues[stream].connect()
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}/api/v1", timeout=50) as client:
                        await wait_api(client, api)
                        async def post(path, payload, status=200):
                            response = await client.post(path, json=payload)
                            (evidence / "last-http.json").write_text(json.dumps({"path": path,
                                "status": response.status_code, "body": response.text}, ensure_ascii=False, indent=2), encoding="utf-8")
                            assert response.status_code == status, (response.status_code, response.text)
                            return response.json()
                        project = await post("/projects", {"project_key": "ordinary-"+uuid4().hex, "name": "普通文档模块生成"}, 201)
                        doc = await post("/registry/api-docs/upload", {"project_id": project["id"], "filename": source.name,
                            "request_id": "ordinary-upload", "content_base64": base64.b64encode(source.read_bytes()).decode()})
                        owned.append(doc["original_asset"]["storage_uri"])
                        base = f"/projects/{project['id']}/documents/{doc['id']}"
                        parsed = await post(base+"/parse-tasks", {"creation_key": "ordinary-parse", "version_id": doc["current_version_id"]}, 202)
                        await PostgresTaskOutbox(settings).relay_once(queues[DOCUMENT_PARSE_STREAM])
                        worker = processes.start("ordinary-parser", "-m", "src.cli.run_document_parse_worker", "--once", "--worker-id", "ordinary-parser")
                        assert await processes.wait(worker) == 0
                        parsed = (await client.get(base+"/parse-tasks/"+parsed["id"])).json()
                        assert parsed["status"] == "completed"
                        snapshot = (await client.get(base+"/parse-snapshots/"+parsed["snapshot_id"])).json()
                        owned.append(snapshot["snapshot"]["storage_uri"])
                        assert "退款金额上限" in snapshot["result"]["text"] and not calls
                        policy_path = f"/projects/{project['id']}/case-generation-settings"
                        policy = (await client.get(policy_path)).json()["payload"]
                        policy.update(transport_policy="single_attempt", call_timeout_seconds=15, max_response_bytes=25000)
                        assert (await client.put(policy_path, json=policy)).status_code == 200
                        request = {"creation_key": "ordinary-generation", "parse_snapshot_id": parsed["snapshot_id"],
                            "objective": "覆盖商城需求的所有章节", "mode_key": "api_testing", "max_cases_per_module": 3, "model_key": model.key}
                        group = await post(base+"/document-case-generations", request, 202)
                        scope = (await client.get(base+f"/case-generations/{group['id']}/scope")).json()
                        assert scope["implementation_version"] == "document-generation-scope-v1"
                        assert len(scope["modules"]) == 3 and "requirement" not in scope
                        assert all(("response_transport_protocol" in item["generation"]) is streaming for item in scope["modules"])
                        assert "".join(scope["text"][s["start_char"]:s["end_char"]] for s in scope["segments"]) == scope["text"]
                        assert scope["text"] == snapshot["result"]["text"]
                        template_path = f"/projects/{project['id']}/test-case-generation-template"
                        current_template = (await client.get(template_path)).json()
                        replacement = current_template["payload"]
                        replacement["writing_guidance"] = "新的写作指导不能替换此前提交的固定模板"
                        assert (await client.put(template_path, json=replacement)).status_code == 200
                        # Source freezes before independent CLI execution; changing the live policy is safe.
                        policy["max_response_bytes"] = 8000000
                        assert (await client.put(policy_path, json=policy)).status_code == 200
                        await processes.stop(api); api = processes.start("ordinary-api-restarted", *args); await wait_api(client, api)
                        assert (await post(base+"/document-case-generations", request, 202)) == group
                        async def run(name):
                            await PostgresTaskOutbox(settings).relay_once(queues[GENERATION_STREAM])
                            worker = processes.start(name, "-m", "src.cli.run_case_generation_worker", "--once", "--worker-id", name)
                            assert await processes.wait(worker) == 0
                        for i in range(3): await run("ordinary-generation-"+str(i))
                        report = (await client.get(base+f"/case-generations/{group['id']}")).json()
                        assert [t["status"] for t in report["modules"]] == ["completed", "failed", "completed"], report
                        assert len(calls) == 3
                        for call, module in zip(calls, scope["modules"]):
                            assert bool(call.get("stream")) is streaming
                            assert call["messages"][-1]["content"] == module["generation"]["invocation"]["messages"][0]["content"]
                            assert json.loads(call["messages"][-1]["content"])["evidence_context"]["generation_template"] == scope["template"]
                        failed = report["modules"][1]
                        assert failed["error_code"] == "response_validation_rejected" and failed["checkpoint"]["phase"] == "response_saved"
                        retry = await post(base+f"/case-generation-tasks/{failed['id']}/retries", {"creation_key": "ordinary-recover-one"}, 202)
                        assert retry["retry"]["strategy"] == "saved_response"
                        await run("ordinary-retry")
                        child = (await client.get(base+f"/case-generation-tasks/{retry['task']['id']}")).json()
                        assert child["status"] == "completed" and len(calls) == 3
                        assert (await client.get(base+f"/case-generations/{group['id']}")).json() == report
                        versions = []
                        for task in [report["modules"][0], child, report["modules"][2]]:
                            ref = task["checkpoint"]["receipt"]["versions"][0]
                            values = (await client.get(f"/test-cases/{ref['case_id']}/versions")).json()
                            version = next(v for v in values if v["id"] == ref["version_id"])
                            assert version["content_json"]["嵌套"] == {"零": 0, "布尔": False, "空": None}
                            assert version["generation_metadata"]["document_generation"]["parse_snapshot_id"] == parsed["snapshot_id"]
                            versions.append(version)
                        for task in [*report["modules"], child]:
                            await queues[GENERATION_STREAM].enqueue({"schema_version": 1, "task_type": "case_generation", "project_id": project["id"], "task_id": task["id"]})
                            await run("ordinary-terminal-"+task["id"][:6])
                        assert len(calls) == 3
                        terminal_calls = len(calls)
                        foreign = await post("/projects", {"project_key":"foreign-"+uuid4().hex,"name":"其他项目"}, 201)
                        assert (await client.get(f"/projects/{foreign['id']}/documents/{doc['id']}/case-generations/{group['id']}/scope")).status_code == 404
                        if os.getenv("RUN_LIVE_DOCUMENT_GENERATION_PRODUCT") == "1":
                            from document_generation_product_scenario import document_generation_product
                            await document_generation_product(tmp_path=tmp_path, port=port, evidence=evidence, project=project,
                                doc=doc, snapshot=snapshot, model_key=model.key, client=client, run=run, calls=calls)
                        formats = []
                        if os.getenv("RUN_LIVE_DOCUMENT_GENERATION_FORMATS") == "1":
                            from docx import Document
                            from test_document_parsers import actual_pdf
                            word = Document()
                            word.add_heading("商城需求 😀", 0)
                            word.add_paragraph("所有接口均需登录，退款不能超过实付金额。")
                            for title in ("注册", "订单", "查询"):
                                word.add_heading(title, 1); word.add_paragraph("必须核对权限，失败不能修改原有数据。")
                            table = word.add_table(rows=2, cols=2)
                            table.cell(0, 0).text = "分页"; table.cell(0, 1).text = "最多20条"
                            table.cell(1, 0).text = "金额"; table.cell(1, 1).text = "不超过实付"
                            word_path = evidence/"ordinary-requirements.docx"; word.save(word_path)
                            pdf_path = evidence/"ordinary-requirements.pdf"; actual_pdf(pdf_path)
                            for path, expected_modules in [(word_path, 3), (pdf_path, 1)]:
                                newdoc = await post("/registry/api-docs/upload", {"project_id":project["id"],"filename":path.name,
                                    "request_id":"ordinary-upload-"+path.suffix,"content_base64":base64.b64encode(path.read_bytes()).decode()})
                                owned.append(newdoc["original_asset"]["storage_uri"])
                                newbase = f"/projects/{project['id']}/documents/{newdoc['id']}"
                                parsed_task = await post(newbase+"/parse-tasks", {"creation_key":"ordinary-parse-"+path.suffix,"version_id":newdoc["current_version_id"]},202)
                                await PostgresTaskOutbox(settings).relay_once(queues[DOCUMENT_PARSE_STREAM])
                                parser = processes.start("ordinary-format-parser-"+path.suffix[1:],"-m","src.cli.run_document_parse_worker","--once","--worker-id","ordinary-format-parser")
                                assert await processes.wait(parser) == 0
                                parsed_task = (await client.get(newbase+"/parse-tasks/"+parsed_task["id"])).json()
                                assert parsed_task["status"] == "completed"
                                parsed_view = (await client.get(newbase+"/parse-snapshots/"+parsed_task["snapshot_id"])).json()
                                owned.append(parsed_view["snapshot"]["storage_uri"])
                                submitted = await post(newbase+"/document-case-generations", {**request,"creation_key":"ordinary-generation-"+path.suffix,
                                    "parse_snapshot_id":parsed_task["snapshot_id"]},202)
                                fixed = (await client.get(newbase+f"/case-generations/{submitted['id']}/scope")).json()
                                assert fixed["text"] == parsed_view["result"]["text"] and len(fixed["modules"]) == expected_modules
                                assert "".join(fixed["text"][s["start_char"]:s["end_char"]] for s in fixed["segments"]) == fixed["text"]
                                before = len(calls)
                                for index in range(expected_modules): await run("ordinary-format-"+path.suffix[1:]+"-"+str(index))
                                final = (await client.get(newbase+f"/case-generations/{submitted['id']}")).json()
                                assert all(t["status"] == "completed" for t in final["modules"]), final
                                assert len(calls) == before+expected_modules
                                for call in calls[before:]:
                                    context = json.loads(call["messages"][-1]["content"])["evidence_context"]["document_generation"]
                                    assert context["text"] == fixed["text"]
                                    assert context["tables"] == parsed_view["result"]["tables"]
                                if path.suffix == ".pdf": assert any("无法可靠还原" in warning for warning in fixed["basis_limitations"])
                                formats.append({"filename":path.name,"parse":parsed_view,"fixed_scope":fixed,"report":final})
                        refs = []
                        with postgres_connect(settings) as conn, conn.cursor() as cur:
                            cur.execute(f"SELECT record FROM {settings.database.postgres_api_doc_table}_case_gen_groups")
                            refs.extend(row["record"]["scope"] for row in cur.fetchall())
                            cur.execute(f"SELECT checkpoint FROM {settings.database.postgres_api_doc_table}_case_gen_tasks")
                            for row in cur.fetchall():
                                ref = (row["checkpoint"] or {}).get("response")
                                if ref and ref not in refs: refs.append(ref)
                        downloads = []
                        for i, ref in enumerate(refs):
                            raw = (await objects.read_object_uri(ref["storage_uri"]))["content"]
                            assert len(raw) == ref["size_bytes"] and hashlib.sha256(raw).hexdigest() == ref["sha256"]
                            (evidence/f"object-{i}.json").write_bytes(raw); downloads.append(ref)
                        (evidence/"summary.json").write_text(json.dumps({"scope":"production API, real Markdown and independent parser/generation/retry CLI; controlled HTTP model only",
                            "group":group,"fixed_scope":scope,"report":report,"retry":retry,"child":child,"versions":versions,
                            "physical_http_requests":calls,"downloaded_objects":downloads,"terminal_replay_model_calls":terminal_calls,
                            "template_changed_after_freeze":True,"streaming_transport":streaming,"formats":formats}, ensure_ascii=False, indent=2), encoding="utf-8")
                finally:
                    for queue in queues.values(): await queue.close()
                    with postgres_connect(settings) as conn, conn.cursor() as cur:
                        table = settings.database.postgres_api_doc_table+"_case_gen_groups"
                        cur.execute(f"SELECT record FROM {table}")
                        for row in cur.fetchall():
                            if row["record"]["scope"]["storage_uri"] not in owned: owned.append(row["record"]["scope"]["storage_uri"])
                        cur.execute(f"SELECT checkpoint FROM {settings.database.postgres_api_doc_table}_case_gen_tasks")
                        for row in cur.fetchall():
                            ref = (row["checkpoint"] or {}).get("response")
                            if ref and ref["storage_uri"] not in owned: owned.append(ref["storage_uri"])
    finally:
        for pattern in ("ordinary-*.std*", "product-*.std*"):
            for path in tmp_path.glob(pattern): (evidence/path.name).write_bytes(path.read_bytes())
