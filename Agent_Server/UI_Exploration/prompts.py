"""
UI 探索提示词体系（v3 — 统一引擎，参考 Claude Code 架构）

设计原则（来自 Claude Code queryLoop）：
  - LLM 驱动决策：所有探索路径由 LLM 选择工具决定
  - 工具确定性执行：每个工具是纯粹的确定性操作
  - 结果程序化收集：scan_page/probe_interactions 的结果由系统自动采集，无需 LLM 输出 JSON
  - 精准反馈循环：工具结果（含错误）完整返回 LLM，支持自纠正

工具分层：
  基础工具: navigate, screenshot, read_page, find, click, type, scroll, hover,
            evaluate, get_links, get_forms, get_buttons, get_inputs, select_dropdown, wait
  高级工具: scan_page (一键扫描), probe_interactions (批量探测), auto_login (程序化登录)
"""

# ═══════════════════════════════════════════════════════════════════
# 系统提示词 — 定义 Agent 行为规范
# ═══════════════════════════════════════════════════════════════════

EXPLORER_SYSTEM_PROMPT = """你是一个 Web UI 自动化探索专家 Agent。你通过浏览器工具系统地探索网页的结构、交互元素和功能模块。

## 核心原则

你的职责是**全面记录**一个 Web 应用的 UI 结构和交互能力。系统会自动从你调用的工具结果中采集结构化数据，**你不需要手动输出 JSON**，只需要通过工具充分探索即可。

## 工具体系

### 高级工具（优先使用）
- **scan_page**: 一键扫描当前页面的全部交互项（按钮、链接、菜单、表单、表格）。进入任何新页面后必须首先调用。
- **probe_interactions**: 对 scan_page 发现的交互项逐项探测效果（自动点击 → 记录变化 → 恢复状态）。用于发现导航关系和交互行为。
- **auto_login**: 程序化自动登录。一步完成用户名输入、密码输入、点击登录按钮。

### 基础工具
- navigate: 导航到指定 URL
- screenshot: 截取页面截图
- read_page: 读取页面文本和 DOM 结构
- find: 通过选择器查找元素
- click: 点击元素
- type: 输入文字
- scroll: 滚动页面
- hover: 悬停元素
- evaluate: 执行 JavaScript
- get_links/get_buttons/get_forms/get_inputs: 获取特定类型元素
- select_dropdown: 选择下拉选项
- wait: 等待页面或元素

## ⚡ 探索策略（标准流程）

### 每个页面的标准操作

1. **scan_page** — 进入新页面后立即调用，获取完整的交互项列表（内置空白页自动刷新）
2. **probe_interactions** — 对 scan_page 发现的交互项探测效果
   - 可以指定 indices 只探测部分项（如导航类元素）
   - 系统自动跳过危险操作（删除、提交等）
3. **处理发现的新页面** — probe_interactions 会报告导航到的新 URL
   - 用 navigate 进入新页面
   - 如果页面内容为空，调用 **ensure_page_loaded** 自动刷新
   - 重复步骤 1-2

### 覆盖率追踪

- 每次 probe_interactions 返回 new_urls 时，记住这些未探索的页面
- 确保探索完当前页面后，逐个访问并探索这些新页面
- 如果有侧边导航菜单，逐一点击每个菜单项并对新页面执行 scan_page + probe_interactions
- 目标是覆盖所有可达页面

### 登录流程

如果提供了登录信息，第一步使用 **auto_login** 工具完成登录。登录后调用 read_page 验证登录是否成功。

## 行为约束

### 效率原则
- 一个回合可以同时调用多个不冲突的工具（如 scan_page + screenshot 并行）
- 不要在同一类信息上反复查找
- 不要频繁 screenshot，scan_page 已包含全部信息

### 操作验证
- click/navigate 后调用 read_page 验证效果（或依赖工具返回的 current_url/current_title）
- 进入错误页面时立即 navigate 回正确页面

### 严格禁止
- 触发 JavaScript alert/confirm/prompt
- 提交会修改数据的表单（如删除、付款）
- 同一失败操作重复超过 2 次
- 探索与任务无关的外部页面

### 完成条件
当你认为已充分探索所有可达页面和交互项时，直接用文字说明探索已完成即可。系统会自动整理你通过 scan_page 和 probe_interactions 收集的所有数据。

优先探索用户指定的 focus_areas。"""


# ═══════════════════════════════════════════════════════════════════
# 结果提取提示词 — 仅在采集器无数据时使用的降级方案
# ═══════════════════════════════════════════════════════════════════

EXTRACT_RESULT_PROMPT = """请根据以上对话中的所有工具调用结果，提取页面的结构化能力数据。

输出格式（纯 JSON，不要 markdown 代码块）：
{
  "page_url": "页面URL",
  "page_title": "页面标题",
  "page_type": "页面类型",
  "modules": [{"module_name": "模块名", "description": "描述", "elements": []}],
  "navigation": {},
  "forms": [],
  "tables": [],
  "key_interactions": [],
  "pages_explored": [],
  "summary": "一句话总结"
}"""


# ═══════════════════════════════════════════════════════════════════
# 用户提示词模板 — 根据请求参数动态生成
# ═══════════════════════════════════════════════════════════════════

def build_exploration_user_prompt(
    url: str,
    user_instruction: str = "",
    focus_areas: list[str] = None,
    max_steps: int = 30,
    login_info: dict = None,
) -> str:
    """构建探索任务的用户提示词"""
    parts = [f"## 探索任务\n\n请对以下目标页面进行系统性 UI 探索："]
    parts.append(f"\n**目标 URL**: {url}")

    if login_info and login_info.get("username"):
        parts.append(f"""
**登录信息** — 请先调用 auto_login 工具完成登录:
- 登录页: {login_info.get('url', url)}
- 用户名: {login_info.get('username', '')}
- 密码: {login_info.get('password', '')}
""")

    if focus_areas:
        areas_text = "\n".join(f"- {area}" for area in focus_areas)
        parts.append(f"\n**重点关注区域**:\n{areas_text}")

    if user_instruction:
        parts.append(f"\n**额外指令**:\n{user_instruction}")

    parts.append(f"""
**探索参数**:
- 最大步数: {max_steps}

**标准流程**:
1. 有登录信息时先用 auto_login 登录，然后 read_page 验证
2. 对每个页面依次执行 scan_page → probe_interactions
3. 通过 probe_interactions 发现新页面后，navigate 过去继续探索
4. 确保覆盖所有可达页面后，说明探索完成即可

系统会自动从 scan_page 和 probe_interactions 的结果中采集结构化数据，你不需要手动输出 JSON。
""")

    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 工具描述 — 供 LLM 选择工具的 Schema
# ═══════════════════════════════════════════════════════════════════

TOOL_DEFINITIONS = [
    # ── 高级工具（优先使用）──
    {
        "name": "scan_page",
        "description": "【推荐】一键扫描当前页面的全部交互项。等效于同时执行 get_buttons + get_links + get_forms + tab/menu检测 + 表格检测。进入新页面后必须首先调用。返回完整的交互项列表（含索引号，供 probe_interactions 使用）。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "probe_interactions",
        "description": "【推荐】对当前页面的交互项逐项探测效果。自动执行「点击 → 记录变化 → 恢复状态」，返回每个交互项的效果类型（navigate/modal/content_change/none）和发现的新页面 URL。自动跳过危险操作。",
        "parameters": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "要探测的交互项索引列表（来自 scan_page 返回的 [n] 索引）。不传则探测所有非危险项。",
                },
                "max_count": {
                    "type": "integer",
                    "description": "最大探测数量，默认 15",
                },
            },
        },
    },
    {
        "name": "auto_login",
        "description": "【推荐】程序化自动登录。自动查找用户名/密码输入框和登录按钮，一步完成登录。比手动 type + click 更高效可靠。",
        "parameters": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "登录账号"},
                "password": {"type": "string", "description": "登录密码"},
                "login_url": {"type": "string", "description": "登录页 URL（可选，不传则在当前页登录）"},
            },
            "required": ["username", "password"],
        },
    },
    {
        "name": "ensure_page_loaded",
        "description": "【推荐】检测当前页面是否有实际内容，如果页面空白则自动刷新（最多重试2次）。导航到新页面后如果发现内容为空，应立即调用此工具而不是反复 read_page。",
        "parameters": {"type": "object", "properties": {}},
    },
    # ── 基础工具 ──
    {
        "name": "navigate",
        "description": "导航到指定 URL。打开新页面或刷新当前页面。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要导航到的完整 URL"},
                "timeout": {"type": "integer", "description": "页面加载超时时间（秒），默认 30"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "screenshot",
        "description": "截取当前页面的屏幕截图。",
        "parameters": {
            "type": "object",
            "properties": {
                "full_page": {"type": "boolean", "description": "是否截取整页，默认 false"},
                "filename": {"type": "string", "description": "截图文件名（可选）"},
            },
        },
    },
    {
        "name": "read_page",
        "description": "读取页面完整信息：标题、URL、可见文本、DOM 结构摘要。",
        "parameters": {
            "type": "object",
            "properties": {
                "include_dom": {"type": "boolean", "description": "是否包含 DOM 结构，默认 true"},
                "max_length": {"type": "integer", "description": "最大返回字符数，默认 15000"},
            },
        },
    },
    {
        "name": "find",
        "description": "通过 CSS/XPath/文本查找页面元素。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询条件"},
                "by": {"type": "string", "enum": ["css", "xpath", "text"], "description": "查询方式，默认 css"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "click",
        "description": "点击页面元素。支持 CSS 选择器和 XPath（以 // 开头自动识别）。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器或 XPath"},
                "coordinate": {"type": "array", "items": {"type": "number"}, "description": "[x, y] 坐标点击"},
                "wait_after": {"type": "boolean", "description": "点击后是否等待，默认 true"},
            },
        },
    },
    {
        "name": "type",
        "description": "在输入框中输入文字。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "输入框 CSS/XPath"},
                "text": {"type": "string", "description": "要输入的文字"},
                "clear_first": {"type": "boolean", "description": "是否先清空，默认 true"},
                "submit": {"type": "boolean", "description": "输入后是否回车提交，默认 false"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "scroll",
        "description": "滚动页面。",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["down", "up", "left", "right"], "description": "方向，默认 down"},
                "amount": {"type": "integer", "description": "像素数，默认 500"},
                "selector": {"type": "string", "description": "指定区域内滚动（可选）"},
            },
        },
    },
    {
        "name": "hover",
        "description": "悬停元素，常用于触发下拉菜单、tooltip。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "目标元素 CSS/XPath"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "evaluate",
        "description": "执行 JavaScript 代码。",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "JS 代码"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "get_links",
        "description": "获取页面所有超链接。",
        "parameters": {
            "type": "object",
            "properties": {
                "filter_external": {"type": "boolean", "description": "是否过滤外部链接，默认 false"},
            },
        },
    },
    {
        "name": "get_forms",
        "description": "获取页面所有表单及字段信息。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_buttons",
        "description": "获取页面所有按钮元素。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_inputs",
        "description": "获取页面所有输入框元素。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "select_dropdown",
        "description": "选择下拉选项。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "<select> 元素选择器"},
                "value": {"type": "string", "description": "option value 或文本"},
                "by_text": {"type": "boolean", "description": "是否通过文本匹配，默认 false"},
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "wait",
        "description": "等待页面加载或特定元素出现。",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "等待秒数，默认 2"},
                "selector": {"type": "string", "description": "等待元素出现的选择器（可选）"},
            },
        },
    },
]
