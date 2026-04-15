"""
模型配置管理器

从数据库获取当前激活的模型配置
此模块是对 llm.manager 的兼容封装

作者: 程序员Eighteen
"""
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# 导入 llm 模块的配置管理器
try:
    from llm.manager import (
        ModelConfigManager,
        model_config_manager,
        get_active_llm_config as _get_active_llm_config,
        refresh_llm_config as _refresh_llm_config,
    )
    
    # 重新导出
    get_active_llm_config = _get_active_llm_config
    refresh_llm_config = _refresh_llm_config
    
except ImportError:
    # 如果 llm 模块不可用，使用本地实现
    logger.warning("[Model_manage] llm 模块不可用，使用本地实现")
    
    from typing import Optional
    
    class ModelConfigManager:
        """LLM 模型配置管理器（本地实现）"""
        
        _instance = None
        _cached_config: Optional[Dict] = None
        
        def __new__(cls):
            if cls._instance is None:
                cls._instance = super(ModelConfigManager, cls).__new__(cls)
            return cls._instance
        
        def _get_db_session(self):
            """获取数据库会话"""
            import sys
            import os
            
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            
            from database.connection import SessionLocal
            return SessionLocal()
        
        def get_active_model_config(self, db=None) -> Dict:
            """获取当前激活的模型配置"""
            close_db = False
            if db is None:
                db = self._get_db_session()
                close_db = True
            
            try:
                from database.connection import LLMModel
                
                active_model = db.query(LLMModel).filter(LLMModel.is_active == 1).first()
                
                if not active_model:
                    raise ValueError("未找到激活的模型，请在模型管理页面激活一个模型")
                
                config = {
                    'id': active_model.id,
                    'model_name': active_model.model_name,
                    'api_key': active_model.api_key,
                    'base_url': active_model.base_url or '',
                    'provider': active_model.provider or 'openai',
                    'temperature': 0.0,
                    'max_tokens': 128000,
                }
                
                self._cached_config = config
                
                logger.info(f"[ModelConfig] 从数据库加载激活模型: ID={active_model.id}, "
                           f"model_name={config['model_name']}, provider={config['provider']}")
                
                return config
                
            except Exception as e:
                if self._cached_config:
                    logger.warning(f"[Warning] 数据库查询失败，使用缓存配置: {e}")
                    return self._cached_config
                raise
            finally:
                if close_db:
                    db.close()
        
        def refresh_config(self, db=None) -> Dict:
            """刷新配置缓存"""
            self._cached_config = None
            return self.get_active_model_config(db)
        
        def increment_token_usage(self, tokens: int, db=None):
            """增加 Token 使用量"""
            close_db = False
            if db is None:
                db = self._get_db_session()
                close_db = True
            
            try:
                from database.connection import LLMModel
                
                active_model = db.query(LLMModel).filter(LLMModel.is_active == 1).first()
                if active_model:
                    active_model.tokens_used_today = (active_model.tokens_used_today or 0) + tokens
                    active_model.tokens_used_total = (active_model.tokens_used_total or 0) + tokens
                    db.commit()
            except Exception as e:
                logger.warning(f"[Warning] 更新 Token 使用量失败: {e}")
                db.rollback()
            finally:
                if close_db:
                    db.close()
    
    # 全局单例
    model_config_manager = ModelConfigManager()
    
    def get_active_llm_config(db=None) -> Dict:
        """获取当前激活的 LLM 配置"""
        return model_config_manager.get_active_model_config(db)
    
    def refresh_llm_config(db=None) -> Dict:
        """刷新 LLM 配置缓存"""
        return model_config_manager.refresh_config(db)
