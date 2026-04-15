"""
LLM 模块使用示例

演示如何使用新的 LLM 模块

作者: 程序员Eighteen
"""

# ============================================
# 示例 1: 使用工厂创建 Provider
# ============================================

def example_create_provider():
    """使用工厂函数创建 LLM Provider"""
    from llm import create_llm_provider
    
    # 创建 DeepSeek Provider
    provider = create_llm_provider(
        provider="deepseek",
        model_name="deepseek-chat",
        api_key="your-api-key",
        base_url="https://api.deepseek.com/v1",
        temperature=0.0
    )
    
    # 发送聊天请求
    response = provider.chat([
        {"role": "system", "content": "你是一个专业的测试工程师"},
        {"role": "user", "content": "请帮我分析这个测试用例的问题"}
    ])
    
    print(f"响应内容: {response.content}")
    print(f"Token 使用: {response.total_tokens}")
    
    return response


# ============================================
# 示例 2: 获取 LangChain LLM
# ============================================

def example_langchain_llm():
    """获取 LangChain 格式的 LLM"""
    from llm import get_llm_model
    
    # 获取 OpenAI LLM
    llm = get_llm_model(
        provider="openai",
        model_name="gpt-4o",
        api_key="your-api-key",
        temperature=0.7
    )
    
    # 使用 LangChain 的方式调用
    from langchain_core.messages import HumanMessage
    response = llm.invoke([HumanMessage(content="Hello!")])
    
    print(f"响应: {response.content}")
    
    return llm


# ============================================
# 示例 3: 获取 Browser-Use LLM
# ============================================

def example_browser_use_llm():
    """获取 Browser-Use 格式的 LLM"""
    from llm import get_browser_use_llm
    
    # 获取 DeepSeek LLM（自动禁用结构化输出）
    llm = get_browser_use_llm(
        provider="deepseek",
        model_name="deepseek-chat",
        api_key="your-api-key"
    )
    
    return llm


# ============================================
# 示例 4: 使用配置管理器（从数据库获取激活模型）
# ============================================

def example_config_manager():
    """使用配置管理器获取激活的模型"""
    from llm import (
        get_active_llm_config,
        get_active_langchain_llm,
        get_active_browser_use_llm
    )
    
    # 获取配置
    config = get_active_llm_config()
    print(f"激活的模型: {config['model_name']}")
    print(f"Provider: {config['provider']}")
    
    # 获取 LangChain LLM
    langchain_llm = get_active_langchain_llm()
    
    # 获取 Browser-Use LLM
    browser_use_llm = get_active_browser_use_llm()
    
    return config, langchain_llm, browser_use_llm


# ============================================
# 示例 5: 使用 LLM 客户端（兼容旧接口）
# ============================================

def example_llm_client():
    """使用 LLM 客户端"""
    from llm import get_llm_client
    
    # 获取客户端
    client = get_llm_client()
    
    # 聊天
    response = client.chat([
        {"role": "user", "content": "你好"}
    ])
    print(f"聊天响应: {response}")
    
    # 生成测试用例
    result = client.generate_test_cases(
        requirement="测试登录功能，包括用户名密码验证",
        count=3
    )
    print(f"生成的测试用例: {result}")
    
    # 分析 Bug
    bug_result = client.analyze_bug(
        "测试步骤: 点击登录按钮\n预期结果: 跳转到首页\n实际结果: 显示错误提示"
    )
    print(f"Bug 分析: {bug_result}")
    
    return client


# ============================================
# 示例 6: 支持的 Provider 列表
# ============================================

def example_list_providers():
    """列出支持的 Provider"""
    from llm import get_supported_providers, get_provider_display_name, get_provider_models
    
    providers = get_supported_providers()
    print("支持的 Provider:")
    
    for provider in providers:
        display_name = get_provider_display_name(provider)
        models = get_provider_models(provider)
        print(f"\n  {provider} ({display_name}):")
        print(f"    模型: {', '.join(models[:3])}...")
    
    return providers


# ============================================
# 示例 7: 处理推理模型（DeepSeek R1）
# ============================================

def example_reasoning_model():
    """使用推理模型"""
    from llm import create_llm_provider, is_reasoning_model
    
    # 检查是否为推理模型
    is_r1 = is_reasoning_model("deepseek", "deepseek-reasoner")
    print(f"deepseek-reasoner 是推理模型: {is_r1}")
    
    # 创建 DeepSeek R1 Provider
    provider = create_llm_provider(
        provider="deepseek",
        model_name="deepseek-reasoner",
        api_key="your-api-key"
    )
    
    # 发送请求
    response = provider.chat([
        {"role": "user", "content": "请分析这个数学问题: 1+1=?"}
    ])
    
    # R1 模型会返回思考过程
    print(f"思考过程: {response.reasoning_content}")
    print(f"最终答案: {response.content}")
    
    return response


# ============================================
# 示例 8: 使用自定义 OpenAI 兼容服务
# ============================================

def example_custom_provider():
    """使用自定义 OpenAI 兼容服务"""
    from llm import create_llm_provider
    
    # 创建自定义 Provider（如本地部署的 LLM）
    provider = create_llm_provider(
        provider="custom",
        model_name="my-local-model",
        api_key="not-needed",
        base_url="http://localhost:8000/v1",
        temperature=0.0
    )
    
    response = provider.chat([
        {"role": "user", "content": "Hello!"}
    ])
    
    return response


# ============================================
# 示例 9: 在 Browser-Use Agent 中使用
# ============================================

async def example_browser_use_agent():
    """在 Browser-Use Agent 中使用新的 LLM 模块"""
    from llm import get_active_browser_use_llm, get_active_llm_config
    from llm.config import supports_structured_output
    
    # 获取激活的模型配置
    config = get_active_llm_config()
    provider = config['provider']
    
    # 获取 Browser-Use LLM
    llm = get_active_browser_use_llm()
    
    # 检查是否支持结构化输出
    use_structured = supports_structured_output(provider)
    
    print(f"使用模型: {config['model_name']}")
    print(f"支持结构化输出: {use_structured}")
    
    # 创建 Browser-Use Agent
    # from browser_use import Agent, BrowserSession
    # 
    # browser_session = BrowserSession()
    # agent = Agent(
    #     task="打开百度并搜索 Python",
    #     llm=llm,
    #     browser_session=browser_session,
    # )
    # 
    # await agent.run()
    
    return llm


# ============================================
# 主函数
# ============================================

if __name__ == "__main__":
    print("=" * 50)
    print("LLM 模块使用示例")
    print("=" * 50)
    
    # 运行示例 6: 列出支持的 Provider
    print("\n示例 6: 支持的 Provider 列表")
    example_list_providers()
