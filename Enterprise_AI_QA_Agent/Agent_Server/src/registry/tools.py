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
            "test-case-generator": ToolModule(
                descriptor=ToolDescriptor(
                    key="test-case-generator",
                    name="Test Case Generator",
                    description="Generate structured test scenarios, assertions, and coverage suggestions.",
                    category="qa",
                    permission_level="safe",
                    output_schema={"cases": "array"},
                    tags=["planning", "qa"],
                ),
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
                    output_schema={"checks": "array"},
                    tags=["api", "verification"],
                ),
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
                    output_schema={"report_sections": "array"},
                    tags=["reporting"],
                ),
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
