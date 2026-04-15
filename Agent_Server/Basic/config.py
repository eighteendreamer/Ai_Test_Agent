"""
应用配置模块
"""
import os
from dotenv import load_dotenv

# 加载环境变量 - .env 文件在 Agent_Server 目录下
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)


class Config:
    """应用配置类"""
    
    # 服务器配置 - 必须从环境变量获取
    HOST = os.getenv('HOST')
    PORT = os.getenv('PORT')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    
    # 验证必需的环境变量
    if not HOST:
        raise ValueError("服务器配置缺失！请在 .env 文件中配置 HOST")
    if not PORT:
        raise ValueError("服务器配置缺失！请在 .env 文件中配置 PORT")
    
    PORT = int(PORT)
    
    # CORS 配置
    CORS_ORIGINS_STR = os.getenv('CORS_ORIGINS')
    if not CORS_ORIGINS_STR:
        raise ValueError("CORS 配置缺失！请在 .env 文件中配置 CORS_ORIGINS")
    CORS_ORIGINS = CORS_ORIGINS_STR.split(',')
    
    # 应用信息
    APP_TITLE = "AI Test Agent API"
    APP_VERSION = "2.0.0"
    APP_DESCRIPTION = """
    AI 自动化测试平台 API
    
    ## 功能特性
    
    - 🤖 **智能测试用例生成** - 基于需求自动生成测试用例
    - 🌐 **Browser-Use 自动化执行** - 使用 AI Agent 执行浏览器测试
    - 📊 **测试报告生成** - 自动生成详细的测试报告
    - 🐛 **Bug 智能分析** - 自动分析测试失败原因
    - 🔧 **多模型支持** - 支持 OpenAI、DeepSeek、Claude 等多种大模型
    
    ## 使用流程
    
    1. 在模型管理中配置并激活 LLM 模型
    2. 创建或导入测试用例
    3. 执行测试（单条或批量）
    4. 查看测试报告和 Bug 分析
    """


config = Config()
