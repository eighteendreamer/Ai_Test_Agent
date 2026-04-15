"""
OpenAI Provider 实现

支持 GPT-4、GPT-3.5、O1 等模型

作者: 程序员Eighteen
"""
import os
import logging
from typing import Any, Dict, List

from ..base import BaseOpenAICompatibleProvider, LLMConfig, LLMResponse, ProviderType
from ..config import PROVIDER_DEFAULT_ENDPOINTS, get_api_key_env_var

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseOpenAICompatibleProvider):
    """
    OpenAI Provider
    
    支持所有 OpenAI 官方模型
    """
    
    @property
    def provider_name(self) -> str:
        return "OpenAI"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.OPENAI
    
    def __init__(self, config: LLMConfig):
        # 设置默认 base_url
        if not config.base_url:
            config.base_url = os.getenv(
                "OPENAI_ENDPOINT", 
                PROVIDER_DEFAULT_ENDPOINTS.get("openai", "https://api.openai.com/v1")
            )
        
        # 从环境变量获取 API Key（如果未提供）
        if not config.api_key:
            env_var = get_api_key_env_var("openai")
            config.api_key = os.getenv(env_var, "")
        
        super().__init__(config)
    
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
        """获取 Browser-Use LLM 实例"""
        try:
            from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
            
            return BrowserUseChatOpenAI(
                model=self.config.model_name,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                temperature=self.config.temperature,
            )
        except ImportError:
            logger.warning("[OpenAI] browser-use 未安装，回退到 LangChain")
            return self.get_langchain_llm()
