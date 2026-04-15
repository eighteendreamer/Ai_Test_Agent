"""
LLM 工厂模块

提供统一的 LLM 创建接口，根据 Provider 类型创建对应的实例

作者: 程序员Eighteen
版本: 1.0
"""
import logging
from typing import Any, Dict, Optional, Type, Union

from .base import BaseLLMProvider, LLMConfig, ProviderType
from .exceptions import (
    ProviderNotFoundError,
    APIKeyMissingError,
    ConfigurationError,
    ModelInitializationError,
)
from .config import (
    get_provider_display_name,
    get_api_key_env_var,
    get_default_endpoint,
    supports_structured_output,
)

logger = logging.getLogger(__name__)


# Provider 类映射表
_PROVIDER_CLASSES: Dict[str, Type[BaseLLMProvider]] = {}


def _register_provider(provider_code: str, provider_class: Type[BaseLLMProvider]):
    """注册 Provider 类"""
    _PROVIDER_CLASSES[provider_code.lower()] = provider_class


def _load_providers():
    """加载所有 Provider 类"""
    global _PROVIDER_CLASSES
    
    if _PROVIDER_CLASSES:
        return
    
    from .providers.openai_provider import OpenAIProvider
    from .providers.anthropic_provider import AnthropicProvider
    from .providers.deepseek_provider import DeepSeekProvider
    from .providers.google_provider import GoogleProvider
    from .providers.alibaba_provider import AlibabaProvider
    from .providers.ollama_provider import OllamaProvider
    from .providers.azure_provider import AzureOpenAIProvider
    from .providers.mistral_provider import MistralProvider
    from .providers.moonshot_provider import MoonshotProvider
    from .providers.minimax_provider import MiniMaxProvider
    from .providers.generic_provider import (
        GenericOpenAIProvider,
        SiliconFlowProvider,
        ModelScopeProvider,
        ZhipuProvider,
        GrokProvider,
    )
    
    _PROVIDER_CLASSES = {
        # 核心 Provider（有专门实现）
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "deepseek": DeepSeekProvider,
        "google": GoogleProvider,
        "alibaba": AlibabaProvider,
        "ollama": OllamaProvider,
        "azure_openai": AzureOpenAIProvider,
        "mistral": MistralProvider,
        "moonshot": MoonshotProvider,
        "minimax": MiniMaxProvider,
        
        # 通用 OpenAI 兼容 Provider
        "siliconflow": SiliconFlowProvider,
        "modelscope": ModelScopeProvider,
        "zhipu": ZhipuProvider,
        "grok": GrokProvider,
        
        # 数据库中的其他 Provider（使用通用 OpenAI 兼容接口）
        "baidu": GenericOpenAIProvider,       # 百度文心一言
        "openrouter": GenericOpenAIProvider,  # OpenRouter 聚合平台
        "vercel": GenericOpenAIProvider,      # Vercel AI
        "cerebras": GenericOpenAIProvider,    # Cerebras
        "browser_use": GenericOpenAIProvider, # Browser-Use Cloud
        
        # 自定义
        "custom": GenericOpenAIProvider,
    }
    
    # 添加别名（方便用户使用不同名称）
    _PROVIDER_CLASSES["azure"] = AzureOpenAIProvider
    _PROVIDER_CLASSES["qwen"] = AlibabaProvider
    _PROVIDER_CLASSES["tongyi"] = AlibabaProvider
    _PROVIDER_CLASSES["kimi"] = MoonshotProvider
    _PROVIDER_CLASSES["claude"] = AnthropicProvider
    _PROVIDER_CLASSES["gemini"] = GoogleProvider


def get_supported_providers() -> list:
    """获取支持的 Provider 列表"""
    _load_providers()
    # 主要 Provider 列表（与数据库 model_providers 表对应）
    main_providers = [
        "openai", "anthropic", "google", "deepseek", "alibaba",
        "baidu", "ollama", "mistral", "grok", "openrouter",
        "vercel", "cerebras", "browser_use", "moonshot",
        "siliconflow", "modelscope", "zhipu", "azure_openai", "custom"
    ]
    return main_providers


def create_llm_provider(
    provider: str,
    model_name: str,
    api_key: str = "",
    base_url: str = "",
    temperature: float = 0.0,
    max_tokens: int = 4096,
    **kwargs
) -> BaseLLMProvider:
    """
    创建 LLM Provider 实例
    
    这是主要的工厂函数，根据 provider 类型创建对应的 LLM 实例
    
    Args:
        provider: Provider 代码（如 openai, deepseek, anthropic 等）
        model_name: 模型名称
        api_key: API 密钥（可选，可从环境变量获取）
        base_url: API 端点（可选，使用默认值）
        temperature: 温度参数
        max_tokens: 最大 token 数
        **kwargs: 其他参数（如 api_version, num_ctx 等）
    
    Returns:
        BaseLLMProvider 实例
    
    Raises:
        ProviderNotFoundError: Provider 不存在
        APIKeyMissingError: API Key 缺失
        ModelInitializationError: 模型初始化失败
    
    Example:
        >>> provider = create_llm_provider(
        ...     provider="deepseek",
        ...     model_name="deepseek-chat",
        ...     api_key="sk-xxx"
        ... )
        >>> response = provider.chat([{"role": "user", "content": "Hello"}])
    """
    _load_providers()
    
    provider_lower = provider.lower().strip()
    
    # 检查 Provider 是否存在
    if provider_lower not in _PROVIDER_CLASSES:
        raise ProviderNotFoundError(provider)
    
    # 构建配置
    config = LLMConfig(
        provider=provider_lower,
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        **{k: v for k, v in kwargs.items() if k in LLMConfig.__dataclass_fields__}
    )
    
    # 设置额外参数
    extra_params = {k: v for k, v in kwargs.items() if k not in LLMConfig.__dataclass_fields__}
    if extra_params:
        config.extra_params = extra_params
    
    try:
        # 创建 Provider 实例
        provider_class = _PROVIDER_CLASSES[provider_lower]
        provider_instance = provider_class(config)
        
        logger.info(f"[LLMFactory] 创建 Provider: {provider_class.__name__}(model={model_name})")
        
        return provider_instance
        
    except Exception as e:
        logger.error(f"[LLMFactory] 创建 Provider 失败: {e}")
        raise ModelInitializationError(provider, model_name, str(e))


def get_llm_model(provider: str, **kwargs) -> Any:
    """
    获取 LangChain 格式的 LLM 模型
    
    兼容 web-ui-3.0.0 的接口风格
    
    Args:
        provider: Provider 代码
        **kwargs: 配置参数（model_name, api_key, base_url, temperature 等）
    
    Returns:
        LangChain LLM 实例
    
    Example:
        >>> llm = get_llm_model(
        ...     provider="openai",
        ...     model_name="gpt-4o",
        ...     temperature=0.7
        ... )
    """
    model_name = kwargs.pop("model_name", kwargs.pop("model", ""))
    
    if not model_name:
        raise ConfigurationError(f"必须指定 model_name 参数")
    
    llm_provider = create_llm_provider(
        provider=provider,
        model_name=model_name,
        **kwargs
    )
    
    return llm_provider.get_langchain_llm()


def get_browser_use_llm(provider: str, **kwargs) -> Any:
    """
    获取 Browser-Use 格式的 LLM 模型
    
    Args:
        provider: Provider 代码
        **kwargs: 配置参数
    
    Returns:
        Browser-Use LLM 实例
    """
    model_name = kwargs.pop("model_name", kwargs.pop("model", ""))
    
    if not model_name:
        raise ConfigurationError(f"必须指定 model_name 参数")
    
    llm_provider = create_llm_provider(
        provider=provider,
        model_name=model_name,
        **kwargs
    )
    
    return llm_provider.get_browser_use_llm()


def create_llm_from_config(config: Dict[str, Any]) -> BaseLLMProvider:
    """
    从配置字典创建 LLM Provider
    
    Args:
        config: 配置字典，需要包含 provider 和 model_name
    
    Returns:
        BaseLLMProvider 实例
    
    Example:
        >>> config = {
        ...     "provider": "deepseek",
        ...     "model_name": "deepseek-chat",
        ...     "api_key": "sk-xxx",
        ...     "temperature": 0.0
        ... }
        >>> provider = create_llm_from_config(config)
    """
    provider = config.pop("provider", None)
    model_name = config.pop("model_name", config.pop("model", None))
    
    if not provider:
        raise ConfigurationError("配置中缺少 provider 字段")
    if not model_name:
        raise ConfigurationError("配置中缺少 model_name 字段")
    
    return create_llm_provider(
        provider=provider,
        model_name=model_name,
        **config
    )


class LLMFactory:
    """
    LLM 工厂类
    
    提供面向对象风格的 LLM 创建接口
    """
    
    @staticmethod
    def create(
        provider: str,
        model_name: str,
        **kwargs
    ) -> BaseLLMProvider:
        """创建 LLM Provider"""
        return create_llm_provider(provider, model_name, **kwargs)
    
    @staticmethod
    def get_langchain_llm(provider: str, **kwargs) -> Any:
        """获取 LangChain LLM"""
        return get_llm_model(provider, **kwargs)
    
    @staticmethod
    def get_browser_use_llm(provider: str, **kwargs) -> Any:
        """获取 Browser-Use LLM"""
        return get_browser_use_llm(provider, **kwargs)
    
    @staticmethod
    def from_config(config: Dict[str, Any]) -> BaseLLMProvider:
        """从配置创建"""
        return create_llm_from_config(config.copy())
    
    @staticmethod
    def supported_providers() -> list:
        """获取支持的 Provider 列表"""
        return get_supported_providers()
