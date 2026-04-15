"""
PressTest Agent - AI驱动的多引擎性能测试平台

基于 LLM 的自动化性能测试系统，支持：
- 自动解析测试用例
- 自动选择压测引擎（k6/Locust/JMeter）
- 自动生成压测脚本
- 自动执行压测任务
- 自动分析性能结果

作者: 程序员Eighteen
版本: 1.0
"""

__version__ = "1.0.0"
__author__ = "程序员Eighteen"

from .router import router

__all__ = ["router"]
