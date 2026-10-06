"""Actual mutable business state research, including changed provider call IDs."""
from contextlib import asynccontextmanager
from dataclasses import replace
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from test_agent_case_execution import adapter_environment, approval_continuation, regression_business_http
from test_agent_case_session import ControlledAgent
from src.application.runtime.case_action_binding import ARGUMENT, CaseActionClaimUnavailable
from src.application.runtime.tool_runtime_service import ToolExecutionContext
from src.application.test_runs.agent_case_execution import AgentCaseApprovalWait
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.tool_runtime import ModelToolCall
from src.schemas.model_config import ModelInvocationResult
from src.registry.tools import ToolRegistry

LIVE = pytest.mark.skipif(os.getenv('RUN_LIVE_PLAN_RUN_CREATION') != '1',
    reason='actual isolated MySQL/PG/RustFS, original Agent and stateful HTTP')
POLICY = CasePlanningPolicy(action_binding_protocol='case-action-binding-v1',
    action_admission_protocol='case-action-admission-v1')


class BoundLifecycleAgent(ControlledAgent):
    async def invoke(self, key, request):
        result = await super().invoke(key, request)
        for call in result.tool_calls:
            call.arguments[ARGUMENT] = {'item_key':call.id}
        return result


class ChangedProviderLifecycleAgent(BoundLifecycleAgent):
    def __init__(self):
        super().__init__()
        self.repeated = False
        self.actual_requests = []

    async def invoke(self,key,request):
        self.actual_requests.append(request.model_copy(deep=True))
        previous = [json.loads(m['content']) for m in request.messages if m.get('role') == 'tool']
        if len(previous) == 1 and not self.repeated:
            self.repeated = True
            original = next(call for message in request.messages for call in message.get('tool_calls',[])
                if call['id'] == 'seed')
            return ModelInvocationResult(text='Recover the same preparation operation under a new provider call ID.',
                tool_calls=[ModelToolCall(id='seed-recovery',name='api-test-runner',
                    arguments=deepcopy(original['arguments']))],
                response_summary={'mode':'ok','finish_reason':'tool_calls'})
        # The controlled Agent then continues the same target/cleanup sequence.
        # Retain every actual request above; this local decision view is not wire evidence.
        messages = [m for m in request.messages
            if not (m.get('role') == 'tool' and m.get('tool_call_id') == 'seed-recovery')
            and not any(call.get('id') == 'seed-recovery' for call in m.get('tool_calls',[]))]
        return await super().invoke(key,request.model_copy(update={'messages':messages}))


async def save_research(tmp_path, name, values, business, **extra):
    env, _, _, _, runs, agent, _, _, _, services = values
    base = services['environment']
    source = tmp_path/'assessment-case.json'
    directory = Path(os.environ['QA_STATEFUL_RESEARCH_EVIDENCE_DIR'])/name
    directory.mkdir(parents=True, exist_ok=False)
    directory.joinpath(source.name).write_bytes(source.read_bytes())
    jobs = await env[1].list_jobs()
    value = {'research_baseline':'5102413d',
        'actual_run':(await runs.get(env[0].run_id)).model_dump(mode='json'),
        'actual_MySQL_policy':(await base.service.get_policy(env[0].project_id)).model_dump(mode='json'),
        'original_agent_context':env[0].execution_json(),
        'actual_tool_jobs':[job.model_dump(mode='json') for job in jobs],
        'actual_HTTP_requests':[list(entry) for entry in business[0][1]],
        'actual_business_orders':deepcopy(business[1]), 'actual_created_ids':list(business[2]),
        'model_requests':[request.model_dump(mode='json') for request in getattr(agent,'actual_requests',agent.requests)],
        'controlled_Agent':True,'autonomous_external_Agent_verified':False,
        'logical_operation_identity_implemented':False,'full_P0_P5_complete':False, **extra}
    directory.joinpath('verification.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return value


@pytest.mark.asyncio
@LIVE
async def test_actual_new_admission_stateful_creation_binding_failure_and_cleanup(tmp_path, regression_business_http):
    business = regression_business_http
    async with adapter_environment(tmp_path,business[0],live=True,agent=BoundLifecycleAgent(),
        planning_policy=POLICY) as values:
        env, _, reader, _, runs, agent, adapter, args, _, _ = values
        await save_research(tmp_path,'stateful-lifecycle-input',values,business,
            original_graph_and_approvals_verified=False, actual_stateful_cleanup_verified=False)
        pending = await adapter().execute(**await args())
        assert isinstance(pending,AgentCaseApprovalWait) and not business[2]
        for _ in range(3):
            pending = await approval_continuation(runs,reader,adapter,args,pending)
        assert not isinstance(pending,AgentCaseApprovalWait)
        result = await runs.complete_item(env[0].run_item_id,pending.completion)
        saved = await save_research(tmp_path,'stateful-lifecycle',values,business,
            business_failure_was_not_hidden=True, actual_stateful_cleanup_verified=True,
            effects_ownership_and_cleanup_state_machine_implemented=False)
        created = business[2][0]
        assert business[0][1] == [('POST','/orders'),('GET','/orders/'+created),('DELETE','/orders/'+created)]
        assert len(business[2]) == 1 and not business[1]
        assert result.status == 'failed' and result.actual['case_output']['body']['count'] == 1
        assert result.actual['preparations']['seed']['status'] == result.actual['preparations']['cleanup']['status'] == 'passed'
        assert saved['actual_MySQL_policy']['policy']['action_admission_protocol'] == POLICY.action_admission_protocol
        assert len(saved['actual_tool_jobs']) == 3
        assert [job['metadata']['case_action_binding']['phase'] for job in saved['actual_tool_jobs']] == ['after','case','before']


@pytest.mark.asyncio
@LIVE
async def test_actual_original_agent_changed_provider_call_must_not_repeat_preparation_or_leak_order(tmp_path,regression_business_http):
    business = regression_business_http
    async with adapter_environment(tmp_path,business[0],live=True,agent=ChangedProviderLifecycleAgent(),
        planning_policy=POLICY) as values:
        env, _, reader, _, runs, agent, adapter, args, _, _ = values
        await save_research(tmp_path,'original-agent-input',values,business,
            original_graph_and_approvals_verified=False, actual_stateful_cleanup_verified=False)
        pending = await adapter().execute(**await args())
        for _ in range(4):
            assert isinstance(pending,AgentCaseApprovalWait)
            pending = await approval_continuation(runs,reader,adapter,args,pending)
        result = await runs.complete_item(env[0].run_item_id,pending.completion)
        jobs = await env[1].list_jobs()
        original = next(job for job in jobs if job.call_id == 'seed')
        retry = next(job for job in jobs if job.call_id == 'seed-recovery')
        assert original.input_payload == retry.input_payload
        assert original.metadata['case_action_binding']['item_key'] == retry.metadata['case_action_binding']['item_key'] == 'seed'
        saved = await save_research(tmp_path,'original-agent-changed-call',values,business,
            original_provider_job=original.model_dump(mode='json'),retry_provider_job=retry.model_dump(mode='json'),
            same_task_id_and_arguments=True, original_graph_and_approvals_verified=True,
            actual_target_result_status=result.status, cleanup_used_original_created_id=True,
            actual_stateful_cleanup_verified=False, orphan_order_ids=list(business[1]))
        assert len(business[2]) == 1 and not business[1], 'Original Agent repeated the preparation under a new call_id and leaked its second order'


@asynccontextmanager
async def direct_original_action(tmp_path,business):
    async with adapter_environment(tmp_path,business[0],live=True,agent=BoundLifecycleAgent(),
        planning_policy=POLICY) as values:
        env, _, _, _, _, _, adapter, args, _, services = values
        current = await args()
        context, checkpoint, attempt = await adapter()._load(run=current['run'],item=current['item'],
            version=current['version'],attempt_id=current['attempt_id'])
        await adapter()._save(current['item'],attempt,checkpoint.model_copy(update={'phase':'running'}))
        execution = ToolExecutionContext(session_id=checkpoint.session_id,turn_id=checkpoint.turn_id,
            trace_id='stateful-recovery-research',user_message=context.objective,normalized_input=context.objective,
            context_bundle={**context.runtime_context(),'case_execution_managed':True,
                'attempt_id':checkpoint.execution_attempt_id})
        runtime = services['tool_runtime']
        tool = ToolRegistry().get('api-test-runner')
        arguments = {ARGUMENT:{'item_key':'seed'},'worker_action':'execute_task',
            'task':{'task_id':'original-create-order','method':'POST','path':business[0][0]+'/orders',
                'request_body':{'flag':False,'count':0,'nullable':None}}}
        yield values, runtime, tool, execution, arguments


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize('changed_call_id',[False,True])
async def test_actual_lost_create_return_must_not_create_another_order(tmp_path,regression_business_http,changed_call_id):
    business = regression_business_http
    async with direct_original_action(tmp_path,business) as (values,runtime,tool,execution,arguments):
        jobs = values[0][1]
        original = runtime._handlers[tool.key]
        async def lose_return(raw, context):
            await original(raw,context)  # The actual HTTP server creates the order before returning.
            raise ConnectionError('actual create returned, result lost before durable observation')
        runtime._handlers[tool.key] = lose_return
        request = ModelToolCall(id='provider-original',name=tool.key,arguments=deepcopy(arguments))
        try:
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool,request,execution)
        finally:
            runtime._handlers[tool.key] = original
        first = (await jobs.list_jobs(session_id=execution.session_id))[0]
        assert first.status.value == 'running' and not first.output_payload
        assert len(business[2]) == 1 and len(business[1]) == 1
        before = deepcopy(business[1])
        if changed_call_id:
            request = request.model_copy(update={'id':'provider-recovery'},deep=True)
            try:
                retry = await runtime.execute(tool,request,execution)
                retry_outcome = retry.model_dump(mode='json')
            except CaseActionClaimUnavailable as exc:
                retry_outcome = {'status':'reconciliation_required','error_type':type(exc).__name__}
        else:
            with pytest.raises(CaseActionClaimUnavailable):
                await runtime.execute(tool,request,replace(execution,tool_job_id=first.id))
            retry_outcome = {'status':'reconciliation_required'}
        await save_research(tmp_path,'changed-provider-call' if changed_call_id else 'same-original-job',values,business,
            first_unknown_job=first.model_dump(mode='json'),server_state_before_retry=before,
            original_arguments_preserved=request.arguments == arguments,
            retry_outcome=retry_outcome,
            provider_call_id_changed=changed_call_id, actual_stateful_cleanup_verified=False,
            researched_requirement='unknown original creation must not be resent under a new provider call ID')
        assert len(business[2]) == 1 and len(business[1]) == 1, 'Changed provider call_id repeated the unknown preparation POST'
        assert await jobs.get_job(first.id) == first
