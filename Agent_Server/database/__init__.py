"""
数据库模块

提供数据库连接和模型定义
"""
from .connection import (
    # 数据库连接
    engine,
    SessionLocal,
    Base,
    get_db,
    get_db_session,
    init_db,
    
    # 数据模型
    ExecutionCase,
    ExecutionBatch,
    TestRecord,
    TestReport,
    BugReport,
    LLMModel,
    ModelProvider,
    Contact,
    EmailRecord,
    EmailConfig,
    
    # 别名（兼容旧代码）
    TestCase,
    TestResult,
)

__all__ = [
    # 连接
    "engine",
    "SessionLocal",
    "Base",
    "get_db",
    "get_db_session",
    "init_db",
    
    # 模型
    "ExecutionCase",
    "ExecutionBatch",
    "TestRecord",
    "TestReport",
    "BugReport",
    "LLMModel",
    "ModelProvider",
    "Contact",
    "EmailRecord",
    "EmailConfig",
    
    # 别名
    "TestCase",
    "TestResult",
]
