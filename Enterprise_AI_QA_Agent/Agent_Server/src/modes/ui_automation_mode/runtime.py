from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha1
from typing import Any
from urllib.parse import urlparse

from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.testing.ui_exploration_service import UIExplorationService
from src.modes.ui_automation_mode.contracts import (
    UI_AUTOMATION_BOSS,
    UI_AUTOMATION_DIRECTIONS,
    UI_AUTOMATION_SUBDIRECTIONS,
    direction_option,
    subdirection_option,
)


@dataclass(slots=True)
class UIAutomationRequestState:
    target_url: str
    objective: str
    project_id: str
    project_scope: str
    direction: str
    subdirection: str
    action: str
    max_pages: int
    max_interactions: int
    same_origin_only: bool
    knowledge_gap_reason: str
    login_credentials: dict[str, str]


class UIAutomationModeRuntime:
    def __init__(
        self,
        *,
        memory_runtime_service: MemoryRuntimeService | None = None,
        ui_exploration_service: UIExplorationService | None = None,
        resource_assessor: Any | None = None,
        recording_approval_service: Any | None = None,
        project_catalog_provider: Any | None = None,
    ) -> None:
        self._memory_runtime_service = memory_runtime_service
        self._ui_exploration_service = ui_exploration_service
        # UI 录制编排（方案第 4 章 / P0-8）：三源检索 + 录制审批分支
        self._resource_assessor = resource_assessor
        self._recording_approval_service = recording_approval_service
        self._project_catalog_provider = project_catalog_provider

    def set_memory_runtime_service(self, memory_runtime_service: MemoryRuntimeService | None) -> None:
        self._memory_runtime_service = memory_runtime_service

    def set_recording_orchestration(
        self,
        *,
        resource_assessor: Any | None = None,
        recording_approval_service: Any | None = None,
        project_catalog_provider: Any | None = None,
    ) -> None:
        """注入 UI 录制编排依赖（main.py 在 recorder 域初始化后调用）。"""
        if resource_assessor is not None:
            self._resource_assessor = resource_assessor
        if recording_approval_service is not None:
            self._recording_approval_service = recording_approval_service
        if project_catalog_provider is not None:
            self._project_catalog_provider = project_catalog_provider

    async def handle(self, arguments: dict[str, Any], context) -> dict[str, Any]:
        request = self._resolve_request(arguments, context)
        hierarchy = self._hierarchy_snapshot(request)

        # 环节② 项目确认（方案 4.2）：project_id 缺失时反问——
        # 派生 scope 不能冒充正式 project_id（AGENTS.md 禁止自由文本项目名替代）。
        if not request.project_id:
            return self._build_result(
                status="partial",
                summary="请先选择要测试的项目：录制、资源检索与图谱写入都需要正式项目。",
                phase="awaiting_project_selection",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate={"decision": "missing_project", "reason": "project_id_required"},
                next_actions=["从候选项目中选择", "输入正式 project_id"],
                extras={"project_candidates": await self._project_candidates()},
            )

        if not request.target_url and len(request.objective) < 6:
            return self._build_result(
                status="partial",
                summary="缺少可执行的测试目标信息，请先补充目标地址或更明确的探索需求。",
                phase="awaiting_input",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate={**await self._assess_knowledge(request=request, context=context), "decision": "missing_target_info", "reason": "missing_target_info"},
                next_actions=["补充目标地址", "补充更明确的探索需求"],
            )

        knowledge_gate = await self._assess_resources(request=request, context=context)

        # 环节③→④（方案 4.1）：三源不足且用户未显式选择 AI 探索 → 发起录制审批
        if (
            knowledge_gate.get("decision") == "need_recording"
            and not (request.direction and request.subdirection)
            and self._recording_approval_service is not None
        ):
            approval = await self._recording_approval_service.create_approval(
                session_id=context.session_id,
                turn_id=context.turn_id,
                request=request,
                knowledge_gate=knowledge_gate,
            )
            return self._build_result(
                status="partial",
                summary=(
                    "当前项目缺少可复用的 UI 测试资源，已发起浏览器录制审批；"
                    "审批通过后将自动打开录制窗口。"
                ),
                phase="awaiting_recording_approval",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate=knowledge_gate,
                next_actions=["在审批面板处理录制审批", "或改选 AI 页面探索（方向/子方向）"],
                extras={
                    "approval_id": approval.id,
                    "approval_type": "ui_recording",
                    "recording_request": approval.metadata.get("recording_request"),
                },
            )

        if knowledge_gate["decision"] in {"need_exploration", "need_recording"} and (not request.direction or not request.subdirection):
            return self._build_result(
                status="partial",
                summary=(
                    "当前知识库不足以直接生成测试任务，需要先选择方向和子方向执行页面信息探索"
                    + ("（录制审批服务未启用，走 AI 探索降级链路）。" if knowledge_gate["decision"] == "need_recording" else "。")
                ),
                phase="awaiting_exploration_selection",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate=knowledge_gate,
                next_actions=["选择方向", "选择子方向", "启动信息探索"],
            )

        if request.direction and request.subdirection:
            return await self._run_selected_worker(
                request=request,
                hierarchy=hierarchy,
                knowledge_gate=knowledge_gate,
                context=context,
            )

        return self._build_result(
            status="partial",
            summary="当前知识命中已具备后续任务生成条件，后续可以进入测试任务生成和任务池执行。",
            phase="task_generation_ready",
            request=request,
            hierarchy=hierarchy,
            knowledge_gate={
                **knowledge_gate,
                "decision": "task_generation_ready",
                # 三源检索时保留 assessor 的判定理由（审计可见），memory-only 时维持既有文案
                "reason": knowledge_gate.get("reason") or "knowledge_sufficient",
            },
            next_actions=["生成测试任务", "接入任务池与多 Agent 执行"],
        )

    async def _run_selected_worker(
        self,
        *,
        request: UIAutomationRequestState,
        hierarchy: dict[str, Any],
        knowledge_gate: dict[str, Any],
        context,
    ) -> dict[str, Any]:
        if request.direction != "browser":
            return self._build_result(
                status="partial",
                summary="当前版本只开放浏览器方向，其他方向仍保留组长位但不执行。",
                phase="direction_not_supported",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate=knowledge_gate,
                next_actions=["改选浏览器方向", "等待其他方向接入"],
            )

        if request.subdirection != "information_exploration":
            return self._build_result(
                status="partial",
                summary="当前版本只实现浏览器方向的信息探索员工，测试执行员工先占位。",
                phase="subdirection_not_supported",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate=knowledge_gate,
                next_actions=["改选信息探索", "等待测试执行员工接入"],
            )

        if not request.target_url:
            return self._build_result(
                status="partial",
                summary="浏览器信息探索需要目标地址，当前请求缺少 target_url。",
                phase="awaiting_input",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate={**knowledge_gate, "reason": "missing_target_url"},
                next_actions=["补充目标地址", "重新启动浏览器信息探索"],
            )

        if self._ui_exploration_service is None:
            return self._build_result(
                status="failed",
                summary="UI 探索服务未配置，无法启动旧版页面探索服务。",
                phase="exploration_failed",
                request=request,
                hierarchy=hierarchy,
                knowledge_gate=knowledge_gate,
                next_actions=["检查 UI 探索服务配置"],
            )

        exploration_arguments = {
            "target_url": request.target_url,
            "objective": request.objective,
            "project_id": request.project_id,
            "project_scope": request.project_scope,
            "max_pages": request.max_pages,
            "max_interactions": request.max_interactions,
            "same_origin_only": request.same_origin_only,
        }
        if request.login_credentials:
            exploration_arguments["login_credentials"] = request.login_credentials

        exploration_result = await self._ui_exploration_service.explore(exploration_arguments, context)
        metrics = exploration_result.get("metrics") if isinstance(exploration_result.get("metrics"), dict) else {}
        graph_storage = (
            exploration_result.get("graph_storage")
            if isinstance(exploration_result.get("graph_storage"), dict)
            else {}
        )
        graph_storage_metrics = (
            graph_storage.get("metrics")
            if isinstance(graph_storage.get("metrics"), dict)
            else {}
        )
        result_status = "completed" if exploration_result.get("status") == "success" and exploration_result.get("ok", True) else "failed"
        phase = "exploration_completed" if result_status == "completed" else "exploration_failed"
        summary = str(exploration_result.get("summary") or "页面交互信息采集已完成。").strip()

        ui_state = self._base_state(
            phase=phase,
            request=request,
            hierarchy=hierarchy,
            knowledge_gate=knowledge_gate,
        )
        ui_state["employee_runtime"] = {
            "available": self._ui_exploration_service is not None,
            "mode": "legacy_ui_explorer",
            "note": "Using the legacy UIExplorationService backed by python_playwright_cli.",
            "hands": "python_playwright_cli",
            "brain": "legacy_ui_explorer",
            "tool_policy": "The legacy explorer directly drives python_playwright_cli to breadth-explore reachable UI structure.",
        }
        ui_state["exploration"] = {
            "entry_url": request.target_url,
            "project_scope": request.project_scope,
            "project_id": request.project_id,
            "exploration_complete": result_status == "completed",
            "remaining_observable_targets": [],
            "graph_write_status": str(graph_storage.get("status") or "unknown"),
            "artifact_count": len(exploration_result.get("artifacts") or []),
            "memory_write_count": len(exploration_result.get("memory_write_ids") or []),
            "metrics": {
                "page_count": int(metrics.get("page_count") or 0),
                "element_count": int(metrics.get("element_count") or 0),
                "entity_count": int(metrics.get("entity_count") or 0),
                "edge_count": int(graph_storage_metrics.get("edges") or metrics.get("edge_count") or 0),
                "interaction_count": int(metrics.get("interaction_count") or 0),
                "login_event_count": int(metrics.get("login_event_count") or 0),
            },
            "graph_storage": graph_storage,
        }
        ui_state["artifacts"] = exploration_result.get("artifacts") or []
        return {
            "status": result_status,
            "ok": result_status != "failed",
            "summary": summary,
            "target_url": request.target_url,
            "objective": request.objective,
            "project_scope": request.project_scope,
            "ui_automation_state": ui_state,
            "exploration_result": exploration_result,
            "artifacts": exploration_result.get("artifacts") or [],
            "metrics": metrics,
            "next_actions": ["查看探索摘要和图谱写入结果", "后续将任务生成接入任务池"],
        }

    async def _assess_resources(self, *, request: UIAutomationRequestState, context) -> dict[str, Any]:
        """环节③ 三源检索（方案 4.2）：assessor 可用走图谱/用例/记忆三源，
        否则退回既有 memory-only 评估（不破坏旧链路）。"""
        if self._resource_assessor is None:
            return await self._assess_knowledge(request=request, context=context)
        return await self._resource_assessor.assess(
            project_id=request.project_id,
            target_url=request.target_url,
            objective=request.objective,
            project_scope=request.project_scope,
            context=context,
        )

    async def _project_candidates(self) -> list[dict[str, Any]]:
        """候选项目列表（反问时一次给全，方案 11 风险对策）。不可用时返回空列表。"""
        if self._project_catalog_provider is None:
            return []
        try:
            candidates = await self._project_catalog_provider()
            if isinstance(candidates, list):
                return [
                    item if isinstance(item, dict) else {"project_id": str(item)}
                    for item in candidates
                ]
        except Exception:
            return []
        return []

    async def _assess_knowledge(self, *, request: UIAutomationRequestState, context) -> dict[str, Any]:
        query_parts = [request.target_url, request.objective, request.project_scope]
        query = " ".join(part for part in query_parts if part).strip()
        if not query or self._memory_runtime_service is None:
            return {
                "decision": "need_exploration",
                "reason": "memory_service_unavailable" if self._memory_runtime_service is None else "empty_query",
                "sufficient": False,
                "memory_hit_count": 0,
                "total_docs": 0,
                "max_score": 0.0,
            }

        memory_result = await self._memory_runtime_service.retrieve_for_turn(
            session_id=context.session_id,
            trace_id=context.trace_id,
            query=query,
            context=context.context_bundle,
        )
        scores = [float(item.score or 0.0) for item in memory_result.hits]
        max_score = max(scores, default=0.0)
        hit_count = len(memory_result.hits)
        total_docs = int(memory_result.total_docs or 0)
        sufficient = hit_count >= 3 or (hit_count >= 1 and total_docs >= 6) or max_score >= 0.78
        reason = "knowledge_sufficient" if sufficient else "knowledge_hit_low"
        return {
            "decision": "task_generation_ready" if sufficient else "need_exploration",
            "reason": reason,
            "sufficient": sufficient,
            "memory_hit_count": hit_count,
            "total_docs": total_docs,
            "max_score": round(max_score, 4),
        }

    def _resolve_request(self, arguments: dict[str, Any], context) -> UIAutomationRequestState:
        mode_request = (
            context.context_bundle.get("ui_automation_request")
            if isinstance(context.context_bundle.get("ui_automation_request"), dict)
            else {}
        )
        target_url = self._first_text(
            arguments.get("target_url"),
            mode_request.get("target_url"),
            context.context_bundle.get("target_url"),
            self._extract_url(context.user_message),
        )
        objective = self._first_text(
            arguments.get("objective"),
            mode_request.get("objective"),
            context.context_bundle.get("objective"),
            context.user_message,
        )
        direction = self._first_text(
            arguments.get("direction"),
            mode_request.get("direction"),
            context.context_bundle.get("ui_automation_direction"),
        )
        subdirection = self._first_text(
            arguments.get("subdirection"),
            mode_request.get("subdirection"),
            context.context_bundle.get("ui_automation_subdirection"),
        )
        project_scope = self._first_text(
            arguments.get("project_scope"),
            mode_request.get("project_scope"),
            context.context_bundle.get("project_scope"),
        )
        project_id = self._first_text(
            arguments.get("project_id"),
            mode_request.get("project_id"),
            context.context_bundle.get("project_id"),
        )
        if not project_scope:
            project_scope = self._derive_project_scope(
                target_url=target_url,
                objective=objective,
                session_id=context.session_id,
            )
        return UIAutomationRequestState(
            target_url=target_url,
            objective=objective,
            project_id=project_id,
            project_scope=project_scope,
            direction=direction,
            subdirection=subdirection,
            action=self._first_text(arguments.get("action"), mode_request.get("action")) or "assess",
            max_pages=self._bounded_int(arguments.get("max_pages") or mode_request.get("max_pages"), default=12, minimum=1, maximum=24),
            max_interactions=self._bounded_int(arguments.get("max_interactions") or mode_request.get("max_interactions"), default=40, minimum=0, maximum=80),
            same_origin_only=self._coerce_bool(
                arguments.get("same_origin_only") if "same_origin_only" in arguments else mode_request.get("same_origin_only"),
                default=True,
            ),
            knowledge_gap_reason=self._first_text(mode_request.get("knowledge_gap_reason"), arguments.get("knowledge_gap_reason")),
            login_credentials=self._extract_login_credentials(arguments=arguments, mode_request=mode_request, objective=objective),
        )

    def _build_result(
        self,
        *,
        status: str,
        summary: str,
        phase: str,
        request: UIAutomationRequestState,
        hierarchy: dict[str, Any],
        knowledge_gate: dict[str, Any],
        next_actions: list[str],
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "ok": status != "failed",
            "summary": summary,
            "target_url": request.target_url,
            "objective": request.objective,
            "project_scope": request.project_scope,
            "ui_automation_state": {
                **self._base_state(
                    phase=phase,
                    request=request,
                    hierarchy=hierarchy,
                    knowledge_gate=knowledge_gate,
                ),
                "employee_runtime": {
                    "available": self._ui_exploration_service is not None,
                    "mode": "legacy_ui_explorer",
                    "note": "Using the legacy UIExplorationService backed by python_playwright_cli.",
                    "hands": "python_playwright_cli",
                    "brain": "legacy_ui_explorer",
                },
                "next_actions": next_actions,
                **(extras or {}),
            },
            "next_actions": next_actions,
        }

    def _base_state(
        self,
        *,
        phase: str,
        request: UIAutomationRequestState,
        hierarchy: dict[str, Any],
        knowledge_gate: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "phase": phase,
            "request": {
                "target_url": request.target_url,
                "objective": request.objective,
                "project_scope": request.project_scope,
                "project_id": request.project_id,
                "direction": request.direction,
                "subdirection": request.subdirection,
                "max_pages": request.max_pages,
                "max_interactions": request.max_interactions,
                "same_origin_only": request.same_origin_only,
                "knowledge_gap_reason": request.knowledge_gap_reason,
                "login_credentials": request.login_credentials,
            },
            "knowledge_gate": knowledge_gate,
            "hierarchy": hierarchy,
        }

    def _hierarchy_snapshot(self, request: UIAutomationRequestState) -> dict[str, Any]:
        return {
            "boss": dict(UI_AUTOMATION_BOSS),
            "direction_options": [dict(item) for item in UI_AUTOMATION_DIRECTIONS],
            "subdirection_options": [dict(item) for item in UI_AUTOMATION_SUBDIRECTIONS],
            "selected_direction": direction_option(request.direction) if request.direction else None,
            "selected_subdirection": subdirection_option(request.subdirection) if request.subdirection else None,
        }

    def _extract_login_credentials(
        self,
        *,
        arguments: dict[str, Any],
        mode_request: dict[str, Any],
        objective: str,
    ) -> dict[str, str]:
        login_credentials = arguments.get("login_credentials")
        if isinstance(login_credentials, dict):
            username = self._first_text(
                login_credentials.get("username"),
                login_credentials.get("email"),
                login_credentials.get("account"),
            )
            password = self._first_text(login_credentials.get("password"))
            if username or password:
                return {"username": username, "password": password}

        mode_credentials = mode_request.get("login_credentials")
        if isinstance(mode_credentials, dict):
            username = self._first_text(
                mode_credentials.get("username"),
                mode_credentials.get("email"),
                mode_credentials.get("account"),
            )
            password = self._first_text(mode_credentials.get("password"))
            if username or password:
                return {"username": username, "password": password}

        text = str(objective or "")
        account_match = re.search(r"(?:账号|帐号|account|username)\s*[:：]?\s*([A-Za-z0-9@._-]{4,})", text, flags=re.IGNORECASE)
        password_match = re.search(r"(?:密码|password|passwd)\s*[:：]?\s*([A-Za-z0-9@._!#$%^&*()-=+]{4,})", text, flags=re.IGNORECASE)
        username = account_match.group(1).strip() if account_match else ""
        password = password_match.group(1).strip() if password_match else ""
        return {"username": username, "password": password} if username or password else {}

    def _extract_url(self, text: str) -> str:
        match = re.search(r"https?://[^\s]+", text or "", flags=re.IGNORECASE)
        return match.group(0).strip() if match else ""

    def _derive_project_scope(self, *, target_url: str, objective: str, session_id: str) -> str:
        host = urlparse(target_url).netloc.lower()
        if host:
            scope = re.sub(r"[^a-z0-9._-]+", "-", host).strip("-")
            return scope or f"ui-project-{session_id[:8]}"
        seed = objective or session_id
        digest = sha1(seed.encode("utf-8")).hexdigest()[:10]
        return f"ui-project-{digest}"

    def _first_text(self, *values: Any) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _bounded_int(self, value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))

    def _coerce_bool(self, value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default
