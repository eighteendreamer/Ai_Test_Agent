"""
邮件管理 API 路由

作者: 程序员Eighteen
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from datetime import datetime
from sqlalchemy import func

from database.connection import get_db, EmailRecord, EmailConfig

router = APIRouter(
    prefix="/api/emails",
    tags=["邮件发送管理"]
)


# ============================================
# Pydantic 模型定义
# ============================================

class SendEmailRequest(BaseModel):
    contact_ids: List[int]
    subject: str
    html_content: str
    email_type: str = 'custom'


class EmailConfigCreate(BaseModel):
    config_name: str
    provider: str = 'resend'
    api_key: Optional[str] = None
    secret_key: Optional[str] = None
    sender_email: EmailStr
    test_email: Optional[EmailStr] = None
    test_mode: int = 1
    description: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_username: Optional[str] = None


class EmailConfigUpdate(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None
    secret_key: Optional[str] = None
    sender_email: Optional[EmailStr] = None
    test_email: Optional[EmailStr] = None
    test_mode: Optional[int] = None
    description: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None


class EmailConfigResponse(BaseModel):
    id: int
    config_name: str
    provider: str
    api_key: Optional[str] = None
    secret_key: Optional[str] = None
    sender_email: str
    test_email: Optional[str] = None
    test_mode: int
    is_active: int
    description: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    created_at: datetime
    updated_at: datetime = None

    class Config:
        from_attributes = True


class EmailRecordResponse(BaseModel):
    id: int
    subject: str
    recipients: list
    status: str
    success_count: int
    failed_count: int
    total_count: int
    email_type: str
    content_summary: Optional[str] = None
    email_ids: Optional[list] = None
    failed_details: Optional[list] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ============================================
# 邮件发送接口
# ============================================

@router.post("/send")
def send_custom_email(
    request: SendEmailRequest,
    db: Session = Depends(get_db)
):
    """发送自定义邮件"""
    # TODO: 实现邮件发送逻辑
    return {"code": 200, "message": "功能开发中", "data": None}


# ============================================
# 邮件记录接口
# ============================================

@router.get("/records", response_model=List[EmailRecordResponse])
def get_email_records(
    limit: int = 20,
    offset: int = 0,
    status: str = None,
    email_type: str = None,
    db: Session = Depends(get_db)
):
    """获取邮件发送记录列表"""
    try:
        query = db.query(EmailRecord)
        
        if status:
            query = query.filter(EmailRecord.status == status)
        if email_type:
            query = query.filter(EmailRecord.email_type == email_type)
        
        records = query.order_by(EmailRecord.created_at.desc()).limit(limit).offset(offset).all()
        return records
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取邮件记录失败: {str(e)}")


@router.get("/records/{record_id}", response_model=EmailRecordResponse)
def get_email_record_detail(
    record_id: int,
    db: Session = Depends(get_db)
):
    """获取单条邮件发送记录详情"""
    record = db.query(EmailRecord).filter(EmailRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    return record


@router.get("/statistics")
def get_email_statistics(db: Session = Depends(get_db)):
    """获取邮件发送统计信息"""
    try:
        total_sends = db.query(EmailRecord).count()
        success_sends = db.query(EmailRecord).filter(EmailRecord.status == 'success').count()
        partial_sends = db.query(EmailRecord).filter(EmailRecord.status == 'partial').count()
        failed_sends = db.query(EmailRecord).filter(EmailRecord.status == 'failed').count()
        
        total_success_emails = db.query(func.sum(EmailRecord.success_count)).scalar() or 0
        total_failed_emails = db.query(func.sum(EmailRecord.failed_count)).scalar() or 0
        
        return {
            "success": True,
            "data": {
                "total_sends": total_sends,
                "success_sends": success_sends,
                "partial_sends": partial_sends,
                "failed_sends": failed_sends,
                "total_success_emails": int(total_success_emails),
                "total_failed_emails": int(total_failed_emails),
                "success_rate": round(success_sends / total_sends * 100, 2) if total_sends > 0 else 0
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取统计信息失败: {str(e)}")


@router.delete("/records/{record_id}")
def delete_email_record(
    record_id: int,
    db: Session = Depends(get_db)
):
    """删除邮件发送记录"""
    record = db.query(EmailRecord).filter(EmailRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    
    try:
        db.delete(record)
        db.commit()
        return {"success": True, "message": "删除成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


# ============================================
# 邮件配置管理
# ============================================

@router.get("/config", response_model=List[EmailConfigResponse])
def get_email_configs(db: Session = Depends(get_db)):
    """获取所有邮件配置列表"""
    try:
        configs = db.query(EmailConfig).order_by(EmailConfig.created_at.desc()).all()
        return configs
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")


@router.get("/config/active", response_model=Optional[EmailConfigResponse])
def get_active_config(db: Session = Depends(get_db)):
    """获取当前激活的邮件配置"""
    config = db.query(EmailConfig).filter(EmailConfig.is_active == 1).first()
    if not config:
        return None
    return config


@router.post("/config", response_model=dict)
def create_email_config(
    config_data: EmailConfigCreate,
    db: Session = Depends(get_db)
):
    """创建新的邮件配置"""
    try:
        existing = db.query(EmailConfig).filter(
            EmailConfig.config_name == config_data.config_name
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="配置名称已存在")
        
        new_config = EmailConfig(
            config_name=config_data.config_name,
            provider=config_data.provider,
            api_key=config_data.api_key,
            secret_key=config_data.secret_key,
            sender_email=config_data.sender_email,
            test_email=config_data.test_email,
            test_mode=config_data.test_mode,
            description=config_data.description,
            smtp_host=config_data.smtp_host,
            smtp_port=config_data.smtp_port,
            smtp_username=config_data.smtp_username,
            is_active=0
        )
        
        db.add(new_config)
        db.commit()
        db.refresh(new_config)
        
        return {
            "success": True,
            "message": "配置创建成功",
            "data": {"id": new_config.id, "config_name": new_config.config_name}
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建配置失败: {str(e)}")


@router.put("/config/{config_id}")
def update_email_config(
    config_id: int,
    config_data: EmailConfigUpdate,
    db: Session = Depends(get_db)
):
    """更新邮件配置"""
    config = db.query(EmailConfig).filter(EmailConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    
    try:
        update_data = config_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(config, field, value)
        
        db.commit()
        return {"success": True, "message": "配置更新成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")


@router.post("/config/{config_id}/activate")
def activate_config(
    config_id: int,
    db: Session = Depends(get_db)
):
    """激活指定的邮件配置"""
    config = db.query(EmailConfig).filter(EmailConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    
    try:
        db.query(EmailConfig).update({"is_active": 0})
        config.is_active = 1
        db.commit()
        return {"success": True, "message": f"配置 '{config.config_name}' 已激活"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"激活配置失败: {str(e)}")


@router.delete("/config/{config_id}")
def delete_email_config(
    config_id: int,
    db: Session = Depends(get_db)
):
    """删除邮件配置"""
    config = db.query(EmailConfig).filter(EmailConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")
    
    if config.is_active == 1:
        raise HTTPException(status_code=400, detail="无法删除激活中的配置")
    
    try:
        db.delete(config)
        db.commit()
        return {"success": True, "message": "配置删除成功"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除配置失败: {str(e)}")
