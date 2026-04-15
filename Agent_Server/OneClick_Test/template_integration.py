"""
模板生成集成模块

将模板 + LLM 混合生成方式集成到一键测试服务中

使用方式：
1. 在 .env 中设置 USE_TEMPLATE_GENERATION=true
2. 在 OneClick 服务中调用 generate_with_template()
"""
import os
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


async def generate_with_template(
    page_capabilities: Dict[str, Any],
    user_intent: str = "",
    fallback_to_llm: bool = True,
) -> List[Dict[str, Any]]:
    """
    使用模板 + LLM 混合方式生成测试用例
    
    Args:
        page_capabilities: 页面能力字典
        user_intent: 用户意图描述
        fallback_to_llm: 如果模板生成失败，是否降级为纯 LLM 生成
    
    Returns:
        测试用例列表
    """
    from Build_Use_case.template_generator import get_template_generator
    from llm import get_llm_client
    
    try:
        # 1. 尝试模板生成
        generator = get_template_generator()
        llm_client = get_llm_client()
        
        test_cases = await generator.generate_from_page_capabilities(
            page_capabilities=page_capabilities,
            user_intent=user_intent,
            llm_client=llm_client,
        )
        
        if test_cases:
            logger.info(f"[TemplateGen] ✅ 使用模板生成了 {len(test_cases)} 条用例")
            return test_cases
        
        # 2. 模板生成失败，降级为纯 LLM
        if fallback_to_llm:
            logger.warning("[TemplateGen] 模板生成失败，降级为纯 LLM 生成")
            return await _fallback_llm_generation(page_capabilities, user_intent, llm_client)
        
        return []
    
    except Exception as e:
        logger.error(f"[TemplateGen] 模板生成异常: {e}")
        if fallback_to_llm:
            return await _fallback_llm_generation(page_capabilities, user_intent, llm_client)
        return []


async def _fallback_llm_generation(
    page_capabilities: Dict[str, Any],
    user_intent: str,
    llm_client,
) -> List[Dict[str, Any]]:
    """降级为纯 LLM 生成（原有方式）"""
    # 这里可以调用原有的 LLM 生成逻辑
    # 暂时返回空列表，实际使用时需要实现
    logger.info("[TemplateGen] 执行降级 LLM 生成")
    return []


def should_use_template_generation() -> bool:
    """判断是否启用模板生成"""
    return os.getenv("USE_TEMPLATE_GENERATION", "false").lower() == "true"
