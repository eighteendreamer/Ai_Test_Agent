"""
LLM 配置管理器模块

从数据库获取当前激活的模型配置，提供统一的配置管理接口

作者: 程序员Eighteen
版本: 1.0
"""
import logging
from typing import Any, Dict, Optional

from .base import LLMConfig, BaseLLMProvider
from .factory import create_llm_provider, get_llm_model, get_browser_use_llm
from .exceptions import NoActiveModelError, ConfigurationError

logger = logging.getLogger(__name__)


class ModelConfigManager:
    """
    LLM 模型配置管理器
    
    提供从数据库获取激活模型配置的功能
    """
    
    _instance = None
    _cached_config: Optional[Dict] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ModelConfigManager, cls).__new__(cls)
        return cls._instance
    
    def _get_db_session(self):
        """获取数据库会话"""
        try:
            # 尝试导入项目的数据库连接
            import sys
            import os
            
            # 添加项目路径
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            
            from database.connection import SessionLocal
            return SessionLocal()
        except ImportError as e:
            logger.error(f"[ModelConfigManager] 无法导入数据库连接: {e}")
            raise
    
    def _get_model_from_db(self, db=None) -> Dict:
        """从数据库获取激活的模型配置"""
        close_db = False
        if db is None:
            db = self._get_db_session()
            close_db = True
        
        try:
            from database.connection import LLMModel
            
            # 查询当前激活的模型
            active_model = db.query(LLMModel).filter(LLMModel.is_active == 1).first()
            
            if not active_model:
                raise NoActiveModelError()
            
            config = {
                'id': active_model.id,
                'model_name': active_model.model_name,
                'api_key': active_model.api_key,
                'base_url': active_model.base_url or '',
                'provider': active_model.provider or 'openai',
                'temperature': 0.0,
                'max_tokens': 4096,  # 使用更合理的默认值，避免超出模型限制
            }
            
            logger.info(f"[ModelConfigManager] 从数据库加载激活模型: ID={active_model.id}, "
                       f"model_name={config['model_name']}, provider={config['provider']}")
            
            return config
            
        except NoActiveModelError:
            raise
        except Exception as e:
            logger.error(f"[ModelConfigManager] 查询数据库失败: {e}")
            raise ConfigurationError(f"数据库查询失败: {e}")
        finally:
            if close_db:
                db.close()
    
    def get_active_model_config(self, db=None, use_cache: bool = False) -> Dict:
        """
        获取当前激活的模型配置
        
        Args:
            db: 数据库会话（可选）
            use_cache: 是否使用缓存
        
        Returns:
            模型配置字典
        """
        if use_cache and self._cached_config:
            return self._cached_config
        
        config = self._get_model_from_db(db)
        self._cached_config = config
        return config
    
    def refresh_config(self, db=None) -> Dict:
        """刷新配置缓存"""
        self._cached_config = None
        return self.get_active_model_config(db)
    
    def get_llm_provider(self, db=None) -> BaseLLMProvider:
        """
        获取激活模型的 LLM Provider
        
        Returns:
            BaseLLMProvider 实例
        """
        config = self.get_active_model_config(db)
        
        return create_llm_provider(
            provider=config['provider'],
            model_name=config['model_name'],
            api_key=config['api_key'],
            base_url=config['base_url'],
            temperature=config.get('temperature', 0.0),
            max_tokens=config.get('max_tokens', 4096),  # 使用更合理的默认值
        )
    
    def get_langchain_llm(self, db=None) -> Any:
        """
        获取 LangChain 格式的 LLM
        
        Returns:
            LangChain LLM 实例
        """
        config = self.get_active_model_config(db)
        
        return get_llm_model(
            provider=config['provider'],
            model_name=config['model_name'],
            api_key=config['api_key'],
            base_url=config['base_url'],
            temperature=config.get('temperature', 0.0),
        )
    
    def get_browser_use_llm(self, db=None) -> Any:
        """
        获取 Browser-Use 格式的 LLM
        
        Returns:
            Browser-Use LLM 实例
        """
        config = self.get_active_model_config(db)
        
        return get_browser_use_llm(
            provider=config['provider'],
            model_name=config['model_name'],
            api_key=config['api_key'],
            base_url=config['base_url'],
            temperature=config.get('temperature', 0.0),
        )
    
    def get_model_for_langchain(self, db=None) -> Dict:
        """
        获取 LangChain 格式的模型配置（兼容旧接口）
        
        Returns:
            LangChain 初始化参数
        """
        config = self.get_active_model_config(db)
        
        langchain_config = {
            'model': config['model_name'],
            'api_key': config['api_key'],
            'temperature': config.get('temperature', 0.0),
            'max_tokens': config.get('max_tokens', 4096),  # 使用更合理的默认值
        }
        
        if config.get('base_url'):
            langchain_config['base_url'] = config['base_url']
        
        return langchain_config
    
    def get_model_for_browser_use(self, db=None) -> Dict:
        """
        获取 Browser-Use 格式的模型配置（兼容旧接口）
        
        Returns:
            Browser-Use LLM 初始化参数
        """
        config = self.get_active_model_config(db)
        
        browser_use_config = {
            'model_name': config['model_name'],
            'api_key': config['api_key'],
            'base_url': config['base_url'],
            'temperature': config.get('temperature', 0.0),
            'provider': config['provider'],
        }
        
        return browser_use_config
    
    def increment_token_usage(
        self,
        tokens: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        source: str = "chat",
        session_id: int = None,
        success: bool = True,
        error_type: str = None,
        duration_ms: int = 0,
        db=None,
    ):
        """
        增加 Token 使用量（增强版，支持详细统计）
        
        Args:
            tokens: 总 token 数量（如果为 0 则自动计算）
            prompt_tokens: 输入 token
            completion_tokens: 输出 token
            source: 来源 (chat/browser_use/oneclick/api_test)
            session_id: 关联会话 ID
            success: 是否成功
            error_type: 错误类型
            duration_ms: 耗时毫秒
            db: 数据库会话
        """
        total = tokens if tokens > 0 else (prompt_tokens + completion_tokens)
        if total <= 0:
            return

        close_db = False
        if db is None:
            db = self._get_db_session()
            close_db = True
        
        try:
            from database.connection import LLMModel, TokenUsageLog
            from datetime import datetime
            
            active_model = db.query(LLMModel).filter(LLMModel.is_active == 1).first()
            if active_model:
                active_model.tokens_used_today = (active_model.tokens_used_today or 0) + total
                active_model.tokens_used_total = (active_model.tokens_used_total or 0) + total
                active_model.tokens_input_total = (active_model.tokens_input_total or 0) + prompt_tokens
                active_model.tokens_output_total = (active_model.tokens_output_total or 0) + completion_tokens
                active_model.request_count_total = (active_model.request_count_total or 0) + 1
                active_model.request_count_today = (active_model.request_count_today or 0) + 1
                active_model.last_used_at = datetime.now()
                if not success:
                    active_model.failure_count_total = (active_model.failure_count_total or 0) + 1
                    active_model.last_failure_reason = error_type

                # 写入详细日志
                log_entry = TokenUsageLog(
                    model_id=active_model.id,
                    model_name=active_model.model_name,
                    provider=active_model.provider,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total,
                    source=source,
                    session_id=session_id,
                    success=1 if success else 0,
                    error_type=error_type,
                    duration_ms=duration_ms,
                )
                db.add(log_entry)
                db.commit()

                # 同步到 auto_switcher
                try:
                    from llm.auto_switch import get_auto_switcher
                    switcher = get_auto_switcher()
                    if success:
                        switcher.mark_success(active_model.id, total)
                except Exception:
                    pass

                logger.debug(
                    f"[ModelConfigManager] Token 使用量更新: +{total} "
                    f"(in={prompt_tokens}, out={completion_tokens}, src={source})"
                )
        except Exception as e:
            logger.warning(f"[ModelConfigManager] 更新 Token 使用量失败: {e}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            if close_db:
                db.close()


# 全局单例
model_config_manager = ModelConfigManager()


# 便捷函数
def get_active_llm_config(db=None) -> Dict:
    """获取当前激活的 LLM 配置"""
    return model_config_manager.get_active_model_config(db)


def get_active_llm_provider(db=None) -> BaseLLMProvider:
    """获取当前激活的 LLM Provider"""
    return model_config_manager.get_llm_provider(db)


def get_active_langchain_llm(db=None) -> Any:
    """获取当前激活的 LangChain LLM"""
    return model_config_manager.get_langchain_llm(db)


def get_active_browser_use_llm(db=None) -> Any:
    """获取当前激活的 Browser-Use LLM"""
    return model_config_manager.get_browser_use_llm(db)


def refresh_llm_config(db=None) -> Dict:
    """刷新 LLM 配置缓存"""
    return model_config_manager.refresh_config(db)
