"""Recover the actual failed stateful input through the original execution system."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os

import pytest

import test_case_stateful_recovery_research as research
from test_agent_case_execution import regression_business_http
from src.application.runtime.case_action_binding import ARGUMENT, action_input_schema, _digest
from src.application.runtime.case_operation import RECEIPT_KEY
from src.infrastructure.postgres_runtime import postgres_connect
from src.schemas.case_planning import CasePlanningPolicy
from src.schemas.tool_runtime import ModelToolCall
from src.registry.tools import ToolRegistry

PROTOCOL = 'case-operation-v1'
POLICY = CasePlanningPolicy(action_binding_protocol='case-action-binding-v1',
    action_admission_protocol='case-action-admission-v1',operation_protocol=PROTOCOL)
LIVE = research.LIVE
BaseBound = research.BoundLifecycleAgent
BaseChanged = research.ChangedProviderLifecycleAgent
base_direct = research.direct_original_action
base_save = research.save_research


class OperationAgent(BaseBound):
    async def invoke(self,key,request):
        result = await super().invoke(key,request)
        for call in result.tool_calls:
            call.arguments[ARGUMENT]['operation_key'] = call.arguments[ARGUMENT]['item_key']+'.request'
        return result


class ChangedOperationAgent(BaseChanged):
    async def invoke(self,key,request):
        result = await super().invoke(key,request)
        for call in result.tool_calls:
            call.arguments[ARGUMENT].setdefault('operation_key',call.arguments[ARGUMENT]['item_key']+'.request')
        return result


@asynccontextmanager
async def operation_runtime(tmp_path,business):
    async with base_direct(tmp_path,business) as (values,runtime,tool,execution,arguments):
        arguments[ARGUMENT]['operation_key'] = 'seed.request'
        yield values,runtime,tool,execution,arguments


async def save_operation(tmp_path,name,values,business,**extra):
    jobs = values[0][1]
    store = jobs._case_action_admission.operations
    with postgres_connect(store._settings) as conn,conn.cursor() as cur:
        cur.execute(f'SELECT record FROM {store.table} ORDER BY operation_id')
        indexes = [row['record'] for row in cur.fetchall()]
    extra.update(logical_operation_identity_implemented=True,operation_protocol=PROTOCOL,
        actual_operation_indexes=indexes,effects_ownership_and_cleanup_state_machine_implemented=False,
        research_baseline='253d47f0',failure_input_baseline='5102413d')
    if name == 'original-agent-changed-call':
        extra['actual_stateful_cleanup_verified'] = len(business[2])==1 and not business[1]
    return await base_save(tmp_path,name,values,business,**extra)


@pytest.fixture(autouse=True)
def enable_protocol(monkeypatch):
    monkeypatch.setattr(research,'POLICY',POLICY)
    monkeypatch.setattr(research,'BoundLifecycleAgent',OperationAgent)
    monkeypatch.setattr(research,'ChangedProviderLifecycleAgent',ChangedOperationAgent)
    monkeypatch.setattr(research,'direct_original_action',operation_runtime)
    monkeypatch.setattr(research,'save_research',save_operation)


def test_old_policy_wire_omits_operation_and_prerequisites_are_required():
    assert 'operation_protocol' not in CasePlanningPolicy().model_dump(mode='json')
    with pytest.raises(ValueError,match='逻辑操作恢复'):
        CasePlanningPolicy(operation_protocol=PROTOCOL)
    assert POLICY.model_dump(mode='json')['operation_protocol']==PROTOCOL


@pytest.mark.asyncio
@LIVE
async def test_actual_original_agent_changed_call_recovers_first_order_and_cleans_it(tmp_path,regression_business_http):
    await research.test_actual_original_agent_changed_provider_call_must_not_repeat_preparation_or_leak_order(
        tmp_path,regression_business_http)


@pytest.mark.asyncio
@LIVE
@pytest.mark.parametrize('changed_call_id',[False,True])
async def test_actual_unknown_creation_is_not_resent_across_provider_calls(tmp_path,regression_business_http,changed_call_id):
    await research.test_actual_lost_create_return_must_not_create_another_order(tmp_path,regression_business_http,changed_call_id)


@pytest.mark.asyncio
@LIVE
async def test_actual_new_operation_same_input_and_input_conflict_are_distinguished(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        def call(id,args):return ModelToolCall(id=id,name=tool.key,arguments=deepcopy(args))
        first = await runtime.execute(tool,call('first',arguments),execution)
        conflict_args=deepcopy(arguments);conflict_args['task']['request_body']['count']=1
        conflict = await runtime.execute(tool,call('changed-input',conflict_args),execution)
        assert conflict.status=='denied' and conflict.output['error']=='case_operation_input_conflict'
        assert len(business[2])==1
        new_args=deepcopy(arguments);new_args[ARGUMENT]['operation_key']='seed.deliberate-second'
        second = await runtime.execute(tool,call('deliberate-new',new_args),execution)
        assert second.status=='completed' and len(business[2])==2
        for i,id in enumerate(list(business[1])):
            clean={ARGUMENT:{'item_key':'cleanup','operation_key':'cleanup.'+str(i)},'worker_action':'execute_task',
                'task':{'task_id':'cleanup-'+str(i),'method':'DELETE','path':business[0][0]+'/orders/'+id}}
            assert (await runtime.execute(tool,call('clean-'+str(i),clean),execution)).status=='completed'
        assert not business[1]
        await save_operation(tmp_path,'deliberate-second-and-input-conflict',values,business,
            deliberate_second_same_business_input_verified=True,all_actual_created_orders_removed=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_four_new_calls_share_one_original_creation(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        # Create all original call records; their executions contend in the shared PG claim.
        bound=execution.bind_call(ModelToolCall(id='scope',name=tool.key,arguments=deepcopy(arguments)))
        jobs=values[0][1]
        pending=[await jobs.create_job(tool=tool,call_id='race-'+str(i),session_id=execution.session_id,
            turn_id=execution.turn_id,trace_id=execution.trace_id,input_payload=deepcopy(arguments),
            metadata=bound.job_metadata()) for i in range(4)]
        claims=await asyncio.gather(*(jobs.claim_execution(j.id,context_bundle=execution.context_bundle) for j in pending))
        running=[j for j in claims if j.status.value=='running']
        assert len(running)==1
        from dataclasses import replace
        origin=running[0]
        record=await runtime.execute(tool,ModelToolCall(id=origin.call_id,name=tool.key,arguments=deepcopy(arguments)),
            replace(execution,tool_job_id=origin.id,claimed_job=origin))
        assert record.status=='completed' and len(business[2])==1
        assert len([j for j in claims if j.status.value=='denied'])==3
        await save_operation(tmp_path,'four-call-race',values,business,one_original_claim_verified=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_replay_preserves_origin_and_marks_HTTP_as_reused(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        jobs=values[0][1]
        call=ModelToolCall(id='original',name=tool.key,arguments=deepcopy(arguments))
        first=await runtime.execute(tool,call,execution);origin=await jobs.get_job(first.job_id)
        second=await runtime.execute(tool,call.model_copy(update={'id':'recovery'},deep=True),execution)
        alias=await jobs.get_job(second.job_id)
        await save_operation(tmp_path,'replay-provenance-input',values,business,
            before_provenance_assertions=True,original_job=origin.model_dump(mode='json'),
            alias_job=alias.model_dump(mode='json'),original_record=first.model_dump(mode='json'),
            alias_record=second.model_dump(mode='json'))
        assert alias.started_at is None and alias.input_payload==origin.input_payload
        assert await jobs.get_job(origin.id)==origin and len(business[0][1])==1
        receipt=await jobs.validate_case_operation(alias)
        assert receipt['action_sent'] is False and receipt['origin_job_id']==origin.id
        assert receipt['origin_output_sha256']==_digest(origin.output_payload)
        def observations(value):
            if isinstance(value,dict):
                for key,child in value.items():
                    if key=='http_observation_v1':yield child['acquisition']
                    else:yield from observations(child)
            elif isinstance(value,list):
                for child in value:yield from observations(child)
        assert set(observations(origin.output_payload))=={'network'}
        assert set(observations(alias.output_payload))=={'completed_request'}
        assert second.output[RECEIPT_KEY]['decision']=='replayed'
        await save_operation(tmp_path,'replay-provenance',values,business,
            origin_raw_unchanged=True,alias_HTTP_is_completed_request=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_completed_read_replay_and_deliberate_refresh_are_distinct(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        first=await runtime.execute(tool,ModelToolCall(id='create',name=tool.key,arguments=deepcopy(arguments)),execution)
        id=business[2][0]
        read={ARGUMENT:{'item_key':'target','operation_key':'target.read'},'worker_action':'execute_task',
            'task':{'task_id':'read','method':'GET','path':business[0][0]+'/orders/'+id}}
        def call(key,args):return ModelToolCall(id=key,name=tool.key,arguments=deepcopy(args))
        observed=await runtime.execute(tool,call('read',read),execution)
        replay=await runtime.execute(tool,call('read-recovery',read),execution)
        assert len(business[0][1])==2
        read[ARGUMENT]['operation_key']='target.refresh'
        refreshed=await runtime.execute(tool,call('refresh',read),execution)
        assert len(business[0][1])==3 and [r.status for r in [observed,replay,refreshed]]==['completed']*3
        assert replay.output[RECEIPT_KEY]['action_sent'] is False
        assert refreshed.output[RECEIPT_KEY]['execution_claimed'] is True
        assert refreshed.output[RECEIPT_KEY]['action_sent'] is None
        await save_operation(tmp_path,'read-refresh',values,business,same_read_reused=True,deliberate_read_refreshed=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_observed_return_recovers_across_new_call_without_sending(tmp_path,regression_business_http):
    class ProcessStopped(BaseException):pass
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        jobs=values[0][1];original_handler=runtime._handlers[tool.key]
        async def stopped(raw,context):
            returned=await original_handler(raw,context)
            output=runtime._normalize_result(tool,returned,context=context)
            output,_,_=runtime._output_safety_policy.sanitize_tool_output(output)
            await jobs.observe_output(context.tool_job_id,status=runtime._resolve_result_status(output),output_payload=output)
            raise ProcessStopped('Actual returned output saved; process stopped before delivery')
        runtime._handlers[tool.key]=stopped
        call=ModelToolCall(id='observed-original',name=tool.key,arguments=deepcopy(arguments))
        try:
            with pytest.raises(ProcessStopped):await runtime.execute(tool,call,execution)
        finally:runtime._handlers[tool.key]=original_handler
        before=(await jobs.list_jobs())[0]
        assert before.status.value=='running' and before.metadata['tool_output_observation']
        recovered=await runtime.execute(tool,call.model_copy(update={'id':'observed-recovery'},deep=True),execution)
        assert recovered.status=='partial' and len(business[0][1])==1 and len(business[2])==1
        origin=await jobs.get_job(before.id)
        assert origin.status.value=='partial' and origin.metadata['tool_output_observation']==before.metadata['tool_output_observation']
        assert origin.output_payload['task_result']['response_body']==before.output_payload['task_result']['response_body']
        await save_operation(tmp_path,'observed-return-recovery',values,business,
            original_observed_return=before.model_dump(mode='json'),no_new_HTTP_after_delivery_recovery=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_configuration_change_does_not_replace_frozen_operation_protocol(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        context=values[0][0];base=values[9]['environment']
        path=f'/projects/{context.project_id}/case-plans/policy'
        current=await base.client.get(path)
        assert current.status_code==200 and current.json()['policy']['operation_protocol']==PROTOCOL
        saved=await base.client.put(path,json=CasePlanningPolicy().model_dump(mode='json'))
        assert saved.status_code==200 and 'operation_protocol' not in saved.json()['policy']
        call=ModelToolCall(id='frozen',name=tool.key,arguments=deepcopy(arguments))
        first=await runtime.execute(tool,call,execution)
        second=await runtime.execute(tool,call.model_copy(update={'id':'frozen-recovery'},deep=True),execution)
        assert len(business[0][1])==1 and second.output[RECEIPT_KEY]['decision']=='replayed'
        assert execution.context_bundle['case_execution']['operation_protocol']==PROTOCOL
        schema=action_input_schema(tool.input_schema,execution.context_bundle)
        assert 'operation_key' in schema['properties'][ARGUMENT]['required']
        await save_operation(tmp_path,'frozen-policy-recovery',values,business,
            actual_policy_API_before=current.json(),actual_policy_API_after=saved.json(),frozen_protocol_unchanged=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_lost_PG_claim_reply_preserves_index_and_does_not_send_new_call(tmp_path,regression_business_http,monkeypatch):
    from contextlib import contextmanager
    import src.runtime.postgres_case_action_admission as admission
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        original_connect=admission.postgres_connect
        @contextmanager
        def lost_commit_reply(settings):
            with original_connect(settings) as connection:
                yield connection
            raise ConnectionError('Actual PG operation index and claim committed; reply lost')
        call=ModelToolCall(id='lost-claim',name=tool.key,arguments=deepcopy(arguments))
        with monkeypatch.context() as patch:
            patch.setattr(admission,'postgres_connect',lost_commit_reply)
            with pytest.raises(research.CaseActionClaimUnavailable):await runtime.execute(tool,call,execution)
        jobs=values[0][1];before=(await jobs.list_jobs())[0]
        assert before.status.value=='running' and before.metadata[RECEIPT_KEY]['execution_claimed'] is True
        assert before.metadata[RECEIPT_KEY]['action_sent'] is None
        assert not business[0][1]
        retry=await runtime.execute(tool,call.model_copy(update={'id':'lost-claim-new-call'},deep=True),execution)
        assert retry.status=='denied' and retry.output['error']=='case_operation_outcome_unknown'
        assert await jobs.get_job(before.id)==before and not business[0][1]
        await save_operation(tmp_path,'lost-claim-reply',values,business,original_claim_commit_reply_lost=True,
            no_handler_after_unknown_claim=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_replay_output_tampering_is_rejected_without_HTTP(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        call=ModelToolCall(id='tamper-origin',name=tool.key,arguments=deepcopy(arguments))
        original=await runtime.execute(tool,call,execution)
        replay=await runtime.execute(tool,call.model_copy(update={'id':'tamper-alias'},deep=True),execution)
        jobs=values[0][1];alias=await jobs.get_job(replay.job_id)
        await save_operation(tmp_path,'output-tamper-input',values,business,
            original_record=original.model_dump(mode='json'),replay_record=replay.model_dump(mode='json'))
        assert original.status=='completed' and replay.status=='completed'
        assert isinstance(alias.output_payload['task_result']['response_body'],dict)
        damaged=alias.model_copy(deep=True);damaged.output_payload['task_result']['response_body']['count']=99
        with pytest.raises(ValueError,match='输出SHA不同'):
            await jobs.validate_case_operation(damaged)
        assert await jobs.get_job(alias.id)==alias and len(business[0][1])==1
        await save_operation(tmp_path,'output-tamper',values,business,altered_replay_output_rejected=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_replay_preserves_business_field_named_like_HTTP_protocol(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        await runtime.execute(tool,ModelToolCall(id='create-business-data',name=tool.key,arguments=deepcopy(arguments)),execution)
        id=business[2][0]
        business[1][id]['http_observation_v1']={'业务字段':'保留','count':0,'flag':False,'nullable':None}
        read={ARGUMENT:{'item_key':'target','operation_key':'target.read-business'},'worker_action':'execute_task',
            'task':{'task_id':'read-business','method':'GET','path':business[0][0]+'/orders/'+id}}
        first=await runtime.execute(tool,ModelToolCall(id='read-business',name=tool.key,arguments=deepcopy(read)),execution)
        try:
            second=await runtime.execute(tool,ModelToolCall(id='read-business-recovery',name=tool.key,arguments=deepcopy(read)),execution)
        except research.CaseActionClaimUnavailable:
            await save_operation(tmp_path,'business-HTTP-key-collision',values,business,
                valid_business_field_replay_rejected=True)
            raise
        jobs=values[0][1]
        origin=await jobs.get_job(first.job_id);alias=await jobs.get_job(second.job_id)
        assert origin.output_payload['task_result']['response_body']==alias.output_payload['task_result']['response_body']
        assert alias.status.value=='completed' and len(business[0][1])==2
        await save_operation(tmp_path,'business-HTTP-key-collision',values,business,
            valid_business_field_preserved=True,no_new_HTTP_for_recovery=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_graph_and_governance_explain_missing_operation_key_without_sending(tmp_path,regression_business_http):
    from dataclasses import replace
    from test_deep_agent_tool_replay import _context
    from src.application.permissions.permission_service import PermissionService
    from src.application.runtime.tool_governance_service import ToolGovernanceService
    from src.graph.nodes.tool_executor import _resolve_tool_call
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        del arguments[ARGUMENT]['operation_key']
        call=ModelToolCall(id='missing-operation-key',name=tool.key,arguments=deepcopy(arguments))
        jobs=values[0][1];permissions=PermissionService();registry=ToolRegistry()
        service=ToolGovernanceService(tool_registry=registry,permission_service=permissions,
            tool_runtime_service=runtime,tool_job_service=jobs)
        governed=await service.execute(call,replace(_context(),execution=execution,
            active_mode_key='api_testing',available_tool_keys=frozenset({tool.key})))
        graph=await _resolve_tool_call({'event_log':[]},call,registry,permissions,runtime,jobs,execution)
        assert governed.record.status=='failed' and graph['tool_result']['status']=='failed'
        assert 'operation_key' in governed.record.summary and 'operation_key' in graph['tool_result']['summary']
        assert not await jobs.list_jobs() and not business[0][1]
        await save_operation(tmp_path,'missing-operation-key',values,business,
            actual_governance_rejection=governed.record.model_dump(mode='json'),actual_graph_rejection=graph,
            protocol_feedback_did_not_send_HTTP=True)


@pytest.mark.asyncio
@LIVE
async def test_actual_opaque_Unicode_operation_key_recovers_without_normalization(tmp_path,regression_business_http):
    business=regression_business_http
    async with operation_runtime(tmp_path,business) as (values,runtime,tool,execution,arguments):
        arguments[ARGUMENT]['operation_key']='创建本次订单 / 保留原值😀'
        call=ModelToolCall(id='unicode-original',name=tool.key,arguments=deepcopy(arguments))
        first=await runtime.execute(tool,call,execution)
        alias=await runtime.execute(tool,call.model_copy(update={'id':'unicode-recovery'},deep=True),execution)
        jobs=values[0][1];origin=await jobs.get_job(first.job_id)
        assert alias.status=='completed' and len(business[0][1])==1
        assert alias.output[RECEIPT_KEY]['scope']['operation_key']==arguments[ARGUMENT]['operation_key']
        assert (await jobs.get_job(alias.job_id)).input_payload==origin.input_payload
        await save_operation(tmp_path,'opaque-Unicode-key',values,business,opaque_key_value_preserved=True)
