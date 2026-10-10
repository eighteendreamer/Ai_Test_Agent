"""Actual SDK EOF without a protocol terminal cannot finish a business plan."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from test_case_planning import environment, finish
from test_case_planning_tasks import SubmissionModel
from test_model_completion_http import Registry, provider
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.test_cases.planning_workflow import CasePlanningWorkflow
from src.core.config import get_settings
from src.schemas.case_planning import CasePlanningRequest
from src.schemas.case_planning_task import PlanningTaskCheckpoint
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.model_config import is_model_failure_summary
from src.schemas.project import ProjectCreateRequest
from src.graph.nodes.model_invoker import build_model_invoker_node, route_after_model_invoker
from src.graph.nodes.finalizer import build_finalizer_node
from src.graph.nodes.responder import responder
from src.registry.tools import ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize('transport,variant', [
    ('openai_chat_completions', 'missing-terminal'), ('openai_responses', 'missing-terminal'),
    ('anthropic_messages', 'missing-terminal'), ('google_gemini_generate_content', 'missing-terminal'),
    ('langchain_chat', 'missing-terminal'), ('anthropic_messages', 'missing-message-stop'),
    ('openai_chat_completions', 'budget-limited'), ('openai_responses', 'budget-limited'),
    ('anthropic_messages', 'budget-limited'), ('google_gemini_generate_content', 'budget-limited'),
    ('langchain_chat', 'budget-limited'),
    ('openai_chat_completions', 'tool-missing-terminal'), ('langchain_chat', 'tool-missing-terminal'),
    ('openai_chat_completions', 'tool-complete'), ('langchain_chat', 'tool-complete'),
])
async def test_actual_sdk_missing_terminal_cannot_accept_valid_JSON(tmp_path, transport, variant):
    actual_transport = 'openai_chat_completions' if transport == 'langchain_chat' else transport
    with provider(actual_transport, variant == 'budget-limited', stream_variant=variant) as (url, sends, replies):
        settings = get_settings().model_copy(deep=True)
        settings.model.langchain_model_adapter_enabled = transport == 'langchain_chat'
        config = ModelConfigRecord(key='fixture', name='fixture', provider=(
            'google' if actual_transport.startswith('google') else
            'anthropic' if actual_transport.startswith('anthropic') else 'openai'),
            transport=actual_transport, model_id='fixture-model', api_base_url=url,
            api_key='local-fixture-marker', max_output_tokens=512)
        runtime = ModelRuntimeService(Registry(config), settings)
        deltas = []
        async def receive(delta):
            deltas.append(delta)
        async with runtime.stream_handler(receive):
            response = await runtime.invoke('fixture', ModelInvocationRequest(system_prompt='Return supplied JSON.',
                messages=[{'role': 'user', 'content': json.dumps(finish(), ensure_ascii=False)}]))
        assert len(sends) == 1
        directory = None
        if name := os.getenv('QA_MODEL_STREAM_TERMINAL_DIR'):
            directory = Path(name) / f'{transport}-{variant}'
            directory.mkdir(parents=True, exist_ok=False)
            (directory/'physical-request.json').write_text(json.dumps(sends[0], ensure_ascii=False, indent=2), encoding='utf-8')
            (directory/'physical-response.bin').write_bytes(replies[0])
            (directory/'sdk-result.json').write_text(response.model_dump_json(indent=2), encoding='utf-8')

        async with environment(tmp_path, model=SubmissionModel(json.dumps(finish()))) as env:
            project = await env.projects.create(ProjectCreateRequest(project_key='stream-terminal', name='流式协议终态'))
            seed = await env.service.prepare_task(project.id, CasePlanningRequest(objective='核验真实协议结束'))
            seed.plan.rounds = [{'round': 1, 'status': 'response_received', 'response_metadata': response.response_summary}]
            checkpoint = PlanningTaskCheckpoint(plan=seed.plan.model_copy(deep=True), phase='response',
                next_round=1, response_text=response.text, response_metadata=response.response_summary,
                response_has_tools=bool(response.tool_calls))
            saved = []
            async def persist(value, *, message):
                saved.append(value.model_copy(deep=True))
            terminal = await CasePlanningWorkflow(env.service).run(seed, checkpoint,
                deadline_at=datetime.now(timezone.utc)+timedelta(seconds=30), persist=persist)
            if directory:
                (directory/'facts.json').write_text(json.dumps({'terminal': terminal.model_dump(mode='json'),
                    'physical_sends': len(sends), 'recovery_new_sends': len(env.service._models.requests),
                    'visible_deltas': deltas, 'real_external_provider': False,
                    'controlled_HTTP': True, 'business_execution_verified': False}, ensure_ascii=False, indent=2), encoding='utf-8')
            assert response.text == json.dumps(finish(), ensure_ascii=False)
            assert terminal.plan.status == 'planning_failed'
            assert response.response_summary['completion_status'] == (
                'tool_calls' if variant == 'tool-complete' else 'truncated' if variant == 'budget-limited' else 'incomplete')
            assert not env.service._models.requests

        class SavedResponse:
            async def invoke(self, *args, **kwargs):
                return response
        state = {'session_id': 'stream-fixture', 'turn_id': 'turn', 'trace_id': 'trace',
            'mode_key': 'default', 'selected_agent_key': 'fixture', 'selected_model_key': 'fixture',
            'selected_model_name': 'fixture', 'selected_model_provider': config.provider,
            'available_tool_keys': [], 'model_visible_tool_keys': [], 'allowed_tool_keys': [],
            'approval_required_tool_keys': [], 'system_prompt': 'Return JSON.',
            'runtime_messages': [{'role': 'user', 'content': '{}'}], 'system_prompt_sections': [],
            'runtime_message_sections': [], 'context_bundle': {}, 'turn_token_usage': {},
            'plan_steps': [], 'tool_results': [], 'resolved_skill_keys': [], 'pending_approvals': [],
            'selected_agent_name': 'fixture',
            'loop_iteration': 0, 'final_response': '', 'pending_turn': {}, 'continue_loop': False,
            'termination_reason': '', 'control_state': 'running', 'event_log': []}
        state = await build_model_invoker_node(SavedResponse(), ToolRegistry())(state)
        route = route_after_model_invoker(state)
        if variant == 'tool-complete':
            if directory:
                (directory/'graph-facts.json').write_text(json.dumps({'route': route, 'physical_sends': len(sends),
                    'retained_model_tool_calls': state['model_tool_calls'], 'normal_tool_control': True,
                    'actual_tool_execution_verified': False}, ensure_ascii=False, indent=2), encoding='utf-8')
            assert route == 'tool_executor' and len(response.tool_calls) == 1
            assert not is_model_failure_summary(response.response_summary)
            return
        state = await build_finalizer_node(model_runtime_service=None)(state)
        state = responder(state)
        assert len(sends) == 1
        if directory:
            (directory/'graph-facts.json').write_text(json.dumps({'events': state['event_log'],
                'termination_reason': state['termination_reason'], 'physical_sends': len(sends), 'route': route,
                'retained_model_tool_calls': state['model_tool_calls'],
                'actual_protocol_response_used': True, 'full_App_verified': False}, ensure_ascii=False, indent=2), encoding='utf-8')
        assert state['termination_reason'] == 'interrupted'
        assert route == 'finalizer'
        if variant == 'tool-missing-terminal':
            assert len(response.tool_calls) == len(state['model_tool_calls']) == 1
        assert is_model_failure_summary(response.response_summary)
        assert any(event['type'] == 'model.invocation_failed' for event in state['event_log'])
        failed_event = next(event for event in state['event_log'] if event['type'] == 'model.invocation_failed')
        assert failed_event['payload']['completion_status'] == response.response_summary['completion_status']
        assert not any(event['type'] == 'graph.execution_completed' for event in state['event_log'])
