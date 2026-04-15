"""
一键测试 - 会话管理

管理一键测试的会话状态和消息历史
支持 Token 使用量追踪和循环检测状态
"""
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session

from database.connection import OneclickSession


# 状态机定义
# 新增状态：
#   feature_planning  → L2 功能规划中
#   atomic_planning   → L3 原子任务规划中
#   task_tree_ready   → 任务树已就绪（等待用户确认）
VALID_TRANSITIONS = {
    'init': ['analyzing'],
    'analyzing': ['page_scanned', 'feature_planning', 'cases_generated', 'failed'],
    'page_scanned': ['feature_planning', 'cases_generated', 'failed'],
    'feature_planning': ['atomic_planning', 'cases_generated', 'failed'],
    'atomic_planning': ['task_tree_ready', 'cases_generated', 'failed'],
    'task_tree_ready': ['confirmed', 'cases_generated', 'failed'],
    'cases_generated': ['confirmed', 'failed'],
    'confirmed': ['executing', 'failed'],
    'executing': ['completed', 'failed'],
    'completed': [],
    'failed': ['init'],  # 允许重试
}

# 内存中的会话运行时数据（不持久化到数据库）
# session_id → { "tokens_used": int, "loop_warnings": int, "model_switches": int }
_session_runtime: Dict[int, Dict[str, Any]] = {}


class SessionManager:
    """一键测试会话管理器"""

    @staticmethod
    def create_session(db: Session, user_input: str, project_id: int = None) -> OneclickSession:
        """创建新会话"""
        if project_id is None:
            raise ValueError("project_id is required when creating one-click test sessions")

        session = OneclickSession(
            user_input=user_input,
            status='init',
            project_id=project_id,
            messages=json.dumps([
                {"role": "user", "content": user_input, "time": datetime.now().isoformat()}
            ], ensure_ascii=False)
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        # 初始化运行时数据
        _session_runtime[session.id] = {
            "tokens_used": 0,
            "tokens_input": 0,
            "tokens_output": 0,
            "loop_warnings": 0,
            "loop_critical": 0,
            "model_switches": 0,
            "request_count": 0,
            "start_time": datetime.now().isoformat(),
        }

        return session

    @staticmethod
    def get_session(db: Session, session_id: int) -> Optional[OneclickSession]:
        """获取会话"""
        return db.query(OneclickSession).filter(OneclickSession.id == session_id).first()

    @staticmethod
    def update_status(db: Session, session: OneclickSession, new_status: str) -> bool:
        """更新会话状态（带状态机校验）"""
        current = session.status
        if new_status not in VALID_TRANSITIONS.get(current, []):
            print(f"[Session] ⚠️ 非法状态转换: {current} → {new_status}")
            return False
        session.status = new_status
        db.commit()
        return True

    @staticmethod
    def add_message(db: Session, session: OneclickSession, role: str, content: str, extra: Dict = None):
        """添加对话消息"""
        messages = json.loads(session.messages) if session.messages else []
        msg = {"role": role, "content": content, "time": datetime.now().isoformat()}
        if extra:
            msg.update(extra)
        messages.append(msg)
        session.messages = json.dumps(messages, ensure_ascii=False)
        db.commit()

    @staticmethod
    def get_messages(session: OneclickSession) -> List[Dict]:
        """获取对话消息"""
        if not session.messages:
            return []
        return json.loads(session.messages) if isinstance(session.messages, str) else session.messages

    @staticmethod
    def list_sessions(db: Session, page: int = 1, page_size: int = 20, project_id: int = None) -> Dict:
        """获取会话列表"""
        query = db.query(OneclickSession).order_by(OneclickSession.created_at.desc())
        
        # 项目过滤
        if project_id is not None:
            query = query.filter(OneclickSession.project_id == project_id)
        
        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()
        return {
            "total": total,
            "items": [
                {
                    "id": s.id,
                    "user_input": s.user_input,
                    "status": s.status,
                    "target_url": s.target_url,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                }
                for s in items
            ]
        }

    # ========== Token 追踪 ==========

    @staticmethod
    def track_tokens(session_id: int, prompt_tokens: int = 0, completion_tokens: int = 0):
        """追踪会话的 Token 使用量"""
        rt = _session_runtime.get(session_id)
        if rt is None:
            _session_runtime[session_id] = {
                "tokens_used": 0, "tokens_input": 0, "tokens_output": 0,
                "loop_warnings": 0, "loop_critical": 0, "model_switches": 0,
                "request_count": 0, "start_time": datetime.now().isoformat(),
            }
            rt = _session_runtime[session_id]

        total = prompt_tokens + completion_tokens
        rt["tokens_used"] += total
        rt["tokens_input"] += prompt_tokens
        rt["tokens_output"] += completion_tokens
        rt["request_count"] += 1

    @staticmethod
    def track_loop_event(session_id: int, level: str):
        """追踪循环检测事件"""
        rt = _session_runtime.get(session_id)
        if rt is None:
            return
        if level == "warning":
            rt["loop_warnings"] += 1
        elif level == "critical":
            rt["loop_critical"] += 1

    @staticmethod
    def track_model_switch(session_id: int):
        """追踪模型切换事件"""
        rt = _session_runtime.get(session_id)
        if rt is None:
            return
        rt["model_switches"] += 1

    @staticmethod
    def get_runtime_stats(session_id: int) -> Dict:
        """获取会话运行时统计"""
        return _session_runtime.get(session_id, {})

    @staticmethod
    def cleanup_runtime(session_id: int):
        """清理会话运行时数据"""
        _session_runtime.pop(session_id, None)
