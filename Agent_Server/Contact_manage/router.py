"""
联系人管理 API 路由

作者: 程序员Eighteen
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel, EmailStr
from datetime import datetime

from database.connection import get_db, Contact

router = APIRouter(
    prefix="/api/contacts",
    tags=["联系人管理"]
)


class ContactCreate(BaseModel):
    name: str
    email: EmailStr
    role: str = None
    auto_receive_bug: int = 0


class ContactUpdate(BaseModel):
    name: str = None
    email: EmailStr = None
    role: str = None
    auto_receive_bug: int = None


class ContactResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str = None
    auto_receive_bug: int
    created_at: datetime
    updated_at: datetime = None

    class Config:
        from_attributes = True


@router.get("/list")
def get_contacts_list(
    limit: int = 20,
    offset: int = 0,
    search: str = None,
    db: Session = Depends(get_db)
):
    """获取联系人列表（分页）"""
    try:
        from sqlalchemy import func, or_
        
        query = db.query(Contact)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Contact.name.like(search_term),
                    Contact.email.like(search_term),
                    Contact.role.like(search_term)
                )
            )
        
        total = query.with_entities(func.count(Contact.id)).scalar()
        contacts = query.order_by(Contact.created_at.desc()).limit(limit).offset(offset).all()
        
        return {
            "success": True,
            "data": [
                {
                    "id": c.id,
                    "name": c.name,
                    "email": c.email,
                    "role": c.role,
                    "auto_receive_bug": c.auto_receive_bug,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None
                }
                for c in contacts
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取联系人列表失败: {str(e)}")


@router.get("/", response_model=List[ContactResponse])
def get_contacts(db: Session = Depends(get_db)):
    """获取所有联系人列表"""
    try:
        contacts = db.query(Contact).order_by(Contact.created_at.desc()).all()
        return contacts
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取联系人列表失败: {str(e)}")


@router.get("/{contact_id}", response_model=ContactResponse)
def get_contact(contact_id: int, db: Session = Depends(get_db)):
    """获取单个联系人详情"""
    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="联系人不存在")
    return contact


@router.post("/", response_model=dict)
def create_contact(contact_data: ContactCreate, db: Session = Depends(get_db)):
    """创建新联系人"""
    try:
        existing = db.query(Contact).filter(Contact.email == contact_data.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="该邮箱已存在")
        
        new_contact = Contact(
            name=contact_data.name,
            email=contact_data.email,
            role=contact_data.role,
            auto_receive_bug=contact_data.auto_receive_bug
        )
        
        db.add(new_contact)
        db.commit()
        db.refresh(new_contact)
        
        return {
            "success": True,
            "message": "联系人创建成功",
            "data": {
                "id": new_contact.id,
                "name": new_contact.name,
                "email": new_contact.email
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建联系人失败: {str(e)}")


@router.put("/{contact_id}", response_model=dict)
def update_contact(contact_id: int, contact_data: ContactUpdate, db: Session = Depends(get_db)):
    """更新联系人信息"""
    try:
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="联系人不存在")
        
        if contact_data.email and contact_data.email != contact.email:
            existing = db.query(Contact).filter(
                Contact.email == contact_data.email,
                Contact.id != contact_id
            ).first()
            if existing:
                raise HTTPException(status_code=400, detail="该邮箱已被其他联系人使用")
        
        update_data = contact_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(contact, field, value)
        
        contact.updated_at = datetime.now()
        db.commit()
        db.refresh(contact)
        
        return {
            "success": True,
            "message": "联系人更新成功",
            "data": {
                "id": contact.id,
                "name": contact.name,
                "email": contact.email
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新联系人失败: {str(e)}")


@router.delete("/{contact_id}", response_model=dict)
def delete_contact(contact_id: int, db: Session = Depends(get_db)):
    """删除联系人"""
    try:
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="联系人不存在")
        
        db.delete(contact)
        db.commit()
        
        return {
            "success": True,
            "message": "联系人删除成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"删除联系人失败: {str(e)}")
