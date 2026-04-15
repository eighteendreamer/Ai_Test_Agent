"""
任务树引擎 - 核心数据结构

将一键测试从「线性用例执行」升级为「分层任务树驱动执行」

TaskNode 层级：
- Level 1: 用户意图层（User Intent Layer）   → 整体测试目标
- Level 2: 功能规划层（Feature Planning Layer）→ 基于页面探索生成的功能模块
- Level 3: 原子执行层（Executable Atomic Task）→ 最小可执行测试单元（= 测试用例）

                用户目标（L1）
                  ├── 登录功能测试（L2）
                  │     ├── 正确账号密码登录（L3 = TestCase）
                  │     ├── 错误密码登录（L3）
                  │     └── SQL注入测试（L3）
                  └── 忘记密码功能（L2）
                        ├── 正常流程（L3）
                        └── 非法邮箱格式（L3）
"""
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


# ─────────────────────────────────────────────
# 状态常量
# ─────────────────────────────────────────────

class NodeStatus:
    PENDING   = "pending"    # 待确认
    CONFIRMED = "confirmed"  # 用户已确认
    SKIPPED   = "skipped"    # 用户跳过
    RUNNING   = "running"    # 执行中
    DONE      = "done"       # 执行完成（通过）
    FAILED    = "failed"     # 执行完成（失败）


# ─────────────────────────────────────────────
# TaskNode 数据结构
# ─────────────────────────────────────────────

@dataclass
class TaskNode:
    """任务节点（L1 / L2 / L3 通用）"""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    level: int = 1                          # 1 / 2 / 3
    name: str = ""                          # 节点名称
    description: str = ""                  # 描述
    parent_id: Optional[str] = None        # 父节点 ID
    status: str = NodeStatus.PENDING       # 当前状态
    children: List["TaskNode"] = field(default_factory=list)

    # 仅 L3 节点有测试用例
    test_case: Optional[Dict[str, Any]] = None

    # L2 节点附加信息
    feature_type: Optional[str] = None    # 功能类型（form/auth/table/upload 等）
    priority: str = "3"                   # 优先级

    # 执行结果（L3 有效）
    result: Optional[Dict[str, Any]] = None

    # ---- 便捷属性 ----

    @property
    def is_leaf(self) -> bool:
        return self.level == 3

    @property
    def checked(self) -> bool:
        """是否被选中执行（前端勾选状态）"""
        return self.status in (NodeStatus.CONFIRMED,)

    def to_dict(self) -> Dict:
        """序列化为 JSON 可传输的字典"""
        return {
            "id": self.id,
            "level": self.level,
            "name": self.name,
            "description": self.description,
            "parent_id": self.parent_id,
            "status": self.status,
            "feature_type": self.feature_type,
            "priority": self.priority,
            "test_case": self.test_case,
            "result": self.result,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "TaskNode":
        """从字典反序列化"""
        node = cls(
            id=data.get("id", str(uuid.uuid4())[:8]),
            level=data.get("level", 1),
            name=data.get("name", ""),
            description=data.get("description", ""),
            parent_id=data.get("parent_id"),
            status=data.get("status", NodeStatus.PENDING),
            feature_type=data.get("feature_type"),
            priority=data.get("priority", "3"),
            test_case=data.get("test_case"),
            result=data.get("result"),
        )
        node.children = [TaskNode.from_dict(c) for c in data.get("children", [])]
        return node


# ─────────────────────────────────────────────
# TaskTree - 任务树操作辅助
# ─────────────────────────────────────────────

class TaskTree:
    """任务树操作工具类"""

    def __init__(self, root: TaskNode):
        self.root = root

    # ── 构建 ──────────────────────────────────

    @classmethod
    def build_from_llm_output(cls, llm_json: Dict) -> "TaskTree":
        """
        从 LLM 输出的 JSON 构建任务树

        期望 llm_json 格式：
        {
          "name": "登录功能测试",
          "description": "...",
          "children": [
            {
              "name": "登录正常流程",
              "description": "...",
              "feature_type": "auth",
              "priority": "2",
              "children": [
                {
                  "name": "正确账号密码登录",
                  "description": "...",
                  "test_case": { "title":..., "steps":..., "expected":... }
                }
              ]
            }
          ]
        }
        """
        root = TaskNode(
            level=1,
            name=llm_json.get("name", "测试任务"),
            description=llm_json.get("description", ""),
        )
        for l2_data in llm_json.get("children", []):
            l2 = TaskNode(
                level=2,
                name=l2_data.get("name", ""),
                description=l2_data.get("description", ""),
                parent_id=root.id,
                feature_type=l2_data.get("feature_type"),
                priority=l2_data.get("priority", "3"),
            )
            for l3_data in l2_data.get("children", []):
                tc = l3_data.get("test_case") or {
                    "title": l3_data.get("name", ""),
                    "steps": l3_data.get("steps", []),
                    "expected": l3_data.get("expected", ""),
                    "module": l2.name,
                    "priority": l3_data.get("priority", l2.priority),
                    "case_type": l3_data.get("case_type", "功能测试"),
                    "need_browser": True,
                    "test_data": l3_data.get("test_data", {}),
                }
                l3 = TaskNode(
                    level=3,
                    name=l3_data.get("name", tc.get("title", "")),
                    description=l3_data.get("description", ""),
                    parent_id=l2.id,
                    priority=str(tc.get("priority", l2.priority)),
                    test_case=tc,
                )
                l2.children.append(l3)
            root.children.append(l2)
        return cls(root)

    # ── 查询 ──────────────────────────────────

    def get_all_l2(self) -> List[TaskNode]:
        """获取全部 L2 节点"""
        return self.root.children

    def get_all_l3(self) -> List[TaskNode]:
        """获取全部 L3 叶子节点（测试用例）"""
        nodes = []
        for l2 in self.root.children:
            nodes.extend(l2.children)
        return nodes

    def get_confirmed_l3(self) -> List[TaskNode]:
        """获取用户确认的 L3 节点"""
        return [n for n in self.get_all_l3() if n.status == NodeStatus.CONFIRMED]

    def get_confirmed_cases(self) -> List[Dict]:
        """获取确认的测试用例列表（兼容旧执行逻辑）"""
        cases = []
        for node in self.get_confirmed_l3():
            if node.test_case:
                cases.append(node.test_case)
        return cases

    def find_node(self, node_id: str) -> Optional[TaskNode]:
        """按 id 查找节点"""
        def _search(node: TaskNode) -> Optional[TaskNode]:
            if node.id == node_id:
                return node
            for child in node.children:
                found = _search(child)
                if found:
                    return found
            return None
        return _search(self.root)

    # ── 批量操作 ──────────────────────────────

    def confirm_all(self):
        """确认所有 L3 节点"""
        for node in self.get_all_l3():
            node.status = NodeStatus.CONFIRMED

    def confirm_l2(self, l2_id: str):
        """确认某个 L2 节点下所有 L3"""
        l2 = self.find_node(l2_id)
        if l2 and l2.level == 2:
            for l3 in l2.children:
                l3.status = NodeStatus.CONFIRMED

    def skip_l2(self, l2_id: str):
        """跳过某个 L2 节点"""
        l2 = self.find_node(l2_id)
        if l2 and l2.level == 2:
            l2.status = NodeStatus.SKIPPED
            for l3 in l2.children:
                l3.status = NodeStatus.SKIPPED

    def set_node_status(self, node_id: str, status: str):
        """设置节点状态"""
        node = self.find_node(node_id)
        if node:
            node.status = status

    def apply_user_selection(self, selections: Dict[str, bool]):
        """
        应用用户的勾选操作
        selections: { node_id: True/False }
        支持 L2 勾选（批量控制子节点）和 L3 单独勾选
        """
        for node_id, checked in selections.items():
            node = self.find_node(node_id)
            if not node:
                continue
            new_status = NodeStatus.CONFIRMED if checked else NodeStatus.PENDING
            if node.level == 2:
                node.status = new_status
                for l3 in node.children:
                    if l3.status != NodeStatus.SKIPPED:
                        l3.status = new_status
            elif node.level == 3:
                node.status = new_status

    # ── 统计 ──────────────────────────────────

    def stats(self) -> Dict:
        """统计信息"""
        all_l3 = self.get_all_l3()
        total = len(all_l3)
        confirmed = sum(1 for n in all_l3 if n.status == NodeStatus.CONFIRMED)
        done = sum(1 for n in all_l3 if n.status == NodeStatus.DONE)
        failed = sum(1 for n in all_l3 if n.status == NodeStatus.FAILED)
        skipped = sum(1 for n in all_l3 if n.status == NodeStatus.SKIPPED)
        return {
            "total": total,
            "confirmed": confirmed,
            "done": done,
            "failed": failed,
            "skipped": skipped,
            "l2_count": len(self.root.children),
        }

    # ── 序列化 ────────────────────────────────

    def to_dict(self) -> Dict:
        return self.root.to_dict()

    @classmethod
    def from_dict(cls, data: Dict) -> "TaskTree":
        return cls(TaskNode.from_dict(data))
