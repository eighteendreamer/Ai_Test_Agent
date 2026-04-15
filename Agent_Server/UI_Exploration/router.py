"""
UI 探索 API 路由

接入接口（与前端 agent_web_server 兼容）：
  POST /api/knowledge/explore-page     — 启动 UI 探索
  GET  /api/knowledge/explore-status   — 查询探索状态
  POST /api/knowledge/stop-explore     — 停止探索并返回结果（前端调用的路径）
  POST /api/knowledge/explore-stop     — 停止探索（别名）
  POST /api/knowledge/explore-cancel   — 取消探索
  GET  /api/knowledge/explore-list     — 列出活跃探索会话
"""

import logging
from typing import Optional, List
from pydantic import BaseModel, Field
from fastapi import APIRouter, Query

from UI_Exploration.service import UIExploreService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["UI界面探索"])


# ─── 请求 / 响应模型（与前端字段对齐）─────────────────────────────

class ExplorePageRequest(BaseModel):
    """启动探索请求 - 兼容前端发送的所有字段名"""
    url: Optional[str] = Field(default=None, description="目标页面 URL")
    target_url: Optional[str] = Field(default=None, description="目标URL(别名)")
    user_instruction: str = Field(default="", description="额外的探索指令")
    goal: str = Field(default="", description="探索目标(前端字段)")
    user_goal: str = Field(default="", description="探索目标(前端字段)")
    focus_areas: List[str] = Field(default_factory=list, description="关注区域列表")
    max_steps: int = Field(default=30, ge=10, le=50, description="最大探索步数")
    login_info: dict = Field(default_factory=dict, description="登录信息字典")
    username: str = Field(default="", description="登录账号(前端字段)")
    password: str = Field(default="", description="登录密码(前端字段)")
    project_id: Optional[int] = Field(default=None, description="项目ID")
    projectId: Optional[int] = Field(default=None, description="项目ID(别名)")
    auto_store_knowledge: bool = Field(default=True, description="是否自动存入知识库")


class StopExploreRequest(BaseModel):
    """停止/取消请求 - 同时支持 task_id 和 session_id"""
    task_id: Optional[str] = Field(default=None, description="任务ID(前端字段)")
    session_id: Optional[str] = Field(default=None, description="会话ID")


def _resolve_session_id(req: StopExploreRequest) -> str:
    """从请求中提取会话/任务 ID"""
    sid = req.session_id or req.task_id
    if not sid:
        raise ValueError("必须提供 task_id 或 session_id")
    return sid


# ─── 接口实现 ──────────────────────────────────────────────────

@router.post("/explore-page")
async def explore_page(req: ExplorePageRequest):
    """
    **启动 UI 界面探索**

    前端 KnowledgeBase.vue 调用入口。
    返回 { success: true, data: { task_id: "xxx", ... } }
    """
    try:
        # ── 解析 URL（兼容多种字段名）──
        resolved_url = req.url or req.target_url or ""
        if not resolved_url:
            return {"success": False, "message": "URL 不能为空"}

        # ── 构建登录信息（兼容 username/password 和 login_info）──
        login_info = dict(req.login_info or {})
        if req.username:
            login_info["username"] = req.username
        if req.password:
            login_info["password"] = req.password

        # ── 解析用户指令（兼容 goal/user_goal/user_instruction）──
        user_instruction = req.user_goal or req.goal or req.user_instruction or ""

        # ── 解析项目 ID ──
        project_id = req.project_id or req.projectId

        # 调用服务层
        result = await UIExploreService.start(
            url=resolved_url,
            user_instruction=user_instruction,
            focus_areas=req.focus_areas,
            max_steps=req.max_steps,
            login_info=login_info,
            project_id=project_id,
        )

        if not result.get("success"):
            return result

        data = result["data"]
        session_id = data["session_id"]

        # ★ 关键：返回 task_id 字段供前端使用 ★
        response_data = {
            **data,
            "task_id": session_id,       # 前端用 task_id
        }

        # 如果配置了 auto_store，注册一个回调在完成后自动存储
        if req.auto_store_knowledge:
            import asyncio
            from database.connection import SessionLocal

            async def auto_store_when_done():
                """等待探索完成后自动存储结果（优先多页存储）"""
                from UI_Exploration.service import ExplorePostProcessor
                from UI_Exploration.explorer import ExploreSessionManager

                max_wait = 600
                poll_interval = 3
                elapsed = 0

                while elapsed < max_wait:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                    session = ExploreSessionManager.get(session_id)
                    if not session or session.status in ("completed", "failed", "cancelled"):
                        break

                if session and session.result:
                    try:
                        db_session = SessionLocal()

                        # 优先使用采集器的 site_result（每页独立存储）
                        site_result = getattr(session, '_site_result', None)
                        if site_result and hasattr(site_result, 'pages') and site_result.pages:
                            stored = await ExplorePostProcessor.persist_site_to_knowledge(
                                site_result, db=db_session, project_id=project_id
                            )
                            logger.info(f"[ExploreAPI] 会话 {session_id} 多页存储完成: {stored} 条记录")
                        else:
                            # 降级：旧方式单条存储
                            cleaned = ExplorePostProcessor.clean_result(session.result)
                            await ExplorePostProcessor.persist_to_knowledge(
                                cleaned, db=db_session, project_id=project_id
                            )
                            logger.info(f"[ExploreAPI] 会话 {session_id} 单条存储完成")
                    except Exception as store_err:
                        logger.warning(f"[ExploreAPI] 自动存储失败（不影响探索结果）: {store_err}")
                    finally:
                        try:
                            db_session.close()
                        except Exception:
                            pass

            asyncio.create_task(auto_store_when_done())

        return {
            "success": True,
            "message": f"已开始探索 {resolved_url}",
            "data": response_data,
        }

    except Exception as e:
        logger.error(f"[ExploreAPI] explore-page 失败: {e}", exc_info=True)
        return {"success": False, "message": f"启动探索失败: {str(e)}"}


@router.get("/explore-status")
async def explore_status(
    task_id: Optional[str] = Query(default=None, description="任务ID(前端字段)"),
    session_id: Optional[str] = Query(default=None, description="会话ID"),
):
    """
    **查询探索状态**
    支持 task_id 和 session_id 两种参数名。
    """
    try:
        sid = session_id or task_id
        if not sid:
            return {"success": False, "message": "缺少 task_id 或 session_id 参数"}
        result = await UIExploreService.get_status(sid)

        # 确保响应中包含 task_id 字段
        if isinstance(result.get("data"), dict) and "session_id" in result["data"]:
            result["data"]["task_id"] = result["data"]["session_id"]
        return result
    except Exception as e:
        logger.error(f"[ExploreAPI] explore-status 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/stop-explore")  # ← 前端调用的路径
@router.post("/explore-stop")   # ← 别名
async def explore_stop(req: StopExploreRequest):
    """
    **停止探索并返回结果**

    前端调用 POST /knowledge/stop-explore，同时注册 /explore-stop 别名。
    """
    try:
        sid = _resolve_session_id(req)
        result = await UIExploreService.cancel(sid)

        # stop-explore 对前端改为异步取消语义：立即返回，不等待后台任务自然收尾
        if isinstance(result.get("data"), dict) and "session_id" in result["data"]:
            result["data"]["task_id"] = result["data"]["session_id"]
        result["message"] = result.get("message") or "已发送停止信号，后台正在异步结束探索"
        return result
    except Exception as e:
        logger.error(f"[ExploreAPI] stop-explore 失败: {e}")
        return {"success": False, "message": str(e)}


@router.post("/explore-cancel")
async def explore_cancel(req: StopExploreRequest):
    """
    **取消探索**（支持 task_id/session_id）
    """
    try:
        sid = _resolve_session_id(req)
        return await UIExploreService.cancel(sid)
    except Exception as e:
        logger.error(f"[ExploreAPI] explore-cancel 失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/explore-list")
async def explore_list():
    """
    **列出活跃探索会话**
    """
    try:
        return await UIExploreService.list_sessions()
    except Exception as e:
        logger.error(f"[ExploreAPI] explore-list 失败: {e}")
        return {"success": False, "message": str(e)}
