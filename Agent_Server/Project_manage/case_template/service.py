"""
用例模板配置服务

从项目管理平台同步用例字段结构，作为全局用例生成模板。
"""
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from database.connection import CaseTemplateConfig, ProjectPlatformConfig

logger = logging.getLogger(__name__)

# 各平台的默认字段结构（当无法从平台拉取时使用）
_DEFAULT_FIELDS = [
    {"key": "module",       "label": "所属模块",   "required": True,  "type": "text"},
    {"key": "title",        "label": "用例标题",   "required": True,  "type": "text"},
    {"key": "precondition", "label": "前置条件",   "required": False, "type": "textarea"},
    {"key": "steps",        "label": "测试步骤",   "required": True,  "type": "steps"},
    {"key": "expected",     "label": "预期结果",   "required": True,  "type": "textarea"},
    {"key": "keywords",     "label": "关键词",     "required": False, "type": "text"},
    {"key": "priority",     "label": "优先级",     "required": True,  "type": "select"},
    {"key": "case_type",    "label": "用例类型",   "required": False, "type": "select"},
    {"key": "stage",        "label": "适用阶段",   "required": False, "type": "select"},
]

# 各平台字段映射（平台字段名 → 系统字段名）
_PLATFORM_FIELD_MAP = {
    "zentao": {
        "title": "title", "precondition": "precondition",
        "steps": "steps", "keywords": "keywords", "pri": "priority",
        "type": "case_type", "stage": "stage", "module": "module",
    },
    "jira": {
        "summary": "title", "description": "precondition",
        "priority": "priority", "issuetype": "case_type",
    },
    "tapd": {
        "name": "title", "precondition": "precondition",
        "steps": "steps", "priority": "priority",
        "case_type": "case_type", "module_id": "module",
    },
    "ones": {
        "name": "title", "desc": "precondition",
        "priority": "priority", "type": "case_type",
    },
    "pingcode": {
        "title": "title", "description": "precondition",
        "priority": "priority", "type": "case_type",
    },
}

_PLATFORM_PRIORITY_OPTIONS = {
    "zentao": [
        {"value": "1", "label": "1级（紧急）"},
        {"value": "2", "label": "2级（高）"},
        {"value": "3", "label": "3级（中）"},
        {"value": "4", "label": "4级（低）"},
    ],
    "jira": [
        {"value": "Highest", "label": "最高"},
        {"value": "High",    "label": "高"},
        {"value": "Medium",  "label": "中"},
        {"value": "Low",     "label": "低"},
        {"value": "Lowest",  "label": "最低"},
    ],
    "tapd": [
        {"value": "1", "label": "高"},
        {"value": "2", "label": "中"},
        {"value": "3", "label": "低"},
    ],
    "_default": [
        {"value": "1", "label": "高"},
        {"value": "2", "label": "中"},
        {"value": "3", "label": "低"},
        {"value": "4", "label": "最低"},
    ],
}

_PLATFORM_CASE_TYPE_OPTIONS = {
    "zentao": [
        {"value": "功能测试", "label": "功能测试"},
        {"value": "性能测试", "label": "性能测试"},
        {"value": "安全测试", "label": "安全测试"},
        {"value": "接口测试", "label": "接口测试"},
        {"value": "回归测试", "label": "回归测试"},
        {"value": "冒烟测试", "label": "冒烟测试"},
    ],
    "_default": [
        {"value": "功能测试", "label": "功能测试"},
        {"value": "性能测试", "label": "性能测试"},
        {"value": "安全测试", "label": "安全测试"},
        {"value": "接口测试", "label": "接口测试"},
        {"value": "回归测试", "label": "回归测试"},
    ],
}

_PLATFORM_STAGE_OPTIONS = {
    "zentao": [
        {"value": "单元测试", "label": "单元测试"},
        {"value": "功能测试", "label": "功能测试"},
        {"value": "集成测试", "label": "集成测试"},
        {"value": "系统测试", "label": "系统测试"},
        {"value": "验收测试", "label": "验收测试"},
    ],
    "_default": [
        {"value": "单元测试", "label": "单元测试"},
        {"value": "集成测试", "label": "集成测试"},
        {"value": "系统测试", "label": "系统测试"},
        {"value": "验收测试", "label": "验收测试"},
    ],
}


class CaseTemplateService:

    @staticmethod
    def get_active_template(db: Session) -> Optional[dict]:
        """获取当前启用的模板配置"""
        tpl = db.query(CaseTemplateConfig).filter(
            CaseTemplateConfig.is_active == 1
        ).order_by(CaseTemplateConfig.updated_at.desc()).first()
        return CaseTemplateService._to_dict(tpl) if tpl else None

    @staticmethod
    def get_template_for_llm(db: Session) -> dict:
        """
        获取注入 LLM prompt 的模板信息。
        若无自定义模板，返回系统默认值。
        """
        tpl = CaseTemplateService.get_active_template(db)
        if tpl:
            return tpl
        # 返回系统默认
        return {
            "source_platform": None,
            "template_name": "系统默认模板",
            "fields": _DEFAULT_FIELDS,
            "priority_options": _PLATFORM_PRIORITY_OPTIONS["_default"],
            "case_type_options": _PLATFORM_CASE_TYPE_OPTIONS["_default"],
            "stage_options": _PLATFORM_STAGE_OPTIONS["_default"],
            "extra_prompt": "",
        }

    @staticmethod
    def sync_from_platform(db: Session, platform_id: str) -> dict:
        """
        从指定平台同步用例字段结构，保存为全局模板。
        """
        pid = platform_id.lower()

        # 获取平台配置（用于显示来源名称）
        platform_cfg = db.query(ProjectPlatformConfig).filter(
            ProjectPlatformConfig.platform_id == pid
        ).first()
        platform_name = platform_cfg.platform_name if platform_cfg else pid

        # 构建字段列表（基于平台字段映射）
        field_map = _PLATFORM_FIELD_MAP.get(pid, {})
        if field_map:
            # 按平台字段映射构建字段定义
            fields = _build_fields_from_map(field_map)
        else:
            fields = list(_DEFAULT_FIELDS)

        priority_options = _PLATFORM_PRIORITY_OPTIONS.get(pid, _PLATFORM_PRIORITY_OPTIONS["_default"])
        case_type_options = _PLATFORM_CASE_TYPE_OPTIONS.get(pid, _PLATFORM_CASE_TYPE_OPTIONS["_default"])
        stage_options = _PLATFORM_STAGE_OPTIONS.get(pid, _PLATFORM_STAGE_OPTIONS["_default"])

        # 先将所有旧模板设为非激活
        db.query(CaseTemplateConfig).update({"is_active": 0})

        # 查找是否已有该平台的模板
        existing = db.query(CaseTemplateConfig).filter(
            CaseTemplateConfig.source_platform == pid
        ).first()

        if existing:
            existing.template_name = f"{platform_name} 模板"
            existing.fields = fields
            existing.priority_options = priority_options
            existing.case_type_options = case_type_options
            existing.stage_options = stage_options
            existing.is_active = 1
            existing.synced_at = datetime.now()
            db.commit()
            db.refresh(existing)
            logger.info(f"[CaseTemplate] 更新平台模板: {pid}")
            return {"success": True, "message": f"已从 {platform_name} 同步用例模板", "data": CaseTemplateService._to_dict(existing)}
        else:
            tpl = CaseTemplateConfig(
                source_platform=pid,
                template_name=f"{platform_name} 模板",
                fields=fields,
                priority_options=priority_options,
                case_type_options=case_type_options,
                stage_options=stage_options,
                is_active=1,
                synced_at=datetime.now(),
            )
            db.add(tpl)
            db.commit()
            db.refresh(tpl)
            logger.info(f"[CaseTemplate] 新建平台模板: {pid}")
            return {"success": True, "message": f"已从 {platform_name} 同步用例模板", "data": CaseTemplateService._to_dict(tpl)}

    @staticmethod
    def update_template(db: Session, template_id: int, data: dict) -> dict:
        """更新模板配置（用户自定义字段）"""
        tpl = db.query(CaseTemplateConfig).filter(CaseTemplateConfig.id == template_id).first()
        if not tpl:
            raise ValueError("模板不存在")
        for k, v in data.items():
            if hasattr(tpl, k) and k not in ("id", "created_at"):
                setattr(tpl, k, v)
        db.commit()
        db.refresh(tpl)
        return CaseTemplateService._to_dict(tpl)

    @staticmethod
    def reset_to_default(db: Session) -> dict:
        """重置为系统默认模板（停用所有自定义模板）"""
        db.query(CaseTemplateConfig).update({"is_active": 0})
        db.commit()
        return {"success": True, "message": "已重置为系统默认模板"}

    @staticmethod
    def list_templates(db: Session) -> list:
        """列出所有模板"""
        rows = db.query(CaseTemplateConfig).order_by(
            CaseTemplateConfig.is_active.desc(),
            CaseTemplateConfig.updated_at.desc()
        ).all()
        return [CaseTemplateService._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(tpl: CaseTemplateConfig) -> dict:
        return {
            "id": tpl.id,
            "source_platform": tpl.source_platform,
            "template_name": tpl.template_name,
            "fields": tpl.fields,
            "priority_options": tpl.priority_options,
            "case_type_options": tpl.case_type_options,
            "stage_options": tpl.stage_options,
            "extra_prompt": tpl.extra_prompt,
            "is_active": tpl.is_active,
            "synced_at": tpl.synced_at.isoformat() if tpl.synced_at else None,
            "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
            "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None,
        }


def _build_fields_from_map(field_map: dict) -> list:
    """根据平台字段映射构建字段定义列表"""
    # 系统字段的元数据
    _field_meta = {
        "title":        {"label": "用例标题",   "required": True,  "type": "text"},
        "module":       {"label": "所属模块",   "required": True,  "type": "text"},
        "precondition": {"label": "前置条件",   "required": False, "type": "textarea"},
        "steps":        {"label": "测试步骤",   "required": True,  "type": "steps"},
        "expected":     {"label": "预期结果",   "required": True,  "type": "textarea"},
        "keywords":     {"label": "关键词",     "required": False, "type": "text"},
        "priority":     {"label": "优先级",     "required": True,  "type": "select"},
        "case_type":    {"label": "用例类型",   "required": False, "type": "select"},
        "stage":        {"label": "适用阶段",   "required": False, "type": "select"},
    }
    # 收集映射到的系统字段（去重，保持顺序）
    seen = set()
    fields = []
    for sys_key in ["module", "title", "precondition", "steps", "expected", "keywords", "priority", "case_type", "stage"]:
        if sys_key in field_map.values() or sys_key in ["module", "title", "steps", "expected", "priority"]:
            if sys_key not in seen:
                seen.add(sys_key)
                meta = _field_meta.get(sys_key, {"label": sys_key, "required": False, "type": "text"})
                # 找到平台原始字段名
                platform_key = next((k for k, v in field_map.items() if v == sys_key), sys_key)
                fields.append({
                    "key": sys_key,
                    "label": meta["label"],
                    "required": meta["required"],
                    "type": meta["type"],
                    "platform_key": platform_key,
                })
    return fields
