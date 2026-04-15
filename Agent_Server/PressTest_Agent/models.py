"""
PressTest Agent 数据模型

定义统一的压测 DSL 和数据结构
"""
from typing import Dict, List, Optional, Any, Literal
from pydantic import BaseModel, Field
from enum import Enum


class EngineType(str, Enum):
    """压测引擎类型"""
    K6 = "k6"
    LOCUST = "locust"
    JMETER = "jmeter"


class TestType(str, Enum):
    """测试类型"""
    API = "api"              # 单接口压测
    FLOW = "flow"            # 业务流程压测
    PROTOCOL = "protocol"    # 协议压测（TCP/WebSocket/数据库）


class LoadPattern(str, Enum):
    """负载模式"""
    CONSTANT = "constant"    # 恒定负载
    RAMP_UP = "ramp_up"      # 阶梯增长
    SPIKE = "spike"          # 尖峰测试
    STRESS = "stress"        # 压力测试


class TaskStatus(str, Enum):
    """任务状态"""
    CREATED = "created"
    WAITING = "waiting"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================
# 统一压测 DSL
# ============================================================

class LoadConfig(BaseModel):
    """负载配置"""
    vus: int = Field(..., description="虚拟用户数", ge=1)
    duration: str = Field(..., description="持续时间（如 1m, 30s）")
    pattern: LoadPattern = Field(LoadPattern.CONSTANT, description="负载模式")
    ramp_time: Optional[str] = Field(None, description="爬坡时间")
    stages: Optional[List[Dict[str, Any]]] = Field(None, description="阶段配置")


class RequestConfig(BaseModel):
    """请求配置"""
    method: str = Field(..., description="HTTP 方法")
    url: str = Field(..., description="请求 URL")
    headers: Optional[Dict[str, str]] = Field(None, description="请求头")
    body: Optional[Any] = Field(None, description="请求体")
    params: Optional[Dict[str, str]] = Field(None, description="查询参数")
    timeout: Optional[int] = Field(30, description="超时时间（秒）")


class ThresholdConfig(BaseModel):
    """阈值配置"""
    max_response_time: Optional[int] = Field(None, description="最大响应时间（ms）")
    max_error_rate: Optional[float] = Field(None, description="最大错误率（%）")
    min_rps: Optional[int] = Field(None, description="最小 RPS")


class PressTestDSL(BaseModel):
    """统一压测 DSL"""
    name: str = Field(..., description="测试名称")
    type: TestType = Field(..., description="测试类型")
    engine: Optional[EngineType] = Field(None, description="指定引擎（可选，由 AI 自动选择）")
    
    # 请求配置
    request: RequestConfig = Field(..., description="请求配置")
    
    # 负载配置
    load: LoadConfig = Field(..., description="负载配置")
    
    # 阈值配置
    thresholds: Optional[ThresholdConfig] = Field(None, description="阈值配置")
    
    # 高级配置
    setup: Optional[str] = Field(None, description="前置脚本")
    teardown: Optional[str] = Field(None, description="后置脚本")
    think_time: Optional[int] = Field(None, description="思考时间（秒）")
    
    # 元数据
    tags: Optional[List[str]] = Field(None, description="标签")
    description: Optional[str] = Field(None, description="描述")


# ============================================================
# API 请求/响应模型
# ============================================================

class CreateTaskRequest(BaseModel):
    """创建压测任务请求"""
    project_id: int = Field(1, description="项目 ID")
    name: str = Field(..., description="任务名称")
    description: Optional[str] = Field(None, description="任务描述")
    
    # 方式1：直接提供 DSL
    dsl: Optional[PressTestDSL] = Field(None, description="压测 DSL")
    
    # 方式2：提供测试用例，由 AI 解析
    test_case: Optional[str] = Field(None, description="测试用例描述")
    
    # 方式3：提供原始配置
    raw_config: Optional[Dict[str, Any]] = Field(None, description="原始配置")


class TaskResponse(BaseModel):
    """任务响应"""
    id: int
    name: str
    status: TaskStatus
    engine: Optional[EngineType]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    duration: Optional[int]
    error_message: Optional[str]


class TaskProgressResponse(BaseModel):
    """任务进度响应"""
    task_id: int
    status: TaskStatus
    progress: int = Field(..., ge=0, le=100, description="进度百分比")
    current_vus: Optional[int] = Field(None, description="当前虚拟用户数")
    current_rps: Optional[float] = Field(None, description="当前 RPS")
    elapsed_time: Optional[int] = Field(None, description="已运行时间（秒）")


class MetricsData(BaseModel):
    """性能指标数据"""
    timestamp: int
    rps: float = Field(..., description="每秒请求数")
    response_time_avg: float = Field(..., description="平均响应时间（ms）")
    response_time_p95: float = Field(..., description="P95 响应时间（ms）")
    response_time_p99: float = Field(..., description="P99 响应时间（ms）")
    error_rate: float = Field(..., description="错误率（%）")
    active_vus: int = Field(..., description="活跃虚拟用户数")


class TaskResultResponse(BaseModel):
    """任务结果响应"""
    task_id: int
    status: TaskStatus
    
    # 汇总指标
    total_requests: int
    total_errors: int
    duration: int
    
    # 性能指标
    avg_rps: float
    max_rps: float
    avg_response_time: float
    p95_response_time: float
    p99_response_time: float
    error_rate: float
    
    # 时序数据
    metrics: List[MetricsData]
    
    # AI 分析
    ai_analysis: Optional[str] = Field(None, description="AI 性能分析报告")
    recommendations: Optional[List[str]] = Field(None, description="优化建议")


class EngineSelectionResponse(BaseModel):
    """引擎选择响应"""
    recommended_engine: EngineType
    reason: str
    confidence: float = Field(..., ge=0, le=1, description="置信度")
