"""Replay actual default-Agent failure and verify native HTTP/PG outcomes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_agent_case_execution import regression_business_http
import test_case_stateful_recovery_research as research
from test_case_operations import LIVE, POLICY, operation_runtime, save_operation
from src.application.test_cases.planning_tool_contracts import capture_tool_contracts
from src.modes.api_testing_mode.campaign_state import ApiTestTask
from src.modes.api_testing_mode.runtime import ApiTestingModeRuntime
from src.registry.tools import ToolRegistry
from src.schemas.tool_runtime import ModelToolCall


ROOT = Path(__file__).resolve().parents[2]


def failed_arguments():
    # Real model reply preserved in the next physical request, before correction.
    path = ROOT / 'docs/upgrade_2026-10-03/p4-default-coverage-first/physical/8ff44445c8aa4fb9b48151577f8fae63.request.json'
    body = json.loads(path.read_bytes())
    calls = [c for m in body['messages'] for c in m.get('tool_calls', [])]
    failed = next(c for c in calls if c['function']['name'] == 'api-test-runner')
    return json.loads(failed['function']['arguments'])


def test_registered_and_planning_task_contract_matches_actual_consumer():
    schema = ToolRegistry().get('api-test-runner').input_schema
    actual = ApiTestTask.model_json_schema()
    definitions = actual.pop('$defs')
    actual.pop('description')
    assert schema['$defs'] == definitions
    task = deepcopy(schema['properties']['task'])
    task.pop('description')
    assert task == actual
    assert set(task['required']) == {'task_id', 'method', 'path'}
    assert 'endpoint' in schema['properties']  # Existing campaign input stays available.
    assert capture_tool_contracts()['api-test-runner']['input_schema'] == schema
    def refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == '$ref':
                    found = schema
                    for token in child.removeprefix('#/').split('/'):
                        found = found[token]
                else:
                    refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)
    refs(schema)


@pytest.mark.asyncio
async def test_captured_invalid_payload_has_safe_known_failure_without_HTTP(regression_business_http, caplog):
    arguments = failed_arguments()
    assert {'endpoint', 'method', 'expected_status'} <= arguments['task'].keys()
    runtime = ApiTestingModeRuntime(api_docs_service=None)
    result = await runtime.handle(arguments, None)
    assert result['status'] == 'failed' and result['error'] == 'invalid_task_payload'
    assert result['http_request_sent'] is False
    assert result['validation_errors'] == [
        {'location': ['task_id'], 'type': 'missing'}, {'location': ['path'], 'type': 'missing'}]
    assert not regression_business_http[0][1] and not regression_business_http[2]
    assert 'api_task_payload_rejected' in caplog.text
    assert arguments['task']['endpoint'] not in caplog.text


@pytest.mark.asyncio
async def test_native_contract_executes_actual_create_query_and_cleanup(regression_business_http):
    (endpoint, effects, _), orders, created = regression_business_http
    runtime = ApiTestingModeRuntime(api_docs_service=None)
    async def send(task_id, method, path):
        return await runtime.handle({'worker_action': 'execute_task', 'task': {
            'task_id': task_id, 'method': method, 'path': path}}, None)
    first = await send('first', 'POST', endpoint + '/orders')
    second = await send('second', 'POST', endpoint + '/orders')
    ids = [r['task_result']['response_body']['order.id'] for r in [first, second]]
    assert ids == created and len(orders) == 2
    result = await send('query', 'GET', endpoint + '/orders/' + ids[0])
    body = result['task_result']['response_body']
    assert result['ok'] and result['task_result']['response_status'] == 200
    assert body['flag'] is False and body['count'] == 1 and body['nullable'] is None
    for index, identity in enumerate(ids):
        assert (await send('cleanup-' + str(index), 'DELETE', endpoint + '/orders/' + identity))['ok']
    assert not orders and [m for m, _ in effects] == ['POST', 'POST', 'GET', 'DELETE', 'DELETE']


@pytest.mark.asyncio
@LIVE
async def test_actual_PG_invalid_task_checkpoint_recovery_then_corrected_new_operation(tmp_path, regression_business_http, monkeypatch):
    monkeypatch.setattr(research, 'POLICY', POLICY)
    business = regression_business_http
    async with operation_runtime(tmp_path, business) as (values, runtime, tool, execution, arguments):
        invalid = deepcopy(arguments)
        invalid['task'] = failed_arguments()['task']
        call = ModelToolCall(id='captured-invalid', name=tool.key, arguments=invalid)
        result = await runtime.execute(tool, call, execution)
        assert result.status == 'failed' and result.output['error'] == 'invalid_task_payload'
        job = await values[0][1].get_job(result.job_id)
        assert job.status.value == 'failed'
        assert job.output_payload['http_request_sent'] is False
        recovered = await runtime.execute(tool, call.model_copy(update={'id': 'recovered-invalid'}), execution)
        assert recovered.status == 'failed' and not business[2] and not business[0][1]
        corrected = deepcopy(arguments)
        corrected['_case_action']['operation_key'] = 'seed.corrected-after-known-validation'
        result = await runtime.execute(tool, ModelToolCall(id='corrected', name=tool.key, arguments=corrected), execution)
        assert result.status == 'completed' and len(business[2]) == 1
        identity = business[2][0]
        clean = {'_case_action': {'item_key': 'cleanup', 'operation_key': 'cleanup.corrected'},
            'worker_action': 'execute_task', 'task': {'task_id': 'cleanup', 'method': 'DELETE', 'path': business[0][0] + '/orders/' + identity}}
        assert (await runtime.execute(tool, ModelToolCall(id='cleanup', name=tool.key, arguments=clean), execution)).status == 'completed'
        assert not business[1]
        await save_operation(tmp_path, 'invalid-task-checkpoint', values, business,
            captured_default_failure_replayed=True, known_validation_failed_without_HTTP=True,
            actual_PG_terminal_recovery_verified=True, actual_corrected_operation_and_cleanup_verified=True)
