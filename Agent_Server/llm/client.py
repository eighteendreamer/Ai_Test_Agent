"""
LLM 客户端模块

提供与原有 LLMClient 兼容的接口，方便迁移

作者: 程序员Eighteen
版本: 1.0
"""
import json
import logging
from typing import Any, Dict, List, Optional

from .manager import get_active_llm_config, model_config_manager
from .factory import create_llm_provider
from .base import BaseLLMProvider

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM 客户端
    
    兼容原有 LLMClient 接口，使用新的 Provider 架构
    """
    
    def __init__(self):
        self._provider: Optional[BaseLLMProvider] = None
        self._config: Optional[Dict] = None
    
    def _get_config(self) -> Dict:
        """获取模型配置（每次都从数据库获取最新配置）"""
        try:
            # 每次都重新获取，确保使用最新的激活模型
            self._config = get_active_llm_config()
        except Exception as e:
            logger.error(f"[LLMClient] 获取数据库模型配置失败: {e}")
            raise
        return self._config
    
    def _ensure_provider(self):
        """确保 Provider 已初始化（检查配置变化）"""
        current_config = self._get_config()
        
        # 检查配置是否变化
        config_changed = (
            self._provider is None or
            self._config is None or
            current_config.get('id') != self._config.get('id') or
            current_config.get('model_name') != self._config.get('model_name')
        )
        
        if config_changed:
            logger.info(f"[LLMClient] 配置变化，重新初始化 Provider: {current_config.get('model_name')}")
            self._config = current_config
            self._provider = create_llm_provider(
                provider=current_config['provider'],
                model_name=current_config['model_name'],
                api_key=current_config['api_key'],
                base_url=current_config['base_url'],
                temperature=current_config.get('temperature', 0.0),
                max_tokens=current_config.get('max_tokens', 128000),
            )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4000,
        response_format: Optional[Dict[str, str]] = None,
        source: str = "chat",
        session_id: int = None,
    ) -> str:
        """
        发送聊天请求（带自动切换和详细 Token 统计）
        """
        import time as _time
        self._ensure_provider()
        start_ms = int(_time.time() * 1000)
        
        try:
            response = self._provider.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format
            )
            
            duration_ms = int(_time.time() * 1000) - start_ms

            # 更新 token 使用量（增强版）
            model_config_manager.increment_token_usage(
                tokens=response.total_tokens,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                source=source,
                session_id=session_id,
                success=True,
                duration_ms=duration_ms,
            )
            
            return response.content
            
        except Exception as e:
            duration_ms = int(_time.time() * 1000) - start_ms
            logger.error(f"[LLMClient] 请求失败: {e}")

            # 记录失败的 token 使用
            model_config_manager.increment_token_usage(
                tokens=0,
                prompt_tokens=0,
                completion_tokens=0,
                source=source,
                session_id=session_id,
                success=False,
                error_type=str(type(e).__name__),
                duration_ms=duration_ms,
            )

            # 尝试自动切换
            try:
                from llm.auto_switch import get_auto_switcher, classify_failure_reason
                switcher = get_auto_switcher()
                # 确保 profiles 已加载（首次调用时可能为空）
                if not switcher._profiles:
                    switcher.load_profiles_from_db()
                if switcher.enabled and self._config and len(switcher._profiles) > 1:
                    reason = classify_failure_reason(e)
                    current_id = self._config.get('id', 0)
                    new_id = switcher.mark_failure(current_id, reason)
                    if new_id and new_id != current_id:
                        logger.info(f"[LLMClient] 🔄 自动切换: ID={current_id} → ID={new_id}，重试请求")
                        self.refresh()
                        self._ensure_provider()
                        response = self._provider.chat(
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            response_format=response_format
                        )
                        retry_duration = int(_time.time() * 1000) - start_ms
                        model_config_manager.increment_token_usage(
                            tokens=response.total_tokens,
                            prompt_tokens=response.prompt_tokens,
                            completion_tokens=response.completion_tokens,
                            source=source,
                            session_id=session_id,
                            success=True,
                            duration_ms=retry_duration,
                        )
                        return response.content
                    else:
                        logger.warning(f"[LLMClient] ❌ 没有可用的备选模型 (current={current_id}, new={new_id})")
            except Exception as retry_err:
                logger.error(f"[LLMClient] 自动切换重试也失败: {retry_err}")

            raise
    
    async def achat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4000,
        response_format: Optional[Dict[str, str]] = None,
        source: str = "chat",
        session_id: int = None,
    ) -> str:
        """异步聊天请求（带自动切换和详细 Token 统计）"""
        import time as _time
        self._ensure_provider()
        start_ms = int(_time.time() * 1000)
        
        try:
            response = await self._provider.achat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format
            )
            
            duration_ms = int(_time.time() * 1000) - start_ms

            model_config_manager.increment_token_usage(
                tokens=response.total_tokens,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                source=source,
                session_id=session_id,
                success=True,
                duration_ms=duration_ms,
            )
            
            return response.content
            
        except Exception as e:
            duration_ms = int(_time.time() * 1000) - start_ms
            logger.error(f"[LLMClient] 异步请求失败: {e}")

            model_config_manager.increment_token_usage(
                tokens=0, prompt_tokens=0, completion_tokens=0,
                source=source, session_id=session_id,
                success=False, error_type=str(type(e).__name__),
                duration_ms=duration_ms,
            )

            # 尝试自动切换
            try:
                from llm.auto_switch import get_auto_switcher, classify_failure_reason
                switcher = get_auto_switcher()
                # 确保 profiles 已加载（首次调用时可能为空）
                if not switcher._profiles:
                    switcher.load_profiles_from_db()
                if switcher.enabled and self._config and len(switcher._profiles) > 1:
                    reason = classify_failure_reason(e)
                    current_id = self._config.get('id', 0)
                    new_id = switcher.mark_failure(current_id, reason)
                    if new_id and new_id != current_id:
                        logger.info(f"[LLMClient] 🔄 自动切换: ID={current_id} → ID={new_id}，重试异步请求")
                        self.refresh()
                        self._ensure_provider()
                        response = await self._provider.achat(
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            response_format=response_format
                        )
                        retry_duration = int(_time.time() * 1000) - start_ms
                        model_config_manager.increment_token_usage(
                            tokens=response.total_tokens,
                            prompt_tokens=response.prompt_tokens,
                            completion_tokens=response.completion_tokens,
                            source=source, session_id=session_id,
                            success=True, duration_ms=retry_duration,
                        )
                        return response.content
                    else:
                        logger.warning(f"[LLMClient] ❌ 没有可用的备选模型 (current={current_id}, new={new_id})")
            except Exception as retry_err:
                logger.error(f"[LLMClient] 自动切换重试也失败: {retry_err}")

            raise
    
    def chat_with_tools(
        self,
        messages: List[Dict],
        tools: List[Dict],
        temperature: float = 0.3,
        max_tokens: int = 4096,
        source: str = "tool_calling",
        provider_type: str = None,
    ):
        """
        使用 native function calling 调用 LLM，返回完整 LLMResponse（含 tool_calls）。

        Args:
            messages: 消息列表（provider-agnostic 格式）
            tools: 工具定义列表（会自动根据 provider 转换格式）
            temperature: 温度
            max_tokens: 最大输出 token
            source: 来源标识
            provider_type: 强制指定 provider 类型（'openai' | 'anthropic'）

        Returns:
            LLMResponse，包含 content 和 tool_calls
        """
        import time as _time
        from .base import LLMResponse, ToolCallData

        self._ensure_provider()
        start_ms = int(_time.time() * 1000)

        # 检测 provider 类型
        if provider_type is None:
            provider_type = self._detect_provider_type()

        # 转换工具定义和消息为 provider 专用格式
        api_tools = self._convert_tools_for_provider(tools, provider_type)
        api_messages = self._convert_messages_for_provider(messages, provider_type)

        try:
            response = self._provider.chat(
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=api_tools,
            )

            duration_ms = int(_time.time() * 1000) - start_ms
            model_config_manager.increment_token_usage(
                tokens=response.total_tokens,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                source=source,
                success=True,
                duration_ms=duration_ms,
            )

            logger.debug(
                f"[LLMClient] chat_with_tools: finish={response.finish_reason}, "
                f"tool_calls={len(response.tool_calls)}, content_len={len(response.content)}"
            )
            return response

        except Exception as e:
            duration_ms = int(_time.time() * 1000) - start_ms
            logger.error(f"[LLMClient] chat_with_tools 失败: {e}")
            model_config_manager.increment_token_usage(
                tokens=0, prompt_tokens=0, completion_tokens=0,
                source=source, success=False,
                error_type=str(type(e).__name__),
                duration_ms=duration_ms,
            )
            raise

    def _detect_provider_type(self) -> str:
        """检测当前 provider 是 OpenAI 兼容还是 Anthropic"""
        from .base import ProviderType
        pt = getattr(self._provider, 'provider_type', None)
        if pt == ProviderType.ANTHROPIC:
            return "anthropic"
        return "openai"

    @staticmethod
    def _convert_tools_for_provider(tools: List[Dict], provider_type: str) -> List[Dict]:
        """将通用工具定义转换为 provider 的 API 格式"""
        if provider_type == "anthropic":
            # Anthropic 格式: {name, description, input_schema}
            converted = []
            for t in tools:
                if "input_schema" in t:
                    converted.append(t)  # 已经是 Anthropic 格式
                elif "function" in t:
                    # 从 OpenAI 格式转换
                    func = t["function"]
                    converted.append({
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                    })
                else:
                    # 原始格式 {name, description, parameters}
                    converted.append({
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                    })
            return converted
        else:
            # OpenAI 格式: {type: "function", function: {name, description, parameters}}
            converted = []
            for t in tools:
                if "type" in t and t["type"] == "function":
                    converted.append(t)  # 已经是 OpenAI 格式
                elif "input_schema" in t:
                    # 从 Anthropic 格式转换
                    converted.append({
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t["input_schema"],
                        }
                    })
                else:
                    # 原始格式 {name, description, parameters}
                    converted.append({
                        "type": "function",
                        "function": {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                        }
                    })
            return converted

    @staticmethod
    def _convert_messages_for_provider(messages: List[Dict], provider_type: str) -> List[Dict]:
        """
        将 provider-agnostic 消息格式转换为 provider 专用格式。

        Explorer 使用统一格式:
          - {"role": "assistant", "content": "...", "tool_calls": [...]}
          - {"role": "tool_result", "tool_call_id": "...", "name": "...", "content": "..."}
        """
        if provider_type == "anthropic":
            return LLMClient._messages_to_anthropic(messages)
        else:
            return LLMClient._messages_to_openai(messages)

    @staticmethod
    def _messages_to_openai(messages: List[Dict]) -> List[Dict]:
        """转换为 OpenAI 消息格式"""
        result = []
        for msg in messages:
            role = msg.get("role", "user")

            if role == "tool_result":
                # 转换为 OpenAI 的 tool role
                result.append({
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg.get("content", ""),
                })
            elif role == "assistant" and "tool_calls" in msg and msg["tool_calls"]:
                # assistant 消息带 tool_calls
                openai_tool_calls = []
                for tc in msg["tool_calls"]:
                    openai_tool_calls.append({
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False) if isinstance(tc["arguments"], dict) else tc["arguments"],
                        }
                    })
                result.append({
                    "role": "assistant",
                    "content": msg.get("content", "") or None,
                    "tool_calls": openai_tool_calls,
                })
            else:
                result.append(msg)
        return result

    @staticmethod
    def _messages_to_anthropic(messages: List[Dict]) -> List[Dict]:
        """转换为 Anthropic 消息格式"""
        result = []
        pending_tool_results = []

        for msg in messages:
            role = msg.get("role", "user")

            if role == "system":
                # system 在 Anthropic 中是单独参数，保留在消息中让 provider 处理
                result.append(msg)
            elif role == "tool_result":
                # 收集 tool results，合并到一个 user 消息中
                pending_tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": msg["tool_call_id"],
                    "content": msg.get("content", ""),
                })
            elif role == "assistant" and "tool_calls" in msg and msg["tool_calls"]:
                # 先 flush pending tool results
                if pending_tool_results:
                    result.append({"role": "user", "content": pending_tool_results})
                    pending_tool_results = []

                # assistant 消息带 tool_use 内容块
                content_blocks = []
                text = msg.get("content", "")
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for tc in msg["tool_calls"]:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc["arguments"] if isinstance(tc["arguments"], dict) else {},
                    })
                result.append({"role": "assistant", "content": content_blocks})
            else:
                # 先 flush pending tool results
                if pending_tool_results:
                    result.append({"role": "user", "content": pending_tool_results})
                    pending_tool_results = []
                result.append(msg)

        # flush remaining tool results
        if pending_tool_results:
            result.append({"role": "user", "content": pending_tool_results})

        return result

    def generate_test_cases(
        self,
        requirement: str,
        count: int = 3,
        priority: str = "3",
        db=None,
    ) -> Dict[str, Any]:
        """
        根据需求生成测试用例。
        若传入 db，则从数据库读取当前激活的用例模板注入 prompt，实现自适应字段结构。

        Args:
            requirement: 用户需求或测试点
            count: 生成数量
            priority: 默认优先级
            db: SQLAlchemy Session（可选，传入后启用模板自适应）

        Returns:
            生成的测试用例数据
        """
        # ── 读取模板配置 ──────────────────────────────────────────
        template_info = None
        if db is not None:
            try:
                from Project_manage.case_template.service import CaseTemplateService
                template_info = CaseTemplateService.get_template_for_llm(db)
            except Exception as e:
                logger.warning(f"[LLMClient] 读取用例模板失败，使用默认字段: {e}")

        if template_info:
            fields = template_info.get("fields", [])
            field_keys = [f["key"] for f in fields]
            field_desc = "\n".join(
                f'  - {f["key"]}（{f["label"]}）{"【必填】" if f.get("required") else "【可选】"}'
                for f in fields
            )
            priority_opts = template_info.get("priority_options") or []
            case_type_opts = template_info.get("case_type_options") or []
            stage_opts = template_info.get("stage_options") or []
            extra_prompt = template_info.get("extra_prompt") or ""
            source = template_info.get("source_platform") or "系统默认"

            priority_hint = ""
            if priority_opts:
                opts_str = "、".join(f'{o["value"]}({o["label"]})' for o in priority_opts)
                priority_hint = f"\n- priority 字段的合法值为：{opts_str}"
            case_type_hint = ""
            if case_type_opts:
                opts_str = "、".join(o["value"] for o in case_type_opts)
                case_type_hint = f"\n- case_type 字段的合法值为：{opts_str}"
            stage_hint = ""
            if stage_opts:
                opts_str = "、".join(o["value"] for o in stage_opts)
                stage_hint = f"\n- stage 字段的合法值为：{opts_str}"

            template_section = f"""
【用例模板来源：{source}】
每条用例必须包含以下字段：
{field_desc}
{priority_hint}{case_type_hint}{stage_hint}
{extra_prompt}
""".strip()
        else:
            field_keys = ["module", "title", "precondition", "steps", "expected",
                          "keywords", "priority", "case_type", "stage"]
            template_section = "每条用例包含字段：module, title, precondition, steps, expected, keywords, priority, case_type, stage"

        # ── 构建 example JSON ─────────────────────────────────────
        example_fields = {k: f"<{k}>" for k in field_keys}
        example_fields["steps"] = ["步骤1", "步骤2"]
        example_json = json.dumps({"test_cases": [example_fields]}, ensure_ascii=False, indent=4)

        system_prompt = """你是一个专业的软件测试工程师，擅长编写测试用例。
请根据用户提供的需求，生成结构化的测试用例。
输出格式要求：必须返回有效的 JSON 对象，包含 test_cases 数组。"""

        user_prompt = f"""请根据以下需求生成测试用例：

需求：{requirement}

{template_section}

以 JSON 格式返回，示例：
{example_json}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = self.chat(
                messages=messages,
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            result = json.loads(response)
            return {"success": True, **result}
        except Exception as e:
            return {"success": False, "message": str(e), "test_cases": []}
    
    def generate_test_report(
        self,
        test_results: List[Dict[str, Any]],
        summary: Dict[str, Any],
        format_type: str = "markdown"
    ) -> Dict[str, Any]:
        """
        生成测试报告
        
        Args:
            test_results: 测试结果列表
            summary: 测试统计摘要
            format_type: 报告格式
        
        Returns:
            生成的报告
        """
        total_cases = summary.get('total', len(test_results))
        is_single_case = total_cases == 1
        
        test_results_json = json.dumps(test_results, ensure_ascii=False, indent=2)
        
        system_prompt = """你是一个专业的测试报告生成专家。
请根据测试结果生成清晰、专业的测试报告。"""
        
        if is_single_case:
            user_prompt = f"""请根据以下测试结果生成测试报告:

测试结果:
{test_results_json}

请生成 {format_type.upper()} 格式的测试报告。"""
        else:
            user_prompt = f"""请根据以下批量测试结果生成测试报告:

测试统计:
- 总用例数: {summary.get('total', 0)}
- 通过数: {summary.get('pass', 0)}
- 失败数: {summary.get('fail', 0)}
- 执行耗时: {summary.get('duration', 0)} 秒

测试结果详情:
{test_results_json}

请生成 {format_type.upper()} 格式的测试报告。"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        try:
            response = self.chat(
                messages=messages,
                temperature=0.3
            )
            
            return {
                "success": True,
                "content": response
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "content": ""
            }
    
    def analyze_bug(self, analysis_prompt: str) -> Dict[str, Any]:
        """
        分析 Bug
        
        Args:
            analysis_prompt: Bug 分析提示
        
        Returns:
            Bug 分析结果
        """
        system_prompt = """你是一个专业的 Bug 分析专家。
请分析测试失败的原因，并返回结构化的 Bug 报告。

返回 JSON 格式:
{
    "error_type": "错误类型（功能错误/设计缺陷/安全问题/系统错误）",
    "severity_level": "严重程度（一级/二级/三级/四级）",
    "actual_result": "实际结果描述",
    "result_feedback": "问题分析和建议"
}"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": analysis_prompt}
        ]
        
        try:
            response = self.chat(
                messages=messages,
                temperature=0.1,
                max_tokens=2000
            )
            
            # 清理 markdown 代码块
            cleaned_response = response.strip()
            if cleaned_response.startswith('```json'):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.startswith('```'):
                cleaned_response = cleaned_response[3:]
            if cleaned_response.endswith('```'):
                cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()
            
            bug_data = json.loads(cleaned_response)
            
            return {
                "success": True,
                "data": bug_data
            }
            
        except json.JSONDecodeError as e:
            logger.warning(f"[LLMClient] JSON 解析失败: {e}")
            return {
                "success": True,
                "data": {
                    "error_type": "系统错误",
                    "severity_level": "一级",
                    "actual_result": response if 'response' in dir() else "响应格式异常",
                    "result_feedback": "LLM 返回格式异常，无法解析"
                }
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "data": {}
            }
    
    def refresh(self):
        """刷新配置和 Provider"""
        self._config = None
        self._provider = None
        model_config_manager.refresh_config()

    def parse_json_response(self, content: str) -> dict:
        """
        使用当前 Provider 的 JSON 解析器解析 LLM 响应

        自动根据当前活跃的模型 Provider 选择最合适的解析策略

        Args:
            content: LLM 原始响应文本

        Returns:
            解析后的 dict
        """
        self._ensure_provider()
        return self._provider.parse_json_response(content)


# 全局实例
_llm_client_instance: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端实例（单例）"""
    global _llm_client_instance
    if _llm_client_instance is None:
        _llm_client_instance = LLMClient()
    return _llm_client_instance
