"""
UI 探索 Agent 引擎（v3 — 统一引擎，参考 Claude Code queryLoop 架构）

核心设计（来自 Claude Code 源码分析）：
  1. LLM 驱动决策 — 所有探索路径由 LLM 通过 tool_use 选择
  2. 工具确定性执行 — 每个工具是纯粹的确定性操作
  3. 结果程序化采集 — scan_page/probe_interactions 结果由采集器自动捕获，无需 LLM 输出 JSON
  4. 精准反馈循环 — 工具结果（含错误）完整返回 LLM，支持自纠正
  5. 分层工具体系 — 高级工具(scan_page/probe_interactions/auto_login) + 基础工具(16个)

执行流程：
  Phase 0 (Bootstrap):  自动导航 → 截图 → 读取页面（不等 LLM）
  Phase 1-N (Agent Loop): LLM 决策(tool_use) → 执行工具 → 采集器捕获结构化数据 → tool_result 反馈 LLM → 迭代
  Final:                  LLM 不调工具(end_turn) → 从采集器获取结构化结果 → 每页存 Qdrant
"""

import json
import re
import logging
import asyncio
import time
import traceback
import uuid
from typing import Any, Optional, Dict, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 终端彩色输出（参考 browser-use 风格）
# ═══════════════════════════════════════════════════════════════════

BLUE = '\033[34m'
GREEN = '\033[32m'
RED = '\033[31m'
MAGENTA = '\033[35m'
CYAN = '\033[36m'
YELLOW = '\033[33m'
BOLD = '\033[1m'
RESET = '\033[0m'


def _print_step(step: int, total: int, msg: str):
    """终端打印步骤信息"""
    print(f"\n{BOLD}{CYAN}📍 Step {step}/{total}{RESET}: {msg}")


def _print_action(action_num: int, total: int, action_name: str, params: dict):
    """终端打印工具调用（browser-use 风格）"""
    params_short = {k: (str(v)[:80] + '...' if len(str(v)) > 80 else v) for k, v in params.items()}
    params_str = ', '.join(f"{MAGENTA}{k}{RESET}: {v}" for k, v in params_short.items())
    print(f"  ▶️  [{action_num}/{total}] {BLUE}{action_name}{RESET}: {params_str}")


def _print_result(action_name: str, success: bool, detail: str = ""):
    """终端打印工具结果"""
    icon = f"{GREEN}✅{RESET}" if success else f"{RED}❌{RESET}"
    detail_short = detail[:120] + '...' if len(detail) > 120 else detail
    print(f"  {icon} {action_name}: {detail_short}")


def _print_thinking(content: str):
    """终端打印 LLM 思考内容"""
    if content:
        short = content[:200] + '...' if len(content) > 200 else content
        print(f"  {YELLOW}💡 思考{RESET}: {short}")


def _print_phase(phase: str, msg: str):
    """终端打印阶段信息"""
    print(f"\n{BOLD}{GREEN}{'═'*60}{RESET}")
    print(f"{BOLD}{GREEN}  {phase}{RESET}: {msg}")
    print(f"{BOLD}{GREEN}{'═'*60}{RESET}")

# ═══════════════════════════════════════════════════════════════════
# 配置常量
# ═══════════════════════════════════════════════════════════════════

LLM_CALL_TIMEOUT = 120       # LLM 调用超时（秒）
TOOL_EXEC_TIMEOUT = 60       # 工具执行超时（秒）
MAX_TOOL_RESULT_CHARS = 3000 # 单个工具结果最大字符数
MAX_CONTEXT_MESSAGES = 40    # 最大保留消息数（避免上下文爆炸）
MAX_RETRIES = 2              # LLM 调用失败重试次数
AUTO_EXTEND_STEPS = 15       # 自动续航增加的步数
MAX_TOTAL_STEPS = 100        # 绝对最大步数上限（防止无限循环）


# ═══════════════════════════════════════════════════════════════════
# 结构化数据采集器（参考 Claude Code 的 Side-Channel 采集模式）
# ═══════════════════════════════════════════════════════════════════

class SiteExplorationCollector:
    """
    探索数据采集器

    在 Agent 循环中，拦截 scan_page / probe_interactions 工具的执行结果，
    程序化构建 SiteExplorationResult。LLM 不需要输出 JSON，数据自动收集。

    工作原理（类比 Claude Code）：
      - Claude Code 的每个工具执行后，结果同时发给 LLM（作为 tool_result）
        和内部监听器（日志、telemetry 等）
      - 我们的采集器就是这个内部监听器：工具结果同时发给 LLM 和采集器
    """

    def __init__(self, entry_url: str):
        from UI_Exploration.interaction_map import SiteExplorationResult, PageInteractionMap
        self.site_result = SiteExplorationResult(entry_url=entry_url)
        self._page_maps: Dict[str, PageInteractionMap] = {}  # url → PageInteractionMap
        self._start_time = time.time()

    def on_tool_result(self, tool_name: str, tool_result) -> None:
        """
        拦截工具执行结果，提取结构化数据。

        在 Agent 循环中，每次工具执行完毕后调用此方法。
        只有 scan_page 和 probe_interactions 会产生可采集数据。
        """
        if not getattr(tool_result, 'success', False):
            return

        output = getattr(tool_result, 'output', None)
        if not isinstance(output, dict):
            return

        page_map_dict = output.get("_page_map_dict")
        if not page_map_dict:
            return

        if tool_name in ("scan_page", "probe_interactions"):
            self._upsert_page_map(page_map_dict)

    def _upsert_page_map(self, page_map_dict: dict) -> None:
        """更新或插入页面交互数据"""
        from UI_Exploration.interaction_map import PageInteractionMap

        url = page_map_dict.get("page_url", "")
        if not url:
            return

        if url in self._page_maps:
            # 已存在则更新（probe_interactions 会更新 effects 字段）
            existing = self._page_maps[url]
            new_map = PageInteractionMap.from_dict(page_map_dict)
            # 用新数据的 interactions 替换旧的（probe 后的数据更完整）
            existing.interactions = new_map.interactions
            existing.forms = new_map.forms or existing.forms
            existing.tables = new_map.tables or existing.tables
            existing.summary = new_map.summary or existing.summary
            # 合并 child_pages
            existing_children = set(existing.child_pages)
            for child in new_map.child_pages:
                if child not in existing_children:
                    existing.child_pages.append(child)
        else:
            self._page_maps[url] = PageInteractionMap.from_dict(page_map_dict)

    def build_result(self) -> "SiteExplorationResult":
        """构建最终的 SiteExplorationResult"""
        self.site_result.pages = list(self._page_maps.values())
        self.site_result.duration_seconds = time.time() - self._start_time
        self.site_result.build_navigation_graph()

        page_titles = [p.page_title for p in self.site_result.pages if p.page_title]
        self.site_result.summary = (
            f"共探索 {self.site_result.total_pages} 个页面，"
            f"发现 {self.site_result.total_interactions} 个交互项。"
            f"页面包括：{'、'.join(page_titles[:10])}"
        )
        return self.site_result

    @property
    def has_data(self) -> bool:
        return len(self._page_maps) > 0

    @property
    def page_count(self) -> int:
        return len(self._page_maps)

    @property
    def interaction_count(self) -> int:
        return sum(p.interaction_count for p in self._page_maps.values())


# ═══════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ExploreRequest:
    """探索请求"""
    url: str
    user_instruction: str = ""
    focus_areas: List[str] = field(default_factory=list)
    max_steps: int = 30
    login_info: Dict = field(default_factory=dict)
    project_id: Optional[int] = None


@dataclass
class ExploreSession:
    """探索会话状态"""
    session_id: str
    url: str
    status: str = "pending"  # pending -> exploring -> completed | failed | cancelled
    current_step: int = 0
    max_steps: int = 30
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    result: Optional[Dict] = None
    error: Optional[str] = None
    screenshots: List[str] = field(default_factory=list)
    messages: List[Dict] = field(default_factory=list)  # 对话历史
    driver = None  # Selenium WebDriver 实例

    @property
    def duration(self) -> float:
        return (self.end_time or time.time()) - self.start_time

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "url": self.url,
            "status": self.status,
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "duration_seconds": round(self.duration, 1),
            "result": self.result,
            "error": self.error,
            "screenshots_count": len(self.screenshots),
            # 兼容前端字段
            "task_id": self.session_id,
            "message": f"步骤 {self.current_step}/{self.max_steps}" if self.status == "exploring"
                       else (self.error or ("探索完成" if self.status == "completed" else "")),
            "engine_type": "selenium_agent",
            "current_task": f"正在执行第 {self.current_step} 步操作" if self.status == "exploring" else "",
        }


# ═══════════════════════════════════════════════════════════════════
# 核心 Agent 循环（v2 — Native Function Calling）
# ═══════════════════════════════════════════════════════════════════

class UIExplorerAgent:
    """
    UI 探索 Agent（v2）

    关键改进：
      - 使用 LLMClient.chat_with_tools() 原生 function calling
      - 正确的消息协议：assistant(tool_use) → user(tool_result)
      - 基于 finish_reason 的停止检测（不再依赖文本正则）
      - 上下文压缩防止 token 爆炸
      - 错误重试和恢复
    """

    def __init__(self):
        self._llm = None
        self._last_llm_error = ""
        self._last_llm_error_non_retryable = False

    @property
    def llm(self):
        """延迟获取 LLM 客户端"""
        if self._llm is None:
            logger.info("[UIExplore] 初始化 LLM 客户端...")
            from llm.client import get_llm_client
            self._llm = get_llm_client()
            logger.info("[UIExplore] LLM 客户端就绪")
        return self._llm

    async def explore(
        self,
        request: ExploreRequest,
        session: ExploreSession,
        on_step_callback=None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> Dict:
        """执行完整的 UI 探索流程"""
        from UI_Exploration.prompts import (
            EXPLORER_SYSTEM_PROMPT,
            build_exploration_user_prompt,
            TOOL_DEFINITIONS,
        )
        from UI_Exploration.tools import BrowserTools

        try:
            session.status = "exploring"
            logger.info(f"[UIExplore] ========== 开始探索 {request.url} ==========")
            _print_phase("UI 探索启动", f"目标: {request.url}")

            if not session.driver:
                raise RuntimeError("浏览器驱动未初始化")

            tools = BrowserTools(session.driver)

            # ═══ 初始化采集器 ═══
            collector = SiteExplorationCollector(entry_url=request.url)

            # ═══ Phase 0: 自动 Bootstrap（不等 LLM）═══
            logger.info(f"[UIExplore] [Phase 0/Bootstrap] 导航到: {request.url}")
            print(f"\n{CYAN}🔍 Phase 0: 自动侦察{RESET}")

            nav_result = await asyncio.to_thread(tools.navigate, request.url, timeout=30)
            logger.info(f"[UIExplore] [Phase 0] 导航结果: {nav_result.output}")

            await asyncio.sleep(2)  # 等待页面渲染

            screenshot_result = await asyncio.to_thread(tools.screenshot, full_page=False)
            logger.info(f"[UIExplore] [Phase 0] 截图完成")

            read_result = await asyncio.to_thread(tools.read_page)
            page_text = ""
            page_title = ""
            if hasattr(read_result, 'output') and isinstance(read_result.output, dict):
                page_text = read_result.output.get("text", read_result.output.get("visible_text", ""))[:3000]
                page_title = read_result.output.get("title", "")
            else:
                page_text = str(getattr(read_result, 'output', ""))[:3000]
                try:
                    page_title = session.driver.title
                except Exception:
                    pass

            logger.info(f"[UIExplore] [Phase 0] 页面读取完成 (标题={page_title}, 内容长度={len(page_text)})")

            links_result = await asyncio.to_thread(tools.get_links)
            buttons_result = await asyncio.to_thread(tools.get_buttons)
            forms_result = await asyncio.to_thread(tools.get_forms)

            links_count = self._count_result(links_result)
            buttons_count = self._count_result(buttons_result)
            forms_count = self._count_result(forms_result)

            logger.info(f"[UIExplore] [Phase 0] 页面概览: 标题={page_title}, "
                        f"链接={links_count}, 按钮={buttons_count}, 表单={forms_count}")
            print(f"  📄 页面标题: {BOLD}{page_title}{RESET}")
            print(f"  🔗 链接: {links_count}  🔘 按钮: {buttons_count}  📝 表单: {forms_count}")

            # ═══ 构建初始消息 ═══
            user_prompt = build_exploration_user_prompt(
                url=request.url,
                user_instruction=request.user_instruction,
                focus_areas=request.focus_areas,
                max_steps=request.max_steps,
                login_info=request.login_info,
            )
            user_prompt += f"\n\n## 已完成的初步侦察（Phase 0 自动执行）\n"
            user_prompt += f"- 当前 URL: {nav_result.output.get('url', request.url) if isinstance(nav_result.output, dict) else request.url}\n"
            user_prompt += f"- 页面标题: {page_title}\n"
            user_prompt += f"- 页面文本预览（前1500字符）: {page_text[:1500]}\n"
            user_prompt += f"- 发现链接数: {links_count}\n"
            user_prompt += f"- 按钮数: {buttons_count}\n"
            user_prompt += f"- 表单数: {forms_count}\n\n"
            user_prompt += "请根据以上信息，先调用 scan_page 获取交互项列表，然后用 probe_interactions 探测效果。"

            # 消息列表（provider-agnostic 格式）
            messages = [
                {"role": "system", "content": EXPLORER_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            session.messages = list(messages)

            # 探索覆盖率追踪
            pages_explored = set()
            pages_explored.add(request.url)

            # ═══ 主循环：Native Function Calling Agent Loop ═══
            logger.info(f"[UIExplore] [Agent Loop] 开始主循环 (最大步骤: {request.max_steps})")
            print(f"\n{CYAN}🤖 Agent Loop 开始 (最大 {request.max_steps} 步){RESET}")

            effective_max_steps = request.max_steps
            idle_rounds_without_tools = 0
            consecutive_llm_failures = 0

            while session.current_step < effective_max_steps:

                if cancel_event and cancel_event.is_set():
                    logger.info("[UIExplore] 收到取消信号")
                    session.status = "cancelled"
                    session.error = "用户取消了探索"
                    break

                session.current_step += 1
                step_num = session.current_step
                logger.info(f"[UIExplore] [Step {step_num}/{request.max_steps}] ── 开始 ──")
                _print_step(step_num, request.max_steps, "调用 LLM 决策中...")

                # 上下文压缩：保留系统消息 + 最近 N 条
                working_messages = self._compact_messages(messages)

                # 调用 LLM（native function calling + 重试）
                llm_response = await self._call_llm_with_retry(
                    working_messages, TOOL_DEFINITIONS, step_num, cancel_event=cancel_event
                )

                if llm_response is None:
                    consecutive_llm_failures += 1
                    # 重试后仍然失败，跳过此步
                    logger.error(f"[UIExplore] [Step {step_num}] LLM 调用失败，跳过")
                    print(f"  {RED}❌ LLM 调用失败，跳过此步{RESET}")
                    logger.error(
                        f"[UIExplore] [Step {step_num}] llm_failure "
                        f"consecutive={consecutive_llm_failures} "
                        f"non_retryable={self._last_llm_error_non_retryable} "
                        f"error={self._last_llm_error}"
                    )

                    if cancel_event and cancel_event.is_set():
                        logger.info("[UIExplore] LLM 失败后检测到取消信号，结束探索")
                        session.status = "cancelled"
                        session.error = "用户取消了探索"
                        break

                    if self._last_llm_error_non_retryable:
                        session.status = "failed"
                        session.error = self._last_llm_error or "LLM 配额或限流异常，探索已终止"
                        logger.error(f"[UIExplore] [Step {step_num}] 非重试类 LLM 错误，终止探索: {session.error}")
                        break

                    if consecutive_llm_failures >= 3:
                        session.status = "failed"
                        session.error = self._last_llm_error or "LLM 连续失败，探索已终止"
                        logger.error(f"[UIExplore] [Step {step_num}] LLM 连续失败 {consecutive_llm_failures} 次，终止探索")
                        break
                    continue

                consecutive_llm_failures = 0
                logger.info(
                    f"[UIExplore] [Step {step_num}] LLM 响应: "
                    f"finish={llm_response.finish_reason}, "
                    f"tool_calls={len(llm_response.tool_calls)}, "
                    f"content_len={len(llm_response.content)}"
                )

                # 打印 LLM 思考内容
                _print_thinking(llm_response.content)

                # ── 情况 1：无工具调用 → 模型认为探索完成 ──
                if not llm_response.has_tool_calls:
                    idle_rounds_without_tools += 1
                    messages.append({"role": "assistant", "content": llm_response.content})
                    explicit_completion = self._is_explicit_completion(llm_response.content)
                    sufficient_evidence = self._has_sufficient_exploration_evidence(
                        collector=collector,
                        pages_explored=pages_explored,
                        step_num=step_num,
                        effective_max_steps=effective_max_steps,
                    )
                    logger.info(
                        f"[UIExplore] [Step {step_num}] no_tool_call "
                        f"finish={llm_response.finish_reason} "
                        f"explicit_completion={explicit_completion} "
                        f"sufficient_evidence={sufficient_evidence} "
                        f"idle_rounds={idle_rounds_without_tools} "
                        f"pages_explored={len(pages_explored)} "
                        f"collector_pages={collector.page_count} "
                        f"collector_interactions={collector.interaction_count} "
                        f"content={(llm_response.content or '')[:300]}"
                    )
                    print(f"  {YELLOW}📝 模型未调用工具{RESET}")

                    # 如果采集器有数据，说明模型认为探索充分了
                    if collector.has_data and explicit_completion and sufficient_evidence:
                        logger.info(f"[UIExplore] [Step {step_num}] 模型完成探索，采集器已有 "
                                    f"{collector.page_count} 页 {collector.interaction_count} 交互项")
                        session.status = "completed"
                        break

                    # 采集器无数据 + 最后几步 → 提醒用 scan_page
                    if step_num >= effective_max_steps - 2:
                        messages.append({
                            "role": "user",
                            "content": "已接近步数上限。请立即对当前页面调用 scan_page + probe_interactions 收集数据。"
                        })
                    else:
                        messages.append({
                            "role": "user",
                            "content": "请使用 scan_page 工具扫描当前页面交互项，然后用 probe_interactions 探测效果。"
                        })
                    continue

                # ── 情况 2：有工具调用 → 执行并反馈 ──
                # 记录 assistant 消息（含 tool_calls 的 provider-agnostic 格式）
                assistant_msg = {
                    "role": "assistant",
                    "content": llm_response.content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in llm_response.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                # 逐个执行工具调用并生成 tool_result 消息
                idle_rounds_without_tools = 0
                total_tools = len(llm_response.tool_calls)
                for tool_idx, tc in enumerate(llm_response.tool_calls, 1):
                    if cancel_event and cancel_event.is_set():
                        session.status = "cancelled"
                        break

                    tool_name = tc.name
                    tool_args = tc.arguments
                    logger.info(f"[UIExplore] [Step {step_num}] 执行: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:200]})")
                    _print_action(tool_idx, total_tools, tool_name, tool_args)

                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(tools.execute, tool_name, tool_args),
                            timeout=TOOL_EXEC_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"[UIExplore] [Step {step_num}] 工具 {tool_name} 超时")
                        from UI_Exploration.tools import ToolResult as TR
                        result = TR(success=False, error='工具执行超时(60s)')
                    except Exception as e:
                        logger.warning(f"[UIExplore] [Step {step_num}] 工具 {tool_name} 失败: {e}")
                        from UI_Exploration.tools import ToolResult as TR
                        result = TR(success=False, error=str(e))

                    # 跟踪导航后的新页面
                    if tool_name in ('navigate', 'click') and getattr(result, 'success', False):
                        try:
                            pages_explored.add(session.driver.current_url)
                        except Exception:
                            pass

                    # ★ 采集器拦截：从 scan_page/probe_interactions 结果中提取结构化数据
                    collector.on_tool_result(tool_name, result)

                    # 构建 tool_result 消息（发送给 LLM 的版本，移除内部数据）
                    result_content = self._format_tool_result(result, tool_name)

                    messages.append({
                        "role": "tool_result",
                        "tool_call_id": tc.id,
                        "name": tool_name,
                        "content": result_content,
                    })

                    is_success = getattr(result, 'success', False)
                    result_short = getattr(result, 'output', getattr(result, 'error', ''))
                    if isinstance(result_short, dict):
                        result_short = json.dumps(result_short, ensure_ascii=False)[:120]
                    else:
                        result_short = str(result_short)[:120]
                    logger.info(f"[UIExplore] [Step {step_num}] {tool_name} → "
                               f"{'✅' if is_success else '❌'} "
                               f"({len(result_content)} chars)")
                    _print_result(tool_name, is_success, result_short)

                # 跟踪导航后的新页面
                try:
                    cur_url = session.driver.current_url
                    if cur_url not in pages_explored:
                        pages_explored.add(cur_url)
                        print(f"  {CYAN}🌐 新页面: {cur_url}{RESET}")
                except Exception:
                    pass

                # 进度回调
                if on_step_callback:
                    try:
                        await on_step_callback({
                            "step": step_num,
                            "total_steps": effective_max_steps,
                            "tools_called": [tc.name for tc in llm_response.tool_calls],
                            "session": session.to_dict(),
                        })
                    except Exception as cb_err:
                        logger.debug(f"[UIExplore] 进度回调异常: {cb_err}")

                logger.info(f"[UIExplore] [Step {step_num}/{effective_max_steps}] ✅ 完成")
                print(f"  {GREEN}── Step {step_num} 完成 (已探索 {len(pages_explored)} 个页面) ──{RESET}")

                # ── 步数即将耗尽时的自动续航检查 ──
                if (session.current_step >= effective_max_steps
                    and session.status == "exploring"
                    and effective_max_steps < MAX_TOTAL_STEPS):
                    should_extend = await self._should_extend_exploration(
                        messages, pages_explored, session
                    )
                    if should_extend:
                        old_max = effective_max_steps
                        effective_max_steps = min(effective_max_steps + AUTO_EXTEND_STEPS, MAX_TOTAL_STEPS)
                        session.max_steps = effective_max_steps
                        print(f"\n{BOLD}{YELLOW}🔄 自动续航: {old_max} → {effective_max_steps} 步 (探索未完成){RESET}")
                        messages.append({
                            "role": "user",
                            "content": (
                                f"已自动增加 {AUTO_EXTEND_STEPS} 步探索额度（总计 {effective_max_steps} 步）。"
                                f"已探索 {len(pages_explored)} 个页面，采集器已收集 {collector.page_count} 页数据。"
                                f"请继续对未探索的页面执行 scan_page + probe_interactions。"
                            )
                        })

            # ═══ 强制收尾：从采集器获取结构化结果 ═══
            if session.status == "exploring":
                session.status = "completed"

            if collector.has_data:
                # 程序化采集成功 → 直接使用采集器数据
                site_result = collector.build_result()
                session.result = site_result.to_legacy_format()
                # 将 site_result 存到 session 上供 service 层使用
                session._site_result = site_result
                logger.info(f"[UIExplore] 采集器结果: {collector.page_count} 页, "
                            f"{collector.interaction_count} 交互项")
            else:
                # 采集器无数据（LLM 没调用 scan_page）→ 降级为 LLM 提取
                logger.warning("[UIExplore] 采集器无数据，降级为 LLM 提取结果")
                result = await self._extract_final_result_fallback(messages, pages_explored)
                session.result = result

            logger.info(f"[UIExplore] ========== 探索结束 ({session.status}) ==========")
            _print_phase("探索结束", f"状态: {session.status}, 步骤: {session.current_step}, 耗时: {session.duration:.1f}s")
            if session.result:
                modules_count = len(session.result.get('modules', []))
                pages_count = collector.page_count
                print(f"  📊 采集 {pages_count} 个页面，{modules_count} 个模块")
            return session.to_dict()

        except Exception as e:
            logger.error(f"[UIExplore] 探索异常: {e}\n{traceback.format_exc()}")
            session.status = "failed"
            session.error = str(e)
            return session.to_dict()

        finally:
            session.end_time = time.time()
            # 自动关闭浏览器
            self._cleanup_browser(session)

    # ─── 浏览器清理 ───────────────────────────────────────────────

    @staticmethod
    def _is_explicit_completion(content: str) -> bool:
        """只在模型明确表达探索已完成时才允许收尾。"""
        text = (content or "").strip().lower()
        if not text:
            return False

        completion_markers = [
            "探索完成",
            "已完成探索",
            "探索已完成",
            "全部探索完成",
            "已经完成探索",
            "已充分探索",
            "所有可达页面",
            "exploration complete",
            "completed exploration",
            "all reachable pages",
        ]
        return any(marker in text for marker in completion_markers)

    @staticmethod
    def _has_sufficient_exploration_evidence(
        collector,
        pages_explored: set,
        step_num: int,
        effective_max_steps: int,
    ) -> bool:
        """避免模型在探索证据不足时过早结束。"""
        if step_num >= effective_max_steps - 1:
            return True
        if collector.page_count >= 2:
            return True
        if len(pages_explored) >= 3:
            return True
        if collector.interaction_count >= 8 and step_num >= 6:
            return True
        return False

    @staticmethod
    def _is_non_retryable_llm_error(error_text: str) -> bool:
        """配额/限流类错误不应继续按步骤重试探索。"""
        text = (error_text or "").lower()
        markers = [
            "quota",
            "rate limit",
            "rate_limit",
            "exceeded today's quota",
            "you have exceeded",
            "429",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _cleanup_browser(session):
        """探索结束后自动关闭浏览器"""
        try:
            if session.driver:
                session.driver.quit()
                session.driver = None
                logger.info(f"[UIExplore] 浏览器已关闭")
                print(f"  {GREEN}✅ 浏览器已关闭{RESET}")
        except Exception as e:
            logger.warning(f"[UIExplore] 关闭浏览器异常: {e}")
        # 同时清理 BrowserManager 注册
        try:
            from UI_Exploration.explorer import BrowserManager
            BrowserManager.cleanup(session.session_id)
        except Exception:
            pass

    # ─── 自动续航判断 ─────────────────────────────────────────────

    async def _should_extend_exploration(
        self,
        messages: List[dict],
        pages_explored: set,
        session,
    ) -> bool:
        """
        判断是否需要自动续航。

        条件：
          1. 已探索页面数少于 3（探索不充分）
          2. 最后几步还在积极探索（不是在反复失败）
          3. 未达到绝对上限
        """
        # 已探索足够多页面，或已到绝对上限
        if len(pages_explored) >= 6:
            return False

        # 检查最后 5 条消息中是否有成功的工具调用
        recent_success = 0
        recent_fail = 0
        for m in reversed(messages[-10:]):
            content = m.get("content", "")
            if isinstance(content, str):
                if '"success": true' in content or '"success":true' in content:
                    recent_success += 1
                elif '"success": false' in content or '"success":false' in content:
                    recent_fail += 1

        # 如果最近大多是失败，不续航（避免浪费）
        if recent_fail > recent_success + 2:
            logger.info(f"[UIExplore] 续航检查：最近失败 {recent_fail} > 成功 {recent_success}，不续航")
            return False

        logger.info(f"[UIExplore] 续航检查：已探索 {len(pages_explored)} 页面, 最近成功 {recent_success}, 失败 {recent_fail} → 继续")
        return True

    # ─── LLM 调用方法 ────────────────────────────────────────────

    async def _call_llm_with_retry(
        self,
        messages: List[dict],
        tool_definitions: List[dict],
        step_num: int,
        cancel_event: Optional[asyncio.Event] = None,
    ):
        """调用 LLM（native function calling），失败时重试"""
        # 消息安全检查：确保没有 orphan tool_result
        messages = self._sanitize_messages(messages)
        self._last_llm_error = ""
        self._last_llm_error_non_retryable = False

        for attempt in range(MAX_RETRIES + 1):
            if cancel_event and cancel_event.is_set():
                self._last_llm_error = "exploration cancelled"
                self._last_llm_error_non_retryable = True
                return None
            try:
                llm_response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.llm.chat_with_tools,
                        messages=messages,
                        tools=tool_definitions,
                        temperature=0.3,
                        max_tokens=4096,
                        source="ui_exploration",
                    ),
                    timeout=LLM_CALL_TIMEOUT,
                )
                return llm_response

            except asyncio.TimeoutError:
                self._last_llm_error = f"LLM timeout after {LLM_CALL_TIMEOUT}s"
                self._last_llm_error_non_retryable = False
                logger.error(f"[UIExplore] [Step {step_num}] LLM 超时 (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                if attempt < MAX_RETRIES and not self._last_llm_error_non_retryable:
                    await asyncio.sleep(2 ** attempt)  # 指数退避
                    continue

            except Exception as e:
                error_text = str(e)
                self._last_llm_error = error_text
                self._last_llm_error_non_retryable = self._is_non_retryable_llm_error(error_text)
                logger.error(f"[UIExplore] [Step {step_num}] LLM 异常: {e} (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                if attempt < MAX_RETRIES and not self._last_llm_error_non_retryable:
                    await asyncio.sleep(2 ** attempt)
                    continue

        return None  # 所有重试都失败

    # ─── 上下文管理 ──────────────────────────────────────────────

    @staticmethod
    def _compact_messages(messages: List[dict]) -> List[dict]:
        """
        压缩消息列表，避免上下文爆炸。

        关键约束：
          - tool_result 必须紧跟在包含对应 tool_call 的 assistant 消息之后
          - 裁剪时不能切断 assistant(tool_calls) + tool_result 配对
          - 消息序列必须保持 user/assistant 交替（Anthropic 要求）
        """
        if len(messages) <= MAX_CONTEXT_MESSAGES:
            return list(messages)

        # 找到安全切割点：不在 tool_call/tool_result 配对中间
        # 策略：从后往前找，保留最近 N 条消息，但确保不在配对中间切断
        keep_from = len(messages) - (MAX_CONTEXT_MESSAGES - 3)

        # 向后调整切割点，确保不切断 tool_call/tool_result 配对
        while keep_from < len(messages):
            msg = messages[keep_from]
            role = msg.get("role", "")
            # 如果切割点位于 tool_result 消息上，需要往前找到对应的 assistant(tool_calls)
            if role == "tool_result":
                keep_from -= 1
                continue
            # 如果切割点位于带 tool_calls 的 assistant 上，也要包含后面的 tool_result
            if role == "assistant" and "tool_calls" in msg and msg.get("tool_calls"):
                # 已经在开头了，保留
                break
            break

        # 确保 keep_from 不小于 2（保留 system + 初始 user）
        keep_from = max(2, keep_from)

        system_msgs = list(messages[:2])  # system + initial user
        recent_msgs = messages[keep_from:]
        trimmed_count = keep_from - 2

        if trimmed_count <= 0:
            return list(messages)

        # 构建压缩后的消息
        # system(0), user(1), assistant(summary), [recent_msgs...]
        summary_assistant = {
            "role": "assistant",
            "content": f"（已完成前 {trimmed_count} 条消息的探索。继续基于最近的工具结果深入探索。）"
        }

        compacted = system_msgs + [summary_assistant]

        # 确保 recent_msgs 第一条不是 assistant（会导致连续 assistant）
        if recent_msgs and recent_msgs[0].get("role") == "assistant":
            compacted.append({"role": "user", "content": "请继续探索。"})

        # 确保 recent_msgs 第一条不是 tool_result（必须先有 assistant）
        if recent_msgs and recent_msgs[0].get("role") == "tool_result":
            compacted.append({"role": "user", "content": "请继续探索。"})

        compacted.extend(recent_msgs)
        return compacted

    @staticmethod
    def _sanitize_messages(messages: List[dict]) -> List[dict]:
        """
        API 调用前最终安全检查：移除 orphan tool_result 消息。

        OpenAI 兼容 API (阿里通义等) 要求每个 tool 角色消息必须有
        对应的 assistant tool_calls 引用，否则报 "messages illegal"。
        """
        # 收集所有 assistant 消息中声明的 tool_call_id
        valid_tc_ids = set()
        for m in messages:
            if m.get("role") == "assistant" and "tool_calls" in m:
                for tc in (m.get("tool_calls") or []):
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id:
                        valid_tc_ids.add(tc_id)

        # 过滤掉 orphan tool_result
        sanitized = []
        for m in messages:
            if m.get("role") == "tool_result":
                tc_id = m.get("tool_call_id")
                if tc_id and tc_id not in valid_tc_ids:
                    logger.warning(f"[UIExplore] 移除 orphan tool_result: {tc_id}")
                    continue
            sanitized.append(m)
        return sanitized

    # ─── 工具结果格式化 ─────────────────────────────────────────

    @staticmethod
    def _format_tool_result(result, tool_name: str) -> str:
        """
        格式化工具执行结果，控制长度。

        对 scan_page/probe_interactions 的结果，优先使用 summary 字段
        （可读性好且 token 友好），移除 _page_map_dict 内部数据。
        """
        try:
            result_dict = result.to_dict() if hasattr(result, 'to_dict') else {
                "success": getattr(result, 'success', False),
                "output": getattr(result, 'output', None),
                "error": getattr(result, 'error', None),
            }
        except Exception:
            result_dict = {"success": False, "error": "无法序列化结果"}

        # 对高级工具，用 summary 替代完整输出（减少 token）
        if tool_name in ("scan_page", "probe_interactions"):
            output = result_dict.get("output", {})
            if isinstance(output, dict):
                # 移除内部数据，只保留 summary 和关键数字
                summary = output.get("summary", "")
                clean_output = {
                    "success": result_dict.get("success"),
                    "summary": summary,
                }
                if "interaction_count" in output:
                    clean_output["interaction_count"] = output["interaction_count"]
                if "probed_count" in output:
                    clean_output["probed_count"] = output["probed_count"]
                if "new_urls" in output:
                    clean_output["new_urls"] = output["new_urls"]
                return json.dumps(clean_output, ensure_ascii=False)

        result_str = json.dumps(result_dict, ensure_ascii=False, default=str)

        # 截断过长的结果
        if len(result_str) > MAX_TOOL_RESULT_CHARS:
            result_str = result_str[:MAX_TOOL_RESULT_CHARS] + "...(结果已截断)"

        return result_str

    # ─── 降级：LLM 提取结果（仅在采集器无数据时使用）────────────

    async def _extract_final_result_fallback(self, messages: List[dict], pages_explored: set = None) -> dict:
        """降级方案：从对话历史中用 LLM 提取结构化结果"""
        from UI_Exploration.prompts import EXTRACT_RESULT_PROMPT

        logger.info("[UIExplore] 使用 LLM 降级提取结果...")

        context_msgs = []
        total_chars = 0
        for msg in reversed(messages[-20:]):
            content_text = msg.get("content", "")
            if isinstance(content_text, list):
                content_text = json.dumps(content_text, ensure_ascii=False)
            if total_chars + len(str(content_text)) > 20000:
                break
            context_msgs.insert(0, msg)
            total_chars += len(str(content_text))

        clean_msgs = []
        for msg in context_msgs:
            role = msg.get("role", "user")
            if role == "tool_result":
                clean_msgs.append({
                    "role": "user",
                    "content": f"[工具 {msg.get('name', '')} 结果]: {msg.get('content', '')[:500]}"
                })
            elif role == "assistant" and "tool_calls" in msg:
                tc_names = [tc["name"] for tc in msg.get("tool_calls", [])]
                text = msg.get("content", "") or ""
                clean_msgs.append({
                    "role": "assistant",
                    "content": f"{text}\n[调用了工具: {', '.join(tc_names)}]" if text else f"[调用了工具: {', '.join(tc_names)}]"
                })
            elif role == "system":
                clean_msgs.append(msg)
            else:
                clean_msgs.append(msg)

        pages_text = ""
        if pages_explored:
            pages_text = f"\n\n已探索的页面 URL:\n" + "\n".join(f"- {u}" for u in sorted(pages_explored))

        extract_msgs = clean_msgs + [
            {"role": "user", "content": EXTRACT_RESULT_PROMPT + pages_text}
        ]

        try:
            raw_result = await asyncio.wait_for(
                asyncio.to_thread(
                    self.llm.chat,
                    messages=extract_msgs,
                    temperature=0.1,
                    max_tokens=6000,
                    source="ui_explore_extract",
                ),
                timeout=90,
            )

            cleaned = raw_result.strip()
            for prefix in ["```json", "```JSON", "```"]:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix):].strip()
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()

            result = json.loads(cleaned)
            logger.info(f"[UIExplore] LLM 降级提取成功: {len(result.get('modules', []))} 个模块")
            return result

        except Exception as e:
            logger.warning(f"[UIExplore] LLM 降级提取失败: {e}")
            return {
                "page_url": "", "page_title": "", "page_type": "unknown",
                "modules": [], "navigation": {}, "forms": [], "tables": [],
                "key_interactions": [], "pages_explored": list(pages_explored or []),
                "summary": "无法提取结构化数据",
            }

    @staticmethod
    def _count_result(result) -> int:
        """安全计算工具结果中的元素数量"""
        if not result or not getattr(result, 'output', None):
            return 0
        output = result.output
        if isinstance(output, dict):
            for key in ['total', 'count']:
                if key in output:
                    return output[key]
            for v in output.values():
                if isinstance(v, list):
                    return len(v)
        if isinstance(output, list):
            return len(output)
        return 0


# ═══════════════════════════════════════════════════════════════════
# 浏览器管理
# ═══════════════════════════════════════════════════════════════════

class BrowserManager:
    """浏览器实例管理器 — 负责 WebDriver 的创建、配置、复用和清理"""

    _drivers: Dict[str, Any] = {}

    @classmethod
    def create_driver(cls, headless: bool = True) -> Any:
        """创建 Selenium WebDriver 实例"""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service

            options = Options()
            if headless:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            options.add_argument("--lang=zh-CN")

            driver = webdriver.Chrome(options=options)
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    window.chrome = {runtime: {}};
                """
            })

            logger.info("[BrowserManager] WebDriver 创建成功")
            return driver

        except Exception as e:
            logger.error(f"[BrowserManager] WebDriver 创建失败: {e}")
            raise RuntimeError(f"无法创建浏览器实例: {e}")

    @classmethod
    def register(cls, session_id: str, driver) -> None:
        """注册 WebDriver 到会话"""
        cls._drivers[session_id] = driver
        logger.debug(f"[BrowserManager] 注册驱动: {session_id}")

    @classmethod
    def get(cls, session_id: str):
        """获取指定会话的 WebDriver"""
        return cls._drivers.get(session_id)

    @classmethod
    def cleanup(cls, session_id: str) -> None:
        """清理指定会话的 WebDriver"""
        driver = cls._drivers.pop(session_id, None)
        if driver:
            try:
                driver.quit()
                logger.info(f"[BrowserManager] 已关闭浏览器: {session_id}")
            except Exception as e:
                logger.warning(f"[BrowserManager] 关闭浏览器异常: {e}")

    @classmethod
    def cleanup_all(cls) -> None:
        """清理所有 WebDriver"""
        for sid in list(cls._drivers.keys()):
            cls.cleanup(sid)


# ═══════════════════════════════════════════════════════════════════
# 会话管理
# ═══════════════════════════════════════════════════════════════════

class ExploreSessionManager:
    """探索会话管理器（内存存储）"""

    _sessions: Dict[str, ExploreSession] = {}

    @classmethod
    def create(cls, request: ExploreRequest) -> ExploreSession:
        """创建新会话"""
        session_id = uuid.uuid4().hex[:16]

        headless = request.login_info.get("headless", False)
        logger.info(f"[ExploreSession] 创建浏览器实例 (headless={headless})")
        driver = BrowserManager.create_driver(headless=headless)
        BrowserManager.register(session_id, driver)

        session = ExploreSession(
            session_id=session_id,
            url=request.url,
            max_steps=request.max_steps,
        )
        session.driver = driver
        cls._sessions[session_id] = session
        logger.info(f"[ExploreSession] 创建会话: {session_id}, URL: {request.url}")
        return session

    @classmethod
    def get(cls, session_id: str) -> Optional[ExploreSession]:
        """获取会话"""
        return cls._sessions.get(session_id)

    @classmethod
    def remove(cls, session_id: str) -> None:
        """移除并清理会话"""
        BrowserManager.cleanup(session_id)
        cls._sessions.pop(session_id, None)
        logger.info(f"[ExploreSession] 移除会话: {session_id}")

    @classmethod
    def list_active(cls) -> List[Dict]:
        """列出所有活跃会话"""
        return [s.to_dict() for s in cls._sessions.values()]
