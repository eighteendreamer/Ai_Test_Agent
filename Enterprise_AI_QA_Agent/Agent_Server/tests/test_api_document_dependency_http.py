from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.documents.api_doc_store import InMemoryApiDocStore, PostgresApiDocStore
from src.application.documents.api_docs_service import ApiDocsService
from src.application.projects.project_service import ProjectService
from src.application.projects.project_store import InMemoryProjectStore, PostgresProjectStore
from src.core.config import get_settings
from src.infrastructure.postgres_runtime import postgres_connect
from src.modes.api_testing_mode.campaign_state import ApiTestTask, DocumentCandidate, EndpointCandidate, ExecutionPolicy, InputBinding
from src.modes.api_testing_mode.capability_mapper import CapabilityMapper
from src.modes.api_testing_mode.coordinator import ApiTestCoordinator
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.dependency_planner import DependencyPlanner
from src.modes.api_testing_mode.doc_parser import ApiDocParser
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.precondition_resolver import PreconditionResolver
from src.modes.api_testing_mode.subagent_coordinator import ApiSubagentCoordinator
from src.modes.api_testing_mode.task_pool import ApiTaskPool
from src.schemas.project import ProjectCreateRequest


class _MemoryObjects:
    def __init__(self):
        self.objects = {}

    async def store_uploaded_bytes(self, **kwargs):
        name = f"{kwargs['object_prefix']}/{kwargs['filename']}"
        uri = f"memory://{name}"
        self.objects[uri] = kwargs["content"]
        return {"uri": uri, "bucket": "tests", "object_name": name, "content_type": kwargs["content_type"]}

    async def read_object_uri(self, uri):
        return {"content": self.objects[uri]}

    async def delete_object_uri(self, uri):
        self.objects.pop(uri, None)


@asynccontextmanager
async def _document_storage(tmp_path, live_storage):
    if not live_storage:
        yield (SimpleNamespace(data_dir=str(tmp_path)), InMemoryProjectStore(),
               _MemoryObjects(), InMemoryApiDocStore())
        return

    settings = get_settings().model_copy(deep=True)
    settings.data_dir = str(tmp_path)
    suffix = uuid4().hex[:12]
    tables = (f"up_d_{suffix}", f"up_p_{suffix}")
    settings.database.postgres_api_doc_table, settings.database.postgres_project_table = tables
    # Only this test's fresh UUID-named tables may be created and removed.
    # Never initialize or clean up the application's production catalog.
    with postgres_connect(settings) as connection:
        with connection.cursor() as cursor:
            for table in tables:
                assert re.fullmatch(r"up_[dp]_[0-9a-f]{12}", table)
                cursor.execute("SELECT to_regclass(%s) AS existing", (table,))
                assert cursor.fetchone()["existing"] is None
    objects = ArtifactStorageService(settings)
    try:
        yield settings, PostgresProjectStore(settings), objects, PostgresApiDocStore(settings)
    finally:
        await objects.close()
        with postgres_connect(settings) as connection:
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f'DROP TABLE IF EXISTS "{table}"')


def _plan(endpoints):
    analysis = PreconditionResolver().analyze(endpoints=endpoints)
    graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many(endpoints), preconditions=analysis)
    return graph, analysis


def test_generic_resource_id_keeps_endpoint_and_task_identities_separate():
    graph, analysis = _plan([
        EndpointCandidate(endpoint_id="create-order", method="POST", path="/orders", summary="创建订单"),
        EndpointCandidate(endpoint_id="read-order", method="GET", path="/orders/{id}", summary="订单详情"),
    ])
    create, read = graph.tasks
    assert read.depends_on == [create.task_id]
    assert analysis.requires_manual_data is False
    assert read.path_parameter_sources == {"id": create.task_id}


def test_nested_path_parameters_have_distinct_resource_sources():
    graph, analysis = _plan([
        EndpointCandidate(endpoint_id="order", method="POST", path="/orders"),
        EndpointCandidate(endpoint_id="line", method="POST", path="/orders/{order_id}/lines"),
        EndpointCandidate(endpoint_id="detail", method="GET", path="/orders/{order_id}/lines/{line_id}"),
    ])
    order, line, detail = graph.tasks
    assert detail.path_parameter_sources == {"order_id": order.task_id, "line_id": line.task_id}
    assert set(detail.depends_on) == {order.task_id, line.task_id}
    assert line.path_parameter_sources == {"order_id": order.task_id}
    assert analysis.requires_manual_data is False


def test_ambiguous_resource_producer_is_not_arbitrarily_selected():
    graph, analysis = _plan([
        EndpointCandidate(endpoint_id="first", method="POST", path="/orders"),
        EndpointCandidate(endpoint_id="second", method="POST", path="/orders"),
        EndpointCandidate(endpoint_id="read", method="GET", path="/orders/{id}"),
    ])
    assert analysis.requires_manual_data is True
    assert graph.tasks[-1].path_parameter_sources == {}


@pytest.mark.parametrize("coordinator_type", [ApiTestCoordinator, ApiSubagentCoordinator])
@pytest.mark.parametrize("path", ["/orders/{order_id}/lines/{line_id}", "/orders/:order_id/lines/:line_id"])
def test_each_parameter_binds_only_to_its_planned_producer(coordinator_type, path):
    graph, _ = _plan([
        EndpointCandidate(endpoint_id="order", method="POST", path="/orders"),
        EndpointCandidate(endpoint_id="line", method="POST", path="/orders/{order_id}/lines"),
        EndpointCandidate(endpoint_id="detail", method="GET", path="/orders/{order_id}/lines/{line_id}"),
    ])
    order, line, detail = graph.tasks
    detail.path = path
    detail.full_url = "http://127.0.0.1:12345" + path
    coordinator = object.__new__(coordinator_type)
    coordinator._task_outputs = {order.task_id: {"id": 0}, line.task_id: {"id": 19}}
    coordinator._apply_input_bindings(detail)
    assert detail.path == "/orders/0/lines/19"
    assert detail.full_url == "http://127.0.0.1:12345/orders/0/lines/19"


@pytest.mark.parametrize("coordinator_type", [ApiTestCoordinator, ApiSubagentCoordinator])
def test_explicit_path_binding_takes_precedence_over_inferred_id(coordinator_type):
    task = ApiTestTask(task_id="detail", method="GET", path="/orders/{id}", depends_on=["create"],
                       path_parameter_sources={"id": "create"},
                       input_bindings=[InputBinding(source_task_id="create", source_path="public_id", target_path="path.id")])
    coordinator = object.__new__(coordinator_type)
    coordinator._task_outputs = {"create": {"id": 19, "public_id": "order-visible"}}
    coordinator._apply_input_bindings(task)
    assert task.path == "/orders/order-visible"


def test_old_persisted_task_remains_readable_without_parameter_sources():
    task = ApiTestTask.model_validate({"task_id": "old", "method": "GET", "path": "/orders/{id}", "depends_on": ["create"]})
    assert task.path_parameter_sources == {}
    coordinator = object.__new__(ApiTestCoordinator)
    coordinator._task_outputs = {"create": {"data": {"id": 0}}}
    coordinator._apply_input_bindings(task)
    assert task.path == "/orders/0"


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("live_storage", [False, pytest.param(True, marks=pytest.mark.skipif(
    os.getenv("RUN_LIVE_UPGRADE_STORAGE_TESTS") != "1",
    reason="requires configured PostgreSQL and RustFS; creates isolated test catalogs",
))])
def test_created_document_drives_real_create_then_detail_requests(tmp_path, nested, live_storage):
    requests = []
    order_created = False
    line_created = False

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status, body):
            content = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            nonlocal order_created, line_created
            requests.append(("POST", self.path))
            if self.path == "/orders":
                order_created = True
                self._respond(201, {"id": 0})
            elif self.path == "/orders/0/lines" and order_created:
                line_created = True
                self._respond(201, {"id": 19})
            else:
                self._respond(404, {})

        def do_GET(self):
            requests.append(("GET", self.path))
            valid = (order_created and self.path == "/orders/0"
                     or line_created and self.path == "/orders/0/lines/19")
            self._respond(200 if valid else 404, {"id": 19 if nested else 0})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    document = tmp_path / "orders.md"
    content = ("# 订单接口\n\n### GET /orders/{order_id}/lines/{line_id}\n**功能**: 明细详情\n\n"
               "### POST /orders/{order_id}/lines\n**功能**: 创建明细\n\n### POST /orders\n**功能**: 创建订单\n"
               if nested else "# 订单接口\n\n### GET /orders/{id}\n**功能**: 订单详情\n\n### POST /orders\n**功能**: 创建订单\n")
    document.write_text(content, encoding="utf-8")

    async def scenario():
        async with _document_storage(tmp_path, live_storage) as (settings, project_store, objects, catalog):
            projects = ProjectService(store=project_store)
            await projects.initialize()
            project = await projects.create(ProjectCreateRequest(project_key="dependency-http", name="Dependency HTTP"))
            documents = ApiDocsService(settings=settings, artifact_storage_service=objects, catalog_store=catalog, project_service=projects)
            await documents.initialize()
            record = await documents.upload_document(filename=document.name, content_base64=base64.b64encode(document.read_bytes()).decode(), project_id=project.id, project_url=base_url)
            try:
                if live_storage:
                    restarted_catalog = PostgresApiDocStore(settings)
                    reloaded = await restarted_catalog.get(record.id)
                    assert reloaded.project_id == project.id
                    assert reloaded.storage_uri == record.storage_uri
                indexes = await ApiDocParser(api_docs_service=documents).parse_documents(documents=[DocumentCandidate(doc_id=record.id, title=record.title, project_url=base_url)])
                endpoints = indexes[0].endpoints
                assert len(endpoints) == (3 if nested else 2)
                graph = DependencyPlanner().plan(mapped_endpoints=CapabilityMapper().map_many(endpoints), preconditions=PreconditionResolver().analyze(endpoints=endpoints), base_url=base_url)
                executor = ApiTaskExecutor(credential_manager=CredentialManager())
                tasks = await ApiTestCoordinator(pool=ApiTaskPool(graph.tasks), policy=ExecutionPolicy(max_retries=0), executor_fn=executor.execute).run_all()
                assert all(task.status == "completed" for task in tasks)
                assert all(task.check_results and all(check["passed"] for check in task.check_results) for task in tasks)
                assert (await objects.read_object_uri(record.storage_uri))["content"] == document.read_bytes()
                assert requests == ([("POST", "/orders"), ("POST", "/orders/0/lines"), ("GET", "/orders/0/lines/19")]
                                    if nested else [("POST", "/orders"), ("GET", "/orders/0")])
            finally:
                await documents.delete_document(record.id)
                assert await catalog.get(record.id) is None

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
