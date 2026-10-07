"""One planning loop for synchronous callers and durable Worker checkpoints."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from src.application.test_cases.planning_sources import json_text
from src.application.test_cases.planning_stages import merge_evidence_decision, seal_logical_decision
from src.schemas.case_planning_stages import (
    EvidencePlanningDecision, LogicalPlanningDecision, PlanningStageCheckpoint, STAGED_PROMPT_VERSION,
    RELATION_STAGE_PROTOCOL, RELATION_PROMPT_VERSION, LogicalPlanningDecisionV2, EvidencePlanningDecisionV2,
    LogicalPlanningDecisionV3, EvidencePlanningDecisionV3,
)
from src.schemas.case_effect_sources import SOURCE_PROMPT_VERSION
from src.application.test_cases.planning_tool_contracts import NAVIGATION_PROMPT_VERSION, OPERATION_PROMPT_VERSION, query_tool_contracts, tool_catalog
from src.runtime.store import ContinuationLeaseLostError
from src.schemas.case_planning_task import PLANNING_RESPONSE_PROTOCOL, planning_model_parameters
from src.schemas.model_config import MODEL_FAILURE_MODES, ModelInvocationRequest

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
        fixed_case_versions = dict(fixed_case_versions or {})
        if seed.plan.regression is not None:
            source = seed.plan.regression
            if fixed_case_versions and fixed_case_versions != source.required_case_versions:
                raise ValueError("回归目标不能替换持久任务的固定输入")
            fixed_case_versions = dict(source.required_case_versions)
            planning_context = source.context

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

        try:
            if seed.stage_prompts is not None:
                expected_version = SOURCE_PROMPT_VERSION if policy.effect_expected_source_protocol else RELATION_PROMPT_VERSION if seed.stage_prompts.protocol == RELATION_STAGE_PROTOCOL else STAGED_PROMPT_VERSION
                if (record.prompt_version != expected_version or seed.system_prompt != seed.stage_prompts.logic
                    or policy.planning_stage_protocol != seed.stage_prompts.protocol):
                    raise ValueError("阶段Prompt与固定规划版本不一致")
                if checkpoint.stages is None:
                    if checkpoint.phase != "retrieval":
                        raise ValueError("已开始的阶段规划缺少原阶段检查点")
                    checkpoint.stages = PlanningStageCheckpoint()
                PlanningStageCheckpoint.model_validate(checkpoint.stages.model_dump(mode="json"))
            remaining = remaining_seconds if remaining_seconds is not None else (deadline_at - datetime.now(timezone.utc)).total_seconds()
            if remaining <= 0:
                raise TimeoutError("planning deadline expired")
            async with asyncio.timeout(remaining):
                while checkpoint.phase != "finished":
                    await self._planner._projects.require_active(record.project_id)
                    if checkpoint.phase == "retrieval":
                        await self._planner._recall(record, fixed_case_versions)
                        checkpoint.catalog = await self._planner._sources.catalog(record.project_id, policy.max_candidates)
                        checkpoint.phase = "ready"
                        await save("候选完整版本与项目目录已保存")
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
                        stage = checkpoint.stages.stage if seed.stage_prompts is not None else None
                        prompt = seed.system_prompt
                        if stage is not None:
                            context["planning_stage"] = stage
                            prompt = getattr(seed.stage_prompts, stage)
                            if stage == "logic":
                                context["available_tools"] = [{"key": key, "name": value["name"]}
                                    for key, value in (seed.tool_contracts or {}).items()]
                            else:
                                context["logical_plan"] = checkpoint.stages.logical_decision.model_dump(mode="json")
                                context["logical_hash"] = checkpoint.stages.logical_hash
                        if seed.tool_contracts is not None and stage != "logic":
                            context["tool_catalog"] = tool_catalog(seed.tool_contracts, policy.max_candidates,
                                navigation=seed.plan.prompt_version in {NAVIGATION_PROMPT_VERSION, OPERATION_PROMPT_VERSION, STAGED_PROMPT_VERSION, RELATION_PROMPT_VERSION, SOURCE_PROMPT_VERSION},
                                operations=seed.tool_operations if seed.plan.prompt_version in {OPERATION_PROMPT_VERSION, STAGED_PROMPT_VERSION, RELATION_PROMPT_VERSION, SOURCE_PROMPT_VERSION} else None)
                        if fixed_case_versions:
                            context["constraints"] = {"required_case_versions": fixed_case_versions, "regression": planning_context or {}}
                        if record.environment is not None:
                            context["business_environment"] = record.environment.model_dump(mode="json")
                        body = json_text(context)
                        if len(body) + len(prompt) > policy.max_context_chars:
                            raise ValueError("完整规划上下文超过配置限制，未截断用例；请缩小候选范围或调整配置")
                        checkpoint.next_round += 1
                        round_record = {"round": checkpoint.next_round, "status": "invoking",
                            "logical_call_id": str(uuid4()), "attempt_id": attempt_id, "streaming": model_config.capabilities.streaming}
                        if stage is not None:
                            round_record["planning_stage"] = stage
                        record.rounds.append(round_record)
                        checkpoint.phase = "invoking"
                        await save("模型调用意图已保存")
                        logger.info("case_planning_model_invoking", extra={"project_id": record.project_id,
                            "plan_id": record.id, "logical_call_id": round_record["logical_call_id"],
                            "round": checkpoint.next_round, "attempt_id": attempt_id})
                        # Preserve existing synchronous tracing. Durable tasks
                        # use the Worker request context without a fake Session.
                        trace = ({"project_id": record.project_id, "trace_id": record.id,
                            "turn_id": f"{record.id}:{checkpoint.next_round}", "session_id": record.id,
                            "agent_key": "case-planning"} if persist is None else {})
                        model_request = ModelInvocationRequest(system_prompt=prompt,
                            messages=[{"role": "user", "content": body}], tools=[], trace_context=trace)
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
                        if mode in MODEL_FAILURE_MODES:
                            raise ValueError(f"模型调用失败（{mode}）；未创建测试运行")
                        if checkpoint.response_has_tools:
                            raise ValueError("规划模型返回了不可执行的工具调用；此阶段只能查询项目依据")
                        if seed.response_protocol == PLANNING_RESPONSE_PROTOCOL:
                            metadata = checkpoint.response_metadata
                            disposition = ("truncated" if metadata.get("finish_reason") in {"length", "max_tokens"}
                                or metadata.get("stop_reason") == "max_tokens"
                                else "empty" if not (checkpoint.response_text or "").strip() else "received")
                            if disposition != "received":
                                reason = "模型规划输出达到生成上限，响应不完整" if disposition == "truncated" else "模型规划响应正文为空"
                                round_record.update({"status": "response_rejected", "response_disposition": disposition,
                                    "response_protocol": seed.response_protocol, "validation_error": reason,
                                    "response_text": checkpoint.response_text})
                                logger.warning("case_planning_response_incomplete", extra={"project_id": record.project_id,
                                    "plan_id": record.id, "round": checkpoint.next_round,
                                    "response_protocol": seed.response_protocol, "response_disposition": disposition,
                                    "finish_reason": metadata.get("finish_reason"), "stop_reason": metadata.get("stop_reason")})
                                raise ValueError(f"{reason}；已保留响应，不自动重发；未创建测试运行")
                        try:
                            stage = checkpoint.stages.stage if seed.stage_prompts is not None else None
                            logic_type, effects_type = (LogicalPlanningDecisionV2, EvidencePlanningDecisionV2) if (
                                seed.stage_prompts is not None and seed.stage_prompts.protocol == RELATION_STAGE_PROTOCOL) else (
                                LogicalPlanningDecision, EvidencePlanningDecision)
                            if policy.effect_expected_source_protocol:
                                logic_type, effects_type = LogicalPlanningDecisionV3, EvidencePlanningDecisionV3
                            decision = (self._planner._parse(checkpoint.response_text or "", decision_type=(
                                logic_type if stage == "logic" else effects_type))
                                if stage is not None else self._planner._parse(checkpoint.response_text or ""))
                            wire_decision = decision.model_copy(deep=True)
                            if decision.action == "finish" and stage == "effects":
                                decision = merge_evidence_decision(checkpoint.stages, decision, record.evidence)
                            if decision.action == "finish":
                                if policy.preparation_scope_protocol is not None:
                                    round_record["preparation_scope_declaration"] = {
                                        item.key: {"cleanup_sources": list(item.cleanup_sources),
                                            "depends_on": list(item.depends_on)}
                                        for item in decision.items if getattr(item, "cleanup_sources", None) is not None}
                                versions = {}
                                if pin_read_versions:
                                    versions.update({item.source_id: item.version for item in record.evidence if item.kind == "case"})
                                versions.update(fixed_case_versions)
                                await self._planner._validate(record, decision, fixed_case_versions=versions,
                                    preserve_read_metadata=pin_read_versions, validate_effects=stage != "logic")
                                if fixed_case_versions and {item.case_id: item.case_version_id for item in decision.items if item.kind == "target"} != fixed_case_versions:
                                    raise ValueError("回归目标必须完整保留指定用例及版本，其他正式项只能作为必要依赖")
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
                        round_record.update({"status": "parsed", "decision": (
                            wire_decision if stage is not None else decision).model_dump(mode="json")})
                        logger.info("case_planning_round", extra={"project_id": record.project_id, "plan_id": record.id,
                            "round": checkpoint.next_round, "action": decision.action, "query_count": len(decision.queries)})
                        checkpoint.response_text = None
                        if decision.action == "finish":
                            if stage == "logic":
                                checkpoint.stages = seal_logical_decision(decision)
                                round_record["logical_hash"] = checkpoint.stages.logical_hash
                                if any(item.kind == "target" for item in decision.items):
                                    checkpoint.query_results = []
                                    checkpoint.phase = "ready"
                                    await save("逻辑计划已固定，等待执行证据声明")
                                    logger.info("case_planning_logic_frozen", extra={"project_id": record.project_id,
                                        "plan_id": record.id, "logical_hash": checkpoint.stages.logical_hash})
                                    continue
                                await self._planner._validate(record, decision, fixed_case_versions=versions,
                                    preserve_read_metadata=pin_read_versions)
                            await self._planner._projects.require_active(record.project_id)
                            record.decision, record.summary = decision, decision.summary
                            record.status = ("no_match" if not any(item.kind == "target" for item in decision.items)
                                else "blocked" if decision.unresolved else "planned")
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
                            try:
                                if query.kind in {"tool", "tools"}:
                                    evidence, metadata = query_tool_contracts(seed.tool_contracts, query, policy.max_candidates,
                                        navigation=seed.plan.prompt_version in {NAVIGATION_PROMPT_VERSION, OPERATION_PROMPT_VERSION, STAGED_PROMPT_VERSION, RELATION_PROMPT_VERSION, SOURCE_PROMPT_VERSION},
                                        operations=seed.tool_operations if seed.plan.prompt_version in {OPERATION_PROMPT_VERSION, STAGED_PROMPT_VERSION, RELATION_PROMPT_VERSION, SOURCE_PROMPT_VERSION} else None)
                                    logger.info("case_planning_tool_query_resolved", extra={"plan_id": record.id,
                                        "round": checkpoint.next_round, "kind": query.kind,
                                        "tool_contract_count": len(evidence), "prompt_version": seed.plan.prompt_version,
                                        "operation": query.operation,
                                        "evidence_scope": metadata.get("scope", "full-tool" if evidence else "catalog")})
                                else:
                                    evidence, metadata = await self._planner._sources.query(record.project_id, query, policy.max_candidates)
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
