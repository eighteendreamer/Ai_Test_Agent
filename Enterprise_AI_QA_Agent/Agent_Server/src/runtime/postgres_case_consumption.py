"""Close a declared business phase in the original action-claim transaction."""
import asyncio
from copy import deepcopy
import json
import logging

from src.application.runtime.case_action_binding import CONSUMPTION_PROTOCOL, CaseActionBinding, _digest
from src.application.runtime.case_effect_relations import compile_relations, definition_selection
from src.application.test_runs.agent_case_assessment import AgentCaseAssessmentService, SETTLED_EVIDENCE_STATUSES
from src.infrastructure.postgres_runtime import postgres_connect
from src.infrastructure.postgres_identifiers import derived_tool_table
from src.runtime.postgres_tool_job_store import _job_from_row
from src.schemas.scene_receipt import consumption_closure_type
from src.schemas.tool_job import ToolOutputObservation

logger = logging.getLogger(__name__)


def action_role(context, job):
    key = job.metadata['case_action_binding']['item_key']
    step = next(step for step in context.steps if step.item.key == key)
    definitions = compile_relations(step.item, step.content_json, context.evidence, context.effect_expected_source_protocol)
    roles = {d['role'] for d in definitions if definition_selection(d, job.input_payload) == 'matched'}
    return 'conflict' if len(roles) > 1 else 'cleanup' if roles == {'cleanup'} else 'ordinary'


class PostgresCaseConsumption:
    def __init__(self, admission):
        self._admission = admission
        self._settings = admission._settings
        self.table = derived_tool_table(admission._table, "case_end")

    async def initialize(self):
        def create():
            with postgres_connect(self._settings) as conn, conn.cursor() as cur:
                cur.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    run_item_id UUID PRIMARY KEY REFERENCES {self._admission._runs._item_table}(id),
                    closure_job_id TEXT UNIQUE NOT NULL REFERENCES {self._admission._table}(id),
                    record JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        await asyncio.to_thread(create)

    @staticmethod
    def action_finished(job):
        if job.status not in SETTLED_EVIDENCE_STATUSES:
            return False
        if job.started_at is None:
            return True
        # Original cancellation/failed status cannot resolve an unknown send.
        # A delivery interruption may change final output; the observation is
        # the original handler-return checkpoint, not a passing assertion.
        try:
            ToolOutputObservation.model_validate(job.metadata.get('tool_output_observation'))
        except ValueError:
            logger.warning('case_consumption_return_unconfirmed',extra={'tool_job_id':job.id,
                'run_item_id':job.metadata.get('run_item_id'),'status':job.status.value})
            return False
        return True

    def read(self, cur, item_id):
        cur.execute(f'SELECT record FROM {self.table} WHERE run_item_id=%s', (item_id,))
        row = cur.fetchone()
        if not row:
            return None
        record = row['record']
        if record.get('version') != CONSUMPTION_PROTOCOL or record.get('sha256') != _digest({k:v for k,v in record.items() if k != 'sha256'}):
            raise ValueError('原消费结束记录的协议或SHA不同')
        origin = self._admission._read_job(cur, record['closure_job_id'])
        if (origin.metadata.get('case_action_binding') != record['closing_binding'] or
            _digest(origin.input_payload.get('_case_action', {}).get('consumption_closure')) != record['closure_payload_sha256']):
            raise ValueError('消费结束记录与原关闭调用不同')
        for ref in record['normal_job_refs']:
            job = self._admission._read_job(cur, ref['job_id'])
            if (job.call_id != ref['call_id'] or job.status.value != ref['status'] or
                _digest(job.output_payload) != ref['output_sha256'] or
                _digest(job.metadata.get('case_action_binding')) != ref['binding_sha256']):
                raise ValueError('消费结束的原普通动作证据已变化')
            if not self.action_finished(job):
                raise ValueError('原消费关闭记录含有未确认结束的动作')
            if ('return_observation_sha256' in ref and
                ref['return_observation_sha256'] != _digest(job.metadata.get('tool_output_observation'))):
                raise ValueError('消费结束的原可靠返回观察已变化')
        return record

    def prepare(self, cur, *, context, job, binding, detail, now):
        if context.consumption_protocol is None:
            return None, None
        existing = self.read(cur, context.run_item_id)
        if existing and (existing['context_sha256'] != binding.context_sha256 or
            existing['project_id'] != context.project_id or existing['run_id'] != context.run_id or
            existing['plan_id'] != context.plan_id or existing['target_key'] != context.target_key):
            raise ValueError('消费结束记录与原固定执行上下文不同')
        role = action_role(context, job)
        payload = job.input_payload['_case_action'].get('consumption_closure')
        if role == 'conflict':
            return None, 'case_consumption_role_conflict'
        if role != 'cleanup':
            return None, 'case_consumption_closed' if existing else 'case_consumption_not_cleanup' if payload is not None else None
        if existing and payload is not None and existing['closure_payload_sha256'] != _digest(payload):
            return None, 'case_consumption_closure_conflict'
        if not existing and payload is None:
            return None, 'case_consumption_receipt_required'
        candidate = existing
        if candidate is None:
            try:
                candidate = self._candidate(cur, context, job, binding, payload, now)
            except ValueError as exc:
                logger.warning('case_consumption_receipt_rejected', extra={'tool_job_id':job.id,
                    'run_item_id':context.run_item_id, 'reason':str(exc)})
                return None, 'case_consumption_receipt_invalid'
        sources = [scope for scope in (context.preparation_scopes or {}).values() if binding.item_key in scope.cleanup_keys]
        if not sources:
            return None, 'case_consumption_scope_missing'
        items = {item.plan_item_key:item for item in detail.items}
        results = {result.id:result for result in detail.results}
        consumers = []
        for key in dict.fromkeys(key for scope in sources for key in scope.consumer_keys):
            item = items.get(key)
            if item is None:
                raise ValueError('消费范围缺少原正式运行条目')
            if item.id == context.run_item_id:
                consumers.append({'item_key':key, 'run_item_id':item.id, 'closure_sha256':candidate['sha256']})
                continue
            result = results.get(item.result_id)
            if (item.status not in {'passed','failed','error','blocked','skipped','cancelled'} or result is None or
                result.run_id != context.run_id or result.run_item_id != item.id or result.status != item.status or
                result.case_id != item.case_id or result.case_version_id != item.case_version_id):
                return None, 'case_consumption_consumer_pending'
            cur.execute(f"""SELECT * FROM {self._admission._table} WHERE metadata->>'run_item_id'=%s
                AND started_at IS NOT NULL ORDER BY created_at,id""", (item.id,))
            actions = []
            for row in cur.fetchall():
                original = _job_from_row(row)
                original_binding = CaseActionBinding.model_validate(original.metadata.get('case_action_binding'))
                if any(getattr(original_binding,key) != value for key,value in {
                    'project_id':context.project_id,'run_id':context.run_id,'plan_id':context.plan_id,
                    'run_item_id':item.id}.items()):
                    raise ValueError('外部消费者原动作不属于固定项目、运行或条目')
                self._admission._check_prior_receipt(original,original_binding)
                self._admission.operations.validate_job_in_transaction(cur,original)
                if not self.action_finished(original):
                    return None, 'case_consumption_consumer_unknown'
                actions.append({'job_id':original.id,'status':original.status.value,
                    'output_sha256':_digest(original.output_payload),
                    'return_observation_sha256':_digest(original.metadata.get('tool_output_observation'))})
            consumers.append({'item_key':key, 'run_item_id':item.id, 'result_id':result.id,
                'result_sha256':_digest(result.model_dump(mode='json')), 'status':item.status,'started_actions':actions})
        proof = {'version':CONSUMPTION_PROTOCOL, 'closure_sha256':candidate['sha256'],
            'source_keys':[scope.source_key for scope in sources], 'consumers':consumers,
            'scope':'explicit_completed_consumption_phase', 'run_item_completed':False}
        return {'new_record':candidate if existing is None else None, 'proof':proof}, None

    def _candidate(self, cur, context, job, binding, payload, now):
        closing = consumption_closure_type(context.scene_receipt_protocol).model_validate(payload)
        receipt = closing.receipt
        if (len(receipt.steps) != 1 or receipt.steps[0].item_key != context.target_key or
            not receipt.steps[0].checks or receipt.steps[0].status not in {'passed','failed'}):
            raise ValueError('消费结束回执需要当前正式项的确证通过或失败，不能省略检查')
        cur.execute(f"""SELECT * FROM {self._admission._table} WHERE session_id=%s AND turn_id=%s
            ORDER BY created_at,id""", (job.session_id,job.turn_id))
        jobs = {}
        bundle = {**context.runtime_context(), 'case_execution_managed':True, 'attempt_id':binding.attempt_id}
        for row in cur.fetchall():
            original = _job_from_row(row)
            selected = self._admission._binding(original, bundle, context)
            self._admission._check_prior_receipt(original, selected)
            self._admission.operations.validate_job_in_transaction(cur, original)
            if action_role(context, original) != 'ordinary':
                continue
            if not self.action_finished(original):
                raise ValueError('普通动作仍未结束或结果未知: '+original.id)
            if original.call_id in jobs:
                raise ValueError('消费回执原调用身份重复')
            jobs[original.call_id] = original
        target = next(step for step in context.steps if step.item.key == context.target_key)
        assessment = AgentCaseAssessmentService(None)._assess_loaded_steps(context=context, receipt=receipt,
            jobs=jobs, steps={context.target_key:target}, session_id=job.session_id, turn_id=job.turn_id,
            attempt_id=binding.attempt_id, model_key=job.metadata.get('selected_model_key',''),
            requested_item_keys=[context.target_key], inventory=[], coverage=None)
        verification = assessment.verification_results[0]
        if verification.status.value not in {'passed','failed'} or not assessment.used_jobs:
            raise ValueError('消费结束检查没有完整且可靠的原证据')
        record = {'version':CONSUMPTION_PROTOCOL, 'project_id':context.project_id, 'run_id':context.run_id,
            'plan_id':context.plan_id, 'run_item_id':context.run_item_id, 'target_key':context.target_key,
            'context_sha256':binding.context_sha256, 'closing_binding':binding.model_dump(mode='json'),
            'closure_job_id':job.id, 'closure_payload_sha256':_digest(payload), 'reason':closing.reason,
            'receipt':receipt.model_dump(mode='json'), 'assessment':deepcopy(assessment.results),
            'verification':verification.model_dump(mode='json'), 'normal_job_refs':[{'job_id':j.id,
                'call_id':j.call_id, 'status':j.status.value, 'output_sha256':_digest(j.output_payload),
                'binding_sha256':_digest(j.metadata['case_action_binding']),
                'return_observation_sha256':_digest(j.metadata.get('tool_output_observation'))} for j in jobs.values()],
            'closed_at':now.isoformat(), 'run_item_completed':False}
        raw = json.dumps(record,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
        if len(raw) > context.effect_coverage_max_bytes:
            raise ValueError('消费结束证据超出原固定证据预算')
        return {**record,'sha256':_digest(record)}

    def save(self, cur, prepared):
        record = prepared['new_record']
        if record is not None:
            # No upstream counterpart (checked: original operation registration,
            # fixed scopes, receipt kernel and Agent trajectory course): explicit
            # phase closure must share the cleanup claim; it is not RunItem completion.
            cur.execute(f'INSERT INTO {self.table}(run_item_id,closure_job_id,record) VALUES(%s,%s,%s::jsonb)',
                (record['run_item_id'],record['closure_job_id'],json.dumps(record,ensure_ascii=False,allow_nan=False)))
            logger.info('case_consumption_closed',extra={'run_id':record['run_id'],
                'run_item_id':record['run_item_id'], 'closure_job_id':record['closure_job_id'],
                'closure_sha256':record['sha256'], 'normal_job_count':len(record['normal_job_refs']),
                'run_item_completed':False})
