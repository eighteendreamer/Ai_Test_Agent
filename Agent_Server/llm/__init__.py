"""
LLM 模块

提供统一的大模型接口，支持多种模型供应商

主要功能:
- 工厂模式创建不同的 LLM 实例
- 统一的 Provider 接口
- 支持 LangChain 和 Browser-Use 两种使用场景
- 自动处理不同模型的输出差异（如 DeepSeek R1 的 reasoning_content）

支持的 Provider:
- OpenAI (GPT-4, GPT-3.5, O1 等)
- Anthropic (Claude 3 系列)
- DeepSeek (Chat, Coder, Reasoner)
- Google (Gemini 系列)
- 阿里云/通义千问 (Qwen 系列)
- Moonshot/Kimi
- Ollama (本地模型)
- Azure OpenAI
- Mistral AI
- 硅基流动 (SiliconFlow)
- 魔搭社区 (ModelScope)
- 智谱 AI (GLM)
- Grok (xAI)
- 自定义 OpenAI 兼容接口

使用示例:

1. 使用工厂创建 Provider:
    >>> from llm import create_llm_provider
    >>> provider = create_llm_provider(
    ...     provider="deepseek",
    ...     model_name="deepseek-chat",
    ...     api_key="sk-xxx"
    ... )
    >>> response = provider.chat([{"role": "user", "content": "你好"}])
    >>> print(response.content)

2. 获取 LangChain LLM:
    >>> from llm import get_llm_model
    >>> llm = get_llm_model(
    ...     provider="openai",
    ...     model_name="gpt-4o",
    ...     api_key="sk-xxx"
    ... )

3. 获取 Browser-Use LLM:
    >>> from llm import get_browser_use_llm
    >>> llm = get_browser_use_llm(
    ...     provider="deepseek",
    ...     model_name="deepseek-chat"
    ... )

4. 使用配置管理器（从数据库获取激活模型）:
    >>> from llm import get_active_llm_config, get_active_langchain_llm
    >>> config = get_active_llm_config()
    >>> llm = get_active_langchain_llm()

作者: 程序员Eighteen
版本: 1.0
"""

# 基础类和数据类型
from .base import (
    BaseLLMProvider,
    BaseOpenAICompatibleProvider,
    LLMConfig,
    LLMResponse,
    ProviderType,
)

# 异常类
from .exceptions import (
    LLMError,
    ProviderNotFoundError,
    ModelNotFoundError,
    APIKeyMissingError,
    ConfigurationError,
    ModelInitializationError,
    OutputParsingError,
    TokenLimitExceededError,
    RateLimitError,
    NoActiveModelError,
)

# 配置
from .config import (
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_DEFAULT_ENDPOINTS,
    MODEL_NAMES,
    REASONING_MODELS,
    get_provider_display_name,
    get_provider_models,
    get_default_endpoint,
    get_api_key_env_var,
    is_reasoning_model,
    supports_structured_output,
    get_provider_feature,
)

# 工厂函数
from .factory import (
    create_llm_provider,
    create_llm_from_config,
    get_llm_model,
    get_browser_use_llm,
    get_supported_providers,
    LLMFactory,
)

# 包装器
from .wrapper import (
    LLMWrapper,
    wrap_llm,
)

# 配置管理器
from .manager import (
    ModelConfigManager,
    model_config_manager,
    get_active_llm_config,
    get_active_llm_provider,
    get_active_langchain_llm,
    get_active_browser_use_llm,
    refresh_llm_config,
)

# 自动切换
from .auto_switch import (
    ModelAutoSwitcher,
    ModelProfile,
    FailureReason,
    classify_failure_reason,
    get_auto_switcher,
    FailoverChatModel,
)

# LLM 客户端（兼容旧接口）
from .client import (
    LLMClient,
    get_llm_client,
)


__all__ = [
    # 基础类
    "BaseLLMProvider",
    "BaseOpenAICompatibleProvider",
    "LLMConfig",
    "LLMResponse",
    "ProviderType",
    
    # 异常
    "LLMError",
    "ProviderNotFoundError",
    "ModelNotFoundError",
    "APIKeyMissingError",
    "ConfigurationError",
    "ModelInitializationError",
    "OutputParsingError",
    "TokenLimitExceededError",
    "RateLimitError",
    "NoActiveModelError",
    
    # 配置
    "PROVIDER_DISPLAY_NAMES",
    "PROVIDER_DEFAULT_ENDPOINTS",
    "MODEL_NAMES",
    "REASONING_MODELS",
    "get_provider_display_name",
    "get_provider_models",
    "get_default_endpoint",
    "get_api_key_env_var",
    "is_reasoning_model",
    "supports_structured_output",
    "get_provider_feature",
    
    # 工厂
    "create_llm_provider",
    "create_llm_from_config",
    "get_llm_model",
    "get_browser_use_llm",
    "get_supported_providers",
    "LLMFactory",
    
    # 包装器
    "LLMWrapper",
    "wrap_llm",
    
    # 配置管理器
    "ModelConfigManager",
    "model_config_manager",
    "get_active_llm_config",
    "get_active_llm_provider",
    "get_active_langchain_llm",
    "get_active_browser_use_llm",
    "refresh_llm_config",
    
    # 自动切换
    "ModelAutoSwitcher",
    "ModelProfile",
    "FailureReason",
    "classify_failure_reason",
    "get_auto_switcher",
    "FailoverChatModel",
    
    # LLM 客户端
    "LLMClient",
    "get_llm_client",
]


__version__ = "1.0.0"
