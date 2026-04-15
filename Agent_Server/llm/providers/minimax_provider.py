"""
MiniMax Provider 实现

支持 MiniMax 系列模型（abab6.5s、abab6.5、abab5.5s 等），兼容 OpenAI API

作者: 程序员Eighteen
"""
import json
import os
import re
import logging
from typing import Any, Dict, List

from ..base import BaseOpenAICompatibleProvider, LLMConfig, LLMResponse, ProviderType
from ..config import PROVIDER_DEFAULT_ENDPOINTS, get_api_key_env_var

logger = logging.getLogger(__name__)


class MiniMaxProvider(BaseOpenAICompatibleProvider):
    """
    MiniMax Provider

    支持 MiniMax 系列模型:
    - abab6.5s-chat
    - abab6.5-chat
    - abab5.5s-chat
    - abab5.5-chat
    """

    @property
    def provider_name(self) -> str:
        return "MiniMax"

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MINIMAX

    def __init__(self, config: LLMConfig):
        # 设置默认 base_url
        if not config.base_url:
            config.base_url = os.getenv(
                "MINIMAX_ENDPOINT",
                PROVIDER_DEFAULT_ENDPOINTS.get("minimax", "https://api.minimax.chat/v1"),
            )

        # 从环境变量获取 API Key
        if not config.api_key:
            env_var = get_api_key_env_var("minimax")
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

        # MiniMax 特有的 action 别名
        minimax_aliases = {
            'extract_content': 'extract',
            'scroll_down': 'scroll',
            'scroll_up': 'scroll',
            'click_element': 'click',
            'input_text': 'input',
            'evaluate': 'run_javascript',
            'execute_js': 'run_javascript',
            'go_to_url': 'navigate',
        }

        return wrap_llm(raw_llm, action_aliases=minimax_aliases)

    def supports_structured_output(self) -> bool:
        """MiniMax 不支持结构化输出"""
        return False

    def parse_json_response(self, content: str) -> dict:
        """
        MiniMax JSON 解析

        MiniMax 的特点：
        - 不支持 response_format=json_object
        - 输出通常比较规范，偶尔有 markdown 代码块包裹
        - 可能在 JSON 前后加中文说明文字
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

        # 3. 移除尾部逗号
        fixed = re.sub(r',\s*([}\]])', r'\1', text)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 4. 提取第一个 JSON 对象
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                candidate = re.sub(r',\s*([}\]])', r'\1', match.group(0))
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        # 5. 使用 json_repair 库修复
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                logger.info(f"[MiniMax] json_repair 成功修复 JSON ({len(text)} 字符)")
                return repaired
        except Exception as e:
            logger.debug(f"[MiniMax] json_repair 失败: {e}")

        # 6. 回退到基类
        return super().parse_json_response(content)
