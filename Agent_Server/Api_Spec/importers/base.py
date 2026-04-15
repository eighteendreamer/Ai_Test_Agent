"""
API 文档导入器基类

定义统一的导入接口和数据结构

作者: 程序员Eighteen
"""
from typing import Dict, Any, List, Optional
from abc import ABC, abstractmethod


class Endpoint:
    """接口端点数据结构"""
    
    def __init__(
        self,
        method: str,
        path: str,
        summary: str = '',
        description: Optional[str] = None,
        params: Optional[str] = None,
        success_example: Optional[str] = None,
        error_example: Optional[str] = None,
        notes: Optional[str] = None
    ):
        self.method = method.upper()
        self.path = path
        self.summary = summary
        self.description = description
        self.params = params
        self.success_example = success_example
        self.error_example = error_example
        self.notes = notes
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'method': self.method,
            'path': self.path,
            'summary': self.summary,
            'description': self.description,
            'params': self.params,
            'success_example': self.success_example,
            'error_example': self.error_example,
            'notes': self.notes
        }


class BaseImporter(ABC):
    """API 文档导入器基类"""
    
    @abstractmethod
    def parse(self, content: Any) -> Dict[str, Any]:
        """
        解析 API 文档内容
        
        Args:
            content: 文档内容（dict, str 等）
            
        Returns:
            {
                'service_name': str,
                'endpoints': List[Dict],
                'metadata': Dict
            }
        """
        pass
    
    @staticmethod
    def detect(content: Any) -> float:
        """
        检测内容是否符合该格式
        
        Args:
            content: 文档内容
            
        Returns:
            置信度 (0.0 - 1.0)
        """
        return 0.0
