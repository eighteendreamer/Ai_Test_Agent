"""
模型管理 API 路由

提供 LLM 模型的 CRUD 接口

作者: 程序员Eighteen
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from datetime import datetime

from database.connection import get_db, LLMModel, ModelProvider, TokenUsageLog
from llm.client import get_llm_client

router = APIRouter(
    prefix="/api/models",
    tags=["模型管理"]
)


# ============================================
# Pydantic 模型定义
# ============================================

class ModelCreate(BaseModel):
    """创建模型的请求模型"""
    model_name: str
    api_key: str
    base_url: str = None
    provider: str = None
    priority: int = 1
    utilization: int = 100


class ModelUpdate(BaseModel):
    """更新模型的请求模型"""
    model_name: str = None
    api_key: str = None
    base_url: str = None
    provider: str = None
    priority: int = None
    utilization: int = None
    is_active: int = None
    status: str = None


class ModelResponse(BaseModel):
    """模型响应模型"""
    id: int
    model_name: str
    api_key: str
    base_url: str = None
    provider: str = None
    provider_display_name: str = None
    is_active: int
    priority: int
    utilization: int
    tokens_used_total: int = 0
    tokens_used_today: int = 0
    status: str
    created_at: datetime
    updated_at: datetime = None

    class Config:
        from_attributes = True


# ============================================
# API 接口
# ============================================

@router.get("/", response_model=List[ModelResponse])
def get_models(db: Session = Depends(get_db)):
    """获取所有模型列表"""
    try:
        models = db.query(LLMModel).order_by(LLMModel.priority).all()
        
        # 获取供应商信息映射
        providers = db.query(ModelProvider).all()
        provider_map = {p.code: p.display_name for p in providers}
        
        result = []
        for model in models:
            model_dict = {
                "id": model.id,
                "model_name": model.model_name,
                "api_key": model.api_key,
                "base_url": model.base_url,
                "provider": model.provider,
                "provider_display_name": provider_map.get(model.provider, model.provider),
                "is_active": model.is_active,
                "priority": model.priority,
                "utilization": model.utilization,
                "tokens_used_total": model.tokens_used_total or 0,
                "tokens_used_today": model.tokens_used_today or 0,
                "status": model.status,
                "created_at": model.created_at,
                "updated_at": model.updated_at
            }
            result.append(ModelResponse(**model_dict))
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取模型列表失败: {str(e)}")


@router.get("/providers", response_model=dict)
def get_model_providers(db: Session = Depends(get_db)):
    """获取所有模型供应商列表（仅启用的，用于下拉选择）"""
    try:
        providers = db.query(ModelProvider).filter(
            ModelProvider.is_active == 1
        ).order_by(ModelProvider.sort_order).all()
        
        provider_list = [
            {
                "id": p.id,
                "name": p.name,
                "code": p.code,
                "display_name": p.display_name,
                "default_base_url": p.default_base_url,
                "sort_order": p.sort_order,
                "description": p.description
            }
            for p in providers
        ]
        
        return {
            "success": True,
            "data": provider_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取供应商列表失败: {str(e)}")


# ============================================
# 供应商管理 API
# ============================================

class ProviderCreate(BaseModel):
    """创建供应商的请求模型"""
    name: str
    code: str
    display_name: str
    default_base_url: str = None
    is_active: int = 1
    sort_order: int = 0
    description: str = None


class ProviderUpdate(BaseModel):
    """更新供应商的请求模型"""
    name: str = None
    code: str = None
    display_name: str = None
    default_base_url: str = None
    is_active: int = None
    sort_order: int = None
    description: str = None


@router.get("/providers/all", response_model=dict)
def get_all_providers(db: Session = Depends(get_db)):
    """获取所有供应商列表（包含禁用的，用于供应商管理页面）"""
    try:
        providers = db.query(ModelProvider).order_by(ModelProvider.sort_order).all()

        # 统计每个供应商下的模型数量
        from sqlalchemy import func
        model_counts = dict(
            db.query(LLMModel.provider, func.count(LLMModel.id))
            .group_by(LLMModel.provider).all()
        )

        provider_list = [
            {
                "id": p.id,
                "name": p.name,
                "code": p.code,
                "display_name": p.display_name,
                "default_base_url": p.default_base_url,
                "is_active": p.is_active,
                "sort_order": p.sort_order,
                "description": p.description,
                "model_count": model_counts.get(p.code, 0),
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in providers
        ]

        return {"success": True, "data": provider_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取供应商列表失败: {str(e)}")


@router.post("/providers", response_model=dict)
def create_provider(data: ProviderCreate, db: Session = Depends(get_db)):
    """创建新供应商"""
    try:
        existing = db.query(ModelProvider).filter(
            (ModelProvider.code == data.code) | (ModelProvider.name == data.name)
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="供应商名称或代码已存在")

        provider = ModelProvider(
            name=data.name,
            code=data.code,
            display_name=data.display_name,
            default_base_url=data.default_base_url,
            is_active=data.is_active,
            sort_order=data.sort_order,
            description=data.description,
        )
        db.add(provider)
        db.commit()
        db.refresh(provider)

        return {
            "success": True,
            "message": "供应商创建成功",
            "data": {"id": provider.id, "name": provider.name, "code": provider.code}
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建供应商失败: {str(e)}")


@router.put("/providers/{provider_id}", response_model=dict)
def update_provider(provider_id: int, data: ProviderUpdate, db: Session = Depends(get_db)):
    """更新供应商信息"""
    try:
        provider = db.query(ModelProvider).filter(ModelProvider.id == provider_id).first()
        if not provider:
            raise HTTPException(status_code=404, detail="供应商不存在")

        update_data = data.dict(exclude_unset=True)

        # 检查 code/name 唯一性
        if "code" in update_data and update_data["code"] != provider.code:
            dup = db.query(ModelProvider).filter(
                ModelProvider.code == update_data["code"], ModelProvider.id != provider_id
            ).first()
            if dup:
                raise HTTPException(status_code=400, detail="供应商代码已被使用")

        if "name" in update_data and update_data["name"] != provider.name:
            dup = db.query(ModelProvider).filter(
                ModelProvider.name == update_data["name"], ModelProvider.id != provider_id
            ).first()
            if dup:
                raise HTTPException(status_code=400, detail="供应商名称已被使用")

        for field, value in update_data.items():
            setattr(provider, field, value)

        provider.updated_at = datetime.now()
        db.commit()
        db.refresh(provider)

        return {
            "success": True,
            "message": "供应商更新成功",
            "data": {"id": provider.id, "name": provider.name}
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新供应商失败: {str(e)}")


@router.delete("/providers/{provider_id}", response_model=dict)
def delete_provider(provider_id: int, db: Session = Depends(get_db)):
    """删除供应商"""
    try:
        provider = db.query(ModelProvider).filter(ModelProvider.id == provider_id).first()
        if not provider:
            raise HTTPException(status_code=404, detail="供应商不存在")

        # 检查是否有模型在使用该供应商
        model_count = db.query(LLMModel).filter(LLMModel.provider == provider.code).count()
        if model_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f"该供应商下有 {model_count} 个模型正在使用，无法删除"
            )

        db.delete(provider)
        db.commit()

        return {"success": True, "message": "供应商删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除供应商失败: {str(e)}")


@router.put("/providers/{provider_id}/toggle", response_model=dict)
def toggle_provider(provider_id: int, db: Session = Depends(get_db)):
    """启用/禁用供应商"""
    try:
        provider = db.query(ModelProvider).filter(ModelProvider.id == provider_id).first()
        if not provider:
            raise HTTPException(status_code=404, detail="供应商不存在")

        provider.is_active = 0 if provider.is_active == 1 else 1
        provider.updated_at = datetime.now()
        db.commit()

        status = "启用" if provider.is_active == 1 else "禁用"
        return {"success": True, "message": f"供应商已{status}"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"操作失败: {str(e)}")


@router.get("/active/current", response_model=dict)
def get_active_model(db: Session = Depends(get_db)):
    """获取当前激活的模型"""
    try:
        active_model = db.query(LLMModel).filter(LLMModel.is_active == 1).first()
        if not active_model:
            return {
                "success": True,
                "data": None,
                "message": "当前没有激活的模型"
            }
        
        return {
            "success": True,
            "data": {
                "id": active_model.id,
                "model_name": active_model.model_name,
                "provider": active_model.provider,
                "status": active_model.status,
                "tokens_used_total": active_model.tokens_used_total,
                "tokens_used_today": active_model.tokens_used_today
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取激活模型失败: {str(e)}")


@router.get("/{model_id}", response_model=ModelResponse)
def get_model(model_id: int, db: Session = Depends(get_db)):
    """获取单个模型详情"""
    model = db.query(LLMModel).filter(LLMModel.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    return model


@router.post("/", response_model=dict)
def create_model(model_data: ModelCreate, db: Session = Depends(get_db)):
    """创建新模型"""
    try:
        existing = db.query(LLMModel).filter(
            LLMModel.model_name == model_data.model_name
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="模型名称已存在")
        
        new_model = LLMModel(
            model_name=model_data.model_name,
            api_key=model_data.api_key,
            base_url=model_data.base_url,
            provider=model_data.provider,
            priority=model_data.priority,
            utilization=model_data.utilization,
            is_active=0,
            tokens_used_total=0,
            tokens_used_today=0,
            status='待命'
        )
        
        db.add(new_model)
        db.commit()
        db.refresh(new_model)
        
        return {
            "success": True,
            "message": "模型创建成功",
            "data": {
                "id": new_model.id,
                "model_name": new_model.model_name
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建模型失败: {str(e)}")


@router.put("/{model_id}", response_model=dict)
def update_model(model_id: int, model_data: ModelUpdate, db: Session = Depends(get_db)):
    """更新模型信息"""
    try:
        model = db.query(LLMModel).filter(LLMModel.id == model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="模型不存在")
        
        update_data = model_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(model, field, value)
        
        model.updated_at = datetime.now()
        db.commit()
        db.refresh(model)
        
        return {
            "success": True,
            "message": "模型更新成功",
            "data": {
                "id": model.id,
                "model_name": model.model_name
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新模型失败: {str(e)}")


@router.delete("/{model_id}", response_model=dict)
def delete_model(model_id: int, db: Session = Depends(get_db)):
    """删除模型"""
    try:
        model = db.query(LLMModel).filter(LLMModel.id == model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="模型不存在")
        
        if model.is_active == 1:
            raise HTTPException(status_code=400, detail="无法删除激活中的模型")
        
        db.delete(model)
        db.commit()
        
        return {
            "success": True,
            "message": "模型删除成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除模型失败: {str(e)}")


@router.post("/{model_id}/activate", response_model=dict)
def activate_model(model_id: int, db: Session = Depends(get_db)):
    """激活模型"""
    try:
        model = db.query(LLMModel).filter(LLMModel.id == model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="模型不存在")
        
        # 将所有模型设为非激活
        db.query(LLMModel).update({LLMModel.is_active: 0})
        
        # 激活目标模型
        model.is_active = 1
        model.status = model.model_name
        model.updated_at = datetime.now()
        
        db.commit()
        
        # 刷新模型配置缓存
        try:
            from Model_manage.config_manager import refresh_llm_config
            refresh_llm_config()
        except Exception as refresh_err:
            print(f"[Warning] 刷新配置缓存失败: {refresh_err}")
        
        return {
            "success": True,
            "message": f"模型 {model.model_name} 已激活，配置已刷新"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"激活模型失败: {str(e)}")


# ============================================
# 自动切换 API
# ============================================

@router.get("/auto-switch/status", response_model=dict)
def get_auto_switch_status(db: Session = Depends(get_db)):
    """获取自动切换状态"""
    try:
        from llm.auto_switch import get_auto_switcher
        switcher = get_auto_switcher()
        switcher.load_profiles_from_db(db)

        return {
            "success": True,
            "data": {
                "enabled": switcher.enabled,
                "current_model_id": switcher._current_model_id,
                "profiles": switcher.get_all_profiles_status(),
                "switch_history": switcher.get_switch_history(20),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取自动切换状态失败: {str(e)}")


@router.post("/auto-switch/toggle", response_model=dict)
def toggle_auto_switch(enabled: bool, db: Session = Depends(get_db)):
    """开启/关闭自动切换"""
    try:
        from llm.auto_switch import get_auto_switcher
        switcher = get_auto_switcher()
        switcher.enabled = enabled
        return {
            "success": True,
            "message": f"自动切换已{'开启' if enabled else '关闭'}"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auto-switch/reset", response_model=dict)
def reset_auto_switch(model_id: int = None, db: Session = Depends(get_db)):
    """重置模型的失败状态"""
    try:
        from llm.auto_switch import get_auto_switcher
        switcher = get_auto_switcher()
        if model_id:
            switcher.reset_profile(model_id)
        else:
            switcher.reset_all()
        return {
            "success": True,
            "message": "状态已重置"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Token 统计 API
# ============================================

@router.get("/token-stats/summary", response_model=dict)
def get_token_stats_summary(db: Session = Depends(get_db)):
    """获取 Token 使用统计摘要"""
    try:
        from sqlalchemy import func

        # 各模型统计
        models = db.query(LLMModel).order_by(LLMModel.priority).all()
        model_stats = []
        for m in models:
            model_stats.append({
                "id": m.id,
                "model_name": m.model_name,
                "provider": m.provider,
                "tokens_used_total": m.tokens_used_total or 0,
                "tokens_used_today": m.tokens_used_today or 0,
                "tokens_input_total": getattr(m, 'tokens_input_total', 0) or 0,
                "tokens_output_total": getattr(m, 'tokens_output_total', 0) or 0,
                "request_count_total": getattr(m, 'request_count_total', 0) or 0,
                "request_count_today": getattr(m, 'request_count_today', 0) or 0,
                "failure_count_total": getattr(m, 'failure_count_total', 0) or 0,
                "is_active": m.is_active,
            })

        # 按来源统计
        source_stats = []
        try:
            rows = db.query(
                TokenUsageLog.source,
                func.count(TokenUsageLog.id).label('count'),
                func.sum(TokenUsageLog.total_tokens).label('total_tokens'),
                func.sum(TokenUsageLog.prompt_tokens).label('prompt_tokens'),
                func.sum(TokenUsageLog.completion_tokens).label('completion_tokens'),
            ).group_by(TokenUsageLog.source).all()

            for row in rows:
                source_stats.append({
                    "source": row.source or "unknown",
                    "count": row.count,
                    "total_tokens": row.total_tokens or 0,
                    "prompt_tokens": row.prompt_tokens or 0,
                    "completion_tokens": row.completion_tokens or 0,
                })
        except Exception:
            pass  # 表可能还不存在

        return {
            "success": True,
            "data": {
                "models": model_stats,
                "by_source": source_stats,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")


@router.get("/token-stats/recent", response_model=dict)
def get_recent_token_logs(limit: int = 50, db: Session = Depends(get_db)):
    """获取最近的 Token 使用日志"""
    try:
        logs = db.query(TokenUsageLog).order_by(
            TokenUsageLog.created_at.desc()
        ).limit(limit).all()

        result = []
        for log in logs:
            result.append({
                "id": log.id,
                "model_name": log.model_name,
                "provider": log.provider,
                "prompt_tokens": log.prompt_tokens,
                "completion_tokens": log.completion_tokens,
                "total_tokens": log.total_tokens,
                "source": log.source,
                "success": log.success,
                "error_type": log.error_type,
                "duration_ms": log.duration_ms,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })

        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取日志失败: {str(e)}")


@router.post("/token-stats/reset-today", response_model=dict)
def reset_today_tokens(db: Session = Depends(get_db)):
    """重置今日 Token 统计"""
    try:
        db.query(LLMModel).update({
            LLMModel.tokens_used_today: 0,
            LLMModel.request_count_today: 0,
        })
        db.commit()
        return {"success": True, "message": "今日统计已重置"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test_connection")
def test_connection(request_data: dict = None):
    """
    测试模型连接
    如果不传参数，测试当前活动模型
    如果传 { "model_id": 1 }，测试指定模型
    """
    try:
        model_id = request_data.get("model_id") if request_data else None
        
        if model_id:
            # 测试指定模型
            db = next(get_db())
            model = db.query(LLMModel).filter(LLMModel.id == model_id).first()
            if not model:
                return {"status": "error", "message": "模型不存在"}
            
            # 临时创建 Provider
            from llm.factory import create_llm_provider
            
            try:
                provider = create_llm_provider(
                    provider=model.provider,
                    model_name=model.model_name,
                    api_key=model.api_key,
                    base_url=model.base_url,
                    temperature=0.1,
                    max_tokens=5
                )
                
                response = provider.chat(
                    messages=[{"role": "user", "content": "Hello"}],
                    max_tokens=5,
                    temperature=0.1
                )
                
                return {"status": "success", "message": "模型连接正常", "response": response.content}
            except Exception as provider_err:
                 return {"status": "error", "message": f"模型初始化或调用失败: {str(provider_err)}"}
            
        else:
            # 测试当前活动模型
            client = get_llm_client()
            # 强制重新加载配置以确保测试的是最新激活的模型
            client._config = None
            client._provider = None
            
            response = client.chat(
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=5,
                temperature=0.1
            )
            return {"status": "success", "message": "模型连接正常", "response": response}
            
    except Exception as e:
        return {"status": "error", "message": f"模型连接失败: {str(e)}"}
