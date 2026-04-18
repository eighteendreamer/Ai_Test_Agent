from __future__ import annotations

from dataclasses import dataclass

from src.schemas.agent import ToolDescriptor


@dataclass(frozen=True)
class ToolModule:
    descriptor: ToolDescriptor
    handler_key: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolModule] = {
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
                                "description": "Optional explicit Selenium action list for the browser executor.",
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
                                "description": "One of navigate, inspect, screenshot, evaluate_js, or run_actions.",
                            },
                            "target_url": {"type": "string", "description": "The target page URL."},
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
            "message-dispatch": ToolModule(
                descriptor=ToolDescriptor(
                    key="message-dispatch",
                    name="Message Dispatch",
                    description="Send execution notifications as email or persist message payloads as local delivery artifacts.",
                    category="communication",
                    permission_level="ask",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "channel": {
                                "type": "string",
                                "description": "Delivery channel, such as email or artifact.",
                                "default": "artifact",
                            },
                            "to": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "One or more recipients for external delivery.",
                            },
                            "subject": {"type": "string", "description": "Message subject or title."},
                            "content": {"type": "string", "description": "Plain text message content."},
                            "content_html": {"type": "string", "description": "Optional HTML content for email delivery."},
                            "file_name": {"type": "string", "description": "Optional artifact file name for local delivery."},
                        },
                    },
                    output_schema={"delivery": "object", "artifacts": "array"},
                    tags=["message", "notification", "email"],
                ),
                handler_key="message-dispatch",
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
                        },
                    },
                    output_schema={"report_sections": "array"},
                    tags=["reporting"],
                ),
                handler_key="report-writer",
            ),
        }

    def list(self) -> list[ToolDescriptor]:
        return [module.descriptor for module in self._tools.values()]

    def get(self, key: str) -> ToolDescriptor:
        if key not in self._tools:
            raise KeyError(f"Unknown tool: {key}")
        return self._tools[key].descriptor

    def get_many(self, keys: list[str]) -> list[ToolDescriptor]:
        return [self._tools[key].descriptor for key in keys if key in self._tools]

    def get_handler_key(self, key: str) -> str | None:
        if key not in self._tools:
            raise KeyError(f"Unknown tool: {key}")
        return self._tools[key].handler_key

    def has_handler_binding(self, key: str) -> bool:
        return key in self._tools and self._tools[key].handler_key is not None

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
