"""One planning loop for synchronous callers and durable Worker checkpoints."""
from __future__ import annotations

from src.schemas.model_request_options import ModelOutputFormat

import asyncio
import logging
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from src.application.test_cases.planning_sources import json_text
from src.application.test_cases.planning_stages import (
    merge_evidence_decision, previous_relation_logic_prompt_version, seal_logical_decision,
    stage_prompt_version, validate_relation_logic_finish,
)
from src.schemas.case_planning_stages import (
    EvidencePlanningDecision, LogicalPlanningDecision, PlanningStageCheckpoint,
    RELATION_STAGE_PROTOCOL, LogicalPlanningDecisionV2, EvidencePlanningDecisionV2,
    LogicalPlanningDecisionV3, EvidencePlanningDecisionV3,
    LogicalPlanningDecisionV4,
)
from src.application.test_cases.planning_operation_prefetch import (
    PREFETCH_QUERY_BATCH_SIZE, declared_operation_queries, validate_operation_selections, ITEM_PREFETCH_PROTOCOL,
)
from src.schemas.case_planning import PlanningQuery
from src.schemas.case_planning_sources import SCENE_LINKED_SOURCE_PROTOCOL, SceneContentBasis
from src.schemas.case_planning_scenes import scene_decision_types, SceneEvidencePlanningDecision
from src.application.test_cases.planning_linked_sources import (
    linked_source_queue, linked_document_bases, linked_source_context, linked_context_prompt_version,
)
from src.application.test_cases.planning_required_sources import REQUIRED_SOURCE_PROMPT_VERSION, required_source_prompt_version, validate_required_sources
from src.application.test_cases.planning_evidence_references import evidence_reference_catalog, bind_evidence_references
from src.application.test_cases.planning_effect_tasks import (
    decisions_for_protocol, next_effect_task, task_roles, accept_task_decision, effect_task_output_format,
)
from src.schemas.case_planning_effect_tasks import EFFECT_TASK_PROTOCOL, EffectTaskDecision, EffectTaskCheckpoint
from src.schemas.case_planning_references import (
    REFERENCE_PROTOCOL_V2, REFERENCE_PROTOCOL_V3, ReferencedEvidenceDecision,
    ReferencedEvidenceDecisionV2, ReferencedEvidenceDecisionV3,
)
from src.application.test_cases.planning_tool_contracts import NAVIGATION_PROMPT_VERSION, OPERATION_PROMPT_VERSION, query_tool_contracts, tool_catalog
from src.runtime.store import ContinuationLeaseLostError
from src.core.request_context import get_request_context
from src.schemas.case_planning_task import PLANNING_RESPONSE_PROTOCOL, planning_model_parameters
from src.schemas.model_config import MODEL_FAILURE_MODES, ModelInvocationRequest
from src.application.model_clients.completion import model_completion_status, is_model_response_incomplete, stream_completion_fields

logger = logging.getLogger(__name__)


class PlanningCheckpointUnavailable(RuntimeError):
    """A durable write was not confirmed; preserve the last database fact."""


class CasePlanningWorkflow:
    def __init__(self, planner):
        self._planner = planner

    async def run(self, seed, checkpoint, *, deadline_at, persist=None, attempt_id=None,
                  fixed_case_versions=None, planning_context=None, pin_read_versions=False, remaining_seconds=None):
        """Cancellation/lease loss propagates; business failures become saved plans.

        A response checkpoint is processed without re-invoking the model. A
        saved invocation with no response consumes that round, records unknown
        transport outcome and may use only the remaining original rounds.
        """
        record, request, policy = checkpoint.plan, checkpoint.plan.request, checkpoint.plan.config_snapshot.policy
        base_prompt_version = seed.plan.prompt_version.removesuffix("+" + REQUIRED_SOURCE_PROMPT_VERSION) if seed.required_source_prompt else seed.plan.prompt_version
        native_navigation = seed.stage_prompts is not None or base_prompt_version in {NAVIGATION_PROMPT_VERSION, OPERATION_PROMPT_VERSION}
        native_operations = seed.tool_operations if seed.stage_prompts is not None or base_prompt_version == OPERATION_PROMPT_VERSION else None
        fixed_case_versions = dict(fixed_case_versions or {})
        if seed.plan.regression is not None:
            source = seed.plan.regression
            if fixed_case_versions and fixed_case_versions != source.required_case_versions:
                raise ValueError("回归目标不能替换持久任务的固定输入")
            fixed_case_versions = dict(source.required_case_versions)
            planning_context = source.context
        if seed.run_revision is not None:
            source = seed.run_revision
            if fixed_case_versions and fixed_case_versions != source.required_case_versions:
                raise ValueError("运行修订目标不能替换持久任务的固定输入")
            fixed_case_versions = dict(source.required_case_versions)
            planning_context = source.context
        read_case_versions = seed.run_revision.read_case_versions if seed.run_revision else fixed_case_versions

        async def save(message):
            if persist is not None:
                try:
                    await persist(checkpoint.model_copy(deep=True), message=message)
                except ContinuationLeaseLostError:
                    raise
                except Exception as exc:
                    logger.error("case_planning_checkpoint_unconfirmed", extra={"project_id": record.project_id,
                        "plan_id": record.id, "phase": checkpoint.phase, "error_type": type(exc).__name__})
                    raise PlanningCheckpointUnavailable("规划检查点提交尚不能确认") from exc

        async def validate_plan(decision, *, validate_effects):
            versions = {item.source_id: item.version for item in record.evidence if item.kind == "case"} if pin_read_versions else {}
            versions.update(fixed_case_versions)
            await self._planner._validate(record, decision, fixed_case_versions=versions,
                preserve_read_metadata=pin_read_versions, validate_effects=validate_effects)
            if fixed_case_versions and {item.case_id: item.case_version_id for item in decision.items if item.kind == "target"} != fixed_case_versions:
                label = "运行修订目标" if seed.run_revision else "回归目标"
                raise ValueError(f"{label}必须完整保留指定用例及版本，其他正式项只能作为必要依赖")

        try:
            if checkpoint.phase == "finished" and record.decision is not None:
                validate_required_sources(record, record.decision)
            elif bool(seed.required_source_prompt) != bool(request.required_sources):
                raise ValueError("明确来源要求与原固定Prompt不一致")
            if seed.stage_prompts is not None:
                expected_version = required_source_prompt_version(linked_context_prompt_version(
                    stage_prompt_version(policy, seed.stage_prompts), seed.linked_source_context_protocol), request)
                prompt_version_matches = record.prompt_version == expected_version
                if not prompt_version_matches:
                    legacy_version = required_source_prompt_version(linked_context_prompt_version(
                        stage_prompt_version(policy, seed.stage_prompts, include_logic_revision=False),
                        seed.linked_source_context_protocol), request)
                    previous_logic_version = previous_relation_logic_prompt_version(expected_version)
                    if previous_logic_version is not None:
                        previous_logic_version = required_source_prompt_version(previous_logic_version, request)
                    prompt_version_matches = record.prompt_version in {legacy_version, previous_logic_version}
                if (not prompt_version_matches or seed.system_prompt != seed.stage_prompts.logic
                    or policy.planning_stage_protocol != seed.stage_prompts.protocol):
                    raise ValueError("阶段Prompt与固定规划版本不一致")
                if checkpoint.stages is None:
                    if checkpoint.phase != "retrieval":
                        raise ValueError("已开始的阶段规划缺少原阶段检查点")
                    checkpoint.stages = PlanningStageCheckpoint()
                PlanningStageCheckpoint.model_validate(checkpoint.stages.model_dump(mode="json"))
                if checkpoint.stages.stage == "effects" and (
                    (checkpoint.stages.operation_selections is not None) != (policy.operation_prefetch_protocol == ITEM_PREFETCH_PROTOCOL)):
                    raise ValueError("固定阶段操作选择与原规划协议不一致")
            remaining = remaining_seconds if remaining_seconds is not None else (deadline_at - datetime.now(timezone.utc)).total_seconds()
            if checkpoint.linked_sources is not None and not policy.linked_source_protocol:
                raise ValueError("旧规划任务不能新增关联来源状态")
            if checkpoint.effect_tasks is not None and policy.planning_stage_protocol != EFFECT_TASK_PROTOCOL:
                raise ValueError("旧规划任务不能新增分任务证据状态")
            if policy.linked_source_protocol and checkpoint.phase not in {"retrieval", "finished"} and checkpoint.linked_sources is None:
                raise ValueError("固定关联来源预读缺少原检查点")
            if remaining <= 0:
                raise TimeoutError("planning deadline expired")
            async with asyncio.timeout(remaining):
                while checkpoint.phase != "finished":
                    await self._planner._projects.require_active(record.project_id)
                    if checkpoint.phase == "retrieval":
                        await self._planner._recall(record, read_case_versions)
                        catalog_options = {"scene_queries":True} if policy.scene_query_protocol else {}
                        checkpoint.catalog = await self._planner._sources.catalog(record.project_id, policy.max_candidates, **catalog_options)
                        if policy.linked_source_protocol:
                            checkpoint.linked_sources = linked_source_queue(record.evidence)
                        checkpoint.phase = "sources" if policy.linked_source_protocol else "ready"
                        await save("候选完整版本与项目目录已保存")
                        continue
                    if checkpoint.phase == "sources":
                        progress = checkpoint.linked_sources
                        if progress is None or progress.next_index > len(progress.entries):
                            raise ValueError("关联来源查询检查点无效")
                        while progress.next_index < len(progress.entries):
                            entry = progress.entries[progress.next_index]
                            if entry.basis is None and entry.error is None:
                                try:
                                    capture = (self._planner._sources.capture_linked_basis
                                        if policy.linked_source_protocol == SCENE_LINKED_SOURCE_PROTOCOL
                                        else self._planner._sources.capture_document_basis)
                                    entry.basis = await capture(record.project_id, entry.source)
                                except (KeyError, ValueError, RuntimeError, OSError) as exc:
                                    entry.error = str(exc)
                                await save("关联来源固定身份已保存")
                            if entry.error is None and not entry.read:
                                try:
                                    if isinstance(entry.basis, SceneContentBasis):
                                        if policy.linked_source_protocol != SCENE_LINKED_SOURCE_PROTOCOL:
                                            raise ValueError("旧关联文档协议不能读取场景基准")
                                        evidence, metadata = await self._planner._sources.read_scene_basis(record.project_id, entry.basis)
                                    else:
                                        evidence, metadata = await self._planner._sources.query(record.project_id,
                                            PlanningQuery(kind="document", source_id=entry.source.source_id, max_lines=500), policy.max_candidates,
                                            identity_protocol=policy.source_identity_protocol,
                                            fixed_documents={entry.source.source_id: [entry.basis]})
                                    self._planner._merge_evidence(record, evidence)
                                    entry.evidence_ids = [item.id for item in evidence]
                                    entry.read = True
                                    if metadata.get("has_more"):
                                        record.warnings.append(f"关联文档{entry.source.source_id}尚有后续行；可继续读取同一固定来源")
                                except (KeyError, ValueError, RuntimeError, OSError) as exc:
                                    entry.error = str(exc)
                            if entry.error:
                                record.warnings.append(f"关联来源{entry.source.source_id}未自动读取：{entry.error}")
                                logger.warning("case_planning_linked_source_unread", extra={"project_id": record.project_id,
                                    "plan_id": record.id, "source_id": entry.source.source_id})
                            progress.next_index += 1
                            await save("关联来源只读结果已保存")
                        checkpoint.phase = "ready"
                        await save("首次推理的关联来源读取完成")
                        continue
                    if checkpoint.phase == "invoking":
                        round_record = record.rounds[-1]
                        round_record["status"] = "outcome_unknown"
                        round_record["recovered_by_attempt"] = attempt_id
                        record.warnings.append(f"第 {checkpoint.next_round} 回合已记录调用意图但没有持久响应；不能确认是否发送或计费。该回合额度已消耗，后续仅使用剩余额度。")
                        checkpoint.validation_feedback = {"error": "上一规划调用响应无法确认，请仅按已保存依据继续",
                            "available_evidence_ids": [item.id for item in record.evidence]}
                        checkpoint.phase = "ready"
                        await save("上次调用响应无法确认，保留调用记录及剩余额度")
                        continue
                    if checkpoint.phase == "ready":
                        if checkpoint.next_round >= policy.max_rounds:
                            raise ValueError("达到规划回合限制，尚未得到完整计划；未创建测试运行")
                        model_config = await asyncio.to_thread(self._planner._model_config, record.model_key or request.model_key)
                        if seed.model_parameters and planning_model_parameters(model_config) != seed.model_parameters:
                            raise ValueError("规划模型参数已变化，不能使用新配置续写原任务；请另建规划任务")
                        record.model_key = model_config.key
                        context = {"project": seed.project_context, "objective": request.objective,
                            "runtime_context": request.runtime_context,
                            "evidence": [item.model_dump(mode="json") for item in record.evidence],
                            "catalog": checkpoint.catalog, "query_results": checkpoint.query_results,
                            "warnings": record.warnings, "validation_feedback": checkpoint.validation_feedback,
                            "round": checkpoint.next_round + 1, "remaining_rounds": policy.max_rounds - checkpoint.next_round}
                        if checkpoint.linked_sources is not None:
                            context["linked_source_reads"] = linked_source_context(
                                checkpoint.linked_sources, seed.linked_source_context_protocol)
                            if seed.linked_source_context_protocol:
                                logger.info("case_planning_linked_context_projected", extra={
                                    "project_id": record.project_id, "plan_id": record.id,
                                    "protocol": seed.linked_source_context_protocol,
                                    "source_count": len(checkpoint.linked_sources.entries),
                                    "snapshot_chars": len(json_text(checkpoint.linked_sources.model_dump(mode="json"))),
                                    "navigation_chars": len(json_text(context["linked_source_reads"])),
                                })
                        stage = checkpoint.stages.stage if seed.stage_prompts is not None else None
                        prompt = seed.system_prompt
                        if stage is not None:
                            context["planning_stage"] = stage
                            prompt = getattr(seed.stage_prompts, stage)
                            if stage == "logic":
                                context["available_tools"] = [{"key": key, "name": value["name"]}
                                    | ({"operations": list((seed.tool_operations or {}).get(key, {}))} if policy.operation_prefetch_protocol else {})
                                    for key, value in (seed.tool_contracts or {}).items()]
                            else:
                                context["logical_plan"] = checkpoint.stages.logical_decision.model_dump(mode="json")
                                context["logical_hash"] = checkpoint.stages.logical_hash
                                if checkpoint.stages.operation_selections is not None:
                                    context["operation_selections"] = [entry.model_dump(mode="json") for entry in checkpoint.stages.operation_selections]
                                if seed.stage_prompts.protocol == EFFECT_TASK_PROTOCOL:
                                    if checkpoint.effect_tasks is None:
                                        raise ValueError("分任务证据阶段缺少原检查点")
                                    task = next_effect_task(checkpoint.stages, checkpoint.effect_tasks)
                                    if task is None:
                                        raise ValueError("声明任务已完成但缺少最终校验结果")
                                    scope = [item.key for item in checkpoint.stages.logical_decision.items] if task == "bindings" else task_roles(checkpoint.stages, checkpoint.effect_tasks)[task]
                                    context["evidence_task"] = {"task": task, "scope": scope}
                                    context["completed_tasks"] = checkpoint.effect_tasks.model_dump(mode="json")
                                    prompt = seed.stage_prompts.effect_tasks[task]
                        if seed.required_source_prompt is not None:
                            context["required_sources"] = [source.model_dump(mode="json") for source in request.required_sources]
                            prompt += "\n" + seed.required_source_prompt
                        if seed.tool_contracts is not None and stage != "logic":
                            context["tool_catalog"] = tool_catalog(seed.tool_contracts, policy.max_candidates,
                                navigation=native_navigation, operations=native_operations)
                        if fixed_case_versions:
                            context["constraints"] = {"required_case_versions": fixed_case_versions,
                                "run_revision" if seed.run_revision else "regression": planning_context or {}}
                        if record.environment is not None:
                            context["business_environment"] = record.environment.model_dump(mode="json")
                        if stage == "effects" and policy.evidence_reference_protocol:
                            context["evidence_reference_catalog"] = evidence_reference_catalog(
                                record.evidence, policy.evidence_reference_protocol)
                        body = json_text(context)
                        if len(body) + len(prompt) > policy.max_context_chars:
                            raise ValueError("完整规划上下文超过配置限制，未截断用例；请缩小候选范围或调整配置")
                        checkpoint.next_round += 1
                        round_record = {"round": checkpoint.next_round, "status": "invoking",
                            "logical_call_id": str(uuid4()), "attempt_id": attempt_id, "streaming": model_config.capabilities.streaming}
                        if stage is not None:
                            round_record["planning_stage"] = stage
                        if "evidence_task" in context:
                            round_record["evidence_task"] = context["evidence_task"]
                        record.rounds.append(round_record)
                        if checkpoint.linked_sources is not None:
                            round_record["linked_source_reads"] = checkpoint.linked_sources.model_dump(mode="json")
                        if "evidence_reference_catalog" in context:
                            round_record["evidence_reference_catalog"] = context["evidence_reference_catalog"]
                        checkpoint.phase = "invoking"
                        await save("模型调用意图已保存")
                        logger.info("case_planning_model_invoking", extra={"project_id": record.project_id,
                            "plan_id": record.id, "logical_call_id": round_record["logical_call_id"],
                            "round": checkpoint.next_round, "attempt_id": attempt_id})
                        # Preserve existing synchronous tracing. Durable tasks
                        # use the Worker request context without a fake Session.
                        if persist is None:
                            trace = {"project_id": record.project_id, "trace_id": record.id,
                                "turn_id": f"{record.id}:{checkpoint.next_round}", "session_id": record.id,
                                "agent_key": "case-planning", "logical_call_id": round_record["logical_call_id"]}
                        else:
                            # Durable workers do not have a user Session, but
                            # their request context has a stable task trace and
                            # lease attempt. Keep those identifiers on the
                            # model request so provider HTTP observations can
                            # be joined across worker retries without inventing
                            # a session identity.
                            worker_context = get_request_context()
                            trace = {
                                "project_id": record.project_id,
                                "trace_id": worker_context.trace_id if worker_context else record.id,
                                "turn_id": f"{record.id}:{checkpoint.next_round}",
                                "attempt_id": attempt_id or (worker_context.attempt_id if worker_context else ""),
                                "agent_key": "case-planning",
                                "logical_call_id": round_record["logical_call_id"],
                            }
                        output_format = ModelOutputFormat(kind="json_object", required=False)
                        if stage == "effects" and seed.stage_prompts.protocol == EFFECT_TASK_PROTOCOL:
                            output_format = effect_task_output_format(context["evidence_task"]["task"],
                                policy.evidence_reference_protocol)
                        model_request = ModelInvocationRequest(system_prompt=prompt,
                            messages=[{"role": "user", "content": body}], tools=[], trace_context=trace,
                            output_format=output_format)
                        started = perf_counter()
                        async def observe_delta(delta):
                            if "first_text_seconds" not in round_record:
                                round_record["first_text_seconds"] = round(perf_counter() - started, 3)
                            round_record["received_text_chars"] = round_record.get("received_text_chars", 0) + len(delta)
                        kwargs = {"expected_parameters": seed.model_parameters} if seed.model_parameters else {}
                        try:
                            if model_config.capabilities.streaming:
                                async with self._planner._models.stream_handler(observe_delta):
                                    result = await self._planner._models.invoke(record.model_key, model_request, **kwargs)
                            else:
                                result = await self._planner._models.invoke(record.model_key, model_request, **kwargs)
                        except BaseException:
                            round_record["status"] = "interrupted"
                            raise
                        finally:
                            round_record["model_elapsed_seconds"] = round(perf_counter() - started, 3)
                        checkpoint.response_text = result.text
                        checkpoint.response_metadata = {key: result.response_summary.get(key) for key in
                            ("mode", "error_type", "status_code", "response_id", "finish_reason", "stop_reason", "usage")}
                        checkpoint.response_has_tools = bool(result.tool_calls)
                        checkpoint.response_metadata.update(stream_completion_fields(result.response_summary))
                        if "request_mapping" in result.response_summary:
                            checkpoint.response_metadata["request_mapping"] = result.response_summary["request_mapping"]
                        round_record.update({"status": "response_received", "response_mode": str(result.response_summary.get("mode") or "")[:80],
                            "response_metadata": checkpoint.response_metadata})
                        if round_record["response_mode"] in MODEL_FAILURE_MODES:
                            round_record["error_type"] = result.response_summary.get("error_type")
                            round_record["status_code"] = result.response_summary.get("status_code")
                        checkpoint.phase = "response"
                        await save("模型响应已保存，待校验")
                        continue
                    if checkpoint.phase == "response":
                        round_record = record.rounds[-1]
                        mode = str(checkpoint.response_metadata.get("mode") or "")
                        if mode in MODEL_FAILURE_MODES and (mode != "incomplete_response" or
                            seed.response_protocol != PLANNING_RESPONSE_PROTOCOL):
                            raise ValueError(f"模型调用失败（{mode}）；未创建测试运行")
                        if checkpoint.response_has_tools:
                            raise ValueError("规划模型返回了不可执行的工具调用；此阶段只能查询项目依据")
                        if seed.response_protocol == PLANNING_RESPONSE_PROTOCOL:
                            metadata = checkpoint.response_metadata
                            disposition = ("truncated" if model_completion_status(metadata) == "truncated"
                                else "incomplete" if is_model_response_incomplete(metadata)
                                else "empty" if not (checkpoint.response_text or "").strip() else "received")
                            if disposition != "received":
                                reason = ("模型规划输出达到生成上限，响应不完整" if disposition == "truncated"
                                    else "模型规划输出未完整结束" if disposition == "incomplete" else "模型规划响应正文为空")
                                round_record.update({"status": "response_rejected", "response_disposition": disposition,
                                    "response_protocol": seed.response_protocol, "validation_error": reason,
                                    "response_text": checkpoint.response_text})
                                logger.warning("case_planning_response_incomplete", extra={"project_id": record.project_id,
                                    "plan_id": record.id, "round": checkpoint.next_round,
                                    "response_protocol": seed.response_protocol, "response_disposition": disposition,
                                    "finish_reason": metadata.get("finish_reason"), "stop_reason": metadata.get("stop_reason")})
                                raise ValueError(f"{reason}；已保留响应，不自动重发；未创建测试运行")
                        try:
                            task_candidate = None
                            stage = checkpoint.stages.stage if seed.stage_prompts is not None else None
                            if stage == "effects" and policy.evidence_reference_protocol:
                                round_record["wire_response_text"] = checkpoint.response_text
                            logic_type, effects_type = (LogicalPlanningDecisionV2, EvidencePlanningDecisionV2) if (
                                seed.stage_prompts is not None and seed.stage_prompts.protocol == RELATION_STAGE_PROTOCOL) else (
                                LogicalPlanningDecision, EvidencePlanningDecision)
                            if policy.effect_expected_source_protocol:
                                logic_type, effects_type = LogicalPlanningDecisionV3, EvidencePlanningDecisionV3
                            if policy.operation_prefetch_protocol == ITEM_PREFETCH_PROTOCOL:
                                logic_type = LogicalPlanningDecisionV4
                            if stage == "effects" and policy.evidence_reference_protocol:
                                effects_type = (
                                    ReferencedEvidenceDecisionV3
                                    if policy.evidence_reference_protocol == REFERENCE_PROTOCOL_V3 else
                                    ReferencedEvidenceDecisionV2
                                    if policy.evidence_reference_protocol == REFERENCE_PROTOCOL_V2 else
                                    ReferencedEvidenceDecision)
                            if policy.scene_query_protocol:
                                logic_type, effects_type = scene_decision_types(
                                    policy.operation_prefetch_protocol == ITEM_PREFETCH_PROTOCOL, policy.evidence_reference_protocol)
                            if stage == "effects" and seed.stage_prompts.protocol == EFFECT_TASK_PROTOCOL:
                                effects_type = decisions_for_protocol(policy.evidence_reference_protocol)[
                                    next_effect_task(checkpoint.stages, checkpoint.effect_tasks)]
                            decision = (self._planner._parse(checkpoint.response_text or "", decision_type=(
                                logic_type if stage == "logic" else effects_type))
                                if stage is not None else self._planner._parse(checkpoint.response_text or ""))
                            wire_decision = decision.model_copy(deep=True)
                            if isinstance(decision, EffectTaskDecision):
                                if decision.task != round_record["evidence_task"]["task"]:
                                    raise ValueError("声明任务响应与原调用身份不同")
                                if decision.action == "query":
                                    query_type = SceneEvidencePlanningDecision if policy.scene_query_protocol else EvidencePlanningDecisionV3
                                    decision = query_type.model_validate(decision.model_dump(mode="json", exclude={"task"}))
                                else:
                                    task_candidate, decision = accept_task_decision(checkpoint.stages, checkpoint.effect_tasks,
                                        decision, round_record["evidence_reference_catalog"], record.evidence,
                                        policy.evidence_reference_protocol)
                                    if next_effect_task(checkpoint.stages, task_candidate) is not None:
                                        await validate_plan(decision, validate_effects=False)
                                        checkpoint.effect_tasks = task_candidate
                                        round_record.update(status="parsed", decision=wire_decision.model_dump(mode="json"),
                                            canonical_task_receipt=task_candidate.completed[-1].model_dump(mode="json"))
                                        checkpoint.validation_feedback = None
                                        checkpoint.response_text = None
                                        checkpoint.phase = "ready"
                                        logger.info("case_planning_effect_task_accepted", extra={"project_id": record.project_id,
                                            "plan_id": record.id, "task": wire_decision.task, "round": checkpoint.next_round,
                                            "completed_tasks": len(task_candidate.completed)})
                                        await save("声明任务回执已保存，继续原剩余预算")
                                        continue
                            if isinstance(decision, (ReferencedEvidenceDecision, ReferencedEvidenceDecisionV2,
                                                      ReferencedEvidenceDecisionV3)):
                                reference_options = {"query_decision_type":SceneEvidencePlanningDecision} if policy.scene_query_protocol else {}
                                decision = bind_evidence_references(decision,
                                    round_record.get("evidence_reference_catalog"), record.evidence,
                                    reference_protocol=policy.evidence_reference_protocol, **reference_options)
                                round_record["canonical_evidence_decision"] = decision.model_dump(mode="json")
                            if isinstance(decision, LogicalPlanningDecisionV4) and (decision.action == "finish" or not policy.scene_query_protocol):
                                validate_operation_selections(decision, decision.operation_selections, record.evidence)
                                decision = LogicalPlanningDecisionV3.model_validate(decision.model_dump(mode="json", exclude={"operation_selections"}))
                            if decision.action == "finish" and stage == "effects" and not isinstance(wire_decision, EffectTaskDecision):
                                decision = merge_evidence_decision(checkpoint.stages, decision, record.evidence)
                            if decision.action == "finish":
                                if stage == "logic":
                                    validate_relation_logic_finish(decision, record.prompt_version)
                                if policy.preparation_scope_protocol is not None:
                                    round_record["preparation_scope_declaration"] = {
                                        item.key: {"cleanup_sources": list(item.cleanup_sources),
                                            "depends_on": list(item.depends_on)}
                                        for item in decision.items if getattr(item, "cleanup_sources", None) is not None}
                                await validate_plan(decision, validate_effects=stage != "logic")
                        except ValueError as exc:
                            round_record.update({"status": "validation_rejected", "validation_error": str(exc)})
                            checkpoint.validation_feedback = {"error": str(exc), "candidate_response": checkpoint.response_text,
                                "available_evidence_ids": [item.id for item in record.evidence]}
                            logger.warning("case_planning_validation_rejected", extra={"project_id": record.project_id,
                                "plan_id": record.id, "round": checkpoint.next_round})
                            if checkpoint.next_round >= policy.max_rounds:
                                raise
                            checkpoint.phase = "ready"
                            checkpoint.response_text = None
                            await save("模型计划校验未通过，已保存具体反馈")
                            continue
                        checkpoint.validation_feedback = None
                        if task_candidate is not None:
                            checkpoint.effect_tasks = task_candidate
                            round_record["canonical_task_receipt"] = task_candidate.completed[-1].model_dump(mode="json")
                            logger.info("case_planning_effect_task_accepted", extra={"project_id": record.project_id,
                                "plan_id": record.id, "task": wire_decision.task, "round": checkpoint.next_round,
                                "completed_tasks": len(task_candidate.completed)})
                        round_record.update({"status": "parsed", "decision": (
                            wire_decision if stage is not None else decision).model_dump(mode="json")})
                        logger.info("case_planning_round", extra={"project_id": record.project_id, "plan_id": record.id,
                            "round": checkpoint.next_round, "action": decision.action, "query_count": len(decision.queries)})
                        checkpoint.response_text = None
                        if decision.action == "finish":
                            if stage == "logic":
                                selections = wire_decision.operation_selections if isinstance(wire_decision, LogicalPlanningDecisionV4) else None
                                checkpoint.stages = seal_logical_decision(decision, operation_selections=selections)
                                if seed.stage_prompts.protocol == EFFECT_TASK_PROTOCOL:
                                    checkpoint.effect_tasks = EffectTaskCheckpoint()
                                round_record["logical_hash"] = checkpoint.stages.logical_hash
                                if any(item.kind == "target" for item in decision.items):
                                    checkpoint.query_results = []
                                    checkpoint.phase = "ready"
                                    if policy.operation_prefetch_protocol:
                                        queries, origins = declared_operation_queries(checkpoint.stages.logical_decision, checkpoint.stages.operation_selections)
                                        if queries:
                                            checkpoint.pending_decision = EvidencePlanningDecisionV3(action="query", summary="读取固定逻辑明确选择的操作契约", queries=queries[:PREFETCH_QUERY_BATCH_SIZE])
                                            checkpoint.query_index = 0
                                            checkpoint.phase = "query"
                                            round_record["operation_prefetch"] = {"protocol": policy.operation_prefetch_protocol,
                                                "logical_hash": checkpoint.stages.logical_hash, "declarations": origins,
                                                "next_offset": min(len(queries), PREFETCH_QUERY_BATCH_SIZE)}
                                            logger.info("case_planning_operation_prefetch_queued", extra={"project_id": record.project_id,
                                                "plan_id": record.id, "logical_hash": checkpoint.stages.logical_hash, "query_count": len(queries)})
                                    await save("逻辑计划已固定，等待执行证据声明")
                                    logger.info("case_planning_logic_frozen", extra={"project_id": record.project_id,
                                        "plan_id": record.id, "logical_hash": checkpoint.stages.logical_hash})
                                    continue
                                await validate_plan(decision, validate_effects=True)
                            await self._planner._projects.require_active(record.project_id)
                            record.decision, record.summary = decision, decision.summary
                            no_match = not any(item.kind == "target" for item in decision.items)
                            record.status = ("no_match" if no_match
                                else "blocked" if decision.unresolved else "planned")
                            if no_match:
                                record.summary = "未找到符合本次目标的正式用例；本次未创建测试运行。"
                                logger.info("case_planning_no_match", extra={"project_id": record.project_id,
                                    "plan_id": record.id, "unresolved_count": len(decision.unresolved)})
                            checkpoint.phase = "finished"
                            break
                        checkpoint.pending_decision = decision
                        checkpoint.query_index = 0
                        checkpoint.query_results = []
                        checkpoint.phase = "query"
                        await save("已保存资料查询决定")
                        continue
                    if checkpoint.phase == "query":
                        if checkpoint.pending_decision is None:
                            raise ValueError("查询检查点缺少结构化决定")
                        queries = checkpoint.pending_decision.queries
                        while checkpoint.query_index < len(queries):
                            query = queries[checkpoint.query_index]
                            result = {"query": query.model_dump(mode="json")}
                            if (policy.operation_prefetch_protocol and checkpoint.stages.stage == "effects"
                                and record.rounds[-1].get("planning_stage") == "logic"):
                                result.update(origin=policy.operation_prefetch_protocol, logical_hash=checkpoint.stages.logical_hash)
                            try:
                                if query.kind in {"tool", "tools"}:
                                    evidence, metadata = query_tool_contracts(seed.tool_contracts, query, policy.max_candidates,
                                        navigation=native_navigation, operations=native_operations)
                                    logger.info("case_planning_tool_query_resolved", extra={"plan_id": record.id,
                                        "round": checkpoint.next_round, "kind": query.kind,
                                        "tool_contract_count": len(evidence), "prompt_version": seed.plan.prompt_version,
                                        "operation": query.operation,
                                        "evidence_scope": metadata.get("scope", "full-tool" if evidence else "catalog")})
                                else:
                                    identity_options = {"identity_protocol": policy.source_identity_protocol} if policy.source_identity_protocol else {}
                                    if policy.scene_query_protocol:
                                        identity_options["scene_query_protocol"] = policy.scene_query_protocol
                                    if checkpoint.linked_sources is not None:
                                        identity_options["fixed_documents"] = linked_document_bases(checkpoint.linked_sources)
                                    evidence, metadata = await self._planner._sources.query(record.project_id, query, policy.max_candidates, **identity_options)
                                versions = dict(fixed_case_versions)
                                if pin_read_versions:
                                    versions.update({item.source_id: item.version for item in record.evidence if item.kind == "case"})
                                evidence = [await self._planner._sources.case(record.project_id, item.source_id, version_id=versions[item.source_id])
                                    if item.kind == "case" and item.source_id in versions else item for item in evidence]
                                if pin_read_versions:
                                    for item in evidence:
                                        if any(old.kind == item.kind and old.source_id == item.source_id and old.version != item.version for old in record.evidence):
                                            raise ValueError("项目资料在规划过程中已变化，不能混用来源版本")
                                self._planner._merge_evidence(record, evidence)
                                result.update({"evidence_ids": [item.id for item in evidence], **metadata})
                            except (KeyError, ValueError) as exc:
                                result["error"] = str(exc)
                                logger.warning("case_planning_query_rejected", extra={"project_id": record.project_id,
                                    "plan_id": record.id, "kind": query.kind, "error_type": type(exc).__name__})
                            checkpoint.query_results.append(result)
                            checkpoint.query_index += 1
                            record.rounds[-1]["query_results"] = list(checkpoint.query_results)
                            await save("项目资料查询结果已保存")
                        prefetch = record.rounds[-1].get("operation_prefetch") if (
                            policy.operation_prefetch_protocol and checkpoint.stages.stage == "effects"
                            and record.rounds[-1].get("planning_stage") == "logic") else None
                        if prefetch is not None:
                            offset = prefetch.get("next_offset", len(prefetch["declarations"]))
                            batch = prefetch["declarations"][offset:offset + PREFETCH_QUERY_BATCH_SIZE]
                            if batch:
                                checkpoint.pending_decision = EvidencePlanningDecisionV3(action="query", summary="继续读取固定逻辑明确选择的操作契约",
                                    queries=[PlanningQuery.model_validate(entry["query"]) for entry in batch])
                                prefetch["next_offset"] = offset + len(batch)
                                checkpoint.query_index = 0
                                await save("下一批明确操作查询已保存")
                                continue
                        checkpoint.pending_decision = None
                        checkpoint.phase = "ready"
                        await save("本轮依据查询完成")
        except (ContinuationLeaseLostError, PlanningCheckpointUnavailable):
            raise
        except TimeoutError:
            record.status = "retrieval_failed" if checkpoint.phase == "retrieval" else "planning_failed"
            record.summary = "用例规划超时；未创建测试运行"
            checkpoint.phase = "finished"
            logger.warning("case_planning_timeout", extra={"project_id": record.project_id, "plan_id": record.id})
        except Exception as exc:
            record.status = "retrieval_failed" if checkpoint.phase == "retrieval" else "planning_failed"
            record.summary = str(exc) if type(exc) is ValueError else f"规划失败（{type(exc).__name__}）；未创建测试运行"
            checkpoint.phase = "finished"
            logger.error("case_planning_failed", extra={"project_id": record.project_id,
                "plan_id": record.id, "error_type": type(exc).__name__})
        return checkpoint
