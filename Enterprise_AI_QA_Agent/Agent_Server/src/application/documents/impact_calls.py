"""Fixed-scope impact requests and durable logical call/response recovery.

This service saves responses before candidate parsing. It does not infer final
scope coverage, review, deployment applicability or a successful test result.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import logging
from datetime import datetime, timezone

from src.application.documents.impact_batches import DocumentImpactBatches
from src.application.documents.impact_checkpoint_io import (
    ImpactCallBudgetExceeded, ImpactCallCheckpointUnavailable, confirmed_impact_write, store_impact_json,
)
from src.application.documents.impact_sources import canonical_impact_json, impact_record_hash
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.document_impact_calls import (
    DocumentImpactCallInput, DocumentImpactCallResponse,
    DocumentImpactCallSelection, DocumentImpactTokenEstimate, DocumentImpactUnit,
)
from src.schemas.model_config import MODEL_FAILURE_MODES, ModelInvocationRequest, model_invocation_parameters
from src.schemas.document_impact_workflow import DocumentImpactCoordinationContext
from src.schemas.document_impact_responses import DocumentImpactResponseAssessment

logger = logging.getLogger(__name__)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class DocumentImpactCallService:
    # No upstream counterpart (checked: impact_sources, impact_batches,
    # planning_workflow and local Agent trajectory course): impact has a new
    # scope unit contract, while persistence, lease fencing and SDK reuse follow existing paths.
    def __init__(self, *, tasks, store, models, objects, projects):
        self._tasks, self._store, self._models = tasks, store, models
        self._objects, self._projects = objects, projects

    @staticmethod
    def units(manifest):
        return [DocumentImpactUnit(kind="segment", id=item.id) for item in manifest.segments] + [
            DocumentImpactUnit(kind="difference", id=f"{item.kind}:{item.index}") for item in manifest.differences] + [
            DocumentImpactUnit(kind="case", id=item.version_id) for item in manifest.cases]

    @staticmethod
    def estimate(seed, request):
        snapshot, context = seed.invocation_snapshot, seed.analysis_context
        if snapshot is None or context is None:
            raise ValueError("历史影响任务未冻结模型调用策略，请创建新任务")
        policy = snapshot.config.payload
        contents = [request.system_prompt, *[message["content"] for message in request.messages]]
        if any(not isinstance(content, str) for content in contents) or request.tools:
            raise ValueError("影响范围调用只接受固定文本消息，不执行工具")
        if policy.token_method == "tiktoken_content":
            import tiktoken
            if importlib.metadata.version("tiktoken") != snapshot.tokenizer_version:
                raise ValueError("影响内容Tokenizer版本已变化，不能续写原任务")
            encoding = tiktoken.get_encoding(policy.encoding_name)
            content_count = sum(len(encoding.encode_ordinary(content)) for content in contents)
        else:
            content_count = sum(len(content.encode("utf-8")) for content in contents)
        size = len(_json({"system_prompt": request.system_prompt, "messages": request.messages}).encode("utf-8"))
        if size > context.config.payload.max_input_bytes:
            raise ImpactCallBudgetExceeded("完整影响批次请求超过字节预算，未截断", kind="input_bytes")
        input_estimate = content_count + policy.framing_allowance_tokens
        reserved = input_estimate + context.model.max_output_tokens
        if reserved > context.model.context_window:
            raise ImpactCallBudgetExceeded("影响批次内容估算与预留输出超过固定上下文窗口，未截断", kind="context_window")
        return DocumentImpactTokenEstimate(method=policy.token_method, encoding_name=policy.encoding_name,
            tokenizer_version=snapshot.tokenizer_version, content_tokens_or_estimate=content_count,
            framing_allowance_tokens=policy.framing_allowance_tokens, input_estimated_tokens=input_estimate,
            output_reserved_tokens=context.model.max_output_tokens, reserved_estimated_tokens=reserved,
            canonical_input_bytes=size)

    @staticmethod
    def _difference(seed, envelope, manifest, identity):
        kind, _, index = identity.partition(":")
        if not index.isdecimal() or str(int(index)) != index:
            raise ValueError("影响差异单元身份无效")
        return DocumentImpactBatches.read_difference(seed, envelope, manifest, kind, int(index))

    @classmethod
    def build_request(cls, seed, envelope, manifest, selection, *, previous=None, coordination_context=None):
        DocumentImpactBatches.validate(seed, envelope, manifest)
        if seed.analysis_context is None or seed.invocation_snapshot is None:
            raise ValueError("历史影响任务未冻结模型调用策略，请创建新任务")
        if seed.response_protocol is None:
            raise ValueError("历史影响任务未冻结响应协议，已保存调用仍可恢复；新调用请创建新任务")
        protocol = type(seed.response_protocol).model_validate(seed.response_protocol.model_dump(mode="json"))
        if selection.unit not in cls.units(manifest):
            raise ValueError("影响调用单元不属于固定完整范围")
        if selection.unit.kind == "segment":
            primary = DocumentImpactBatches.read_segment(seed, envelope, manifest, selection.unit.id)
        elif selection.unit.kind == "case":
            primary = DocumentImpactBatches.read_case(seed, envelope, manifest, selection.unit.id)
        else:
            primary = cls._difference(seed, envelope, manifest, selection.unit.id)
        reads = [DocumentImpactBatches.read_segment(seed, envelope, manifest, identity) for identity in selection.segment_ids]
        reads += [DocumentImpactBatches.read_case(seed, envelope, manifest, identity) for identity in selection.case_version_ids]
        reads += [cls._difference(seed, envelope, manifest, identity) for identity in selection.difference_ids]
        reads += [DocumentImpactBatches.read_range(seed, envelope, manifest, item.source_id,
            item.start_char, item.end_char) for item in selection.ranges]
        payload = {"input_sha256": envelope.input_sha256, "unit": selection.unit.model_dump(mode="json"),
            "project": seed.analysis_context.project.model_dump(mode="json"),
            "environment": seed.analysis_context.environment.model_dump(mode="json"),
            "primary": primary, "additional_fixed_reads": reads,
            "fixed_source_catalog": [source.model_dump(mode="json") for source in manifest.sources],
            # v2 requests retain their original catalog shape for fixed-history replay.
            # v3 supplies the existing verified record hash required by citations.
            "fixed_case_catalog": [case.model_dump(mode="json", include={"case_id", "version_id"} if
                protocol.version == "impact-call-v2" else {"case_id", "version_id", "content_hash", "version_record_sha256"})
                for case in manifest.cases],
            "fixed_difference_catalog": [{"id": f"{item.kind}:{item.index}", **item.model_dump(mode="json")}
                for item in manifest.differences],
            "source_gaps": [gap.model_dump(mode="json") for gap in manifest.source_gaps],
            "scope_counts": {"segments": len(manifest.segments), "differences": len(manifest.differences), "cases": len(manifest.cases)},
            "analysis_protocol": protocol.model_dump(mode="json")}
        if coordination_context is not None:
            context = DocumentImpactCoordinationContext.model_validate(coordination_context.model_dump(mode="json"))
            if context.protocol != seed.workflow_protocol or ((selection.unit.kind == "case") != (context.stage == "case_impacts")):
                raise ValueError("影响协调上下文不能替换固定协议或单元阶段")
            payload["coordination_context"] = context.model_dump(mode="json")
        messages = []
        if previous is not None:
            old_input, old_response = previous
            if old_input.selection.unit != selection.unit:
                raise ValueError("影响续读不能更换固定单元")
            messages = [*old_input.request.messages, {"role": "assistant", "content": old_response.text}]
        messages.append({"role": "user", "content": _json(payload)})
        request = ModelInvocationRequest(system_prompt=seed.analysis_context.prompt.system_prompt, messages=messages, tools=[])
        return request

    async def read_fixed_scope(self, claim):
        task = await self._tasks.get(claim.task.project_id, claim.task.document_id, claim.task.id)
        seed = await self._store.get_seed(claim.task.project_id, claim.task.document_id, claim.task.id)
        if seed != claim.seed:
            raise ValueError("影响调用不能替换固定seed")
        _, envelope = await self._tasks.read_input(task.project_id, task.document_id, task.id)
        checkpoint, manifest = await self._tasks.read_manifest(task.project_id, task.document_id, task.id)
        return task, envelope, checkpoint, manifest

    async def _read_object(self, reference, schema):
        raw = (await self._objects.read_object_uri(reference.storage_uri))["content"]
        if len(raw) != reference.size_bytes or hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise ValueError("影响调用对象大小或哈希不一致")
        return await asyncio.to_thread(schema.model_validate_json, raw)

    async def read_request(self, project_id, document_id, task_id, call_id):
        await self._tasks.get(project_id, document_id, task_id)
        record = await self._store.calls.get(project_id, document_id, task_id, call_id=call_id)
        if record is None:
            raise KeyError("当前项目文档没有该影响调用")
        payload = await self._read_object(record.request, DocumentImpactCallInput)
        seed = await self._store.get_seed(project_id, document_id, task_id)
        manifest = await self._store.get_manifest_checkpoint(project_id, document_id, task_id)
        if (manifest is None or seed is None or seed.analysis_context is None or
            payload.input_sha256 != manifest.input_sha256 or payload.manifest_sha256 != manifest.sha256 or
            payload.selection != record.selection or impact_record_hash(payload.selection) != record.selection_hash or
            payload.budget != record.budget or payload.request.system_prompt != seed.analysis_context.prompt.system_prompt):
            raise ValueError("影响调用输入与固定检查点不同")
        return record, payload

    async def read_response(self, project_id, document_id, task_id, call_id):
        record, _ = await self.read_request(project_id, document_id, task_id, call_id)
        if record.phase != "response_saved":
            raise ValueError("该影响调用没有已保存响应")
        response = await self._read_object(record.response, DocumentImpactCallResponse)
        if response.call_id != record.id or response.request_sha256 != record.request.sha256:
            raise ValueError("影响响应不能替换调用或请求")
        return record, response

    async def _discard_unreferenced_request(self, claim, reference):
        try:
            if await self._store.calls.object_is_referenced(reference.storage_uri):
                return
            await self._objects.delete_object_uri(reference.storage_uri)
            logger.info("document_impact_call_unused_request_removed", extra={"task_id": claim.task.id,
                "request_sha256": reference.sha256})
        except Exception as exc:
            # Keep the original business/commit result. Report a retained orphan for reconciliation.
            logger.error("document_impact_call_request_cleanup_unconfirmed", extra={"task_id": claim.task.id,
                "request_sha256": reference.sha256, "error_type": type(exc).__name__})

    async def _validate_coordination_origins(self, claim, context):
        for rule in context.rules:
            body = rule.candidate.model_dump(mode="json", exclude={"key"})
            if hashlib.sha256(_json(body).encode("utf-8")).hexdigest() != rule.id:
                raise ValueError("影响协调规则身份与完整候选不同")
            for origin in rule.origins:
                checkpoint = await self._store.responses.get(claim.task.project_id, claim.task.document_id,
                    claim.task.id, origin.checkpoint.call_id)
                if checkpoint != origin.checkpoint:
                    raise ValueError("影响协调规则不能引用其他任务或未保存的核验")
                assessment = await self._read_object(checkpoint.result, DocumentImpactResponseAssessment)
                if assessment.status != "unit_finished" or assessment.decision is None:
                    raise ValueError("影响协调规则需要已完成单元的候选")
                candidate = next((item for item in assessment.decision.candidates if item.key == origin.local_key), None)
                if candidate is None or candidate.model_dump(mode="json", exclude={"key"}) != body:
                    raise ValueError("影响协调规则与原核验候选不同")

    async def prepare(self, claim, *, call_key, selection: DocumentImpactCallSelection, coordination_context=None):
        if not isinstance(call_key, str) or not call_key.strip() or len(call_key) > 160:
            raise ValueError("影响调用身份不能为空或超过160字符")
        await self._tasks.get(claim.task.project_id, claim.task.document_id, claim.task.id)
        digest = impact_record_hash(selection)
        prior = await self._store.calls.get(claim.task.project_id, claim.task.document_id, claim.task.id, call_key=call_key)
        if prior is not None:
            self._store.calls.check_identity(prior, digest)
            _, saved = await self.read_request(claim.task.project_id, claim.task.document_id, claim.task.id, prior.id)
            expected = coordination_context.model_dump(mode="json") if coordination_context is not None else None
            if json.loads(saved.request.messages[-1]["content"]).get("coordination_context") != expected:
                raise ValueError("已保存影响调用不能替换协调上下文")
            return prior
        task, envelope, checkpoint, manifest = await self.read_fixed_scope(claim)
        if claim.seed.invocation_snapshot is None:
            raise ValueError("历史影响任务未冻结模型调用策略，请创建新任务")
        remaining = (task.deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise TimeoutError("影响调用原任务预算已耗尽")
        async with asyncio.timeout(remaining):
            if coordination_context is not None:
                await self._validate_coordination_origins(claim, coordination_context)
            previous = None
            if selection.previous_call_id:
                old_record, old_input = await self.read_request(task.project_id, task.document_id, task.id, selection.previous_call_id)
                if old_record.selection.unit != selection.unit or old_record.response_disposition != "received":
                    raise ValueError("影响续读必须关联相同单元的完整已保存响应")
                _, response = await self.read_response(task.project_id, task.document_id, task.id, old_record.id)
                previous = old_input, response
            if selection.retry_of_call_id:
                old_record, old_input = await self.read_request(task.project_id, task.document_id, task.id, selection.retry_of_call_id)
                if old_record.phase not in {"outcome_unknown", "response_unavailable"} or old_record.selection.unit != selection.unit:
                    raise ValueError("影响未知结果重试必须关联同单元的原未知调用")
                old_selection = old_input.selection.model_copy(update={"retry_of_call_id": None, "previous_call_id": None})
                if old_selection != selection.model_copy(update={"retry_of_call_id": None}):
                    raise ValueError("未知结果重试不能改变原固定读取范围")
                if coordination_context is not None and json.loads(old_input.request.messages[-1]["content"]).get(
                    "coordination_context") != coordination_context.model_dump(mode="json"):
                    raise ValueError("未知结果重试不能替换原协调上下文")
                request = old_input.request
            else:
                request = await asyncio.to_thread(self.build_request, claim.seed, envelope, manifest, selection,
                    previous=previous, coordination_context=coordination_context)
            budget = await asyncio.to_thread(self.estimate, claim.seed, request)
            payload = DocumentImpactCallInput(input_sha256=envelope.input_sha256, manifest_sha256=checkpoint.sha256,
                selection=selection, request=request, budget=budget)
            reference = await store_impact_json(self._objects, claim, payload, "request.json")
        try:
            result = await confirmed_impact_write(self._store.calls.prepare(claim, call_key=call_key, selection_hash=digest,
                selection=selection, request=reference, budget=budget, manifest_sha256=checkpoint.sha256),
                task_id=claim.task.id, stage="prepare")
        except (ValueError, KeyError, TimeoutError, ContinuationLeaseLostError):
            await self._discard_unreferenced_request(claim, reference)
            raise
        # An unconfirmed database exception above skips cleanup and propagates.
        if result.request != reference:
            await self._discard_unreferenced_request(claim, reference)
        return result

    async def invoke(self, claim, call_id):
        record, payload = await self.read_request(claim.task.project_id, claim.task.document_id, claim.task.id, call_id)
        if record.phase == "response_saved":
            await self.read_response(claim.task.project_id, claim.task.document_id, claim.task.id, call_id)
            return record
        if record.phase == "invoking":
            if record.invocation_attempt_id == claim.attempt_id:
                return record  # Another coroutine owns this call; do not send or mark it unknown.
            return await confirmed_impact_write(self._store.calls.mark_unknown(claim, call_id, code="recovered_without_response"),
                task_id=claim.task.id, stage="recover_unknown")
        if record.phase in {"outcome_unknown", "response_unavailable"}:
            return record
        snapshot, context = claim.seed.invocation_snapshot, claim.seed.analysis_context
        if snapshot is None or context is None:
            raise ValueError("历史影响任务未冻结模型调用策略，请创建新任务")
        # Validate before saving send intent; parameter rejection is evidence of no model dispatch here.
        if await asyncio.to_thread(self.estimate, claim.seed, payload.request) != record.budget:
            raise ValueError("影响调用预算与固定请求不同")
        model = await asyncio.to_thread(self._models.get_model_config, context.model.key)
        if model is None or model_invocation_parameters(model) != context.model.model_dump(mode="json"):
            logger.warning("document_impact_call_preflight_rejected", extra={"task_id": claim.task.id,
                "call_id": call_id, "reason": "model_parameters_changed"})
            raise ValueError("模型参数已变化，不能使用新配置续写原任务")
        if await asyncio.to_thread(self._models.get_invocation_runtime_parameters, context.model.key) != snapshot.runtime_parameters:
            logger.warning("document_impact_call_preflight_rejected", extra={"task_id": claim.task.id,
                "call_id": call_id, "reason": "runtime_parameters_changed"})
            raise ValueError("模型调用适配器、SDK或超时设置已变化，不能续写原任务")
        await self._projects.require_active(claim.task.project_id)
        task = await self._tasks.get(claim.task.project_id, claim.task.document_id, claim.task.id)
        remaining = min(snapshot.config.payload.call_timeout_seconds,
            (task.deadline_at - datetime.now(timezone.utc)).total_seconds())
        if remaining <= 0:
            raise TimeoutError("影响调用原任务预算已耗尽")
        record, send = await confirmed_impact_write(self._store.calls.start(claim, call_id), task_id=task.id, stage="intent")
        if not send:
            return record
        logger.info("document_impact_model_dispatching", extra={"task_id": task.id, "call_id": call_id,
            "attempt_id": claim.attempt_id, "request_sha256": record.request.sha256})
        try:
            async with asyncio.timeout(remaining):
                result = await self._models.invoke(context.model.key, payload.request,
                    expected_parameters=context.model.model_dump(mode="json"),
                    expected_runtime_parameters=snapshot.runtime_parameters)
        except asyncio.CancelledError:
            logger.warning("document_impact_call_interrupted", extra={"task_id": task.id, "call_id": call_id})
            raise  # Keep invoking. A new lease owner establishes outcome_unknown.
        except Exception as exc:
            logger.warning("document_impact_call_dispatch_unconfirmed", extra={"task_id": task.id,
                "call_id": call_id, "error_type": type(exc).__name__})
            return await confirmed_impact_write(self._store.calls.mark_unknown(claim, call_id, code="dispatch_" + type(exc).__name__),
                task_id=task.id, stage="dispatch_unknown")
        metadata = {key: result.response_summary.get(key) for key in
            ("mode", "error_type", "status_code", "response_id", "finish_reason", "stop_reason", "usage")}
        # Provider errors may embed request credentials in their diagnostics.
        raw = result.raw_response if metadata.get("mode") not in MODEL_FAILURE_MODES else {
            "diagnostics_omitted": "provider error diagnostics may contain credentials", "metadata": metadata}
        response = DocumentImpactCallResponse(call_id=call_id, request_sha256=record.request.sha256,
            text=result.text, tool_calls=result.tool_calls, metadata=metadata, raw_response=raw)
        disposition = ("provider_failure" if metadata.get("mode") in MODEL_FAILURE_MODES else "tool_calls" if result.tool_calls
            else "truncated" if metadata.get("finish_reason") in {"length", "max_tokens"} or metadata.get("stop_reason") == "max_tokens"
            else "empty" if not result.text.strip() else "received")
        encoded = canonical_impact_json(response)
        if len(encoded) > snapshot.config.payload.max_response_bytes:
            logger.warning("document_impact_call_response_limit", extra={"task_id": task.id, "call_id": call_id,
                "response_bytes": len(encoded), "max_response_bytes": snapshot.config.payload.max_response_bytes})
            return await confirmed_impact_write(self._store.calls.mark_unknown(claim, call_id,
                code="response_over_size_limit", response_unavailable=True), task_id=task.id, stage="response_limit")
        # Object or PG failures propagate with invoking preserved; no automatic repeat or parsing.
        reference = await confirmed_impact_write(store_impact_json(self._objects, claim, response, "response.json"), task_id=task.id, stage="response_object")
        return await confirmed_impact_write(self._store.calls.save_response(claim, call_id, reference, disposition),
            task_id=task.id, stage="response_checkpoint")
