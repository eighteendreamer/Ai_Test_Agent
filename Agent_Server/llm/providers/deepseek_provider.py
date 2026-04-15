"""
DeepSeek Provider 实现

支持 DeepSeek Chat 和 DeepSeek Reasoner（R1）模型
特别处理 R1 模型的 reasoning_content 输出

作者: 程序员Eighteen
"""
import json
import os
import re
import logging
from typing import Any, Dict, List

from langchain_core.language_models.base import LanguageModelInput
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage

from ..base import BaseOpenAICompatibleProvider, LLMConfig, LLMResponse, ProviderType
from ..config import PROVIDER_DEFAULT_ENDPOINTS, get_api_key_env_var, is_reasoning_model

logger = logging.getLogger(__name__)


class DeepSeekR1ChatOpenAI:
    """
    DeepSeek R1 专用的 LangChain 兼容实现
    
    处理 DeepSeek Reasoner 的 reasoning_content 输出
    """
    
    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        temperature: float = 0.0,
        **kwargs
    ):
        from openai import OpenAI
        
        self.model_name = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key
        )
        self.temperature = temperature
        
        # LangChain 兼容属性
        self.model = model
    
    def _convert_messages(self, input_messages) -> List[Dict]:
        """转换 LangChain 消息格式为 OpenAI 格式"""
        message_history = []
        for msg in input_messages:
            if isinstance(msg, SystemMessage):
                message_history.append({"role": "system", "content": msg.content})
            elif isinstance(msg, AIMessage):
                message_history.append({"role": "assistant", "content": msg.content})
            else:
                message_history.append({"role": "user", "content": msg.content})
        return message_history
    
    async def ainvoke(
        self,
        input: LanguageModelInput,
        config: RunnableConfig = None,
        **kwargs
    ) -> AIMessage:
        """异步调用"""
        message_history = self._convert_messages(input)
        
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=message_history,
            temperature=self.temperature
        )
        
        # 提取 reasoning_content 和 content
        message = response.choices[0].message
        reasoning_content = getattr(message, 'reasoning_content', '') or ''
        content = message.content or ''
        
        return AIMessage(
            content=content,
            additional_kwargs={"reasoning_content": reasoning_content}
        )
    
    def invoke(
        self,
        input: LanguageModelInput,
        config: RunnableConfig = None,
        **kwargs
    ) -> AIMessage:
        """同步调用"""
        message_history = self._convert_messages(input)
        
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=message_history,
            temperature=self.temperature
        )
        
        message = response.choices[0].message
        reasoning_content = getattr(message, 'reasoning_content', '') or ''
        content = message.content or ''
        
        return AIMessage(
            content=content,
            additional_kwargs={"reasoning_content": reasoning_content}
        )


class DeepSeekProvider(BaseOpenAICompatibleProvider):
    """
    DeepSeek Provider
    
    支持:
    - deepseek-chat: 通用对话模型
    - deepseek-coder: 代码专用模型
    - deepseek-reasoner: R1 推理模型（特殊处理）
    """
    
    @property
    def provider_name(self) -> str:
        return "DeepSeek"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.DEEPSEEK
    
    def __init__(self, config: LLMConfig):
        # 设置默认 base_url
        if not config.base_url:
            config.base_url = os.getenv(
                "DEEPSEEK_ENDPOINT",
                PROVIDER_DEFAULT_ENDPOINTS.get("deepseek", "https://api.deepseek.com/v1")
            )
        
        # 从环境变量获取 API Key
        if not config.api_key:
            env_var = get_api_key_env_var("deepseek")
            config.api_key = os.getenv(env_var, "")
        
        super().__init__(config)
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """DeepSeek 聊天，特殊处理 R1 模型"""
        self.ensure_initialized()
        
        request_params = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        
        # DeepSeek 不支持结构化输出，移除 response_format
        kwargs.pop('response_format', None)
        request_params.update(kwargs)
        
        try:
            response = self._client.chat.completions.create(**request_params)
            
            message = response.choices[0].message
            content = message.content or ""
            
            # R1 模型特殊处理：提取 reasoning_content
            reasoning_content = ""
            if hasattr(message, 'reasoning_content') and message.reasoning_content:
                reasoning_content = message.reasoning_content
            elif self.is_reasoning_model():
                # 尝试从内容中解析
                reasoning_content, content = self._parse_response_content(content)
            
            usage = response.usage
            
            return LLMResponse(
                content=content,
                reasoning_content=reasoning_content,
                model=response.model,
                finish_reason=response.choices[0].finish_reason,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[DeepSeek] 聊天请求失败: {e}")
            raise
    
    def get_langchain_llm(self) -> Any:
        """获取 LangChain LLM 实例"""
        # R1 模型使用特殊实现
        if self.is_reasoning_model():
            return DeepSeekR1ChatOpenAI(
                model=self.config.model_name,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                temperature=self.config.temperature,
            )
        
        # 普通模型使用标准 ChatOpenAI
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

        使用 langchain_openai.ChatOpenAI + LLMWrapper，而非 BrowserUseChatOpenAI。
        原因：BrowserUseChatOpenAI 的序列化器不接受 LangChain 消息类型，
        而 LLMWrapper 会将消息转为 LangChain 类型，两者冲突。
        """
        from ..wrapper import wrap_llm
        from langchain_openai import ChatOpenAI

        timeout = max(self.config.timeout, 120)

        raw_llm = ChatOpenAI(
            model=self.config.model_name,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            request_timeout=timeout,
        )

        # DeepSeek 特有的 action 别名
        # deepseek-chat 倾向于返回 extract_content, scroll_down, evaluate 等非标准名称
        deepseek_aliases = {
            'extract_content': 'extract',
            'scroll_down': 'scroll',
            'scroll_up': 'scroll',
            'evaluate': 'run_javascript',
            'execute_js': 'run_javascript',
            'click_element': 'click',
            'input_text': 'input',
            'go_to_url': 'navigate',
        }

        return wrap_llm(raw_llm, action_aliases=deepseek_aliases)
    
    def supports_structured_output(self) -> bool:
        """DeepSeek 不支持结构化输出"""
        return False

    def parse_json_response(self, content: str) -> dict:
        """
        DeepSeek JSON 解析

        DeepSeek 的特点：
        - 不支持 response_format=json_object，必须从自由文本中提取 JSON
        - R1 (reasoner) 模型有 reasoning_content 字段，但 chat() 返回的 content
          可能仍包含 <think>...</think> 标签（取决于 API 版本）
        - deepseek-chat 通常比较规范，但偶尔有 markdown 包裹
        - 可能在 JSON 后面追加解释文字
        - 可能缺少逗号分隔符或有多余逗号
        - <think> 内容可能嵌在 JSON 字段值内（如 "thinking": "<think>..."）
        """
        if not content:
            raise ValueError("LLM 响应为空")

        text = content.strip()

        # 1. 剥离所有 <think>...</think>（包括嵌在 JSON 字段值内的）
        text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()

        # 2. 剥离 **JSON Response:** 格式
        if "**JSON Response:**" in text:
            text = text.split("**JSON Response:**")[-1].strip()

        # 3. 剥离 markdown 代码块
        text = self._extract_json(text)

        # 4. 如果不是以 { 开头，找到第一个 {
        if text and text[0] != '{':
            idx = text.find('{')
            if idx >= 0:
                text = text[idx:]

        # 5. 用括号匹配提取完整 JSON 对象
        from ..base import _find_matching_brace
        if text and text.startswith('{'):
            end_idx = _find_matching_brace(text)
            if end_idx > 0:
                extracted = text[:end_idx + 1]
                if end_idx < len(text) - 1:
                    logger.debug(
                        f"[DeepSeek] 括号匹配截断: "
                        f"原始 {len(text)} → 提取 {len(extracted)} 字符"
                    )
                text = extracted

        # 6. 直接尝试
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 7. 修复常见 JSON 格式问题
        fixed = self._fix_common_json_issues(text)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 8. 使用 json_repair 库修复
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                logger.info(f"[DeepSeek] json_repair 成功修复 JSON ({len(text)} 字符)")
                return repaired
        except Exception as e:
            logger.debug(f"[DeepSeek] json_repair 失败: {e}")

        # 9. 回退到基类通用解析
        return super().parse_json_response(content)

    @staticmethod
    def _fix_common_json_issues(text: str) -> str:
        """修复 DeepSeek 常见的 JSON 格式问题"""
        # 移除尾部逗号: ,} 或 ,]
        fixed = re.sub(r',\s*([}\]])', r'\1', text)
        # 修复缺少逗号: "value"\n"key" → "value",\n"key"
        fixed = re.sub(r'"\s*\n(\s*")', r'",\n\1', fixed)
        # 修复缺少逗号: }\n{ 或 ]\n{
        fixed = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', fixed)
        # 修复缺少逗号: true/false/null/数字 后面直接跟 "key"
        fixed = re.sub(r'(true|false|null|\d+)\s*\n(\s*")', r'\1,\n\2', fixed)
        return fixed
