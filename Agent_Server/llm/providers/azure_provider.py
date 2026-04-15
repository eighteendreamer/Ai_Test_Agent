"""
Azure OpenAI Provider 实现

支持 Azure 部署的 OpenAI 模型

作者: 程序员Eighteen
"""
import os
import logging
from typing import Any, Dict, List

from ..base import BaseLLMProvider, LLMConfig, LLMResponse, ProviderType
from ..config import get_api_key_env_var

logger = logging.getLogger(__name__)


class AzureOpenAIProvider(BaseLLMProvider):
    """
    Azure OpenAI Provider
    
    支持 Azure 部署的 GPT 模型
    """
    
    @property
    def provider_name(self) -> str:
        return "Azure OpenAI"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.AZURE_OPENAI
    
    def __init__(self, config: LLMConfig):
        # 设置默认值
        if not config.base_url:
            config.base_url = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        
        if not config.api_key:
            env_var = get_api_key_env_var("azure_openai")
            config.api_key = os.getenv(env_var, "")
        
        if not config.api_version:
            config.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        
        super().__init__(config)
    
    def _initialize_client(self) -> Any:
        """初始化 Azure OpenAI 客户端"""
        from openai import AzureOpenAI
        
        return AzureOpenAI(
            api_key=self.config.api_key,
            api_version=self.config.api_version,
            azure_endpoint=self.config.base_url,
        )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """Azure OpenAI 聊天"""
        self.ensure_initialized()
        
        request_params = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        request_params.update(kwargs)
        
        try:
            response = self._client.chat.completions.create(**request_params)
            
            message = response.choices[0].message
            content = message.content or ""
            
            usage = response.usage
            
            return LLMResponse(
                content=content,
                model=response.model,
                finish_reason=response.choices[0].finish_reason,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Azure OpenAI] 聊天请求失败: {e}")
            raise
    
    async def achat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """异步聊天"""
        from openai import AsyncAzureOpenAI
        
        async_client = AsyncAzureOpenAI(
            api_key=self.config.api_key,
            api_version=self.config.api_version,
            azure_endpoint=self.config.base_url,
        )
        
        request_params = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        request_params.update(kwargs)
        
        try:
            response = await async_client.chat.completions.create(**request_params)
            
            message = response.choices[0].message
            content = message.content or ""
            
            usage = response.usage
            
            return LLMResponse(
                content=content,
                model=response.model,
                finish_reason=response.choices[0].finish_reason,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Azure OpenAI] 异步聊天请求失败: {e}")
            raise
    
    def get_langchain_llm(self) -> Any:
        """获取 LangChain LLM 实例"""
        from langchain_openai import AzureChatOpenAI
        
        return AzureChatOpenAI(
            model=self.config.model_name,
            api_key=self.config.api_key,
            api_version=self.config.api_version,
            azure_endpoint=self.config.base_url,
            temperature=self.config.temperature,
        )
    
    def get_browser_use_llm(self) -> Any:
        """获取 Browser-Use LLM 实例"""
        return self.get_langchain_llm()
