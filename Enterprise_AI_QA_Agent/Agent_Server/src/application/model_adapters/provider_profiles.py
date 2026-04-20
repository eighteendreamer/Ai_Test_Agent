from __future__ import annotations

from dataclasses import dataclass

from src.schemas.model_config import ModelCapabilities, ModelTransport


@dataclass(frozen=True)
class ProviderProfile:
    provider: str
    aliases: tuple[str, ...]
    transport: ModelTransport
    capabilities: ModelCapabilities


_OPENAI_COMPAT_CAPABILITIES = ModelCapabilities(
    text_input=True,
    text_output=True,
    tool_calling=True,
    vision=False,
    reasoning=True,
    streaming=True,
    image_url_input=True,
    image_base64_input=True,
)

_VISION_OPENAI_COMPAT_CAPABILITIES = ModelCapabilities(
    text_input=True,
    text_output=True,
    tool_calling=True,
    vision=True,
    reasoning=True,
    streaming=True,
    image_url_input=True,
    image_base64_input=True,
)

PROVIDER_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        provider="anthropic",
        aliases=("anthropic",),
        transport="anthropic_messages",
        capabilities=ModelCapabilities(
            text_input=True,
            text_output=True,
            tool_calling=True,
            vision=True,
            reasoning=True,
            streaming=True,
            image_url_input=False,
            image_base64_input=True,
        ),
    ),
    ProviderProfile(
        provider="openai",
        aliases=("openai", "azure-openai", "azure_openai"),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="deepseek",
        aliases=("deepseek",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="qwen",
        aliases=("qwen", "dashscope"),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="zhipu",
        aliases=("zhipu", "glm"),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="moonshot",
        aliases=("moonshot", "kimi"),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="groq",
        aliases=("groq",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="siliconflow",
        aliases=("siliconflow",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="ollama",
        aliases=("ollama",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="vllm",
        aliases=("vllm",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="openrouter",
        aliases=("openrouter",),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="mistral",
        aliases=("mistral",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="together",
        aliases=("together",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="xai",
        aliases=("xai",),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="yi",
        aliases=("yi",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="baichuan",
        aliases=("baichuan",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="minimax",
        aliases=("minimax",),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="volcengine",
        aliases=("volcengine", "doubao"),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="hunyuan",
        aliases=("hunyuan",),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="baidu",
        aliases=("baidu", "ernie"),
        transport="openai_chat_completions",
        capabilities=_VISION_OPENAI_COMPAT_CAPABILITIES,
    ),
    ProviderProfile(
        provider="google",
        aliases=("google", "gemini"),
        transport="google_gemini_generate_content",
        capabilities=ModelCapabilities(
            text_input=True,
            text_output=True,
            tool_calling=True,
            vision=True,
            multi_image=True,
            file_input=True,
            reasoning=True,
            streaming=True,
            image_url_input=False,
            image_base64_input=True,
        ),
    ),
)


def resolve_provider_profile(provider: str) -> ProviderProfile:
    normalized = normalize_provider(provider)
    for profile in PROVIDER_PROFILES:
        if normalized == profile.provider:
            return profile
    return ProviderProfile(
        provider=normalized or "openai",
        aliases=(normalized or "openai",),
        transport="openai_chat_completions",
        capabilities=_OPENAI_COMPAT_CAPABILITIES,
    )


def normalize_provider(provider: str) -> str:
    value = (provider or "").strip().lower()
    for profile in PROVIDER_PROFILES:
        if value == profile.provider or value in profile.aliases:
            return profile.provider
    return value or "openai"
