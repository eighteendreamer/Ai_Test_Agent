"""Real SDK/HTTP responses must retain provider facts at the business boundary."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

import pytest

from test_case_planning import environment, finish
from test_case_planning_tasks import SubmissionModel
from src.application.models.model_runtime_service import ModelRuntimeService
from src.application.test_cases.planning_workflow import CasePlanningWorkflow
from src.core.config import get_settings
from src.schemas.case_planning import CasePlanningRequest
from src.schemas.case_planning_task import PlanningTaskCheckpoint
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest
from src.schemas.project import ProjectCreateRequest
from src.application.model_clients.completion import model_completion_status
from src.application.context.token_usage import normalize_usage


@contextmanager
def provider(transport, limited, *, delta_input=None, stream_variant='complete', content_supplier=None):
    sends, replies = [], []
    initial_content = json.dumps(finish(), ensure_ascii=False)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            content = json.dumps(content_supplier(), ensure_ascii=False) if content_supplier else initial_content
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            sends.append({'path': self.path, 'body': body})
            streaming = bool(body.get('stream')) or ':streamGenerateContent' in self.path
            if transport == 'openai_responses':
                response = {'id': 'resp_fixture', 'object': 'response', 'created_at': 1,
                    'model': 'fixture-model', 'status': 'incomplete' if limited else 'completed',
                    'incomplete_details': {'reason': 'max_output_tokens'} if limited else None,
                    'output': [{'id': 'msg_fixture', 'type': 'message', 'status': 'completed',
                        'role': 'assistant', 'content': [{'type': 'output_text', 'text': content, 'annotations': []}]}],
                    'usage': {'input_tokens': 7, 'output_tokens': 3, 'total_tokens': 10}}
                events = [{'type': 'response.output_text.delta', 'delta': content,
                    'item_id': 'msg_fixture', 'output_index': 0, 'content_index': 0, 'sequence_number': 1},
                    {'type': 'response.incomplete' if limited else 'response.completed',
                     'response': response, 'sequence_number': 2}]
            elif transport == 'anthropic_messages':
                reason = 'max_tokens' if limited else 'end_turn'
                response = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': 'fixture-model',
                    'content': [{'type': 'text', 'text': content}], 'stop_reason': reason,
                    'stop_sequence': None, 'usage': {'input_tokens': 7, 'output_tokens': 3}}
                events = [{'type': 'message_start', 'message': {**response, 'content': [], 'stop_reason': None}},
                    {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                    {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': content}},
                    {'type': 'content_block_stop', 'index': 0},
                    {'type': 'message_delta', 'delta': {'stop_reason': reason, 'stop_sequence': None},
                        'usage': {'output_tokens': 3, **({'input_tokens': delta_input}
                            if delta_input is not None else {})}}, {'type': 'message_stop'}]
            elif transport == 'google_gemini_generate_content':
                response = {'responseId': 'gemini_fixture', 'candidates': [{'index': 0,
                    'content': {'role': 'model', 'parts': [{'text': content}]},
                    'finishReason': 'MAX_TOKENS' if limited else 'STOP'}],
                    'usageMetadata': {'promptTokenCount': 7, 'candidatesTokenCount': 3, 'totalTokenCount': 10}}
                events = [response]
            else:
                reason = 'length' if limited else 'stop'
                response = {'id': 'chatcmpl_fixture', 'object': 'chat.completion', 'created': 1,
                    'model': 'fixture-model', 'choices': [{'index': 0,
                        'message': {'role': 'assistant', 'content': content}, 'finish_reason': reason}],
                    'usage': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}}
                events = [{**response, 'object': 'chat.completion.chunk', 'choices': [{'index': 0,
                    'delta': {'role': 'assistant', 'content': content}, 'finish_reason': reason}]}]
            if streaming and stream_variant == 'missing-terminal':
                if transport == 'openai_responses':
                    events = events[:-1]
                elif transport == 'anthropic_messages':
                    events = [event for event in events if event['type'] not in {'message_delta', 'message_stop'}]
                elif transport == 'google_gemini_generate_content':
                    events[0]['candidates'][0].pop('finishReason')
                else:
                    events[0]['choices'][0]['finish_reason'] = None
            elif streaming and stream_variant == 'missing-message-stop':
                assert transport == 'anthropic_messages'
                events = events[:-1]
            elif streaming and stream_variant in {'tool-missing-terminal', 'tool-complete'}:
                assert transport == 'openai_chat_completions'
                events[0]['choices'][0]['finish_reason'] = 'tool_calls' if stream_variant == 'tool-complete' else None
                events[0]['choices'][0]['delta']['tool_calls'] = [{'index': 0, 'id': 'owned-fixture-call',
                    'type': 'function', 'function': {'name': 'fixture_tool', 'arguments': '{"value":"owned"}'}}]
            if streaming:
                raw = ''.join((('event: '+event['type']+'\n') if transport == 'anthropic_messages' else '')
                    +'data: '+json.dumps(event, ensure_ascii=False)+'\n\n' for event in events).encode()
            else:
                raw = json.dumps(response, ensure_ascii=False).encode()
            replies.append(raw)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if streaming else 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        # Anthropic's SDK adds /v1/messages itself; OpenAI uses the /v1 base.
        prefix = '' if transport == 'anthropic_messages' else '/v1'
        yield f'http://127.0.0.1:{server.server_port}{prefix}', sends, replies
    finally:
        server.shutdown()
        server.server_close()
        serving.join(5)
        assert not serving.is_alive()


class Registry:
    def __init__(self, config):
        self.config = config

    def get_runtime_config(self, key):
        assert key == self.config.key
        return self.config


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['openai_chat_completions', 'openai_responses',
    'anthropic_messages', 'google_gemini_generate_content', 'langchain_chat'])
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('limited', [False, True])
async def test_actual_sdk_fields_and_business_completion(tmp_path, transport, streaming, limited):
    actual_transport = 'openai_chat_completions' if transport == 'langchain_chat' else transport
    with provider(actual_transport, limited) as (url, sends, replies):
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
        request = ModelInvocationRequest(system_prompt='Return the supplied JSON object.',
            messages=[{'role': 'user', 'content': json.dumps(finish(), ensure_ascii=False)}])
        async with runtime.stream_handler(receive if streaming else None):
            response = await runtime.invoke('fixture', request)
        directory = None
        if os.getenv('QA_MODEL_COMPLETION_DIR'):
            directory = Path(os.environ['QA_MODEL_COMPLETION_DIR']) / (
                f'{transport}-{"stream" if streaming else "native"}-{"limited" if limited else "complete"}')
            directory.mkdir(parents=True, exist_ok=False)
            (directory/'physical-request.json').write_text(json.dumps(sends[0], ensure_ascii=False, indent=2), encoding='utf-8')
            (directory/'physical-response.bin').write_bytes(replies[0])
            (directory/'sdk-result.json').write_text(response.model_dump_json(indent=2), encoding='utf-8')
        assert response.response_summary['mode'] == ('incomplete_response' if limited else 'ok'), response.response_summary
        assert response.response_summary['completion_status'] == ('truncated' if limited else 'complete')
        assert model_completion_status(response.response_summary) == response.response_summary['completion_status']
        assert normalize_usage(response.response_summary['usage']).model_dump() == {
            'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}
        assert response.text == json.dumps(finish(), ensure_ascii=False)
        assert len(sends) == 1
        body = sends[0]['body']
        expected_field = ('max_output_tokens' if actual_transport == 'openai_responses' else
            'max_completion_tokens' if transport == 'langchain_chat' else 'max_tokens')
        if actual_transport.startswith('google'):
            assert body['generationConfig']['maxOutputTokens'] == 512
            assert body['contents'][0]['parts'][0]['text'] == request.messages[0]['content']
        else:
            assert body[expected_field] == 512
        if streaming:
            assert ''.join(deltas).strip() == response.text

        async with environment(tmp_path, model=SubmissionModel(json.dumps(finish()))) as env:
            project = await env.projects.create(ProjectCreateRequest(project_key='matrix', name='多模型实际协议'))
            seed = await env.service.prepare_task(project.id, CasePlanningRequest(objective='核验协议终态'))
            seed.plan.rounds = [{'round': 1, 'status': 'response_received',
                'response_metadata': response.response_summary}]
            checkpoint = PlanningTaskCheckpoint(plan=seed.plan.model_copy(deep=True), phase='response',
                next_round=1, response_text=response.text, response_metadata=response.response_summary)
            saved = []
            async def persist(value, *, message):
                saved.append(value.model_copy(deep=True))
            terminal = await CasePlanningWorkflow(env.service).run(seed, checkpoint,
                deadline_at=datetime.now(timezone.utc)+timedelta(seconds=30), persist=persist)
            if directory is not None:
                (directory/'result.json').write_text(json.dumps({'response': response.model_dump(mode='json'),
                    'terminal': terminal.model_dump(mode='json'), 'physical_sends': len(sends),
                    'real_provider': False, 'controlled_HTTP': True, 'streaming': streaming,
                    'private_headers_saved': False, 'full_P0_P5_complete': False}, ensure_ascii=False, indent=2), encoding='utf-8')
            assert terminal.plan.status == ('planning_failed' if limited else 'no_match')
            assert not env.service._models.requests


@pytest.mark.asyncio
async def test_actual_anthropic_explicit_zero_is_not_dropped():
    with provider('anthropic_messages', False, delta_input=0) as (url, sends, replies):
        settings = get_settings().model_copy(deep=True)
        settings.model.langchain_model_adapter_enabled = False
        config = ModelConfigRecord(key='fixture', name='fixture', provider='anthropic',
            transport='anthropic_messages', model_id='fixture-model', api_base_url=url,
            api_key='local-fixture-marker', max_output_tokens=512)
        runtime = ModelRuntimeService(Registry(config), settings)
        async def receive(delta):
            pass
        async with runtime.stream_handler(receive):
            result = await runtime.invoke('fixture', ModelInvocationRequest(system_prompt='Return JSON.',
                messages=[{'role': 'user', 'content': '{}'}]))
        assert result.response_summary['usage']['input_tokens'] == 0
        assert normalize_usage(result.response_summary['usage']).total_tokens == 3
        assert len(sends) == 1
        if os.getenv('QA_MODEL_COMPLETION_DIR'):
            directory = Path(os.environ['QA_MODEL_COMPLETION_DIR']) / 'anthropic-explicit-zero'
            directory.mkdir(parents=True, exist_ok=False)
            (directory/'physical-request.json').write_text(json.dumps(sends[0], indent=2), encoding='utf-8')
            (directory/'physical-response.bin').write_bytes(replies[0])
            (directory/'sdk-result.json').write_text(result.model_dump_json(indent=2), encoding='utf-8')
