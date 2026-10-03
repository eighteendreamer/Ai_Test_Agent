from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_case_plan_run_creation import LIVE, planned
from test_case_planning import SequenceModel, active_case, finish, item
from test_prompt_history_http import _state, model_http_server
from test_run_item_dependencies import finish as complete
from test_runtime_interrupt_tool_calls import _ModelRuntimeStub, _session
from src.application.prompting.prompt_assembly_service import PromptAssemblyService
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.runtime.agent_loop import AgentLoop
from src.application.runtime.runtime_service import RuntimeService
from src.registry.tools import ToolRegistry
from src.registry.agents import AgentRegistry
from src.graph.nodes.model_invoker import build_model_invoker_node
from src.graph.nodes.prompt_assembler import build_prompt_assembler_node
from src.runtime.control import RuntimeControlRegistry
from src.schemas.case_planning import CasePlanningRequest, CasePlanRunRequest
from src.schemas.model_config import ModelConfigRecord


def context(detail, record, version):
    from src.application.test_runs.agent_case_context import build_agent_case_context
    return build_agent_case_context(detail, record, version)


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
async def test_inputs_reference_actual_results_and_local_preparation_outputs(tmp_path, live):
    async with planned(tmp_path, live) as (env, store, suites, service, plan, now, table):
        detail = await service.create(plan.project_id, plan.id, CasePlanRunRequest(creation_key='context', mode_key='api_testing'))
        first = await store.claim_items(run_id=detail.run.id, worker_id='producer', limit=10, lease_seconds=60, now=now)
        for claim in first:
            await complete(store, claim, now, {'case_output': {'body': {'order.id': False}}})
        second = await store.claim_items(run_id=detail.run.id, worker_id='consumer', limit=10, lease_seconds=60, now=now)
        left, _ = second[0]
        detail = await store.get_run(detail.run.id)
        actual = context(detail, left, await env.cases.get_version(left.case_version_id))
        assert [step.item.key for step in actual.steps] == ['seed', 'left']
        seed, target = actual.steps
        assert seed.phase == 'before' and target.phase == 'case'
        assert seed.inputs['id'].resolved and seed.inputs['id'].value is False
        assert seed.inputs['id'].actual_path == ['case_output', 'body', 'order.id']
        assert seed.inputs['id'].result_id in {r.id for r in detail.results}
        assert not target.inputs['id'].resolved
        assert target.inputs['id'].binding.source_item == 'seed'
        assert target.content_json == (await env.cases.get_version(left.case_version_id)).business_content()
        assert 'DO-NOT-INJECT-TEMPLATE' not in actual.model_dump_json()
        await complete(store, second[0], now, {'case_output': {'assertion': 'failed'},
            'preparations': {'seed': {'status': 'passed', 'output': {'id': 0}}}}, status='failed')
        third = await store.claim_items(run_id=detail.run.id, worker_id='shared', limit=10, lease_seconds=60, now=now)
        right, _ = third[0]
        detail = await store.get_run(detail.run.id)
        shared = context(detail, right, await env.cases.get_version(right.case_version_id))
        assert [s.item.key for s in shared.steps] == ['right', 'cleanup']
        assert shared.steps[-1].phase == 'after'
        assert shared.steps[0].inputs['id'].value == 0
        assert shared.steps[-1].inputs['id'].actual_path == ['preparations', 'seed', 'output', 'id']
        assert shared.steps[0].inputs['id'].run_item_id == left.id
        foreign = detail.model_copy(deep=True)
        next(r for r in foreign.results if r.run_item_id == left.id).run_id = str(uuid4())
        with pytest.raises(ValueError, match='实际来源'):
            context(foreign, right, await env.cases.get_version(right.case_version_id))
        corrupt = detail.model_copy(deep=True)
        next(r for r in corrupt.results if r.run_item_id == left.id).actual['preparations']['seed']['status'] = 'failed'
        with pytest.raises(ValueError, match='依赖'):
            context(corrupt, right, await env.cases.get_version(right.case_version_id))
        with pytest.raises(ValueError, match='引用'):
            context(detail, right.model_copy(update={'case_version_id': left.case_version_id}),
                await env.cases.get_version(right.case_version_id))


@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, LIVE])
@pytest.mark.parametrize('langchain_enabled', [False, True])
async def test_actual_json_file_and_original_produce_full_frozen_prompt(tmp_path, live, langchain_enabled,
    model_http_server, monkeypatch):
    async with planned(tmp_path, live) as (env, store, suites, service, old_plan, now, table):
        project = await env.projects.get(old_plan.project_id)
        payload = {'objective': '读取完整 JSON 验证负向条件',
            'steps': [{'order': i, 'action': '验证步骤'} for i in range(1, 8)] +
                [{'order': 8, 'action': 'LAST-STEP-IS-ESSENTIAL😀', 'expected': '未登录必须拒绝'}],
            'custom': {'literal.key': [0, False, None, {'': '保留未知字段'}]}}
        file = tmp_path / 'full-case.json'
        file.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        original = await env.docs.upload_document(project_id=project.id, filename=file.name,
            content_base64=base64.b64encode(file.read_bytes()).decode(), source='case-context-acceptance')
        stored = await env.objects.read_object_uri(original.storage_uri)
        assert stored['content'] == file.read_bytes()
        version = await active_case(env, project, 'full-context', json.loads(stored['content']))
        env.service._models = SequenceModel(lambda c: finish(item(c, 'full-context')))
        plan = await env.service.create(project.id, CasePlanningRequest(objective='核对完整负向用例'))
        detail = await service.create(project.id, plan.id, CasePlanRunRequest(creation_key='full', mode_key='ui_automation'))
        record = detail.items[0]
        basis = context(detail, record, await env.cases.get_version(record.case_version_id))
        assert basis.steps[0].content_json == payload
        state = _state([])
        state['context_bundle'] = basis.runtime_context()
        state['test_case_prompt_blocks'] = ['DO-NOT-REPLACE-FROZEN-CASE']
        assembly = PromptAssemblyService().build_for_turn(state, [])
        sections = {s.key: s for s in assembly.system_sections}
        assert 'frozen_case_execution' in sections
        assert sections['frozen_case_execution'].metadata['trusted'] is False
        assert 'LAST-STEP-IS-ESSENTIAL😀' in assembly.system_prompt
        assert '未登录必须拒绝' in assembly.system_prompt
        assert 'literal.key' in assembly.system_prompt
        assert '读取全部步骤' in assembly.system_prompt
        assert 'DO-NOT-INJECT-TEMPLATE' not in assembly.system_prompt
        assert 'DO-NOT-REPLACE-FROZEN-CASE' not in assembly.system_prompt
        assert 'active_test_cases' not in sections
        assert version.version.id == record.case_version_id
        wrong = deepcopy(state)
        wrong['context_bundle']['project_id'] = 'foreign-project'
        with pytest.raises(ValueError, match='身份'):
            PromptAssemblyService().build_for_turn(wrong, [])
        monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
        monkeypatch.setenv('LANGSMITH_TRACING', 'false')
        url, requests = model_http_server
        config = ModelConfigRecord(key='local-model', name='Local', provider='openai',
            transport='openai_chat_completions', model_id='local-model', api_base_url=url, api_key=uuid4().hex)
        runtime = ModelRuntimeService(model_registry=SimpleNamespace(get_runtime_config=lambda key: config),
            settings=SimpleNamespace(model=SimpleNamespace(llm_request_timeout_seconds=10,
                langchain_model_adapter_enabled=langchain_enabled)))
        assemble = build_prompt_assembler_node(PromptAssemblyService(), AgentRegistry())
        invoke = build_model_invoker_node(runtime, ToolRegistry())
        for iteration in range(3):
            state['loop_iteration'] = iteration
            assemble(state)
            await invoke(state)
            assert state['model_response_summary']['mode'] == 'ok'
        assert len(requests) == 3
        for request in requests:
            system = '\n'.join(m['content'] for m in request['messages'] if m['role'] == 'system')
            assert system.count('LAST-STEP-IS-ESSENTIAL😀') == 1
            assert '未登录必须拒绝' in system and 'literal.key' in system
            assert 'DO-NOT-INJECT-TEMPLATE' not in system
            assert 'DO-NOT-REPLACE-FROZEN-CASE' not in system
        # Actual TCP plus the production SDK/adapter, with a controlled local
        # model response. This does not verify external model inference/tools.


def test_explicit_free_testing_does_not_inject_active_case_summaries():
    state = _state([])
    state['context_bundle'] = {'execution_basis': 'free'}
    state['test_case_prompt_blocks'] = ['DO-NOT-INJECT-FREE-CASE']
    result = PromptAssemblyService().build_for_turn(state, [])
    assert 'DO-NOT-INJECT-FREE-CASE' not in result.system_prompt
    assert 'active_test_cases' not in [s.key for s in result.system_sections]


@pytest.mark.asyncio
async def test_loop_interrupt_preserves_context_for_existing_resume_fallback():
    original = {'execution_basis': 'cases', 'case_execution': {'run_item_id': 'owned', 'tail': 'TAIL'},
        'execution_checkpoint': {'payload': {'side_effect_status': 'unknown'}}}
    state = _state([])
    state.update(context_bundle=deepcopy(original), termination_reason='', continue_loop=False)
    loop = AgentLoop(graph=None, runtime_control=RuntimeControlRegistry())
    pending = loop._build_pending_turn(state)
    assert pending['context_bundle'] == original
    state['context_bundle']['case_execution']['tail'] = 'mutated'
    assert pending['context_bundle']['case_execution']['tail'] == 'TAIL'
    session = _session('persist-context')
    runtime = RuntimeService(graph=None, model_runtime_service=_ModelRuntimeStub(), tool_runtime_service=None,
        tool_registry=ToolRegistry(), runtime_control=RuntimeControlRegistry())
    restored = runtime._state_from_pending_turn(session, pending)
    assert restored['context_bundle']['case_execution']['tail'] == 'TAIL'
    _, turn, config = loop._decompose_graph_state(restored)
    recomposed = loop._compose_graph_state(loop._decompose_graph_state(restored)[0], turn, config)
    assert recomposed['context_bundle']['case_execution']['tail'] == 'TAIL'
    snapshot = runtime._build_snapshot(session, recomposed, 1)
    assert runtime._state_from_snapshot(session, snapshot)['context_bundle']['execution_checkpoint'] == original['execution_checkpoint']


@pytest.mark.asyncio
async def test_explicit_basis_skips_live_case_retrieval_but_preserves_old_request_behavior():
    from unittest.mock import AsyncMock
    from src.application.skills.skill_runtime_service import SkillRuntimeService
    from src.graph.nodes.router import build_router_node
    from src.registry.agents import AgentRegistry
    from src.registry.skills import SkillRegistry
    from src.schemas.agent import ModelDescriptor
    class Models:
        def resolve_for_agent(self, **kwargs): return ModelDescriptor(key='test-model', name='Test', provider='fake', summary='Test')
    mcp = SimpleNamespace(list_active_servers=lambda: [], build_prompt_blocks=lambda _: [])
    cases = SimpleNamespace(retrieve=AsyncMock(return_value=SimpleNamespace(hits=[], prompt_blocks=['old references'])))
    skills = SkillRegistry()
    router = build_router_node(AgentRegistry(), ToolRegistry(), Models(), skills, SkillRuntimeService(skills), mcp,
        test_case_embedding_service=cases)
    for basis in ['free', 'cases', None]:
        state = _state([])
        state.update(preferred_model='', selected_agent_key='api-testing-agent', requested_skill_keys=[],
            observation_prompt_blocks=[])
        state['context_bundle'] = {'project_id': 'project'}
        if basis: state['context_bundle']['execution_basis'] = basis
        await router(state)
        assert state['test_case_prompt_blocks'] == (['old references'] if basis is None else [])
    assert cases.retrieve.await_count == 1


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_LIVE_AGENT_CASE_CONTEXT_MODEL') != '1',
    reason='requires current configured actual model and real isolated storage')
async def test_actual_model_reads_complete_frozen_case_without_authoring_template(tmp_path):
    from pathlib import Path
    from time import perf_counter
    from src.infrastructure.model_config_store import MySQLModelConfigStore
    from src.registry.models import ModelRegistry
    from src.schemas.model_config import ModelInvocationRequest
    async with planned(tmp_path, True) as (env, store, suites, service, original_plan, now, table):
        project = await env.projects.get(original_plan.project_id)
        tail = 'tail-' + uuid4().hex
        data = {'objective': '未登录访问拒绝测试', 'precondition': '保持未登录，不自动补登录',
            'steps': [{'order': i, 'action': '记录验证信息'} for i in range(1, 8)] +
                [{'order': 8, 'action': tail, 'expected': 401}], 'custom': {'literal.key': False}}
        file = tmp_path / 'actual-context.json'
        file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        original = await env.docs.upload_document(project_id=project.id, filename=file.name,
            content_base64=base64.b64encode(file.read_bytes()).decode(), source='actual-model-context-test')
        stored = await env.objects.read_object_uri(original.storage_uri)
        assert stored['content'] == file.read_bytes()
        await active_case(env, project, 'actual-context', json.loads(stored['content']))
        env.service._models = SequenceModel(lambda c: finish(item(c, 'actual-context')))
        plan = await env.service.create(project.id, CasePlanningRequest(objective='保持未登录的拒绝验证'))
        detail = await service.create(project.id, plan.id, CasePlanRunRequest(creation_key='actual-model-context', mode_key='api_testing'))
        record = detail.items[0]
        frozen = context(detail, record, await env.cases.get_version(record.case_version_id))
        state = _state([])
        state.update(user_message='本次只检查收到的冻结用例输入，不执行业务或调用工具。只返回 JSON：'
            '{"tail":第8步的action原文,"missing_login":是否应保留未登录条件的布尔值,"literal":custom中literal.key的原值}。',
            normalized_input='', context_bundle=frozen.runtime_context())
        assembly = PromptAssemblyService().build_for_turn(state, [])
        assert tail in assembly.system_prompt and 'DO-NOT-INJECT-TEMPLATE' not in assembly.system_prompt
        runtime = ModelRuntimeService(model_registry=ModelRegistry(MySQLModelConfigStore(env.settings)), settings=env.settings)
        model = runtime.get_default_model_config()
        assert model is not None
        started = perf_counter()
        response = await runtime.invoke(model.key, ModelInvocationRequest(system_prompt=assembly.system_prompt,
            messages=assembly.runtime_messages, tools=[], system_prompt_sections=assembly.system_sections))
        assert response.response_summary.get('mode') == 'ok', response.response_summary.get('mode')
        assert response.tool_calls == []
        actual = json.loads(response.text)
        assert actual == {'tail': tail, 'missing_login': True, 'literal': False}
        report = {'model_key': model.key, 'elapsed_seconds': round(perf_counter()-started, 3),
            'response_mode': response.response_summary.get('mode'), 'full_tail_read': True,
            'negative_condition_preserved': True, 'false_preserved': True,
            'template_excluded': True, 'business_tools_executed': False}
        Path('../docs/upgrade_2026-10-03/p2-agent-context-model.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
