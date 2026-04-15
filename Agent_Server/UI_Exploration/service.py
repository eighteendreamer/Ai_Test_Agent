"""
UI 探索服务层

封装探索流程的编排逻辑，提供高层接口供 Router 调用。
包含：启动探索、状态查询、取消探索、结果处理、知识库回写。

统一引擎（v3）：
  - 单一 Agent 引擎（explorer.py），内置采集器自动收集结构化数据
  - 高级工具（scan_page/probe_interactions）+ LLM 驱动决策
  - 每页独立存入 Qdrant
"""

import json
import logging
import asyncio
import traceback
from typing import Optional, Dict, Any, List

from Page_Knowledge.runtime_config import load_page_knowledge_runtime_config

logger = logging.getLogger(__name__)

# 全局存储：正在运行的探索任务（用于进度推送和取消）
_running_tasks: Dict[str, asyncio.Task] = {}
_cancel_events: Dict[str, asyncio.Event] = {}


async def _finalize_cancel_async(session_id: str, timeout: float = 5.0) -> None:
    """后台等待探索任务停止，避免取消接口阻塞前端请求。"""
    task = _running_tasks.get(session_id)
    if not task or task.done():
        return

    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        logger.warning(f"[UIExploreService] cancel timeout, force cancelling task: {session_id}")
        task.cancel()
    except Exception as e:
        logger.warning(f"[UIExploreService] cancel finalize failed for {session_id}: {e}")


def ensure_ui_exploration_runtime_config(db=None) -> Optional[dict]:
    """Reuse the DB-backed Page Knowledge runtime config inside old UI_Exploration."""
    try:
        cfg = load_page_knowledge_runtime_config(db=db)
        logger.info(
            "[UIExplore Runtime] Active config applied: collection=%s dim=%s model=%s",
            cfg.get("collection_name"),
            cfg.get("vector_size"),
            cfg.get("embedding_model"),
        )
        return cfg
    except Exception as e:
        logger.warning(f"[UIExplore Runtime] runtime config load failed: {e}")
        return None


class UIExploreService:
    """
    UI 探索服务（对外接口）

    提供完整的探索生命周期管理：
      1. start()    — 启动异步探索任务
      2. status()   — 查询会话状态和进度
      3. cancel()   — 取消正在运行的探索
      4. cleanup()  — 清理会话资源
    """

    # ─── 公开方法 ────────────────────────────────────────────────

    @staticmethod
    async def start(
        url: str,
        user_instruction: str = "",
        focus_areas: List[str] = None,
        max_steps: int = 30,
        login_info: Dict = None,
        project_id: int = None,
    ) -> Dict[str, Any]:
        """
        启动 UI 探索

        Args:
            url: 目标页面 URL
            user_instruction: 额外的探索指令
            focus_areas: 关注区域列表
            max_steps: 最大探索步数 (10-50)
            login_info: 登录信息 {"url": "...", "username": "...", "password": "..."}
            project_id: 项目 ID（可选，用于后续存入知识库）

        Returns:
            {"session_id": "xxx", "status": "exploring", ...}
        """
        from UI_Exploration.explorer import (
            ExploreRequest,
            ExploreSession,
            UIExplorerAgent,
            ExploreSessionManager,
        )
        ensure_ui_exploration_runtime_config()

        # 参数校验
        if not url:
            return {"success": False, "message": "URL 不能为空"}

        max_steps = max(10, min(100, max_steps))

        # 构建请求和会话
        request = ExploreRequest(
            url=url,
            user_instruction=user_instruction or "",
            focus_areas=focus_areas or [],
            max_steps=max_steps,
            login_info=login_info or {},
            project_id=project_id,
        )

        session = ExploreSessionManager.create(request)

        # 创建取消事件
        _cancel_events[session.session_id] = asyncio.Event()

        # 定义进度回调（记录到 session）
        async def on_step(progress_data):
            logger.debug(f"[UIExploreService] 步骤 {progress_data['step']}/{progress_data['total_steps']}: "
                        f"{progress_data.get('tools_called', [])}")

        # 启动后台探索任务（带错误捕获回调）
        agent = UIExplorerAgent()

        async def run_explore():
            """包装 explore 调用，确保异常被完整记录"""
            try:
                return await agent.explore(request, session, on_step_callback=on_step,cancel_event=_cancel_events[session.session_id])
            except Exception as e:
                logger.error(f"[UIExploreService] 后台任务异常: {e}\n{traceback.format_exc()}")
                session.status = "failed"
                session.error = str(e)
                raise

        task = asyncio.create_task(run_explore())
        _running_tasks[session.session_id] = task

        # 等待一小段时间确保启动成功
        await asyncio.sleep(0.5)

        if task.done() and task.exception():
            raise task.exception()

        return {
            "success": True,
            "data": {
                "session_id": session.session_id,
                "url": url,
                "status": "exploring",
                "max_steps": max_steps,
                "message": f"已开始探索 {url}",
            }
        }

    @staticmethod
    async def get_status(session_id: str) -> Dict[str, Any]:
        """获取探索会话状态"""
        from UI_Exploration.explorer import ExploreSessionManager

        session = ExploreSessionManager.get(session_id)
        if not session:
            return {"success": False, "message": "会话不存在或已过期"}

        # 检查任务是否已完成但还没更新状态
        task = _running_tasks.get(session_id)
        if task and task.done():
            try:
                task.result()
            except Exception as e:
                session.status = "failed"
                session.error = str(e)
                logger.error(f"[UIExploreService] 后台任务失败: {e}")

        data = session.to_dict()

        # 状态名映射：exploring → running（前端期望的格式）
        _STATUS_MAP = {"exploring": "running", "pending": "running"}
        mapped_status = _STATUS_MAP.get(data["status"], data["status"])
        data["status"] = mapped_status

        logger.debug(f"[UIExploreService] 状态查询: {session_id} -> {mapped_status} (步骤 {data.get('current_step', 0)})")
        return {
            "success": True,
            "data": data,
        }

    @staticmethod
    async def cancel(session_id: str) -> Dict[str, Any]:
        """取消正在运行的探索"""
        from UI_Exploration.explorer import ExploreSessionManager

        session = ExploreSessionManager.get(session_id)
        if not session:
            return {"success": False, "message": "会话不存在或已过期"}

        if session.status in ("completed", "failed", "cancelled"):
            return {"success": False, "message": f"会话已结束（{session.status}），无法取消"}

        # 触发取消事件
        cancel_event = _cancel_events.get(session_id)
        if cancel_event:
            cancel_event.set()

        session.status = "cancelled"
        session.error = "用户主动取消"
        logger.info(f"[UIExploreService] cancel requested: {session_id}")

        # 后台等待任务退出，接口立即返回，避免前端请求阻塞
        asyncio.create_task(_finalize_cancel_async(session_id))

        return {
            "success": True,
            "data": {"session_id": session_id, "status": "cancelled"},
            "message": "已发送取消信号",
        }

    @staticmethod
    async def stop_and_get_result(session_id: str) -> Dict[str, Any]:
        """
        停止探索并返回最终结果（阻塞等待完成）
        用于同步场景，等待探索完成后返回结构化数据。
        """
        from UI_Exploration.explorer import ExploreSessionManager

        session = ExploreSessionManager.get(session_id)
        if not session:
            return {"success": False, "message": "会话不存在"}

        task = _running_tasks.get(session_id)
        if task and not task.done():
            try:
                await task
            except Exception as e:
                session.status = "failed"
                session.error = str(e)

        result_data = session.to_dict()

        # 如果有结果且需要写入知识库
        if session.result:
            result_data["capabilities"] = session.result
            logger.info(f"[UIExploreService] 探索完成，结果包含 {len(session.result.get('modules', []))} 个模块")

        return {
            "success": True,
            "data": result_data,
        }

    @staticmethod
    async def cleanup(session_id: str) -> Dict[str, Any]:
        """清理会话资源"""
        from UI_Exploration.explorer import ExploreSessionManager

        ExploreSessionManager.remove(session_id)
        _running_tasks.pop(session_id, None)
        _cancel_events.pop(session_id, None)

        return {"success": True, "message": "资源已清理"}

    @staticmethod
    async def list_sessions() -> Dict[str, Any]:
        """列出所有活跃的探索会话"""
        from UI_Exploration.explorer import ExploreSessionManager

        sessions = ExploreSessionManager.list_active()
        return {
            "success": True,
            "data": {"sessions": sessions, "total": len(sessions)},
        }


# ═══════════════════════════════════════════════════════════════════
# 结果后处理 Hooks
# ═══════════════════════════════════════════════════════════════════

class ExplorePostProcessor:
    """
    探索结果后处理器（Hooks）

    对探索完成的原始数据进行清洗、增强和持久化：
      - 清洗无效/空数据
      - 补充推断字段（page_type、tech_stack）
      - 可选地写入 PageKnowledgeService
    """

    @staticmethod
    def clean_result(raw_result: dict) -> dict:
        """清洗原始探索结果"""
        cleaned = dict(raw_result)

        # 清理 modules
        modules = []
        for mod in raw_result.get("modules", []):
            if not mod.get("module_name") and not mod.get("elements"):
                continue
            # 清理空元素
            elements = [e for e in mod.get("elements", []) if e.get("name") or e.get("selector")]
            if elements or mod.get("module_name"):
                mod["elements"] = elements
                modules.append(mod)
        cleaned["modules"] = modules

        # 确保 summary 存在
        if not cleaned.get("summary"):
            module_names = [m.get("module_name", "") for m in modules]
            cleaned["summary"] = (
                f"页面「{cleaned.get('page_title', '')}」"
                f"({cleaned.get('page_type', 'unknown')})，"
                f"包含 {len(modules)} 个模块: {'、'.join(module_names[:5])}"
            )

        return cleaned

    @staticmethod
    async def persist_to_knowledge(result: dict, db=None, project_id: int = None) -> Optional[dict]:
        """
        将探索结果持久化到页面知识库（旧版单条存储）

        Args:
            result: 清洗后的探索结果
            db: 数据库 Session
            project_id: 项目 ID

        Returns:
            知识库存储结果
        """
        try:
            ensure_ui_exploration_runtime_config(db=db)
            from Page_Knowledge.service import PageKnowledgeService
            from Page_Knowledge.schema import PageKnowledge

            url = result.get("page_url", "")
            if not url:
                logger.warning("[PostProcessor] 无 URL，跳过知识库存储")
                return None

            knowledge = PageKnowledge.from_capabilities(url, result)
            store_result = await PageKnowledgeService.store(knowledge, db, project_id=project_id)

            logger.info(f"[PostProcessor] 已存入知识库: {url}")
            return store_result

        except ImportError as e:
            logger.warning(f"[PostProcessor] PageKnowledgeService 不可用: {e}")
            return None
        except Exception as e:
            logger.error(f"[PostProcessor] 知识库存储失败: {e}")
            return None

    @staticmethod
    async def persist_site_to_knowledge(
        site_result,
        db=None,
        project_id: int = None,
    ) -> int:
        """
        将探索结果持久化到页面知识库（每页一条记录）

        已存在的页面会自动更新（版本号+1），不会重复创建。

        Args:
            site_result: SiteExplorationResult 实例
            db: 数据库 Session
            project_id: 项目 ID

        Returns:
            成功存储/更新的记录数
        """
        import asyncio as _asyncio
        stored_count = 0
        try:
            ensure_ui_exploration_runtime_config(db=db)
            from Page_Knowledge.service import PageKnowledgeService
            from Page_Knowledge.schema import PageKnowledge
            from Page_Knowledge.vector_store import get_vector_store, generate_point_id

            store = get_vector_store()

            for page_map in site_result.pages:
                try:
                    knowledge = PageKnowledge.from_interaction_map(page_map)

                    # 查已有记录 → 版本号递增
                    point_id = generate_point_id(knowledge.url)
                    existing = await _asyncio.to_thread(store.get_by_id, point_id)
                    if existing and existing.get("payload"):
                        old_version = existing["payload"].get("version", 1)
                        knowledge.version = old_version + 1
                        action = "更新"
                    else:
                        knowledge.version = 1
                        action = "新增"

                    await PageKnowledgeService.store(
                        knowledge, db, project_id=project_id
                    )
                    stored_count += 1
                    logger.info(
                        f"[PostProcessor] {action}: {page_map.page_url} "
                        f"(v{knowledge.version}, {page_map.interaction_count} 个交互项)"
                    )
                except Exception as e:
                    logger.warning(f"[PostProcessor] 存储 {page_map.page_url} 失败: {e}")
                    continue

        except ImportError as e:
            logger.warning(f"[PostProcessor] PageKnowledgeService 不可用: {e}")
        except Exception as e:
            logger.error(f"[PostProcessor] 批量存储异常: {e}")

        return stored_count
