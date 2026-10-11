"""Record SDK sends without replacing network, responses or production modules."""
import asyncio
import hashlib
import json
import os
from contextvars import ContextVar
from pathlib import Path
import runpy
import sys
from time import perf_counter
from uuid import uuid4

from src.application.model_clients.openai_client import OpenAIChatClient
from src.application.model_clients.base import response_to_dict

_sdk_call = ContextVar('qa_default_sdk_call', default=None)


async def observe_stream(stream, root, identity, state):
    """Forward actual SDK objects; save counts, never private field contents."""
    started = state['started']
    fields = {key: {'events': 0, 'nonempty_events': 0, 'string_chars': 0,
        'first_nonempty_seconds': None} for key in ('content', 'reasoning', 'reasoning_content', 'refusal')}
    observation = {'sdk_call_id': state['id'], 'physical_request_id': identity,
        'chunk_count': 0, 'first_chunk_seconds': None, 'last_chunk_seconds': None,
        'field_counts': fields, 'other_nonempty_delta_field_events': 0,
        'sdk_iteration_finished': False, 'finish_reason': None, 'numeric_usage': {},
        'private_contents_saved': False, 'response_body_saved': False}
    try:
        async for chunk in stream:
            elapsed = round(perf_counter()-started, 3)
            observation['chunk_count'] += 1
            observation['last_chunk_seconds'] = elapsed
            if observation['first_chunk_seconds'] is None:
                observation['first_chunk_seconds'] = elapsed
            value = response_to_dict(chunk)
            usage = value.get('usage') or {}
            if isinstance(usage, dict):
                for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                    if type(usage.get(key)) is int:
                        observation['numeric_usage'][key] = usage[key]
            choices = value.get('choices') or []
            if choices and isinstance(choices[0], dict):
                choice = choices[0]
                if choice.get('finish_reason') in {'stop', 'length', 'tool_calls', 'content_filter', 'function_call'}:
                    observation['finish_reason'] = choice['finish_reason']
                delta = choice.get('delta') or {}
                if isinstance(delta, dict):
                    for key, part in delta.items():
                        if key not in fields:
                            if key not in {'role', 'tool_calls', 'function_call'} and part:
                                observation['other_nonempty_delta_field_events'] += 1
                            continue
                        entry = fields[key]
                        if part is not None:
                            entry['events'] += 1
                        if isinstance(part, str):
                            entry['string_chars'] += len(part)
                        if part:
                            entry['nonempty_events'] += 1
                            if entry['first_nonempty_seconds'] is None:
                                entry['first_nonempty_seconds'] = elapsed
            yield chunk
        observation['sdk_iteration_finished'] = True
    except BaseException as exc:
        observation['error_type'] = type(exc).__name__
        raise
    finally:
        observation['elapsed_seconds'] = round(perf_counter()-started, 3)
        (root/(identity+'.sdk-stream.json')).write_text(json.dumps(observation, ensure_ascii=False, indent=2),encoding='utf-8')


def install(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    original = OpenAIChatClient._make_client

    def make_client(self, config, api_key):
        client = original(self, config, api_key)

        async def request_hook(request):
            raw = await request.aread()
            identity = uuid4().hex
            request.extensions["qa_probe_identity"] = identity
            state = _sdk_call.get()
            if state is not None:
                state['physical_requests'].append(identity)
            (root / (identity + ".request.json")).write_bytes(raw)
            (root / (identity + ".send.json")).write_text(json.dumps({
                "process_id": os.getpid(), "method": request.method, "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), "model_key": config.key,
                "network_outcome": "unknown", "request_headers_saved": False,
                "sdk_call_id": state['id'] if state else None,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            marker_path = os.getenv("QA_DEFAULT_PROBE_BLOCK_CORRECTION_MARKER")
            release_path = os.getenv("QA_DEFAULT_PROBE_BLOCK_CORRECTION_RELEASE")
            if marker_path and release_path:
                # The hook observes the actual serialized request and blocks only
                # the read-only receipt-correction prompt. It never changes the
                # request or response and is used solely by the process-recovery
                # acceptance test.
                try:
                    payload = json.loads(raw)
                    system = "\n".join(str(item.get("content", "")) for item in payload.get("messages", [])
                        if item.get("role") == "system")
                except (ValueError, TypeError):
                    system = ""
                if "Review this completed Agent execution against its complete frozen cases" in system:
                    Path(marker_path).write_text(json.dumps({
                        "process_id": os.getpid(), "physical_request_id": identity,
                        "sha256": hashlib.sha256(raw).hexdigest(), "request_size_bytes": len(raw),
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    deadline = perf_counter() + 120
                    while not Path(release_path).exists():
                        if perf_counter() >= deadline:
                            raise TimeoutError("test-only receipt correction barrier was not released")
                        await asyncio.sleep(0.1)

        async def response_hook(response):
            identity = response.request.extensions["qa_probe_identity"]
            (root / (identity + ".response.json")).write_text(json.dumps({
                "status_code": response.status_code, "process_id": os.getpid(),
                "body_read_by_probe": False, "stream_completion_verified": False,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        client._client.event_hooks["request"].append(request_hook)
        client._client.event_hooks["response"].append(response_hook)
        send = client._client.send
        async def observed_send(request, *args, **kwargs):
            started = perf_counter()
            record = {"response_headers_received": False, "request_headers_saved": False, "body_read_by_probe": False}
            try:
                response = await send(request, *args, **kwargs)
                record.update(response_headers_received=True, status_code=response.status_code)
                return response
            except BaseException as exc:
                record["error_type"] = type(exc).__name__
                raise
            finally:
                identity = request.extensions.get("qa_probe_identity")
                if identity:
                    record["elapsed_seconds"] = round(perf_counter() - started, 3)
                    (root / (identity + ".attempt.json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
        client._client.send = observed_send
        create = client.chat.completions.create
        async def observed_create(*args, **kwargs):
            state = {'id':uuid4().hex, 'started':perf_counter(), 'physical_requests':[]}
            token = _sdk_call.set(state)
            record = {'sdk_call_id':state['id'], 'stream_requested':bool(kwargs.get('stream')),
                'physical_requests':state['physical_requests'], 'sdk_create_returned':False}
            try:
                result = await create(*args, **kwargs)
                record['sdk_create_returned'] = True
                if kwargs.get('stream'):
                    identity = result.response.request.extensions['qa_probe_identity']
                    return observe_stream(result, root, identity, state)
                return result
            except BaseException as exc:
                record['error_type'] = type(exc).__name__
                raise
            finally:
                record['elapsed_seconds'] = round(perf_counter()-state['started'], 3)
                (root/(state['id']+'.sdk-create.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                _sdk_call.reset(token)
        client.chat.completions.create = observed_create
        return client

    OpenAIChatClient._make_client = make_client
    return original


if __name__ == "__main__":
    # Test process only; run the actual CLI/API module with its original args.
    install(os.environ["QA_DEFAULT_PROBE_DIR"])
    module = sys.argv[1]
    sys.argv = [module, *sys.argv[2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
