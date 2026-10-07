"""Actual HTTP/SDK counts must preserve content, tools, retries and cancellation."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

import default_model_probe as probe
from src.application.model_clients.base import ProviderClientError
from src.application.model_clients.openai_client import OpenAIChatClient
from src.schemas.model_config import ModelConfigRecord, ModelInvocationRequest


@contextmanager
def actual_provider(mode, private):
    calls = []
    release = threading.Event()
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(body)
            if mode == 'http-failure' or (mode == 'retry' and len(calls) < 3):
                raw = json.dumps({'error':{'message':private if mode=='http-failure' else 'fixture retry','type':'server_error'}}).encode()
                self.send_response(500); self.send_header('Content-Length',str(len(raw)))
                self.end_headers(); self.wfile.write(raw); return
            def event(delta, reason=None):
                data = {'id':'actual-sdk-observation','object':'chat.completion.chunk','created':1,
                    'model':body['model'],'choices':[{'index':0,'delta':delta,'finish_reason':reason}]}
                if reason: data['usage']={'prompt_tokens':17,'completion_tokens':8192,'total_tokens':8209}
                return ('data: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode()
            first = event({'reasoning':private,'reasoning_content':private,'other_private':private,'content':''})
            if mode == 'tool':
                chunks = [first,event({'tool_calls':[{'index':0,'id':'call-fixture','type':'function',
                    'function':{'name':'trace_fixture','arguments':'{"count":'}}]}),
                    event({'tool_calls':[{'index':0,'function':{'arguments':'0,"flag":false,"nullable":null}'}}]},'tool_calls')]
            elif mode == 'length':
                chunks = [first,event({'content':''},'length')]
            elif mode in {'midstream','cancel'}:
                chunks = [first]
            else:
                chunks = [first,event({'content':'正'}),event({'content':'文😀'},'stop')]
            raw = b''.join(chunks)+(b'' if mode in {'midstream','cancel'} else b'data: [DONE]\n\n')
            self.send_response(200); self.send_header('Content-Type','text/event-stream')
            self.send_header('Content-Length',str(len(raw)+(100 if mode in {'midstream','cancel'} else 0)))
            self.end_headers()
            try:
                self.wfile.write(raw); self.wfile.flush()
                if mode == 'cancel':release.wait(10)
            except (BrokenPipeError,ConnectionResetError):pass
            self.close_connection=True
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider);server.daemon_threads=True
    serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    try:yield f'http://127.0.0.1:{server.server_port}/v1',calls
    finally:
        release.set();server.shutdown();server.server_close();serving.join(5)
        assert not serving.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['normal','retry','length','tool','midstream','cancel'])
async def test_actual_stream_observation_never_saves_private_fields_or_changes_execution(tmp_path,monkeypatch,mode):
    private='PRIVATE_REASONING_SENTINEL_'+uuid4().hex
    key='PRIVATE_AUTH_SENTINEL_'+uuid4().hex
    original=probe.install(tmp_path)
    first=asyncio.Event();convert=probe.response_to_dict
    def observed_chunk(chunk):
        value=convert(chunk);first.set();return value
    monkeypatch.setattr(probe,'response_to_dict',observed_chunk)
    text=[]
    async def receive(delta):text.append(delta)
    try:
        with actual_provider(mode,private) as (url,calls):
            config=ModelConfigRecord(key='fixture',name='fixture',provider='openai',transport='openai_chat_completions',
                model_id='fixture',api_base_url=url,max_output_tokens=8192)
            tools=[{'name':'trace.fixture','description':'fixture','input_schema':{'type':'object'}}] if mode=='tool' else []
            request=ModelInvocationRequest(system_prompt='Observe only',messages=[{'role':'user','content':'Actual SDK fixture'}],tools=tools)
            client=OpenAIChatClient(timeout_seconds=5,max_retries=2)
            if mode=='cancel':
                task=asyncio.create_task(client.stream(config,key,request,receive))
                await asyncio.wait_for(first.wait(),5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
                result=None
            elif mode=='midstream':
                with pytest.raises(ProviderClientError):await client.stream(config,key,request,receive)
                result=None
            else:
                result=await client.stream(config,key,request,receive)
            sends=list(tmp_path.glob('*.send.json'));creates=list(tmp_path.glob('*.sdk-create.json'));streams=list(tmp_path.glob('*.sdk-stream.json'))
            assert len(sends)==len(calls)==(3 if mode=='retry' else 1)
            assert len(creates)==len(streams)==1
            create=json.loads(creates[0].read_bytes());observation=json.loads(streams[0].read_bytes())
            assert create['sdk_create_returned'] and len(create['physical_requests'])==len(sends)
            assert observation['sdk_call_id']==create['sdk_call_id']
            assert observation['physical_request_id']==create['physical_requests'][-1]
            assert all(json.loads(p.read_bytes())['sdk_call_id']==create['sdk_call_id'] for p in sends)
            assert observation['field_counts']['reasoning']['string_chars']==len(private)
            assert observation['field_counts']['reasoning_content']['string_chars']==len(private)
            assert observation['other_nonempty_delta_field_events']==1
            assert observation['sdk_iteration_finished'] is (mode not in {'midstream','cancel'})
            assert observation['first_chunk_seconds']<=observation['last_chunk_seconds']<=observation['elapsed_seconds']
            for p in tmp_path.glob('*.json'):
                contents=p.read_text(encoding='utf-8')
                assert private not in contents and key not in contents and 'other_private' not in contents
            if mode in {'normal','retry'}:
                assert result['text']=='正文😀'==''.join(text)
                assert observation['field_counts']['content']['string_chars']==len('正文😀')
                assert observation['finish_reason']=='stop'
            elif mode=='length':
                assert result['text']=='' and not text and result['finish_reason']=='length'
                assert observation['field_counts']['content']['string_chars']==0
                assert observation['numeric_usage']['completion_tokens']==8192
            elif mode=='tool':
                assert not result['text'] and not text
                assert result['tool_calls'][0].name=='trace.fixture'
                assert result['tool_calls'][0].arguments=={'count':0,'flag':False,'nullable':None}
                assert observation['finish_reason']=='tool_calls'
            else:
                assert observation['error_type'] in {'CancelledError','APIConnectionError'}
            if os.getenv('QA_SDK_OBSERVATION_DIR'):
                directory=Path(os.environ['QA_SDK_OBSERVATION_DIR'])/mode
                directory.mkdir(parents=True,exist_ok=False)
                for p in tmp_path.glob('*.json'):
                    (directory/p.name).write_bytes(p.read_bytes())
                (directory/'verification.json').write_text(json.dumps({'controlled_HTTP':True,
                    'original_official_SDK':True,'physical_requests':len(calls),'sdk_create_count':len(creates),
                    'private_contents_saved':False,'normal_content_preserved':mode in {'normal','retry'},
                    'tool_json_types_preserved':mode=='tool','cancellation_preserved':mode=='cancel',
                    'midstream_no_retry':mode=='midstream','full_P0_P5_complete':False},indent=2),encoding='utf-8')
    finally:
        OpenAIChatClient._make_client=original


@pytest.mark.asyncio
async def test_actual_http_failure_links_retries_without_saving_error_body(tmp_path):
    private='PRIVATE_ERROR_SENTINEL_'+uuid4().hex
    original=probe.install(tmp_path)
    try:
        with actual_provider('http-failure',private) as (url,calls):
            config=ModelConfigRecord(key='fixture',name='fixture',provider='openai',transport='openai_chat_completions',
                model_id='fixture',api_base_url=url,max_output_tokens=8192)
            async def receive(delta):raise AssertionError('HTTP failure has no text delta')
            with pytest.raises(ProviderClientError):
                await OpenAIChatClient(timeout_seconds=5,max_retries=2).stream(config,'private-auth',
                    ModelInvocationRequest(system_prompt='Observe only',messages=[{'role':'user','content':'Actual failure fixture'}]),receive)
            creates=list(tmp_path.glob('*.sdk-create.json'))
            assert len(calls)==3 and len(creates)==1 and not list(tmp_path.glob('*.sdk-stream.json'))
            record=json.loads(creates[0].read_bytes())
            assert not record['sdk_create_returned'] and len(record['physical_requests'])==3
            assert record['error_type']=='InternalServerError'
            for p in tmp_path.glob('*.json'):
                assert private not in p.read_text(encoding='utf-8')
            if os.getenv('QA_SDK_OBSERVATION_DIR'):
                directory=Path(os.environ['QA_SDK_OBSERVATION_DIR'])/'http-failure'
                directory.mkdir(parents=True,exist_ok=False)
                for p in tmp_path.glob('*.json'):(directory/p.name).write_bytes(p.read_bytes())
    finally:
        OpenAIChatClient._make_client=original
