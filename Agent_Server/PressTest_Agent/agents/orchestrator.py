"""
压测任务调度器

负责：
1. 解析测试用例（通过 LLM）
2. 选择最优引擎
3. 调度执行
4. 结果分析
"""
import logging
from typing import Optional, Dict, Any
import json

from ..models import (
    PressTestDSL, EngineType, TestType, TaskResultResponse,
    EngineSelectionResponse, CreateTaskRequest
)
from .k6_agent import K6Agent
from .locust_agent import LocustAgent
from .jmeter_agent import JMeterAgent
from .base import BaseEngine

logger = logging.getLogger(__name__)


class PressTestOrchestrator:
    """压测任务调度器"""
    
    def __init__(self, llm_provider=None):
        """
        初始化调度器
        
        Args:
            llm_provider: LLM Provider 实例（可选）
        """
        self.llm_provider = llm_provider
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def parse_test_case(self, test_case: str) -> PressTestDSL:
        """
        使用 LLM 解析测试用例，生成统一 DSL
        
        Args:
            test_case: 测试用例描述
            
        Returns:
            压测 DSL
        """
        if not self.llm_provider:
            raise ValueError("需要 LLM Provider 才能解析测试用例")
        
        prompt = f"""你是性能测试专家，请根据以下测试用例生成压测配置。

测试用例：
{test_case}

要求：
1. 分析测试类型（api/flow/protocol）
2. 提取请求信息（method, url, headers, body）
3. 确定负载参数（vus, duration）
4. 输出标准 JSON 格式

输出格式：
{{
  "name": "测试名称",
  "type": "api|flow|protocol",
  "request": {{
    "method": "GET|POST|PUT|DELETE",
    "url": "完整URL",
    "headers": {{}},
    "body": {{}}
  }},
  "load": {{
    "vus": 虚拟用户数,
    "duration": "持续时间（如1m, 30s）",
    "pattern": "constant|ramp_up|spike|stress"
  }},
  "thresholds": {{
    "max_response_time": 最大响应时间ms,
    "max_error_rate": 最大错误率%
  }}
}}

只返回 JSON，不要其他说明。
"""
        
        try:
            response = self.llm_provider.chat([
                {"role": "user", "content": prompt}
            ])
            
            # 解析 JSON
            content = response.content.strip()
            dsl_dict = self.llm_provider.parse_json_response(content)
            
            # 转换为 PressTestDSL
            dsl = PressTestDSL(**dsl_dict)
            
            self.logger.info(f"成功解析测试用例: {dsl.name}")
            return dsl
            
        except Exception as e:
            self.logger.error(f"解析测试用例失败: {e}")
            raise
    
    def select_engine(self, dsl: PressTestDSL) -> EngineSelectionResponse:
        """
        选择最优压测引擎
        
        Args:
            dsl: 压测 DSL
            
        Returns:
            引擎选择结果
        """
        # 如果 DSL 中已指定引擎，直接使用
        if dsl.engine:
            return EngineSelectionResponse(
                recommended_engine=dsl.engine,
                reason="用户指定",
                confidence=1.0
            )
        
        # 基于规则的引擎选择
        if dsl.type == TestType.API:
            # 单接口压测 → k6
            return EngineSelectionResponse(
                recommended_engine=EngineType.K6,
                reason="单接口压测，k6 性能最优",
                confidence=0.9
            )
        
        elif dsl.type == TestType.FLOW:
            # 业务流程压测 → Locust
            return EngineSelectionResponse(
                recommended_engine=EngineType.LOCUST,
                reason="业务流程压测，Locust 更灵活",
                confidence=0.85
            )
        
        elif dsl.type == TestType.PROTOCOL:
            # 协议压测 → JMeter
            return EngineSelectionResponse(
                recommended_engine=EngineType.JMETER,
                reason="协议压测，JMeter 支持最全面",
                confidence=0.8
            )
        
        # 默认使用 k6
        return EngineSelectionResponse(
            recommended_engine=EngineType.K6,
            reason="默认选择",
            confidence=0.5
        )
    
    def select_engine_with_llm(self, dsl: PressTestDSL) -> EngineSelectionResponse:
        """
        使用 LLM 智能选择引擎
        
        Args:
            dsl: 压测 DSL
            
        Returns:
            引擎选择结果
        """
        if not self.llm_provider:
            return self.select_engine(dsl)
        
        prompt = f"""你是性能测试专家，请为以下压测场景选择最优的压测引擎。

压测配置：
- 测试类型: {dsl.type}
- 请求方法: {dsl.request.method}
- 目标 URL: {dsl.request.url}
- 虚拟用户数: {dsl.load.vus}
- 持续时间: {dsl.load.duration}

可选引擎：
1. k6: 适合 HTTP 接口高并发压测，性能最优
2. Locust: 适合业务流程压测（登录/下单），Python 编写灵活
3. JMeter: 适合协议压测（TCP/WebSocket/数据库），功能最全

请输出 JSON 格式：
{{
  "recommended_engine": "k6|locust|jmeter",
  "reason": "选择理由",
  "confidence": 0.0-1.0
}}

只返回 JSON，不要其他说明。
"""
        
        try:
            response = self.llm_provider.chat([
                {"role": "user", "content": prompt}
            ])
            
            content = response.content.strip()
            result_dict = self.llm_provider.parse_json_response(content)
            
            return EngineSelectionResponse(**result_dict)
            
        except Exception as e:
            self.logger.warning(f"LLM 引擎选择失败，使用规则选择: {e}")
            return self.select_engine(dsl)
    
    def create_engine(self, task_id: int, dsl: PressTestDSL, engine_type: EngineType) -> BaseEngine:
        """
        创建引擎实例
        
        Args:
            task_id: 任务 ID
            dsl: 压测 DSL
            engine_type: 引擎类型
            
        Returns:
            引擎实例
        """
        if engine_type == EngineType.K6:
            return K6Agent(task_id, dsl)
        elif engine_type == EngineType.LOCUST:
            return LocustAgent(task_id, dsl)
        elif engine_type == EngineType.JMETER:
            return JMeterAgent(task_id, dsl)
        else:
            raise ValueError(f"不支持的引擎类型: {engine_type}")
    
    def dispatch(self, task_id: int, dsl: PressTestDSL) -> TaskResultResponse:
        """
        调度执行压测任务
        
        Args:
            task_id: 任务 ID
            dsl: 压测 DSL
            
        Returns:
            任务结果
        """
        try:
            # 选择引擎
            selection = self.select_engine(dsl)
            self.logger.info(f"选择引擎: {selection.recommended_engine} (置信度: {selection.confidence})")
            self.logger.info(f"选择理由: {selection.reason}")
            
            # 创建引擎
            engine = self.create_engine(task_id, dsl, selection.recommended_engine)
            
            # 执行压测
            result = engine.run()
            
            return result
            
        except Exception as e:
            self.logger.error(f"调度执行失败: {e}")
            raise
    
    def analyze_result(self, result: TaskResultResponse) -> Dict[str, Any]:
        """
        使用 LLM 分析压测结果
        
        Args:
            result: 任务结果
            
        Returns:
            分析报告
        """
        if not self.llm_provider:
            return {
                "analysis": "需要 LLM Provider 才能进行智能分析",
                "recommendations": []
            }
        
        prompt = f"""你是性能测试专家，请分析以下压测结果并给出优化建议。

压测结果：
- 总请求数: {result.total_requests}
- 总错误数: {result.total_errors}
- 持续时间: {result.duration}秒
- 平均 RPS: {result.avg_rps:.2f}
- 最大 RPS: {result.max_rps:.2f}
- 平均响应时间: {result.avg_response_time:.2f}ms
- P95 响应时间: {result.p95_response_time:.2f}ms
- P99 响应时间: {result.p99_response_time:.2f}ms
- 错误率: {result.error_rate:.2f}%

请输出 JSON 格式：
{{
  "analysis": "性能分析总结",
  "bottlenecks": ["瓶颈1", "瓶颈2"],
  "recommendations": ["建议1", "建议2"],
  "score": 0-100分
}}

只返回 JSON，不要其他说明。
"""
        
        try:
            response = self.llm_provider.chat([
                {"role": "user", "content": prompt}
            ])
            
            content = response.content.strip()
            analysis = self.llm_provider.parse_json_response(content)
            
            # 更新结果
            result.ai_analysis = analysis.get("analysis")
            result.recommendations = analysis.get("recommendations", [])
            
            return analysis
            
        except Exception as e:
            self.logger.error(f"结果分析失败: {e}")
            return {
                "analysis": f"分析失败: {e}",
                "recommendations": []
            }
