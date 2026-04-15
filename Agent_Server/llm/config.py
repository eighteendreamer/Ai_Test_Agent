"""
LLM 模块配置

包含 Provider 显示名称、默认端点、预定义模型列表等配置

作者: 程序员Eighteen
版本: 1.0
"""
import os
from typing import Dict, List


def is_primary_model_only_mode() -> bool:
    """是否强制所有调用仅使用当前激活主模型。"""
    raw = str(os.getenv("LLM_PRIMARY_MODEL_ONLY", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


# Provider 显示名称映射（与数据库 model_providers 表对应）
PROVIDER_DISPLAY_NAMES: Dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google (Gemini)",
    "deepseek": "DeepSeek",
    "alibaba": "Alibaba (通义千问)",
    "baidu": "Baidu (文心一言)",
    "ollama": "Ollama",
    "mistral": "Mistral AI",
    "grok": "Grok",
    "openrouter": "OpenRouter",
    "vercel": "Vercel AI",
    "cerebras": "Cerebras",
    "browser_use": "Browser Use Cloud",
    "azure_openai": "Azure OpenAI",
    "moonshot": "MoonShot/Kimi",
    "siliconflow": "硅基流动",
    "modelscope": "魔搭社区",
    "zhipu": "智谱 AI",
    "baichuan": "百川智能",
    "minimax": "MiniMax",
    "ibm": "IBM Watson",
    "unbound": "Unbound AI",
    "custom": "自定义 (OpenAI 兼容)",
}


# Provider 默认端点配置（与数据库 model_providers.default_base_url 对应）
PROVIDER_DEFAULT_ENDPOINTS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "baidu": "https://aip.baidubce.com/rpc/2.0/ai_custom/v1",
    "ollama": "http://localhost:11434",
    "mistral": "https://api.mistral.ai/v1",
    "grok": "https://api.grok.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "vercel": "https://ai-gateway.vercel.sh/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "browser_use": "https://llm.api.browser-use.com",
    "moonshot": "https://api.moonshot.cn/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "modelscope": "https://api-inference.modelscope.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "baichuan": "https://api.baichuan-ai.com/v1",
    "minimax": "https://api.minimax.chat/v1",
}


# Provider API Key 环境变量名
PROVIDER_API_KEY_ENV_VARS: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "alibaba": "ALIBABA_API_KEY",
    "baidu": "BAIDU_API_KEY",
    "ollama": "",  # Ollama 本地不需要 API Key
    "mistral": "MISTRAL_API_KEY",
    "grok": "GROK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "vercel": "VERCEL_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "browser_use": "BROWSER_USE_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "modelscope": "MODELSCOPE_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
    "baichuan": "BAICHUAN_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "ibm": "IBM_API_KEY",
}


# 各 Provider 的预定义模型列表
MODEL_NAMES: Dict[str, List[str]] = {
    "openai": [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo",
        "o1-preview",
        "o1-mini",
        "o3-mini",
    ],
    "anthropic": [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-20240620",
        "claude-3-opus-20240229",
        "claude-3-sonnet-20240229",
        "claude-3-haiku-20240307",
    ],
    "deepseek": [
        "deepseek-chat",
        "deepseek-coder",
        "deepseek-reasoner",
    ],
    "google": [
        "gemini-2.0-flash",
        "gemini-2.0-flash-thinking-exp",
        "gemini-1.5-flash-latest",
        "gemini-1.5-flash-8b-latest",
        "gemini-1.5-pro-latest",
        "gemini-2.5-pro-preview-03-25",
        "gemini-2.5-flash-preview-04-17",
    ],
    "alibaba": [
        "qwen-plus",
        "qwen-max",
        "qwen-turbo",
        "qwen-long",
        "qwen-vl-max",
        "qwen-vl-plus",
        "qwen2.5-72b-instruct",
        "qwen2.5-32b-instruct",
        "qwen2.5-14b-instruct",
        "qwen2.5-7b-instruct",
        "qwen2.5-coder-32b-instruct",
    ],
    "moonshot": [
        "moonshot-v1-32k-vision-preview",
        "moonshot-v1-8k-vision-preview",
        "moonshot-v1-128k",
        "moonshot-v1-32k",
        "moonshot-v1-8k",
    ],
    "ollama": [
        "qwen2.5:7b",
        "qwen2.5:14b",
        "qwen2.5:32b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:32b",
        "llama3.1:8b",
        "llama3.1:70b",
        "deepseek-r1:14b",
        "deepseek-r1:32b",
        "deepseek-coder-v2:16b",
        "mistral:7b",
    ],
    "azure_openai": [
        "gpt-4o",
        "gpt-4",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ],
    "mistral": [
        "mistral-large-latest",
        "mistral-medium-latest",
        "mistral-small-latest",
        "pixtral-large-latest",
        "ministral-8b-latest",
        "codestral-latest",
    ],
    "grok": [
        "grok-3",
        "grok-3-fast",
        "grok-3-mini",
        "grok-3-mini-fast",
        "grok-2-vision",
        "grok-2",
    ],
    "siliconflow": [
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "Qwen/Qwen2.5-Coder-32B-Instruct",
        "Qwen/QwQ-32B-Preview",
    ],
    "modelscope": [
        "Qwen/Qwen2.5-Coder-32B-Instruct",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "Qwen/QwQ-32B-Preview",
        "deepseek-ai/DeepSeek-R1",
        "deepseek-ai/DeepSeek-V3",
        "Qwen/Qwen3-32B",
        "Qwen/Qwen3-235B-A22B",
    ],
    "zhipu": [
        "glm-4-plus",
        "glm-4-0520",
        "glm-4",
        "glm-4-air",
        "glm-4-airx",
        "glm-4-flash",
        "glm-4v-plus",
        "glm-4v",
    ],
    "baichuan": [
        "Baichuan4",
        "Baichuan3-Turbo",
        "Baichuan3-Turbo-128k",
        "Baichuan2-Turbo",
    ],
    "minimax": [
        "abab6.5s-chat",
        "abab6.5-chat",
        "abab5.5s-chat",
        "abab5.5-chat",
    ],
    "ibm": [
        "ibm/granite-vision-3.1-2b-preview",
        "meta-llama/llama-4-maverick-17b-128e-instruct-fp8",
        "meta-llama/llama-3-2-90b-vision-instruct",
    ],
}


# 需要特殊处理的推理模型（如 DeepSeek R1、Qwen QwQ）
REASONING_MODELS: Dict[str, List[str]] = {
    "deepseek": ["deepseek-reasoner"],
    "ollama": ["deepseek-r1"],
    "siliconflow": [
        "deepseek-ai/DeepSeek-R1",
        "Qwen/QwQ-32B-Preview",
    ],
    "modelscope": [
        "deepseek-ai/DeepSeek-R1",
        "Qwen/QwQ-32B-Preview",
    ],
}


# 不支持结构化输出的 Provider
PROVIDERS_WITHOUT_STRUCTURED_OUTPUT: List[str] = [
    "deepseek",
    "alibaba",
    "ollama",
    "moonshot",
    "zhipu",
    "baichuan",
    "minimax",
]


# Provider 特性配置
PROVIDER_FEATURES: Dict[str, Dict[str, bool]] = {
    "openai": {
        "supports_vision": True,
        "supports_function_calling": True,
        "supports_structured_output": True,
        "supports_streaming": True,
    },
    "anthropic": {
        "supports_vision": True,
        "supports_function_calling": True,
        "supports_structured_output": True,
        "supports_streaming": True,
    },
    "deepseek": {
        "supports_vision": False,
        "supports_function_calling": True,
        "supports_structured_output": False,
        "supports_streaming": True,
    },
    "google": {
        "supports_vision": True,
        "supports_function_calling": True,
        "supports_structured_output": True,
        "supports_streaming": True,
    },
    "alibaba": {
        "supports_vision": True,
        "supports_function_calling": True,
        "supports_structured_output": False,
        "supports_streaming": True,
    },
    "ollama": {
        "supports_vision": True,  # 部分模型支持
        "supports_function_calling": False,
        "supports_structured_output": False,
        "supports_streaming": True,
    },
}


def get_provider_display_name(provider: str) -> str:
    """获取 Provider 显示名称"""
    return PROVIDER_DISPLAY_NAMES.get(provider, provider.upper())


def get_provider_models(provider: str) -> List[str]:
    """获取 Provider 支持的模型列表"""
    return MODEL_NAMES.get(provider, [])


def get_default_endpoint(provider: str) -> str:
    """获取 Provider 默认端点"""
    return PROVIDER_DEFAULT_ENDPOINTS.get(provider, "")


def get_api_key_env_var(provider: str) -> str:
    """获取 Provider API Key 环境变量名"""
    return PROVIDER_API_KEY_ENV_VARS.get(provider, f"{provider.upper()}_API_KEY")


def is_reasoning_model(provider: str, model_name: str) -> bool:
    """判断是否为推理模型（需要特殊处理输出）"""
    provider_models = REASONING_MODELS.get(provider, [])
    # 精确匹配
    if model_name in provider_models:
        return True
    # 模糊匹配（如 deepseek-r1:14b 匹配 deepseek-r1）
    for rm in provider_models:
        if rm in model_name or model_name.startswith(rm):
            return True
    return False


def supports_structured_output(provider: str) -> bool:
    """判断 Provider 是否支持结构化输出"""
    return provider not in PROVIDERS_WITHOUT_STRUCTURED_OUTPUT


def get_provider_feature(provider: str, feature: str) -> bool:
    """获取 Provider 特性"""
    features = PROVIDER_FEATURES.get(provider, {})
    return features.get(feature, False)


def supports_vision_model(provider: str, model_name: str = "") -> bool:
    """判断具体模型是否支持视觉输入。

    说明：
    - 仅靠 provider 能力不够，某些 provider 同时托管文本模型和视觉模型。
    - 对于通用支持视觉的 provider，若模型名明显是纯文本模型则仍返回 False。
    """
    provider = str(provider or "").strip().lower()
    model_name = str(model_name or "").strip().lower()
    if not get_provider_feature(provider, "supports_vision"):
        return False

    if not model_name:
        return provider in {"openai", "anthropic", "google", "azure_openai"}

    positive_patterns = [
        "vision",
        "-vl",
        "_vl",
        "vl-",
        "qwen-vl",
        "qvq",
        "gpt-4o",
        "omni",
        "gemini",
        "claude-3",
        "claude-4",
        "pixtral",
        "granite-vision",
        "llama-3.2-11b-vision",
        "llama-3.2-90b-vision",
        "moonshot-v1-8k-vision-preview",
        "moonshot-v1-32k-vision-preview",
        "grok-2-vision",
    ]
    negative_patterns = [
        "deepseek-chat",
        "deepseek-v3",
        "deepseek-v3.2",
        "deepseek-r1",
        "deepseek-ai/deepseek-v3",
        "deepseek-ai/deepseek-v3.2",
    ]

    if any(pattern in model_name for pattern in negative_patterns):
        return False

    if provider in {"openai", "anthropic", "google", "azure_openai"}:
        return True

    return any(pattern in model_name for pattern in positive_patterns)
