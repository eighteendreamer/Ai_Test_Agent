"""
一键测试 - 核心服务

LLM 编排：分析意图 → 查询数据库 → 获取环境 → 页面分析 → 生成用例 → 执行测试

特性：
- 停止功能：通过 asyncio.Event 取消正在运行的任务 + 关闭浏览器
- 浏览器复用：所有用例共享一个 BrowserSession
- 429 限流检测：遇到配额耗尽立即停止后续用例
- 循环检测：集成 LoopDetector 防止 Agent 陷入无限循环
- 自动切换：集成 ModelAutoSwitcher 在模型失败时自动切换
- Token 统计：按会话追踪 Token 使用量
"""
import json
import os
import time
import asyncio
import logging
import traceback
from typing import Dict, List, Optional, Any
from datetime import datetime

from sqlalchemy.orm import Session

from database.connection import (
    ExecutionCase, OneclickSession, Skill,
    ExecutionBatch, TestRecord, TestReport, BugReport,
    TestEnvironment,
)
from llm.client import get_llm_client
from llm.auto_switch import get_auto_switcher, classify_failure_reason
from Api_request.prompts import (
    ONECLICK_INTENT_ANALYSIS_V2_SYSTEM,
    ONECLICK_INTENT_ANALYSIS_V2_USER_TEMPLATE,
    ONECLICK_GENERATE_CASES_V2_SYSTEM,
    ONECLICK_GENERATE_CASES_V2_USER_TEMPLATE,
    ONECLICK_SUBTASK_GENERATION_SYSTEM,
    ONECLICK_SUBTASK_GENERATION_USER_TEMPLATE,
    PAGE_CAPABILITY_ABSTRACTION_SYSTEM,
    PAGE_CAPABILITY_ABSTRACTION_USER_TEMPLATE,
    TASK_TREE_FEATURE_PLANNING_SYSTEM,
    TASK_TREE_FEATURE_PLANNING_USER_TEMPLATE,
    TASK_TREE_ATOMIC_PLANNING_SYSTEM,
    TASK_TREE_ATOMIC_PLANNING_USER_TEMPLATE,
)
from OneClick_Test.session import SessionManager
from OneClick_Test.skill_manager import SkillManager
from OneClick_Test.loop_detection import LoopDetector, LoopDetectionConfig
from OneClick_Test.task_tree import TaskTree, TaskNode, NodeStatus
from Page_Knowledge.service import PageKnowledgeService
from Page_Knowledge.schema import PageKnowledge

logger = logging.getLogger(__name__)


# ========== 全局运行状态管理 ==========
_running_sessions: Dict[int, Dict[str, Any]] = {}


def _build_runtime_state(
    cancel_event: asyncio.Event,
    loop_detector: Any = None,
) -> Dict[str, Any]:
    return {
        "cancel_event": cancel_event,
        "browser_session": None,
        "loop_detector": loop_detector,
        "engine": "",
        "current_task": "",
        "events": [],
        "artifact_summary": None,
        "session_snapshot": None,
        "session_completion": None,
        "assigned_task": None,
    }


def _serialize_runtime_state(runtime_state: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not runtime_state:
        return None
    return {
        "engine": runtime_state.get("engine", "") or "task_driven",
        "current_task": runtime_state.get("current_task", ""),
        "artifact_summary": runtime_state.get("artifact_summary"),
        "session_snapshot": runtime_state.get("session_snapshot"),
        "session_completion": runtime_state.get("session_completion"),
        "assigned_task": runtime_state.get("assigned_task"),
        "events": list((runtime_state.get("events", []))[-20:]),
    }


def _extract_artifact_summary(page_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    page_data = page_data or {}
    session_summary = page_data.get("session_summary") or {}
    return page_data.get("artifact_summary") or session_summary.get("artifact_summary")


class OneClickService:
    """一键测试核心服务"""

    # ========== Phase 1: 启动会话 & 分析意图 ==========

    @staticmethod
    async def start_session(db: Session, user_input: str, skill_ids: List[int] = None, project_id: int = None) -> Dict:
        """
        启动一键测试会话（全自主流程）

        流程：
        1. 创建会话
        2. LLM 分析用户意图（快速返回）
        3. 从数据库获取测试环境
        4. 后台异步执行：知识库查询 → 子任务生成 → 用例生成
        5. 前端通过轮询 session 获取进度
        """
        # 创建会话
        session = SessionManager.create_session(db, user_input, project_id=project_id)
        session_id = session.id

        try:
            # 更新状态
            SessionManager.update_status(db, session, 'analyzing')
            SessionManager.add_message(db, session, 'assistant', '🔍 正在分析您的需求...')

            # 1. LLM 分析意图（增强版）
            intent = await OneClickService._analyze_intent_v2(user_input, db)
            logger.info(f"[OneClick] 意图分析: {intent}")
            SessionManager.add_message(
                db, session, 'assistant',
                f'✅ 意图分析完成: {intent.get("test_scope", user_input)}'
            )

            # 2. 获取测试环境
            env_info = OneClickService._resolve_environment(intent, db)
            target_url = env_info.get('base_url', '')

            if not target_url:
                SessionManager.add_message(db, session, 'assistant',
                    '⚠️ 未找到测试环境配置，请在「测试环境」中配置或在指令中提供URL')
                session.status = 'failed'
                db.commit()
                return {"success": False, "session_id": session_id,
                        "message": "未找到测试环境，请先配置或在指令中提供URL"}

            session.target_url = target_url
            session.login_info = json.dumps(env_info, ensure_ascii=False)

            env_source = env_info.get('_source', '环境变量')
            SessionManager.add_message(
                db, session, 'assistant',
                f'✅ 测试环境: {target_url}（来源: {env_source}）'
            )

            if skill_ids:
                session.skill_ids = json.dumps(skill_ids)
            db.commit()

            # 3. 启动后台异步任务（知识库查询 + 生成）
            SessionManager.add_message(db, session, 'assistant',
                '📚 正在查询页面知识并准备生成用例，请稍候...')
            db.commit()

            asyncio.create_task(
                OneClickService._background_explore_and_generate(
                    session_id, user_input, intent, env_info, skill_ids
                )
            )

            # 快速返回，前端通过轮询获取后续进度
            return {
                "success": True,
                "session_id": session_id,
                "status": session.status,
                "data": {
                    "intent": intent,
                    "existing_cases_count": 0,
                    "target_url": target_url,
                    "env_source": env_source,
                    "page_exploration": None,
                    "subtasks": [],
                    "generated_cases": [],
                    "messages": SessionManager.get_messages(session),
                }
            }

        except Exception as e:
            logger.error(f"[OneClick] 启动失败: {e}\n{traceback.format_exc()}")
            session.status = 'failed'
            SessionManager.add_message(db, session, 'assistant', f'❌ 分析失败: {str(e)}')
            db.commit()
            return {"success": False, "session_id": session_id, "message": str(e)}

    @staticmethod
    async def _background_explore_and_generate(
        session_id: int, user_input: str, intent: Dict,
        env_info: Dict, skill_ids: List[int] = None
    ):
        """
        后台异步执行：知识库查询 → 子任务生成 → 用例生成
        独立数据库会话，不阻塞前端请求
        """
        from database.connection import SessionLocal
        db = SessionLocal()

        # 注册 cancel_event，使 stop_session 能在运行阶段发送取消信号
        cancel_event = asyncio.Event()
        _running_sessions[session_id] = _build_runtime_state(
            cancel_event=cancel_event,
            loop_detector=None,
        )

        def _is_cancelled() -> bool:
            """检查会话是否已被手动停止"""
            if cancel_event.is_set():
                return True
            # 双重检查：从数据库刷新状态
            db.refresh(session)
            return session.status == 'failed'

        try:
            session = SessionManager.get_session(db, session_id)
            if not session:
                logger.error(f"[OneClick] 后台任务：会话 {session_id} 不存在")
                return

            # 1. 知识库查询（命中则跳过，直接生成）
            SessionManager.update_status(db, session, 'page_scanned')

            target_url = env_info.get('base_url', session.target_url or '')
            login_url = env_info.get('login_url', '')
            kb_hit = None
            try:
                # 用 login_url + base_url 精确查知识库
                kb_hit = await PageKnowledgeService.lookup_by_env_urls(
                    base_url=target_url,
                    login_url=login_url,
                    user_input=user_input,
                )
            except Exception as kb_err:
                logger.warning(f"[OneClick] 知识库查询失败（不影响流程）: {kb_err}")

            page_data = {}
            page_capabilities = None

            # 判断是否为多模块测试
            scope_type = intent.get('scope_type', 'single_page')
            is_multi_module = scope_type == 'multi_module'

            if kb_hit and kb_hit.get('hit'):
                # ━━ 知识库命中：用 LLM 判断是否满足需求 ━━
                knowledge = kb_hit['knowledge']
                matched_url = kb_hit.get('matched_url', target_url)

                if kb_hit.get('stale'):
                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'⏰ 知识库已老化（{matched_url}），将重新获取页面信息...'
                    )
                    kb_hit = None
                else:
                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'📚 知识库命中（{matched_url}），正在评估是否满足需求...'
                    )
                    # LLM 判断充分性
                    try:
                        sufficiency = await PageKnowledgeService.assess_sufficiency_with_llm(
                            knowledge=knowledge,
                            user_input=user_input,
                        )
                    except Exception as e:
                        logger.warning(f"[OneClick] 充分性判断失败: {e}")
                        sufficiency = {"is_sufficient": False, "reason": str(e),
                                       "matched_elements": [], "missing_elements": []}

                    is_sufficient = sufficiency.get('is_sufficient', False)
                    reason = sufficiency.get('reason', '')
                    matched_elements = sufficiency.get('matched_elements', [])
                    missing_elements = sufficiency.get('missing_elements', [])

                    if is_sufficient:
                        SessionManager.add_message(
                            db, session, 'assistant',
                            f'⚡ 知识库满足需求，跳过页面分析\n原因：{reason}'
                        )
                        if matched_elements:
                            SessionManager.add_message(
                                db, session, 'assistant',
                                f'✅ 已覆盖：{", ".join(matched_elements[:10])}'
                            )
                        page_capabilities = knowledge.to_dict() if isinstance(knowledge, PageKnowledge) else knowledge
                        page_data = page_capabilities
                        session.page_analysis = json.dumps(page_data, ensure_ascii=False)
                        session.page_capabilities = json.dumps(page_capabilities, ensure_ascii=False)
                        if not SessionManager.update_status(db, session, 'page_scanned'):
                            return
                        db.commit()
                        logger.info(f"[OneClick] 知识库足够，跳过分析: {matched_url}")
                    else:
                        SessionManager.add_message(
                            db, session, 'assistant',
                            f'⚠️ 知识库内容不足，需要重新分析页面\n原因：{reason}'
                        )
                        if missing_elements:
                            SessionManager.add_message(
                                db, session, 'assistant',
                                f'❌ 缺失功能：{", ".join(missing_elements[:10])}'
                            )
                        kb_hit = None  # 触发页面分析
            
            if not kb_hit or not kb_hit.get('hit'):
                # ━━ 知识库未命中 / 已老化 / 覆盖不足 ━━
                SessionManager.add_message(
                    db, session, 'assistant',
                    '📋 知识库未命中，将基于已有信息和 LLM 规划能力生成用例...'
                )
                
                # 降级：标记 page_scanned 并继续
                if not SessionManager.update_status(db, session, 'page_scanned'):
                    return
                db.commit()

                if _is_cancelled():
                    return

                SessionManager.add_message(db, session, 'assistant', '📋 正在规划测试子任务...')
                subtasks = await OneClickService._generate_subtasks(user_input, page_data)
                subtask_list = subtasks.get("subtasks", [])
                total_est = subtasks.get("total_estimated_cases", 0)
                SessionManager.add_message(
                    db, session, 'assistant',
                    f'✅ 已规划 {len(subtask_list)} 个子任务，预计生成 {total_est} 条测试用例'
                )
                existing_cases = OneClickService._query_related_cases(db, intent)
                await OneClickService._generate_test_cases(
                    db, session, user_input, intent, existing_cases, env_info,
                    skill_ids, page_data=page_data, subtasks=subtasks
                )
                return

            if not page_capabilities:
                page_capabilities = page_data

            if _is_cancelled():
                return

            # ── L2 功能规划（注入 RAG 上下文）─────────────────
            if not SessionManager.update_status(db, session, 'feature_planning'):
                logger.info(f"[OneClick] ⏹️ 状态跳转失败，任务中止: session_id={session_id}")
                return
            SessionManager.add_message(db, session, 'assistant', '📐 正在规划功能测试模块（L2任务树）...')

            # 检索 RAG 上下文
            rag_context_text = ""
            try:
                rag_domain = intent.get('target_module', '')
                rag_query = f"{user_input} {rag_domain}"
                rag_contexts = await PageKnowledgeService.retrieve_context(
                    query=rag_query, domain=rag_domain, limit=3
                )
                if rag_contexts:
                    rag_context_text = PageKnowledgeService.build_rag_prompt_context(rag_contexts)
                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'📖 已检索到 {len(rag_contexts)} 条相关知识库上下文'
                    )
            except Exception as rag_err:
                logger.warning(f"[OneClick] RAG 上下文检索失败（不影响流程）: {rag_err}")

            feature_plan = await OneClickService._plan_feature_tasks(
                user_input, page_capabilities or page_data, rag_context=rag_context_text
            )
            l2_list = feature_plan.get("l2_nodes", [])
            l1_name = feature_plan.get("l1_name", intent.get("test_scope", user_input))
            total_est = feature_plan.get("total_estimated_cases", 0)
            SessionManager.add_message(
                db, session, 'assistant',
                f'✅ 规划出 {len(l2_list)} 个功能测试模块，预计 {total_est} 条用例'
            )
            db.commit()

            if _is_cancelled():
                logger.info(f"[OneClick] ⏹️ 后台任务已取消（L3规划前）: session_id={session_id}")
                return

            # ── L3 原子任务规划 ─────────────────────
            if not SessionManager.update_status(db, session, 'atomic_planning'):
                logger.info(f"[OneClick] ⏹️ 状态跳转失败，任务中止: session_id={session_id}")
                return
            SessionManager.add_message(db, session, 'assistant', '⚙️ 正在为每个模块设计原子测试用例（L3任务树）...')
            task_tree = await OneClickService._build_task_tree(
                user_input, l1_name, feature_plan, page_capabilities or page_data,
                env_info=env_info
            )
            # 保存任务树
            session.task_tree = json.dumps(task_tree.to_dict(), ensure_ascii=False)
            db.commit()

            # 同时保存扁平化用例（向下兼容确认/执行逻辑）
            all_cases = [n.test_case for n in task_tree.get_all_l3() if n.test_case]
            session.generated_cases = json.dumps(all_cases, ensure_ascii=False)
            l3_count = len(all_cases)
            SessionManager.add_message(
                db, session, 'assistant',
                f'✅ 任务树构建完成！共 {len(l2_list)} 个模块 × {l3_count} 条原子用例',
                extra={"type": "task_tree_ready", "l2_count": len(l2_list), "l3_count": l3_count}
            )

            # 进入 task_tree_ready 状态，等待用户确认
            if not SessionManager.update_status(db, session, 'task_tree_ready'):
                # 降级兼容：直接进入 cases_generated
                session.status = 'cases_generated'
            db.commit()

            logger.info(f"[OneClick] ✅ 后台任务完成: session_id={session_id}")

        except Exception as e:
            logger.error(f"[OneClick] 后台任务失败: {e}\n{traceback.format_exc()}")
            try:
                session = SessionManager.get_session(db, session_id)
                if session and session.status not in ('cases_generated', 'completed', 'failed'):
                    session.status = 'failed'
                    SessionManager.add_message(db, session, 'assistant',
                        f'❌ 后台处理失败: {str(e)}')
                    db.commit()
            except Exception:
                pass
        finally:
            _running_sessions.pop(session_id, None)
            db.close()

    # ========== 任务树确认接口 ==========

    @staticmethod
    async def confirm_task_tree(
        db: Session,
        session_id: int,
        selections: Dict[str, bool],  # { node_id: checked }
    ) -> Dict:
        """
        接受用户对任务树的勾选操作并触发执行

        selections 支持：
        - L2 节点 ID → 控制整个模块是否执行
        - L3 节点 ID → 控制单条用例是否执行
        - 传入 None/空字典 → 全部确认执行

        返回更新后的任务树统计信息
        """
        session = SessionManager.get_session(db, session_id)
        if not session:
            return {"success": False, "message": "会话不存在"}

        if session.status not in ('task_tree_ready', 'cases_generated', 'confirmed'):
            return {"success": False, "message": f"状态 '{session.status}' 不支持任务树确认"}

        if not session.task_tree:
            return {"success": False, "message": "该会话没有任务树，请使用传统确认接口"}

        try:
            tree = TaskTree.from_dict(
                json.loads(session.task_tree) if isinstance(session.task_tree, str)
                else session.task_tree
            )

            if selections:
                tree.apply_user_selection(selections)
            else:
                tree.confirm_all()

            # 更新任务树状态
            session.task_tree = json.dumps(tree.to_dict(), ensure_ascii=False)

            # 同步扁平化用例（兼容旧执行逻辑）
            confirmed_cases = tree.get_confirmed_cases()
            session.confirmed_cases = json.dumps(confirmed_cases, ensure_ascii=False)
            session.status = 'confirmed'
            db.commit()

            stats = tree.stats()
            SessionManager.add_message(
                db, session, 'user',
                f'确认执行任务树：{stats["confirmed"]} 条用例（共 {stats["total"]} 条）'
            )
            SessionManager.update_status(db, session, 'executing')
            SessionManager.add_message(db, session, 'assistant', '🚀 开始按任务树执行测试...')
            db.commit()

            # 后台异步执行
            asyncio.create_task(
                OneClickService._background_execute_tree(session_id, tree, confirmed_cases)
            )

            return {
                "success": True,
                "session_id": session_id,
                "status": "executing",
                "stats": stats,
                "data": {"messages": SessionManager.get_messages(session)},
            }

        except Exception as e:
            logger.error(f"[OneClick] 任务树确认失败: {e}\n{traceback.format_exc()}")
            return {"success": False, "message": str(e)}

    @staticmethod
    async def _background_execute_tree(
        session_id: int, tree: TaskTree, cases: List[Dict]
    ):
        """
        后台树驱动执行（Tree-Driven Execution）

        按 L2 → L3 顺序执行：
        - 支持 L2 模块级暂停/跳过
        - 实时更新树节点状态
        - 执行完成后更新任务树持久化
        """
        from database.connection import SessionLocal
        db = SessionLocal()

        try:
            session = SessionManager.get_session(db, session_id)
            if not session:
                return

            result = await OneClickService._execute_tests_by_tree(db, session, tree)

            session = SessionManager.get_session(db, session_id)
            if not session:
                return

            # 回写更新后的任务树
            session.task_tree = json.dumps(tree.to_dict(), ensure_ascii=False)
            session.execution_result = json.dumps(result, ensure_ascii=False)

            if result.get("success"):
                session.status = 'completed'
                summary = result.get("summary", {})
                msg = f"✅ 测试完成！通过 {summary.get('passed', 0)}/{summary.get('total', 0)} 条"
                if result.get("stopped"):
                    msg += "（已手动停止）"
                try:
                    report_info = await OneClickService._save_reports(
                        db, session, cases, result
                    )
                    if report_info.get("report_id"):
                        msg += f"\n📄 测试报告已生成 (ID: {report_info['report_id']})"
                    if report_info.get("bug_count", 0) > 0:
                        msg += f"\n🐛 已生成 Bug 报告 ({report_info['bug_count']} 项)"
                except Exception as rpt_err:
                    logger.warning(f"[OneClick] 生成报告失败: {rpt_err}")
            else:
                session.status = 'failed'
                msg = f"❌ 执行失败: {result.get('message', '未知错误')}"

            SessionManager.add_message(db, session, 'assistant', msg)
            db.commit()

        except Exception as e:
            logger.error(f"[OneClick] 树执行失败: {e}\n{traceback.format_exc()}")
            try:
                session = SessionManager.get_session(db, session_id)
                if session and session.status not in ('completed', 'failed'):
                    session.status = 'failed'
                    SessionManager.add_message(db, session, 'assistant', f'❌ 执行异常: {str(e)}')
                    db.commit()
            except Exception:
                pass
        finally:
            _running_sessions.pop(session_id, None)
            db.close()

    @staticmethod
    async def _execute_tests_by_tree(
        db: Session, session: OneclickSession, tree: TaskTree
    ) -> Dict:
        """
        树驱动执行引擎

        遍历 L2 → L3，在每条 L3 用例执行前/后更新节点状态
        """
        results = []
        passed = 0
        failed = 0
        stopped = False
        rate_limited = False
        start_time = time.time()
        session_id = session.id

        env_info = json.loads(session.login_info) if session.login_info else {}
        target_url = session.target_url or env_info.get("base_url", "")

        cancel_event = asyncio.Event()
        loop_detector = LoopDetector(LoopDetectionConfig(
            enabled=True, warning_threshold=3, critical_threshold=5, global_circuit_breaker=8
        ))
        _running_sessions[session_id] = {
            "cancel_event": cancel_event,
            "browser_session": None,
            "loop_detector": loop_detector,
        }

        switcher = get_auto_switcher()
        try:
            switcher.load_profiles_from_db()
        except Exception:
            pass

        # 只取已确认的 L3 节点，按 L2 分组
        all_l2 = tree.get_all_l2()
        confirmed_l3_all = tree.get_confirmed_l3()
        total = len(confirmed_l3_all)

        shared_browser = None
        try:
            shared_browser = await OneClickService._create_shared_browser(env_info)
            _running_sessions[session_id]["browser_session"] = shared_browser
            logger.info(f"[OneClick Tree] ✅ 共享浏览器已创建，执行 {total} 条用例")
        except Exception as e:
            logger.error(f"[OneClick Tree] ❌ 创建共享浏览器失败: {e}")
            return {"success": False, "message": f"浏览器启动失败: {str(e)}"}

        global_idx = 0

        try:
            for l2 in all_l2:
                if l2.status == NodeStatus.SKIPPED:
                    continue

                # 更新 L2 状态为执行中
                l2.status = NodeStatus.RUNNING

                confirmed_l3 = [n for n in l2.children if n.status == NodeStatus.CONFIRMED]
                if not confirmed_l3:
                    l2.status = NodeStatus.SKIPPED
                    continue

                for l3 in confirmed_l3:
                    if cancel_event.is_set():
                        stopped = True
                        l3.status = NodeStatus.FAILED
                        remaining = total - global_idx
                        SessionManager.add_message(
                            db, session, 'assistant', f'⏹️ 已停止，跳过剩余 {remaining} 条用例'
                        )
                        break

                    case = l3.test_case or {}
                    case_title = case.get("title", l3.name)
                    global_idx += 1

                    l3.status = NodeStatus.RUNNING
                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'⏳ [{global_idx}/{total}] [{l2.name}] 正在执行: {case_title}',
                        extra={"type": "executing", "index": global_idx - 1, "l2_id": l2.id, "l3_id": l3.id}
                    )

                    try:
                        need_browser = case.get("need_browser", True)
                        if need_browser:
                            loop_detector.reset()
                            if shared_browser and global_idx > 1:
                                try:
                                    await OneClickService._reset_browser_state(shared_browser, target_url)
                                except Exception:
                                    pass

                            result = await OneClickService._execute_browser_test(
                                case, target_url, env_info, db,
                                browser_session=shared_browser,
                                cancel_event=cancel_event,
                                loop_detector=loop_detector,
                                session_id=session_id,
                            )
                        else:
                            result = {"status": "skip", "message": "非浏览器测试，跳过"}

                        status = result.get("status", "error")

                        if status == "rate_limited":
                            rate_limited = True
                            failed += 1
                            l3.status = NodeStatus.FAILED
                            l3.result = result
                            results.append({
                                "index": global_idx, "title": case_title,
                                "l2_name": l2.name, "l3_id": l3.id,
                                "status": "rate_limited",
                                "message": result.get("message", "API 配额耗尽"),
                                "duration": result.get("duration", 0),
                            })
                            SessionManager.add_message(
                                db, session, 'assistant',
                                f'🚫 [{global_idx}/{total}] {case_title}: API 配额耗尽，停止执行'
                            )
                            stopped = True
                            break

                        if status == "pass":
                            passed += 1
                            l3.status = NodeStatus.DONE
                            emoji = "✅"
                        else:
                            failed += 1
                            l3.status = NodeStatus.FAILED
                            emoji = "❌" if status == "fail" else "⚠️"

                        l3.result = {
                            "status": status,
                            "message": (result.get("message", "") or "")[:500],
                            "duration": result.get("duration", 0),
                            "steps": result.get("steps", 0),
                        }
                        results.append({
                            "index": global_idx, "title": case_title,
                            "l2_name": l2.name, "l3_id": l3.id,
                            "status": status,
                            "message": result.get("message", ""),
                            "duration": result.get("duration", 0),
                            "steps": result.get("steps", 0),
                        })
                        SessionManager.add_message(
                            db, session, 'assistant',
                            f'{emoji} [{global_idx}/{total}] [{l2.name}] {case_title}: {status}',
                            extra={"type": "case_result", "index": global_idx - 1,
                                   "status": status, "l2_id": l2.id, "l3_id": l3.id}
                        )

                    except Exception as e:
                        failed += 1
                        l3.status = NodeStatus.FAILED
                        error_msg = str(e)
                        l3.result = {"status": "error", "message": error_msg[:500]}
                        if _is_rate_limit_error(error_msg):
                            rate_limited = True
                            stopped = True
                            results.append({"index": global_idx, "title": case_title,
                                            "l2_name": l2.name, "status": "rate_limited",
                                            "message": error_msg})
                            break
                        results.append({"index": global_idx, "title": case_title,
                                        "l2_name": l2.name, "status": "error",
                                        "message": error_msg})
                        SessionManager.add_message(
                            db, session, 'assistant',
                            f'❌ [{global_idx}/{total}] [{l2.name}] {case_title}: 执行异常 - {error_msg}'
                        )

                if stopped:
                    break

                # 更新 L2 完成状态
                l2_done = all(n.status in (NodeStatus.DONE, NodeStatus.SKIPPED) for n in l2.children)
                l2_failed = any(n.status == NodeStatus.FAILED for n in l2.children)
                l2.status = NodeStatus.FAILED if l2_failed else (NodeStatus.DONE if l2_done else NodeStatus.RUNNING)

        finally:
            if shared_browser:
                try:
                    await shared_browser.kill()
                except Exception:
                    pass
            _running_sessions.pop(session_id, None)

        return {
            "success": True,
            "stopped": stopped,
            "rate_limited": rate_limited,
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "executed": len(results),
                "duration": int(time.time() - start_time),
            },
            "results": results,
        }

    # ========== Phase 2: 用户确认 & 执行测试 ==========

    @staticmethod
    async def confirm_and_execute(
        db: Session, session_id: int, confirmed_cases: List[Dict] = None
    ) -> Dict:
        """
        用户确认测试用例后执行（异步模式）

        支持两种模式：
        - 任务树模式（task_tree_ready）：confirmed_cases 中包含 _tree_node_id 字段
        - 传统模式（cases_generated）：flat list of cases
        """
        session = SessionManager.get_session(db, session_id)
        if not session:
            return {"success": False, "message": "会话不存在"}

        if session.status not in ('cases_generated', 'confirmed', 'task_tree_ready'):
            return {"success": False, "message": f"当前状态 '{session.status}' 不允许执行"}

        try:
            # 使用用户确认的用例，或使用已生成的
            cases = confirmed_cases
            if not cases:
                cases = json.loads(session.generated_cases) if session.generated_cases else []

            if not cases:
                return {"success": False, "message": "没有可执行的测试用例"}

            session.confirmed_cases = json.dumps(cases, ensure_ascii=False)
            session.status = 'confirmed'
            db.commit()

            SessionManager.add_message(db, session, 'user', f'确认执行 {len(cases)} 条测试用例')
            SessionManager.update_status(db, session, 'executing')
            SessionManager.add_message(db, session, 'assistant', '🚀 开始执行测试...')

            # 启动后台异步任务执行测试，立即返回
            asyncio.create_task(
                OneClickService._background_execute(session_id, cases)
            )

            return {
                "success": True,
                "session_id": session_id,
                "status": "executing",
                "data": {
                    "messages": SessionManager.get_messages(session),
                }
            }

        except Exception as e:
            logger.error(f"[OneClick] 执行启动失败: {e}\n{traceback.format_exc()}")
            session.status = 'failed'
            SessionManager.add_message(db, session, 'assistant', f'❌ 执行异常: {str(e)}')
            db.commit()
            return {"success": False, "session_id": session_id, "message": str(e)}

    @staticmethod
    async def _background_execute(session_id: int, cases: List[Dict]):
        """
        后台异步执行测试用例

        独立数据库会话，不阻塞前端请求。
        前端通过轮询 /oneclick/session/{id} 获取实时进度。
        """
        from database.connection import SessionLocal
        db = SessionLocal()

        try:
            session = SessionManager.get_session(db, session_id)
            if not session:
                logger.error(f"[OneClick] 后台执行：会话 {session_id} 不存在")
                return

            # 执行测试
            result = await OneClickService._execute_tests(db, session, cases)

            # 重新获取 session（防止过期）
            session = SessionManager.get_session(db, session_id)
            if not session:
                return

            # 更新会话
            session.execution_result = json.dumps(result, ensure_ascii=False)
            if result.get("success"):
                session.status = 'completed'
                summary = result.get("summary", {})
                msg = f"✅ 测试完成！通过 {summary.get('passed', 0)}/{summary.get('total', 0)} 条"
                if result.get("stopped"):
                    msg += "（已手动停止）"
                if result.get("rate_limited"):
                    msg += "（因 API 配额耗尽提前终止）"

                # 自动生成测试报告和 Bug 报告
                try:
                    report_info = await OneClickService._save_reports(
                        db, session, cases, result
                    )
                    if report_info.get("report_id"):
                        msg += f"\n📄 测试报告已生成 (ID: {report_info['report_id']})"
                    if report_info.get("bug_count", 0) > 0:
                        msg += f"\n🐛 已生成 Bug 报告（包含 {report_info['bug_count']} 个Bug）"
                    # 邮件发送结果
                    email_info = report_info.get("email", {})
                    if email_info.get("success"):
                        msg += f"\n📧 {email_info.get('message', '邮件已发送')}"
                    elif email_info.get("message") and email_info["message"] not in ("未发送", "没有自动接收联系人", "未配置邮件服务"):
                        msg += f"\n📧 邮件发送失败: {email_info['message']}"
                except Exception as report_err:
                    logger.warning(f"[OneClick] 生成报告失败: {report_err}")
            else:
                session.status = 'failed'
                msg = f"❌ 执行失败: {result.get('message', '未知错误')}"

            SessionManager.add_message(db, session, 'assistant', msg)
            db.commit()

            logger.info(f"[OneClick] ✅ 后台执行完成: session_id={session_id}")

        except Exception as e:
            logger.error(f"[OneClick] 后台执行失败: {e}\n{traceback.format_exc()}")
            try:
                session = SessionManager.get_session(db, session_id)
                if session and session.status not in ('completed', 'failed'):
                    session.status = 'failed'
                    SessionManager.add_message(db, session, 'assistant',
                        f'❌ 执行异常: {str(e)}')
                    db.commit()
            except Exception:
                pass
        finally:
            # 清理运行状态
            _running_sessions.pop(session_id, None)
            db.close()

    # ========== 内部方法 ==========

    @staticmethod
    async def _analyze_intent(user_input: str, db: Session) -> Dict:
        """LLM 分析用户意图（旧版，保留兼容）"""
        llm = get_llm_client()

        # 获取数据库中已有的模块列表
        modules = db.query(ExecutionCase.module).distinct().all()
        module_list = [m[0] for m in modules if m[0]]

        system_prompt = ONECLICK_INTENT_ANALYSIS_SYSTEM

        user_prompt = ONECLICK_INTENT_ANALYSIS_USER_TEMPLATE.format(
            user_input=user_input,
            module_list=', '.join(module_list) if module_list else '暂无',
        )

        try:
            response = await llm.achat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            return llm.parse_json_response(response)
        except Exception as e:
            logger.warning(f"[OneClick] 意图分析失败: {e}")
            return {
                "target_module": user_input,
                "test_scope": user_input,
                "keywords": user_input.split(),
                "need_login": True,
                "test_type": "功能测试"
            }

    @staticmethod
    async def _analyze_intent_v2(user_input: str, db: Session) -> Dict:
        """LLM 分析用户意图（增强版：识别用户提供的 URL/凭据）"""
        llm = get_llm_client()

        # 获取数据库中已有的模块列表
        modules = db.query(ExecutionCase.module).distinct().all()
        module_list = [m[0] for m in modules if m[0]]

        # 获取数据库中已配置的测试环境
        envs = db.query(TestEnvironment).filter(TestEnvironment.is_active == 1).all()
        env_list_parts = []
        for env in envs:
            default_tag = " [默认]" if env.is_default else ""
            env_list_parts.append(
                f"- {env.name}{default_tag}: {env.base_url}"
                f" ({env.description or '无描述'})"
            )
        env_list = "\n".join(env_list_parts) if env_list_parts else "暂无配置"

        user_prompt = ONECLICK_INTENT_ANALYSIS_V2_USER_TEMPLATE.format(
            user_input=user_input,
            module_list=', '.join(module_list) if module_list else '暂无',
            env_list=env_list,
        )

        try:
            response = await llm.achat(
                messages=[
                    {"role": "system", "content": ONECLICK_INTENT_ANALYSIS_V2_SYSTEM},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            result = llm.parse_json_response(response)
            # 确保必要字段存在
            result.setdefault("target_module", user_input)
            result.setdefault("test_scope", user_input)
            result.setdefault("scope_type", "single_page")
            result.setdefault("required_modules", [])
            result.setdefault("keywords", user_input.split())
            result.setdefault("need_login", True)
            result.setdefault("test_type", "功能测试")
            return result
        except Exception as e:
            logger.warning(f"[OneClick] 意图分析V2失败: {e}")
            return {
                "target_module": user_input,
                "test_scope": user_input,
                "scope_type": "single_page",
                "required_modules": [],
                "keywords": user_input.split(),
                "need_login": True,
                "test_type": "功能测试",
                "user_provided_url": None,
                "user_provided_username": None,
                "user_provided_password": None,
                "navigation_hints": [],
            }

    @staticmethod
    def _resolve_environment(intent: Dict, db: Session) -> Dict:
        """
        解析测试环境配置

        优先级：
        1. 用户在指令中明确提供的 URL/凭据
        2. 按 preferred_env_name 模糊匹配数据库环境（如"教师端"匹配"教师端开发环境"）
        3. 数据库中的默认测试环境
        4. 数据库中第一个激活的测试环境
        5. 环境变量兜底
        """
        user_url = intent.get("user_provided_url")
        user_username = intent.get("user_provided_username")
        user_password = intent.get("user_provided_password")

        # 1. 用户明确提供了 URL
        if user_url:
            return {
                "base_url": user_url,
                "login_url": user_url,
                "username": user_username or "",
                "password": user_password or "",
                "headless": os.getenv("HEADLESS", "false").lower() == "true",
                "_source": "用户输入",
            }

        # 获取所有激活的环境
        all_envs = db.query(TestEnvironment).filter(
            TestEnvironment.is_active == 1,
        ).all()

        matched_env = None

        # 2. 按 preferred_env_name 模糊匹配
        preferred_name = intent.get("preferred_env_name")
        if preferred_name and all_envs:
            preferred_lower = preferred_name.lower()
            # 先精确包含匹配
            for env in all_envs:
                env_name_lower = (env.name or "").lower()
                env_desc_lower = (env.description or "").lower()
                if preferred_lower in env_name_lower or preferred_lower in env_desc_lower:
                    matched_env = env
                    break
            # 再尝试关键词部分匹配（如"教师"匹配"教师端开发环境"）
            if not matched_env:
                keywords = [preferred_lower[i:i+2] for i in range(len(preferred_lower)-1)]
                for env in all_envs:
                    env_name_lower = (env.name or "").lower()
                    if any(kw in env_name_lower for kw in keywords):
                        matched_env = env
                        break

        # 3. 默认环境
        if not matched_env:
            matched_env = next(
                (e for e in all_envs if e.is_default == 1), None
            )

        # 4. 第一个激活的环境
        if not matched_env and all_envs:
            matched_env = all_envs[0]

        if matched_env:
            return {
                "base_url": matched_env.base_url,
                "login_url": matched_env.login_url or matched_env.base_url,
                "username": user_username or matched_env.username or "",
                "password": user_password or matched_env.password or "",
                "headless": os.getenv("HEADLESS", "false").lower() == "true",
                "_source": f"数据库({matched_env.name})",
                "_env_id": matched_env.id,
            }

        # 5. 环境变量兜底
        base_url = os.getenv("API_BASE_URL", "")
        if base_url:
            return {
                "base_url": base_url,
                "login_url": base_url,
                "username": user_username or "",
                "password": user_password or "",
                "headless": os.getenv("HEADLESS", "false").lower() == "true",
                "_source": "环境变量",
            }

        return {"base_url": "", "_source": "未配置"}

    @staticmethod
    def _resolve_environment(intent: Dict, db: Session) -> Dict:
        """
        Standardized environment resolver for test environment resolution.
        """
        user_url = intent.get("user_provided_url")
        user_username = intent.get("user_provided_username")
        user_password = intent.get("user_provided_password")

        if user_url:
            return {
                "base_url": user_url,
                "target_url": user_url,
                "login_url": user_url,
                "username": user_username or "",
                "password": user_password or "",
                "extra_credentials": {},
                "headless": os.getenv("HEADLESS", "false").lower() == "true",
                "_source": "user_input",
                "env_name": "user_input",
            }

        all_envs = db.query(TestEnvironment).filter(
            TestEnvironment.is_active == 1,
        ).all()

        matched_env = None
        preferred_name = intent.get("preferred_env_name")
        if preferred_name and all_envs:
            preferred_lower = preferred_name.lower()
            for env in all_envs:
                env_name_lower = (env.name or "").lower()
                env_desc_lower = (env.description or "").lower()
                if preferred_lower in env_name_lower or preferred_lower in env_desc_lower:
                    matched_env = env
                    break
            if not matched_env and len(preferred_lower) > 1:
                keywords = [preferred_lower[i:i + 2] for i in range(len(preferred_lower) - 1)]
                for env in all_envs:
                    env_name_lower = (env.name or "").lower()
                    if any(keyword in env_name_lower for keyword in keywords):
                        matched_env = env
                        break

        if not matched_env:
            matched_env = next((env for env in all_envs if env.is_default == 1), None)
        if not matched_env and all_envs:
            matched_env = all_envs[0]

        if matched_env:
            extra_credentials = (
                dict(matched_env.extra_credentials)
                if isinstance(matched_env.extra_credentials, dict)
                else {}
            )
            return {
                "base_url": matched_env.base_url,
                "target_url": matched_env.base_url,
                "login_url": matched_env.login_url or matched_env.base_url,
                "username": user_username or matched_env.username or "",
                "password": user_password or matched_env.password or "",
                "extra_credentials": extra_credentials,
                "headless": os.getenv("HEADLESS", "false").lower() == "true",
                "_source": f"test_env:{matched_env.name}",
                "_env_id": matched_env.id,
                "env_name": matched_env.name,
            }

        base_url = os.getenv("API_BASE_URL", "")
        if base_url:
            return {
                "base_url": base_url,
                "target_url": base_url,
                "login_url": base_url,
                "username": user_username or "",
                "password": user_password or "",
                "extra_credentials": {},
                "headless": os.getenv("HEADLESS", "false").lower() == "true",
                "_source": "env_var",
                "env_name": "env_var",
            }

        return {
            "base_url": "",
            "target_url": "",
            "login_url": "",
            "username": "",
            "password": "",
            "extra_credentials": {},
            "headless": os.getenv("HEADLESS", "false").lower() == "true",
            "_source": "unknown",
            "env_name": "unknown",
        }

    @staticmethod
    async def _parse_user_scope(user_input: str, db: Session) -> Dict:
        """
        解析用户的测试范围
        
        返回：
        {
            "scope_type": "specific/all",  # 具体范围 or 所有功能
            "requested_functions": ["课程选择", "章节学习"],  # 用户指定的功能
            "scope_description": "测试课程选择和章节学习功能"  # 范围描述
        }
        """
        llm = get_llm_client()
        
        prompt = f"""
用户输入: {user_input}

请分析用户的测试范围需求：

1. 范围类型：
   - specific: 用户明确指定了具体的功能（如："测试课程选择和章节学习"）
   - all: 用户要求测试所有功能（如："测试所有功能"、"全面测试"）

2. 具体功能列表：
   - 如果是 specific，列出用户明确提到的功能
   - 如果是 all，返回空列表

3. 范围描述：
   - 用一句话描述用户的测试范围

返回 JSON 格式：
{{
    "scope_type": "specific/all",
    "requested_functions": ["功能1", "功能2"],
    "scope_description": "范围描述"
}}

示例：
- 输入："测试课程选择、章节学习、自主练习"
  输出：{{"scope_type": "specific", "requested_functions": ["课程选择", "章节学习", "自主练习"], "scope_description": "测试课程选择、章节学习、自主练习这3个功能"}}

- 输入："测试所有功能"
  输出：{{"scope_type": "all", "requested_functions": [], "scope_description": "测试系统的所有功能"}}

- 输入："帮我测试学生端"
  输出：{{"scope_type": "all", "requested_functions": [], "scope_description": "测试学生端的所有功能"}}
"""
        
        try:
            response = await llm.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            scope = llm.parse_json_response(response)
            
            # 验证和补充
            scope.setdefault("scope_type", "all")
            scope.setdefault("requested_functions", [])
            scope.setdefault("scope_description", user_input)
            
            return scope
        except Exception as e:
            logger.warning(f"[OneClick] 范围解析失败: {e}")
            return {
                "scope_type": "all",
                "requested_functions": [],
                "scope_description": user_input
            }

    @staticmethod
    async def _generate_subtasks(user_input: str, page_data: Dict) -> Dict:
        """基于页面能力数据，LLM 生成测试子任务"""
        llm = get_llm_client()

        page_exploration_text = json.dumps(page_data, ensure_ascii=False, indent=2)
        # 截断过长的探索结果
        if len(page_exploration_text) > 8000:
            page_exploration_text = page_exploration_text[:8000] + "\n... (已截断)"

        user_prompt = ONECLICK_SUBTASK_GENERATION_USER_TEMPLATE.format(
            user_input=user_input,
            page_exploration=page_exploration_text,
        )

        try:
            response = await llm.achat(
                messages=[
                    {"role": "system", "content": ONECLICK_SUBTASK_GENERATION_SYSTEM},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.4,
                max_tokens=6000,
                response_format={"type": "json_object"}
            )
            result = llm.parse_json_response(response)
            result.setdefault("subtasks", [])
            result.setdefault("total_estimated_cases", 0)
            return result
        except Exception as e:
            logger.warning(f"[OneClick] 子任务生成失败: {e}")
            return {"subtasks": [], "total_estimated_cases": 0, "test_strategy": ""}

    # ─────────────────────────────────────────────────────
    # 任务树引擎：三层规划方法
    # ─────────────────────────────────────────────────────

    @staticmethod
    async def _abstract_page_capabilities(
        user_input: str, intent: Dict, page_data: Dict
    ) -> Dict:
        """
        页面能力抽象层（Page Capability Abstraction）
        
        把页面数据提炼为「语义化页面能力描述」：
        forms / buttons / tables / auth_required / security_surface 等
        """
        llm = get_llm_client()

        page_exploration_text = json.dumps(page_data, ensure_ascii=False, indent=2)
        if len(page_exploration_text) > 6000:
            page_exploration_text = page_exploration_text[:6000] + "\n... (已截断)"

        target_module = intent.get("target_module", intent.get("test_scope", user_input))
        user_prompt = PAGE_CAPABILITY_ABSTRACTION_USER_TEMPLATE.format(
            target_module=target_module,
            page_exploration=page_exploration_text,
        )

        try:
            response = await llm.achat(
                messages=[
                    {"role": "system", "content": PAGE_CAPABILITY_ABSTRACTION_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=3000,
                response_format={"type": "json_object"},
            )
            result = llm.parse_json_response(response)
            result.setdefault("summary", "页面能力抽象完成")
            result.setdefault("page_type", "mixed")
            return result
        except Exception as e:
            logger.warning(f"[OneClick] 页面能力抽象失败: {e}")
            # 降级：返回基本信息
            return {
                "forms": [],
                "buttons": [],
                "tables": [],
                "auth_required": True,
                "page_type": "mixed",
                "summary": "页面能力抽象降级",
                "raw_fallback": True,
            }

    @staticmethod
    async def _plan_feature_tasks(user_input: str, page_capabilities: Dict, rag_context: str = "") -> Dict:
        """
        L2 功能规划层（Feature Planning Layer）

        根据页面能力摘要，规划出测试维度（L2 节点）：
        - 正常流程测试
        - 异常场景测试
        - 边界值测试
        - 安全测试
        - 权限测试
        等

        rag_context: 从知识库检索到的 RAG 上下文（可选），用于补充页面信息
        """
        llm = get_llm_client()

        caps_text = json.dumps(page_capabilities, ensure_ascii=False, indent=2)
        if len(caps_text) > 5000:
            caps_text = caps_text[:5000] + "\n... (已截断)"

        # 注入 RAG 上下文到 caps_text 末尾
        if rag_context:
            caps_text += f"\n\n--- 知识库参考上下文 ---\n{rag_context}"

        user_prompt = TASK_TREE_FEATURE_PLANNING_USER_TEMPLATE.format(
            user_input=user_input,
            page_capabilities=caps_text,
        )

        try:
            response = await llm.achat(
                messages=[
                    {"role": "system", "content": TASK_TREE_FEATURE_PLANNING_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                max_tokens=4000,
                response_format={"type": "json_object"},
            )
            result = llm.parse_json_response(response)
            result.setdefault("l2_nodes", [])
            result.setdefault("l1_name", user_input)
            result.setdefault("total_estimated_cases", 0)
            return result
        except Exception as e:
            logger.warning(f"[OneClick] L2 功能规划失败: {e}")
            return {
                "l1_name": user_input,
                "l1_description": "",
                "l2_nodes": [],
                "total_estimated_cases": 0,
            }

    @staticmethod
    async def _plan_atomic_tasks_for_l2(
        user_input: str, l2_node: Dict, page_capabilities: Dict,
        env_info: Dict = None
    ) -> List[Dict]:
        """
        L3 原子任务规划（Atomic Task Planning）

        为单个 L2 节点规划 L3 原子测试用例
        """
        llm = get_llm_client()

        caps_text = json.dumps(page_capabilities, ensure_ascii=False, indent=2)
        if len(caps_text) > 3000:
            caps_text = caps_text[:3000] + "\n... (已截断)"

        # 构造测试环境描述，传给 LLM 用于填充 test_data
        env = env_info or {}
        test_env_text = (
            f"目标URL: {env.get('base_url') or env.get('login_url', '未配置')}\n"
            f"登录URL: {env.get('login_url') or env.get('base_url', '未配置')}\n"
            f"账号(username): {env.get('username', '未配置')}\n"
            f"密码(password): {env.get('password', '未配置')}\n"
            f"额外凭据(extra_credentials): {json.dumps(env.get('extra_credentials', {}), ensure_ascii=False)}\n"
            f"环境名称: {env.get('env_name', env.get('_source', '默认环境'))}"
        )

        estimated_count = l2_node.get("estimated_l3_count", 4)
        user_prompt = TASK_TREE_ATOMIC_PLANNING_USER_TEMPLATE.format(
            user_input=user_input,
            l2_name=l2_node.get("name", ""),
            l2_description=l2_node.get("description", ""),
            feature_type=l2_node.get("feature_type", "form"),
            test_focus=json.dumps(l2_node.get("test_focus", []), ensure_ascii=False),
            page_capabilities=caps_text,
            test_env=test_env_text,
            estimated_count=estimated_count,
        )

        try:
            response = await llm.achat(
                messages=[
                    {"role": "system", "content": TASK_TREE_ATOMIC_PLANNING_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.5,
                max_tokens=4000,
                response_format={"type": "json_object"},
            )
            result = llm.parse_json_response(response)
            return result.get("l3_nodes", [])
        except Exception as e:
            logger.warning(f"[OneClick] L3 原子规划失败 ({l2_node.get('name', '')}): {e}")
            return []

    @staticmethod
    async def _build_task_tree(
        user_input: str, l1_name: str, feature_plan: Dict, page_capabilities: Dict,
        env_info: Dict = None
    ) -> TaskTree:
        """
        构建完整三层任务树

        为每个 L2 节点并发（或串行）规划 L3 原子任务
        """
        l2_nodes = feature_plan.get("l2_nodes", [])
        l1_desc = feature_plan.get("l1_description", "")

        # 构建 LLM 输出格式（传给 TaskTree.build_from_llm_output）
        tree_json = {
            "name": l1_name,
            "description": l1_desc,
            "children": [],
        }

        for l2_data in l2_nodes:
            l3_nodes_raw = await OneClickService._plan_atomic_tasks_for_l2(
                user_input, l2_data, page_capabilities, env_info=env_info
            )
            l2_entry = {
                "name": l2_data.get("name", ""),
                "description": l2_data.get("description", ""),
                "feature_type": l2_data.get("feature_type", ""),
                "priority": str(l2_data.get("priority", "3")),
                "children": l3_nodes_raw,
            }
            tree_json["children"].append(l2_entry)
            logger.info(
                f"[OneClick] 🌲 L2 规划完成: {l2_data.get('name', '')} → {len(l3_nodes_raw)} 条用例"
            )

        tree = TaskTree.build_from_llm_output(tree_json)
        logger.info(
            f"[OneClick] 🌳 任务树构建完成: {len(tree.get_all_l2())} 个 L2, {len(tree.get_all_l3())} 个 L3"
        )
        return tree

    @staticmethod
    def _query_related_cases(db: Session, intent: Dict) -> List[Dict]:
        """从数据库查询相关用例"""
        keywords = intent.get("keywords", [])
        module = intent.get("target_module", "")

        query = db.query(ExecutionCase)

        # 按模块匹配
        if module:
            query = query.filter(
                ExecutionCase.module.like(f"%{module}%") |
                ExecutionCase.title.like(f"%{module}%") |
                ExecutionCase.keywords.like(f"%{module}%")
            )

        cases = query.limit(50).all()

        # 如果模块匹配没结果，用关键词
        if not cases and keywords:
            for kw in keywords:
                kw_cases = db.query(ExecutionCase).filter(
                    ExecutionCase.title.like(f"%{kw}%") |
                    ExecutionCase.keywords.like(f"%{kw}%") |
                    ExecutionCase.module.like(f"%{kw}%")
                ).limit(20).all()
                cases.extend(kw_cases)
            # 去重
            seen = set()
            unique = []
            for c in cases:
                if c.id not in seen:
                    seen.add(c.id)
                    unique.append(c)
            cases = unique[:50]

        return [
            {
                "id": c.id,
                "title": c.title,
                "module": c.module,
                "steps": c.steps,
                "expected": c.expected,
                "priority": c.priority,
                "test_data": c.test_data,
            }
            for c in cases
        ]

    @staticmethod
    def _get_env_info() -> Dict:
        """获取测试环境信息"""
        return {
            "base_url": os.getenv("API_BASE_URL", ""),
            "token": os.getenv("API_TOKEN", ""),
            "headless": os.getenv("HEADLESS", "false").lower() == "true",
        }

    @staticmethod
    async def _generate_test_cases(
        db: Session,
        session: OneclickSession,
        user_input: str,
        intent: Dict,
        existing_cases: List[Dict],
        env_info: Dict,
        skill_ids: List[int] = None,
        page_data: Dict = None,
        subtasks: Dict = None,
    ) -> Dict:
        """LLM 生成测试用例（最小上下文：L1任务+环境+页面探索）"""
        llm = get_llm_client()

        # 严格最小上下文：仅 L1 任务 + 测试环境 + 页面探索
        l1_task = (
            intent.get("test_scope")
            or intent.get("target_module")
            or intent.get("task")
            or user_input
        )

        test_env = {
            "base_url": env_info.get("base_url", ""),
            "username": env_info.get("username", ""),
            "password": env_info.get("password", ""),
        }
        test_env_text = json.dumps(test_env, ensure_ascii=False, indent=2)

        # ━━ 检查探索结果中的模块数量 ━━
        explored_modules = page_data.get("explored_modules", []) if page_data else []
        explored_functions = page_data.get("explored_functions", []) if page_data else []
        module_count = len(explored_modules) or len(explored_functions)
        
        if module_count > 1:
            logger.info(f"[OneClick] 检测到多模块测试：共 {module_count} 个模块")
            SessionManager.add_message(
                db, session, 'assistant',
                f'📋 检测到 {module_count} 个功能模块，将为每个模块生成测试用例'
            )
        else:
            logger.info(f"[OneClick] 单页面测试：{module_count} 个模块")

        page_exploration_text = json.dumps(page_data or {}, ensure_ascii=False, indent=2)
        if len(page_exploration_text) > 7000:
            page_exploration_text = page_exploration_text[:7000] + "\n... (已截断)"

        system_prompt = ONECLICK_GENERATE_CASES_V2_SYSTEM
        user_prompt = ONECLICK_GENERATE_CASES_V2_USER_TEMPLATE.format(
            l1_task=l1_task,
            test_env=test_env_text,
            page_exploration=page_exploration_text,
        )

        try:
            SessionManager.add_message(db, session, 'assistant', '正在生成测试用例...')

            response = await llm.achat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5,
                max_tokens=8000,
                response_format={"type": "json_object"}
            )

            # 使用 Provider 感知的 JSON 解析（带重试）
            result = None
            last_error = None
            for attempt in range(2):
                try:
                    result = llm.parse_json_response(response)
                    break
                except (json.JSONDecodeError, ValueError) as parse_err:
                    last_error = parse_err
                    if attempt == 0:
                        logger.warning(f"[OneClick] 用例 JSON 解析失败（第1次），重新请求 LLM: {parse_err}")
                        response = await llm.achat(
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt + "\n\n⚠️ 请确保输出是合法的 JSON 格式，不要在 JSON 外添加任何文字。"}
                            ],
                            temperature=0.3,
                            max_tokens=8000,
                            response_format={"type": "json_object"}
                        )

            if result is None:
                raise last_error

            cases = result.get("cases", [])
            summary = result.get("summary", "")

            # ━━ 验证生成的用例是否覆盖所有模块 ━━
            if module_count > 1:
                case_modules = set(case.get("module", "") for case in cases)
                logger.info(f"[OneClick] 生成的用例覆盖模块：{case_modules}")
                
                if len(case_modules) < module_count:
                    logger.warning(
                        f"[OneClick] ⚠️ 用例覆盖不足：探索了 {module_count} 个模块，"
                        f"但只生成了 {len(case_modules)} 个模块的用例"
                    )
                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'⚠️ 注意：探索了 {module_count} 个模块，但只生成了 {len(case_modules)} 个模块的用例'
                    )

            # 保存到会话
            session.generated_cases = json.dumps(cases, ensure_ascii=False)
            SessionManager.update_status(db, session, 'cases_generated')

            mode_tag = "🔍 基于页面探索"
            SessionManager.add_message(
                db, session, 'assistant',
                f'✅ 已生成 {len(cases)} 条测试用例（{mode_tag}）\n{summary}',
                extra={"type": "cases_generated", "count": len(cases)}
            )

            return {"cases": cases, "summary": summary}

        except Exception as e:
            logger.error(f"[OneClick] 生成用例失败: {e}\n{traceback.format_exc()}")
            SessionManager.add_message(db, session, 'assistant', f'⚠️ 用例生成失败: {str(e)}')
            # 即使失败也要更新状态，否则前端轮询永远不会停止
            session.generated_cases = json.dumps([], ensure_ascii=False)
            SessionManager.update_status(db, session, 'cases_generated')
            return {"cases": [], "summary": ""}

    @staticmethod
    async def _execute_tests(
        db: Session, session: OneclickSession, cases: List[Dict]
    ) -> Dict:
        """
        执行测试用例（使用 browser-use）

        关键改进：
        1. 所有用例共享一个 BrowserSession，不再每条用例都新建浏览器
        2. 通过 asyncio.Event 支持取消，stop_session() 可以真正停止执行
        3. 检测 429 限流错误，立即停止后续用例
        """
        results = []
        passed = 0
        failed = 0
        total = len(cases)
        start_time = time.time()
        stopped = False
        rate_limited = False

        env_info = json.loads(session.login_info) if session.login_info else {}
        target_url = session.target_url or env_info.get("base_url", "")

        # 注册取消事件
        cancel_event = asyncio.Event()
        session_id = session.id
        loop_detector = LoopDetector(LoopDetectionConfig(
            enabled=True,
            warning_threshold=3,
            critical_threshold=5,
            global_circuit_breaker=8,
        ))
        _running_sessions[session_id] = {
            "cancel_event": cancel_event,
            "browser_session": None,
            "loop_detector": loop_detector,
        }

        # 确保 auto_switcher 已加载
        switcher = get_auto_switcher()
        try:
            switcher.load_profiles_from_db()
        except Exception as e:
            logger.warning(f"[OneClick] 加载 auto_switcher 配置失败: {e}")

        # 创建共享的 BrowserSession
        shared_browser = None
        try:
            shared_browser = await OneClickService._create_shared_browser(env_info)
            _running_sessions[session_id]["browser_session"] = shared_browser
            logger.info(f"[OneClick] ✅ 共享浏览器已创建，开始执行 {total} 条用例")
        except Exception as e:
            logger.error(f"[OneClick] ❌ 创建共享浏览器失败: {e}")
            return {"success": False, "message": f"浏览器启动失败: {str(e)}"}

        try:
            for idx, case in enumerate(cases):
                # ===== 检查是否被取消 =====
                if cancel_event.is_set():
                    stopped = True
                    remaining = total - idx
                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'⏹️ 已停止，跳过剩余 {remaining} 条用例'
                    )
                    logger.info(f"[OneClick] ⏹️ 会话 {session_id} 已被取消，跳过剩余 {remaining} 条")
                    break

                case_title = case.get("title", f"用例{idx+1}")
                SessionManager.add_message(
                    db, session, 'assistant',
                    f'⏳ [{idx+1}/{total}] 正在执行: {case_title}',
                    extra={"type": "executing", "index": idx}
                )

                try:
                    need_browser = case.get("need_browser", True)

                    if need_browser:
                        # 重置循环检测器（每条用例独立检测）
                        loop_detector.reset()

                        # ===== 用例间状态隔离：确保从目标页面开始 =====
                        if shared_browser and idx > 0:
                            try:
                                await OneClickService._reset_browser_state(
                                    shared_browser, target_url
                                )
                            except Exception as reset_err:
                                logger.warning(f"[OneClick] ⚠️ 重置浏览器状态失败: {reset_err}")

                        # 使用共享浏览器执行
                        result = await OneClickService._execute_browser_test(
                            case, target_url, env_info, db,
                            browser_session=shared_browser,
                            cancel_event=cancel_event,
                            loop_detector=loop_detector,
                            session_id=session_id,
                        )
                    else:
                        result = {"status": "skip", "message": "非浏览器测试，跳过"}

                    status = result.get("status", "error")

                    # ===== 检测 429 限流 =====
                    if status == "rate_limited":
                        rate_limited = True
                        failed += 1
                        results.append({
                            "index": idx + 1,
                            "title": case_title,
                            "status": "rate_limited",
                            "message": result.get("message", "API 配额耗尽"),
                            "duration": result.get("duration", 0),
                            "steps": result.get("steps", 0),
                        })
                        remaining = total - idx - 1
                        SessionManager.add_message(
                            db, session, 'assistant',
                            f'🚫 [{idx+1}/{total}] {case_title}: API 配额耗尽 (429)，停止执行剩余 {remaining} 条用例',
                            extra={"type": "rate_limited"}
                        )
                        logger.warning(f"[OneClick] 🚫 429 限流，停止后续用例")
                        break

                    if status == "pass":
                        passed += 1
                        emoji = "✅"
                    elif status == "fail":
                        failed += 1
                        emoji = "❌"
                    else:
                        failed += 1
                        emoji = "⚠️"

                    results.append({
                        "index": idx + 1,
                        "title": case_title,
                        "status": status,
                        "message": result.get("message", ""),
                        "duration": result.get("duration", 0),
                        "steps": result.get("steps", 0),
                    })

                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'{emoji} [{idx+1}/{total}] {case_title}: {status}',
                        extra={"type": "case_result", "index": idx, "status": status}
                    )

                except Exception as e:
                    failed += 1
                    error_msg = str(e)

                    # 检查异常中是否包含 429
                    if _is_rate_limit_error(error_msg):
                        rate_limited = True
                        results.append({
                            "index": idx + 1,
                            "title": case_title,
                            "status": "rate_limited",
                            "message": error_msg,
                        })
                        SessionManager.add_message(
                            db, session, 'assistant',
                            f'🚫 [{idx+1}/{total}] {case_title}: API 配额耗尽，停止执行'
                        )
                        break

                    results.append({
                        "index": idx + 1,
                        "title": case_title,
                        "status": "error",
                        "message": error_msg,
                    })
                    SessionManager.add_message(
                        db, session, 'assistant',
                        f'❌ [{idx+1}/{total}] {case_title}: 执行异常 - {error_msg}'
                    )

            total_duration = int(time.time() - start_time)

            return {
                "success": True,
                "stopped": stopped,
                "rate_limited": rate_limited,
                "summary": {
                    "total": total,
                    "passed": passed,
                    "failed": failed,
                    "executed": len(results),
                    "duration": total_duration,
                },
                "results": results,
                "loop_stats": loop_detector.get_stats(),
            }

        finally:
            # ===== 关闭共享浏览器 =====
            if shared_browser:
                try:
                    logger.info(f"[OneClick] 正在关闭共享浏览器...")
                    # 使用 kill() 强制关闭，因为 keep_alive=True 时 stop() 不会真正关闭
                    await shared_browser.kill()
                    logger.info(f"[OneClick] ✅ 共享浏览器已关闭")
                except Exception as e:
                    logger.warning(f"[OneClick] ⚠️ 关闭浏览器异常: {e}")
            # 清理运行状态
            _running_sessions.pop(session_id, None)

    @staticmethod
    async def _create_shared_browser(env_info: Dict):
        """创建共享的 BrowserSession（所有用例复用）"""
        try:
            from browser_use import BrowserSession
        except ImportError:
            # 兼容旧版本
            from browser_use.browser import BrowserSession
        from Execute_test.service import find_chrome_path

        headless = env_info.get("headless", False)
        chrome_path = os.getenv('BROWSER_PATH', '').strip() or find_chrome_path()
        disable_security = os.getenv('DISABLE_SECURITY', 'false').lower() == 'true'

        browser_session = BrowserSession(
            headless=headless,
            disable_security=disable_security,
            executable_path=chrome_path if chrome_path else None,
            enable_default_extensions=os.getenv("BROWSER_USE_ENABLE_DEFAULT_EXTENSIONS", "false").lower() == "true",
            minimum_wait_page_load_time=0.5,
            wait_between_actions=0.3,
            keep_alive=True,
        )

        logger.info(f"[OneClick] 🚀 创建共享浏览器: headless={headless}, chrome={chrome_path or '自动'}")
        return browser_session

    @staticmethod
    async def _reset_browser_state(browser_session, target_url: str):
        """
        用例间状态隔离：清除 cookies/storage + 导航到目标页面

        解决问题：用例1登录成功后，用例2（如错误密码测试）会在已登录状态下开始，
        导致测试结果不准确。

        策略（适配 browser-use 0.11.1 CDP 架构）：
        1. 清除所有 cookies（通过 BrowserSession.clear_cookies）
        2. 清除 localStorage/sessionStorage（通过 CDP Runtime.evaluate）
        3. 导航到目标 URL，确保从干净状态开始
        """
        import asyncio

        try:
            # 1. 清除所有 cookies（使用 browser-use 内置的 CDP Storage.clearCookies）
            try:
                await browser_session._cdp_clear_cookies()
                logger.debug("[OneClick] 🧹 已清除浏览器 cookies（_cdp_clear_cookies）")
            except Exception as e:
                # 备用：通过 JS 覆盖清除 document.cookie
                try:
                    page = await browser_session.must_get_current_page()
                    await page.evaluate(
                        "document.cookie.split(';').forEach(c => { "
                        "document.cookie = c.trim().split('=')[0] + '=;expires=Thu, 01 Jan 1970 00:00:00 UTC;path=/'; });"
                    )
                    logger.debug("[OneClick] 🧹 已通过 JS 清除 cookies")
                except Exception as e2:
                    logger.warning(f"[OneClick] ⚠️ 清除 cookies 失败: {e} / {e2}")

            # 2. 清除 localStorage 和 sessionStorage
            try:
                page = await browser_session.must_get_current_page()
                await page.evaluate("try { localStorage.clear(); sessionStorage.clear(); } catch(e) {}")
                logger.debug("[OneClick] 🧹 已清除 localStorage/sessionStorage")
            except Exception as e:
                logger.debug(f"[OneClick] 清除 storage 失败（非致命）: {e}")

            # 3. 导航到目标 URL
            try:
                from browser_use.browser.events import NavigateToUrlEvent
                await browser_session.event_bus.dispatch(
                    NavigateToUrlEvent(url=target_url, new_tab=False)
                )
                await asyncio.sleep(0.5)
                logger.debug(f"[OneClick] 🔄 已导航到目标页面: {target_url}")
            except Exception as e:
                logger.warning(f"[OneClick] ⚠️ 导航到目标页面失败: {e}")
                # 备用方案：通过 JS 直接导航
                try:
                    page = await browser_session.must_get_current_page()
                    await page.evaluate(f"window.location.href = '{target_url}'")
                    await asyncio.sleep(0.8)
                    logger.debug(f"[OneClick] 🔄 已通过 JS 导航到目标页面: {target_url}")
                except Exception as e2:
                    logger.warning(f"[OneClick] ⚠️ JS 导航也失败: {e2}")

        except Exception as e:
            logger.warning(f"[OneClick] ⚠️ 重置浏览器状态异常: {e}")
            # 不抛出异常，允许测试继续

    @staticmethod
    async def _execute_browser_test(
        case: Dict, target_url: str, env_info: Dict, db: Session,
        browser_session=None,
        cancel_event: asyncio.Event = None,
        loop_detector: LoopDetector = None,
        session_id: int = None,
    ) -> Dict:
        """
        使用 browser-use 执行单条浏览器测试

        特性：
        - 接受外部传入的 browser_session（共享浏览器）
        - 接受 cancel_event 用于中途取消
        - 检测 429 限流错误并返回特殊状态
        - 集成循环检测，防止 Agent 陷入无限循环
        - 集成自动切换，模型失败时自动切换
        - 集成 DOM 模式：每次执行前检测页面 DOM 丰富度，丰富则切换 DOM 模式
        """
        start = time.time()

        # ── DOM 模式检测 ──────────────────────────────────────────────
        # 如果有共享浏览器会话，先检测当前页面 DOM 丰富度
        if browser_session is not None:
            try:
                from Execute_test.dom_mode.agent_browser_client import AgentBrowserClient
                from Execute_test.dom_mode.dom_executor import DomExecutor
                logger.info("[OneClick] DOM 优先执行已启用，先尝试 DOM 执行器")
                cdp_port = None
                raw_cdp = None
                for attr in ("cdp_url", "_cdp_url", "ws_url"):
                    val = getattr(browser_session, attr, None)
                    if val:
                        raw_cdp = val
                        break

                if raw_cdp:
                    import re as _re
                    m = _re.search(r":(\d{4,5})/", raw_cdp)
                    if m:
                        cdp_port = int(m.group(1))

                if cdp_port:
                    ab_client = AgentBrowserClient.from_cdp_port(cdp_port)
                    dom_executor = DomExecutor(ab_client)
                    dom_result = await dom_executor.execute_test_case(
                        case=case,
                        target_url=target_url,
                        cancel_event=cancel_event,
                    )
                    logger.info(f"[OneClick] DOM 模式执行完成: {dom_result.get('status')}")
                    if dom_result.get("status") != "error":
                        return dom_result
                    logger.warning("[OneClick] DOM 执行器返回 error，回退到视觉辅助链路")
                else:
                    logger.warning("[OneClick] 无法获取 CDP 端口，回退到视觉辅助链路")

            except ImportError as ie:
                logger.debug(f"[OneClick] DOM 模式模块未加载，回退到视觉辅助链路: {ie}")
            except Exception as dom_err:
                logger.warning(f"[OneClick] DOM 优先执行失败，回退到视觉辅助链路: {dom_err}")
        # ── DOM 模式检测结束 ──────────────────────────────────────────

        try:
            from llm import get_active_browser_use_llm, FailoverChatModel, get_auto_switcher
            from browser_use import Agent
            from Api_request.prompts import BROWSER_USE_CHINESE_SYSTEM

            raw_llm = get_active_browser_use_llm()
            # 用 FailoverChatModel 包装，实现 429 时自动切换模型
            switcher = get_auto_switcher()
            if switcher.enabled and len(switcher._profiles) > 1:
                llm = FailoverChatModel(raw_llm, switcher)
                logger.info("[OneClick] ✅ 已启用 FailoverChatModel，支持 429 自动切换")
            else:
                llm = raw_llm
                logger.info("[OneClick] ⚠️ 自动切换未启用或仅有1个模型，使用原始 LLM")
            max_steps = int(os.getenv("MAX_STEPS", "100"))
            max_actions = int(os.getenv("MAX_ACTIONS", "10"))
            use_vision = os.getenv("LLM_USE_VISION", "false").lower() == "true"

            # 构建任务描述
            steps_text = ""
            steps = case.get("steps", [])
            if isinstance(steps, list):
                steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
            elif isinstance(steps, str):
                steps_text = steps

            test_data = case.get("test_data", {})
            data_text = ""
            if test_data:
                data_text = f"\n测试数据: {json.dumps(test_data, ensure_ascii=False)}"

            # 执行阶段严格最小上下文：仅测试用例 + 目标地址
            task = f"""【一键测试任务】
目标地址: {target_url}
测试用例: {case.get('title', '')}
测试步骤:
{steps_text}
预期结果: {case.get('expected', '')}
{data_text}

请按照步骤执行测试，并验证预期结果。

⚠️ 重要提醒：
- 点击「登录」「提交」「确定」等会触发页面跳转的按钮后，先用 wait 等待 2 秒，再观察浏览器页面状态来判断结果
- 点击其他普通按钮后等待 1 秒即可
- 不要使用 extract 或 run_javascript 去搜索 Toast/消息提示（它们只显示1-3秒就消失了）
- 通过 URL 变化、页面内容变化、表单是否仍在等间接证据来判断操作结果
- 每一步都要先判断当前页面状态（URL、关键元素是否存在、是否已登录），再决定下一步操作
- 如果页面状态与预期不一致，优先执行纠偏步骤（回到目标页、重新定位元素、等待加载）

🔴 【严格步骤边界 - 必须遵守】：
- 只执行测试步骤中明确列出的操作，执行完所有步骤后立即调用 done 返回结果
- 禁止自行添加「步骤之外」的额外验证操作（如步骤只要求退出登录，不要再次登录验证）
- 验证预期结果只需观察当前页面状态，不需要额外导航或重新执行之前的操作
- 如果测试步骤完成后结果模糊，基于当前页面最直接的证据判断，然后立即 done

🔴 【关键】done 的 success 字段判定规则：
- success 表示"实际结果是否符合预期结果"，而不是"操作本身是否成功"
- 如果预期结果是"登录失败/提示错误/停留在登录页"，而实际确实登录失败了 → success: true（符合预期）
- 如果预期结果是"登录成功/跳转到首页"，而实际确实登录成功了 → success: true（符合预期）
- 如果预期结果是"登录失败"，但实际登录成功了 → success: false（不符合预期）
- 简单来说：实际结果 == 预期结果 → success: true，实际结果 != 预期结果 → success: false

🚫 动作约束（必须遵守）：
- 只允许使用以下动作：click、input、navigate、search、wait、done
- 严禁输出 evaluate、run_javascript、extract 或任何未定义动作
- 若无法继续，请使用 done 且 success=false，并在 text 中写明阻塞原因"""

            extend_prompt = BROWSER_USE_CHINESE_SYSTEM

            extend_prompt += (
                "\n\n🔒 执行动作白名单（严格）：仅可输出 click/input/navigate/search/wait/done。"
                "若输出 evaluate/run_javascript/extract 或其他动作，视为无效响应。"
            )

            # 如果有循环检测器，在 system prompt 中注入提示
            if loop_detector:
                extend_prompt += (
                    "\n\n⚠️ 循环检测已启用：如果你发现自己在重复执行相同的操作且没有进展，"
                    "请立即改变策略或标记任务为失败。不要反复尝试同一个操作。"
                )

            # 创建 Agent（复用共享浏览器）
            agent = Agent(
                task=task,
                llm=llm,
                browser_session=browser_session,
                use_vision=use_vision,
                max_actions_per_step=max_actions,
                extend_system_message=extend_prompt,
            )

            # 执行测试（带取消检测）
            try:
                history = await agent.run(max_steps=max_steps)
            except Exception as run_err:
                run_err_text = str(run_err)
                # 常见 schema 错误：重试一次，并进一步收紧动作提示
                if "AgentOutput" in run_err_text or "validation error" in run_err_text.lower():
                    logger.warning(f"[OneClick] Agent 输出 schema 校验失败，开始一次收敛重试: {run_err_text}")
                    strict_prompt = extend_prompt + (
                        "\n\n二次重试规则：每次只输出一个动作；不要使用任何白名单外动作；"
                        "若元素不可定位，先 wait 或 navigate，再 done(success=false)。"
                    )
                    retry_agent = Agent(
                        task=task,
                        llm=llm,
                        browser_session=browser_session,
                        use_vision=use_vision,
                        max_actions_per_step=1,
                        extend_system_message=strict_prompt,
                    )
                    history = await retry_agent.run(max_steps=max_steps)
                else:
                    raise

            # 检查执行后是否被取消
            if cancel_event and cancel_event.is_set():
                return {
                    "status": "cancelled",
                    "message": "测试已被手动停止",
                    "duration": int(time.time() - start),
                    "steps": 0,
                }

            duration = int(time.time() - start)

            # 分析结果
            final_result = history.final_result() if hasattr(history, 'final_result') else ""

            # 判断成功/失败
            # 优先使用 Agent 自身的判定（done action 的 success 字段）
            # Agent 会根据预期结果来判断：如果预期"登录失败"且确实失败了，success=true
            status = "pass"
            
            # 1. 优先：Agent 的 done action 中的 success 字段
            agent_success = None
            if hasattr(history, 'is_successful'):
                agent_success = history.is_successful()
            
            if agent_success is not None:
                # Agent 明确给出了判定
                status = "pass" if agent_success else "fail"
                logger.info(f"[OneClick] Agent 判定结果: success={agent_success} → status={status}")
            elif hasattr(history, 'has_errors') and history.has_errors():
                # 2. Agent 没有明确判定，但执行过程有错误
                status = "fail"
                logger.info("[OneClick] Agent 未给出判定，但执行过程有错误 → status=fail")
            elif not (hasattr(history, 'is_done') and history.is_done()):
                # 3. Agent 没有正常完成（没有调用 done）
                status = "fail"
                logger.info("[OneClick] Agent 未正常完成（未调用 done）→ status=fail")
            else:
                # 4. Agent 调用了 done 但 success 字段为 None（兼容旧版本）
                # 此时保持 pass，因为 Agent 认为任务完成了
                logger.info("[OneClick] Agent 调用了 done 但未设置 success 字段 → status=pass")

            total_steps = len(history.history) if hasattr(history, 'history') else 0

            # 收集循环检测统计
            loop_stats = loop_detector.get_stats() if loop_detector else {}

            return {
                "status": status,
                "message": final_result if final_result else ("测试通过" if status == "pass" else "测试失败"),
                "duration": duration,
                "steps": total_steps,
                "loop_stats": loop_stats,
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(f"[OneClick] 浏览器测试失败: {error_msg}")

            # 检测 429 限流
            if _is_rate_limit_error(error_msg):
                # 尝试自动切换模型
                try:
                    switcher = get_auto_switcher()
                    if switcher.enabled:
                        reason = classify_failure_reason(e)
                        new_id = switcher.mark_failure(
                            switcher._current_model_id or 0, reason
                        )
                        if new_id:
                            logger.info(f"[OneClick] 🔄 模型已自动切换到 ID={new_id}")
                except Exception as switch_err:
                    logger.warning(f"[OneClick] 自动切换失败: {switch_err}")

                return {
                    "status": "rate_limited",
                    "message": f"API 配额耗尽: {error_msg}",
                    "duration": int(time.time() - start),
                    "steps": 0,
                }

            # 其他错误也尝试标记到 auto_switcher
            try:
                switcher = get_auto_switcher()
                if switcher.enabled:
                    reason = classify_failure_reason(e)
                    switcher.mark_failure(
                        switcher._current_model_id or 0, reason
                    )
            except Exception:
                pass

            return {
                "status": "error",
                "message": error_msg,
                "duration": int(time.time() - start),
                "steps": 0,
            }

    # ========== 自动生成报告 ==========

    @staticmethod
    async def _save_reports(
        db: Session, session: OneclickSession,
        cases: List[Dict], result: Dict
    ) -> Dict:
        """
        执行完成后自动生成测试报告 + Bug 报告

        - 测试报告：保存到 test_reports 表，在 /report/run 页面可查看
        - Bug 报告：对失败/错误用例，保存到 bug_reports 表，在 /report/bug 页面可查看
        """
        from database.connection import TestReport, BugReport

        summary = result.get("summary", {})
        results_list = result.get("results", [])
        report_id = None
        bug_count = 0

        # ---- 1. 生成测试报告 ----
        try:
            pass_rate = round(
                summary.get("passed", 0) / max(summary.get("total", 1), 1) * 100, 2
            )

            # 构建报告详情（Markdown）
            details_lines = [
                f"# 一键测试报告",
                f"",
                f"## 测试概览",
                f"- 会话 ID: {session.id}",
                f"- 用户需求: {session.user_input}",
                f"- 目标地址: {session.target_url or '-'}",
                f"- 总用例数: {summary.get('total', 0)}",
                f"- 通过: {summary.get('passed', 0)}",
                f"- 失败: {summary.get('failed', 0)}",
                f"- 通过率: {pass_rate}%",
                f"- 总耗时: {summary.get('duration', 0)} 秒",
                f"",
                f"## 用例执行详情",
                f"",
            ]

            for r in results_list:
                status = r.get("status", "unknown")
                icon = "✅" if status == "pass" else "❌" if status in ("fail", "error") else "⚠️"
                details_lines.append(f"### {icon} {r.get('index', '')}. {r.get('title', '')}")
                details_lines.append(f"- 状态: {status}")
                details_lines.append(f"- 耗时: {r.get('duration', 0)} 秒")
                details_lines.append(f"- 步数: {r.get('steps', 0)}")
                if r.get("message"):
                    # 截断过长的消息
                    msg_text = r["message"][:500]
                    details_lines.append(f"- 结果: {msg_text}")
                details_lines.append("")

            report_details = "\n".join(details_lines)

            report = TestReport(
                project_id=getattr(session, 'project_id', None),
                title=f"一键测试_{session.user_input[:30]}_{datetime.now().strftime('%m%d_%H%M')}",
                summary={
                    "total": summary.get("total", 0),
                    "pass": summary.get("passed", 0),
                    "fail": summary.get("failed", 0),
                    "pass_rate": pass_rate,
                    "duration": summary.get("duration", 0),
                    "total_steps": sum(r.get("steps", 0) for r in results_list),
                    "execution_mode": "一键测试",
                    "session_id": session.id,
                },
                details=report_details,
                format_type="markdown",
                total_steps=sum(r.get("steps", 0) for r in results_list),
            )
            db.add(report)
            db.flush()
            report_id = report.id
            logger.info(f"[OneClick] 📄 测试报告已保存: ID={report_id}")
        except Exception as e:
            logger.warning(f"[OneClick] 保存测试报告失败: {e}")

        # ---- 2. 将所有失败用例整合为一条 Bug 报告 ----
        failed_items = []
        highest_severity = "四级"
        severity_rank = {"一级": 1, "二级": 2, "三级": 3, "四级": 4}
        error_types_set = set()

        for r in results_list:
            status = r.get("status", "")
            if status not in ("fail", "error"):
                continue

            idx = r.get("index", 0) - 1
            case = cases[idx] if 0 <= idx < len(cases) else {}

            # 根据状态判断严重程度
            if status == "error":
                severity = "一级"
                error_type = "系统错误"
            elif "rate_limited" in r.get("message", ""):
                severity = "三级"
                error_type = "环境问题"
            else:
                severity = "二级"
                error_type = "功能错误"

            error_types_set.add(error_type)
            if severity_rank.get(severity, 4) < severity_rank.get(highest_severity, 4):
                highest_severity = severity

            failed_items.append({
                "title": r.get("title", "未知用例"),
                "status": status,
                "severity": severity,
                "error_type": error_type,
                "expected": case.get("expected", ""),
                "actual": (r.get("message", "") or "")[:500],
                "steps": case.get("steps", []),
            })

        bug_count = len(failed_items)

        if bug_count > 0:
            try:
                # 整合复现步骤：每个失败用例作为一个段落
                reproduce_sections = []
                actual_sections = []
                expected_sections = []
                for i, item in enumerate(failed_items, 1):
                    reproduce_sections.append(f"【Bug {i}】{item['title']}（{item['severity']}/{item['error_type']}）")
                    steps = item["steps"]
                    if isinstance(steps, list):
                        for s_idx, step in enumerate(steps, 1):
                            step_text = step if isinstance(step, str) else json.dumps(step, ensure_ascii=False)
                            reproduce_sections.append(f"  {s_idx}. {step_text}")
                    else:
                        reproduce_sections.append(f"  {steps}")
                    reproduce_sections.append("")

                    expected_sections.append(f"【Bug {i}】{item['title']}: {item['expected']}")
                    actual_sections.append(f"【Bug {i}】{item['title']}: {item['actual']}")

                reproduce_text = "\n".join(reproduce_sections)
                expected_text = "\n".join(expected_sections)
                actual_text = "\n".join(actual_sections)

                # 汇总反馈
                feedback_lines = [f"本次一键测试共发现 {bug_count} 个问题："]
                for i, item in enumerate(failed_items, 1):
                    feedback_lines.append(f"  {i}. [{item['severity']}][{item['error_type']}] {item['title']}")
                feedback_text = "\n".join(feedback_lines)

                combined_error_type = "/".join(sorted(error_types_set)) if error_types_set else "功能错误"

                bug = BugReport(
                    project_id=getattr(session, 'project_id', None),
                    test_record_id=None,
                    bug_name=f"[一键测试] 会话#{session.id} Bug汇总（{bug_count}项）",
                    test_case_id=None,
                    location_url=session.target_url or "",
                    error_type=combined_error_type,
                    severity_level=highest_severity,
                    reproduce_steps=reproduce_text[:5000],
                    result_feedback=feedback_text[:2000],
                    expected_result=expected_text[:2000],
                    actual_result=actual_text[:2000],
                    status="待处理",
                    description=f"一键测试会话 #{session.id}（{session.user_input[:50]}）共发现 {bug_count} 个Bug",
                    case_type="功能测试",
                    execution_mode="一键测试",
                )
                db.add(bug)
                logger.info(f"[OneClick] 🐛 已生成 1 条整合 Bug 报告（包含 {bug_count} 个Bug）")
            except Exception as e:
                logger.warning(f"[OneClick] 保存 Bug 报告失败: {e}")

        try:
            db.commit()
        except Exception as e:
            logger.error(f"[OneClick] 提交报告到数据库失败: {e}")
            db.rollback()

        # ---- 3. 自动发送邮件给 auto_receive_bug 联系人（放入线程池避免阻塞） ----
        email_result = {"success": False, "message": "未发送"}
        try:
            loop = asyncio.get_event_loop()
            email_result = await loop.run_in_executor(
                None,
                OneClickService._send_oneclick_report_email,
                db, session, summary, results_list, cases,
                report_id, bug_count
            )
        except Exception as e:
            logger.warning(f"[OneClick] 自动发送邮件失败: {e}")
            email_result = {"success": False, "message": str(e)}

        return {
            "report_id": report_id,
            "bug_count": bug_count,
            "email": email_result,
        }

    @staticmethod
    def _send_oneclick_report_email(
        db: Session,
        session: OneclickSession,
        summary: Dict,
        results_list: List[Dict],
        cases: List[Dict],
        report_id: Optional[int],
        bug_count: int,
    ) -> Dict:
        """
        将测试报告 + Bug 报告整合为一封邮件，发送给 auto_receive_bug=1 的联系人

        仅在一键测试功能中触发，整合两种报告类型。
        """
        from database.connection import Contact, EmailConfig, EmailRecord

        # 查询自动接收 Bug 的联系人
        contacts = db.query(Contact).filter(Contact.auto_receive_bug == 1).all()
        if not contacts:
            logger.info("[OneClick] 📧 没有自动接收 Bug 的联系人，跳过邮件发送")
            return {"success": False, "message": "没有自动接收联系人"}

        # 获取激活的邮件配置
        email_config = db.query(EmailConfig).filter(EmailConfig.is_active == 1).first()
        if not email_config:
            logger.info("[OneClick] 📧 未配置邮件服务，跳过邮件发送")
            return {"success": False, "message": "未配置邮件服务"}

        # 构建邮件
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        total = summary.get("total", 0)
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        duration = summary.get("duration", 0)
        pass_rate = round(passed / max(total, 1) * 100, 1)

        subject = f"[一键测试] {session.user_input[:40]} - 通过 {passed}/{total} - {now_str}"

        # 构建用例结果表格行
        case_rows = ""
        bug_rows = ""
        for r in results_list:
            status = r.get("status", "unknown")
            status_text = "✅ 通过" if status == "pass" else "❌ 失败" if status == "fail" else "⚠️ 错误" if status == "error" else "🚫 限流"
            status_color = "#16a34a" if status == "pass" else "#dc2626" if status in ("fail", "error") else "#d97706"
            msg = (r.get("message", "") or "")[:150]

            case_rows += f"""<tr>
                <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;">{r.get('index', '')}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;">{r.get('title', '')}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;color:{status_color};font-weight:600;">{status_text}</td>
                <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;">{r.get('duration', 0)}s</td>
                <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#64748b;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{msg}</td>
            </tr>"""

            # Bug 详情行（仅失败/错误用例）
            if status in ("fail", "error"):
                idx = r.get("index", 0) - 1
                case = cases[idx] if 0 <= idx < len(cases) else {}
                expected = (case.get("expected", "") or "")[:100]
                severity = "一级(系统错误)" if status == "error" else "二级(功能错误)"
                sev_color = "#dc2626" if status == "error" else "#ea580c"

                bug_rows += f"""<tr>
                    <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;">{r.get('title', '')}</td>
                    <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:13px;color:{sev_color};font-weight:600;">{severity}</td>
                    <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:12px;">{expected}</td>
                    <td style="padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:12px;color:#dc2626;">{msg}</td>
                </tr>"""

        # Bug 报告区块（仅在有 Bug 时显示）
        bug_section = ""
        if bug_count > 0 and bug_rows:
            bug_section = f"""
            <div style="margin-top:8px;">
                <h2 style="font-size:16px;font-weight:600;color:#1e293b;margin:0 0 12px;">
                    🐛 Bug 报告（{bug_count} 条）
                </h2>
                <table width="100%" style="border-collapse:collapse;">
                    <tr style="background:#fef2f2;">
                        <th style="padding:10px 12px;text-align:left;font-size:12px;color:#991b1b;font-weight:600;">用例名称</th>
                        <th style="padding:10px 12px;text-align:left;font-size:12px;color:#991b1b;font-weight:600;">严重程度</th>
                        <th style="padding:10px 12px;text-align:left;font-size:12px;color:#991b1b;font-weight:600;">预期结果</th>
                        <th style="padding:10px 12px;text-align:left;font-size:12px;color:#991b1b;font-weight:600;">实际结果</th>
                    </tr>
                    {bug_rows}
                </table>
            </div>"""

        # 完整 HTML 邮件
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:'Helvetica Neue',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
<div style="max-width:720px;margin:20px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
  <div style="background:linear-gradient(135deg,#007857,#00a67e);padding:28px 36px;color:#fff;">
    <h1 style="margin:0;font-size:20px;font-weight:600;">一键测试报告</h1>
    <p style="margin:6px 0 0;font-size:13px;opacity:0.85;">{now_str} · 会话 #{session.id} · {session.user_input[:50]}</p>
  </div>

  <div style="padding:24px 36px;">
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
      <tr>
        <td style="width:25%;text-align:center;padding:16px 8px;background:#f0fdf4;border-radius:8px;">
          <div style="font-size:28px;font-weight:700;color:#16a34a;">{pass_rate}%</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">通过率</div>
        </td>
        <td style="width:8px;"></td>
        <td style="width:25%;text-align:center;padding:16px 8px;background:#f0fdf4;border-radius:8px;">
          <div style="font-size:28px;font-weight:700;color:#007857;">{passed}/{total}</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">通过/总计</div>
        </td>
        <td style="width:8px;"></td>
        <td style="width:25%;text-align:center;padding:16px 8px;background:{'#fef2f2' if failed > 0 else '#f0fdf4'};border-radius:8px;">
          <div style="font-size:28px;font-weight:700;color:{'#dc2626' if failed > 0 else '#16a34a'};">{failed}</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">失败</div>
        </td>
        <td style="width:8px;"></td>
        <td style="width:25%;text-align:center;padding:16px 8px;background:#eff6ff;border-radius:8px;">
          <div style="font-size:28px;font-weight:700;color:#2563eb;">{duration}s</div>
          <div style="font-size:12px;color:#666;margin-top:4px;">耗时</div>
        </td>
      </tr>
    </table>

    <div style="border-top:1px solid #e5e7eb;padding-top:20px;">
      <h2 style="font-size:16px;font-weight:600;color:#1e293b;margin:0 0 12px;">📋 用例执行详情</h2>
      <table width="100%" style="border-collapse:collapse;">
        <tr style="background:#f8fafc;">
          <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;font-weight:600;">#</th>
          <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;font-weight:600;">用例名称</th>
          <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;font-weight:600;">状态</th>
          <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;font-weight:600;">耗时</th>
          <th style="padding:10px 12px;text-align:left;font-size:12px;color:#64748b;font-weight:600;">结果说明</th>
        </tr>
        {case_rows}
      </table>
    </div>

    {bug_section}

    <div style="margin-top:20px;padding:12px 16px;background:#f8fafc;border-radius:8px;font-size:12px;color:#94a3b8;">
      测试报告 ID: {report_id or '-'} · Bug 报告: {bug_count} 条 · 目标地址: {session.target_url or '-'}
    </div>
  </div>

  <div style="background:#f8fafc;padding:14px 36px;text-align:center;font-size:12px;color:#94a3b8;">
    此邮件由 AI 测试平台（一键测试）自动生成发送
  </div>
</div>
</body></html>"""

        # 发送邮件
        from Email_manage.sender import dispatch_send
        recipients_result = []
        success_count = 0
        failed_count_email = 0

        for contact in contacts:
            to_email = (
                email_config.test_email
                if email_config.test_mode == 1 and email_config.test_email
                else contact.email
            )
            try:
                dispatch_send(email_config, to_email, subject, html)
                success_count += 1
                recipients_result.append({
                    "name": contact.name, "email": contact.email, "status": "success"
                })
                logger.info(f"[OneClick] 📧 邮件已发送: {contact.name} <{to_email}>")
            except Exception as e:
                failed_count_email += 1
                recipients_result.append({
                    "name": contact.name, "email": contact.email,
                    "status": "failed", "error": str(e)
                })
                logger.warning(f"[OneClick] 📧 邮件发送失败: {contact.name} - {e}")

        # 记录发送历史
        status = (
            'success' if failed_count_email == 0
            else ('partial' if success_count > 0 else 'failed')
        )
        try:
            record = EmailRecord(
                subject=subject,
                recipients=recipients_result,
                status=status,
                success_count=success_count,
                failed_count=failed_count_email,
                total_count=len(contacts),
                email_type='oneclick_report',
                content_summary=f"一键测试报告: 通过 {passed}/{total}, Bug {bug_count} 条",
            )
            db.add(record)
            db.commit()
        except Exception as e:
            logger.warning(f"[OneClick] 保存邮件记录失败: {e}")
            db.rollback()

        logger.info(
            f"[OneClick] 📧 邮件发送完成: 成功 {success_count}, 失败 {failed_count_email}"
        )
        return {
            "success": success_count > 0,
            "message": f"已发送 {success_count}/{len(contacts)} 位联系人",
            "success_count": success_count,
            "failed_count": failed_count_email,
        }

    # ========== 会话查询 ==========

    @staticmethod
    def get_session_detail(db: Session, session_id: int) -> Optional[Dict]:
        """获取会话详情（含任务树）"""
        session = SessionManager.get_session(db, session_id)
        if not session:
            return None

        # 解析任务树
        task_tree_data = None
        task_tree_stats = None
        if session.task_tree:
            try:
                raw = json.loads(session.task_tree) if isinstance(session.task_tree, str) else session.task_tree
                task_tree_data = raw
                tree = TaskTree.from_dict(raw)
                task_tree_stats = tree.stats()
            except Exception as e:
                logger.warning(f"[OneClick] 解析任务树失败: {e}")

        runtime_state = _serialize_runtime_state(_running_sessions.get(session_id))

        return {
            "id": session.id,
            "user_input": session.user_input,
            "status": session.status,
            "target_url": session.target_url,
            "login_info": json.loads(session.login_info) if session.login_info else None,
            "page_analysis": json.loads(session.page_analysis) if session.page_analysis else None,
            "page_capabilities": json.loads(session.page_capabilities) if getattr(session, 'page_capabilities', None) else None,
            "task_tree": task_tree_data,
            "task_tree_stats": task_tree_stats,
            "generated_cases": json.loads(session.generated_cases) if session.generated_cases else [],
            "confirmed_cases": json.loads(session.confirmed_cases) if session.confirmed_cases else [],
            "execution_result": json.loads(session.execution_result) if session.execution_result else None,
            "messages": SessionManager.get_messages(session),
            "runtime_stats": SessionManager.get_runtime_stats(session_id),
            "exploration_debug": runtime_state,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @staticmethod
    async def _kill_browser_with_timeout(browser, session_id: int, timeout_sec: float = 5.0) -> None:
        """异步安全关闭浏览器，避免 stop 接口被长时间阻塞。"""
        if not browser:
            return
        try:
            await asyncio.wait_for(browser.kill(), timeout=timeout_sec)
            logger.info(f"[OneClick] ✅ 浏览器已强制关闭: session_id={session_id}")
        except asyncio.TimeoutError:
            logger.warning(f"[OneClick] ⚠️ 关闭浏览器超时({timeout_sec}s): session_id={session_id}")
        except Exception as e:
            logger.warning(f"[OneClick] ⚠️ 关闭浏览器异常: {e}")

    @staticmethod
    async def stop_session(db: Session, session_id: int) -> Dict:
        """
        停止会话 — 真正取消正在运行的任务

        改进：
        1. 设置 cancel_event 通知执行循环停止
        2. 关闭正在运行的浏览器实例
        3. 更新数据库状态
        """
        session = SessionManager.get_session(db, session_id)
        if not session:
            return {"success": False, "message": "会话不存在"}

        if session.status in ('completed', 'failed'):
            return {"success": False, "message": "会话已结束"}

        # 1. 设置取消信号
        running = _running_sessions.get(session_id)
        if running:
            cancel_event = running.get("cancel_event")
            if cancel_event:
                cancel_event.set()
                logger.info(f"[OneClick] ⏹️ 已发送取消信号: session_id={session_id}")

            # 2. 关闭浏览器
            browser = running.get("browser_session")
            if browser:
                # 不阻塞 stop 接口，后台异步关闭浏览器
                asyncio.create_task(
                    OneClickService._kill_browser_with_timeout(browser, session_id)
                )
            _cleanup_runtime_exploration_cache(running)
        else:
            logger.info(f"[OneClick] ℹ️ 会话 {session_id} 没有正在运行的任务")

        # 3. 更新数据库状态
        session.status = 'failed'
        session.updated_at = datetime.now()
        SessionManager.add_message(db, session, 'assistant', '⏹️ 测试已手动停止')
        db.commit()
        return {"success": True, "message": "已停止"}


# ========== 工具函数 ==========

def _is_rate_limit_error(error_msg: str) -> bool:
    """检测是否为 429 限流错误"""
    lower = error_msg.lower()
    return any(kw in lower for kw in [
        '429', 'rate limit', 'rate_limit', 'quota',
        'exceeded', 'too many requests',
        'modelratelimiterror',
    ])
