"""
Anthropic Provider 实现

支持 Claude 系列模型

作者: 程序员Eighteen
"""
import json
import os
import re
import logging
from typing import Any, Dict, List

from ..base import BaseLLMProvider, LLMConfig, LLMResponse, ProviderType, ToolCallData
from ..config import PROVIDER_DEFAULT_ENDPOINTS, get_api_key_env_var

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Provider
    
    支持 Claude 3 系列模型
    """
    
    @property
    def provider_name(self) -> str:
        return "Anthropic"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.ANTHROPIC
    
    def __init__(self, config: LLMConfig):
        # 设置默认 base_url
        if not config.base_url:
            config.base_url = os.getenv(
                "ANTHROPIC_ENDPOINT",
                PROVIDER_DEFAULT_ENDPOINTS.get("anthropic", "https://api.anthropic.com")
            )
        
        # 从环境变量获取 API Key
        if not config.api_key:
            env_var = get_api_key_env_var("anthropic")
            config.api_key = os.getenv(env_var, "")
        
        super().__init__(config)
    
    def _initialize_client(self) -> Any:
        """初始化 Anthropic 客户端"""
        try:
            from anthropic import Anthropic
            return Anthropic(
                api_key=self.config.api_key,
                base_url=self.config.base_url
            )
        except ImportError:
            logger.error("[Anthropic] anthropic 库未安装，请运行: pip install anthropic")
            raise
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """Anthropic 聊天（支持 native function calling）"""
        self.ensure_initialized()
        
        # 分离系统消息和其他消息
        system_message = ""
        chat_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                chat_messages.append(msg)
        
        # 构建 API 参数
        api_params = {
            "model": self.config.model_name,
            "max_tokens": max_tokens or self.config.max_tokens,
            "messages": chat_messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if system_message:
            api_params["system"] = system_message

        # 支持 native function calling：传入 tools 参数
        tools = kwargs.get("tools")
        if tools:
            api_params["tools"] = tools

        try:
            response = self._client.messages.create(**api_params)
            
            # 提取文本内容和工具调用
            content = ""
            tool_calls_data = []

            if response.content:
                for block in response.content:
                    if block.type == "text":
                        content += block.text
                    elif block.type == "tool_use":
                        tool_calls_data.append(ToolCallData(
                            id=block.id,
                            name=block.name,
                            arguments=block.input if isinstance(block.input, dict) else {},
                        ))
            
            return LLMResponse(
                content=content,
                model=response.model,
                finish_reason=response.stop_reason or "",
                tool_calls=tool_calls_data,
                prompt_tokens=response.usage.input_tokens if response.usage else 0,
                completion_tokens=response.usage.output_tokens if response.usage else 0,
                total_tokens=(response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Anthropic] 聊天请求失败: {e}")
            raise
    
    async def achat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """异步聊天（支持 native function calling）"""
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            logger.error("[Anthropic] anthropic 库未安装")
            raise
        
        async_client = AsyncAnthropic(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )
        
        system_message = ""
        chat_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                chat_messages.append(msg)
        
        # 构建 API 参数
        api_params = {
            "model": self.config.model_name,
            "max_tokens": max_tokens or self.config.max_tokens,
            "messages": chat_messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if system_message:
            api_params["system"] = system_message

        tools = kwargs.get("tools")
        if tools:
            api_params["tools"] = tools

        try:
            response = await async_client.messages.create(**api_params)
            
            content = ""
            tool_calls_data = []

            if response.content:
                for block in response.content:
                    if block.type == "text":
                        content += block.text
                    elif block.type == "tool_use":
                        tool_calls_data.append(ToolCallData(
                            id=block.id,
                            name=block.name,
                            arguments=block.input if isinstance(block.input, dict) else {},
                        ))
            
            return LLMResponse(
                content=content,
                model=response.model,
                finish_reason=response.stop_reason or "",
                tool_calls=tool_calls_data,
                prompt_tokens=response.usage.input_tokens if response.usage else 0,
                completion_tokens=response.usage.output_tokens if response.usage else 0,
                total_tokens=(response.usage.input_tokens + response.usage.output_tokens) if response.usage else 0,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Anthropic] 异步聊天请求失败: {e}")
            raise
    
    def get_langchain_llm(self) -> Any:
        """获取 LangChain LLM 实例"""
        from langchain_anthropic import ChatAnthropic
        
        return ChatAnthropic(
            model=self.config.model_name,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
        )
    
    def get_browser_use_llm(self) -> Any:
        """获取 Browser-Use LLM 实例"""
        # Browser-Use 使用 LangChain 的 Anthropic
        return self.get_langchain_llm()

    def parse_json_response(self, content: str) -> dict:
        """
        Anthropic (Claude) JSON 解析

        Claude 的特点：
        - 经常在 JSON 前后添加解释性文字（如 "Here is the JSON:" 或 "以下是结果："）
        - 有时用 markdown 代码块包裹
        - JSON 本身通常格式规范，很少有尾部逗号等问题
        """
        if not content:
            raise ValueError("LLM 响应为空")

        text = content.strip()

        # 1. 剥离 markdown 代码块
        text = self._extract_json(text)

        # 2. 直接尝试
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 3. Claude 喜欢在 JSON 前后加文字，提取第一个完整 JSON 对象
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # 4. 使用 json_repair 库修复
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                logger.info(f"[Anthropic] json_repair 成功修复 JSON ({len(text)} 字符)")
                return repaired
        except Exception as e:
            logger.debug(f"[Anthropic] json_repair 失败: {e}")

        # 5. 回退到基类通用解析
        return super().parse_json_response(content)
