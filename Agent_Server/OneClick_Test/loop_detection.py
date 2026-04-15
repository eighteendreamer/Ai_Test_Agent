"""
工具循环检测模块

借鉴 OpenClaw 的 tool-loop-detection 机制：
- No-Progress 检测：同一操作重复执行无进展
- Ping-Pong 检测：两个操作交替执行
- 全局熔断器：超过最大阈值强制停止

适配 browser-use 的 Agent 执行场景

作者: 程序员Eighteen
版本: 1.0
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class LoopDetectionConfig:
    """循环检测配置"""
    enabled: bool = True
    warning_threshold: int = 3       # 警告阈值
    critical_threshold: int = 5      # 严重阈值（阻断）
    global_circuit_breaker: int = 8  # 全局熔断阈值
    history_window: int = 20         # 历史窗口大小


@dataclass
class ToolCallRecord:
    """工具调用记录"""
    action_type: str       # 操作类型: click, input, navigate, wait, done
    args_hash: str         # 参数哈希
    result_hash: str = ""  # 结果哈希
    timestamp: float = 0.0
    url: str = ""          # 当前页面 URL


@dataclass
class LoopDetectionResult:
    """检测结果"""
    stuck: bool = False
    level: str = ""        # "warning" | "critical"
    detector: str = ""     # 检测器名称
    count: int = 0
    message: str = ""


class LoopDetector:
    """
    循环检测器

    检测 browser-use Agent 执行过程中的循环行为：
    1. 同一元素重复点击（no-progress）
    2. 页面来回跳转（ping-pong）
    3. 同一输入框反复输入（generic-repeat）
    """

    def __init__(self, config: LoopDetectionConfig = None):
        self.config = config or LoopDetectionConfig()
        self.history: List[ToolCallRecord] = []
        self._warning_keys: set = set()  # 已发出警告的 key

    def reset(self):
        """重置检测状态"""
        self.history.clear()
        self._warning_keys.clear()

    @staticmethod
    def _hash_args(action_type: str, args: dict) -> str:
        """计算操作参数的哈希"""
        key = json.dumps({"type": action_type, "args": args}, sort_keys=True, default=str)
        return hashlib.md5(key.encode()).hexdigest()[:12]

    @staticmethod
    def _hash_result(result: str) -> str:
        """计算结果哈希"""
        if not result:
            return ""
        return hashlib.md5(result.encode()).hexdigest()[:12]

    def record_action(
        self,
        action_type: str,
        args: dict,
        result: str = "",
        url: str = ""
    ):
        """记录一次操作"""
        record = ToolCallRecord(
            action_type=action_type,
            args_hash=self._hash_args(action_type, args),
            result_hash=self._hash_result(result),
            timestamp=time.time(),
            url=url,
        )
        self.history.append(record)

        # 保持窗口大小
        if len(self.history) > self.config.history_window * 2:
            self.history = self.history[-self.config.history_window:]

    def detect(self, action_type: str, args: dict) -> LoopDetectionResult:
        """
        检测当前操作是否构成循环

        在执行操作前调用，返回检测结果
        """
        if not self.config.enabled:
            return LoopDetectionResult()

        current_hash = self._hash_args(action_type, args)

        # 1. 全局熔断器
        result = self._check_global_circuit_breaker(action_type, current_hash)
        if result.stuck:
            return result

        # 2. No-Progress 检测
        result = self._check_no_progress(action_type, current_hash)
        if result.stuck:
            return result

        # 3. Ping-Pong 检测
        result = self._check_ping_pong(current_hash)
        if result.stuck:
            return result

        # 4. URL 循环检测（页面来回跳转）
        if action_type == "navigate":
            result = self._check_url_loop(args.get("url", ""))
            if result.stuck:
                return result

        return LoopDetectionResult()

    def _check_global_circuit_breaker(
        self, action_type: str, current_hash: str
    ) -> LoopDetectionResult:
        """全局熔断器：同一操作重复次数超过阈值"""
        count = sum(
            1 for h in self.history
            if h.action_type == action_type and h.args_hash == current_hash
        )

        if count >= self.config.global_circuit_breaker:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="global_circuit_breaker",
                count=count,
                message=(
                    f"🚨 全局熔断: {action_type} 操作已重复 {count} 次，"
                    f"参数完全相同。强制停止以防止无限循环。"
                ),
            )
        return LoopDetectionResult()

    def _check_no_progress(
        self, action_type: str, current_hash: str
    ) -> LoopDetectionResult:
        """No-Progress 检测：同一操作+同一结果连续出现"""
        if not self.history:
            return LoopDetectionResult()

        # 从最近的记录往回看，统计连续相同操作+相同结果的次数
        streak = 0
        latest_result = None
        for record in reversed(self.history):
            if record.action_type == action_type and record.args_hash == current_hash:
                if latest_result is None:
                    latest_result = record.result_hash
                if record.result_hash == latest_result:
                    streak += 1
                else:
                    break
            else:
                break

        if streak >= self.config.critical_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="no_progress",
                count=streak,
                message=(
                    f"🚨 无进展循环: {action_type} 操作连续 {streak} 次执行相同参数且结果不变。"
                    f"任务可能卡住，强制停止。"
                ),
            )

        if streak >= self.config.warning_threshold:
            warning_key = f"noprog:{action_type}:{current_hash}"
            if warning_key not in self._warning_keys:
                self._warning_keys.add(warning_key)
                return LoopDetectionResult(
                    stuck=True,
                    level="warning",
                    detector="no_progress",
                    count=streak,
                    message=(
                        f"⚠️ 警告: {action_type} 操作已连续 {streak} 次无进展。"
                        f"请尝试不同的操作策略。"
                    ),
                )

        return LoopDetectionResult()

    def _check_ping_pong(self, current_hash: str) -> LoopDetectionResult:
        """Ping-Pong 检测：两个操作交替执行"""
        if len(self.history) < 4:
            return LoopDetectionResult()

        recent = self.history[-6:]  # 看最近 6 条
        if len(recent) < 4:
            return LoopDetectionResult()

        # 检查 A-B-A-B 模式
        hashes = [r.args_hash for r in recent]
        ping_pong_count = 0
        for i in range(len(hashes) - 2):
            if hashes[i] == hashes[i + 2] and hashes[i] != hashes[i + 1]:
                ping_pong_count += 1

        if ping_pong_count >= self.config.critical_threshold - 1:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="ping_pong",
                count=ping_pong_count,
                message=(
                    f"🚨 乒乓循环: 检测到操作交替重复 {ping_pong_count} 次。"
                    f"任务陷入来回切换，强制停止。"
                ),
            )

        if ping_pong_count >= self.config.warning_threshold - 1:
            return LoopDetectionResult(
                stuck=True,
                level="warning",
                detector="ping_pong",
                count=ping_pong_count,
                message=(
                    f"⚠️ 警告: 检测到操作交替重复 {ping_pong_count} 次，"
                    f"可能陷入乒乓循环。请改变策略。"
                ),
            )

        return LoopDetectionResult()

    def _check_url_loop(self, target_url: str) -> LoopDetectionResult:
        """URL 循环检测：页面来回跳转"""
        if not target_url:
            return LoopDetectionResult()

        # 统计最近的 navigate 操作中，目标 URL 出现的次数
        recent_navigates = [
            r for r in self.history[-10:]
            if r.action_type == "navigate"
        ]

        url_count = sum(
            1 for r in recent_navigates
            if target_url in r.url or r.url in target_url
        )

        if url_count >= self.config.critical_threshold:
            return LoopDetectionResult(
                stuck=True,
                level="critical",
                detector="url_loop",
                count=url_count,
                message=(
                    f"🚨 URL 循环: 页面 {target_url[:60]} 已被导航 {url_count} 次。"
                    f"可能陷入重定向循环。"
                ),
            )

        return LoopDetectionResult()

    def get_stats(self) -> Dict:
        """获取检测统计"""
        if not self.history:
            return {"total_actions": 0, "unique_actions": 0, "warnings": 0}

        unique = set((r.action_type, r.args_hash) for r in self.history)
        return {
            "total_actions": len(self.history),
            "unique_actions": len(unique),
            "warnings": len(self._warning_keys),
            "history_window": self.config.history_window,
        }
