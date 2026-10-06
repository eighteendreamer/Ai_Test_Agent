"""Real HTTP origins; a matching body may come from a different target."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.credential_manager import CredentialManager
from src.modes.api_testing_mode.executor import ApiTaskExecutor
from src.modes.api_testing_mode.campaign_state import ApiTestCampaign, ApiTestingState
from src.modes.api_testing_mode.http_observation import http_target, target_in_scope
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.modes.api_testing_mode.report_builder import ReportBuilder

KEY = "http_observation_v1"


@contextmanager
def fixed_target(days):
    calls, redirects = [], {}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self.reply()
        def do_POST(self): self.reply()
        def reply(self):
            path = urlsplit(self.path).path
            calls.append({"method": self.command, "path": path})
            if path in redirects:
                self.send_response(303)
                self.send_header("Location", redirects[path])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            raw = json.dumps({"days": days, "release": "same-declared-release", "expired_count": 0,
                              "expired": False, "deleted_at": None}).encode()
            self.send_response(503 if path == "/error" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, redirects
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        assert not thread.is_alive()


def record(name, **value):
    directory = os.getenv("QA_HTTP_ORIGIN_EVIDENCE_DIR")
    if directory:
        path = Path(directory) / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_actual_cross_target_redirect_requires_response_origin():
    with fixed_target(7) as (old, old_calls, redirects), fixed_target(9) as (new, new_calls, _):
        redirects["/redirect"] = new + "/rule"
        task = ApiTestTask(task_id="cross-target", method="POST", path=old + "/redirect")
        result = await ApiTaskExecutor(credential_manager=CredentialManager()).execute(task)
        record("cross-target", task=result.model_dump(mode="json"), old=old, new=new,
               old_requests=old_calls, new_requests=new_calls)
        assert result.response_body["days"] == 9
        observation = result.execution_checkpoint.get(KEY)
        assert observation is not None, "Actual response origin is absent despite cross-target redirect"
        assert [hop["target"]["port"] for hop in observation["hops"]] == [urlsplit(old).port, urlsplit(new).port]
        assert [hop["method"] for hop in observation["hops"]] == ["POST", "GET"]
        assert observation["acquisition"] == "network"


@pytest.mark.asyncio
async def test_actual_targets_share_release_but_keep_independent_rules():
    with fixed_target(7) as (old, old_calls, _), fixed_target(9) as (new, new_calls, _):
        executor = ApiTaskExecutor(credential_manager=CredentialManager())
        results = [await executor.execute(ApiTestTask(task_id=f"target-{index}", method="GET", path=url + "/rule?days=999"))
                   for index, url in enumerate([old, new])]
        record("fixed-rules", tasks=[task.model_dump(mode="json") for task in results],
               old_requests=old_calls, new_requests=new_calls)
        assert [task.response_body["days"] for task in results] == [7, 9]
        assert len({task.response_body["release"] for task in results}) == 1
        assert all(task.execution_checkpoint.get(KEY) for task in results)


@pytest.mark.asyncio
async def test_completed_request_restores_origin_as_checkpoint_without_new_http():
    with fixed_target(9) as (url, calls, _):
        task = await ApiTaskExecutor(credential_manager=CredentialManager()).execute(
            ApiTestTask(task_id="completed-original", method="GET", path=url + "/rule"))
        campaign = ApiTestCampaign(campaign_id="actual-campaign", tasks=[task])
        state, context = ApiTestingState(campaign=campaign), SimpleNamespace(context_bundle={})
        runtime = ApiTestingModeRuntime(api_docs_service=None)
        runtime._checkpoint_execution_state(state=state, context=context, event_type="task_completed", task=task)
        checkpoint = deepcopy(state.execution_checkpoint)
        assert checkpoint["completed_request"][KEY] == task.execution_checkpoint[KEY]
        restored = await ApiTaskExecutor(credential_manager=CredentialManager()).execute(
            ApiTestTask(task_id=task.task_id, method="GET", path=task.path, execution_checkpoint=checkpoint))
        assert len(calls) == 1 and restored.execution_checkpoint[KEY]["acquisition"] == "completed_request"
        assert checkpoint["completed_request"][KEY]["acquisition"] == "network"
        report = ReportBuilder().build(campaign=ApiTestCampaign(campaign_id="restored", tasks=[restored]))
        assert report.tasks[0][KEY] == restored.execution_checkpoint[KEY]
        record("checkpoint", original=task.model_dump(mode="json"), completed_request=checkpoint,
               restored=restored.model_dump(mode="json"), report=report.model_dump(mode="json"), requests=calls)


@pytest.mark.asyncio
async def test_new_failed_attempt_clears_previous_response_and_forged_origin():
    with fixed_target(9) as (url, calls, _):
        executor = ApiTaskExecutor(credential_manager=CredentialManager())
        task = await executor.execute(ApiTestTask(task_id="retry", method="GET", path=url + "/rule"))
        first = task.model_dump(mode="json")
        task.path, task.full_url = "http://127.0.0.1:1/unreachable", ""
        task.execution_checkpoint[KEY]["hops"][0]["target"]["host"] = "forged.invalid"
        failed = await executor.execute(task)
        assert failed.status == "failed" and failed.response_status is failed.response_body is None
        assert KEY not in failed.execution_checkpoint and failed.response_headers == {} and failed.check_results == []
        assert len(calls) == 1
        record("failed-retry", first=first, final=failed.model_dump(mode="json"), requests=calls)


@pytest.mark.asyncio
async def test_actual_roundtrip_hops_error_response_and_query_omission():
    with fixed_target(7) as (old, old_calls, redirects), fixed_target(9) as (new, new_calls, back):
        redirects["/roundtrip"] = new + "/back?nonce=omitted-value"
        back["/back"] = old + "/rule"
        executor = ApiTaskExecutor(credential_manager=CredentialManager())
        roundtrip = await executor.execute(ApiTestTask(task_id="roundtrip", method="GET", path=old + "/roundtrip"))
        origin = roundtrip.execution_checkpoint[KEY]
        assert [hop["target"]["port"] for hop in origin["hops"]] == [urlsplit(old).port, urlsplit(new).port, urlsplit(old).port]
        assert "omitted-value" not in json.dumps(origin) and origin["hops"][1]["target"]["query_omitted"]
        error = await executor.execute(ApiTestTask(task_id="http-error", method="GET", path=old + "/error"))
        assert error.status == "failed" and error.execution_checkpoint[KEY]["hops"][0]["status_code"] == 503
        assert ReportBuilder()._build_task_evidence(error)[KEY] == error.execution_checkpoint[KEY]
        assert not target_in_scope(http_target(old + "/application"), old + "/app")
        record("roundtrip-error", roundtrip=roundtrip.model_dump(mode="json"), error=error.model_dump(mode="json"),
               old_requests=old_calls, new_requests=new_calls)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_HTTP_ORIGINS") != "1", reason="actual isolated MySQL/PostgreSQL/RustFS and parser processes")
@pytest.mark.parametrize("extension", ["md", "docx", "pdf"])
async def test_actual_fixed_environment_tool_output_inspection_and_archive(tmp_path, monkeypatch, extension):
    import base64
    import hashlib
    import shutil
    from src.application.runtime.tool_job_service import ToolJobService
    from src.application.runtime.tool_runtime_service import ToolRuntimeService, ToolExecutionContext
    from src.infrastructure.postgres_runtime import postgres_connect
    from src.registry.tools import ToolRegistry
    from src.schemas.document_parsing import DocumentParseTaskRequest
    from src.schemas.tool_runtime import ModelToolCall
    from test_business_environment import actual_environment, capture_request
    from test_document_differences import write_pair
    with fixed_target(7) as (old, old_calls, redirects), fixed_target(9) as (new, new_calls, _):
        async with actual_environment(tmp_path, include_tool_jobs=True) as values:
            env, parsing, parse_store, store, service, client, project, other = values
            paths = write_pair(tmp_path, extension)
            document = await env.docs.upload_document(filename=paths[0].name, project_id=project.id,
                request_id="origin-doc", content_base64=base64.b64encode(paths[0].read_bytes()).decode())
            task = await parsing.submit(project.id, document.id, DocumentParseTaskRequest(creation_key="origin-parser", version_id=document.current_version_id))
            parsed = await parsing.execute(await parse_store.claim(project.id, task.id, worker_id="origin-parser"))
            assert parsed.status == "completed"
            snapshots, roots = [], []
            root = f"/projects/{project.id}/business-environments"
            for key, address in [("old", old), ("new", new)]:
                registered = (await client.post(root, json={"request_id": "register-" + key, "environment_key": key})).json()
                selected = root + "/" + registered["id"]
                configured = await client.put(selected + "/configuration", json={"display_name": key, "base_url": address + "/app"})
                assert configured.status_code == 200, configured.text
                captured = await client.post(selected + "/snapshots", json=capture_request(configured.json(), "capture-" + key))
                assert captured.status_code == 201, captured.text
                snapshots.append(captured.json()); roots.append(selected)
            redirects["/app/cross"] = new + "/app/rule"
            jobs = ToolJobService(service._tool_jobs)
            runtime = ToolRuntimeService(tool_job_service=jobs)
            context = ToolExecutionContext(session_id=str(uuid4()), turn_id=str(uuid4()), trace_id=str(uuid4()),
                user_message="核对实际来源", normalized_input="核对实际来源", context_bundle={"project_id": project.id})
            tool = ToolRegistry().get("api-test-runner")
            records = []
            for key, url in [("old", old + "/app/rule"), ("new", new + "/app/rule"), ("cross", old + "/app/cross")]:
                records.append(await runtime.execute(tool, ModelToolCall(id=key, name=tool.key, arguments={"worker_action": "execute_task",
                    "task": {"task_id": key, "method": "GET", "path": url, "timeout_seconds": 5}}), context))
            route = roots[0] + f"/snapshots/{snapshots[0]['id']}/tool-observations/inspect"
            async def inspect(job_id, field="days", selected_route=route):
                return await client.post(selected_route, json={"tool_job_id": job_id,
                    "output_path_tokens": ["task_result", "response_body", field]})
            observed = [(await inspect(r.job_id)).json() for r in records]
            assert [v["value"] for v in observed] == [7, 9, 9]
            assert [v["target_status"] for v in observed] == ["matched", "mismatch", "mismatch"]
            assert all(v["deployment_status"] == "unknown" for v in observed)
            new_route = roots[1] + f"/snapshots/{snapshots[1]['id']}/tool-observations/inspect"
            assert (await inspect(records[1].job_id, selected_route=new_route)).json()["target_status"] == "matched"
            literal_values = [(await inspect(records[0].job_id, field)).json() for field in ["expired_count", "expired", "deleted_at"]]
            assert [v["value"] for v in literal_values] == [0, False, None]
            assert len({v["value_sha256"] for v in literal_values}) == 3
            foreign_context = ToolExecutionContext(session_id=str(uuid4()), turn_id=str(uuid4()), trace_id=str(uuid4()),
                user_message="另一个项目", normalized_input="另一个项目", context_bundle={"project_id": other.id})
            foreign = await runtime.execute(tool, ModelToolCall(id="foreign", name=tool.key,
                arguments={"worker_action": "execute_task", "task": {"task_id": "foreign", "method": "GET", "path": old + "/app/rule"}}), foreign_context)
            assert (await inspect(foreign.job_id)).status_code == 404
            assert (await inspect(records[0].job_id, "missing")).status_code == 409
            first = records[0].output["task_result"]
            completed = {k: deepcopy(first[k]) for k in ["status", "response_status", "response_headers", "response_body", "check_results", "duration_ms", "completed_at"]}
            completed.update(idempotency_key=records[0].output["idempotency_key"], **{KEY: first["execution_checkpoint"][KEY]})
            replay = await runtime.execute(tool, ModelToolCall(id="replay", name=tool.key, arguments={"worker_action": "execute_task",
                "task": {"task_id": first["task_id"], "method": "GET", "path": first["path"], "execution_checkpoint": {"completed_request": completed}}}), context)
            replay_observed = (await inspect(replay.job_id)).json()
            assert replay_observed["target_status"] == "unknown" and "completed_request_is_not_new_network_evidence" in replay_observed["limitations"]
            legacy_completed = {k: v for k, v in completed.items() if k != KEY}
            legacy = await runtime.execute(tool, ModelToolCall(id="legacy-replay", name=tool.key, arguments={"worker_action": "execute_task",
                "task": {"task_id": first["task_id"], "method": "GET", "path": first["path"],
                    "execution_checkpoint": {"completed_request": legacy_completed, KEY: first["execution_checkpoint"][KEY]}}}), context)
            legacy_observed = (await inspect(legacy.job_id)).json()
            assert legacy_observed["target_status"] == "unknown" and legacy_observed["http_observation"] is None
            injected = await client.post(route, json={"tool_job_id": records[0].job_id,
                "output_path_tokens": ["task_result", "response_body", "days"], "target_status": "matched"})
            assert injected.status_code == 422
            original_job = await jobs.get_job(records[0].job_id)
            altered = original_job.model_copy(deep=True)
            altered.output_payload["task_result"]["execution_checkpoint"][KEY]["hops"][0]["target"]["port"] = urlsplit(new).port
            await service._tool_jobs.save_job(altered)
            tampered_observed = (await inspect(records[0].job_id)).json()
            assert tampered_observed["target_status"] == "unknown" and "returned_output_checkpoint_missing_or_different" in tampered_observed["limitations"]
            await service._tool_jobs.save_job(original_job)
            requests_before = len(old_calls) + len(new_calls)
            await client.put(roots[0] + "/configuration", json={"display_name": "改址", "base_url": new + "/app"})
            await client.post(roots[0] + "/archive")
            await env.projects.archive(project.id)
            def current_read_forbidden(*args, **kwargs): raise AssertionError("历史核对不得读取当前MySQL配置")
            monkeypatch.setattr(env.configs, "get", current_read_forbidden)
            assert (await inspect(records[0].job_id)).json() == observed[0]
            assert len(old_calls) + len(new_calls) == requests_before == 5
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT id,output_payload,metadata FROM {env.settings.database.postgres_tool_job_table} ORDER BY created_at")
                pg_jobs = [dict(row) for row in cur.fetchall()]
            assert len(pg_jobs) == 6
            record("live-" + extension, document=document.model_dump(mode="json"), parsed_task=parsed.model_dump(mode="json"),
                snapshots=snapshots, observed=observed, literal_values=literal_values, replay_observed=replay_observed,
                legacy_observed=legacy_observed, tampered_observed=tampered_observed,
                pg_jobs=pg_jobs, requests_old=old_calls, requests_new=new_calls, archive_history_preserved=True,
                current_config_read=False, inspection_new_http_calls=0, deployment_status="unknown", source_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest())
            evidence_dir = os.getenv("QA_HTTP_ORIGIN_EVIDENCE_DIR")
            if evidence_dir:
                for path in paths:
                    shutil.copyfile(path, Path(evidence_dir) / (extension + "-" + path.name))
