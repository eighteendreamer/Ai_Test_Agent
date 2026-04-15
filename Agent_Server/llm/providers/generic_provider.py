"""
通用 OpenAI 兼容 Provider 实现

用于支持任何兼容 OpenAI API 的模型服务

作者: 程序员Eighteen
"""
import json
import os
import re
import logging
from typing import Any, Dict, List

from ..base import BaseOpenAICompatibleProvider, LLMConfig, LLMResponse, ProviderType
from ..config import is_reasoning_model

logger = logging.getLogger(__name__)


class GenericOpenAIProvider(BaseOpenAICompatibleProvider):
    """
    通用 OpenAI 兼容 Provider
    
    支持任何兼容 OpenAI API 的模型服务，如:
    - 硅基流动 (SiliconFlow)
    - 魔搭社区 (ModelScope)
    - 智谱 AI
    - 百川智能
    - MiniMax
    - Grok (xAI)
    - OpenRouter
    - Vercel AI
    - Cerebras
    - Browser-Use Cloud
    - 百度文心一言
    - 其他自定义服务
    
    这些 Provider 都使用 OpenAI 兼容的 API 格式
    """
    
    def __init__(self, config: LLMConfig, provider_name: str = None):
        # 如果没有指定 provider_name，从 config.provider 获取显示名称
        if provider_name is None:
            from ..config import get_provider_display_name
            provider_name = get_provider_display_name(config.provider)
        
        self._provider_name = provider_name
        
        # 如果没有设置 base_url，尝试从默认配置获取
        if not config.base_url:
            from ..config import get_default_endpoint
            config.base_url = os.getenv(
                f"{config.provider.upper()}_ENDPOINT",
                get_default_endpoint(config.provider)
            )
        
        super().__init__(config)
    
    @property
    def provider_name(self) -> str:
        return self._provider_name
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.CUSTOM
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """聊天，自动检测推理模型"""
        self.ensure_initialized()
        
        request_params = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.config.max_tokens,
        }
        
        # 添加额外参数（如 enable_thinking）
        if self.config.extra_params:
            request_params.update(self.config.extra_params)
        
        request_params.update(kwargs)
        
        try:
            response = self._client.chat.completions.create(**request_params)
            
            message = response.choices[0].message
            content = message.content or ""
            
            # 处理推理模型
            reasoning_content = ""
            if hasattr(message, 'reasoning_content') and message.reasoning_content:
                reasoning_content = message.reasoning_content
            elif self.is_reasoning_model():
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
            logger.error(f"[{self.provider_name}] 聊天请求失败: {e}")
            raise
    
    def get_langchain_llm(self) -> Any:
        """获取 LangChain LLM 实例"""
        from langchain_openai import ChatOpenAI
        
        kwargs = {
            "model": self.config.model_name,
            "api_key": self.config.api_key,
            "base_url": self.config.base_url,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        
        # 添加额外参数
        if self.config.extra_params:
            kwargs["extra_body"] = self.config.extra_params
        
        return ChatOpenAI(**kwargs)
    
    def get_browser_use_llm(self) -> Any:
        """获取 Browser-Use LLM 实例"""
        try:
            from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
            
            return BrowserUseChatOpenAI(
                model=self.config.model_name,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                temperature=self.config.temperature,
                dont_force_structured_output=not self.supports_structured_output(),
            )
        except ImportError:
            logger.warning(f"[{self.provider_name}] browser-use 未安装，回退到 LangChain")
            return self.get_langchain_llm()

    def parse_json_response(self, content: str) -> dict:
        """
        通用 OpenAI 兼容 Provider JSON 解析

        覆盖 SiliconFlow、ModelScope、智谱、Grok 等多种服务：
        - SiliconFlow/ModelScope 上的 DeepSeek R1 有 <think> 标签
        - 智谱 GLM 不支持结构化输出，可能有 markdown 包裹
        - 参考 openclaw: Minimax 是 reasoning tag provider
        """
        if not content:
            raise ValueError("LLM 响应为空")

        text = content.strip()

        # 1. 剥离推理标签（SiliconFlow/ModelScope 上的 R1、QwQ 等）
        if self.is_reasoning_model() and "<think>" in text and "</think>" in text:
            parts = text.split("</think>", 1)
            text = parts[1].strip() if len(parts) >= 2 else text

        # 2. 剥离 **JSON Response:**
        if "**JSON Response:**" in text:
            text = text.split("**JSON Response:**")[-1].strip()

        # 3. 剥离 markdown 代码块
        text = self._extract_json(text)

        # 4. 直接尝试
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 5. 移除尾部逗号
        fixed = re.sub(r',\s*([}\]])', r'\1', text)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 6. 提取第一个 JSON 对象
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                candidate = re.sub(r',\s*([}\]])', r'\1', match.group(0))
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # 7. 使用 json_repair 库修复
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                logger.info(f"[{self.provider_name}] json_repair 成功修复 JSON ({len(text)} 字符)")
                return repaired
        except Exception as e:
            logger.debug(f"[{self.provider_name}] json_repair 失败: {e}")

        # 8. 回退到基类
        return super().parse_json_response(content)


# 预定义的特定 Provider（继承 GenericOpenAIProvider）

class SiliconFlowProvider(GenericOpenAIProvider):
    """硅基流动 Provider"""
    
    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = os.getenv("SILICONFLOW_ENDPOINT", "https://api.siliconflow.cn/v1")
        if not config.api_key:
            config.api_key = os.getenv("SILICONFLOW_API_KEY", "")
        super().__init__(config, "SiliconFlow")


class ModelScopeProvider(GenericOpenAIProvider):
    """魔搭社区 Provider"""
    
    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = os.getenv("MODELSCOPE_ENDPOINT", "https://api-inference.modelscope.cn/v1")
        if not config.api_key:
            config.api_key = os.getenv("MODELSCOPE_API_KEY", "")
        
        # ModelScope 需要禁用 thinking
        if not config.extra_params:
            config.extra_params = {}
        config.extra_params["enable_thinking"] = config.enable_thinking
        
        super().__init__(config, "ModelScope")


class ZhipuProvider(GenericOpenAIProvider):
    """智谱 AI Provider"""
    
    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = os.getenv("ZHIPU_ENDPOINT", "https://open.bigmodel.cn/api/paas/v4")
        if not config.api_key:
            config.api_key = os.getenv("ZHIPU_API_KEY", "")
        super().__init__(config, "Zhipu")
    
    def supports_structured_output(self) -> bool:
        return False


class GrokProvider(GenericOpenAIProvider):
    """Grok (xAI) Provider"""
    
    def __init__(self, config: LLMConfig):
        if not config.base_url:
            config.base_url = os.getenv("GROK_ENDPOINT", "https://api.x.ai/v1")
        if not config.api_key:
            config.api_key = os.getenv("GROK_API_KEY", "")
        super().__init__(config, "Grok")
