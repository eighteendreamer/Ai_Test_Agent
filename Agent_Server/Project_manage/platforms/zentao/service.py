"""
禅道集成 - 服务层

Bug 推送 / 同步、用例导入。
配置管理已统一到 ProjectPlatformConfig 表。
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session
from database.connection import ProjectPlatformConfig, BugReport, ExecutionCase
from Project_manage.platforms.zentao.client import ZentaoClient

logger = logging.getLogger(__name__)

# 禅道用例类型映射：英文代码 -> 中文名称
ZENTAO_CASE_TYPE_MAPPING = {
    "unit": "单元测试",
    "interface": "接口测试",
    "feature": "功能测试",
    "install": "安装部署",
    "config": "配置相关",
    "performance": "性能测试",
    "security": "安全相关",
    "other": "其他",
}


def _map_zentao_case_type(zentao_type: str) -> str:
    """
    将禅道的用例类型英文代码映射为中文名称
    
    Args:
        zentao_type: 禅道返回的类型代码 (unit/interface/feature/install/config/performance/security/other)
    
    Returns:
        中文类型名称，如果未找到映射则返回"功能测试"
    """
    if not zentao_type:
        return "功能测试"
    
    # 转换为小写进行匹配
    zentao_type_lower = str(zentao_type).lower().strip()
    
    # 返回映射的中文名称，默认为"功能测试"
    return ZENTAO_CASE_TYPE_MAPPING.get(zentao_type_lower, "功能测试")


def _get_zentao_config(db: Session) -> Optional[ProjectPlatformConfig]:
    """获取已激活的禅道配置"""
    return db.query(ProjectPlatformConfig).filter(
        ProjectPlatformConfig.platform_id == 'zentao',
        ProjectPlatformConfig.is_active == 1
    ).first()


def _build_client(cfg: ProjectPlatformConfig) -> ZentaoClient:
    return ZentaoClient(
        base_url=cfg.base_url,
        account=cfg.account,
        password=cfg.password,
    )


class ZentaoBugService:

    @staticmethod
    async def push_bug(db: Session, bug_id: int,
                       product_id: int | None = None,
                       severity: int = 3, pri: int = 3) -> dict:
        """将本系统 Bug 推送到禅道"""
        cfg = _get_zentao_config(db)
        if not cfg:
            raise ValueError("请先在项目管理平台总控制台配置并激活禅道连接")

        bug: BugReport = db.query(BugReport).filter(BugReport.id == bug_id).first()
        if not bug:
            raise ValueError("Bug 不存在")
        if bug.zentao_bug_id:
            return {"success": False, "message": f"该 Bug 已推送到禅道 (ID={bug.zentao_bug_id})"}

        pid = product_id or cfg.default_product_id
        if not pid:
            raise ValueError("未指定产品ID，且禅道配置中未设置默认产品")

        logger.info(f"[Zentao] 推送 Bug id={bug_id} 到产品 product_id={pid}")

        steps = _format_bug_steps(bug)
        client = _build_client(cfg)
        result = await client.create_bug(
            product_id=pid,
            title=f"【AI Test Agent】{bug.bug_name}",
            steps=steps,
            severity=severity,
            pri=pri,
            bug_type=_map_error_type(bug.error_type),
        )

        logger.info(f"[Zentao] push_bug 原始响应: {result}")

        zentao_id = (
            result.get("id")
            or result.get("bugID")
            or (result.get("bug") or {}).get("id")
            or (result.get("data") or {}).get("id")
        )

        if not zentao_id:
            error_msg = (
                result.get("error")
                or result.get("message")
                or result.get("msg")
                or f"禅道响应中未找到 Bug ID，原始响应: {result}"
            )
            logger.error(f"[Zentao] Bug 创建失败: {error_msg}")
            return {"success": False, "message": str(error_msg)}

        bug.zentao_bug_id = int(zentao_id)
        db.commit()

        return {
            "success": True,
            "zentao_bug_id": int(zentao_id),
            "message": f"Bug 已成功推送到禅道 (ID={zentao_id})"
        }

    @staticmethod
    async def batch_push_bugs(db: Session, bug_ids: list[int],
                              product_id: int | None = None) -> dict:
        """批量推送 Bug 到禅道"""
        results = []
        for bid in bug_ids:
            try:
                r = await ZentaoBugService.push_bug(db, bid, product_id)
                results.append({"bug_id": bid, **r})
            except Exception as e:
                results.append({"bug_id": bid, "success": False, "message": str(e)})
        success_count = sum(1 for r in results if r.get("success"))
        return {
            "total": len(bug_ids),
            "success_count": success_count,
            "failed_count": len(bug_ids) - success_count,
            "details": results
        }

    @staticmethod
    async def sync_bug_status(db: Session, bug_id: int | None = None) -> dict:
        """从禅道同步 Bug 状态到本地"""
        cfg = _get_zentao_config(db)
        if not cfg:
            raise ValueError("请先在项目管理平台总控制台配置并激活禅道连接")

        client = _build_client(cfg)

        if bug_id:
            bugs = [db.query(BugReport).filter(BugReport.id == bug_id).first()]
        else:
            bugs = db.query(BugReport).filter(BugReport.zentao_bug_id.isnot(None)).all()

        synced = 0
        errors = []
        for bug in bugs:
            if not bug or not bug.zentao_bug_id:
                continue
            try:
                remote = await client.get_bug(bug.zentao_bug_id)
                remote_status = remote.get("status", "")
                local_status = _map_zentao_status(remote_status)
                if local_status and bug.status != local_status:
                    bug.status = local_status
                    synced += 1
            except Exception as e:
                errors.append({"bug_id": bug.id, "error": str(e)})

        db.commit()
        return {
            "total": len(bugs),
            "synced": synced,
            "errors": errors,
            "message": f"同步完成，更新 {synced} 条"
        }


class ZentaoCaseService:

    @staticmethod
    async def import_cases(db: Session, product_id: int | None = None,
                           suite_id: int | None = None,
                           case_ids: list[int] | None = None,
                           project_id: int = 1,
                           limit: int | None = None,
                           concurrency: int = 5) -> dict:
        """从禅道并发导入测试用例到本地用例库（支持增量导入和项目隔离）"""
        cfg = _get_zentao_config(db)
        if not cfg:
            raise ValueError("请先在项目管理平台总控制台配置并激活禅道连接")

        client = _build_client(cfg)
        _conc = max(1, concurrency or 5)

        def _apply_limit(id_list):
            return id_list[:limit] if limit and limit > 0 else id_list

        # 步骤1：查询当前项目已导入的禅道用例ID集合
        existing_zentao_ids = {
            row[0] for row in db.query(ExecutionCase.zentao_case_id)
            .filter(ExecutionCase.project_id == project_id)
            .filter(ExecutionCase.zentao_case_id.isnot(None))
            .all()
        }
        
        logger.info(f"[Zentao] 项目 {project_id} 已导入 {len(existing_zentao_ids)} 个禅道用例")

        # 步骤2：获取禅道所有用例ID列表（不应用limit）
        if case_ids:
            cids = case_ids  # 用户指定的用例ID
        elif suite_id:
            raw = await client.get_test_suite_cases(suite_id)
            cids = _extract_case_ids(raw)  # 获取所有用例ID
        elif product_id:
            raw = await client.get_product_cases(product_id)
            cids = _extract_case_ids(raw)  # 获取所有用例ID
        else:
            pid = cfg.default_product_id
            if not pid:
                raise ValueError("需要指定 product_id 或 suite_id")
            raw = await client.get_product_cases(pid)
            cids = _extract_case_ids(raw)  # 获取所有用例ID

        logger.info(f"[Zentao] 从禅道获取到 {len(cids)} 个用例ID")

        # 步骤3：过滤已导入的用例ID（实现增量导入）
        new_case_ids = [cid for cid in cids if cid not in existing_zentao_ids]
        
        logger.info(f"[Zentao] 发现 {len(new_case_ids)} 个未导入的用例")
        
        if not new_case_ids:
            logger.info(f"[Zentao] 所有用例已存在，无需导入")
            return {
                "success": True,
                "total_fetched": len(cids),
                "new_cases": 0,
                "imported": 0,
                "skipped": len(cids),
                "message": f"所有 {len(cids)} 条用例已存在，无需导入"
            }

        # 步骤4：应用limit（只限制本次导入的数量）
        limited_new_ids = _apply_limit(new_case_ids)
        
        logger.info(f"[Zentao] 本次将导入 {len(limited_new_ids)} 个用例（limit={limit or '无限制'}）")

        # 步骤5：只拉取新用例的详细信息
        cases_data = await _fetch_cases_detail(client, limited_new_ids, _conc)

        # 步骤5：导入新用例
        imported = 0
        skipped = 0
        for c in cases_data:
            zentao_id = c.get("id")
            title = c.get("title", "").strip()
            
            if not title or not zentao_id:
                logger.warning(f"[Zentao] 用例数据不完整，跳过: zentao_id={zentao_id}, title={title}")
                continue
            
            # 二次检查（防止并发导入冲突）
            if zentao_id in existing_zentao_ids:
                skipped += 1
                continue
            
            ec = ExecutionCase(
                project_id=project_id,
                zentao_case_id=zentao_id,
                title=title,
                module=c.get("module", "禅道导入"),
                precondition=c.get("precondition", ""),
                steps=c.get("steps", "[]"),
                expected=c.get("expected", ""),
                keywords=c.get("keywords", ""),
                priority=str(c.get("priority", 3)),
                case_type=c.get("type", "功能测试"),
            )
            db.add(ec)
            existing_zentao_ids.add(zentao_id)
            imported += 1

        db.commit()
        
        already_existed = len(cids) - len(new_case_ids)  # 之前已存在的数量
        logger.info(f"[Zentao] 导入完成: 新增 {imported} 条，本次跳过 {skipped} 条，之前已存在 {already_existed} 条")
        
        return {
            "success": True,
            "total_fetched": len(cids),
            "new_cases": len(new_case_ids),
            "imported": imported,
            "skipped": already_existed + skipped,
            "message": f"成功导入 {imported} 条新用例，跳过已存在 {already_existed} 条（禅道共 {len(cids)} 条，还有 {len(new_case_ids) - imported} 条未导入）"
        }

    @staticmethod
    async def list_products(db: Session) -> list:
        """获取禅道产品列表"""
        cfg = _get_zentao_config(db)
        if not cfg:
            raise ValueError("请先在项目管理平台总控制台配置并激活禅道连接")
        client = _build_client(cfg)
        return await client.get_products()


# =====================================================================
# 内部辅助函数
# =====================================================================

def _format_bug_steps(bug: BugReport) -> str:
    parts = ["<p><strong>[步骤]</strong></p>"]
    if bug.reproduce_steps:
        parts.append(f"<p>{bug.reproduce_steps}</p>")
    parts.append("<p><strong>[结果]</strong></p>")
    if bug.actual_result:
        parts.append(f"<p>{bug.actual_result}</p>")
    elif bug.result_feedback:
        parts.append(f"<p>{bug.result_feedback}</p>")
    parts.append("<p><strong>[期望]</strong></p>")
    if bug.expected_result:
        parts.append(f"<p>{bug.expected_result}</p>")
    if bug.location_url:
        parts.append(f"<p><strong>[定位地址]</strong> {bug.location_url}</p>")
    if bug.description:
        parts.append(f"<p><strong>[描述]</strong> {bug.description}</p>")
    return "\n".join(parts)


_ERROR_TYPE_MAP = {
    "功能缺陷": "codeerror",
    "UI异常": "interface",
    "性能问题": "performance",
    "安全漏洞": "security",
    "兼容性问题": "compatible",
    "配置错误": "config",
}


def _map_error_type(error_type: str) -> str:
    return _ERROR_TYPE_MAP.get(error_type, "codeerror")


_ZENTAO_STATUS_MAP = {
    "active": "待处理",
    "resolved": "已修复",
    "closed": "已关闭",
}


def _map_zentao_status(zentao_status: str) -> str | None:
    return _ZENTAO_STATUS_MAP.get(zentao_status)


def _extract_case(raw: dict) -> dict:
    case_data = raw.get("data", raw)
    if isinstance(case_data, str):
        import json
        try:
            case_data = json.loads(case_data)
        except Exception:
            return {}

    case_obj = case_data.get("case", case_data)
    steps_list = case_obj.get("steps", {})
    steps_text = []
    expected_text = []
    if isinstance(steps_list, dict):
        for _, step in sorted(steps_list.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
            if isinstance(step, dict):
                steps_text.append(step.get("desc", ""))
                expected_text.append(step.get("expect", ""))
    elif isinstance(steps_list, list):
        for step in steps_list:
            if isinstance(step, dict):
                steps_text.append(step.get("desc", ""))
                expected_text.append(step.get("expect", ""))

    import json
    return {
        "id": case_obj.get("id"),  # 禅道用例ID
        "title": case_obj.get("title", ""),
        "module": case_obj.get("moduleName", case_obj.get("module", "")),
        "precondition": case_obj.get("precondition", ""),
        "steps": json.dumps([{"step": s, "expected": e}
                             for s, e in zip(steps_text, expected_text)],
                            ensure_ascii=False),
        "expected": "\n".join(e for e in expected_text if e),
        "keywords": case_obj.get("keywords", ""),
        "priority": case_obj.get("pri", 3),
        "type": _map_zentao_case_type(case_obj.get("type", "")),
    }


def _extract_case_ids(raw) -> list:
    ids = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                cid = _parse_case_id(item["id"])
                if cid:
                    ids.append(cid)
        return ids

    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, str):
        import json
        try:
            data = json.loads(data)
        except Exception:
            return []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                cid = _parse_case_id(item["id"])
                if cid:
                    ids.append(cid)
        return ids

    case_list = data.get("cases", data.get("testcases", []))
    if isinstance(case_list, dict):
        case_list = list(case_list.values())

    for item in case_list:
        if isinstance(item, dict) and item.get("id"):
            cid = _parse_case_id(item["id"])
            if cid:
                ids.append(cid)
    return ids


def _parse_case_id(raw_id) -> int | None:
    import re
    s = str(raw_id).strip()
    m = re.search(r'\d+', s)
    if m:
        return int(m.group())
    return None


async def _fetch_cases_detail(client, case_ids: list, concurrency: int = 5) -> list:
    """并发拉取用例完整详情"""
    import asyncio
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _fetch_one(cid):
        async with semaphore:
            try:
                raw = await client.get_test_case(cid)
                c = _extract_case(raw)
                return c if c.get("title") else None
            except Exception as e:
                logger.warning(f"[Zentao] 拉取用例详情 id={cid} 失败: {e}")
                return None

    results = await asyncio.gather(*[_fetch_one(cid) for cid in case_ids])
    return [r for r in results if r is not None]
