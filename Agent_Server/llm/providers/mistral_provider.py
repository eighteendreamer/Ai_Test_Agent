"""
Mistral AI Provider 实现

支持 Mistral 系列模型

作者: 程序员Eighteen
"""
import os
import logging
from typing import Any, Dict, List

from ..base import BaseLLMProvider, LLMConfig, LLMResponse, ProviderType
from ..config import PROVIDER_DEFAULT_ENDPOINTS, get_api_key_env_var

logger = logging.getLogger(__name__)


class MistralProvider(BaseLLMProvider):
    """
    Mistral AI Provider
    
    支持 Mistral 系列模型
    """
    
    @property
    def provider_name(self) -> str:
        return "Mistral"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MISTRAL
    
    def __init__(self, config: LLMConfig):
        # 设置默认 base_url
        if not config.base_url:
            config.base_url = os.getenv(
                "MISTRAL_ENDPOINT",
                PROVIDER_DEFAULT_ENDPOINTS.get("mistral", "https://api.mistral.ai/v1")
            )
        
        # 从环境变量获取 API Key
        if not config.api_key:
            env_var = get_api_key_env_var("mistral")
            config.api_key = os.getenv(env_var, "")
        
        super().__init__(config)
    
    def _initialize_client(self) -> Any:
        """初始化 Mistral 客户端"""
        try:
            from mistralai import Mistral
            return Mistral(api_key=self.config.api_key)
        except ImportError:
            logger.error("[Mistral] mistralai 库未安装，请运行: pip install mistralai")
            raise
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """Mistral 聊天"""
        self.ensure_initialized()
        
        try:
            response = self._client.chat.complete(
                model=self.config.model_name,
                messages=messages,
                temperature=temperature if temperature is not None else self.config.temperature,
                max_tokens=max_tokens or self.config.max_tokens,
            )
            
            content = response.choices[0].message.content or ""
            
            usage = response.usage
            
            return LLMResponse(
                content=content,
                model=response.model,
                finish_reason=response.choices[0].finish_reason or "",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Mistral] 聊天请求失败: {e}")
            raise
    
    async def achat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """异步聊天"""
        self.ensure_initialized()
        
        try:
            response = await self._client.chat.complete_async(
                model=self.config.model_name,
                messages=messages,
                temperature=temperature if temperature is not None else self.config.temperature,
                max_tokens=max_tokens or self.config.max_tokens,
            )
            
            content = response.choices[0].message.content or ""
            
            usage = response.usage
            
            return LLMResponse(
                content=content,
                model=response.model,
                finish_reason=response.choices[0].finish_reason or "",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Mistral] 异步聊天请求失败: {e}")
            raise
    
    def get_langchain_llm(self) -> Any:
        """获取 LangChain LLM 实例"""
        from langchain_mistralai import ChatMistralAI
        
        return ChatMistralAI(
            model=self.config.model_name,
            api_key=self.config.api_key,
            temperature=self.config.temperature,
        )
    
    def get_browser_use_llm(self) -> Any:
        """获取 Browser-Use LLM 实例"""
        return self.get_langchain_llm()
