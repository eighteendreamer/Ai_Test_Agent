"""Replay real output exhaustion; persist actual SDK responses before recovery."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from test_case_planning import environment, active_case, finish
from test_case_planning_tasks import SubmissionModel, tasks_environment, delivery, expire
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.test_cases.planning_workflow import CasePlanningWorkflow, PlanningCheckpointUnavailable
from src.infrastructure.model_config_store import MySQLModelConfigStore
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.sqlalchemy_runtime import mysql_raw_connection
from src.registry.models import ModelRegistry
from src.runtime.planning_task_handler import PlanningTaskHandler
from src.runtime.postgres_planning_task_store import PostgresPlanningTaskStore
from src.schemas.case_planning import CasePlanningPolicy, CasePlanningRequest
from src.schemas.case_planning_task import PLANNING_RESPONSE_PROTOCOL, PlanningTaskSeed, PlanningTaskCheckpoint, CasePlanningTaskRequest
from src.schemas.model_config import ModelCapabilitiesOverride, ModelInvocationResult
from src.schemas.project import ProjectCreateRequest
from src.schemas.settings import ModelConfigUpdateRequest

ROOT = Path(__file__).resolve().parents[2]


def captured_failure():
    directory = ROOT / 'docs/upgrade_2026-10-03/p4-tool-navigation-default'
    pg = json.loads((directory / 'PG-planning.json').read_bytes())
    request = json.loads((directory / 'physical/e3ff2517e17d48cd9d408d00c9d4890b.request.json').read_bytes())
    body = json.loads(request['messages'][-1]['content'])
    text = body['validation_feedback']['candidate_response']
    metadata = deepcopy(pg['checkpoint']['plan']['rounds'][1]['response_metadata'])
    assert text == '' and metadata['finish_reason'] == 'length'
    assert metadata['usage']['completion_tokens'] == 8192
    return pg, text, metadata


@pytest.mark.asyncio
@pytest.mark.parametrize('variant', ['real-length', 'valid-length', 'empty-stop', 'bad-json', 'legacy'])
async def test_real_response_replay_distinguishes_exhaustion_from_json_repair(tmp_path, variant):
    pg, text, metadata = captured_failure()
    if variant == 'valid-length':
        text = json.dumps(finish())
    elif variant == 'empty-stop':
        text, metadata['finish_reason'] = ' \n ', 'stop'
    elif variant == 'bad-json':
        text, metadata['finish_reason'] = '{invalid:}', 'stop'
    async with environment(tmp_path, model=SubmissionModel(json.dumps(finish()))) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='completion', name='真实响应回放'))
        seed = await env.service.prepare_task(project.id, CasePlanningRequest(objective='按固定用例规划'))
        assert seed.response_protocol == PLANNING_RESPONSE_PROTOCOL
        if variant == 'legacy':
            seed.response_protocol = None
        seed.plan.rounds = [deepcopy(pg['checkpoint']['plan']['rounds'][1])]
        seed.plan.rounds[0]['status'] = 'response_received'
        seed.plan.rounds[0]['response_metadata'] = metadata
        checkpoint = PlanningTaskCheckpoint(plan=seed.plan.model_copy(deep=True), phase='response',
            next_round=2, response_text=text, response_metadata=metadata)
        snapshots = []
        async def save(value, *, message):
            snapshots.append(value.model_copy(deep=True))
        result = await CasePlanningWorkflow(env.service).run(seed, checkpoint,
            deadline_at=datetime.now(timezone.utc)+timedelta(seconds=30), persist=save)
        calls = env.service._models.requests
        if variant in {'legacy', 'bad-json'}:
            assert result.plan.status == 'no_match' and len(calls) == 1
            context = json.loads(calls[0].messages[0]['content'])
            assert context['validation_feedback']['candidate_response'] == text
            assert snapshots[0].plan.rounds[0]['status'] == 'validation_rejected'
        else:
            assert result.plan.status == 'planning_failed' and result.phase == 'finished'
            assert not calls and result.response_text == text and result.response_metadata == metadata
            assert result.plan.rounds[0]['status'] == 'response_rejected'
            assert result.plan.rounds[0]['response_disposition'] == ('empty' if variant == 'empty-stop' else 'truncated')
            assert result.plan.decision is None and result.validation_feedback is None
            assert '不自动重发' in result.plan.summary


def test_actual_legacy_seed_serialization_does_not_add_protocol():
    raw = captured_failure()[0]['seed']
    seed = PlanningTaskSeed.model_validate(raw)
    assert seed.response_protocol is None and seed.model_dump(mode='json') == raw


@pytest.mark.asyncio
async def test_synchronous_rejected_response_is_saved_with_its_actual_text(tmp_path, monkeypatch):
    _, _, metadata = captured_failure()
    text = json.dumps(finish(), ensure_ascii=False)
    calls = []
    model = SubmissionModel()
    async def receive(key, request, **kwargs):
        calls.append(request)
        return ModelInvocationResult(text=text, response_summary=metadata)
    monkeypatch.setattr(model, 'invoke', receive)
    async with environment(tmp_path, model=model) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='sync-completion', name='同步响应保存'))
        target = await active_case(env, project, 'full', {'objective':'读取全部八步',
            'steps':[{'order':i,'action':'完整步骤'} for i in range(8)], 'typed':[False,0,None]})
        plan = await env.service.create(project.id, CasePlanningRequest(objective='验证完整用例',candidate_case_ids=[target.case.id]))
        assert plan.status == 'planning_failed' and len(calls) == 1
        saved = await env.service.get(project.id, plan.id)
        assert saved.rounds[-1]['response_text'] == text
        assert saved.rounds[-1]['response_metadata'] == metadata


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_CASE_PLANNING_TASKS') != '1', reason='actual MySQL/PG/RustFS and HTTP/SDK')
@pytest.mark.parametrize('streaming', [False, True])
async def test_actual_http_response_ack_loss_recovers_without_third_send(tmp_path, monkeypatch, streaming):
    _, failed_text, failed_metadata = captured_failure()
    requests, responses = [], []
    async with tasks_environment(tmp_path) as env:
        project = await env.projects.create(ProjectCreateRequest(project_key='http-completion', name='输出完整性实测'))
        source = tmp_path/'completion-requirements.md'
        source.write_text('# 查询要求\n按完整八步用例测试；保留False、0、null。\n', encoding='utf-8')
        doc = await env.docs.upload_document(project_id=project.id, filename=source.name,
            content_base64=base64.b64encode(source.read_bytes()).decode())
        assert (await env.objects.read_object_uri(doc.storage_uri))['content'] == source.read_bytes()
        full = {'objective': '八步完整用例', 'steps': [{'order': i, 'action': '完整步骤'} for i in range(8)], 'typed': [False, 0, None]}
        target = await active_case(env, project, 'complete', full)

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                context = json.loads(body['messages'][-1]['content'])
                assert context['evidence'][0]['payload']['content'] == full
                if len(requests) == 1:
                    content = json.dumps({'action': 'query', 'summary': '读取项目实际原件',
                        'queries': [{'kind': 'document', 'source_id': doc.id}]}, ensure_ascii=False)
                    reason, usage = 'stop', {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}
                else:
                    assert len(requests) == 2
                    assert next(e for e in context['evidence'] if e['kind'] == 'document')['payload']['content'] == source.read_text(encoding='utf-8').rstrip('\n')
                    content, reason, usage = failed_text, failed_metadata['finish_reason'], failed_metadata['usage']
                response = {'id': 'completion-fixture', 'model': body['model'], 'usage': usage,
                    'choices': [{'index': 0, 'finish_reason': reason}]}
                if body.get('stream'):
                    response.update(object='chat.completion.chunk', created=1)
                    response['choices'][0]['delta'] = {'role': 'assistant', 'content': content}
                    raw = ('data: '+json.dumps(response, ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode()
                else:
                    response.update(object='chat.completion', created=1)
                    response['choices'][0]['message'] = {'role': 'assistant', 'content': content}
                    raw = json.dumps(response, ensure_ascii=False).encode()
                responses.append(raw)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream' if body.get('stream') else 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        serving = threading.Thread(target=server.serve_forever, daemon=True); serving.start()
        table = 'up_completion_'+uuid4().hex[:12]
        env.settings.database.llm_model_table = table
        with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
            cur.execute('SHOW TABLES LIKE %s', (table,)); assert cur.fetchone() is None
        models = MySQLModelConfigStore(env.settings); models.initialize()
        models.upsert(ModelConfigUpdateRequest(model_name='completion-fixture', provider='openai',
            transport='openai_chat_completions', base_url=f'http://127.0.0.1:{server.server_port}/v1',
            api_key='local-fixture-marker', is_active=True, use_provider_defaults=False,
            capability_overrides=ModelCapabilitiesOverride(streaming=streaming), max_output_tokens=8192))
        env.service._models = ModelRuntimeService(ModelRegistry(models), env.settings)
        env.settings.model.langchain_model_adapter_enabled = False
        original = env.jobs.save_checkpoint
        try:
            await env.service.save_policy(project.id, CasePlanningPolicy(timeout_seconds=180))
            task = await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key='completion',
                objective='按项目完整用例执行', candidate_case_ids=[target.case.id]))
            async def lose_response_ack(claim, checkpoint, *, message):
                saved = await original(claim, checkpoint, message=message)
                if checkpoint.phase == 'response' and checkpoint.next_round == 2:
                    raise RuntimeError('actual PG response committed; acknowledgement lost')
                return saved
            monkeypatch.setattr(env.jobs, 'save_checkpoint', lose_response_ack)
            with pytest.raises(PlanningCheckpointUnavailable):
                await PlanningTaskHandler(env.service, env.jobs, worker_id='before-ack-loss')(delivery(task))
            before = await env.jobs.get(project.id, task.id)
            assert before.phase == 'response' and before.round_count == 2 and len(requests) == 2
            async def forbidden(*args, **kwargs):
                raise AssertionError('saved response must not invoke a model again')
            monkeypatch.setattr(env.service._models, 'invoke', forbidden)
            await env.service.save_policy(project.id, CasePlanningPolicy(max_rounds=1))
            monkeypatch.setattr(env.jobs, 'save_checkpoint', original)
            expire(env, task)
            store = PostgresPlanningTaskStore(env.settings)
            await PlanningTaskHandler(env.service, store, worker_id='after-ack-loss')(delivery(task))
            terminal = await store.get(project.id, task.id)
            assert terminal.status == 'failed' and terminal.round_count == 2
            assert terminal.deadline_at == before.deadline_at and terminal.attempt_no == 2
            plan = await env.service.get(project.id, terminal.plan_id)
            assert plan.status == 'planning_failed' and plan.decision is None
            assert plan.rounds[-1]['response_disposition'] == 'truncated'
            await PlanningTaskHandler(env.service, store, worker_id='terminal-redelivery')(delivery(task))
            assert await env.tasks.submit(project.id, CasePlanningTaskRequest(creation_key='completion',
                objective='按项目完整用例执行', candidate_case_ids=[target.case.id])) == terminal
            assert len(requests) == 2
            with postgres_connect(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f'SELECT seed,checkpoint,status FROM {env.jobs._table} WHERE id=%s', (task.id,))
                persisted = dict(cur.fetchone())
            assert persisted['seed']['response_protocol'] == PLANNING_RESPONSE_PROTOCOL
            assert persisted['checkpoint']['response_text'] == failed_text
            assert persisted['checkpoint']['response_metadata']['usage'] == failed_metadata['usage']
            assert persisted['seed']['plan']['config_snapshot']['policy']['max_rounds'] == 5
            directory = Path(os.environ['QA_PLANNING_COMPLETION_DIR'])/('stream' if streaming else 'native')
            directory.mkdir(parents=True, exist_ok=False)
            (directory/source.name).write_bytes(source.read_bytes())
            for i, (request, response) in enumerate(zip(requests, responses), 1):
                (directory/f'physical-{i}.request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
                (directory/f'physical-{i}.response.bin').write_bytes(response)
            (directory/'verification.json').write_text(json.dumps({'actual_PG':persisted,
                'actual_plan':plan.model_dump(mode='json'), 'current_MySQL_policy':(await env.service.get_policy(project.id)).model_dump(mode='json'),
                'physical_http_sends':len(requests),'response_committed_ack_lost':True,'recovery_no_new_model_call':True,
                'original_deadline_preserved':True,'terminal_no_new_calls':True,'source_bytes_read_back':True,
                'streaming':streaming,'controlled_provider':True,'usage_replayed_from_failure':True,
                'default_autonomous_Agent_verified':False,'full_P0_P5_complete':False},ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            server.shutdown(); server.server_close(); serving.join(5)
            assert not serving.is_alive()
            with mysql_raw_connection(env.settings) as conn, conn.cursor() as cur:
                cur.execute(f'DROP TABLE `{table}`')
