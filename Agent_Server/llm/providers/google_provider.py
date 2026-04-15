"""
Google Provider 实现

支持 Gemini 系列模型

作者: 程序员Eighteen
"""
import json
import os
import re
import logging
from typing import Any, Dict, List

from ..base import BaseLLMProvider, LLMConfig, LLMResponse, ProviderType
from ..config import get_api_key_env_var

logger = logging.getLogger(__name__)


class GoogleProvider(BaseLLMProvider):
    """
    Google Provider
    
    支持 Gemini 系列模型
    """
    
    @property
    def provider_name(self) -> str:
        return "Google"
    
    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GOOGLE
    
    def __init__(self, config: LLMConfig):
        # 从环境变量获取 API Key
        if not config.api_key:
            env_var = get_api_key_env_var("google")
            config.api_key = os.getenv(env_var, "")
        
        super().__init__(config)
    
    def _initialize_client(self) -> Any:
        """初始化 Google AI 客户端"""
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.config.api_key)
            return genai.GenerativeModel(self.config.model_name)
        except ImportError:
            logger.error("[Google] google-generativeai 库未安装，请运行: pip install google-generativeai")
            raise
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = None,
        max_tokens: int = None,
        **kwargs
    ) -> LLMResponse:
        """Google AI 聊天"""
        self.ensure_initialized()
        
        # 转换消息格式
        history = []
        system_instruction = ""
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                history.append({"role": "user", "parts": [msg["content"]]})
            elif msg["role"] == "assistant":
                history.append({"role": "model", "parts": [msg["content"]]})
        
        try:
            import google.generativeai as genai
            
            # 配置生成参数
            generation_config = genai.GenerationConfig(
                temperature=temperature if temperature is not None else self.config.temperature,
                max_output_tokens=max_tokens or self.config.max_tokens,
            )
            
            # 如果有系统指令，重新创建模型
            if system_instruction:
                model = genai.GenerativeModel(
                    self.config.model_name,
                    system_instruction=system_instruction
                )
            else:
                model = self._client
            
            # 开始聊天
            chat = model.start_chat(history=history[:-1] if history else [])
            
            # 发送最后一条消息
            last_message = history[-1]["parts"][0] if history else ""
            response = chat.send_message(last_message, generation_config=generation_config)
            
            # 提取内容
            content = response.text if response.text else ""
            
            # 提取 token 使用量（如果可用）
            usage_metadata = getattr(response, 'usage_metadata', None)
            prompt_tokens = getattr(usage_metadata, 'prompt_token_count', 0) if usage_metadata else 0
            completion_tokens = getattr(usage_metadata, 'candidates_token_count', 0) if usage_metadata else 0
            
            return LLMResponse(
                content=content,
                model=self.config.model_name,
                finish_reason="stop",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Google] 聊天请求失败: {e}")
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
        
        # 转换消息格式
        history = []
        system_instruction = ""
        
        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                history.append({"role": "user", "parts": [msg["content"]]})
            elif msg["role"] == "assistant":
                history.append({"role": "model", "parts": [msg["content"]]})
        
        try:
            import google.generativeai as genai
            
            generation_config = genai.GenerationConfig(
                temperature=temperature if temperature is not None else self.config.temperature,
                max_output_tokens=max_tokens or self.config.max_tokens,
            )
            
            if system_instruction:
                model = genai.GenerativeModel(
                    self.config.model_name,
                    system_instruction=system_instruction
                )
            else:
                model = self._client
            
            chat = model.start_chat(history=history[:-1] if history else [])
            
            last_message = history[-1]["parts"][0] if history else ""
            response = await chat.send_message_async(last_message, generation_config=generation_config)
            
            content = response.text if response.text else ""
            
            usage_metadata = getattr(response, 'usage_metadata', None)
            prompt_tokens = getattr(usage_metadata, 'prompt_token_count', 0) if usage_metadata else 0
            completion_tokens = getattr(usage_metadata, 'candidates_token_count', 0) if usage_metadata else 0
            
            return LLMResponse(
                content=content,
                model=self.config.model_name,
                finish_reason="stop",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                raw_response=response
            )
            
        except Exception as e:
            logger.error(f"[Google] 异步聊天请求失败: {e}")
            raise
    
    def get_langchain_llm(self) -> Any:
        """获取 LangChain LLM 实例"""
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        return ChatGoogleGenerativeAI(
            model=self.config.model_name,
            api_key=self.config.api_key,
            temperature=self.config.temperature,
        )
    
    def get_browser_use_llm(self) -> Any:
        """获取 Browser-Use LLM 实例"""
        return self.get_langchain_llm()

    def parse_json_response(self, content: str) -> dict:
        """
        Google (Gemini) JSON 解析

        Gemini 的特点：
        - Thinking 模型（gemini-2.0-flash-thinking-exp 等）会在响应中包含推理过程
        - 经常用 ```json 代码块包裹
        - 有时返回多个 JSON 候选，需要取第一个
        - 2.5 系列模型可能在 JSON 前加 "```json\n" 后加 "\n```"
        """
        if not content:
            raise ValueError("LLM 响应为空")

        text = content.strip()

        # 1. Gemini thinking 模型可能有类似推理标签的输出
        #    参考 openclaw: google-generative-ai 是 reasoning tag provider
        if "<think>" in text and "</think>" in text:
            parts = text.split("</think>", 1)
            text = parts[1].strip() if len(parts) >= 2 else text

        # 2. 剥离 markdown 代码块
        text = self._extract_json(text)

        # 3. 直接尝试
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 4. 使用 json_repair 库修复
        try:
            from json_repair import repair_json
            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict):
                logger.info(f"[Google] json_repair 成功修复 JSON ({len(text)} 字符)")
                return repaired
        except Exception as e:
            logger.debug(f"[Google] json_repair 失败: {e}")

        # 5. 回退到基类通用解析
        return super().parse_json_response(content)
