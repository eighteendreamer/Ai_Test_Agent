"""One durable evidence-only correction, within the original graph turn budget."""
from __future__ import annotations

import hashlib
import json
import logging
from uuid import uuid4

from src.application.test_runs.agent_case_contract import receipt_prompt_version
from src.schemas.case_execution import CaseReceiptCorrectionCheckpoint, CaseReceiptObject
from src.schemas.model_config import ModelInvocationRequest, ModelInvocationResult, model_invocation_parameters
from src.application.model_clients.completion import is_model_response_incomplete

logger = logging.getLogger(__name__)


class CaseReceiptOutcomeUnknown(RuntimeError):
    """The original read-only call has intent but no confirmed response."""


class AgentCaseReceiptService:
    # Existing counterparts: CasePlanningWorkflow intent/response checkpoints,
    # AgentCaseAssessmentService's owned manifest and ArtifactStorageService.
    def __init__(self, *, models, objects, assessment):
        self._models, self._objects, self._assessment = models, objects, assessment

    async def _write(self, value, *, prefix, filename):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        stored = await self._objects.store_uploaded_bytes(content=raw, filename=filename,
            object_prefix=prefix, object_name=f"{prefix}/{digest}-{filename}", content_type="application/json")
        return CaseReceiptObject(uri=stored["uri"], sha256=digest, size_bytes=len(raw))

    async def _read(self, reference):
        raw = (await self._objects.read_object_uri(reference.uri))["content"]
        if len(raw) != reference.size_bytes or hashlib.sha256(raw).hexdigest() != reference.sha256:
            raise ValueError("正式回执原件大小或哈希与持久检查点不一致")
        return json.loads(raw)

    async def correct(self, *, context, checkpoint, snapshot, records, validation_error, save):
        """save is the existing lease-fenced RunItem checkpoint transaction."""
        current = checkpoint.receipt_correction
        state = snapshot.graph_state
        transport = context.receipt_correction_transport_options
        transport_kwargs = {"transport_options": transport} if transport is not None else {}
        if current is not None:
            if (current.protocol != context.receipt_correction_protocol or current.snapshot_id != snapshot.id
                or current.model_key != state.get("selected_model_key")
                or current.execution_model_calls != state.get("model_call_count")
                or current.max_iterations != state.get("max_iterations")
                or current.runtime_parameters.get("transport_options") != (
                    transport.model_dump(mode="json") if transport is not None else None)
                or current.total_model_calls != current.execution_model_calls + 1
                or current.total_model_calls > current.max_iterations):
                raise ValueError("回执阶段与原固定图身份或预算不一致")
            if current.phase != "response" or current.response is None:
                raise CaseReceiptOutcomeUnknown("只读回执调用已有意图但无可靠响应；保留未知，不能重新发送或重复业务操作")
            # A saved response is authoritative even if today's model defaults changed.
            return ModelInvocationResult.model_validate(await self._read(current.response)), checkpoint

        summary = state.get("model_response_summary") or {}
        if summary.get("mode") != "ok" or is_model_response_incomplete(summary):
            raise ValueError("原模型输出未成功完整结束，不自动纠正或重复发送")
        count, limit = state.get("model_call_count"), state.get("max_iterations")
        usage = state.get("turn_token_usage") or {}
        window = int(state.get("model_context_window") or 0)
        if count is None or limit is None:
            raise ValueError("回执纠正缺少原累计预算，不能为历史运行补造调用额度")
        if count >= limit or (window > 0 and int(usage.get("prompt_tokens") or 0) >= window * .9):
            raise ValueError("原模型或上下文预算已耗尽，保留原回执错误，不新增发送")
        if self._models is None or self._objects is None:
            raise RuntimeError("正式只读回执依赖未配置，不能丢失原响应后调用模型")
        # The builder rejects foreign/unsettled evidence before any admission.
        request = await self._assessment.build_assessment_request(context=context, tool_records=records,
            session_id=checkpoint.session_id, turn_id=checkpoint.turn_id,
            attempt_id=checkpoint.execution_attempt_id, trace_id=str(state.get("trace_id") or checkpoint.turn_id),
            validation_feedback={"error": validation_error, "original_response": state.get("model_response_text", "")})
        call_id = str(uuid4())
        request.trace_context.update(logical_call_id=call_id, phase="case_receipt_correction",
            agent_key=str(state.get("selected_agent_key") or ""), mode_key=str(state.get("mode_key") or "default"))
        model_key = str(state.get("selected_model_key") or "")
        model = self._models.get_model_config(model_key)
        if model is None:
            raise ValueError("原执行模型不可用，不能切换默认模型纠正回执")
        runtime_parameters = self._models.get_invocation_runtime_parameters(model_key, **transport_kwargs)
        model_parameters = model_invocation_parameters(model)
        response_protocol = ("case-receipt-stream-response-v1"
            if model_parameters.get("capabilities", {}).get("streaming") else None)
        prefix = f"case-receipts/{context.project_id}/{context.run_id}/{context.run_item_id}/{checkpoint.execution_attempt_id}/{call_id}"
        original = await self._write(request.model_dump(mode="json"), prefix=prefix, filename="request.json")
        current = CaseReceiptCorrectionCheckpoint(phase="invoking", call_id=call_id, snapshot_id=snapshot.id,
            prompt_version=receipt_prompt_version(context.scene_receipt_protocol), model_key=model_key,
            model_parameters=model_parameters, runtime_parameters=runtime_parameters,
            response_transport_protocol=response_protocol,
            execution_model_calls=count, total_model_calls=count + 1, max_iterations=limit, request=original)
        checkpoint = checkpoint.model_copy(update={"receipt_correction": current})
        await save(checkpoint)
        logger.info("agent_case_receipt_invoking", extra={"run_id": context.run_id, "run_item_id": context.run_item_id,
            "logical_call_id": call_id, "model_call_count": count + 1, "max_iterations": limit})
        # Re-read exactly the persisted input; no graph or business tools participate.
        request = ModelInvocationRequest.model_validate(await self._read(original))
        async def invoke():
            return await self._models.invoke(model_key, request, expected_parameters=current.model_parameters,
                expected_runtime_parameters=current.runtime_parameters, **transport_kwargs)
        logger.info("agent_case_receipt_transport_decided", extra={"run_id": context.run_id,
            "run_item_id": context.run_item_id, "logical_call_id": call_id,
            "response_transport_protocol": current.response_transport_protocol,
            "model_streaming": bool(current.model_parameters.get("capabilities", {}).get("streaming"))})
        if current.response_transport_protocol is not None:
            # Existing counterpart: generation_pipeline.invoke_frozen. Persist
            # the returned result for assessment; chunks are not a receipt.
            observed = {"public_text_chunks": 0, "public_text_chars": 0}
            async def receive(text):
                observed["public_text_chunks"] += 1
                observed["public_text_chars"] += len(text)
            async with self._models.stream_handler(receive):
                response = await invoke()
            logger.info("agent_case_receipt_stream_received", extra={"run_id": context.run_id,
                "logical_call_id": call_id, **observed})
        else:
            response = await invoke()
        saved = await self._write(response.model_dump(mode="json", exclude={"request_payload"}),
            prefix=prefix, filename="response.json")
        current = current.model_copy(update={"phase": "response", "response": saved})
        checkpoint = checkpoint.model_copy(update={"receipt_correction": current})
        await save(checkpoint)
        logger.info("agent_case_receipt_response_saved", extra={"run_id": context.run_id,
            "run_item_id": context.run_item_id, "logical_call_id": call_id, "response_sha256": saved.sha256})
        return response, checkpoint
