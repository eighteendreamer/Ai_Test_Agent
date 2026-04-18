from __future__ import annotations

import asyncio
import json
import re
import smtplib
from dataclasses import asdict, dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from src.application.memory_runtime_service import MemoryRuntimeService
from src.application.mcp_runtime_service import MCPRuntimeService
from src.application.tool_job_service import ToolJobService
from src.core.config import Settings
from src.infrastructure.email_config_store import MySQLEmailConfigStore
from src.runtime.store import SessionStore
from src.schemas.agent import ToolDescriptor
from src.schemas.tool_runtime import ModelToolCall, ToolExecutionRecord


@dataclass
class ToolExecutionContext:
    session_id: str
    turn_id: str
    trace_id: str
    user_message: str
    normalized_input: str
    context_bundle: dict[str, Any]
    selected_agent_key: str = ""
    selected_model_key: str = ""
    tool_job_id: str = ""


class ToolRuntimeService:
    def __init__(
        self,
        request_timeout_seconds: int = 20,
        settings: Settings | None = None,
        mcp_runtime_service: MCPRuntimeService | None = None,
        memory_runtime_service: MemoryRuntimeService | None = None,
        tool_job_service: ToolJobService | None = None,
        session_store: SessionStore | None = None,
        coordinator_runtime_service=None,
    ) -> None:
        self._request_timeout_seconds = request_timeout_seconds
        self._settings = settings
        self._docs_dir = Path(__file__).resolve().parents[3] / "docs"
        self._workspace_root = Path.cwd()
        self._mcp_runtime_service = mcp_runtime_service
        self._memory_runtime_service = memory_runtime_service
        self._tool_job_service = tool_job_service
        self._session_store = session_store
        self._coordinator_runtime_service = coordinator_runtime_service
        self._email_config_store = MySQLEmailConfigStore(settings) if settings is not None else None
        self._handlers = {
            "workflow-router": self._run_workflow_router,
            "subagent-dispatch": self._run_subagent_dispatch,
            "knowledge-rag": self._run_knowledge_rag,
            "session-history": self._run_session_history,
            "test-case-generator": self._run_test_case_generator,
            "dom-inspector": self._run_dom_inspector,
            "browser-automation": self._run_browser_automation,
            "browser-control": self._run_browser_control,
            "api-tester": self._run_api_tester,
            "cli-executor": self._run_cli_executor,
            "file-artifact-manager": self._run_file_artifact_manager,
            "message-dispatch": self._run_message_dispatch,
            "report-writer": self._run_report_writer,
        }

    def set_coordinator_runtime_service(self, coordinator_runtime_service) -> None:
        self._coordinator_runtime_service = coordinator_runtime_service

    def set_memory_runtime_service(self, memory_runtime_service: MemoryRuntimeService) -> None:
        self._memory_runtime_service = memory_runtime_service

    def set_tool_job_service(self, tool_job_service: ToolJobService) -> None:
        self._tool_job_service = tool_job_service

    def set_session_store(self, session_store: SessionStore) -> None:
        self._session_store = session_store

    def has_handler(self, tool_key: str) -> bool:
        return tool_key in self._handlers

    async def execute(
        self,
        tool: ToolDescriptor,
        call: ModelToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionRecord:
        started_at = datetime.utcnow()
        job = None
        handler = self._handlers.get(tool.key)
        if handler is None:
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="failed",
                summary=f"No runtime handler is registered for tool '{tool.key}'.",
                trace_id=context.trace_id,
                input=call.arguments,
                output={},
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

        try:
            job_context = context
            if self._tool_job_service is not None:
                job = await self._tool_job_service.create_job(
                    tool=tool,
                    call_id=call.id,
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    trace_id=context.trace_id,
                    input_payload=call.arguments,
                    metadata={
                        "selected_agent_key": context.selected_agent_key,
                        "selected_model_key": context.selected_model_key,
                    },
                )
                await self._tool_job_service.mark_running(job)
                job_context = ToolExecutionContext(
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    trace_id=context.trace_id,
                    user_message=context.user_message,
                    normalized_input=context.normalized_input,
                    context_bundle=context.context_bundle,
                    selected_agent_key=context.selected_agent_key,
                    selected_model_key=context.selected_model_key,
                    tool_job_id=job.id,
                )

            raw_result = await handler(call.arguments, job_context)
            result = self._normalize_result(tool, raw_result, context=job_context)
            resolved_status = self._resolve_result_status(result)
            summary = str(result.get("summary", f"Tool '{tool.key}' completed."))
            if job is not None and self._tool_job_service is not None:
                if resolved_status == "failed":
                    await self._tool_job_service.mark_failed(
                        job.id,
                        summary=summary,
                        error_message=str(result.get("error") or summary),
                        output_payload=result,
                    )
                elif resolved_status == "partial":
                    await self._tool_job_service.mark_partial(
                        job.id,
                        summary=summary,
                        output_payload=result,
                        artifacts=result.get("artifacts", []) if isinstance(result, dict) else [],
                    )
                elif resolved_status == "waiting_approval":
                    await self._tool_job_service.mark_waiting_approval(
                        job.id,
                        summary=summary,
                        metadata={"output_payload": result},
                    )
                elif resolved_status == "denied":
                    await self._tool_job_service.mark_denied(
                        job.id,
                        summary=summary,
                        output_payload=result,
                    )
                else:
                    await self._tool_job_service.mark_completed(
                        job.id,
                        summary=summary,
                        output_payload=result,
                        artifacts=result.get("artifacts", []) if isinstance(result, dict) else [],
                    )
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status=resolved_status,
                summary=summary,
                trace_id=str(result.get("trace_id") or context.trace_id or ""),
                job_id=job.id if job is not None else None,
                input=call.arguments,
                output=result,
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )
        except Exception as exc:
            if job is not None and self._tool_job_service is not None:
                await self._tool_job_service.mark_failed(
                    job.id,
                    summary=f"Tool '{tool.key}' failed.",
                    error_message=str(exc),
                    output_payload={"error": str(exc)},
                )
            return ToolExecutionRecord(
                call_id=call.id,
                tool_key=tool.key,
                tool_name=tool.name,
                status="failed",
                summary=f"Tool '{tool.key}' failed: {exc}",
                trace_id=context.trace_id,
                job_id=job.id if job is not None else None,
                input=call.arguments,
                output={"error": str(exc)},
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

    def _resolve_result_status(self, result: dict[str, Any]) -> str:
        explicit_status = str(result.get("status") or "").strip().lower()
        if explicit_status in {"completed", "partial", "failed", "waiting_approval", "denied"}:
            return explicit_status
        if result.get("ok") is False:
            workers = result.get("workers")
            if isinstance(workers, list):
                running_count = sum(1 for item in workers if isinstance(item, dict) and item.get("status") == "running")
                failed_count = sum(1 for item in workers if isinstance(item, dict) and item.get("status") == "failed")
                if running_count > 0 and failed_count > 0:
                    return "partial"
            return "failed"
        return "completed"

    async def _run_knowledge_rag(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or context.normalized_input).strip()
        top_k = int(arguments.get("top_k") or 3)
        if not query:
            return {
                "summary": "No query was provided for knowledge retrieval.",
                "chunks": [],
            }

        memory_matches: list[dict[str, Any]] = []
        if self._memory_runtime_service is not None:
            memory_result = await self._memory_runtime_service.retrieve_for_turn(
                session_id=context.session_id,
                trace_id=context.trace_id,
                query=query,
                context=context.context_bundle,
            )
            memory_matches = [
                {
                    "source": item.source or "arangodb",
                    "score": item.score or 0.0,
                    "excerpt": item.summary or item.content,
                    "kind": item.kind,
                }
                for item in memory_result.hits
            ]

        tokens = [token.lower() for token in re.split(r"\W+", query) if token.strip()]
        matches: list[dict[str, Any]] = []

        if self._docs_dir.exists():
            for file_path in sorted(self._docs_dir.glob("*.md")):
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                lowered = text.lower()
                filename_score = sum(file_path.name.lower().count(token) for token in tokens)
                score = (sum(lowered.count(token) for token in tokens) + filename_score) or 0
                if score <= 0:
                    continue
                excerpt = _build_excerpt(text, tokens)
                matches.append(
                    {
                        "source": file_path.name,
                        "score": score,
                        "excerpt": excerpt,
                    }
                )

        matches.sort(key=lambda item: item["score"], reverse=True)
        selected = [*memory_matches, *matches][:top_k]
        return {
            "summary": f"Retrieved {len(selected)} knowledge chunks for query '{query}'.",
            "chunks": selected,
            "query": query,
        }

    async def _run_subagent_dispatch(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        coordinator_runtime = self._require_coordinator_runtime()
        return await coordinator_runtime.dispatch(
            payload=arguments,
            context=asdict(context),
        )

    async def _run_test_case_generator(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        feature = str(arguments.get("feature") or arguments.get("goal") or context.user_message).strip()
        requirements = _to_string_list(arguments.get("requirements"))
        acceptance_criteria = _to_string_list(arguments.get("acceptance_criteria"))
        platforms = _to_string_list(arguments.get("platforms")) or ["web"]
        risk_focus = _to_string_list(arguments.get("risk_focus"))

        if not feature:
            return {
                "status": "failed",
                "ok": False,
                "summary": "No feature or QA goal was provided for test case generation.",
                "cases": [],
                "metrics": {"case_count": 0},
                "error": "missing_feature",
            }

        scenario_inputs = acceptance_criteria or requirements or [feature]
        cases: list[dict[str, Any]] = []
        for index, item in enumerate(scenario_inputs, start=1):
            title = item if len(item) <= 72 else f"{item[:69]}..."
            case_id = f"TC-{index:03d}"
            steps = [
                {"step": 1, "action": f"Prepare the environment for {feature}.", "expected": "The target workflow is reachable."},
                {"step": 2, "action": f"Execute scenario: {item}.", "expected": "The workflow processes the action without validation errors."},
                {"step": 3, "action": "Verify outcome and evidence.", "expected": "Observed results match the expected behavior."},
            ]
            cases.append(
                {
                    "id": case_id,
                    "title": title,
                    "type": "functional",
                    "priority": "high" if index == 1 else "medium",
                    "platforms": platforms,
                    "preconditions": [
                        f"The {feature} workflow is available.",
                        "Required test data and environment access are prepared.",
                    ],
                    "steps": steps,
                    "assertions": [
                        item,
                        *[criterion for criterion in acceptance_criteria if criterion != item],
                    ],
                    "risk_focus": risk_focus,
                }
            )

        coverage = {
            "requirements": requirements,
            "acceptance_criteria": acceptance_criteria,
            "risk_focus": risk_focus,
            "platforms": platforms,
        }
        return {
            "summary": f"Generated {len(cases)} structured test cases for '{feature}'.",
            "cases": cases,
            "coverage": coverage,
            "artifacts": [
                {
                    "type": "generated_cases",
                    "label": "test_cases.json",
                    "content": json.dumps({"feature": feature, "cases": cases}, ensure_ascii=False, indent=2),
                }
            ],
            "metrics": {
                "case_count": len(cases),
                "requirement_count": len(requirements),
                "acceptance_criteria_count": len(acceptance_criteria),
            },
        }

    async def _run_session_history(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        store = self._require_session_store()
        action = str(arguments.get("action") or "history_summary").strip().lower()
        scope = str(arguments.get("scope") or "current_session").strip().lower()
        include_assistant = bool(arguments.get("include_assistant"))
        limit = int(arguments.get("limit") or 10)
        limit = max(1, min(limit, 50))

        current_session = await store.get_session(context.session_id)
        if current_session is None:
            return {
                "status": "failed",
                "ok": False,
                "summary": f"Session '{context.session_id}' was not found.",
                "error": "session_not_found",
            }

        if scope == "all_sessions":
            session_summaries = await store.list_sessions()
            sessions = []
            for item in session_summaries[:limit]:
                loaded = await store.get_session(item.id)
                if loaded is not None:
                    sessions.append(loaded)
        else:
            scope = "current_session"
            sessions = [current_session]

        if action == "count_sessions":
            status_counts: dict[str, int] = {}
            for item in sessions:
                status_key = item.status.value
                status_counts[status_key] = status_counts.get(status_key, 0) + 1
            return {
                "summary": (
                    f"Counted {len(sessions)} stored session(s) for scope '{scope}'."
                    if scope == "all_sessions"
                    else "Counted the current session."
                ),
                "scope": scope,
                "session_count": len(sessions),
                "status_counts": status_counts,
                "sessions": [self._session_overview(item) for item in sessions[:limit]],
                "metrics": {
                    "session_count": len(sessions),
                    "status_kind_count": len(status_counts),
                },
            }

        if action == "list_questions":
            questions = self._extract_questions(
                sessions=sessions,
                include_assistant=include_assistant,
                limit=limit,
            )
            return {
                "summary": f"Collected {len(questions)} historical question item(s) from scope '{scope}'.",
                "scope": scope,
                "questions": questions,
                "metrics": {
                    "question_count": len(questions),
                    "session_count": len(sessions),
                },
            }

        if action == "session_report":
            report = await self._build_session_report(
                session=current_session,
                store=store,
                include_assistant=include_assistant,
                limit=limit,
            )
            markdown = self._format_session_report_markdown(report)
            return {
                "summary": f"Generated a structured report for session '{current_session.id}'.",
                "scope": "current_session",
                "report": report,
                "artifacts": [
                    {
                        "type": "session_report_markdown",
                        "label": "session_report.md",
                        "content": markdown,
                    }
                ],
                "metrics": {
                    "message_count": report["message_count"],
                    "event_count": report["event_count"],
                    "snapshot_count": report["snapshot_count"],
                },
            }

        sessions_overview = [self._session_overview(item) for item in sessions[:limit]]
        recent_questions = self._extract_questions(
            sessions=sessions,
            include_assistant=False,
            limit=limit,
        )
        return {
            "summary": f"Built a history summary for scope '{scope}' across {len(sessions)} session(s).",
            "scope": scope,
            "sessions": sessions_overview,
            "questions": recent_questions,
            "metrics": {
                "session_count": len(sessions),
                "question_count": len(recent_questions),
            },
        }

    async def _run_workflow_router(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        query = str(arguments.get("query") or context.user_message).lower()
        route = "coordinator"
        rationale = "Defaulted to coordinator for orchestration."
        if any(token in query for token in ["browser", "page", "ui", "selenium", "playwright"]):
            route = "ui-executor"
            rationale = "Detected browser or UI execution intent."
        elif any(token in query for token in ["cli", "terminal", "shell", "powershell", "command"]):
            route = "ops-executor"
            rationale = "Detected terminal or command execution intent."
        elif any(token in query for token in ["api", "payload", "response", "request"]):
            route = "api-verifier"
            rationale = "Detected API verification intent."
        elif any(token in query for token in ["history", "session report", "conversation report", "previous questions", "历史", "会话报告", "对话报告", "之前问", "以前问"]):
            route = "report-analyst"
            rationale = "Detected session history or conversation reporting intent."
        elif any(token in query for token in ["email", "message", "notify", "notification", "send"]):
            route = "communication-agent"
            rationale = "Detected messaging or notification intent."
        elif any(token in query for token in ["report", "summary", "结论", "报告"]):
            route = "report-analyst"
            rationale = "Detected reporting intent."
        elif any(token in query for token in ["plan", "case", "scenario", "用例", "测试点"]):
            route = "qa-planner"
            rationale = "Detected planning or case design intent."

        return {
            "summary": f"Recommended execution route: {route}.",
            "route": route,
            "rationale": rationale,
        }

    async def _run_dom_inspector(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "inspect-page",
            arguments,
            asdict(context),
        )

    async def _run_browser_automation(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "browser-automation",
            arguments,
            asdict(context),
        )

    async def _run_browser_control(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "browser-mcp",
            "browser-control",
            arguments,
            asdict(context),
        )

    async def _run_api_tester(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        endpoint = str(arguments.get("endpoint") or arguments.get("url") or "").strip()
        method = str(arguments.get("method") or "GET").upper()
        request_body = arguments.get("request_body") if isinstance(arguments.get("request_body"), dict) else {}
        response_body = arguments.get("response_body") if isinstance(arguments.get("response_body"), dict) else {}
        response_status = arguments.get("response_status")
        expected_status = arguments.get("expected_status")
        expected_fields = _to_string_list(arguments.get("expected_fields"))
        assertions = _to_string_list(arguments.get("assertions"))

        checks: list[dict[str, Any]] = []
        passed = 0
        failed = 0

        if expected_status is not None:
            status_ok = response_status == expected_status
            checks.append(
                {
                    "name": "status_code",
                    "passed": status_ok,
                    "expected": expected_status,
                    "actual": response_status,
                    "reason": "Response status matched expectation." if status_ok else "Response status did not match expectation.",
                }
            )
            passed += 1 if status_ok else 0
            failed += 0 if status_ok else 1

        for field_path in expected_fields:
            value, exists = _lookup_path(response_body, field_path)
            checks.append(
                {
                    "name": f"field:{field_path}",
                    "passed": exists,
                    "expected": "present",
                    "actual": value if exists else None,
                    "reason": "Field is present in the response payload." if exists else "Field is missing from the response payload.",
                }
            )
            passed += 1 if exists else 0
            failed += 0 if exists else 1

        for assertion in assertions:
            checks.append(
                {
                    "name": f"assertion:{len(checks)+1}",
                    "passed": True,
                    "expected": assertion,
                    "actual": "captured",
                    "reason": "Assertion was captured for downstream verification.",
                }
            )
            passed += 1

        if not checks:
            return {
                "status": "partial",
                "ok": True,
                "summary": f"No executable API checks were supplied for {method} {endpoint or 'request'}; captured the request context only.",
                "checks": [],
                "request": {"endpoint": endpoint, "method": method, "body": request_body},
                "response": {"status": response_status, "body": response_body},
                "metrics": {"check_count": 0, "passed": 0, "failed": 0},
                "artifacts": [],
            }

        overall_ok = failed == 0
        status = "completed" if overall_ok else "partial"
        return {
            "status": status,
            "ok": overall_ok,
            "summary": (
                f"Validated {len(checks)} API checks for {method} {endpoint or 'request'}; "
                f"{passed} passed and {failed} failed."
            ),
            "checks": checks,
            "request": {"endpoint": endpoint, "method": method, "body": request_body},
            "response": {"status": response_status, "body": response_body},
            "metrics": {
                "check_count": len(checks),
                "passed": passed,
                "failed": failed,
            },
            "artifacts": [
                {
                    "type": "api-check-summary",
                    "label": "api_checks.json",
                    "content": json.dumps({"checks": checks, "endpoint": endpoint, "method": method}, ensure_ascii=False, indent=2),
                }
            ],
            "error": None if overall_ok else "api_assertions_failed",
        }

    async def _run_cli_executor(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        command = str(arguments.get("command") or "").strip()
        if not command:
            return {
                "status": "failed",
                "ok": False,
                "summary": "CLI Executor requires a non-empty command.",
                "error": "missing_command",
            }

        shell_name = str(arguments.get("shell") or "powershell").strip().lower()
        timeout_seconds = float(arguments.get("timeout_seconds") or 20)
        timeout_seconds = max(1.0, min(timeout_seconds, 300.0))
        cwd = self._resolve_cli_cwd(arguments.get("cwd"))
        artifact_dir = self._prepare_local_artifact_dir(context, "cli-executor")

        executable, shell_args = self._build_cli_invocation(shell_name, command)
        started_at = datetime.utcnow()
        process = await asyncio.create_subprocess_exec(
            executable,
            *shell_args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = process.returncode if process.returncode is not None else -1
        status = "completed" if exit_code == 0 and not timed_out else "partial" if timed_out else "failed"
        ok = exit_code == 0 and not timed_out

        transcript = {
            "command": command,
            "shell": shell_name,
            "cwd": str(cwd),
            "started_at": started_at.isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout": stdout_text,
            "stderr": stderr_text,
        }
        transcript_path = artifact_dir / "cli_transcript.json"
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")

        summary = (
            f"Command finished with exit code {exit_code} in {shell_name}."
            if not timed_out
            else f"Command timed out after {timeout_seconds:.0f}s in {shell_name}."
        )
        return {
            "status": status,
            "ok": ok,
            "summary": summary,
            "command": command,
            "shell": shell_name,
            "cwd": str(cwd),
            "exit_code": exit_code,
            "stdout": stdout_text[-8000:],
            "stderr": stderr_text[-8000:],
            "metrics": {
                "stdout_chars": len(stdout_text),
                "stderr_chars": len(stderr_text),
                "timed_out": timed_out,
            },
            "artifacts": [{"type": "cli_transcript", "path": str(transcript_path)}],
            "error": None if ok else ("timeout" if timed_out else stderr_text[-500:] or f"exit_code={exit_code}"),
        }

    async def _run_file_artifact_manager(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        runtime = self._require_mcp_runtime()
        return await runtime.call(
            "filesystem-mcp",
            "write-artifact",
            arguments,
            asdict(context),
        )

    async def _run_message_dispatch(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        channel = str(arguments.get("channel") or "artifact").strip().lower()
        subject = str(arguments.get("subject") or "Runtime Notification").strip()
        content = str(arguments.get("content") or "").strip()
        content_html = str(arguments.get("content_html") or "").strip()
        artifact_dir = self._prepare_local_artifact_dir(context, "message-dispatch")

        if not content and not content_html:
            return {
                "status": "failed",
                "ok": False,
                "summary": "Message Dispatch requires content or content_html.",
                "error": "missing_content",
            }

        recipients = [
            str(item).strip()
            for item in (arguments.get("to") if isinstance(arguments.get("to"), list) else [])
            if str(item).strip()
        ]

        delivery_record = {
            "channel": channel,
            "subject": subject,
            "recipients": recipients,
            "content": content,
            "content_html": content_html,
            "session_id": context.session_id,
            "turn_id": context.turn_id,
            "trace_id": context.trace_id,
            "created_at": datetime.utcnow().isoformat(),
        }
        artifact_name = _slug(str(arguments.get("file_name") or subject or "message")) or "message"
        artifact_path = artifact_dir / f"{artifact_name}.json"
        artifact_path.write_text(json.dumps(delivery_record, ensure_ascii=False, indent=2), encoding="utf-8")

        if channel == "artifact":
            return {
                "summary": f"Persisted local message artifact '{artifact_path.name}'.",
                "delivery": {"channel": "artifact", "artifact_path": str(artifact_path), "sent": False},
                "artifacts": [{"type": "message_artifact", "path": str(artifact_path)}],
            }

        if channel == "email":
            if not recipients:
                return {
                    "status": "failed",
                    "ok": False,
                    "summary": "Email delivery requires at least one recipient in 'to'.",
                    "artifacts": [{"type": "message_artifact", "path": str(artifact_path)}],
                    "error": "missing_recipients",
                }
            email_result = await asyncio.to_thread(
                self._send_email_message,
                recipients,
                subject,
                content,
                content_html,
            )
            return {
                "summary": f"Delivered email notification to {len(recipients)} recipient(s).",
                "delivery": {"channel": "email", **email_result},
                "artifacts": [{"type": "message_artifact", "path": str(artifact_path)}],
            }

        return {
            "status": "failed",
            "ok": False,
            "summary": f"Unsupported message channel '{channel}'.",
            "artifacts": [{"type": "message_artifact", "path": str(artifact_path)}],
            "error": "unsupported_channel",
        }

    async def _run_report_writer(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        title = str(arguments.get("title") or "QA Execution Report").strip()
        objective = str(arguments.get("objective") or context.user_message).strip()
        summary = str(arguments.get("summary") or "").strip()
        status = str(arguments.get("status") or "completed").strip()
        findings = arguments.get("findings") if isinstance(arguments.get("findings"), list) else []
        evidence = arguments.get("evidence") if isinstance(arguments.get("evidence"), list) else []
        recommendations = _to_string_list(arguments.get("recommendations"))

        report_sections = [
            {"title": "Overview", "content": summary or f"Report generated for: {objective or title}."},
            {"title": "Status", "content": status},
            {"title": "Findings", "content": _format_object_list(findings)},
            {"title": "Evidence", "content": _format_object_list(evidence)},
            {"title": "Recommendations", "content": "\n".join(f"- {item}" for item in recommendations) or "None provided."},
        ]
        markdown_lines = [f"# {title}", "", f"Status: {status}"]
        if objective:
            markdown_lines.extend(["", f"Objective: {objective}"])
        for section in report_sections:
            markdown_lines.extend(["", f"## {section['title']}", "", str(section["content"])])
        markdown = "\n".join(markdown_lines).strip()

        return {
            "summary": f"Generated a structured QA report with {len(report_sections)} sections.",
            "report_title": title,
            "report_sections": report_sections,
            "artifacts": [
                {
                    "type": "report_markdown",
                    "label": "qa_report.md",
                    "content": markdown,
                }
            ],
            "metrics": {
                "section_count": len(report_sections),
                "finding_count": len(findings),
                "evidence_count": len(evidence),
                "recommendation_count": len(recommendations),
            },
        }

    def _require_mcp_runtime(self) -> MCPRuntimeService:
        if self._mcp_runtime_service is None:
            raise RuntimeError("MCP runtime service is not configured.")
        return self._mcp_runtime_service

    def _require_session_store(self) -> SessionStore:
        if self._session_store is None:
            raise RuntimeError("Session store is not configured.")
        return self._session_store

    def _require_coordinator_runtime(self):
        if self._coordinator_runtime_service is None:
            raise RuntimeError("Coordinator runtime service is not configured.")
        return self._coordinator_runtime_service

    def _resolve_cli_cwd(self, requested_cwd: Any) -> Path:
        if isinstance(requested_cwd, str) and requested_cwd.strip():
            candidate = Path(requested_cwd.strip())
            resolved = candidate.resolve() if candidate.is_absolute() else (self._workspace_root / candidate).resolve()
        else:
            resolved = self._workspace_root.resolve()
        workspace_root = self._workspace_root.resolve()
        if workspace_root != resolved and workspace_root not in resolved.parents:
            raise RuntimeError(f"CLI Executor cwd must stay within the workspace root: {workspace_root}")
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved

    def _build_cli_invocation(self, shell_name: str, command: str) -> tuple[str, list[str]]:
        if shell_name in {"powershell", "pwsh"}:
            executable = "powershell" if shell_name == "powershell" else "pwsh"
            return executable, ["-Command", command]
        if shell_name in {"cmd", "command"}:
            return "cmd", ["/c", command]
        if shell_name in {"bash", "sh"}:
            executable = "bash" if shell_name == "bash" else "sh"
            return executable, ["-lc", command]
        return "powershell", ["-Command", command]

    def _prepare_local_artifact_dir(self, context: ToolExecutionContext, tool_key: str) -> Path:
        settings = self._settings or Settings()
        artifact_root = Path(__file__).resolve().parents[2] / settings.artifact_root_dir
        artifact_root.mkdir(parents=True, exist_ok=True)
        session_id = _slug(context.session_id or "session")
        turn_id = _slug(context.turn_id or datetime.utcnow().strftime("%Y%m%d%H%M%S"))
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        artifact_dir = artifact_root / session_id / turn_id / f"{tool_key}_{timestamp}"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    def _send_email_message(
        self,
        recipients: list[str],
        subject: str,
        content: str,
        content_html: str,
    ) -> dict[str, Any]:
        if self._email_config_store is None:
            raise RuntimeError("Email config store is not available.")
        records = self._email_config_store.list_all()
        enabled = [item for item in records if item.enabled]
        if not enabled:
            raise RuntimeError("No enabled email configuration is available.")
        record = next((item for item in enabled if item.is_default), enabled[0])
        if not record.smtp_host or not record.smtp_port:
            raise RuntimeError("Selected email configuration is missing SMTP host or port.")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = record.from_email or record.smtp_username or ""
        message["To"] = ", ".join(recipients)
        if record.reply_to:
            message["Reply-To"] = record.reply_to
        if content:
            message.set_content(content)
        else:
            message.set_content(" ")
        if content_html:
            message.add_alternative(content_html, subtype="html")

        if record.use_tls:
            with smtplib.SMTP_SSL(record.smtp_host, int(record.smtp_port)) as client:
                if record.smtp_username:
                    client.login(record.smtp_username, record.smtp_password or "")
                client.send_message(message)
        else:
            with smtplib.SMTP(record.smtp_host, int(record.smtp_port)) as client:
                client.ehlo()
                try:
                    client.starttls()
                    client.ehlo()
                except smtplib.SMTPException:
                    pass
                if record.smtp_username:
                    client.login(record.smtp_username, record.smtp_password or "")
                client.send_message(message)

        return {
            "sent": True,
            "provider": record.provider,
            "from_email": record.from_email or record.smtp_username or "",
            "recipient_count": len(recipients),
        }

    def _normalize_result(
        self,
        tool: ToolDescriptor,
        result: dict[str, Any],
        context: ToolExecutionContext,
    ) -> dict[str, Any]:
        normalized = dict(result)
        explicit_status = str(normalized.get("status") or "").strip().lower()
        if "artifacts" not in normalized or not isinstance(normalized.get("artifacts"), list):
            normalized["artifacts"] = []
        if "metrics" not in normalized or not isinstance(normalized.get("metrics"), dict):
            normalized["metrics"] = {}
        if "summary" not in normalized or not str(normalized.get("summary") or "").strip():
            normalized["summary"] = f"Tool '{tool.key}' completed."
        if explicit_status in {"failed", "denied"} and "ok" not in normalized:
            normalized["ok"] = False
        elif "ok" not in normalized:
            normalized["ok"] = True
        if not normalized["ok"] and "error" not in normalized:
            normalized["error"] = normalized.get("summary")
        normalized.setdefault("trace_id", context.trace_id)
        return normalized

    def _session_overview(self, session) -> dict[str, Any]:
        return {
            "session_id": session.id,
            "title": session.title,
            "status": session.status.value,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "message_count": len(session.messages),
            "event_count": session.event_count,
            "snapshot_count": session.snapshot_count,
            "selected_agent": session.selected_agent,
            "preferred_model": session.preferred_model,
        }

    def _extract_questions(
        self,
        sessions: list[Any],
        include_assistant: bool,
        limit: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for session in sorted(sessions, key=lambda item: item.updated_at, reverse=True):
            for message in session.messages:
                role = getattr(message.role, "value", str(message.role))
                if role != "user" and not include_assistant:
                    continue
                if not str(message.content or "").strip():
                    continue
                if role == "assistant" and not include_assistant:
                    continue
                items.append(
                    {
                        "session_id": session.id,
                        "role": role,
                        "content": str(message.content).strip(),
                        "created_at": message.created_at.isoformat(),
                    }
                )
        items.sort(key=lambda item: item["created_at"])
        return items[:limit]

    async def _build_session_report(
        self,
        session,
        store: SessionStore,
        include_assistant: bool,
        limit: int,
    ) -> dict[str, Any]:
        events = await store.list_events(session.id)
        snapshots = await store.list_snapshots(session.id)
        approvals = await store.list_approvals(session.id)
        user_messages = [item for item in session.messages if getattr(item.role, "value", str(item.role)) == "user"]
        assistant_messages = [item for item in session.messages if getattr(item.role, "value", str(item.role)) == "assistant"]
        tool_messages = [item for item in session.messages if getattr(item.role, "value", str(item.role)) == "tool"]

        transcript_excerpt = []
        for item in session.messages[-limit:]:
            role = getattr(item.role, "value", str(item.role))
            if role == "assistant" and not include_assistant:
                continue
            transcript_excerpt.append(
                {
                    "role": role,
                    "content": str(item.content or "").strip(),
                    "created_at": item.created_at.isoformat(),
                }
            )

        recent_event_types = [event.type for event in events[-limit:]]
        snapshot_stages = [snapshot.stage for snapshot in snapshots[-limit:]]
        return {
            "session_id": session.id,
            "title": session.title,
            "status": session.status.value,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "message_count": len(session.messages),
            "event_count": len(events),
            "snapshot_count": len(snapshots),
            "approval_count": len(approvals),
            "user_message_count": len(user_messages),
            "assistant_message_count": len(assistant_messages),
            "tool_message_count": len(tool_messages),
            "recent_user_questions": [
                {
                    "content": str(item.content or "").strip(),
                    "created_at": item.created_at.isoformat(),
                }
                for item in user_messages[-limit:]
            ],
            "recent_event_types": recent_event_types,
            "snapshot_stages": snapshot_stages,
            "transcript_excerpt": transcript_excerpt,
        }

    def _format_session_report_markdown(self, report: dict[str, Any]) -> str:
        lines = [
            f"# Session Report: {report['title']}",
            "",
            f"- Session ID: {report['session_id']}",
            f"- Status: {report['status']}",
            f"- Created At: {report['created_at']}",
            f"- Updated At: {report['updated_at']}",
            f"- Messages: {report['message_count']}",
            f"- Events: {report['event_count']}",
            f"- Snapshots: {report['snapshot_count']}",
            f"- Approvals: {report['approval_count']}",
            "",
            "## Recent User Questions",
        ]
        questions = report.get("recent_user_questions") or []
        if questions:
            for item in questions:
                lines.append(f"- {item['created_at']}: {item['content']}")
        else:
            lines.append("- None")
        lines.extend(["", "## Recent Event Types"])
        event_types = report.get("recent_event_types") or []
        if event_types:
            for item in event_types:
                lines.append(f"- {item}")
        else:
            lines.append("- None")
        lines.extend(["", "## Snapshot Stages"])
        snapshot_stages = report.get("snapshot_stages") or []
        if snapshot_stages:
            for item in snapshot_stages:
                lines.append(f"- {item}")
        else:
            lines.append("- None")
        return "\n".join(lines).strip()


def _build_excerpt(text: str, tokens: list[str], radius: int = 140) -> str:
    lowered = text.lower()
    pivot = min(
        (lowered.find(token) for token in tokens if lowered.find(token) >= 0),
        default=0,
    )
    start = max(0, pivot - radius)
    end = min(len(text), pivot + radius)
    return " ".join(text[start:end].split())


def _to_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _lookup_path(payload: dict[str, Any], dotted_path: str) -> tuple[Any, bool]:
    current: Any = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            return None, False
        current = current[segment]
    return current, True


def _format_object_list(items: list[Any]) -> str:
    if not items:
        return "None provided."
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("name") or item.get("id") or "Item")
            body = str(item.get("summary") or item.get("detail") or item.get("content") or item)
            lines.append(f"- {title}: {body}")
            continue
        lines.append(f"- {item}")
    return "\n".join(lines)
