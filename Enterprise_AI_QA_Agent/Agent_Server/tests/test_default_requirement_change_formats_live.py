"""Default-model impact analysis on real DOCX and PDF requirement changes.

Parsing and deterministic comparison already have production acceptance; this
covers the missing impact segment for non-Markdown originals.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from docx import Document
from pydantic import SecretStr
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pypdf import PdfWriter
from redis import Redis

from src.application.documents.difference_service import DOCUMENT_DIFFERENCE_STREAM
from src.application.documents.parsing_service import DOCUMENT_PARSE_STREAM
from src.core.config import get_settings
from src.infrastructure.application_config_store import MySQLApplicationConfigStore
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.runtime.document_impact_runtime import DOCUMENT_IMPACT_STREAM
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_management import TestCaseBundle as CaseBundle
from src.schemas.document_impact_views import DocumentImpactSnapshotView
from src.schemas.document_parsing import DocumentParseSnapshotView
from src.schemas.model_config import model_invocation_parameters
from src.schemas.settings import ModelConfigUpdateRequest
from test_execution_worker_cli import child_environment, free_port, isolated_runtime, wait_api

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_LIVE_DEFAULT_REQUIREMENT_CHANGE_FORMATS") != "1",
    reason="real default model, production API/independent CLIs, isolated MySQL/PG/Redis/RustFS")]


V1_LINES = [
    "Order Query Requirements",
    "Order detail: GET /orders/{id} returns 200 with status (string) and total",
    "(integer cents, sum of original item prices).",
    "A missing order returns 404.",
]
V2_LINES = [
    "Order Query Requirements",
    "Order detail: GET /orders/{id} returns 200 with status (string), total",
    "(integer cents, discounted payable total) and discount (integer cents,",
    "total discount, 0 when no discount is applied).",
    "A missing order returns 404.",
]


def write_docx(path: Path, lines: list[str]) -> bytes:
    document = Document()
    document.add_heading(lines[0], 1)
    for line in lines[1:]:
        document.add_paragraph(line)
    document.save(path)
    return path.read_bytes()


def write_pdf(path: Path, lines: list[str]) -> bytes:
    writer = PdfWriter()
    for text in lines:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        literal = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream.set_data(f"BT /F1 12 Tf 50 700 Td ({literal}) Tj ET".encode())
        page[NameObject("/Contents")] = stream
    writer.write(path)
    return path.read_bytes()


WRITERS = {"docx": write_docx, "pdf": write_pdf}
PARSER_IDS = {"docx": "docx", "pdf": "pdf"}


@pytest.mark.skipif(os.getenv("RUN_LIVE_DEFAULT_REQUIREMENT_CHANGE_FORMATS") != "1",
    reason="see module gate")
@pytest.mark.parametrize("extension", ["docx", "pdf"])
async def test_default_model_impact_analysis_for_office_and_pdf_changes(tmp_path, extension):
    evidence = Path(os.environ["QA_DEFAULT_REQUIREMENT_CHANGE_FORMATS_DIR"]) / extension
    evidence.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")

    def model_sends():
        return len(list((evidence / "physical").glob("*.send.json")))

    configured_store = MySQLModelConfigStore(get_settings().model_copy(deep=True))
    configured = configured_store.get_default_active()
    configured_parameters = model_invocation_parameters(configured)
    facts = {"extension": extension, "model_id": configured.model_id,
        "transport": configured.transport, "configured_parameters": configured_parameters,
        "controlled_model": False, "impact_analysis": None}
    writer = WRITERS[extension]
    v1_bytes = writer(tmp_path / f"requirements-v1.{extension}", V1_LINES)
    v2_bytes = writer(tmp_path / f"requirements-v2.{extension}", V2_LINES)
    try:
        async with isolated_runtime(tmp_path) as (settings, processes, _, objects, owned):
            settings.api_auth_token = SecretStr(uuid4().hex)
            settings.orchestration.redis_task_outbox_streams = ",".join([
                DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM, DOCUMENT_IMPACT_STREAM])
            models = MySQLModelConfigStore(settings)
            models.initialize()
            model = models.upsert(ModelConfigUpdateRequest(
                model_name=configured.model_id, provider=configured.provider,
                transport=configured.transport, request_profile=configured.request_profile,
                base_url=configured.api_base_url, api_key=configured.api_key,
                auth_type=configured.auth_type, oauth_provider=configured.oauth_provider,
                oauth_refresh_token=configured.oauth_refresh_token,
                applications=configured.applications, is_active=True, use_provider_defaults=False,
                capability_overrides=configured.capability_overrides,
                context_window=configured.context_window,
                max_output_tokens=configured.max_output_tokens))
            assert model_invocation_parameters(models.get_default_active()) == configured_parameters
            save("model-parameters.json", configured_parameters)
            # These document streams use process-level names shared by every
            # run; a previous parameter's straggler delivery would make the
            # one-shot Worker claim a task row that does not exist here.
            shared = Redis.from_url(settings.database.redis_url)
            try:
                shared.delete(DOCUMENT_PARSE_STREAM, DOCUMENT_DIFFERENCE_STREAM,
                    DOCUMENT_IMPACT_STREAM)
            finally:
                shared.close()
            processes.env = child_environment(settings)
            processes.env["QA_DEFAULT_PROBE_DIR"] = str(evidence / "physical")
            api_port = free_port()
            api_args = ("-m", "tests.default_model_probe", "uvicorn", "src.main:app",
                        "--host", "127.0.0.1", "--port", str(api_port))
            api = processes.start("requirement-formats-api", *api_args)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}/api/v1", timeout=300,
                headers={"Authorization": "Bearer " + settings.api_auth_token.get_secret_value()}) as client:
                await wait_api(client, api)

                async def post(path, body=None, status=200):
                    response = await client.post(path, json=body)
                    assert response.status_code == status, (path, response.status_code, response.text)
                    return response.json()

                async def get(path):
                    response = await client.get(path)
                    assert response.status_code == 200, (path, response.status_code, response.text)
                    return response.json()

                async def relay(stream, limit):
                    queue = RedisTaskQueue(settings.database.redis_url, stream=stream,
                        group=settings.orchestration.redis_task_consumer_group, socket_timeout_seconds=1)
                    await queue.connect()
                    try:
                        return await PostgresTaskOutbox(settings).relay_once(queue, limit=limit)
                    finally:
                        await queue.close()

                async def run_worker(name, module, worker_id, *, timeout=600):
                    process = processes.start(name, "-m", "tests.default_model_probe", module,
                        "--once", "--worker-id", worker_id)
                    assert await processes.wait(process, timeout=timeout) == 0, (
                        name, (tmp_path / f"{name}.stderr").read_text(
                            encoding="utf-8", errors="replace")[-2000:])

                project = await post("/projects", {"project_key": "req-formats-" + uuid4().hex[:10],
                    "name": "默认模型多格式需求变更", "base_url": "http://127.0.0.1:1"}, 201)
                root = "/projects/" + project["id"]
                mysql = MySQLApplicationConfigStore(settings)
                analysis = (await get(root + "/document-impact-analysis-settings"))["payload"]
                analysis.update(model_key=model.key,
                    analysis_guidance="以真实变更语义为准，引用新版本来源位置")
                assert (await client.put(root + "/document-impact-analysis-settings",
                    json=analysis)).status_code == 200
                invocation = (await get(root + "/document-impact-invocation-settings"))["payload"]
                invocation.update(transport_policy="single_attempt")
                assert (await client.put(root + "/document-impact-invocation-settings",
                    json=invocation)).status_code == 200
                assert mysql.get("document.impact_analysis_settings",
                    scope_key=project["id"]).payload["model_key"] == model.key

                filename = f"requirements.{extension}"
                document = await post("/registry/api-docs/upload", {
                    "project_id": project["id"], "filename": filename,
                    "request_id": "formats-v1",
                    "content_base64": base64.b64encode(v1_bytes).decode()})
                v1_version_id = document["current_version_id"]
                base = root + f"/documents/{document['id']}"
                second = await post(f"/registry/api-docs/{document['id']}/versions", {
                    "project_id": project["id"], "filename": filename,
                    "request_id": "formats-v2",
                    "expected_parent_version_id": document["current_version_id"],
                    "content_base64": base64.b64encode(v2_bytes).decode()})
                document = second["document"]
                v2_version_id = document["current_version_id"]
                assert (await objects.read_object_uri(document["storage_uri"]))["content"] == v2_bytes

                views = []
                for number, version_id in enumerate([v1_version_id, v2_version_id], 1):
                    task = await post(base + "/parse-tasks",
                        {"creation_key": f"parse-v{number}", "version_id": version_id}, 202)
                    assert await relay(DOCUMENT_PARSE_STREAM, 1) == 1
                    await run_worker(f"parse-worker-{number}", "src.cli.run_document_parse_worker",
                        f"parse-v{number}")
                    parsed = await get(base + f"/parse-tasks/{task['id']}")
                    assert parsed["status"] == "completed", parsed
                    view = DocumentParseSnapshotView.model_validate(
                        await get(base + f"/parse-snapshots/{parsed['snapshot_id']}"))
                    views.append(view)
                assert views[0].snapshot.id != views[1].snapshot.id
                assert all(view.snapshot.parser.parser_id == PARSER_IDS[extension] for view in views), [
                    view.snapshot.parser for view in views]
                assert all(view.result.blocks for view in views), [view.result.text for view in views]
                save("parse-snapshots.json",
                    {"v1": views[0].model_dump(mode="json"), "v2": views[1].model_dump(mode="json")})

                comparison = await post(base + "/difference-tasks", {
                    "creation_key": "formats-comparison",
                    "old_snapshot_id": views[0].snapshot.id,
                    "new_snapshot_id": views[1].snapshot.id}, 202)
                assert await relay(DOCUMENT_DIFFERENCE_STREAM, 1) == 1
                await run_worker("difference-worker", "src.cli.run_document_difference_worker",
                    "compare")
                diff = await get(base + f"/difference-tasks/{comparison['id']}")
                assert diff["status"] == "completed", diff
                save("difference.json", diff)

                case_payload = {"case_key": "order-total-check", "title": "核对订单总价",
                    "case_type": "json", "mode_key": "api_testing",
                    "content_json": {"objective": "查询订单详情并核对 total 为商品原价合计",
                        "steps": [{"order": 1, "action": "GET /orders/{{id}}，核对 status 与 total"}],
                        "expected": {"status_code": 200, "total": "商品原价合计"}},
                    "execution_notes": "完整读取用例 JSON；total 语义以需求文档为准",
                    "source_refs": [{"source_type": "api_doc", "source_id": document["id"]}],
                    "model_key": "manual", "prompt_version": "manual-v1",
                    "skill_versions": {"manual": "v1"}}
                case = CaseBundle.model_validate(await post(root + "/test-cases", case_payload, 201))

                request = {"creation_key": "formats-impact",
                    "difference_snapshot_id": diff["snapshot_id"],
                    "environment_key": "qa", "target_release": "change-v2"}
                submitted = await post(base + "/impact-tasks", request, 202)
                assert submitted["status"] == "queued"
                assert await relay(DOCUMENT_IMPACT_STREAM, 1) == 1
                await run_worker("impact-worker", "src.cli.run_document_impact_worker", "impact")
                task = await get(base + f"/impact-tasks/{submitted['id']}")
                assert task["status"] == "completed", task
                view = DocumentImpactSnapshotView.model_validate(
                    await get(base + f"/impact-snapshots/{task['snapshot_id']}"))
                result = view.result
                save("impact-result.json", view.model_dump(mode="json"))
                assert result.semantic_status == "pending_review", result.semantic_status
                assert result.deployment_status == "unknown", result.deployment_status
                assert result.units and result.rules, "default model produced no impact rules"
                analyzed = [item for item in result.units if item.status == "analyzed"]
                assert analyzed, result.model_dump(mode="json")
                citations = [citation for item in analyzed for candidate in (item.candidates or [])
                    for impact in candidate.case_impacts for citation in impact.citations]
                assert citations, "no source citations in impact result"
                text = json.dumps([rule.model_dump(mode="json") for rule in result.rules],
                    ensure_ascii=False).lower()
                assert any(token in text for token in ("discount", "折扣", "优惠")), text[:2000]
                facts["impact_analysis"] = {
                    "parser_ids": [value.snapshot.parser.parser_id for value in views],
                    "units": len(result.units), "analyzed": len(analyzed),
                    "rules": len(result.rules), "citations": len(citations),
                    "rule_statements": [rule.model_dump(mode="json") for rule in result.rules],
                    "status": result.status, "model_http": model_sends(),
                    "case_version": case.version.id}
                save("verification.json", facts)
    finally:
        for path in tmp_path.iterdir():
            if path.suffix in {".stdout", ".stderr"}:
                (evidence / path.name).write_bytes(path.read_bytes())
        facts["global_model_unchanged"] = model_invocation_parameters(
            configured_store.get_default_active()) == configured_parameters
        save("verification.json", facts)
