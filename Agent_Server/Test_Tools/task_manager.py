"""
测试任务管理器

支持任务的暂停、停止、恢复功能

作者: 程序员Eighteen
"""
import threading
from typing import Dict, Any, Optional
from enum import Enum
from datetime import datetime


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    ERROR = "error"


class TaskManager:
    """任务管理器"""

    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(TaskManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._tasks: Dict[int, Dict[str, Any]] = {}
        self._task_counter = 0
        self._initialized = True
    
    def create_task(self, task_type: str, description: str = "") -> int:
        """创建任务"""
        with self._lock:
            self._task_counter += 1
            task_id = self._task_counter
            
            self._tasks[task_id] = {
                "id": task_id,
                "type": task_type,
                "description": description,
                "status": TaskStatus.PENDING,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
                "progress": 0,
                "error": None,
                "pause_flag": threading.Event(),
                "stop_flag": threading.Event()
            }
            
            # 默认不暂停
            self._tasks[task_id]["pause_flag"].set()
            
            return task_id
    
    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        return self._tasks.get(task_id)
    
    def get_task_status(self, task_id: int) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        task = self._tasks.get(task_id)
        if not task:
            return None
        
        return {
            "id": task["id"],
            "type": task["type"],
            "description": task["description"],
            "status": task["status"].value,
            "progress": task["progress"],
            "error": task["error"],
            "created_at": task["created_at"].isoformat(),
            "updated_at": task["updated_at"].isoformat()
        }
    
    def update_task_status(
        self,
        task_id: int,
        status: TaskStatus = None,
        progress: int = None,
        error: str = None
    ):
        """更新任务状态"""
        task = self._tasks.get(task_id)
        if not task:
            return
        
        if status:
            task["status"] = status
        if progress is not None:
            task["progress"] = progress
        if error is not None:
            task["error"] = error
        
        task["updated_at"] = datetime.now()
    
    def pause_task(self, task_id: int):
        """暂停任务"""
        task = self._tasks.get(task_id)
        if task and task["status"] == TaskStatus.RUNNING:
            task["pause_flag"].clear()
            task["status"] = TaskStatus.PAUSED
            task["updated_at"] = datetime.now()
    
    def resume_task(self, task_id: int):
        """恢复任务"""
        task = self._tasks.get(task_id)
        if task and task["status"] == TaskStatus.PAUSED:
            task["pause_flag"].set()
            task["status"] = TaskStatus.RUNNING
            task["updated_at"] = datetime.now()
    
    def stop_task(self, task_id: int):
        """停止任务"""
        task = self._tasks.get(task_id)
        if task and task["status"] in (TaskStatus.RUNNING, TaskStatus.PAUSED):
            task["stop_flag"].set()
            task["pause_flag"].set()  # 确保不会卡在暂停
            task["status"] = TaskStatus.STOPPED
            task["updated_at"] = datetime.now()
    
    def should_stop(self, task_id: int) -> bool:
        """检查任务是否应该停止"""
        task = self._tasks.get(task_id)
        if not task:
            return True
        return task["stop_flag"].is_set()
    
    def wait_if_paused(self, task_id: int, timeout: float = None) -> bool:
        """如果任务暂停则等待"""
        task = self._tasks.get(task_id)
        if not task:
            return False
        return task["pause_flag"].wait(timeout)
    
    def get_all_tasks(self) -> list:
        """获取所有任务"""
        return [
            self.get_task_status(task_id)
            for task_id in self._tasks.keys()
        ]


# 全局单例
_task_manager = None


def get_task_manager() -> TaskManager:
    """获取任务管理器单例"""
    global _task_manager
    if _task_manager is None:
        _task_manager = TaskManager()
    return _task_manager
