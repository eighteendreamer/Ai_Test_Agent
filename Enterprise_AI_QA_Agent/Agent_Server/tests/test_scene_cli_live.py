"""Actual production CLI process kill at committed scene query checkpoints."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.test_cases.planning_task_service import CasePlanningTaskService
from src.containers import AppContainer
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.redis_task_queue import RedisTaskQueue
from src.registry.models import ModelRegistry
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.runtime.postgres_task_outbox import PostgresTaskOutbox
from src.schemas.case_planning_scenes import SCENE_QUERY_PROTOCOL
from src.schemas.case_planning_task import CasePlanningTaskRequest
from src.schemas.model_config import ModelCapabilitiesOverride
from src.schemas.settings import ModelConfigUpdateRequest
from test_case_planning import active_case, finish, item
from test_case_planning_tasks import delivery
from test_execution_worker_cli import Processes
from test_planning_linked_sources import linked_policy
from test_recording_scene_product import actual_recording_scene_management

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv("RUN_LIVE_SCENE_CLI") != "1",
    reason="owned main/CDP/CLI processes and actual MySQL/PG/Redis/HTTP")]


class SceneProcesses(Processes):
    """The Windows venv launcher owns a child interpreter, kill both."""
    async def kill_tree(self, child):
        if child.poll() is None:
            if os.name == "nt":
                result = await asyncio.to_thread(subprocess.run,
                    ["taskkill","/PID",str(child.pid),"/T","/F"],capture_output=True,check=False,timeout=10)
                (self.directory/f"scene-cli-taskkill-{child.pid}.stdout").write_bytes(result.stdout)
                (self.directory/f"scene-cli-taskkill-{child.pid}.stderr").write_bytes(result.stderr)
                assert result.returncode == 0,(result.stdout+result.stderr).decode(errors="replace")
            else:
                child.kill()
        return await asyncio.to_thread(child.wait,timeout=10)

    async def wait(self, child, timeout=60):
        try:
            return await asyncio.to_thread(child.wait,timeout=timeout)
        except subprocess.TimeoutExpired:
            await self.kill_tree(child)
            raise

    async def stop(self, child):
        await self.kill_tree(child)


@pytest.mark.parametrize("query_stage,fault", [("logic","pending-read"),("logic","read-ack"),
    ("effects","pending-read"),("effects","read-ack")])
async def test_actual_scene_query_cli_kill_and_recover(tmp_path, monkeypatch, query_stage, fault):
    evidence = Path(os.environ["RECORDING_SCENE_EVIDENCE"])/(query_stage+"-"+fault)
    monkeypatch.setenv("RECORDING_SCENE_EVIDENCE", str(evidence))
    state = {"http":[], "provider_errors":[]}
    processes = queue = None

    def save(name, data):
        (evidence/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding="utf-8")

    def decide(context):
        if query_stage == "effects" and len(state["http"]) == 1:
            return finish(item(context,"scene-cli-target",effect_schema=3))
        scene = next((e for e in context["evidence"] if e["kind"] == "scene"),None)
        if scene is not None:
            expected = state["version"]["version"]
            assert scene["version"] == expected["id"] and scene["content_hash"] == expected["content_hash"]
            assert scene["payload"]["content"] == expected["content"]
            assert scene["payload"]["source"] == expected["source"]
            assert scene["payload"]["verification_status"] == "not_executed"
            if context["planning_stage"] == "logic":
                return finish(item(context,"scene-cli-target",effect_schema=3,
                    evidence_ids=[f"case:{state['case'].version.id}",scene["id"]]))
            key = scene["id"]
            if query_stage == "effects":
                key = next(k for k,v in context["evidence_reference_catalog"]["evidence"].items() if v["evidence_id"] == key)
            return {"action":"finish","summary":"固定场景完整读取","bindings":[
                {"item_key":"scene-cli-target","evidence_ids":[key]}]}
        catalogs = [r["scene_catalog"] for r in context["query_results"] if "scene_catalog" in r]
        if not catalogs:
            return {"action":"query","summary":"查询项目场景目录","queries":[{"kind":"scenes"}]}
        found = next(e for e in catalogs[-1]["items"] if e["scene_id"] == state["version"]["scene_id"])
        return {"action":"query","summary":"读取目录指明的固定版本","queries":[{"kind":"scene",
            "source_id":found["scene_id"],"version_id":found["version_id"],"content_hash":found["content_hash"]}]}

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                context = json.loads(body["messages"][-1]["content"])
                observation = {"path":self.path,"body":body,"context":context}
                state["http"].append(observation)
                decision = decide(context)
                response = {"id":"scene-cli-fixture","object":"chat.completion","model":body["model"],
                    "choices":[{"index":0,"message":{"role":"assistant","content":json.dumps(decision)},
                        "finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":10,"total_tokens":20}}
                observation["response"] = response
                encoded = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except Exception as exc:
                state["provider_errors"].append({"error_type":type(exc).__name__,"message":str(exc)})
                self.send_error(500,"Controlled response validation failed")
            finally:
                save("scene-cli-http.json",state["http"])
                save("scene-cli-provider-errors.json",state["provider_errors"])

    provider = ThreadingHTTPServer(("127.0.0.1",0),Provider)
    provider.daemon_threads = True
    serving = threading.Thread(target=provider.serve_forever,daemon=True)
    serving.start()

    async def probe(*, stage, settings, client, evidence, project_ids, version):
        nonlocal processes, queue
        if stage not in {"initial","restart"}:
            return
        jobs = PostgresPlanningTaskStore(settings,lease_seconds=settings.orchestration.task_outbox_lease_seconds)

        def pg():
            with postgres_connect(settings) as conn,conn.cursor() as cur:
                cur.execute(f"SELECT id,status,seed,checkpoint,attempt_no,attempt_id,attempts,worker_id,"
                    f"deadline_at,lease_expires_at,clock_timestamp() AS server_now FROM {jobs._table} WHERE id=%s",
                    (state["task"].id,))
                return dict(cur.fetchone())

        def start(name, interrupt=False):
            processes.env["QA_SCENE_CLI_FAULT"] = fault if interrupt else ""
            return processes.start(name,"tests/scene_cli_probe.py","--once","--worker-id",name)

        def reads():
            path = evidence/"scene-cli-reads.jsonl"
            return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

        try:
            if stage == "initial":
                state["version"] = version
                stream = "isolated:scene-cli:"+uuid4().hex+":case_planning"
                settings.orchestration.redis_task_outbox_streams = stream
                assert settings.orchestration.case_planning_task_stream == stream
                settings.orchestration.redis_task_consumer_group = "scene-cli-owned"
                settings.orchestration.redis_task_reclaim_idle_ms = 1000
                settings.orchestration.redis_task_block_ms = 100
                assert settings.orchestration.task_outbox_lease_seconds == 30
                models = MySQLModelConfigStore(settings)
                models.initialize()
                config = models.upsert(ModelConfigUpdateRequest(model_name="scene-cli-fixture",provider="openai",
                    transport="openai_chat_completions",base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                    api_key="local-fixture-marker",is_active=True,use_provider_defaults=False,
                    capability_overrides=ModelCapabilitiesOverride(streaming=False),max_output_tokens=1024))
                container = AppContainer(); container.settings.override(settings)
                runtime = ModelRuntimeService(ModelRegistry(models),settings)
                container.model_runtime_service.override(runtime)
                container.test_case_embedding_service.override(None)
                planner = container.case_planning_service()
                tasks = CasePlanningTaskService(store=jobs,planning_service=planner,
                    project_service=container.project_service(),dispatch_stream=stream)
                await tasks.initialize()
                project = await container.project_service().get(project_ids[0])
                env = SimpleNamespace(settings=settings,cases=container.test_case_service())
                case = await active_case(env,project,"scene-cli-target",{"objective":"场景计数验证",
                    "steps":[{"action":"按完整场景两次点击","expected":"计数增加"}],
                    "未知嵌套":{"输入":[0,False,"😀"]}},mode="ui_testing")
                state["case"] = case
                assert all(r.source_type != "ui_recording_scene" for r in case.version.source_refs)
                chosen = linked_policy().model_dump(mode="json")
                chosen.pop("linked_source_protocol")
                chosen.update(scene_query_protocol=SCENE_QUERY_PROTOCOL,max_rounds=6,timeout_seconds=600)
                if query_stage == "effects": chosen["evidence_reference_protocol"] = "case-planning-evidence-references-v1"
                response = await client.put(f"/projects/{project.id}/case-plans/policy",json=chosen)
                response.raise_for_status(); save("scene-cli-policy.json",response.json())
                state["task"] = await tasks.submit(project.id,CasePlanningTaskRequest(creation_key=str(uuid4()),
                    objective="查项目场景后规划计数测试",model_key=config.key,candidate_case_ids=[case.case.id]))
                queue = RedisTaskQueue(settings.database.redis_url,stream=stream,group="scene-cli-owned:case_planning")
                await queue.connect()
                assert await PostgresTaskOutbox(settings).relay_once(queue) == 1
                processes = SceneProcesses(evidence,settings)
                processes.env["QA_SCENE_CLI_DIR"] = str(evidence)
                child = start("scene-cli-before-kill",True)
                async with asyncio.timeout(40):
                    while not (evidence/"scene-cli-commit-marker.json").exists():
                        assert child.poll() is None, (evidence/"scene-cli-before-kill.stderr").read_text(encoding="utf-8")
                        await asyncio.sleep(.05)
                marker = json.loads((evidence/"scene-cli-commit-marker.json").read_bytes())
                assert child.poll() is None
                assert marker["pid"] == child.pid or marker["parent_pid"] == child.pid
                first = pg()
                assert first["checkpoint"] == marker["checkpoint"] and first["attempt_no"] == 1
                assert first["checkpoint"]["query_index"] == (0 if fault == "pending-read" else 1)
                assert len(reads()) == (0 if fault == "pending-read" else 1)
                code = await processes.kill_tree(child)
                assert code != 0
                if os.name == "nt":
                    listing = await asyncio.to_thread(subprocess.run,
                        ["tasklist","/FI",f"PID eq {marker['pid']}","/FO","CSV","/NH"],
                        capture_output=True,text=True,check=True,timeout=10)
                    save("scene-cli-worker-absent.json",{"worker_pid":marker["pid"],
                        "stdout":listing.stdout,"stderr":listing.stderr,"exit_code":listing.returncode})
                    assert f'"{marker["pid"]}"' not in listing.stdout
                state["first"] = first
                save("scene-cli-killed-pg.json",first)
                save("scene-cli-kill.json",{"launcher_pid":child.pid,"worker_pid":marker["pid"],
                    "worker_parent_pid":marker["parent_pid"],"exit_code":code,"alive_before_kill":True,
                    "committed_checkpoint_confirmed":True,"pg_lease_seconds":30,"redis_reclaim_idle_ms":1000})
            else:
                response = await client.put(f"/projects/{project_ids[0]}/case-plans/policy",json=linked_policy().model_dump(mode="json"))
                response.raise_for_status(); save("scene-cli-current-policy.json",response.json())
                current = (await client.get(f"/recordings/scenes/projects/{project_ids[0]}/{version['scene_id']}")).json()
                assert current["version"]["version"] == 4
                save("scene-cli-current-scene.json",current)
                async with asyncio.timeout(40):
                    while True:
                        expired = pg()
                        if expired["server_now"] >= expired["lease_expires_at"]:
                            break
                        await asyncio.sleep(.2)
                assert expired["seed"] == state["first"]["seed"]
                assert expired["checkpoint"] == state["first"]["checkpoint"]
                save("scene-cli-natural-expiry-pg.json",expired)
                child = start("scene-cli-after-kill")
                assert await processes.wait(child,timeout=40) == 0, (evidence/"scene-cli-after-kill.stderr").read_text(encoding="utf-8")
                assert "case_planning_processed=1" in (evidence/"scene-cli-after-kill.stdout").read_text(encoding="utf-8")
                final = pg()
                result = await jobs.get(project_ids[0],state["task"].id)
                save("scene-cli-recovered-pg.json",final)
                save("scene-cli-completed.json",result.model_dump(mode="json"))
                assert result.status == "completed",result.message
                assert final["seed"] == state["first"]["seed"] and final["deadline_at"] == state["first"]["deadline_at"]
                assert final["attempt_no"] == 2 and final["attempts"][-1]["reason"] == "lease_expired"
                scene = next(e for e in final["checkpoint"]["plan"]["evidence"] if e["kind"] == "scene")
                assert scene["version"] == version["version"]["id"]
                assert scene["payload"]["content"] == version["version"]["content"]
                assert scene["id"] in final["checkpoint"]["plan"]["decision"]["items"][0]["evidence_ids"]
                assert len(reads()) == 1 and len(state["http"]) == 4 and not state["provider_errors"]
                assert all(r["path"] == "/v1/chat/completions" for r in state["http"])
                await queue.enqueue(delivery(state["task"]).payload)
                replay = start("scene-cli-terminal")
                assert await processes.wait(replay,timeout=40) == 0
                assert "case_planning_processed=1" in (evidence/"scene-cli-terminal.stdout").read_text(encoding="utf-8")
                after = pg()
                assert len(state["http"]) == 4 and len(reads()) == 1
                for key in ("seed","checkpoint","attempt_no","attempt_id","attempts","deadline_at"):
                    assert after[key] == final[key],key
                save("scene-cli-terminal-pg.json",after)
                save("scene-cli-facts.json",{"query_stage":query_stage,"fault":fault,"http_calls":4,
                    "fixed_scene_reads":1,"attempt_count":2,"natural_pg_expiry":True,
                    "terminal_additional_http":0,"terminal_additional_reads":0,"independent_production_cli":True,
                    "controlled_local_http_model":True,"default_ai_invoked":False})
                await processes.close(); processes = None
                await queue.close(); queue = None
        except BaseException:
            if processes: await processes.close(); processes = None
            if queue: await queue.close(); queue = None
            raise

    try:
        await actual_recording_scene_management(tmp_path,scene_probe=probe)
    finally:
        if processes: await processes.close()
        if queue: await queue.close()
        await asyncio.to_thread(provider.shutdown)
        provider.server_close(); serving.join(timeout=2)
