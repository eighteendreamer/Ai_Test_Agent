"""Record SDK sends without replacing network, responses or production modules."""
import asyncio
import hashlib
import httpx
import json
import os
import re
from contextvars import ContextVar
from pathlib import Path
import runpy
import sys
from time import perf_counter
from uuid import uuid4

from src.application.model_clients.openai_client import OpenAIChatClient
from src.application.model_clients.base import response_to_dict
from src.application.test_runs.agent_case_receipt import AgentCaseReceiptService

_sdk_call = ContextVar('qa_default_sdk_call', default=None)


def exception_type_chain(error):
    """Return exception class names without persisting messages or payloads."""
    chain = []
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(type(current).__name__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None:
            pending.append(current.__context__)
    return chain


_SAFE_PROVIDER_ERROR_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")


def safe_provider_error_metadata(error):
    """Keep only bounded SDK status fields; never persist provider error text/body."""
    status_code = getattr(error, 'status_code', None)
    metadata = {'status_code': status_code if type(status_code) is int else None}
    for field in ('code', 'param', 'type'):
        value = getattr(error, field, None)
        if value is None:
            metadata[field] = None
        elif isinstance(value, str) and _SAFE_PROVIDER_ERROR_TOKEN.fullmatch(value):
            metadata[field] = value
        else:
            metadata[field] = '[redacted]'
    return metadata


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
        observation['error_chain_types'] = exception_type_chain(exc)
        raise
    finally:
        observation['elapsed_seconds'] = round(perf_counter()-started, 3)
        (root/(identity+'.sdk-stream.json')).write_text(json.dumps(observation, ensure_ascii=False, indent=2),encoding='utf-8')


def install(directory):
    scripted_graph = json.loads(os.getenv("QA_DEFAULT_PROBE_SCRIPTED_GRAPH") or "null")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    original = OpenAIChatClient._make_client

    # Test-only barrier: the production receipt service has already written the
    # response object and lease-fenced phase=response checkpoint when correct()
    # returns.  Blocking here allows the live process test to kill the API at
    # that exact durable boundary without changing production behavior.
    original_correct = AgentCaseReceiptService.correct
    after_response_marker = os.getenv("QA_DEFAULT_PROBE_BLOCK_AFTER_CORRECTION_MARKER")
    after_response_release = os.getenv("QA_DEFAULT_PROBE_BLOCK_AFTER_CORRECTION_RELEASE")
    if after_response_marker and after_response_release:
        async def observed_receipt_correct(self, *args, **kwargs):
            result = await original_correct(self, *args, **kwargs)
            checkpoint = result[1] if isinstance(result, tuple) and len(result) > 1 else None
            correction = getattr(checkpoint, "receipt_correction", None)
            if correction is None or correction.phase != "response":
                return result
            Path(after_response_marker).write_text(json.dumps({
                "phase": correction.phase,
                "call_id": correction.call_id,
                "response_uri": correction.response.uri if correction.response else None,
                "response_sha256": correction.response.sha256 if correction.response else None,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            deadline = perf_counter() + 180
            while not Path(after_response_release).exists():
                if perf_counter() >= deadline:
                    raise TimeoutError("test-only persisted receipt response barrier was not released")
                await asyncio.sleep(0.1)
            return result

        AgentCaseReceiptService.correct = observed_receipt_correct

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
                scripted = None
                if scripted_graph is not None:
                    raw = await request.aread()
                    try:
                        payload = json.loads(raw)
                        system = "\n".join(str(item.get("content", "")) for item in payload.get("messages", [])
                            if item.get("role") == "system")
                    except (ValueError, TypeError):
                        payload, system = None, ""
                    # Test-double for the execution graph only: graph turns replay a
                    # caller-provided script (same nature as the controlled HTTP model
                    # server), while the read-only correction call always reaches the
                    # real provider.
                    is_correction = "Review this completed Agent execution" in system
                    is_graph = payload is not None and "Complete Frozen Case" in system
                    if is_graph and not is_correction:
                        # Select the turn from the message history (like the
                        # controlled in-process agents do): a replayed or resumed
                        # graph re-requests with its completed tool results, so
                        # counting them keeps the script idempotent and never
                        # re-fires a business side effect twice.
                        completed = sum(1 for item in payload.get("messages", [])
                            if item.get("role") == "tool")
                        if completed < len(scripted_graph):
                            scripted = scripted_graph[completed]
                            scripted_turn = completed + 1
                if scripted is not None:
                    # The synthesized response bypasses the real transport, so run
                    # the observation hook manually: it assigns the probe identity
                    # the stream observer and record writers rely on.
                    await request_hook(request)
                    if scripted.get("network_outage"):
                        # Real transport failure: dial an endpoint the caller has
                        # closed and let httpx raise its actual connection error.
                        # The exception class (and therefore the product's failure
                        # handling) is the same one a real outage produces.
                        async with httpx.AsyncClient(timeout=5) as outage_client:
                            await outage_client.post(scripted["network_outage"], json={})
                        raise AssertionError("network outage target unexpectedly answered")
                    if scripted.get("block_model_call"):
                        # Test-only barrier used to kill a real Worker while it is
                        # inside a model call.
                        block_marker = os.getenv("QA_DEFAULT_PROBE_GRAPH_BLOCK_MARKER")
                        block_release = os.getenv("QA_DEFAULT_PROBE_GRAPH_BLOCK_RELEASE")
                        if block_marker and block_release:
                            Path(block_marker).write_text(json.dumps({
                                "process_id": os.getpid(),
                                "physical_request_id": request.extensions.get("qa_probe_identity"),
                            }, ensure_ascii=False, indent=2), encoding="utf-8")
                        deadline = perf_counter() + 180
                        while block_release and not Path(block_release).exists():
                            if perf_counter() >= deadline:
                                raise TimeoutError("test-only model call barrier was not released")
                            await asyncio.sleep(0.1)
                    wants_stream = bool(payload.get("stream"))
                    if wants_stream:
                        # The execution graph always streams; synthesize an SSE body
                        # with the same shape the real provider sends.
                        deltas = []
                        if scripted.get("tool_calls"):
                            for index, item in enumerate(scripted["tool_calls"]):
                                deltas.append({"delta": {"tool_calls": [{
                                    "index": index, "id": item.get("id", f"call_{index}"),
                                    "type": "function",
                                    "function": {"name": item["name"], "arguments": item["arguments"]}}]},
                                    "finish_reason": None})
                            finish_reason = "tool_calls"
                        else:
                            deltas.append({"delta": {"role": "assistant",
                                "content": scripted.get("text", "")}, "finish_reason": None})
                            finish_reason = "stop"
                        deltas.append({"delta": {}, "finish_reason": finish_reason})
                        lines = []
                        for choice in deltas:
                            lines.append("data: " + json.dumps({"id": "chatcmpl-probe-scripted",
                                "object": "chat.completion.chunk", "created": 0, "model": config.model_id,
                                "choices": [choice]}, ensure_ascii=False) + "\n\n")
                        lines.append("data: " + json.dumps({"id": "chatcmpl-probe-scripted",
                            "object": "chat.completion.chunk", "created": 0, "model": config.model_id,
                            "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                "total_tokens": 2}}) + "\n\n")
                        lines.append("data: [DONE]\n\n")
                        sse_payload = "".join(lines).encode("utf-8")

                        async def sse_body():
                            yield sse_payload

                        response = httpx.Response(200, headers={"content-type": "text/event-stream"},
                            content=sse_body(), request=request)
                    else:
                        message = {}
                        if scripted.get("tool_calls"):
                            message["tool_calls"] = [{
                                "id": item.get("id", f"call_{index}"),
                                "type": "function",
                                "function": {"name": item["name"], "arguments": item["arguments"]},
                            } for index, item in enumerate(scripted["tool_calls"])]
                            finish = "tool_calls"
                        else:
                            message["content"] = scripted.get("text", "")
                            finish = "stop"
                        completion = {"id": "chatcmpl-probe-scripted", "object": "chat.completion",
                            "created": 0, "model": config.model_id,
                            "choices": [{"index": 0, "message": {"role": "assistant", **message},
                                "finish_reason": finish}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
                        response = httpx.Response(200, json=completion, request=request)
                    record.update(response_headers_received=True, status_code=200,
                        scripted_graph_turn=scripted_turn)
                    return response
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
                record['error_chain_types'] = exception_type_chain(exc)
                record['provider_error'] = safe_provider_error_metadata(exc)
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
