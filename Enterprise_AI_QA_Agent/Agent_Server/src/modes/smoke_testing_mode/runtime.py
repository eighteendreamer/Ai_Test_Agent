from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from src.application.artifacts.artifact_storage_service import ArtifactStorageService
from src.application.context.memory_runtime_service import MemoryRuntimeService
from src.application.documents.api_docs_service import ApiDocsService
from src.application.knowledge.knowledge_graph_service import KnowledgeGraphService
from src.core.config import Settings
from src.modes.smoke_testing_mode.catalog_store import SmokeCatalogStore
from src.modes.smoke_testing_mode.contracts import (
    SmokeApiStep,
    SmokeAssertion,
    SmokeCase,
    SmokeCaseResult,
    SmokeExecutionPlan,
    SmokePlanRevision,
    SmokeRunResult,
    SmokeSource,
    SmokeStep,
    SmokeUiStep,
    utc_now,
)
from src.modes.smoke_testing_mode.plan_store import SmokePlanStore
from src.modes.smoke_testing_mode.project_resolver import SmokeProjectResolver
from src.modes.smoke_testing_mode.result_analyzer import SmokeResultAnalyzer
from src.modes.smoke_testing_mode.source_resolver import SmokeSourceResolver


class SmokeTestingModeRuntime:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        api_docs_service: ApiDocsService | None = None,
        memory_runtime_service: MemoryRuntimeService | None = None,
        artifact_storage_service: ArtifactStorageService | None = None,
        project_service=None,
        knowledge_graph_service: KnowledgeGraphService | None = None,
    ) -> None:
        self._settings = settings
        self._api_docs_service = api_docs_service
        self._memory_runtime_service = memory_runtime_service
        knowledge = knowledge_graph_service or (KnowledgeGraphService(settings) if settings is not None else None)
        if knowledge is not None and project_service is not None and getattr(knowledge, "_projects", None) is None:
            knowledge.set_project_service(project_service)
        catalog = SmokeCatalogStore(settings) if settings is not None else None
        self._project_resolver = SmokeProjectResolver(
            api_docs_service=api_docs_service,
            knowledge_graph_service=knowledge,
            project_service=project_service,
        )
        self._source_resolver = SmokeSourceResolver(
            api_docs_service=api_docs_service,
            knowledge_graph_service=knowledge,
            memory_runtime_service=memory_runtime_service,
        )
        self._plan_store = SmokePlanStore(
            artifact_storage_service=artifact_storage_service,
            catalog_store=catalog,
        )
        self._result_analyzer = SmokeResultAnalyzer()
        self._initialized = False
        self._init_warnings: list[str] = []

    def set_memory_runtime_service(self, memory_runtime_service: MemoryRuntimeService) -> None:
        self._memory_runtime_service = memory_runtime_service
        self._source_resolver._memory_runtime_service = memory_runtime_service

    async def handle(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        await self._ensure_initialized()
        action = str(arguments.get("action") or "draft_plan").strip().lower()
        if action in {"draft", "plan"}:
            action = "draft_plan"
        if action in {"revise", "revision"}:
            action = "revise_plan"
        if action in {"execute", "run"}:
            action = "execute_approved_plan"

        if action == "draft_plan":
            return await self._draft_plan(arguments, context)
        if action == "revise_plan":
            return await self._revise_plan(arguments, context)
        if action == "execute_approved_plan":
            return await self._execute_plan(arguments, context)
        if action == "get_plan":
            return await self._get_plan(arguments)
        return {
            "status": "failed",
            "ok": False,
            "summary": f"Unsupported smoke-suite-runner action: {action}",
            "error": "unsupported_action",
        }

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        try:
            await self._plan_store.initialize()
        except Exception as exc:
            self._init_warnings.append(f"PostgreSQL catalog 初始化失败：{exc}")
        finally:
            self._initialized = True

    async def _draft_plan(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        target_url = self._target_url(arguments, context)
        objective = self._objective(arguments, context)
        explicit_scope = str(arguments.get("project_scope") or "").strip()
        project_scope, project_matches, project_warnings = await self._project_resolver.resolve(
            target_url=target_url,
            explicit_project_scope=explicit_scope,
        )
        project_id = next(
            (
                str(match.metadata.get("project_id") or "").strip()
                for match in project_matches
                if str(match.metadata.get("project_id") or "").strip()
            ),
            None,
        )
        attachments = self._attachments(context)
        attachment_ids = self._string_list(arguments.get("attachment_ids"))
        if attachment_ids:
            wanted = set(attachment_ids)
            attachments = [
                item for item in attachments
                if str((item.get("metadata") or {}).get("attachment_id") or item.get("id") or "") in wanted
            ]
        api_doc_ids = self._string_list(arguments.get("api_doc_ids"))
        source_bundle = await self._source_resolver.resolve(
            project_scope=project_scope,
            project_id=project_id,
            target_url=target_url,
            session_id=str(getattr(context, "session_id", "") or ""),
            trace_id=str(getattr(context, "trace_id", "") or ""),
            api_doc_ids=api_doc_ids,
            attachments=attachments,
            max_api_docs=max(1, min(int(arguments.get("max_api_docs") or 3), 8)),
        )
        plan = self._build_plan(
            objective=objective,
            target_url=target_url,
            project_scope=project_scope,
            source_bundle=source_bundle,
            arguments=arguments,
        )
        plan.review_notes.extend(project_warnings)
        if project_matches:
            plan.review_notes.append(f"project_scope 自动匹配为 {project_scope}。")
        plan, storage_warnings = await self._plan_store.save_plan_version(plan)
        summary = f"冒烟测试方案 v{plan.version} 已生成，包含 {len(plan.cases)} 条用例，等待用户确认或修改。"
        return self._plan_output(
            plan,
            status="partial",
            summary=summary,
            extra={
                "action": "draft_plan",
                "phase": "awaiting_user_confirmation",
                "project_matches": [match.__dict__ for match in project_matches[:5]],
                "storage_warnings": [*self._init_warnings, *project_warnings, *source_bundle.get("warnings", []), *storage_warnings],
            },
        )

    async def _revise_plan(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        plan, warnings = await self._load_plan_from_args(arguments)
        if plan is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": "未找到可修订的冒烟测试方案，请先生成方案。",
                "error": "plan_not_found",
                "storage_warnings": warnings,
            }
        user_revision = str(arguments.get("user_revision") or getattr(context, "user_message", "") or "").strip()
        if not user_revision:
            return {
                "status": "failed",
                "ok": False,
                "summary": "修订方案需要提供 user_revision。",
                "error": "missing_revision",
            }
        plan.version += 1
        plan.status = "awaiting_user_confirmation"
        plan.updated_at = utc_now()
        plan.revisions.append(
            SmokePlanRevision(
                user_revision=user_revision,
                summary="根据用户修改意见更新执行选择与风险设置。",
            )
        )
        self._apply_revision(plan, user_revision, arguments)
        plan.risk_summary = self._risk_summary(plan.cases)
        plan, storage_warnings = await self._plan_store.save_plan_version(
            plan,
            revision_reason="user_revision",
            user_revision=user_revision,
        )
        return self._plan_output(
            plan,
            status="partial",
            summary=f"冒烟测试方案已修订为 v{plan.version}，请再次确认是否执行。",
            extra={"action": "revise_plan", "phase": "awaiting_user_confirmation", "storage_warnings": [*self._init_warnings, *warnings, *storage_warnings]},
        )

    async def _execute_plan(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        plan, warnings = await self._load_plan_from_args(arguments)
        if plan is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": "未找到可执行的冒烟测试方案。",
                "error": "plan_not_found",
                "storage_warnings": warnings,
            }
        selected_case_ids = self._string_list(arguments.get("selected_case_ids"))
        if selected_case_ids:
            selected = set(selected_case_ids)
            for case in plan.cases:
                case.selected = case.case_id in selected
        if arguments.get("selected_indices") is not None:
            selected_indices = set(self._int_list(arguments.get("selected_indices")))
            for idx, case in enumerate(plan.cases, start=1):
                case.selected = idx in selected_indices
        plan, approval_warnings = await self._plan_store.save_approved_plan(
            plan,
            selected_case_ids=[case.case_id for case in plan.cases if case.selected],
        )
        result = SmokeRunResult(
            plan_id=plan.plan_id,
            plan_version=plan.version,
            project_scope=plan.project_scope,
            target_url=plan.target_url,
            status="partial",
        )
        selected_cases = [case for case in plan.cases if case.selected and case.execution_eligible]
        credentials = await self._lookup_execution_credentials(context, plan)
        for case in selected_cases:
            case_result = await self._execute_case(case, plan, credentials=credentials)
            result.case_results.append(case_result)
        result, regression_candidates, report_markdown = self._result_analyzer.finalize(plan=plan, result=result)
        result, result_warnings = await self._plan_store.save_run_result(
            plan=plan,
            result=result,
            report_markdown=report_markdown,
            regression_candidates=regression_candidates,
        )
        return {
            "status": "completed" if result.verdict == "ready" else "partial" if result.verdict == "partial" else "failed",
            "ok": result.verdict == "ready",
            "summary": result.summary,
            "action": "execute_approved_plan",
            "phase": result.verdict,
            "plan": plan.model_dump(mode="json"),
            "run_result": result.model_dump(mode="json"),
            "report_markdown": report_markdown,
            "regression_candidates": [item.model_dump(mode="json") for item in regression_candidates],
            "plan_id": plan.plan_id,
            "plan_version": plan.version,
            "selected_case_ids": [case.case_id for case in selected_cases],
            "approved_plan_uri": plan.rustfs_uris.get("approved_plan_uri", ""),
            "run_result_uri": result.rustfs_uris.get("run_result_uri", ""),
            "report_uri": result.rustfs_uris.get("report_uri", ""),
            "evidence_uris": [evidence.get("uri") for item in result.case_results for evidence in item.evidence if evidence.get("uri")],
            "storage_warnings": [*self._init_warnings, *warnings, *approval_warnings, *result_warnings],
        }

    async def _get_plan(self, arguments: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(arguments.get("plan_id") or "").strip()
        plan, warnings = await self._plan_store.load_plan(plan_id)
        if plan is None:
            return {"status": "failed", "ok": False, "summary": "未找到冒烟测试方案。", "storage_warnings": warnings}
        return self._plan_output(plan, status="completed", summary=f"已加载冒烟测试方案 v{plan.version}。", extra={"storage_warnings": warnings})

    def _build_plan(
        self,
        *,
        objective: str,
        target_url: str,
        project_scope: str,
        source_bundle: dict[str, Any],
        arguments: dict[str, Any],
    ) -> SmokeExecutionPlan:
        include_health = bool(arguments.get("include_health_check", True))
        include_api = bool(arguments.get("include_api", True))
        include_ui = bool(arguments.get("include_ui", True))
        max_cases = max(1, min(int(arguments.get("max_cases") or 8), 30))
        allow_write = bool(arguments.get("allow_write_operations", False))
        sources = [SmokeSource.model_validate(item) for item in source_bundle.get("sources", []) if isinstance(item, dict)]
        plan = SmokeExecutionPlan(
            title=f"{project_scope or '项目'} 冒烟测试方案",
            objective=objective,
            project_scope=project_scope,
            target_url=target_url,
            credential_summary=str(source_bundle.get("credential_summary") or ""),
            source_refs=sources,
        )
        if include_health and target_url:
            plan.cases.append(self._health_case(target_url, sources[:1]))
        for case in self._project_cases(source_bundle.get("project_cases", []), sources):
            plan.cases.append(case)
            if len(plan.cases) >= max_cases:
                break
        if include_api:
            for case in self._api_cases(target_url, source_bundle.get("api_documents", []), sources, allow_write):
                plan.cases.append(case)
                if len(plan.cases) >= max_cases:
                    break
        if include_ui and len(plan.cases) < max_cases:
            for case in self._ui_cases(target_url, source_bundle.get("ui_graph"), sources):
                plan.cases.append(case)
                if len(plan.cases) >= max_cases:
                    break
        if not plan.cases and target_url:
            plan.cases.append(self._health_case(target_url, sources[:1]))
        plan.risk_summary = self._risk_summary(plan.cases)
        plan.review_notes.extend(self._review_notes(plan))
        return plan

    def _project_cases(self, raw_cases: Any, sources: list[SmokeSource]) -> list[SmokeCase]:
        if not isinstance(raw_cases, list):
            return []
        source = next((item for item in sources if item.source_type == "project_case"), None)
        cases: list[SmokeCase] = []
        for raw in raw_cases[:12]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or raw.get("name") or raw.get("case_name") or "项目管理平台冒烟用例")
            case_type = str(raw.get("case_type") or raw.get("type") or "api").lower()
            if case_type not in {"api", "ui", "health"}:
                case_type = "api"
            risk_level = str(raw.get("risk_level") or "medium").lower()
            if risk_level not in {"low", "medium", "high"}:
                risk_level = "medium"
            assertion = SmokeAssertion(kind="manual_project_case", target=title, expected=True, description="项目管理平台用例被纳入冒烟方案")
            cases.append(
                SmokeCase(
                    title=title,
                    case_type=case_type,  # type: ignore[arg-type]
                    description=str(raw.get("description") or "来自项目管理平台的候选冒烟用例。"),
                    selected=bool(raw.get("selected", True)),
                    execution_eligible=bool(raw.get("execution_eligible", False)),
                    requires_approval=not bool(raw.get("execution_eligible", False)),
                    risk_level=risk_level,  # type: ignore[arg-type]
                    source_refs=[source] if source else [],
                    assertions=[assertion],
                    tags=["project-case"],
                )
            )
        return cases

    def _health_case(self, target_url: str, sources: list[SmokeSource]) -> SmokeCase:
        assertion = SmokeAssertion(kind="status_code", target=target_url, expected=200, operator="lt_500", description="目标地址返回非 5xx 状态码")
        return SmokeCase(
            title="目标服务健康检查",
            case_type="health",
            description="确认目标地址可访问，作为后续冒烟测试准入前置检查。",
            source_refs=sources,
            steps=[
                SmokeStep(
                    title="访问目标地址",
                    step_type="health",
                    api=SmokeApiStep(method="GET", url=target_url, expected_status=200),
                    assertions=[assertion],
                )
            ],
            assertions=[assertion],
            tags=["health", "entrypoint"],
        )

    def _api_cases(self, target_url: str, documents: list[dict[str, Any]], sources: list[SmokeSource], allow_write: bool) -> list[SmokeCase]:
        cases: list[SmokeCase] = []
        for doc in documents:
            record = doc.get("record") if isinstance(doc.get("record"), dict) else {}
            source = next((item for item in sources if item.source_id == str(record.get("id") or "")), None)
            for endpoint in _extract_endpoints(str(doc.get("content") or "")):
                method = endpoint["method"].upper()
                path = endpoint["path"]
                full_url = endpoint.get("full_url") or _join_url(str(record.get("project_url") or target_url), path)
                risk = "low" if method in {"GET", "HEAD", "OPTIONS"} else "medium" if method == "POST" else "high"
                requires_approval = method not in {"GET", "HEAD", "OPTIONS"} and not allow_write
                selected = risk == "low" or (method == "POST" and allow_write)
                expected_status = int(endpoint.get("expected_status") or _default_expected_status(method, endpoint))
                expected_fields = _expected_fields_for_endpoint(endpoint)
                assertions = [
                    SmokeAssertion(
                        kind="status_code",
                        target=f"{method} {full_url}",
                        expected=expected_status,
                        description="接口返回预期状态码",
                    )
                ]
                if expected_fields:
                    assertions.append(
                        SmokeAssertion(
                            kind="response_fields",
                            target=f"{method} {full_url}",
                            expected=expected_fields,
                            operator="contains",
                            description="响应 JSON 包含预期关键字段",
                        )
                    )
                query = _query_for_endpoint(endpoint)
                body = _request_body_for_endpoint(method, endpoint)
                cases.append(
                    SmokeCase(
                        title=f"{method} {path} 接口冒烟验证",
                        case_type="api",
                        description=str(endpoint.get("summary") or "验证关键接口基础可用性。"),
                        selected=selected,
                        execution_eligible=not requires_approval,
                        requires_approval=requires_approval,
                        risk_level=risk,
                        source_refs=[source] if source else [],
                        steps=[
                            SmokeStep(
                                title=f"调用 {method} {path}",
                                step_type="api",
                                api=SmokeApiStep(
                                    method=method,
                                    url=full_url,
                                    query=query,
                                    body=body,
                                    expected_status=expected_status,
                                    expected_fields=expected_fields,
                                ),
                                assertions=assertions,
                            )
                        ],
                        assertions=assertions,
                        tags=["api", method.lower(), *_endpoint_tags(endpoint)],
                    )
                )
        cases.sort(key=lambda item: (item.risk_level != "low", item.case_type, item.title))
        return cases

    def _ui_cases(self, target_url: str, graph: dict[str, Any] | None, sources: list[SmokeSource]) -> list[SmokeCase]:
        page_urls: list[tuple[str, str]] = []
        if isinstance(graph, dict):
            for node in graph.get("nodes") or []:
                if not isinstance(node, dict) or node.get("kind") != "page":
                    continue
                url = str(node.get("summary") or node.get("metadata", {}).get("url") or "")
                label = str(node.get("label") or "页面")
                if url:
                    page_urls.append((label, url))
        if target_url and not any(url == target_url for _, url in page_urls):
            page_urls.insert(0, ("目标首页", target_url))
        cases: list[SmokeCase] = []
        source = next((item for item in sources if item.source_type == "ui_graph"), None)
        for label, url in page_urls[:3]:
            assertion = SmokeAssertion(kind="page_reachable", target=url, expected=True, description="页面可访问且返回非 5xx")
            cases.append(
                SmokeCase(
                    title=f"{label} 页面可达性检查",
                    case_type="ui",
                    description="确认核心页面可访问，后续可升级为 Playwright 交互验证。",
                    source_refs=[source] if source else [],
                    steps=[
                        SmokeStep(
                            title=f"打开 {label}",
                            step_type="ui",
                            ui=SmokeUiStep(page_url=url, action="open"),
                            assertions=[assertion],
                        )
                    ],
                    assertions=[assertion],
                    tags=["ui", "reachability"],
                )
            )
        return cases

    async def _execute_case(self, case: SmokeCase, plan: SmokeExecutionPlan, *, credentials: dict[str, str] | None = None) -> SmokeCaseResult:
        started = time.perf_counter()
        if case.requires_approval:
            return SmokeCaseResult(
                case_id=case.case_id,
                title=case.title,
                case_type=case.case_type,
                status="blocked",
                summary="该用例包含写操作风险，需要用户明确允许后才能执行。",
                assertion_count=len(case.assertions),
                failure_category="needs_human",
            )
        if not case.steps:
            return SmokeCaseResult(case_id=case.case_id, title=case.title, case_type=case.case_type, status="blocked", summary="用例缺少执行步骤。", failure_category="test_data_missing")

        status = "passed"
        summary = "用例通过。"
        passed = 0
        failed = 0
        evidence: list[dict[str, Any]] = []
        category = ""
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for step in case.steps:
                try:
                    step_result = await self._execute_step(client, step, credentials=credentials or {})
                except Exception as exc:
                    status = "failed"
                    summary = f"执行步骤失败：{exc}"
                    failed += max(1, len(step.assertions))
                    category = "runner_error"
                    break
                evidence.append(step_result["evidence"])
                if step_result["passed"]:
                    passed += max(1, len(step.assertions))
                else:
                    status = "failed"
                    failed += max(1, len(step.assertions))
                    summary = str(step_result.get("summary") or "断言失败。")
                    category = str(step_result.get("failure_category") or "assertion_failed")
                    break
        duration_ms = int((time.perf_counter() - started) * 1000)
        assertion_count = max(len(case.assertions), passed + failed, 1)
        return SmokeCaseResult(
            case_id=case.case_id,
            title=case.title,
            case_type=case.case_type,
            status=status,
            summary=summary,
            assertion_count=assertion_count,
            passed_count=passed if passed else (assertion_count if status == "passed" else 0),
            failed_count=failed,
            duration_ms=duration_ms,
            failure_category=category,
            evidence=evidence,
        )

    async def _execute_step(self, client: httpx.AsyncClient, step: SmokeStep, *, credentials: dict[str, str] | None = None) -> dict[str, Any]:
        if step.api:
            method = step.api.method.upper()
            credentials = credentials or {}
            headers = _resolve_placeholders(step.api.headers, credentials)
            query = _resolve_placeholders(step.api.query, credentials)
            body = _resolve_placeholders(step.api.body, credentials)
            response = await client.request(method, step.api.url, headers=headers, params=query, json=body if body is not None else None)
            status_passed = response.status_code < 500 if step.step_type in {"health", "ui"} else response.status_code == step.api.expected_status
            missing_fields = _missing_response_fields(response, step.api.expected_fields)
            passed = status_passed and not missing_fields
            evidence = {
                "label": "http_response",
                "method": method,
                "url": step.api.url,
                "status_code": response.status_code,
                "detail": f"{method} {step.api.url} -> HTTP {response.status_code}",
                "response_preview": _safe_preview(response.text),
            }
            if query:
                evidence["query"] = _mask_sensitive(query)
            if step.api.body is not None:
                evidence["request_body_shape"] = _body_shape(step.api.body)
            if missing_fields:
                evidence["missing_fields"] = missing_fields
            summary = ""
            failure_category = "api_contract_failed"
            if not status_passed:
                summary = f"接口返回 HTTP {response.status_code}，不符合预期。"
                failure_category = "environment_unavailable" if response.status_code >= 500 else "api_contract_failed"
            elif missing_fields:
                summary = f"响应缺少预期字段：{', '.join(missing_fields)}。"
            return {
                "passed": passed,
                "summary": summary,
                "failure_category": failure_category,
                "evidence": evidence,
            }
        if step.ui:
            response = await client.get(step.ui.page_url)
            passed = response.status_code < 500
            return {
                "passed": passed,
                "summary": "" if passed else f"页面返回 HTTP {response.status_code}。",
                "failure_category": "environment_unavailable" if response.status_code >= 500 else "ui_locator_failed",
                "evidence": {
                    "label": "page_reachability",
                    "url": step.ui.page_url,
                    "status_code": response.status_code,
                    "detail": f"open {step.ui.page_url} -> HTTP {response.status_code}",
                },
            }
        return {"passed": False, "summary": "步骤缺少 API/UI 执行定义。", "failure_category": "test_data_missing", "evidence": {"label": "invalid_step", "detail": step.title}}

    async def _lookup_execution_credentials(self, context: Any, plan: SmokeExecutionPlan) -> dict[str, str]:
        if self._memory_runtime_service is None or not _plan_uses_credentials(plan):
            return {}
        query = f"测试账号 凭据 {plan.project_scope} {plan.target_url}".strip()
        if not query:
            return {}
        try:
            result = await self._memory_runtime_service.retrieve_for_turn(
                session_id=str(getattr(context, "session_id", "") or ""),
                trace_id=str(getattr(context, "trace_id", "") or ""),
                query=query,
                context={"mode_key": "smoke_testing"},
            )
        except Exception:
            return {}
        for hit in result.hits:
            credentials = _extract_credentials(str(getattr(hit, "content", "") or getattr(hit, "summary", "") or ""))
            if credentials:
                return credentials
        return {}

    async def _load_plan_from_args(self, arguments: dict[str, Any]) -> tuple[SmokeExecutionPlan | None, list[str]]:
        raw_plan = arguments.get("plan")
        if isinstance(raw_plan, dict):
            try:
                return SmokeExecutionPlan.model_validate(raw_plan), []
            except Exception as exc:
                return None, [f"输入 plan 解析失败：{exc}"]
        plan_id = str(arguments.get("plan_id") or arguments.get("approved_plan_id") or "").strip()
        if not plan_id:
            return None, ["缺少 plan_id。"]
        return await self._plan_store.load_plan(plan_id)

    def _apply_revision(self, plan: SmokeExecutionPlan, text: str, arguments: dict[str, Any]) -> None:
        selected_case_ids = self._string_list(arguments.get("selected_case_ids"))
        if selected_case_ids:
            selected = set(selected_case_ids)
            for case in plan.cases:
                case.selected = case.case_id in selected
        selected_indices = self._int_list(arguments.get("selected_indices"))
        if selected_indices:
            selected = set(selected_indices)
            for idx, case in enumerate(plan.cases, start=1):
                case.selected = idx in selected
        lowered = text.lower()
        if "只测api" in lowered or "只测 api" in lowered:
            for case in plan.cases:
                case.selected = case.case_type == "api"
        if "只测ui" in lowered or "只测 ui" in lowered:
            for case in plan.cases:
                case.selected = case.case_type == "ui"
        if "不测ui" in lowered or "不测 ui" in lowered:
            for case in plan.cases:
                if case.case_type == "ui":
                    case.selected = False
        if "不测api" in lowered or "不测 api" in lowered:
            for case in plan.cases:
                if case.case_type == "api":
                    case.selected = False
        if any(token in text for token in ["不要执行创建", "不要创建", "不执行创建", "不要测创建"]):
            for case in plan.cases:
                if "post" in " ".join(case.tags).lower() or "创建" in case.title:
                    case.selected = False
        if any(token in text for token in ["删除", "delete", "DELETE"]):
            for case in plan.cases:
                if "delete" in " ".join(case.tags).lower() or "删除" in case.title:
                    case.selected = False
        if "前三" in text or "前 3" in text:
            for idx, case in enumerate(plan.cases, start=1):
                case.selected = idx <= 3
        if "前两" in text or "前 2" in text:
            for idx, case in enumerate(plan.cases, start=1):
                case.selected = idx <= 2

    def _plan_output(self, plan: SmokeExecutionPlan, *, status: str, summary: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        output = {
            "status": status,
            "ok": status == "completed",
            "summary": summary,
            "plan_id": plan.plan_id,
            "plan_version": plan.version,
            "project_scope": plan.project_scope,
            "target_url": plan.target_url,
            "plan": plan.model_dump(mode="json"),
            "plan_markdown": self._plan_store.plan_markdown(plan),
            "selected_case_ids": [case.case_id for case in plan.cases if case.selected],
            "plan_uri": plan.rustfs_uris.get("plan_uri", ""),
            "plan_md_uri": plan.rustfs_uris.get("plan_md_uri", ""),
            "approved_plan_uri": plan.rustfs_uris.get("approved_plan_uri", ""),
        }
        if extra:
            output.update(extra)
        return output

    def _review_notes(self, plan: SmokeExecutionPlan) -> list[str]:
        notes: list[str] = []
        risky = [case for case in plan.cases if case.requires_approval]
        if risky:
            notes.append(f"{len(risky)} 条用例包含写操作或高风险操作，默认不执行，需要用户明确确认。")
        if not plan.credential_summary:
            notes.append("未从系统记忆中找到项目测试账号；需要登录的用例可能会被阻塞。")
        if not plan.source_refs:
            notes.append("未找到测试文档/API 文档/UI 图谱来源，方案仅基于目标地址生成。")
        return notes

    def _risk_summary(self, cases: list[SmokeCase]) -> dict[str, int]:
        return {
            "low": sum(1 for case in cases if case.risk_level == "low"),
            "medium": sum(1 for case in cases if case.risk_level == "medium"),
            "high": sum(1 for case in cases if case.risk_level == "high"),
            "requires_approval": sum(1 for case in cases if case.requires_approval),
        }

    def _target_url(self, arguments: dict[str, Any], context: Any) -> str:
        bundle = getattr(context, "context_bundle", None) or {}
        request = bundle.get("smoke_testing_request") if isinstance(bundle.get("smoke_testing_request"), dict) else {}
        return str(arguments.get("target_url") or request.get("target_url") or _extract_url(getattr(context, "user_message", "")) or "").strip()

    def _objective(self, arguments: dict[str, Any], context: Any) -> str:
        bundle = getattr(context, "context_bundle", None) or {}
        request = bundle.get("smoke_testing_request") if isinstance(bundle.get("smoke_testing_request"), dict) else {}
        return str(arguments.get("objective") or request.get("objective") or getattr(context, "user_message", "") or "").strip()

    def _attachments(self, context: Any) -> list[dict[str, Any]]:
        bundle = getattr(context, "context_bundle", None) or {}
        value = bundle.get("attachments")
        return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def _string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [item.strip() for item in re.split(r"[,，\s]+", value) if item.strip()]
        return []

    def _int_list(self, value: Any) -> list[int]:
        if isinstance(value, list):
            items = value
        elif isinstance(value, str):
            items = re.split(r"[,，\s]+", value)
        else:
            return []
        result: list[int] = []
        for item in items:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result


def _extract_endpoints(markdown: str) -> list[dict[str, Any]]:
    headings = list(re.finditer(r"^###\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(.+?)\s*$", markdown, flags=re.IGNORECASE | re.MULTILINE))
    endpoints: list[dict[str, Any]] = []
    for index, match in enumerate(headings):
        start = match.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        section = markdown[start:end]
        full_url_match = re.search(r"\*\*调用地址\*\*:\s*`?([^`\n]+)`?", section)
        summary_match = re.search(r"\*\*功能\*\*:\s*(.+)", section)
        success_payload = _json_block_after(section, "成功响应")
        fields = _top_level_fields(success_payload) or re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', section[:2000])
        endpoints.append(
            {
                "method": match.group(1).upper(),
                "path": match.group(2).strip().strip("`"),
                "full_url": full_url_match.group(1).strip() if full_url_match else "",
                "summary": summary_match.group(1).strip() if summary_match else "",
                "section": section,
                "request_body": _json_block_after(section, "请求体"),
                "success_payload": success_payload,
                "expected_status": _extract_expected_status(section),
                "expected_fields": list(dict.fromkeys(fields[:4])),
            }
        )
    return endpoints


def _join_url(base_url: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = base_url or ""
    if not base:
        return path
    parsed = urlparse(base)
    if parsed.scheme and parsed.netloc:
        return urljoin(f"{parsed.scheme}://{parsed.netloc}", path)
    return urljoin(base, path)


def _default_expected_status(method: str, endpoint: dict[str, Any]) -> int:
    if _is_auth_endpoint(endpoint):
        return 200
    if method.upper() == "POST" and _looks_like_create_endpoint(endpoint):
        return 201
    return 200


def _query_for_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    section = str(endpoint.get("section") or "")
    query: dict[str, Any] = {}
    for match in re.finditer(r"-\s*`([^`]+)`\s*\((query|查询参数)[^)]*(必填|required)[^)]*\)", section, flags=re.IGNORECASE):
        name = match.group(1).strip()
        if name:
            query[name] = _sample_value_for_name(name)
    return query


def _request_body_for_endpoint(method: str, endpoint: dict[str, Any]) -> Any:
    if method.upper() not in {"POST", "PUT", "PATCH"}:
        return None
    body = endpoint.get("request_body")
    if not isinstance(body, dict):
        body = {}
    if _is_auth_endpoint(endpoint):
        body = dict(body)
        username_key = next((key for key in body if key.lower() in {"username", "user", "account", "email", "mobile", "phone"}), "username")
        password_key = next((key for key in body if key.lower() in {"password", "passwd", "pwd"}), "password")
        body[username_key] = "{{credential.username}}"
        body[password_key] = "{{credential.password}}"
    elif not body:
        body = _generic_body_from_path(str(endpoint.get("path") or ""))
    return body


def _expected_fields_for_endpoint(endpoint: dict[str, Any]) -> list[str]:
    fields = [str(item) for item in endpoint.get("expected_fields") or [] if str(item).strip()]
    if _is_auth_endpoint(endpoint) and not any(field in {"token", "access_token", "refresh_token"} for field in fields):
        fields.append("access_token")
    return list(dict.fromkeys(fields[:6]))


def _endpoint_tags(endpoint: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    if _is_auth_endpoint(endpoint):
        tags.append("auth")
    if _looks_like_create_endpoint(endpoint):
        tags.append("create")
    return tags


def _json_block_after(section: str, label: str) -> Any:
    pattern = rf"\*\*{re.escape(label)}\*\*:[\s\S]*?```(?:json)?\s*([\s\S]*?)```"
    match = re.search(pattern, section, flags=re.IGNORECASE)
    if not match:
        return None
    text = match.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        return None


def _top_level_fields(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return [str(key) for key in payload.keys() if str(key).strip()][:6]
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return [str(key) for key in payload[0].keys() if str(key).strip()][:6]
    return []


def _extract_expected_status(section: str) -> int | None:
    for pattern in (
        r"(?:HTTP|状态码|status)\s*[:：]?\s*(20[0-9]|204)",
        r"\b(20[0-9]|204)\b",
    ):
        match = re.search(pattern, section[:2500], flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                return None
    return None


def _is_auth_endpoint(endpoint: dict[str, Any]) -> bool:
    text = _endpoint_text(endpoint)
    return any(token in text for token in ("login", "signin", "sign-in", "auth", "token", "oauth", "登录", "认证", "鉴权", "令牌"))


def _looks_like_create_endpoint(endpoint: dict[str, Any]) -> bool:
    text = _endpoint_text(endpoint)
    return any(token in text for token in ("create", "add", "insert", "register", "创建", "新增", "添加", "注册"))


def _endpoint_text(endpoint: dict[str, Any]) -> str:
    return "\n".join(str(endpoint.get(key) or "") for key in ("path", "summary", "section")).lower()


def _generic_body_from_path(path: str) -> dict[str, Any]:
    stem = next((part for part in reversed(path.strip("/").split("/")) if part and "{" not in part), "resource")
    field = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff]+", "_", stem).strip("_") or "name"
    return {"name": f"smoke-{field}"}


def _sample_value_for_name(name: str) -> Any:
    lowered = name.lower()
    if lowered in {"page", "page_no", "page_num", "current"}:
        return 1
    if lowered in {"size", "page_size", "limit", "per_page"}:
        return 10
    if lowered.endswith("_id") or lowered == "id" or "id" in lowered:
        return "{{test_data.id}}"
    if "date" in lowered or "time" in lowered:
        return "{{test_data.datetime}}"
    return f"smoke-{name}"


def _resolve_placeholders(value: Any, credentials: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_placeholders(item, credentials) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(item, credentials) for item in value]
    if isinstance(value, str):
        return (
            value.replace("{{credential.username}}", credentials.get("username", ""))
            .replace("{{credential.password}}", credentials.get("password", ""))
            .replace("{{credential.account}}", credentials.get("username", ""))
        )
    return value


def _missing_response_fields(response: httpx.Response, expected_fields: list[str]) -> list[str]:
    if not expected_fields:
        return []
    try:
        payload = response.json()
    except Exception:
        return expected_fields
    return [field for field in expected_fields if not _field_exists(payload, field)]


def _field_exists(payload: Any, field_path: str) -> bool:
    return _field_parts_exist(payload, [part for part in str(field_path).split(".") if part])


def _field_parts_exist(payload: Any, parts: list[str]) -> bool:
    if not parts:
        return True
    part = parts[0]
    current = payload
    if isinstance(current, dict) and part in current:
        return _field_parts_exist(current[part], parts[1:])
    if isinstance(current, list):
        return any(_field_parts_exist(item, parts) for item in current)
    return False


def _plan_uses_credentials(plan: SmokeExecutionPlan) -> bool:
    return "{{credential." in json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)


def _extract_credentials(content: str) -> dict[str, str]:
    username = ""
    password = ""
    for pattern in (
        r"(?:username|user|account|email|账号|用户名)[:：=]\s*([^\s,，;；]+)",
        r"(?:login|登录)[^\n\r]*(?:账号|用户|user)[:：=]?\s*([^\s,，;；]+)",
    ):
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            username = match.group(1).strip()
            break
    for pattern in (r"(?:password|passwd|pwd|密码)[:：=]\s*([^\s,，;；]+)",):
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match:
            password = match.group(1).strip()
            break
    return {"username": username, "password": password} if username and password else {}


def _mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("password", "passwd", "pwd", "token", "secret", "authorization")):
                masked[key] = "********"
            else:
                masked[key] = _mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [_mask_sensitive(item) for item in value]
    return value


def _body_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("********" if any(token in str(key).lower() for token in ("password", "passwd", "pwd", "token", "secret")) else type(item).__name__) for key, item in value.items()}
    if isinstance(value, list):
        return f"array[{len(value)}]"
    return type(value).__name__


def _extract_url(value: Any) -> str:
    match = re.search(r"https?://[^\s，。；;）)]+", str(value or ""))
    return match.group(0).strip() if match else ""


def _safe_preview(value: str, limit: int = 1200) -> str:
    text = str(value or "").strip()
    text = re.sub(
        r'("(?:password|passwd|pwd|token|access_token|refresh_token|secret|authorization)"\s*:\s*")[^"]+(")',
        r"\1********\2",
        text,
        flags=re.IGNORECASE,
    )
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
