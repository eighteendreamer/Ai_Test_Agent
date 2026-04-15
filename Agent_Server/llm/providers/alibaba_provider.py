"""
阿里云/通义千问 Provider 实现

支持 Qwen 系列模型，兼容 OpenAI API

已知问题（Qwen3.5 等大参数模型）：
- browser-use 的结构化输出 schema 不被可靠支持
- 模型可能返回 {"thinking": "..."} 而非 {"action": [...]} 格式
- 需要 dont_force_structured_output=True + add_schema_to_system_prompt=True

作者: 程序员Eighteen
"""
import json
import os
import re
import logging
from typing import Any, Dict, List

from ..base import BaseOpenAICompatibleProvider, LLMConfig, LLMResponse, ProviderType
from ..config import PROVIDER_DEFAULT_ENDPOINTS, get_api_key_env_var

logger = logging.getLogger(__name__)


class AlibabaProvider(BaseOpenAICompatibleProvider):
    """
    阿里云/通义千问 Provider
    
    支持 Qwen 系列模型，通过 OpenAI 兼容 API
    """
    
    @property
    def provider_name(self) -> str:
        return "Alibaba"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.ALIBABA
    
    def __init__(self, config: LLMConfig):
        # 设置默认 base_url
        if not config.base_url:
            config.base_url = os.getenv(
                "ALIBABA_ENDPOINT",
                PROVIDER_DEFAULT_ENDPOINTS.get("alibaba", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            )
        
        # 从环境变量获取 API Key
        if not config.api_key:
            env_var = get_api_key_env_var("alibaba")
            config.api_key = os.getenv(env_var, "")
        
        # 大参数模型（如 Qwen3.5-397B）响应较慢，确保足够的超时
        if '397b' in config.model_name.lower() or '235b' in config.model_name.lower():
            config.timeout = max(config.timeout, 240)
        
        super().__init__(config)
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """
        Alibaba/Qwen 聊天

        Qwen 模型通过 DashScope 兼容 API 支持 response_format=json_object，
        但大参数模型（如 Qwen3.5-397B）在复杂 schema 下不稳定，
        这里保留 response_format 传递（用于简单 JSON 请求），
        browser-use 场景通过 dont_force_structured_output 单独处理。
        """
        return super().chat(messages, temperature, max_tokens, **kwargs)
    
    def get_langchain_llm(self) -> Any:
        """获取 LangChain LLM 实例"""
        from langchain_openai import ChatOpenAI
        
        return ChatOpenAI(
            model=self.config.model_name,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
    
    def get_browser_use_llm(self) -> Any:
        """
        获取 Browser-Use LLM 实例

        重要：使用 langchain_openai.ChatOpenAI 而非 browser_use.llm.openai.chat.ChatOpenAI。
        
        原因：browser-use 0.11.1 的 ChatOpenAI.ainvoke() 内部调用
        OpenAIMessageSerializer.serialize_messages()，该序列化器只接受
        browser-use 自定义消息类型（browser_use.llm.messages.UserMessage 等），
        不认识 LangChain 消息类型（langchain_core.messages.HumanMessage 等），
        会抛出 ValueError('Unknown message type: ...')。

        而 LLMWrapper 拦截 ainvoke 后会将消息转为 LangChain 类型再传给底层 LLM，
        这与 BrowserUseChatOpenAI 的期望冲突。

        解决方案（参考 web-ui-3.0.0 的做法）：
        - 使用 langchain_openai.ChatOpenAI（接受 LangChain 消息类型）
        - 通过 LLMWrapper 处理 output_format 和 action 格式修正
        """
        from ..wrapper import wrap_llm
        from langchain_openai import ChatOpenAI

        # Qwen3.5-397B 等大参数模型响应较慢，使用更长的超时
        timeout = max(self.config.timeout, 120)

        raw_llm = ChatOpenAI(
            model=self.config.model_name,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            request_timeout=timeout,
        )

        # Qwen 系列特有的 action 别名
        # Qwen3.5 等模型可能返回 click_element, input_text 等非标准名称
        qwen_aliases = {
            'click_element': 'click',
            'input_text': 'input',
            'type_text': 'input',
            'scroll_down': 'scroll',
            'scroll_up': 'scroll',
            'extract_content': 'extract',
            'go_to_url': 'navigate',
            'evaluate': 'run_javascript',
            'execute_js': 'run_javascript',
        }

        return wrap_llm(raw_llm, action_aliases=qwen_aliases)
    
    def supports_structured_output(self) -> bool:
        """Qwen 模型对 browser-use 的复杂 schema 支持不稳定"""
        return False

    def parse_json_response(self, content: str) -> dict:
        """
        Alibaba/Qwen JSON 解析

        Qwen 模型的常见问题：
        - Qwen3.5 等大参数模型可能返回 {"thinking": "...", "action": [...]}
          其中 thinking 字段不属于目标 schema
        - 可能在 JSON 前后添加中文解释文字
        - 偶尔有 markdown 代码块包裹
        - 可能返回 ```json\n{...}\n``` 格式
        - <think> 内容可能嵌在 JSON 字段值内
        """
        if not content:
            raise ValueError("LLM 响应为空")

        text = content.strip()

        # 1. 剥离所有 <think>...</think>（包括嵌在 JSON 字段值内的）
        text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()

        # 2. 剥离 markdown 代码块
        text = self._extract_json(text)

        # 3. 如果不是以 { 开头，找到第一个 {
        if text and text[0] != '{':
            idx = text.find('{')
            if idx >= 0:
                text = text[idx:]

        # 4. 用括号匹配提取完整 JSON 对象
        from ..base import _find_matching_brace
        if text and text.startswith('{'):
            end_idx = _find_matching_brace(text)
            if end_idx > 0:
                extracted = text[:end_idx + 1]
                if end_idx < len(text) - 1:
                    logger.debug(
                        f"[Alibaba] 括号匹配截断: "
                        f"原始 {len(text)} → 提取 {len(extracted)} 字符"
                    )
                text = extracted

        # 5. 直接尝试解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 6. 移除尾部逗号
        fixed = re.sub(r',\s*([}\]])', r'\1', text)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 7. 使用 json_repair 库修复
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                logger.info(f"[Alibaba] json_repair 成功修复 JSON ({len(text)} 字符)")
                return repaired
        except Exception as e:
            logger.debug(f"[Alibaba] json_repair 失败: {e}")

        # 8. 回退到基类通用解析（含截断修复等）
        return super().parse_json_response(content)
