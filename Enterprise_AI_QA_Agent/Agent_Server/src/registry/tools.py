from __future__ import annotations

from dataclasses import dataclass

from src.schemas.agent import ToolDescriptor


@dataclass(frozen=True)
class ToolModule:
    descriptor: ToolDescriptor
    handler_key: str | None = None


SECURITY_RUNNER_OUTPUT_SCHEMA = {
    "status": "string",
    "ok": "boolean",
    "success": "boolean",
    "summary": "string",
    "runner_key": "string",
    "command_profile": "string",
    "tool_name": "string",
    "command": "string",
    "execution_backend": "string",
    "container_name": "string",
    "exit_code": "integer",
    "timed_out": "boolean",
    "metrics": "object",
    "findings": "array",
    "parsed_result": "object",
    "raw_output": "string",
    "artifacts": "array",
    "error": "string",
}

SECURITY_TOOL_BOOTSTRAP_OUTPUT_SCHEMA = {
    "status": "string",
    "ok": "boolean",
    "summary": "string",
    "error": "string",
    "tool_bootstrap": "object",
    "artifacts": "array",
}


SECURITY_SCAN_RUNNER_OUTPUT_SCHEMA = {
    **SECURITY_RUNNER_OUTPUT_SCHEMA,
    "phase": "string",
    "trace_id": "string",
    "selected_agent": "string",
    "selected_tools": "array",
    "context_refs": "array",
    "targets": "array",
    "campaign_id": "string",
    "task_count": "integer",
    "task_summary": "object",
    "report": "object",
    "report_markdown": "string",
    "report_html": "string",
    "delivery": "object",
    "verification_result": "object",
    "evaluation_result": "object",
    "errors": "array",
    "execution_checkpoint": "object",
    "task_events": "array",
    "security_testing_state": "object",
}


PERFORMANCE_RUNNER_OUTPUT_SCHEMA = {
    "status": "string",
    "ok": "boolean",
    "phase": "string",
    "summary": "string",
    "run_id": "string",
    "run_intent": "string",
    "verdict": "string",
    "metrics": "object",
    "sla_result": "object",
    "error_breakdown": "object",
    "engine_threshold_crosscheck": "object",
    "baseline_comparison": "object",
    "load_side_observations": "array",
    "report_markdown": "string",
    "report_html": "string",
    "smoke_result": "object",
    "guard_result": "object",
    "plan": "object",
    "errors": "array",
    "performance_testing_state": "object",
}

COMPATIBILITY_RUNNER_OUTPUT_SCHEMA = {
    "status": "string",
    "ok": "boolean",
    "phase": "string",
    "summary": "string",
    "action": "string",
    "compatibility_run": "object",
    "plan": "object",
    "dispatch_plan": "object",
    "runner_queue": "object",
    "runner_summary": "object",
    "recoverable_tasks": "array",
    "risks": "array",
    "report_markdown": "string",
    "next_steps": "array",
    "missing_components": "array",
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolModule] = {
            "skill": ToolModule(
                descriptor=ToolDescriptor(
                    key="skill",
                    name="Skill Loader",
                    description=(
                        "Search or load a registered Skill on demand. Loading a Skill returns its instructions "
                        "and makes only its associated tools available on the next model turn. Use this before "
                        "attempting a capability whose concrete tools are not currently visible."
                    ),
                    category="system",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["search", "load", "read_reference"],
                                "description": "Search the catalog or load matching Skills and their tools.",
                                "default": "load",
                            },
                            "skill_keys": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Exact Skill keys to load when already known.",
                            },
                            "query": {
                                "type": "string",
                                "description": "Capability description used to find matching Skills and deferred tools.",
                            },
                            "reference_path": {
                                "type": "string",
                                "description": "Relative references/... path to read after its Skill has been loaded.",
                            },
                        },
                    },
                    output_schema={
                        "matched_skills": "array",
                        "loaded_skills": "array",
                        "loaded_tools": "array",
                        "instructions": "array",
                        "reference_content": "string",
                    },
                    tags=["core", "skills", "deferred-tools"],
                ),
                handler_key="graph-skill-loader",
            ),
            "workflow-router": ToolModule(
                descriptor=ToolDescriptor(
                    key="workflow-router",
                    name="Workflow Router",
                    description="Route a request to the right agent role and graph path.",
                    category="system",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The user task to classify and route."},
                        },
                    },
                    output_schema={"route": "string"},
                    tags=["core", "orchestration"],
                ),
                handler_key="workflow-router",
            ),
            "subagent-dispatch": ToolModule(
                descriptor=ToolDescriptor(
                    key="subagent-dispatch",
                    name="Subagent Dispatch",
                    description="Launch one or more background worker sessions and send task-notification results back to the coordinator.",
                    category="orchestration",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "workers": {
                                "type": "array",
                                "description": "One or more worker dispatch specifications.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "description": {"type": "string"},
                                        "prompt": {"type": "string"},
                                        "agent_key": {"type": "string"},
                                        "model_key": {"type": "string"},
                                        "skill_keys": {"type": "array", "items": {"type": "string"}},
                                        "context": {"type": "object"},
                                    },
                                    "required": ["description", "prompt", "agent_key"],
                                },
                            },
                            "description": {"type": "string", "description": "Dispatch description for a single worker."},
                            "prompt": {"type": "string", "description": "Task prompt for a single worker."},
                            "agent_key": {"type": "string", "description": "Worker agent key."},
                            "model_key": {"type": "string", "description": "Optional worker model key override."},
                            "skill_keys": {"type": "array", "items": {"type": "string"}},
                            "context": {"type": "object"},
                            "completion_worker": {
                                "type": "object",
                                "description": "Optional worker dispatched only after the primary workers for this turn have all settled.",
                                "properties": {
                                    "description": {"type": "string"},
                                    "prompt": {"type": "string"},
                                    "agent_key": {"type": "string"},
                                    "model_key": {"type": "string"},
                                    "skill_keys": {"type": "array", "items": {"type": "string"}},
                                    "context": {"type": "object"},
                                },
                            },
                        },
                    },
                    output_schema={"workers": "array"},
                    tags=["coordinator", "worker", "async"],
                ),
                handler_key="subagent-dispatch",
            ),
            "knowledge-rag": ToolModule(
                descriptor=ToolDescriptor(
                    key="knowledge-rag",
                    name="Knowledge RAG",
                    description="Retrieve test rules, page knowledge, and historical defects.",
                    category="knowledge",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The knowledge lookup query."},
                            "top_k": {"type": "integer", "description": "Maximum number of chunks to return.", "default": 3},
                        },
                        "required": ["query"],
                    },
                    supports_streaming=True,
                    output_schema={"chunks": "array"},
                    tags=["retrieval", "knowledge"],
                ),
                handler_key="knowledge-rag",
            ),
            "web_search": ToolModule(
                descriptor=ToolDescriptor(
                    key="web_search",
                    name="Web Search",
                    description=(
                        "Search the public web for current information and return structured results with "
                        "titles, URLs, snippets, and source metadata."
                    ),
                    category="retrieval",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The web search query."},
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return.",
                                "default": 5,
                            },
                            "time_range": {
                                "type": "string",
                                "description": "Optional freshness hint such as day, week, month, year, or all.",
                            },
                        },
                        "required": ["query"],
                    },
                    output_schema={
                        "query": "string",
                        "results": "array",
                        "answer": "string",
                        "provider": "string",
                    },
                    tags=["core", "web", "search", "current-information"],
                    capability_keys=["web.search"],
                ),
                handler_key="web_search",
            ),
            "api-docs-library": ToolModule(
                descriptor=ToolDescriptor(
                    key="api-docs-library",
                    name="API Docs Library",
                    description=(
                        "List imported API documentation/test files, inspect one file's details, and precisely search "
                        "endpoint paths, methods, titles, projects, and generated Markdown content. Use this before "
                        "Knowledge RAG when the user asks questions like '我有哪些接口文档', '查看接口文档详情', or '查找登录接口'."
                    ),
                    category="knowledge",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "description": "One of list, detail, or search.",
                                "enum": ["list", "detail", "search"],
                            },
                            "doc_id": {
                                "type": "string",
                                "description": "Document id returned by action=list, required for action=detail unless query can identify one.",
                            },
                            "query": {
                                "type": "string",
                                "description": "Search text, document title, endpoint keyword, or combined text such as 'POST /login'.",
                            },
                            "project_name": {
                                "type": "string",
                                "description": "Optional project name filter.",
                            },
                            "project_url": {
                                "type": "string",
                                "description": "Optional project/base URL filter.",
                            },
                            "method": {
                                "type": "string",
                                "description": "Optional HTTP method filter, such as GET or POST.",
                            },
                            "path": {
                                "type": "string",
                                "description": "Optional endpoint path or path fragment filter, such as /api/users.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of documents or matches to return.",
                                "default": 10,
                            },
                            "include_preview": {
                                "type": "boolean",
                                "description": "Whether to include Markdown preview snippets in list/search results.",
                                "default": False,
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "Maximum preview or excerpt characters to return.",
                                "default": 4000,
                            },
                        },
                        "required": ["action"],
                    },
                    output_schema={"documents": "array", "document": "object", "matches": "array"},
                    tags=["api", "docs", "testing", "retrieval"],
                ),
                handler_key="api-docs-library",
            ),
            "attachment-reader": ToolModule(
                descriptor=ToolDescriptor(
                    key="attachment-reader",
                    name="Attachment Reader",
                    description="Read the current turn's uploaded attachment from object storage and return its text content, preview, and metadata.",
                    category="knowledge",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "attachment_id": {
                                "type": "string",
                                "description": "Optional attachment identifier from attachment metadata.",
                            },
                            "uri": {
                                "type": "string",
                                "description": "Optional object storage URI such as rustfs://bucket/object.",
                            },
                            "name": {
                                "type": "string",
                                "description": "Optional attachment filename to match against the current turn.",
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "Maximum number of decoded characters to return.",
                                "default": 12000,
                            },
                            "prefer_excerpt": {
                                "type": "boolean",
                                "description": "When true, return the stored excerpt without fetching full object bytes unless needed.",
                                "default": False,
                            },
                        },
                    },
                    output_schema={"attachment": "object", "content": "string"},
                    tags=["attachment", "file", "rustfs", "retrieval"],
                ),
                handler_key="attachment-reader",
            ),
            "session-history": ToolModule(
                descriptor=ToolDescriptor(
                    key="session-history",
                    name="Session History",
                    description="Inspect stored session history, count historical sessions, list prior user questions, and generate a structured session report without relying on model memory reconstruction.",
                    category="knowledge",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "description": "One of count_sessions, list_questions, session_report, or history_summary.",
                            },
                            "scope": {
                                "type": "string",
                                "description": "Use current_session or all_sessions.",
                                "default": "current_session",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of messages, questions, or sessions to include.",
                                "default": 10,
                            },
                            "include_assistant": {
                                "type": "boolean",
                                "description": "Whether assistant messages should be included in the returned transcript excerpts.",
                                "default": False,
                            },
                        },
                        "required": ["action"],
                    },
                    output_schema={"report": "object", "questions": "array", "sessions": "array"},
                    tags=["history", "session", "reporting"],
                ),
                handler_key="session-history",
            ),
            "session-timeline": ToolModule(
                descriptor=ToolDescriptor(
                    key="session-timeline",
                    name="Session Timeline",
                    description="Build a chronological timeline of session messages, events, snapshots, and observations for the current session.",
                    category="knowledge",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of timeline entries to include.",
                                "default": 20,
                            },
                            "include_messages": {
                                "type": "boolean",
                                "description": "Whether user and assistant messages should be included.",
                                "default": True,
                            },
                            "include_events": {
                                "type": "boolean",
                                "description": "Whether execution events should be included.",
                                "default": True,
                            },
                            "include_observations": {
                                "type": "boolean",
                                "description": "Whether persisted testing observations should be included.",
                                "default": True,
                            },
                        },
                    },
                    output_schema={"timeline": "array", "report": "object"},
                    tags=["history", "timeline", "session"],
                ),
                handler_key="session-timeline",
            ),
            "observation-search": ToolModule(
                descriptor=ToolDescriptor(
                    key="observation-search",
                    name="Observation Search",
                    description="Search persisted testing observations and return structured evidence about prior page states, API assertions, CLI runs, and report artifacts.",
                    category="knowledge",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query for historical testing observations."},
                            "scope": {
                                "type": "string",
                                "description": "Use current_session or all_sessions.",
                                "default": "current_session",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of observations to return.",
                                "default": 8,
                            },
                            "category": {
                                "type": "string",
                                "description": "Optional observation category filter, such as page_state or api_assertion.",
                            },
                            "tool_key": {
                                "type": "string",
                                "description": "Optional tool key filter, such as browser-automation or api-tester.",
                            },
                        },
                        "required": ["query"],
                    },
                    output_schema={"observations": "array"},
                    tags=["history", "observation", "retrieval"],
                ),
                handler_key="observation-search",
            ),
            "test-case-generator": ToolModule(
                descriptor=ToolDescriptor(
                    key="test-case-generator",
                    name="Test Case Generator",
                    description="Generate structured test scenarios, assertions, and coverage suggestions.",
                    category="qa",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "feature": {"type": "string", "description": "Feature or workflow under test."},
                            "goal": {"type": "string", "description": "Primary QA objective."},
                            "requirements": {"type": "array", "items": {"type": "string"}},
                            "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                            "platforms": {"type": "array", "items": {"type": "string"}},
                            "risk_focus": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    output_schema={"cases": "array"},
                    tags=["planning", "qa"],
                ),
                handler_key="test-case-generator",
            ),
            "test-case-xlsx-exporter": ToolModule(
                descriptor=ToolDescriptor(
                    key="test-case-xlsx-exporter",
                    name="Test Case XLSX Exporter",
                    description="Export structured test cases to an xlsx file for download after test-case generation.",
                    category="qa",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "cases": {"type": "array", "description": "Test case objects to export."},
                            "feature": {"type": "string", "description": "Feature name used in the filename."},
                        },
                        "required": ["cases"],
                    },
                    output_schema={"ok": "boolean", "summary": "string", "artifact_path": "string", "case_count": "integer", "artifacts": "array", "error": "string"},
                    tags=["planning", "qa", "export", "xlsx"],
                ),
                handler_key="test-case-xlsx-exporter",
            ),
            "browser-automation": ToolModule(
                descriptor=ToolDescriptor(
                    key="browser-automation",
                    name="Browser Automation",
                    description="Drive browser execution for UI automation and replay.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "target_url": {"type": "string", "description": "The web page URL to automate."},
                            "objective": {"type": "string", "description": "What the browser automation should validate."},
                            "actions": {
                                "type": "array",
                                "description": "Optional explicit Playwright CLI action list for the browser executor.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string"},
                                        "selector": {"type": "string"},
                                        "value": {"type": "string"},
                                        "seconds": {"type": "number"},
                                        "label": {"type": "string"},
                                        "y": {"type": "number"},
                                    },
                                    "required": ["type"],
                                },
                            },
                        },
                        "required": ["target_url"],
                    },
                    supports_streaming=True,
                    output_schema={"steps": "array", "artifacts": "array"},
                    tags=["ui", "automation"],
                ),
                handler_key="browser-automation",
            ),
            "browser-control": ToolModule(
                descriptor=ToolDescriptor(
                    key="browser-control",
                    name="Browser Control",
                    description="Control browser actions explicitly for navigation, screenshot, DOM inspection, JavaScript evaluation, and scripted UI actions.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "description": "One of navigate, inspect, screenshot, evaluate_js, run_actions, or command.",
                            },
                            "target_url": {"type": "string", "description": "The target page URL."},
                            "command": {"type": "string", "description": "A playwright-cli style command, used when action=command."},
                            "args": {
                                "type": "array",
                                "description": "A playwright-cli style argument list, used when action=command.",
                                "items": {"type": "string"},
                            },
                            "javascript": {"type": "string", "description": "JavaScript expression for evaluate_js."},
                            "label": {"type": "string", "description": "Optional screenshot or artifact label."},
                            "actions": {
                                "type": "array",
                                "description": "UI actions used when action=run_actions.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string"},
                                        "selector": {"type": "string"},
                                        "value": {"type": "string"},
                                        "seconds": {"type": "number"},
                                        "label": {"type": "string"},
                                        "y": {"type": "number"},
                                    },
                                    "required": ["type"],
                                },
                            },
                        },
                        "required": ["action"],
                    },
                    supports_streaming=True,
                    output_schema={"artifacts": "array", "steps": "array"},
                    tags=["ui", "browser", "automation"],
                ),
                handler_key="browser-control",
            ),
            "ui-page-explorer": ToolModule(
                descriptor=ToolDescriptor(
                    key="ui-page-explorer",
                    name="UI Explorer Agent",
                    description="Explore UI structure with Playwright ARIA snapshots, build semantic context trees, and persist a project-scoped UI graph without creating tests or assertions.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "target_url": {"type": "string", "description": "The UI entry page URL to explore."},
                            "max_pages": {"type": "integer", "description": "Maximum pages to inspect for the app map."},
                            "same_origin_only": {"type": "boolean", "description": "Only follow links on the same origin."},
                            "include_hidden": {"type": "boolean", "description": "Include ARIA nodes that Playwright reports as not visible."},
                            "max_interactions": {"type": "integer", "description": "Maximum non-navigation interactions to open per page, such as dialogs, drawers, tabs, and expandable regions."},
                            "login_credentials": {
                                "type": "object",
                                "description": "Optional credentials used only after a login form is detected.",
                                "properties": {
                                    "username": {"type": "string"},
                                    "password": {"type": "string"},
                                },
                            },
                            "project_scope": {"type": "string", "description": "Optional project scope key for knowledge graph storage."},
                        },
                        "required": ["target_url"],
                    },
                    supports_streaming=True,
                    output_schema={"semantic_graph": "object", "app_map": "object", "artifacts": "array"},
                    tags=["ui", "exploration", "playwright", "aria", "graph"],
                ),
                handler_key="ui-page-explorer",
            ),
            "dom-inspector": ToolModule(
                descriptor=ToolDescriptor(
                    key="dom-inspector",
                    name="DOM Inspector",
                    description="Inspect page structure, selectors, and interactive elements.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "target_url": {"type": "string", "description": "The web page URL to inspect."},
                        },
                        "required": ["target_url"],
                    },
                    output_schema={"dom_summary": "string"},
                    tags=["ui", "inspection"],
                ),
                handler_key="dom-inspector",
            ),
            "api-tester": ToolModule(
                descriptor=ToolDescriptor(
                    key="api-tester",
                    name="API Tester",
                    description="Call APIs, validate payloads, and capture structured assertions.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "endpoint": {"type": "string", "description": "API endpoint or route."},
                            "method": {"type": "string", "description": "HTTP method."},
                            "request_body": {"type": "object"},
                            "response_body": {"type": "object"},
                            "response_status": {"type": "integer"},
                            "expected_status": {"type": "integer"},
                            "expected_fields": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Dot-path fields expected in the response body.",
                            },
                            "assertions": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Free-form checks to include in the report.",
                            },
                        },
                    },
                    output_schema={"checks": "array"},
                    tags=["api", "verification"],
                ),
                handler_key="api-tester",
            ),
            "cli-executor": ToolModule(
                descriptor=ToolDescriptor(
                    key="cli-executor",
                    name="CLI Executor",
                    description="Run shell or PowerShell commands with captured stdout, stderr, exit code, and transcript artifacts.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "The command text to execute."},
                            "shell": {
                                "type": "string",
                                "description": "Shell runtime to use, such as powershell or cmd.",
                                "default": "powershell",
                            },
                            "cwd": {"type": "string", "description": "Optional working directory within the workspace."},
                            "timeout_seconds": {
                                "type": "number",
                                "description": "Execution timeout in seconds.",
                                "default": 20,
                            },
                        },
                        "required": ["command"],
                    },
                    output_schema={"exit_code": "integer", "stdout": "string", "stderr": "string"},
                    tags=["cli", "shell", "terminal"],
                ),
                handler_key="cli-executor",
            ),
            "file-artifact-manager": ToolModule(
                descriptor=ToolDescriptor(
                    key="file-artifact-manager",
                    name="File Artifact Manager",
                    description="Persist run artifacts, screenshots, traces, and output files.",
                    category="artifact",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "file_name": {"type": "string", "description": "Artifact file name without extension."},
                            "extension": {"type": "string", "description": "Artifact extension like json, txt, md."},
                            "content": {"type": "string", "description": "Plain text artifact content."},
                            "json_data": {"type": "object", "description": "Structured JSON artifact content."},
                        },
                    },
                    output_schema={"artifact_paths": "array"},
                    tags=["artifact", "storage"],
                ),
                handler_key="file-artifact-manager",
            ),
            "report-writer": ToolModule(
                descriptor=ToolDescriptor(
                    key="report-writer",
                    name="Report Writer",
                    description="Summarize execution evidence into a structured QA report.",
                    category="reporting",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "objective": {"type": "string"},
                            "summary": {"type": "string"},
                            "findings": {"type": "array", "items": {"type": "object"}},
                            "evidence": {"type": "array", "items": {"type": "object"}},
                            "recommendations": {"type": "array", "items": {"type": "string"}},
                            "status": {"type": "string"},
                            "sender": {"type": "string", "description": "Display sender name rendered into the HTML report template."},
                            "content_markdown": {"type": "string", "description": "Optional prebuilt markdown body. When omitted, the runtime assembles markdown from structured fields."},
                            "template_key": {"type": "string", "description": "Optional report template key, such as default or code_review_debate."},
                            "project_name": {"type": "string", "description": "Project name used by the code review debate report template."},
                            "approval_time": {"type": "string", "description": "Approval time shown by the code review debate report template."},
                            "approval_result": {"type": "string", "description": "Final approval result shown by the code review debate report template."},
                            "agent_count": {"type": "integer", "description": "Participating agent count shown by the code review debate report template."},
                            "result_rows": {
                                "type": "array",
                                "description": "Structured result rows for the code review debate summary table.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "category": {"type": "string"},
                                        "title": {"type": "string"},
                                        "result": {"type": "string"},
                                        "proposer_agent": {"type": "string"},
                                        "agent": {"type": "string"},
                                        "summary": {"type": "string"},
                                        "detail": {"type": "string"},
                                        "recommended_action": {"type": "string"},
                                        "action": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                    output_schema={"report_sections": "array"},
                    tags=["reporting"],
                ),
                handler_key="report-writer",
            ),
            "project-source-loader": ToolModule(
                descriptor=ToolDescriptor(
                    key="project-source-loader",
                    name="Project Source Loader",
                    description="Validate and summarize a local or SSH project source for code review tasks.",
                    category="review",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "project_source": {"type": "object"},
                            "source_type": {"type": "string", "enum": ["local", "ssh"]},
                            "root_path": {"type": "string"},
                            "project_path": {"type": "string"},
                            "project_name": {"type": "string"},
                            "branch": {"type": "string"},
                            "commit_range": {"type": "string"},
                            "ssh_host": {"type": "string"},
                            "ssh_port": {"type": "integer"},
                            "ssh_username": {"type": "string"},
                            "ssh_auth_ref": {"type": "string"},
                            "remote_root_path": {"type": "string"},
                            "timeout_seconds": {"type": "number", "default": 20},
                        },
                    },
                    output_schema={"project_source": "object", "source_info": "object"},
                    tags=["code-review", "project-source", "ssh"],
                ),
                handler_key="project-source-loader",
            ),
            "project-tree-scanner": ToolModule(
                descriptor=ToolDescriptor(
                    key="project-tree-scanner",
                    name="Project Tree Scanner",
                    description="Scan a project tree and return module hints, sample files, and extension distribution for review planning.",
                    category="review",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "project_source": {"type": "object"},
                            "source_type": {"type": "string", "enum": ["local", "ssh"]},
                            "root_path": {"type": "string"},
                            "project_path": {"type": "string"},
                            "max_depth": {"type": "integer", "default": 3},
                            "max_files": {"type": "integer", "default": 400},
                            "timeout_seconds": {"type": "number", "default": 30},
                        },
                    },
                    output_schema={"tree_summary": "object", "sample_files": "array"},
                    tags=["code-review", "tree", "project-map"],
                ),
                handler_key="project-tree-scanner",
            ),
            "project-file-reader": ToolModule(
                descriptor=ToolDescriptor(
                    key="project-file-reader",
                    name="Project File Reader",
                    description="Read a file from a local or SSH project source with optional line slicing.",
                    category="review",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "project_source": {"type": "object"},
                            "source_type": {"type": "string", "enum": ["local", "ssh"]},
                            "root_path": {"type": "string"},
                            "project_path": {"type": "string"},
                            "path": {"type": "string"},
                            "file_path": {"type": "string"},
                            "start_line": {"type": "integer", "default": 1},
                            "end_line": {"type": "integer", "default": 200},
                            "max_chars": {"type": "integer", "default": 16000},
                            "timeout_seconds": {"type": "number", "default": 20},
                        },
                    },
                    output_schema={"content": "string", "file": "object"},
                    tags=["code-review", "file-read", "evidence"],
                ),
                handler_key="project-file-reader",
            ),
            "project-diff-reader": ToolModule(
                descriptor=ToolDescriptor(
                    key="project-diff-reader",
                    name="Project Diff Reader",
                    description="Read git status and diff context from a local or SSH project source.",
                    category="review",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "project_source": {"type": "object"},
                            "source_type": {"type": "string", "enum": ["local", "ssh"]},
                            "root_path": {"type": "string"},
                            "project_path": {"type": "string"},
                            "commit_range": {"type": "string"},
                            "diff_mode": {
                                "type": "string",
                                "description": "One of working_tree, staged, or range.",
                                "default": "working_tree",
                            },
                            "paths": {"type": "array", "items": {"type": "string"}},
                            "max_chars": {"type": "integer", "default": 24000},
                            "timeout_seconds": {"type": "number", "default": 20},
                        },
                    },
                    output_schema={"status_lines": "array", "diff_text": "string", "diff_stat": "string"},
                    tags=["code-review", "git", "diff"],
                ),
                handler_key="project-diff-reader",
            ),
            "code-governance-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="code-governance-runner",
                    name="Code Governance Runner",
                    description=(
                        "Run deterministic code governance pre-scan for a code review campaign. "
                        "It analyzes git diff, changed files, built-in security/database/dependency/test rules, "
                        "optional CI scanner JSON artifacts or installed external scanners, risk score, "
                        "and merge approval decision."
                    ),
                    category="review",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "project_source": {"type": "object"},
                            "source_type": {"type": "string", "enum": ["local", "ssh"]},
                            "root_path": {"type": "string"},
                            "project_path": {"type": "string"},
                            "project_name": {"type": "string"},
                            "commit_range": {"type": "string"},
                            "diff_mode": {
                                "type": "string",
                                "description": "One of working_tree, staged, or range.",
                                "default": "working_tree",
                            },
                            "paths": {"type": "array", "items": {"type": "string"}},
                            "excluded_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Additional paths or directory names to exclude. Code review always excludes "
                                    "dependency and build output directories such as node_modules, dist, build, "
                                    "target, venv, .git, and caches by default."
                                ),
                            },
                            "ignored_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Alias for excluded_paths.",
                            },
                            "diff_text": {"type": "string"},
                            "scanner_artifacts": {
                                "type": "object",
                                "description": "Optional scanner JSON paths, such as semgrep, bandit, pip_audit, npm_audit, or gitleaks.",
                            },
                            "run_external_scanners": {
                                "type": "boolean",
                                "description": "When true, execute installed scanners and merge their JSON findings.",
                                "default": False,
                            },
                            "external_scanners": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ],
                                "description": "Scanners to execute: semgrep, gitleaks, bandit, pip_audit, npm_audit, or all.",
                            },
                            "scanner_timeout_seconds": {
                                "type": "number",
                                "description": "Per-scanner timeout in seconds.",
                                "default": 120,
                            },
                            "skip_code_graph": {
                                "type": "boolean",
                                "description": "Skip lightweight call-chain and architecture graph construction.",
                                "default": False,
                            },
                            "max_graph_files": {
                                "type": "integer",
                                "description": "Maximum source files to include in the lightweight code graph.",
                                "default": 400,
                            },
                            "policy": {
                                "type": "object",
                                "description": "Optional governance policy overrides such as minimum_score and block_on_critical.",
                            },
                            "max_chars": {"type": "integer", "default": 80000},
                        },
                    },
                    output_schema={
                        "approval_decision": "string",
                        "decision": "object",
                        "risk_score": "object",
                        "findings": "array",
                        "changed_files": "array",
                        "code_graph": "object",
                        "scanner_runs": "array",
                        "report_json": "object",
                        "report_markdown": "string",
                    },
                    tags=["code-review", "governance", "ci", "risk-score", "approval"],
                ),
                handler_key="code-governance-runner",
            ),
            "code-review-orchestrator": ToolModule(
                descriptor=ToolDescriptor(
                    key="code-review-orchestrator",
                    name="Code Review Orchestrator",
                    description="Prepare and optionally launch a background multi-agent code review debate campaign for a local or SSH project source.",
                    category="review",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "project_source": {
                                "type": "object",
                                "description": "Project source definition for local or SSH-backed review.",
                                "properties": {
                                    "source_type": {"type": "string", "enum": ["local", "ssh"]},
                                    "root_path": {"type": "string"},
                                    "project_name": {"type": "string"},
                                    "branch": {"type": "string"},
                                    "commit_range": {"type": "string"},
                                    "ssh": {
                                        "type": "object",
                                        "properties": {
                                            "host": {"type": "string"},
                                            "port": {"type": "integer"},
                                            "username": {"type": "string"},
                                            "auth_ref": {"type": "string"},
                                            "remote_root_path": {"type": "string"},
                                        },
                                    },
                                },
                            },
                            "source_type": {"type": "string", "enum": ["local", "ssh"]},
                            "root_path": {"type": "string"},
                            "project_path": {"type": "string"},
                            "project_name": {"type": "string"},
                            "branch": {"type": "string"},
                            "commit_range": {"type": "string"},
                            "review_scope": {"type": "string", "description": "Review scope such as project, diff, module, or target-set."},
                            "change_summary": {"type": "string"},
                            "diff_text": {"type": "string"},
                            "targets": {"type": "array", "items": {"type": "string"}},
                            "excluded_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Additional paths or directory names to exclude from bootstrap, tree scanning, "
                                    "governance, graph building, and reviewer context. Dependency/build output "
                                    "directories are excluded by default."
                                ),
                            },
                            "ignored_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Alias for excluded_paths.",
                            },
                            "ssh_host": {"type": "string"},
                            "ssh_port": {"type": "integer"},
                            "ssh_username": {"type": "string"},
                            "ssh_auth_ref": {"type": "string"},
                            "remote_root_path": {"type": "string"},
                            "worker_model_key": {"type": "string"},
                            "summary_model_key": {"type": "string"},
                            "reviewer_count": {
                                "type": "integer",
                                "description": "Optional reviewer count override for code review debate, typically 2-5.",
                                "minimum": 2,
                                "maximum": 5,
                            },
                            "debate_time_budget_minutes": {
                                "type": "integer",
                                "description": "Optional moderator time budget for the whole debate. The coordinator derives the debate pace from project size and model context, capped at 60 minutes.",
                                "minimum": 5,
                                "maximum": 60,
                                "default": 20,
                            },
                            "email_to": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional email recipients for the final debate report.",
                            },
                            "email_subject": {
                                "type": "string",
                                "description": "Optional override for the final debate report email subject.",
                            },
                            "delivery_channel": {
                                "type": "string",
                                "enum": ["artifact", "email"],
                                "description": "Preferred delivery channel for the final report notification.",
                                "default": "artifact",
                            },
                            "launch_workers": {
                                "type": "boolean",
                                "description": "Whether to immediately launch isolated reviewer sessions in the background.",
                                "default": True,
                            },
                        },
                    },
                    output_schema={
                        "approval_decision": "string",
                        "findings": "array",
                        "campaign": "object",
                        "dispatch": "object",
                    },
                    tags=["code-review", "approval", "mode"],
                ),
                handler_key="code-review-orchestrator",
            ),
            "ui-automation-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="ui-automation-runner",
                    name="UI Automation Runner",
                    description="Mode-scoped entry tool for UI automation workflows.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "target_url": {"type": "string"},
                            "objective": {"type": "string"},
                            "project_scope": {"type": "string"},
                            "direction": {"type": "string"},
                            "subdirection": {"type": "string"},
                            "action": {"type": "string"},
                            "max_pages": {"type": "integer"},
                            "max_interactions": {"type": "integer"},
                            "same_origin_only": {"type": "boolean"},
                        },
                    },
                    output_schema={"ui_automation_state": "object", "artifacts": "array"},
                    tags=["ui", "automation", "mode"],
                ),
                handler_key="ui-automation-runner",
            ),
            "api-test-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="api-test-runner",
                    name="API Test Runner",
                    description="Mode-scoped entry tool for API interface testing workflows with structured phase, verification, evaluation, and report output.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "worker_action": {
                                "type": "string",
                                "description": "Optional worker action such as execute_task for dispatched subagent work.",
                            },
                            "task": {
                                "type": "object",
                                "description": "Serialized ApiTestTask payload when worker_action=execute_task.",
                            },
                            "credential_session": {
                                "type": "object",
                                "description": "Optional serialized credential session for worker task execution.",
                            },
                            "auth_token_field": {
                                "type": "string",
                                "description": "Response JSON field used to extract auth tokens from login tasks.",
                            },
                            "endpoint": {"type": "string"},
                            "method": {"type": "string"},
                            "objective": {"type": "string"},
                            "project_hint": {"type": "string"},
                            "domain_hint": {"type": "string"},
                            "scope_preference": {
                                "type": "string",
                                "description": "Endpoint scope preference: all_related, core_only, manual_pick, or single_target.",
                            },
                            "verification_focus": {"type": "string"},
                            "auth_hint": {"type": "string"},
                        },
                    },
                    output_schema={
                        "status": "string",
                        "phase": "string",
                        "summary": "string",
                        "pending_selection": "object",
                        "selected_project": "object",
                        "campaign_id": "string",
                        "task_count": "integer",
                        "report": "object",
                        "report_markdown": "string",
                        "verification_result": "object",
                        "evaluation_result": "object",
                        "artifacts": "array",
                        "errors": "array",
                        "execution_checkpoint": "object",
                        "task_events": "array",
                        "api_testing_state": "object",
                    },
                    tags=["api", "testing", "mode"],
                ),
                handler_key="api-test-runner",
            ),
            "security-scan-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="security-scan-runner",
                    name="Security Scan Runner",
                    description=(
                        "通用安全扫描 runner，执行安全测试 Campaign 的主入口。"
                        "接受 command_profile 和目标参数，通过结构化 profile 执行安全工具并返回解析后的结果。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "worker_action": {
                                "type": "string",
                                "description": "执行动作，如 execute_security_task 或 run_campaign。",
                            },
                            "command_profile": {
                                "type": "string",
                                "description": "命令 profile key，如 nmap_tcp_basic、nuclei_baseline。",
                            },
                            "target": {
                                "type": "string",
                                "description": "扫描目标（IP、URL、域名、网段）。",
                            },
                            "arguments": {
                                "type": "object",
                                "description": "传递给命令模板的额外参数。",
                            },
                            "task": {
                                "type": "object",
                                "description": "完整的 SecurityTask 序列化对象（worker 模式）。",
                            },
                            "objective": {"type": "string", "description": "测试目标描述。"},
                        },
                    },
                    output_schema=SECURITY_SCAN_RUNNER_OUTPUT_SCHEMA,
                    tags=["security", "testing", "scan", "runner"],
                ),
                handler_key="security-scan-runner",
            ),
            "security-tool-bootstrap": ToolModule(
                descriptor=ToolDescriptor(
                    key="security-tool-bootstrap",
                    name="Security Tool Bootstrap",
                    description=(
                        "P4 专用临时 Kali 工具就绪检查与 allowlist 安装。"
                        "仅接受服务端固定包计划；必须使用独立审批，不能复用扫描批准。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "campaign_id": {"type": "string"},
                            "target_allowlist": {"type": "array", "items": {"type": "string"}},
                            "profile_key": {"type": "string"},
                            "tool_name": {"type": "string"},
                            "requested_version": {"type": "string"},
                            "timeout_seconds": {"type": "number"},
                        },
                        "required": ["campaign_id", "target_allowlist", "profile_key", "tool_name"],
                    },
                    output_schema=SECURITY_TOOL_BOOTSTRAP_OUTPUT_SCHEMA,
                    tags=["security", "bootstrap", "installer", "p4"],
                ),
                handler_key="security-tool-bootstrap",
            ),
            "network-recon-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="network-recon-runner",
                    name="Network Recon Runner",
                    description=(
                        "网络侦察 runner，执行端口扫描、服务探测、资产发现等网络层侦察任务。"
                        "支持 nmap_tcp_basic、nmap_service_detect、nmap_full_scan 等 profile。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command_profile": {
                                "type": "string",
                                "description": "Profile key: nmap_tcp_basic / nmap_service_detect / nmap_full_scan / nmap_os_detect",
                                "enum": ["nmap_tcp_basic", "nmap_service_detect", "nmap_full_scan", "nmap_os_detect"],
                            },
                            "target": {"type": "string", "description": "目标 IP、域名或网段。"},
                            "ports": {"type": "string", "description": "端口范围，如 80,443,8080 或 1-1000。"},
                            "arguments": {"type": "object", "description": "额外参数。"},
                            "task": {"type": "object", "description": "SecurityTask 对象（worker 模式）。"},
                        },
                        "required": ["target"],
                    },
                    output_schema=SECURITY_RUNNER_OUTPUT_SCHEMA,
                    tags=["security", "network", "recon", "nmap", "runner"],
                ),
                handler_key="network-recon-runner",
            ),
            "web-scan-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="web-scan-runner",
                    name="Web Scan Runner",
                    description=(
                        "Web 安全扫描 runner，执行目录扫描、漏洞扫描、注入检测等 Web 层安全测试。"
                        "支持 httpx_probe、whatweb_fingerprint、http_headers_probe、ffuf_common_dirs、nikto_web_scan、"
                        "nuclei_baseline、sqlmap_readonly_probe 等 profile。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command_profile": {
                                "type": "string",
                                "description": "Profile key: httpx_probe / whatweb_fingerprint / http_headers_probe / ffuf_common_dirs / nikto_web_scan / nuclei_baseline / nuclei_cve_scan / sqlmap_readonly_probe",
                            },
                            "target": {"type": "string", "description": "目标 URL 或域名。"},
                            "arguments": {"type": "object", "description": "额外参数。"},
                            "task": {"type": "object", "description": "SecurityTask 对象（worker 模式）。"},
                        },
                        "required": ["target"],
                    },
                    output_schema=SECURITY_RUNNER_OUTPUT_SCHEMA,
                    tags=["security", "web", "scan", "nuclei", "nikto", "runner"],
                ),
                handler_key="web-scan-runner",
            ),
            "service-audit-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="service-audit-runner",
                    name="Service Audit Runner",
                    description=(
                        "服务审计 runner，执行 TLS/SSL 配置审计、漏洞情报检索等服务层安全检测。"
                        "支持 sslscan_tls_audit、searchsploit_lookup 等 profile。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command_profile": {
                                "type": "string",
                                "description": "Profile key: sslscan_tls_audit / searchsploit_lookup",
                            },
                            "target": {"type": "string", "description": "目标地址或查询关键词。"},
                            "query": {"type": "string", "description": "searchsploit 查询词。"},
                            "arguments": {"type": "object", "description": "额外参数。"},
                            "task": {"type": "object", "description": "SecurityTask 对象（worker 模式）。"},
                        },
                    },
                    output_schema=SECURITY_RUNNER_OUTPUT_SCHEMA,
                    tags=["security", "service", "audit", "ssl", "runner"],
                ),
                handler_key="service-audit-runner",
            ),
            "credential-attack-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="credential-attack-runner",
                    name="Credential Attack Runner",
                    description=(
                        "凭证攻击 runner，执行凭证爆破和弱密码检测（高风险，需审批）。"
                        "支持 hydra_basic_login 等 profile。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command_profile": {
                                "type": "string",
                                "description": "Profile key: hydra_basic_login",
                            },
                            "target": {"type": "string", "description": "目标地址。"},
                            "service": {"type": "string", "description": "目标服务，如 ssh、ftp、http-post-form。"},
                            "userlist": {"type": "string", "description": "用户名字典文件路径。"},
                            "passlist": {"type": "string", "description": "密码字典文件路径。"},
                            "arguments": {"type": "object", "description": "额外参数。"},
                            "task": {"type": "object", "description": "SecurityTask 对象（worker 模式）。"},
                        },
                        "required": ["target", "service"],
                    },
                    output_schema=SECURITY_RUNNER_OUTPUT_SCHEMA,
                    tags=["security", "credential", "attack", "hydra", "runner"],
                ),
                handler_key="credential-attack-runner",
            ),
            "traffic-analysis-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="traffic-analysis-runner",
                    name="Traffic Analysis Runner",
                    description="流量分析 runner，执行网络流量捕获和 TLS 分析（Phase 2）。",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command_profile": {"type": "string"},
                            "target": {"type": "string"},
                            "arguments": {"type": "object"},
                            "task": {"type": "object"},
                        },
                    },
                    output_schema=SECURITY_RUNNER_OUTPUT_SCHEMA,
                    tags=["security", "traffic", "analysis", "runner"],
                ),
                handler_key="traffic-analysis-runner",
            ),
            "exploit-workbench-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="exploit-workbench-runner",
                    name="Exploit Workbench Runner",
                    description="漏洞利用工作台 runner，执行 PoC 验证和漏洞利用（高风险，Phase 3/4）。",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "command_profile": {"type": "string"},
                            "target": {"type": "string"},
                            "arguments": {"type": "object"},
                            "task": {"type": "object"},
                        },
                    },
                    output_schema=SECURITY_RUNNER_OUTPUT_SCHEMA,
                    tags=["security", "exploit", "runner"],
                ),
                handler_key="exploit-workbench-runner",
            ),
            "performance-test-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="performance-test-runner",
                    name="Performance Test Runner",
                    description=(
                        "性能/负载测试主入口工具。接受目标、负载模型、SLA 参数，"
                        "驱动 k6 引擎生成脚本、冒烟验证、正式压测、结果解析和报告生成。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "objective": {"type": "string", "description": "测试目标描述。"},
                            "target_url": {"type": "string", "description": "压测目标 URL。"},
                            "method": {"type": "string", "description": "HTTP 方法。", "default": "GET"},
                            "workload_model": {
                                "type": "string",
                                "description": "负载模型: open (到达率) 或 closed (固定VU)。",
                                "enum": ["open", "closed"],
                            },
                            "target_rate_rps": {"type": "integer", "description": "目标到达率 (rps)，open 模型使用。"},
                            "virtual_users": {"type": "integer", "description": "虚拟用户数，closed 模型使用。"},
                            "duration_seconds": {"type": "integer", "description": "持续时长（秒）。"},
                            "run_intent": {
                                "type": "string",
                                "description": "运行意图: probe (探测基线) 或 regression (回归验证)。",
                                "enum": ["probe", "regression"],
                            },
                            "sla_p95_ms": {"type": "number", "description": "SLA P95 延迟阈值 (ms)。"},
                            "sla_p99_ms": {"type": "number", "description": "SLA P99 延迟阈值 (ms)。"},
                            "sla_error_rate": {"type": "number", "description": "SLA 错误率阈值。"},
                            "headers": {"type": "object", "description": "自定义请求头。"},
                            "body_template": {"description": "请求体模板。"},
                            "confirm_target": {"type": "boolean", "description": "用户已确认目标。"},
                            "worker_action": {"type": "string", "description": "Worker 模式动作。"},
                            "arguments": {"type": "object", "description": "透传参数。"},
                        },
                    },
                    output_schema=PERFORMANCE_RUNNER_OUTPUT_SCHEMA,
                    tags=["performance", "testing", "mode", "k6", "load"],
                ),
                handler_key="performance-test-runner",
            ),
            "perf-plan-compiler": ToolModule(
                descriptor=ToolDescriptor(
                    key="perf-plan-compiler",
                    name="Perf Plan Compiler",
                    description="将性能测试计划编译为引擎脚本，纯计算无副作用。",
                    category="execution",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "plan": {"type": "object", "description": "PerfPlan 序列化对象。"},
                            "engine": {"type": "string", "description": "引擎 key，默认 k6。", "default": "k6"},
                        },
                        "required": ["plan"],
                    },
                    output_schema={"script_content": "string", "filename": "string", "data_files": "object"},
                    tags=["performance", "planning", "script"],
                ),
                handler_key="perf-plan-compiler",
            ),
            "perf-container-manager": ToolModule(
                descriptor=ToolDescriptor(
                    key="perf-container-manager",
                    name="Perf Container Manager",
                    description=(
                        "管理 k6/JMeter 压测引擎 Docker 容器生命周期。"
                        "在编排完成后启动容器，测试完成后销毁容器。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "description": "容器动作: start, stop/destroy, status, cleanup。",
                                "enum": ["start", "stop", "destroy", "status", "cleanup"],
                            },
                            "engine": {
                                "type": "string",
                                "description": "性能引擎: k6 或 jmeter。",
                                "enum": ["k6", "jmeter"],
                                "default": "k6",
                            },
                            "container_name": {"type": "string", "description": "容器名称，start 可选，stop/status 必填。"},
                        },
                        "required": ["action", "engine"],
                    },
                    output_schema={
                        "ok": "boolean",
                        "action": "string",
                        "engine": "string",
                        "container_name": "string",
                        "status": "string",
                        "summary": "string",
                    },
                    tags=["performance", "docker", "container", "lifecycle"],
                ),
                handler_key="perf-container-manager",
            ),
            "api-docs-ingest": ToolModule(
                descriptor=ToolDescriptor(
                    key="api-docs-ingest",
                    name="API Docs Ingest",
                    description=(
                        "将附件或 URL 导入 API 文档库，支持 OpenAPI/Swagger 自动识别和转换。"
                        "用于性能测试等模式在缺少接口文档时补充文档后自动续跑。"
                    ),
                    category="knowledge",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "description": "导入动作: import_attachment 或 import_url。",
                                "enum": ["import_attachment", "import_url"],
                            },
                            "attachment_id": {"type": "string", "description": "附件 ID，import_attachment 时使用。"},
                            "url": {"type": "string", "description": "OpenAPI/Swagger/Markdown 文档 URL，import_url 时使用。"},
                            "filename": {"type": "string", "description": "文件名，import_attachment 时使用。"},
                            "content_base64": {"type": "string", "description": "Base64 编码的文件内容，import_attachment 时使用。"},
                            "project_name": {"type": "string", "description": "项目名称。"},
                            "project_url": {"type": "string", "description": "项目 URL。"},
                            "title": {"type": "string", "description": "文档标题。"},
                        },
                        "required": ["action"],
                    },
                    output_schema={
                        "ok": "boolean",
                        "action": "string",
                        "summary": "string",
                        "document": "object",
                    },
                    tags=["api", "docs", "import", "performance"],
                ),
                handler_key="api-docs-ingest",
            ),
            "perf-result-analyzer": ToolModule(
                descriptor=ToolDescriptor(
                    key="perf-result-analyzer",
                    name="Perf Result Analyzer",
                    description="解析压测 RawMetrics 并生成结构化报告，纯计算无副作用。",
                    category="reporting",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "raw_metrics": {"type": "object", "description": "RawMetrics 序列化对象。"},
                            "plan": {"type": "object", "description": "PerfPlan 序列化对象。"},
                            "run": {"type": "object", "description": "PerfRun 序列化对象。"},
                            "baseline_metrics": {"type": "object", "description": "可选基线 PerfMetrics。"},
                        },
                        "required": ["raw_metrics", "plan", "run"],
                    },
                    output_schema={
                        "verdict": "string",
                        "metrics": "object",
                        "sla_result": "object",
                        "error_breakdown": "object",
                        "report_markdown": "string",
                    },
                    tags=["performance", "analysis", "reporting"],
                ),
                handler_key="perf-result-analyzer",
            ),
            "perf-engine-select": ToolModule(
                descriptor=ToolDescriptor(
                    key="perf-engine-select",
                    name="Perf Engine Select",
                    description=(
                        "根据场景、目标类型和用户偏好选择 k6 或 jmeter 引擎。"
                        "模型不直接选镜像，而是通过此工具选择能力链路。"
                    ),
                    category="execution",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "scenario": {
                                "type": "string",
                                "description": "测试场景: api_baseline, api_regression, complex_flow, ci_load 等。",
                                "enum": ["api_baseline", "api_regression", "complex_flow", "ci_load", "smoke", "probe"],
                            },
                            "target_kind": {
                                "type": "string",
                                "description": "目标类型: http_api, complex_flow, jmx_reuse。",
                                "default": "http_api",
                            },
                            "user_requested_engine": {
                                "type": "string",
                                "description": "用户指定的引擎偏好: k6, jmeter, 或留空由系统选择。",
                            },
                            "needs_complex_flow": {"type": "boolean", "default": False},
                            "has_existing_jmx": {"type": "boolean", "default": False},
                        },
                        "required": ["scenario"],
                    },
                    output_schema={
                        "ok": "boolean",
                        "engine": "string",
                        "reason": "string",
                        "selected_image_key": "string",
                        "selected_image": "string",
                    },
                    tags=["performance", "engine", "selection"],
                ),
                handler_key="perf-engine-select",
            ),
            "http-probe-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="http-probe-runner",
                    name="HTTP Probe Runner",
                    description=(
                        "使用 helper 镜像对目标发起连通性探针，验证目标可达性、"
                        "响应状态码和响应时间。适合压测前的前置检查。"
                    ),
                    category="execution",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "target_url": {"type": "string", "description": "探针目标 URL。"},
                            "method": {"type": "string", "default": "GET"},
                            "headers": {"type": "object"},
                            "timeout_seconds": {"type": "integer", "default": 10},
                        },
                        "required": ["target_url"],
                    },
                    output_schema={
                        "ok": "boolean",
                        "status_code": "integer",
                        "response_time_ms": "number",
                        "selected_image_key": "string",
                        "reason": "string",
                        "next_recommendation": "string",
                    },
                    tags=["performance", "helper", "probe", "connectivity"],
                ),
                handler_key="http-probe-runner",
            ),
            "mock-target-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="mock-target-runner",
                    name="Mock Target Runner",
                    description=(
                        "启动和停止本地 mock HTTP 回显靶机，用于压测链路验证。"
                        "启动后返回容器名和端口，停止时需提供容器名。"
                    ),
                    category="execution",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["start", "stop"],
                            },
                            "port": {"type": "integer", "default": 18080},
                            "response_text": {"type": "string", "default": "ok"},
                            "container_name": {"type": "string", "description": "stop 时必填。"},
                        },
                        "required": ["action"],
                    },
                    output_schema={
                        "ok": "boolean",
                        "action": "string",
                        "container_name": "string",
                        "port": "integer",
                        "selected_image_key": "string",
                        "reason": "string",
                    },
                    tags=["performance", "helper", "mock", "local"],
                ),
                handler_key="mock-target-runner",
            ),
            "performance-engine-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="performance-engine-runner",
                    name="Performance Engine Runner",
                    description=(
                        "统一的性能引擎执行入口。内部通过 image resolver 解析为"
                        "k6 或 jmeter 镜像，验证镜像可用性后委托执行。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "engine": {"type": "string", "enum": ["k6", "jmeter"]},
                            "scenario": {"type": "string", "default": "api_baseline"},
                            "plan": {"type": "object"},
                        },
                        "required": ["engine"],
                    },
                    output_schema={
                        "ok": "boolean",
                        "engine": "string",
                        "selected_image_key": "string",
                        "selected_image": "string",
                        "reason": "string",
                        "fallback_used": "boolean",
                    },
                    tags=["performance", "engine", "runner"],
                ),
                handler_key="performance-engine-runner",
            ),
            "compatibility-test-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="compatibility-test-runner",
                    name="Compatibility Test Runner",
                    description=(
                        "兼容性测试模式主入口。负责产品接入、环境矩阵生成、用例生成、"
                        "风险审批计划、Runner/Provider 调度规划和兼容性报告结构化输出。"
                    ),
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["draft_plan", "get_capabilities", "execute_approved_plan"],
                                "description": "兼容性模式动作：生成计划、查看能力或执行已确认计划。",
                            },
                            "objective": {"type": "string", "description": "兼容性测试目标。"},
                            "product": {"type": "object", "description": "结构化产品接入信息。"},
                            "product_name": {"type": "string", "description": "产品名称。"},
                            "product_type": {
                                "type": "string",
                                "enum": [
                                    "web",
                                    "h5",
                                    "android_app",
                                    "ios_app",
                                    "wechat_mini_program",
                                    "alipay_mini_program",
                                    "linux_app",
                                    "unknown",
                                ],
                                "description": "被测产品类型。",
                            },
                            "target_url": {"type": "string", "description": "Web/H5 入口 URL。"},
                            "entrypoint": {"type": "string", "description": "产品入口 URL/AppID/启动命令。"},
                            "product_access_manifest": {
                                "type": "object",
                                "description": "完整产品接入清单，包含 entrypoint/auth/network/test_scope 等结构化字段。",
                            },
                            "access_manifest": {
                                "type": "object",
                                "description": "product_access_manifest 的别名。",
                            },
                            "product_version": {"type": "string", "description": "产品版本号或构建版本。"},
                            "artifact": {"type": "string", "description": "APK/IPA/安装包等构建产物 URI。"},
                            "artifact_type": {
                                "type": "string",
                                "description": "artifact 的类型，如 entrypoint/build_artifact/apk/ipa/package。",
                            },
                            "auth_strategy": {"type": "string", "description": "认证策略，如 test_account/manual_login/no_auth。"},
                            "username_ref": {"type": "string", "description": "用户名 secret 引用。"},
                            "password_ref": {"type": "string", "description": "密码 secret 引用。"},
                            "token_ref": {"type": "string", "description": "Token secret 引用。"},
                            "manual_steps": {"type": "array", "items": {"type": "string"}, "description": "验证码、2FA、扫码等人工接管节点。"},
                            "package_name": {"type": "string", "description": "Android App 包名。"},
                            "activity": {"type": "string", "description": "Android App 启动 Activity。"},
                            "bundle_id": {"type": "string", "description": "iOS App Bundle ID。"},
                            "mini_program_path": {"type": "string", "description": "小程序入口路径。"},
                            "command": {"type": "string", "description": "Linux/Desktop App 启动命令。"},
                            "base_api": {"type": "string", "description": "被测产品后端基础 API 地址。"},
                            "proxy": {"type": "string", "description": "执行测试所需代理地址。"},
                            "requires_vpn": {"type": "boolean", "description": "测试网络是否需要 VPN 或专线。"},
                            "priority_flows": {"type": "array", "items": {"type": "string"}, "description": "核心业务流程。"},
                            "test_scope": {"type": "array", "items": {"type": "string"}, "description": "测试范围。"},
                            "exclude": {"type": "array", "items": {"type": "string"}, "description": "排除的模块、页面或流程。"},
                            "forbidden_actions": {"type": "array", "items": {"type": "string"}, "description": "禁测或需审批动作。"},
                            "data_policy": {"type": "string", "description": "测试数据策略，如 read_only/sandbox_write。"},
                            "environments": {"type": "array", "items": {"type": "object"}, "description": "可选自定义环境矩阵。"},
                            "plan_id": {"type": "string", "description": "执行已确认计划时的计划 ID。"},
                            "plan": {"type": "object", "description": "执行已确认计划时的内联 CompatibilityPlan 对象。"},
                            "approved_plan": {"type": "object", "description": "plan 的别名，用于传入已确认的兼容性计划。"},
                            "confirm_risks": {"type": "boolean", "description": "计划包含风险/人工接管项时，是否已由用户确认。"},
                            "selected_case_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "用户勾选执行的兼容性用例 ID；未提供时默认全选，显式空数组会被视为未选择用例。",
                            },
                            "selected_environment_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "用户勾选执行的环境 ID；未提供时默认使用计划内环境，显式空数组会被视为未选择环境。",
                            },
                        },
                        "required": ["action"],
                    },
                    output_schema=COMPATIBILITY_RUNNER_OUTPUT_SCHEMA,
                    tags=["compatibility", "testing", "mode", "matrix", "runner", "approval"],
                ),
                handler_key="compatibility-test-runner",
            ),
            "smoke-suite-runner": ToolModule(
                descriptor=ToolDescriptor(
                    key="smoke-suite-runner",
                    name="Smoke Suite Runner",
                    description="Generate, revise, persist, and execute user-approved smoke testing plans with RustFS/PostgreSQL asset tracking.",
                    category="execution",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["draft_plan", "revise_plan", "execute_approved_plan", "get_plan"],
                                "description": "冒烟模式动作：生成方案、修订方案、执行已确认方案或读取方案。",
                            },
                            "objective": {"type": "string", "description": "用户冒烟测试目标。"},
                            "target_url": {"type": "string", "description": "被测系统入口地址。"},
                            "project_scope": {"type": "string", "description": "可选项目范围；未提供时按 target_url 自动匹配。"},
                            "api_doc_ids": {"type": "array", "items": {"type": "string"}, "description": "可选 API 文档 ID。"},
                            "attachment_ids": {"type": "array", "items": {"type": "string"}, "description": "可选会话附件 ID。"},
                            "plan_id": {"type": "string", "description": "待修订或执行的方案 ID。"},
                            "approved_plan_id": {"type": "string", "description": "已确认方案 ID。"},
                            "plan": {"type": "object", "description": "可选内联方案对象。"},
                            "user_revision": {"type": "string", "description": "用户对方案的修改意见。"},
                            "selected_case_ids": {"type": "array", "items": {"type": "string"}, "description": "用户勾选执行的用例 ID。"},
                            "selected_indices": {"type": "array", "items": {"type": "integer"}, "description": "用户勾选执行的用例序号。"},
                            "max_cases": {"type": "integer", "description": "最多生成的冒烟用例数。"},
                            "include_api": {"type": "boolean"},
                            "include_ui": {"type": "boolean"},
                            "include_health_check": {"type": "boolean"},
                            "allow_write_operations": {"type": "boolean", "description": "是否允许执行写操作；默认 false。"},
                            "credentials_policy": {"type": "string", "description": "凭据策略，默认 memory_lookup。"},
                        },
                        "required": ["action"],
                    },
                    output_schema={
                        "summary": "string",
                        "plan": "object",
                        "run_result": "object",
                        "plan_uri": "string",
                        "approved_plan_uri": "string",
                        "run_result_uri": "string",
                        "report_uri": "string",
                    },
                    tags=["smoke", "testing", "mode", "approval", "regression-candidate"],
                ),
                handler_key="smoke-suite-runner",
            ),
            "mail-status": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-status",
                    name="Mail Status",
                    description="Check the status and capabilities of the active Agent Mailbox provider.",
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "provider": {"type": "string", "description": "Optional provider key override."},
                        },
                    },
                    output_schema={"ok": "boolean", "provider": "string", "capabilities": "array"},
                    tags=["mail", "mailbox", "status"],
                ),
                handler_key="mail-status",
            ),
            "mail-send": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-send",
                    name="Mail Send Prepare",
                    description=(
                        "Prepare an email and return a confirmation token/summary without sending it. "
                        "After showing the summary and receiving explicit user confirmation, call mail-confirm."
                    ),
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "to": {
                                "type": "array",
                                "items": {"type": "string", "format": "email"},
                                "minItems": 1,
                                "description": "Real recipient email addresses explicitly supplied by the user. Never invent recipients.",
                            },
                            "subject": {"type": "string", "description": "Email subject."},
                            "content": {"type": "string", "description": "Plain text body."},
                            "content_html": {"type": "string", "description": "Optional HTML body."},
                            "content_markdown": {
                                "type": "string",
                                "description": "Markdown report body rendered through the selected HTML email template when content_html is absent.",
                            },
                            "sender": {"type": "string", "description": "Display sender used by the HTML report template."},
                            "time_label": {"type": "string", "description": "Optional time label used by the HTML report template."},
                            "template_key": {
                                "type": "string",
                                "description": "Report template key: default, code_review_debate, or security_testing_full.",
                            },
                            "template_context": {"type": "object", "description": "Template-specific report values."},
                        },
                        "required": ["to", "subject"],
                    },
                    output_schema={
                        "sent": "boolean",
                        "confirmation_required": "boolean",
                        "confirmation_token": "string",
                        "confirmation_summary": "string",
                    },
                    tags=["mail", "mailbox", "send"],
                ),
                handler_key="mail-send",
            ),
            "mail-confirm": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-confirm",
                    name="Mail Confirm",
                    description=(
                        "Commit a previously prepared send, reply, or forward using its confirmation token. "
                        "Call only in a later turn after the user explicitly confirms the displayed summary."
                    ),
                    category="communication",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": ["send", "reply", "forward", "trash"],
                                "description": "Prepared operation type.",
                            },
                            "confirmation_token": {
                                "type": "string",
                                "description": "Opaque ctk_* token returned by the prepare tool.",
                            },
                        },
                        "required": ["operation", "confirmation_token"],
                    },
                    output_schema={"sent": "boolean", "message_id": "string"},
                    tags=["mail", "mailbox", "confirmation", "write"],
                ),
                handler_key="mail-confirm",
            ),
            "mail-list": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-list",
                    name="Mail List",
                    description="List messages in the Agent Mailbox inbox.",
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "folder": {"type": "string", "description": "Folder name.", "default": "inbox"},
                            "limit": {"type": "integer", "description": "Max messages to return.", "default": 20},
                            "before": {"type": "string", "description": "ISO timestamp upper bound."},
                            "after": {"type": "string", "description": "ISO timestamp lower bound."},
                        },
                    },
                    output_schema={"messages": "array"},
                    tags=["mail", "mailbox", "list"],
                ),
                handler_key="mail-list",
            ),
            "mail-read": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-read",
                    name="Mail Read",
                    description="Read a single message by ID from the Agent Mailbox.",
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "message_id": {"type": "string", "description": "Message identifier."},
                        },
                        "required": ["message_id"],
                    },
                    output_schema={"message": "object"},
                    tags=["mail", "mailbox", "read"],
                ),
                handler_key="mail-read",
            ),
            "mail-search": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-search",
                    name="Mail Search",
                    description="Search messages in the Agent Mailbox by query string.",
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query."},
                            "limit": {"type": "integer", "description": "Max results.", "default": 20},
                        },
                        "required": ["query"],
                    },
                    output_schema={"messages": "array"},
                    tags=["mail", "mailbox", "search"],
                ),
                handler_key="mail-search",
            ),
            "mail-reply": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-reply",
                    name="Mail Reply",
                    description="Prepare a reply and return confirmation details without committing it.",
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "message_id": {"type": "string", "description": "Original message ID to reply to."},
                            "content": {"type": "string", "description": "Reply body text."},
                            "content_html": {"type": "string", "description": "Optional HTML reply body."},
                        },
                        "required": ["message_id", "content"],
                    },
                    output_schema={"ok": "boolean", "message_id": "string"},
                    tags=["mail", "mailbox", "reply"],
                ),
                handler_key="mail-reply",
            ),
            "mail-forward": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-forward",
                    name="Mail Forward",
                    description="Prepare a forward and return confirmation details without committing it.",
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "message_id": {"type": "string", "description": "Message ID to forward."},
                            "to": {"type": "array", "items": {"type": "string"}, "description": "Forward recipients."},
                            "comment": {"type": "string", "description": "Optional note prepended to the forwarded message."},
                        },
                        "required": ["message_id", "to"],
                    },
                    output_schema={"ok": "boolean", "message_id": "string"},
                    tags=["mail", "mailbox", "forward"],
                ),
                handler_key="mail-forward",
            ),
            "mail-trash": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-trash",
                    name="Mail Trash Prepare",
                    description="Prepare moving a mailbox message to trash and return confirmation details.",
                    category="communication",
                    permission_level="safe",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "message_id": {"type": "string", "description": "Message ID to move to trash."},
                        },
                        "required": ["message_id"],
                    },
                    output_schema={
                        "confirmation_required": "boolean",
                        "confirmation_token": "string",
                        "confirmation_summary": "string",
                    },
                    tags=["mail", "mailbox", "trash", "write"],
                ),
                handler_key="mail-trash",
            ),
            "mail-download-attachment": ToolModule(
                descriptor=ToolDescriptor(
                    key="mail-download-attachment",
                    name="Mail Download Attachment",
                    description="Download an attachment from a mailbox message.",
                    category="communication",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "message_id": {"type": "string", "description": "Parent message ID."},
                            "attachment_id": {"type": "string", "description": "Attachment identifier."},
                        },
                        "required": ["message_id", "attachment_id"],
                    },
                    output_schema={"ok": "boolean", "path": "string", "size_bytes": "integer"},
                    tags=["mail", "mailbox", "attachment"],
                ),
                handler_key="mail-download-attachment",
            ),
        }
        self._apply_exposure_metadata()
        self._dynamic_tools: dict[str, ToolModule] = {}

    def _apply_exposure_metadata(self) -> None:
        workflow_entries = {
            "api-test-runner": ("api_testing", ["api.validation"]),
            "performance-test-runner": ("performance_testing", ["performance.load_test"]),
            "security-scan-runner": ("security_testing", ["security.assessment"]),
            "ui-automation-runner": ("ui_automation", ["ui.automation"]),
            "compatibility-test-runner": ("compatibility_testing", ["compatibility.matrix_test"]),
            "smoke-suite-runner": ("smoke_testing", ["smoke.validation"]),
            "code-review-orchestrator": ("code_review", ["code.review"]),
        }
        shared = {
            "api-docs-library": ["api.documentation.read"],
            "api-docs-ingest": ["api.documentation.write"],
            "knowledge-rag": ["knowledge.search"],
            "report-writer": ["report.generate"],
            "attachment-reader": ["resource.attachment.read"],
        }
        internal_by_mode = {
            "performance_testing": {
                "perf-plan-compiler", "perf-result-analyzer", "perf-engine-select", "http-probe-runner",
                "mock-target-runner", "performance-engine-runner", "perf-container-manager",
            },
            "security_testing": {
                "network-recon-runner", "web-scan-runner", "service-audit-runner",
                "credential-attack-runner", "traffic-analysis-runner", "exploit-workbench-runner",
                "security-tool-bootstrap",
            },
            "code_review": {
                "code-governance-runner", "project-source-loader", "project-tree-scanner",
                "project-file-reader", "project-diff-reader",
            },
        }
        for key, (owner_mode_key, capability_keys) in workflow_entries.items():
            self._replace_descriptor(
                key,
                exposure="workflow_entry",
                owner_mode_key=owner_mode_key,
                capability_keys=capability_keys,
            )
        for key, capability_keys in shared.items():
            self._replace_descriptor(key, exposure="shared", capability_keys=capability_keys)
        self._replace_descriptor("api-docs-ingest", permission_level="ask")
        for owner_mode_key, keys in internal_by_mode.items():
            for key in keys:
                self._replace_descriptor(
                    key,
                    exposure="internal",
                    owner_mode_key=owner_mode_key,
                    capability_keys=[f"{owner_mode_key}.internal"],
                )

    def _replace_descriptor(self, key: str, **updates) -> None:
        module = self._tools.get(key)
        if module is None:
            return
        self._tools[key] = ToolModule(
            descriptor=module.descriptor.model_copy(update=updates),
            handler_key=module.handler_key,
        )

    def list(self) -> list[ToolDescriptor]:
        return [
            *[module.descriptor for module in self._tools.values()],
            *[module.descriptor for module in self._dynamic_tools.values()],
        ]

    def list_summaries(self) -> list[dict[str, object]]:
        return [self._summarize(tool) for tool in self.list()]

    def search(
        self,
        query: str,
        limit: int = 10,
        include_schema: bool = False,
    ) -> dict[str, object]:
        tools = self.list()
        query_text = str(query or "").strip()
        max_results = max(int(limit or 0), 1)
        if not query_text:
            matches = tools[:max_results]
        else:
            selected = self._parse_select_query(query_text)
            if selected:
                matches = self.get_many(selected)[:max_results]
            else:
                matches = [
                    item["tool"]
                    for item in sorted(
                        (
                            {"tool": tool, "score": self._score_tool(tool, query_text)}
                            for tool in tools
                        ),
                        key=lambda item: (-int(item["score"]), item["tool"].name),
                    )
                    if int(item["score"]) > 0
                ][:max_results]
        return {
            "query": query_text,
            "total_tools": len(tools),
            "matches": [
                tool.model_dump(mode="python") if include_schema else self._summarize(tool)
                for tool in matches
            ],
        }

    def get(self, key: str) -> ToolDescriptor:
        module = self._dynamic_tools.get(key) or self._tools.get(key)
        if module is None:
            raise KeyError(f"Unknown tool: {key}")
        return module.descriptor

    def get_many(self, keys: list[str]) -> list[ToolDescriptor]:
        return [self.get(key) for key in keys if key in self._tools or key in self._dynamic_tools]

    def get_handler_key(self, key: str) -> str | None:
        module = self._dynamic_tools.get(key) or self._tools.get(key)
        if module is None:
            raise KeyError(f"Unknown tool: {key}")
        return module.handler_key

    def has_handler_binding(self, key: str) -> bool:
        module = self._dynamic_tools.get(key) or self._tools.get(key)
        return module is not None and module.handler_key is not None

    def register_dynamic(self, descriptor: ToolDescriptor, handler_key: str) -> None:
        if descriptor.key in self._tools:
            raise ValueError(f"Cannot dynamically override static tool '{descriptor.key}'.")
        self._dynamic_tools[descriptor.key] = ToolModule(
            descriptor=descriptor,
            handler_key=handler_key,
        )

    def unregister_dynamic(self, key: str) -> None:
        self._dynamic_tools.pop(key, None)

    def build_model_tools(self, keys: list[str]) -> list[dict]:
        tools = self.get_many(keys)
        return [
            {
                "name": tool.key,
                "description": tool.description,
                "input_schema": tool.input_schema or {"type": "object", "properties": {}},
            }
            for tool in tools
        ]

    def _summarize(self, tool: ToolDescriptor) -> dict[str, object]:
        return {
            "key": tool.key,
            "name": tool.name,
            "description": tool.description,
            "category": tool.category,
            "permission_level": tool.permission_level,
            "supports_streaming": tool.supports_streaming,
            "enabled_by_default": tool.enabled_by_default,
            "tags": list(tool.tags),
            "capability_keys": list(tool.capability_keys),
            "exposure": tool.exposure,
            "owner_mode_key": tool.owner_mode_key,
        }

    def _parse_select_query(self, query: str) -> list[str]:
        prefix = "select:"
        if not query.lower().startswith(prefix):
            return []
        return [
            item.strip()
            for item in query[len(prefix) :].split(",")
            if item.strip()
        ]

    def _score_tool(self, tool: ToolDescriptor, query: str) -> int:
        terms = [
            term.strip().lower()
            for term in query.replace("_", " ").replace("-", " ").split()
            if term.strip()
        ]
        if not terms:
            return 0
        name_text = f"{tool.key} {tool.name}".lower()
        tag_text = " ".join(tool.tags).lower()
        description_text = tool.description.lower()
        category_text = tool.category.lower()
        score = 0
        for term in terms:
            if term in tool.key.lower():
                score += 12
            if term in name_text:
                score += 8
            if term in tag_text:
                score += 5
            if term in category_text:
                score += 4
            if term in description_text:
                score += 2
        return score
