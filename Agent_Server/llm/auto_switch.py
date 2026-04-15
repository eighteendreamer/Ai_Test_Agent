"""
模型自动切换模块 (Auth Profile Rotation)

借鉴 OpenClaw 的 Auth Profile 轮换机制：
- 维护多个模型/API Key 的健康状态
- 当遇到限流(429)/认证失败/超时时，自动切换到下一个可用模型
- 带冷却检测：失败的模型进入冷却期，冷却结束后自动恢复
- Token 使用量统计和利用率追踪

作者: 程序员Eighteen
版本: 2.0
"""
import time
import logging
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from .config import is_primary_model_only_mode

logger = logging.getLogger(__name__)


class FailureReason(str, Enum):
    """失败原因分类"""
    RATE_LIMIT = "rate_limit"        # 429 限流
    AUTH_FAILURE = "auth_failure"     # 认证失败 (401/403)
    TIMEOUT = "timeout"              # 超时
    SERVER_ERROR = "server_error"    # 服务端错误 (500+)
    QUOTA_EXCEEDED = "quota_exceeded"  # 配额耗尽
    CONTEXT_OVERFLOW = "context_overflow"  # 上下文溢出
    UNKNOWN = "unknown"


@dataclass
class ModelProfile:
    """模型配置档案（对应数据库 llm_models 表的一行）"""
    model_id: int
    model_name: str
    provider: str
    api_key: str
    base_url: str
    priority: int = 1
    utilization: int = 100  # 利用率百分比

    # 运行时状态（不持久化）
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_failure_reason: Optional[FailureReason] = None
    cooldown_until: float = 0.0  # 冷却结束时间戳
    consecutive_failures: int = 0
    last_success_time: float = 0.0
    total_requests: int = 0
    total_tokens_used: int = 0

    @property
    def is_cooling_down(self) -> bool:
        return time.time() < self.cooldown_until

    @property
    def is_available(self) -> bool:
        return not self.is_cooling_down and self.utilization > 0

    @property
    def cooldown_remaining(self) -> float:
        remaining = self.cooldown_until - time.time()
        return max(0, remaining)


# 冷却时间配置（秒）
COOLDOWN_CONFIG = {
    FailureReason.RATE_LIMIT: {
        "base": 60,       # 基础冷却 60 秒
        "max": 600,       # 最大冷却 10 分钟
        "multiplier": 2,  # 指数退避倍数
    },
    FailureReason.AUTH_FAILURE: {
        "base": 300,      # 认证失败冷却 5 分钟
        "max": 3600,      # 最大 1 小时
        "multiplier": 3,
    },
    FailureReason.TIMEOUT: {
        "base": 30,
        "max": 300,
        "multiplier": 2,
    },
    FailureReason.SERVER_ERROR: {
        "base": 30,
        "max": 300,
        "multiplier": 2,
    },
    FailureReason.QUOTA_EXCEEDED: {
        "base": 600,      # 配额耗尽冷却 10 分钟
        "max": 7200,      # 最大 2 小时
        "multiplier": 3,
    },
    FailureReason.CONTEXT_OVERFLOW: {
        "base": 0,        # 上下文溢出不需要冷却（换模型即可）
        "max": 0,
        "multiplier": 1,
    },
    FailureReason.UNKNOWN: {
        "base": 30,
        "max": 300,
        "multiplier": 2,
    },
}


def classify_failure_reason(error: Exception) -> FailureReason:
    """
    根据异常类型分类失败原因
    借鉴 OpenClaw 的 classifyFailoverReason
    """
    error_msg = str(error).lower()

    # 429 限流
    if any(kw in error_msg for kw in [
        "429", "rate limit", "rate_limit", "too many requests",
        "quota", "exceeded", "throttl"
    ]):
        if any(kw in error_msg for kw in ["quota", "exceeded", "billing"]):
            return FailureReason.QUOTA_EXCEEDED
        return FailureReason.RATE_LIMIT

    # 认证失败
    if any(kw in error_msg for kw in [
        "401", "403", "unauthorized", "forbidden",
        "invalid api key", "invalid_api_key", "authentication"
    ]):
        return FailureReason.AUTH_FAILURE

    # 超时
    if any(kw in error_msg for kw in [
        "timeout", "timed out", "time out", "deadline"
    ]):
        return FailureReason.TIMEOUT

    # 服务端错误
    if any(kw in error_msg for kw in [
        "500", "502", "503", "504", "internal server error",
        "bad gateway", "service unavailable", "gateway timeout"
    ]):
        return FailureReason.SERVER_ERROR

    # 上下文溢出
    if any(kw in error_msg for kw in [
        "context length", "context_length", "token limit",
        "maximum context", "too long", "max_tokens"
    ]):
        return FailureReason.CONTEXT_OVERFLOW

    return FailureReason.UNKNOWN


def _calculate_cooldown(reason: FailureReason, consecutive_failures: int) -> float:
    """计算冷却时间（指数退避）"""
    config = COOLDOWN_CONFIG.get(reason, COOLDOWN_CONFIG[FailureReason.UNKNOWN])
    base = config["base"]
    max_cd = config["max"]
    multiplier = config["multiplier"]

    if base == 0:
        return 0

    cooldown = base * (multiplier ** min(consecutive_failures - 1, 5))
    return min(cooldown, max_cd)


class ModelAutoSwitcher:
    """
    模型自动切换器

    核心逻辑（借鉴 OpenClaw）：
    1. 按优先级维护模型列表
    2. 请求失败时标记失败、计算冷却时间
    3. 自动切换到下一个可用模型
    4. 请求成功时标记恢复
    5. 所有模型都不可用时，选择冷却时间最短的等待
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._profiles: Dict[int, ModelProfile] = {}
        self._current_model_id: Optional[int] = None
        self._enabled: bool = not is_primary_model_only_mode()
        self._switch_history: List[Dict] = []  # 切换历史记录
        self._lock = asyncio.Lock()
        self._initialized = True
        logger.info("[AutoSwitch] 模型自动切换器已初始化")

    def load_profiles_from_db(self, db=None):
        """从数据库加载所有模型配置"""
        close_db = False
        if db is None:
            from database.connection import SessionLocal
            db = SessionLocal()
            close_db = True

        try:
            from database.connection import LLMModel
            models = db.query(LLMModel).order_by(LLMModel.priority).all()

            for model in models:
                profile = ModelProfile(
                    model_id=model.id,
                    model_name=model.model_name,
                    provider=model.provider or "openai",
                    api_key=model.api_key,
                    base_url=model.base_url or "",
                    priority=model.priority or 1,
                    utilization=model.utilization or 100,
                )
                # 保留已有的运行时状态
                if model.id in self._profiles:
                    old = self._profiles[model.id]
                    profile.failure_count = old.failure_count
                    profile.last_failure_time = old.last_failure_time
                    profile.last_failure_reason = old.last_failure_reason
                    profile.cooldown_until = old.cooldown_until
                    profile.consecutive_failures = old.consecutive_failures
                    profile.last_success_time = old.last_success_time
                    profile.total_requests = old.total_requests
                    profile.total_tokens_used = old.total_tokens_used

                self._profiles[model.id] = profile

                if model.is_active == 1:
                    self._current_model_id = model.id

            logger.info(
                f"[AutoSwitch] 已加载 {len(self._profiles)} 个模型配置, "
                f"当前活动: {self._current_model_id}"
            )
        except Exception as e:
            logger.error(f"[AutoSwitch] 加载模型配置失败: {e}")
        finally:
            if close_db:
                db.close()

    @property
    def enabled(self) -> bool:
        return self._enabled and not is_primary_model_only_mode()

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = bool(value)
        effective = self.enabled
        if value and not effective:
            logger.info("[AutoSwitch] 主模型锁定模式开启，自动切换保持关闭")
        else:
            logger.info(f"[AutoSwitch] 自动切换已{'开启' if effective else '关闭'}")

    @property
    def current_profile(self) -> Optional[ModelProfile]:
        if self._current_model_id is None:
            return None
        return self._profiles.get(self._current_model_id)

    def get_all_profiles_status(self) -> List[Dict]:
        """获取所有模型的状态信息"""
        result = []
        for mid, p in sorted(self._profiles.items(), key=lambda x: x[1].priority):
            result.append({
                "model_id": p.model_id,
                "model_name": p.model_name,
                "provider": p.provider,
                "priority": p.priority,
                "utilization": p.utilization,
                "is_current": mid == self._current_model_id,
                "is_available": p.is_available,
                "is_cooling_down": p.is_cooling_down,
                "cooldown_remaining": round(p.cooldown_remaining, 1),
                "failure_count": p.failure_count,
                "consecutive_failures": p.consecutive_failures,
                "last_failure_reason": p.last_failure_reason.value if p.last_failure_reason else None,
                "total_requests": p.total_requests,
                "total_tokens_used": p.total_tokens_used,
            })
        return result

    def get_switch_history(self, limit: int = 20) -> List[Dict]:
        """获取切换历史"""
        return self._switch_history[-limit:]

    def mark_failure(self, model_id: int, reason: FailureReason) -> Optional[int]:
        """
        标记模型失败，返回切换到的新模型 ID（如果发生了切换）

        借鉴 OpenClaw 的 markAuthProfileFailure
        """
        profile = self._profiles.get(model_id)
        if not profile:
            return None

        profile.failure_count += 1
        profile.consecutive_failures += 1
        profile.last_failure_time = time.time()
        profile.last_failure_reason = reason

        # 计算冷却时间
        cooldown_seconds = _calculate_cooldown(reason, profile.consecutive_failures)
        if cooldown_seconds > 0:
            profile.cooldown_until = time.time() + cooldown_seconds

        logger.warning(
            f"[AutoSwitch] 模型 {profile.model_name}(ID={model_id}) 失败: "
            f"reason={reason.value}, consecutive={profile.consecutive_failures}, "
            f"cooldown={cooldown_seconds:.0f}s"
        )

        # 如果自动切换开启且当前模型就是失败的模型，尝试切换
        if self.enabled and model_id == self._current_model_id:
            next_id = self._find_next_available()
            if next_id and next_id != model_id:
                return self._do_switch(next_id, reason=f"failover from {profile.model_name}")
        return None

    def mark_success(self, model_id: int, tokens_used: int = 0):
        """
        标记模型成功

        借鉴 OpenClaw 的 markAuthProfileGood
        """
        profile = self._profiles.get(model_id)
        if not profile:
            return

        profile.consecutive_failures = 0
        profile.last_success_time = time.time()
        profile.total_requests += 1
        if tokens_used > 0:
            profile.total_tokens_used += tokens_used

    def _find_next_available(self) -> Optional[int]:
        """
        查找下一个可用模型（按优先级排序）

        策略：
        1. 优先选择 is_available 且优先级最高的
        2. 如果都在冷却中，选择冷却时间最短的
        """
        sorted_profiles = sorted(
            self._profiles.values(),
            key=lambda p: (p.priority, p.failure_count)
        )

        # 第一轮：找可用的
        for p in sorted_profiles:
            if p.is_available and p.model_id != self._current_model_id:
                return p.model_id

        # 第二轮：找冷却时间最短的（排除当前）
        cooling = [
            p for p in sorted_profiles
            if p.model_id != self._current_model_id and p.utilization > 0
        ]
        if cooling:
            best = min(cooling, key=lambda p: p.cooldown_until)
            return best.model_id

        # 所有模型都不可用，返回当前的
        return self._current_model_id

    def _do_switch(self, new_model_id: int, reason: str = "") -> int:
        """执行模型切换"""
        old_id = self._current_model_id
        old_name = self._profiles[old_id].model_name if old_id and old_id in self._profiles else "None"
        new_name = self._profiles[new_model_id].model_name

        self._current_model_id = new_model_id

        # 记录切换历史
        record = {
            "time": datetime.now().isoformat(),
            "from_id": old_id,
            "from_name": old_name,
            "to_id": new_model_id,
            "to_name": new_name,
            "reason": reason,
        }
        self._switch_history.append(record)
        if len(self._switch_history) > 100:
            self._switch_history = self._switch_history[-50:]

        logger.info(
            f"[AutoSwitch] 🔄 模型切换: {old_name} → {new_name} (reason: {reason})"
        )

        # 同步到数据库
        self._sync_active_to_db(new_model_id)

        return new_model_id

    def _sync_active_to_db(self, model_id: int):
        """将活动模型同步到数据库"""
        try:
            from database.connection import SessionLocal, LLMModel
            db = SessionLocal()
            try:
                db.query(LLMModel).update({LLMModel.is_active: 0})
                model = db.query(LLMModel).filter(LLMModel.id == model_id).first()
                if model:
                    model.is_active = 1
                    model.status = model.model_name
                    model.updated_at = datetime.now()
                db.commit()

                # 刷新 ModelConfigManager 缓存
                from llm.manager import model_config_manager
                model_config_manager.refresh_config(db)

                logger.info(f"[AutoSwitch] 数据库已同步: model_id={model_id} 已激活")
            finally:
                db.close()
        except Exception as e:
            logger.error(f"[AutoSwitch] 同步数据库失败: {e}")

    def force_switch(self, model_id: int) -> bool:
        """手动强制切换到指定模型"""
        if model_id not in self._profiles:
            return False
        self._do_switch(model_id, reason="manual switch")
        return True

    def reset_profile(self, model_id: int):
        """重置模型的失败状态"""
        profile = self._profiles.get(model_id)
        if profile:
            profile.failure_count = 0
            profile.consecutive_failures = 0
            profile.cooldown_until = 0
            profile.last_failure_reason = None
            logger.info(f"[AutoSwitch] 已重置模型 {profile.model_name} 的状态")

    def reset_all(self):
        """重置所有模型状态"""
        for p in self._profiles.values():
            p.failure_count = 0
            p.consecutive_failures = 0
            p.cooldown_until = 0
            p.last_failure_reason = None
        logger.info("[AutoSwitch] 已重置所有模型状态")

    async def call_with_failover(
        self,
        call_fn,
        max_retries: int = 3,
        **kwargs
    ) -> Tuple[Any, int]:
        """
        带自动切换的调用封装

        Args:
            call_fn: 异步调用函数，签名 async def fn(profile: ModelProfile, **kwargs) -> result
            max_retries: 最大重试次数
            **kwargs: 传递给 call_fn 的额外参数

        Returns:
            (result, model_id) 元组

        Raises:
            最后一次失败的异常
        """
        if not self._profiles:
            self.load_profiles_from_db()

        last_error = None
        tried_models = set()

        for attempt in range(max_retries):
            profile = self.current_profile
            if not profile:
                raise RuntimeError("没有可用的模型配置")

            # 如果当前模型在冷却中且自动切换开启，先切换
            if profile.is_cooling_down and self.enabled:
                next_id = self._find_next_available()
                if next_id and next_id != profile.model_id:
                    self._do_switch(next_id, reason="current model cooling down")
                    profile = self.current_profile
                elif profile.is_cooling_down:
                    # 所有模型都在冷却，等待最短冷却时间
                    wait_time = min(
                        p.cooldown_remaining for p in self._profiles.values()
                        if p.cooldown_remaining > 0
                    ) if any(p.cooldown_remaining > 0 for p in self._profiles.values()) else 5
                    wait_time = min(wait_time, 30)  # 最多等 30 秒
                    logger.info(f"[AutoSwitch] 所有模型冷却中，等待 {wait_time:.1f}s")
                    await asyncio.sleep(wait_time)

            if not profile:
                raise RuntimeError("没有可用的模型配置")

            tried_models.add(profile.model_id)

            try:
                result = await call_fn(profile, **kwargs)
                self.mark_success(profile.model_id)
                return result, profile.model_id

            except Exception as e:
                last_error = e
                reason = classify_failure_reason(e)
                new_id = self.mark_failure(profile.model_id, reason)

                logger.warning(
                    f"[AutoSwitch] 调用失败 (attempt {attempt + 1}/{max_retries}): "
                    f"model={profile.model_name}, reason={reason.value}, error={str(e)[:200]}"
                )

                # 如果没有切换发生或已经试过所有模型，停止重试
                if not self.enabled:
                    break
                if new_id is None and len(tried_models) >= len(self._profiles):
                    break

        raise last_error


# 全局单例
auto_switcher = ModelAutoSwitcher()


def get_auto_switcher() -> ModelAutoSwitcher:
    """获取自动切换器实例"""
    return auto_switcher


class FailoverChatModel:
    """
    带自动故障转移的 LLM 代理类

    包装 browser-use 的 ChatOpenAI，在 ainvoke 调用中拦截 429/RateLimitError，
    自动从 ModelAutoSwitcher 获取下一个可用模型，创建新的 LLM 实例并重试。

    对 browser-use Agent 完全透明 — Agent 只看到一个正常的 LLM 对象。
    """

    def __init__(self, initial_llm, switcher: ModelAutoSwitcher = None):
        # 减少底层 LLM 的重试次数，因为 FailoverChatModel 自己处理重试（通过切换模型）
        if hasattr(initial_llm, 'max_retries'):
            initial_llm.max_retries = 1
        self._current_llm = initial_llm
        self._switcher = switcher or get_auto_switcher()
        self._switch_count = 0
        self._max_switches_per_call = 3  # 单次 ainvoke 最多切换 3 次

    # ---- 透传 BaseChatModel 协议所需的属性 ----

    @property
    def model(self) -> str:
        return self._current_llm.model

    @model.setter
    def model(self, value: str):
        self._current_llm.model = value

    @property
    def provider(self) -> str:
        return getattr(self._current_llm, 'provider', 'openai')

    @property
    def name(self) -> str:
        return getattr(self._current_llm, 'name', str(self.model))

    @property
    def model_name(self) -> str:
        return self.model

    # ---- 透传其他常用属性 ----

    def __getattr__(self, name):
        """将未定义的属性访问代理到底层 LLM"""
        return getattr(self._current_llm, name)

    # ---- 核心：带故障转移的 ainvoke ----

    async def ainvoke(self, messages, output_format=None):
        """
        带自动故障转移的 ainvoke

        当遇到 429/RateLimitError 时：
        1. 通知 auto_switcher 标记当前模型失败
        2. 获取下一个可用模型的配置
        3. 创建新的 LLM 实例
        4. 用新 LLM 重试
        """
        switches_this_call = 0
        last_error = None

        while switches_this_call <= self._max_switches_per_call:
            try:
                result = await self._current_llm.ainvoke(messages, output_format)
                # 成功 — 标记成功
                if self._switcher.enabled and self._switcher._current_model_id:
                    self._switcher.mark_success(self._switcher._current_model_id, 0)
                return result

            except Exception as e:
                error_msg = str(e)
                last_error = e

                # 判断是否为可切换的错误（429 / quota / rate limit）
                if not self._is_switchable_error(e, error_msg):
                    raise

                if not self._switcher.enabled:
                    logger.warning(f"[FailoverLLM] 自动切换未开启，无法故障转移: {error_msg}")
                    raise

                # 标记当前模型失败
                current_id = self._switcher._current_model_id
                reason = classify_failure_reason(e)
                new_id = self._switcher.mark_failure(current_id or 0, reason)

                if new_id and new_id != current_id:
                    # 切换成功，创建新 LLM
                    logger.info(
                        f"[FailoverLLM] 🔄 模型切换: ID={current_id} → ID={new_id}, "
                        f"正在创建新 LLM 实例..."
                    )
                    new_llm = self._create_llm_from_profile(new_id)
                    if new_llm:
                        self._current_llm = new_llm
                        self._switch_count += 1
                        switches_this_call += 1
                        logger.info(
                            f"[FailoverLLM] ✅ 已切换到新模型，累计切换 {self._switch_count} 次，"
                            f"本次调用第 {switches_this_call} 次切换"
                        )
                        continue  # 用新 LLM 重试
                    else:
                        logger.error("[FailoverLLM] ❌ 创建新 LLM 实例失败")
                        raise
                else:
                    logger.warning(
                        f"[FailoverLLM] ❌ 没有可用的备选模型，无法切换 "
                        f"(current={current_id}, new={new_id})"
                    )
                    raise

        # 超过最大切换次数
        logger.error(f"[FailoverLLM] ❌ 已达到最大切换次数 {self._max_switches_per_call}")
        if last_error:
            raise last_error

    def _is_switchable_error(self, error, error_msg: str) -> bool:
        """判断是否为可以通过切换模型解决的错误"""
        # browser-use 的 ModelRateLimitError
        error_type = type(error).__name__
        if error_type == 'ModelRateLimitError':
            return True

        # 通用 429 检测
        lower_msg = error_msg.lower()
        if '429' in error_msg:
            return True
        if 'rate limit' in lower_msg or 'rate_limit' in lower_msg:
            return True
        if 'quota' in lower_msg and ('exceeded' in lower_msg or 'exhausted' in lower_msg):
            return True
        if '配额' in error_msg or '限流' in error_msg:
            return True

        return False

    def _create_llm_from_profile(self, model_id: int):
        """根据 model_id 从 profile 创建新的 browser-use LLM 实例"""
        profile = self._switcher._profiles.get(model_id)
        if not profile:
            logger.error(f"[FailoverLLM] 找不到模型 profile: ID={model_id}")
            return None

        try:
            from llm.factory import get_browser_use_llm
            new_llm = get_browser_use_llm(
                provider=profile.provider,
                model_name=profile.model_name,
                api_key=profile.api_key,
                base_url=profile.base_url,
                temperature=0.0,
            )
            logger.info(
                f"[FailoverLLM] 创建新 LLM: provider={profile.provider}, "
                f"model={profile.model_name}"
            )
            # 减少重试次数，FailoverChatModel 自己处理切换
            if hasattr(new_llm, 'max_retries'):
                new_llm.max_retries = 1
            return new_llm
        except Exception as e:
            logger.error(f"[FailoverLLM] 创建 LLM 失败: {e}")
            return None

    @property
    def total_switches(self) -> int:
        return self._switch_count
