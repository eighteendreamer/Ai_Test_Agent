"""
压测引擎基类

定义所有压测引擎的统一接口
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import logging

from ..models import PressTestDSL, EngineType, TaskResultResponse

logger = logging.getLogger(__name__)


class BaseEngine(ABC):
    """压测引擎基类"""
    
    def __init__(self, task_id: int, dsl: PressTestDSL):
        """
        初始化引擎
        
        Args:
            task_id: 任务 ID
            dsl: 压测 DSL
        """
        self.task_id = task_id
        self.dsl = dsl
        self.logger = logging.getLogger(f"{self.__class__.__name__}[{task_id}]")
    
    @property
    @abstractmethod
    def engine_type(self) -> EngineType:
        """引擎类型"""
        pass
    
    @abstractmethod
    def generate_script(self) -> str:
        """
        生成压测脚本
        
        Returns:
            脚本内容
        """
        pass
    
    @abstractmethod
    def execute(self, script: str) -> Dict[str, Any]:
        """
        执行压测脚本
        
        Args:
            script: 脚本内容
            
        Returns:
            执行结果
        """
        pass
    
    @abstractmethod
    def parse_result(self, raw_result: Dict[str, Any]) -> TaskResultResponse:
        """
        解析执行结果
        
        Args:
            raw_result: 原始结果
            
        Returns:
            标准化的任务结果
        """
        pass
    
    def run(self) -> TaskResultResponse:
        """
        运行完整的压测流程
        
        Returns:
            任务结果
        """
        try:
            self.logger.info(f"开始生成 {self.engine_type} 脚本...")
            script = self.generate_script()
            
            self.logger.info(f"开始执行 {self.engine_type} 压测...")
            raw_result = self.execute(script)
            
            self.logger.info(f"解析 {self.engine_type} 结果...")
            result = self.parse_result(raw_result)
            
            self.logger.info(f"{self.engine_type} 压测完成")
            return result
            
        except Exception as e:
            self.logger.error(f"{self.engine_type} 压测失败: {e}")
            raise
    
    def validate_dsl(self) -> bool:
        """
        验证 DSL 是否适合当前引擎
        
        Returns:
            是否有效
        """
        return True
    
    def get_docker_command(self, script_path: str) -> list:
        """
        获取 Docker 执行命令
        
        Args:
            script_path: 脚本路径
            
        Returns:
            Docker 命令列表
        """
        raise NotImplementedError("子类必须实现 get_docker_command 方法")
