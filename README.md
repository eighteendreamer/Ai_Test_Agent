# 御策天检 - 智能自动化测试平台

<div align="center">
  <img src="agent_web_server/src/assets/logo.png" alt="AI Test Agent Logo" width="200"/>

  ![License](https://img.shields.io/badge/License-MIT-blue.svg)
  ![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)
  ![Node.js](https://img.shields.io/badge/Node.js-18%2B-green.svg)
  ![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688.svg)
  ![Vue](https://img.shields.io/badge/Vue-3.4.0-4FC08D.svg)
  ![Browser--Use](https://img.shields.io/badge/Browser--Use-0.11.1-FF6B35.svg)
  ![Qdrant](https://img.shields.io/badge/Qdrant-1.x-DC244C.svg)
</div>

## 项目简介

AI Test Agent 是一个基于人工智能的自动化测试平台，利用大语言模型（LLM）和浏览器自动化技术，实现测试用例的智能生成、自动执行、Bug 分析和报告生成。平台采用 **适配器模式（Adapter Pattern）** 重构了底层模型架构，支持 15+ 主流大模型供应商，并内置了智能止损、模糊匹配、Agent 判定优先、瞬态 UI 感知、用例间状态隔离等策略，大幅提升了测试的稳定性和效率。

平台还内置了 **页面知识库（RAG 记忆层）**，使用 Qdrant 向量数据库存储页面结构知识，在一键测试时优先命中已有知识跳过浏览器探索，并通过 Diff Engine 自动检测页面变更、推荐回归测试范围。

## 比赛视角下的差异化

如果只把 AI Test Agent 理解成“让大模型帮我测网页”，它确实会和 Claude Code + 测试 Skills 这类通用方案显得相似。

但本项目真正解决的问题不是“AI 能不能测”，而是“AI 测试能不能更低成本、更稳定、可复用、可持续地运行”。

- **知识复用**：页面探索结果进入知识库，后续同页面优先命中缓存，减少重复探索和上下文膨胀
- **版本感知**：页面结构变化时自动做 Hash 比对和 Diff 分析，辅助回归测试
- **受控生成**：模板 + LLM 混合生成用例，降低 Token 消耗并提升结构稳定性
- **稳定执行**：内置止损、循环检测、429 熔断、模型自动切换、状态隔离等运行保护机制
- **测试治理**：支持报告、Bug、邮件通知、Token 统计、多模型管理和项目平台集成
- **多平台集成**：统一适配器工厂接入 11 大项目管理平台（禅道、Jira、PingCode、TAPD、ONES、云效、ClickUp、Asana、Worktile、8Manage、MS Project），实现 Bug 推送和用例双向同步

适合比赛展示的定位不是“又一个通用测试 Agent”，而是“面向真实回归场景的 AI 测试执行内核”。

更完整的比赛答辩话术、演示主线和对比思路见：[比赛答辩定位与路演策略.md](比赛答辩定位与路演策略.md)

## 核心特性

### 1. 智能测试用例生成
- 基于自然语言需求自动生成测试用例
- 支持多种文件导入（TXT、PDF、DOCX、DOC）
- 自动生成结构化用例（模块、标题、步骤、预期结果、优先级）
- 智能覆盖正常、异常、边界及安全测试场景

### 2. 自动化测试执行与智能策略
- **基于 Browser-Use 0.11.1 的智能执行**：利用 CDP 协议和 DOM 分析进行精准操作
- **单量 / 批量执行模式**：
  - 单量执行：逐条执行并生成独立报告
  - 批量执行：多用例连续执行，生成统一汇总报告，支持暂停/恢复/停止控制
- **智能止损策略 (Stop Loss)**：
  - 连续 3 步操作无效自动熔断
  - 单用例超时控制，防止阻塞整体进度
- **模糊匹配验证 (Fuzzy Matching)**：
  - 支持语义级断言（如"账号或密码错误"与"密码错误"视为匹配），减少误报
- **Agent 判定优先**：
  - Agent 自身的 `done(success=True/False)` 判定优先于关键词匹配和 browser-use 内置 judge 的判定
  - `success` 语义为"实际结果是否符合预期结果"：异常场景测试（如错误密码登录）预期失败且确实失败 → `success: true`
  - 解决了旧版关键词匹配（搜索"失败"/"error"等词）导致异常场景用例被误判为 fail 的问题
- **瞬态 UI 感知**：
  - 针对 Toast/Message/Notification 等 1-3 秒自动消失的前端提示，Agent 被指导先 wait 再观察浏览器状态
  - 禁止使用 `extract`/`run_javascript` 搜索已消失的瞬态 DOM 元素
  - 通过 URL 变化、页面内容变化、表单是否仍在等间接证据判断操作结果
- **用例间状态隔离**：
  - 每条用例执行前通过 CDP 清除 Cookies + localStorage/sessionStorage
  - 自动导航回目标 URL，确保从干净状态开始
  - 解决了上一条用例登录态残留导致下一条用例结果不准确的问题

### 3. LLM 输出容错与 JSON 修复
- **Provider 感知的 JSON 解析**：每个 Provider 内置 `parse_json_response()` 方法，针对各模型输出特点进行专门处理
- **多层修复管线**：
  1. 剥离 `<think>` 推理标签、markdown 代码块、前后缀文字
  2. 括号匹配提取完整 JSON 对象（`_find_matching_brace`）
  3. 修复尾部逗号、缺少逗号（`"value"\n"key"` → `"value",\n"key"`）
  4. 截断 JSON 补全（未闭合括号自动补全）
  5. `json-repair` 库作为最终 fallback（所有 Provider 统一集成）
- **JSON 解析失败自动重试**：用例生成等关键流程在 JSON 解析失败时自动降低 temperature 重新请求 LLM
- **Action 别名映射**：`LLMWrapper` 内置 `DEFAULT_ACTION_ALIASES`（20+ 映射），自动将模型返回的非标准 action 名称转换为 browser-use 0.11.1 的标准名称（如 `evaluate` → `run_javascript`、`scroll_down` → `scroll`、`click_element` → `click`）
- **Provider 特定别名**：每个 Provider 的 `get_browser_use_llm()` 可传入额外的 provider-specific 别名映射

### 4. 接口测试（API Testing）
- **接口文件管理**：上传 Markdown 接口文档到 MinIO，自动解析提取 endpoints（method/path/summary/params/examples）
  - 多策略解析器：支持标题内联格式、中文 KV 格式（`- **路径**: /v1/xxx`）、Markdown 表格、全文正则扫描兜底
  - 卡片式文件管理界面，支持查看详情、原文预览、删除
- **智能接口匹配**：两段式匹配（关键词粗筛 + LLM 精排），根据测试用例文本自动推荐最合适的接口文件
- **三步式执行流程**：
  1. 选择测试用例（多选）
  2. AI 智能匹配接口文件，展示推荐与候选列表、接口预览，支持手动切换
  3. 配置环境（Base URL、Headers）后执行，LLM 生成可执行 DSL → HTTP Runner 发送请求 → 断言验证
- **全链路闭环**：执行结果写入 `test_records` → 自动生成 `test_reports` → 失败用例创建 `bug_reports` → 自动邮件通知 `auto_receive_bug=1` 联系人

### 5. 一键测试（OneClick Test）
- **统一环境上下文**：探索与用例生成统一消费标准化 `env_info`，包含 `target_url / login_url / username / password / extra_credentials / env_name`，避免重复拼装测试数据
- **Browser-Use 探索执行主体**：默认由 FastAPI + 大模型系统提示词驱动 Browser-Use Agent 完成页面探索与交互，不再依赖项目内自定义 Playwright 点击执行器
- **结构化探索产物**：探索阶段会记录页面快照与 DOM 摘要，提取 `forms / tables / buttons / links / page_sections / dialogs` 等结构化能力信息，用于后续子任务规划和用例生成
- **三级任务树 (TaskTree)**：
  - **L1**：用户意图层（整体测试目标）
  - **L2**：功能规划层（基于页面探索自动拆分的功能模块）
  - **L3**：原子执行层（可执行的单条测试用例）
  - 支持状态追踪（pending/confirmed/skipped/running/done/failed）、前端可视化渲染和 JSON 序列化
- **模板混合生成 (Template Integration)**：优先使用模板生成用例，模板不适用时回退到纯 LLM 生成，降低 Token 消耗并提升结构稳定性
- **全自主 AI 测试**：输入一句话任务（如"测试登录功能"），AI 全权执行：意图分析 → 自动获取环境 → 浏览器探索页面 → 生成子任务 → 生成用例 → 用户确认 → 执行测试
- **测试环境管理**：在「测试环境」中配置被测系统的 URL、登录账号密码，一键测试时自动从数据库获取，无需每次手动输入
- **智能环境解析**：优先使用用户指令中提供的 URL/凭据 → 数据库默认环境 → 环境变量兜底，三级降级策略
- **页面自主探索**：LLM 驱动浏览器自动导航到目标页面，收集所有可交互元素、表单字段、按钮、表格等页面结构信息
- **子任务自动规划**：基于页面探索结果，LLM 自动规划测试子任务，覆盖正常流程、异常场景、边界条件、安全测试
- **精准用例生成**：基于真实页面结构生成测试用例，步骤精确到具体按钮名称和输入框位置，而非纯文本猜测
- **对话式交互界面**：类 ChatGPT 的对话流 UI，实时展示分析进度、探索结果、子任务规划和执行结果
- **用户确认机制**：生成用例后展示给用户确认或编辑，确认后重新拉起浏览器执行
- **浏览器复用**：执行阶段所有用例共享一个 BrowserSession，避免每条用例都启动/关闭浏览器
- **用例间状态隔离**：每条用例执行前通过 CDP 清除 Cookies + localStorage/sessionStorage 并导航到目标 URL，防止登录态等状态在用例间泄漏
- **循环检测 (LoopDetector)**：实时监测 Agent 是否陷入重复操作，达到阈值自动熔断
- **模型自动切换 (FailoverChatModel)**：当主模型遇到 429 限流或连续失败时，自动切换到备用模型
- **真正的停止控制**：通过 asyncio.Event 取消机制 + 浏览器强制关闭，点击停止后立即生效
- **探索取消状态收口**：无论是一键测试还是页面知识库探索，被用户中止后都会进入明确的 `cancelled / 已取消` 终态，停止轮询并关闭前端 loading
- **429 限流智能熔断**：检测到 API 配额耗尽时立即停止后续用例
- **自动邮件通知**：测试完成后自动将测试报告 + Bug 报告整合为 HTML 邮件，发送给 `auto_receive_bug=1` 的联系人
- **Skills 知识注入**：执行时自动加载相关 Skills 作为"便签"注入 LLM 提示词
- **RAG 加速**：执行前优先查询页面知识库，命中缓存则跳过浏览器探索，节省 Token 消耗与等待时间

### 6. Skills 管理
- **知识增强系统**：Skills 是 Markdown 格式的程序化知识文件，为 AI Agent 提供测试领域的专业指导
- **MinIO 存储**：Skills 文件存储在 MinIO 对象存储中，数据库仅保存索引信息
- **多种安装方式**：
  - 从 GitHub 仓库下载安装（支持 `GITHUB_PROXY` 代理配置）
  - 手动上传 `.md` 文件安装（适用于无法访问 GitHub 的网络环境）
- **卡片式管理界面**：直观展示已安装 Skills，支持启用/禁用/删除/查看详情
- **便签式注入**：执行测试时，将相关 Skills 内容以"便签"形式注入到 LLM 系统提示词中

### 7. 多模型适配与管理
- **全平台支持**：内置适配器支持以下模型供应商：
  - **OpenAI** (GPT-4o, GPT-3.5)
  - **DeepSeek** (DeepSeek-V3, R1) — 含 `<think>` 推理标签处理、R1 专用 ChatOpenAI 实现
  - **Anthropic** (Claude 3.5 Sonnet/Opus)
  - **Google** (Gemini 1.5 Pro/Flash) — 含 Thinking 模型推理标签处理
  - **Azure OpenAI**
  - **Alibaba / ModelScope** (Qwen3-235B, Qwen3.5-397B 等通义系列) — 含结构化输出适配
  - **MiniMax** (abab6.5s, abab6.5 等) — 兼容 OpenAI API
  - **Ollama** (本地模型) — 含 DeepSeek R1 专用实现、`<think>` 标签处理
  - **Mistral AI**
  - **Moonshot** (Kimi)
  - **通用 OpenAI 兼容** — 覆盖硅基流动、魔搭社区、智谱 AI、Grok (xAI)、OpenRouter 等
- **供应商管理页面**：前端可视化管理模型供应商（CRUD），配置 API Key、Base URL、默认参数
- **Provider 感知的 JSON 解析**：每个 Provider 内置 `parse_json_response()` + `json-repair` 库 fallback
- **Alibaba/Qwen 结构化输出适配**：针对 Qwen3.5 等大参数模型自动启用 `dont_force_structured_output` + `add_schema_to_system_prompt`
- **LLMWrapper 统一包装**：拦截 `ainvoke`，处理消息格式转换、JSON 清洗（`_clean_llm_json_output`）、action 格式修正（`_fix_action_format`）、别名映射
- **智能调度**：基于数据库配置的优先级和激活状态自动选择最佳模型
- **模型自动切换**：内置 `ModelAutoSwitcher`，当模型遇到 429 限流、超时或连续失败时，自动切换到下一个可用模型
- **成本监控**：实时统计 Token 使用量和 API 成本，支持按模型、按来源查看使用日志

### 8. 智能 Bug 分析
- 自动分析失败原因，区分系统 Bug 与脚本错误
- 智能定级（一级致命至四级轻微）
- 自动提取复现步骤，生成带截图的 Bug 报告
- 关联测试用例，记录预期结果与实际结果

### 9. 报告与通知
- **运行测试报告**：详细执行日志、思维链（Thinking）、步骤截图
- **综合评估报告**：AI 驱动的多报告聚合分析，包含质量评级（A/B/C/D）、通过率、改进建议，Markdown 渲染展示
- **邮件推送**：集成阿里云 DirectMail、Resend、SMTP 自定义、CyberMail，支持自动发送格式化 HTML 邮件给联系人；采用工厂模式统一调度（`Email_manage/sender.py`），新增服务商只需注册一行，无需修改任何调用方
- **一键测试自动通知**：一键测试完成后自动将测试报告 + Bug 报告整合为一封 HTML 邮件，发送给 `auto_receive_bug=1` 的联系人
- **Bug 报告**：按严重程度、状态、错误类型多维度管理

### 11. 页面知识库（RAG 记忆层）
- **共享探索执行器**：页面知识库与一键测试共用同一条 Browser-Use 探索链路，统一由 `Exploration.browser_use_agent_explorer` 执行
- **结构化页面快照**：探索时会记录页面快照与 DOM 摘要，并沉淀 `forms / tables / buttons / links / page_sections / dialogs` 等字段，便于后续 RAG 检索与 Diff
- **向量存储**：使用 Qdrant 向量数据库（Docker 部署）存储页面结构的 Embedding，MySQL 同步保存索引记录
- **语义检索**：一键测试前先对目标 URL 做精确匹配，命中则跳过浏览器探索直接复用缓存知识，大幅节省 Token 消耗
- **相似度兜底**：精确匹配未命中时，使用 Embedding 做余弦相似度语义检索（阈值 0.82），再次尝试匹配同域名页面的历史知识
- **新鲜度管理**：4 小时内访问的知识直接复用；超过 30 天未更新的知识标记为"老化"并按需触发重新探索
- **Diff Engine（页面差异引擎）**：对比新旧页面结构，按变更类型分类（字段新增/删除/修改、按钮/表单/表格的增减），自动推荐需要补充的回归测试子任务
- **Collection 配置 UI**：
  - 可视化配置 Qdrant 连接参数（host、port、collection_name、向量维度、距离度量 Cosine/Dot/Euclid/Manhattan）
  - 可视化配置 Embedding 服务（model、API URL、API Key）
  - 配置持久化到 MySQL `qdrant_collection_config` 表，支持"初始化 Collection"和"强制重建"两种操作模式
- **统计面板**：展示总记录数（MySQL）、向量数（Qdrant）、老化记录数、Qdrant 健康状态，一键刷新
- **手动管理**：支持浏览知识列表、查看单条知识详情、手动录入、按 URL 删除
- **系统代理隔离**：在模块初始化时自动将 `localhost/127.0.0.1` 加入 `NO_PROXY`，防止 Windows 系统代理拦截 Python httpx 到本地 Qdrant 的请求

### 12. AI 驱动的自动化渗透测试（Pentest_Agent）✨ 新增
基于 PentAGI 架构的完整复刻，实现了多 Agent 协作的智能渗透测试系统，当前完成度 **85%**，核心功能已可用。

#### 核心架构
- **三层上下文传播**：FlowContext → TaskContext → SubtaskContext，确保测试上下文在整个执行链中无损传递
- **状态机管理**：Created → Running → Waiting/Finished/Failed，支持暂停、恢复、失败重试
- **Worker & Controller 模式**：异步任务调度，支持并发执行和资源隔离
- **Flow/Task/Subtask 三层结构**：
  - **Flow**：整个渗透测试流程（对应数据库 `security_scan_tasks` 表）
  - **Task**：具体测试任务（如 SQL 注入测试、XSS 检测）
  - **Subtask**：原子级测试步骤（如侦察、识别注入点、利用漏洞，对应 `pentest_subtasks` 表）

#### 13 种专业 Agent（100% 完成）
- **Primary Agent**：主协调器，决策执行流程，选择合适的 Agent 处理任务
- **Generator Agent**：任务分解，将测试目标智能拆分为 3-7 个有序子任务
- **Refiner Agent**：任务精炼，在每个子任务完成后动态调整剩余测试计划
- **Reporter Agent**：报告生成，自动生成专业渗透测试报告（Markdown/HTML/JSON）
- **Pentester Agent**：渗透测试执行，调用 Nmap、Nuclei、SQLMap 等安全工具进行漏洞评估
- **Coder Agent**：代码开发，编写和维护测试脚本（Python/Bash/PowerShell）
- **Searcher Agent**：信息搜索，收集漏洞情报和测试方法（DuckDuckGo/Google/Sploitus）
- **Reflector Agent**：错误纠正，自动修正 Agent 输出格式错误和工具调用失败
- **Installer Agent**：环境配置，自动安装和配置安全工具（apt/pip/git）
- **Adviser Agent**：专家建议，提供测试策略和最佳实践（基于 OWASP/PTES）
- **Memorist Agent**：记忆管理，长期记忆存储和检索（基于 Qdrant 向量数据库）
- **Enricher Agent**：信息增强，从多个来源增强测试上下文（CVE/CNVD/ExploitDB）
- **Assistant Agent**：交互助手，提供对话式测试支持和用户交互

#### Agent Chain 执行引擎（90% 完成）
- **100 次迭代循环**：自动调用工具完成复杂测试任务，支持长时间运行
- **执行监控（Execution Monitor）**：
  - 检测同一工具连续调用 5 次或总调用 10 次时触发 Mentor Agent 介入
  - 自动识别循环和低效执行模式
- **重复检测（Repeating Detector）**：
  - 软限制：重复 3 次发出警告
  - 硬限制：重复 7 次强制中止，防止无限循环
- **Reflector 纠正机制**：当 Agent 返回非结构化文本时自动纠正为标准 JSON 格式
- **Tool Call Fixer**：工具参数错误时自动修复并重试（最多 3 次）
- **Caller Reflector**：LLM 调用失败时自动恢复，支持模型切换
- **链摘要（Chain Summarization）**：防止上下文窗口溢出，保留最近消息和关键 QA

#### 20+ 专业工具（70% 完成）
- **环境工具**：
  - `terminal`：命令执行（支持 Docker 容器内执行）
  - `file`：文件读写（支持容器内外文件复制）
- **搜索工具**：
  - `browser`：网页抓取（基于 requests/httpx）
  - `duckduckgo`：DuckDuckGo 搜索（已实现）
  - `google`、`tavily`、`perplexity`：需要 API Key 配置
  - `sploitus`：Exploit 情报搜索（待实现）
- **向量工具**（基于 Qdrant）：
  - `search_in_memory`：语义检索历史测试结果
  - `store_guide` / `search_guide`：存储和检索测试指南
  - `store_answer` / `search_answer`：存储和检索问答知识
  - `store_code` / `search_code`：存储和检索测试脚本
- **Agent 委托工具**：
  - `pentester`：委托 Pentester Agent 执行渗透测试
  - `searcher`：委托 Searcher Agent 搜索信息
  - `coder`：委托 Coder Agent 编写代码
  - `installer`：委托 Installer Agent 安装工具
  - `adviser`：委托 Adviser Agent 提供建议
  - `memorist`：委托 Memorist Agent 管理记忆
- **屏障工具**：
  - `done`：标记任务完成
  - `ask`：请求用户输入（Flow 进入 Waiting 状态）
- **结果存储工具**：
  - `hack_result`、`search_result`、`code_result` 等 18 种工具结果自动存储到向量数据库

#### Docker 容器隔离（100% 完成）
- **默认镜像**：`vxcontrol/kali-linux`（Kali Linux 全工具镜像，预装 Nmap、Nuclei、SQLMap、Metasploit 等）
- **容器生命周期管理**：
  - 自动创建容器（挂载工作目录、配置网络、设置能力）
  - 自动启动和停止
  - 自动清理（测试完成后删除容器）
- **端口动态分配**：避免端口冲突
- **网络隔离**：每个测试任务在独立网络命名空间中执行
- **能力限制**：授予 `NET_RAW`、`NET_ADMIN` 能力（用于网络扫描）
- **容器内命令执行**：支持 `docker exec` 执行命令
- **文件复制**：支持宿主机与容器间双向文件复制

#### 向量存储和长期记忆（100% 完成）
- **复用现有 Qdrant 实现**：集成 `Page_Knowledge/vector_store.py` 和 `Page_Knowledge/embedding.py`
- **从数据库读取配置**：从 `qdrant_collection_config` 表读取 Qdrant 连接参数和 Embedding 配置
- **项目级别数据隔离**：
  - 所有向量存储操作强制要求 `project_id`
  - 使用 `project_id + doc_type + content_hash` 生成确定性 point_id
  - 避免跨项目数据污染和重复存储
- **18 种工具结果自动存储**：
  - 工具执行结果自动向量化并存储到 Qdrant
  - 支持按 `doc_type` 过滤（如 `guide`、`answer`、`code`、`hack_result`）
- **语义检索**：
  - 余弦相似度搜索（阈值可配置，默认 0.2）
  - 支持项目过滤和文档类型过滤
  - 返回 Top-K 结果（默认 3 条）

#### 智能任务规划
- **Generator Agent**：自动将测试目标分解为 3-7 个有序子任务
- **Refiner Agent**：在每个子任务完成后动态调整剩余计划
- **最多支持 15 个 Subtasks**：防止任务过度细分

#### 实时日志系统（100% 完成）
- **MsgLogWorker**：记录 Agent 消息链（存储到 `pentest_message_chains` 表）
- **AgentLogWorker**：记录 Agent 调用日志（Agent 类型、输入、输出、耗时）
- **SearchLogWorker**：记录搜索日志（搜索关键词、来源、结果数）
- **TermLogWorker**：记录终端命令执行日志（命令、返回码、输出）
- **VectorStoreLogWorker**：记录向量存储操作日志（存储/检索操作、文档类型、向量 ID）
- **ScreenshotWorker**：管理截图文件（自动保存到 `../save_floder/pentest/screenshots/`）

#### REST API 接口（100% 完成）
```python
# 创建 Flow
POST /api/pentest/flows
{
    "user_id": 1,
    "project_id": 1,
    "input": "测试 example.com 的 SQL 注入漏洞",
    "provider_name": "openai",
    "provider_config": {
        "model": "gpt-4",
        "temperature": 0.7
    }
}

# 查询 Flow 状态
GET /api/pentest/flows/{flow_id}

# 提交用户输入（当 Subtask 调用 ask 工具时）
POST /api/pentest/flows/{flow_id}/input
{
    "input": "继续执行"
}

# 停止 Flow
POST /api/pentest/flows/{flow_id}/stop

# 完成 Flow
POST /api/pentest/flows/{flow_id}/finish
```

#### 配置管理
所有配置从环境变量加载（`.env` 文件），支持精细化配置：
- **Docker 配置**：`PENTEST_USE_DOCKER`、`PENTEST_DOCKER_IMAGE`、`PENTEST_DOCKER_WORKDIR`
- **执行配置**：`PENTEST_MAX_ITERATIONS`（默认 100）、`PENTEST_MAX_REFLECTOR_ROUNDS`（默认 3）
- **工具配置**：每个工具的超时、输出限制、SSL 验证等
- **安全工具路径**：Nmap、Nuclei、SQLMap、XSStrike、FFUF、Gobuster、Metasploit、Hydra、Amass 等

#### 待完善功能（15%）
- **Mentor Agent 介入机制**：当前仅检测，需完善介入逻辑
- **搜索工具 API 配置**：Google、Tavily、Perplexity、Sploitus 需要 API Key
- **知识图谱集成**：Neo4j + Graphiti（可选功能）
- **实时推送**：WebSocket/SSE 实时推送测试进度（可选）
- **完整链摘要算法**：ChainAST + 分段摘要 + QA 保留（当前是简化版）

#### 使用示例
```bash
# 1. 确保 Docker 服务运行
docker ps

# 2. 确保 Qdrant 服务运行
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant

# 3. 配置环境变量（.env 文件）
PENTEST_USE_DOCKER=true
PENTEST_DOCKER_IMAGE=vxcontrol/kali-linux
PENTEST_MAX_ITERATIONS=100

# 4. 启动后端服务
cd Agent_Server
python src.py

# 5. 创建渗透测试 Flow（通过 API 或前端界面）
curl -X POST http://localhost:8001/api/pentest/flows \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": 1,
    "project_id": 1,
    "input": "全面测试 https://example.com 的安全漏洞",
    "provider_name": "openai"
  }'

# 6. 查询执行状态
curl http://localhost:8001/api/pentest/flows/{flow_id}
```

#### 技术亮点
- **完整复刻 PentAGI**：严格遵循 PentAGI 的架构设计和执行流程
- **项目级别隔离**：向量存储、Docker 容器、日志系统全部支持项目隔离
- **确定性存储**：使用 `project_id + doc_type + content_hash` 生成 point_id，避免重复存储
- **复用现有模块**：最大化复用 LLM、Email、Report、Qdrant、Embedding 等现有实现
- **生产级可靠性**：错误恢复、重试机制、超时控制、资源清理、日志追踪

### 13. 多平台项目管理集成（Project Integration）
- **统一适配器工厂**：采用 Factory Pattern 统一接入以下 **11 个** 项目管理平台：
  - **禅道 (Zentao)** — 支持 v1/v2 双版本 API 自动探测，兼容禅道 15.x ~ 21.x
  - **Jira** — 支持 Jira Cloud & Server，REST API v2/v3
  - **PingCode** — 国产研发管理平台
  - **Worktile** — 企业协作与项目管理
  - **ONES** — 研发管理一体化平台
  - **云效 (Yunxiao)** — 阿里云 DevOps 平台
  - **TAPD** — 腾讯敏捷产品研发平台
  - **8Manage PM** — 企业项目管理系统
  - **Microsoft Project** — 微软项目管理
  - **Asana** — 全球化项目管理工具
  - **ClickUp** — 一体化生产力平台
- **平台总控制台**：前端 `PlatformControl.vue` 提供统一视角管理所有已配置平台，支持激活/停用/测试连接
- **每平台三组件**：每个平台均提供 Config（配置管理）、Cases（用例导入）、Bugs（Bug 推送与同步）三个视图
- **Bug 自动推送**：一键将本地 Bug 报告推送到目标平台，自动映射标题、复现步骤、严重程度、影响模块
- **用例双向导入**：
  - 三种导入模式：按产品一键导入全部用例、按测试套件、按用例 ID 精确导入
  - **并发拉取**：使用 `asyncio.Semaphore` 控制并发数（默认 5，上限 20），大批量用例数十秒完成
  - **自动去重**：导入前查询本地已有标题，跳过重复；同批次内也防止重复写入
  - **全量字段提取**：逐条调用单用例详情接口，保证 `steps`/`precondition`/`expected`/`keywords` 字段完整写入
- **用例模板同步**：从平台同步用例字段结构，统一映射到系统内部格式（module/title/precondition/steps/expected/keywords/priority/case_type/stage）
- **远程项目同步**：可从已连接平台拉取远程项目列表，同步到本地进行管理

### 14. 数据可视化仪表盘
仪表盘提供全局视角的测试数据可视化，包括测试趋势、Bug 分布、用例覆盖、安全扫描统计、漏洞严重程度分布、安全用例状态等多维度图表，采用三列自适应布局。

### 15. UI 自主探索引擎（UI Exploration）
- **独立探索模块**：基于 LLM 原生函数调用（Function Calling）+ Selenium WebDriver 的自主页面探索引擎
- **16 个浏览器工具**：导航、点击、输入、截图、读取页面、查找元素等，支持 2-5 个工具并行执行
- **三阶段探索流程**：
  1. **Bootstrap**：自动导航到目标 URL 并登录
  2. **Agent Loop**：LLM 根据页面状态自主决策下一步操作（最多 40 轮对话）
  3. **Result Extraction**：从探索记录中提取结构化页面知识
- **智能上下文管理**：消息截断、工具结果预算控制、MAX_CONTEXT_MESSAGES = 40，防止上下文膨胀
- **视觉高亮**：Browser-Use 风格的元素脉冲动画，直观展示 Agent 正在操作的目标
- **错误自恢复**：操作失败时自动重试并使用精确 XPath 选择器
- **知识库自动沉淀**：探索完成后可选自动存储到页面知识库（Qdrant），供后续 RAG 检索

## 技术架构

### 后端技术栈
- **框架**: FastAPI 0.115.0 + Uvicorn
- **架构模式**: Adapter Pattern (LLM Layer), Factory Pattern
- **数据库**: MySQL + SQLAlchemy 2.0.25
- **核心引擎**: Browser-Use 0.11.1（统一浏览器执行主体）+ FastAPI / LLM 编排
- **LLM 集成**: 支持 OpenAI, Anthropic, Google GenAI, DeepSeek, Alibaba, MiniMax, Ollama 等多协议
- **LLM 容错**: json-repair 库 + 多层 JSON 修复管线 + LLMWrapper 统一包装
- **向量数据库**: Qdrant（页面知识库 RAG 记忆层，Docker 部署）
- **对象存储**: MinIO（接口文件存储与版本管理）
- **邮件服务**: 工厂模式统一调度（`Email_manage/sender.py`），内置 Resend / 阿里云 DirectMail (HMAC-SHA1) / SMTP 自定义 (STARTTLS) / CyberMail，扩展新服务商无需修改调用方
- **其他**: PyPDF2, Pandas, python-docx, Markdown, requests
- **安全扫描**: Nuclei、SQLMap、XSStrike、内置 Fuzz 引擎

### 前端技术栈
- **框架**: Vue 3.4.0 + Naive UI 2.38.0
- **状态管理**: Pinia 2.1.7
- **构建工具**: Vite 5.0.0
- **样式**: TailwindCSS + SCSS
- **图表**: ECharts
- **Markdown 渲染**: marked

## 项目结构

```
Ai_Test_Agent/
├── Agent_Server/                # 后端服务
│   ├── app.py                  # FastAPI 入口
│   ├── Basic/                  # 核心应用配置与启动
│   │   ├── config.py           # 环境变量配置加载
│   │   ├── endpoints.py        # 健康检查、API 根端点
│   │   ├── routes.py           # 集中路由注册（19 个路由器）
│   │   └── startup.py          # 生命周期管理、启动横幅
│   ├── llm/                    # LLM 核心模块
│   │   ├── base.py             # Provider 基类 + 通用 JSON 解析 + _find_matching_brace
│   │   ├── factory.py          # Provider 工厂
│   │   ├── client.py           # LLM 客户端（兼容层）
│   │   ├── manager.py          # 模型配置管理
│   │   ├── config.py           # 默认配置 + Provider 特性
│   │   ├── auto_switch.py      # 模型自动切换 + FailoverChatModel
│   │   ├── wrapper.py          # LLMWrapper（消息转换/JSON清洗/action别名映射/格式修正）
│   │   ├── examples.py         # 示例配置
│   │   ├── exceptions.py       # 异常定义
│   │   └── providers/          # 具体 Provider 实现（每个含 parse_json_response + json-repair）
│   │       ├── openai_provider.py
│   │       ├── anthropic_provider.py
│   │       ├── google_provider.py
│   │       ├── deepseek_provider.py    # 含 DeepSeekR1ChatOpenAI
│   │       ├── alibaba_provider.py     # 含 Qwen 结构化输出适配
│   │       ├── minimax_provider.py
│   │       ├── ollama_provider.py      # 含 DeepSeekR1ChatOllama
│   │       ├── mistral_provider.py
│   │       ├── moonshot_provider.py
│   │       ├── azure_provider.py
│   │       └── generic_provider.py     # 通用 OpenAI 兼容（含 SiliconFlow/ModelScope/Zhipu/Grok 子类）
│   ├── Pentest_Agent/           # AI 驱动的自动化渗透测试（基于 PentAGI 架构）
│   │   ├── router.py           # REST API 路由（创建/查询/输入/停止 Flow）
│   │   ├── config.py           # 配置管理（从环境变量加载）
│   │   ├── IMPLEMENTATION_STATUS.md  # 实现状态文档（85% 完成）
│   │   ├── core/               # 核心模块
│   │   │   ├── flow.py         # Flow 管理（整个渗透测试流程）
│   │   │   ├── task.py         # Task 管理（具体测试任务）
│   │   │   ├── subtask.py      # Subtask 管理（原子级测试步骤）
│   │   │   ├── context.py      # 三层上下文传播（FlowContext → TaskContext → SubtaskContext）
│   │   │   ├── status.py       # 状态机定义（Created/Running/Waiting/Finished/Failed）
│   │   │   ├── performer.py    # Agent Chain 执行引擎（100 次迭代/监控/重复检测/纠正）
│   │   │   ├── docker.py       # Docker 容器管理（创建/启动/停止/exec/copy）
│   │   │   ├── vector_store.py # 向量存储管理（复用 Qdrant，项目级别隔离）
│   │   │   ├── knowledge_graph.py  # 知识图谱（Neo4j，可选）
│   │   │   └── logs/           # 日志 Worker（6 个）
│   │   │       ├── msg_log.py      # 消息日志
│   │   │       ├── agent_log.py    # Agent 调用日志
│   │   │       ├── search_log.py   # 搜索日志
│   │   │       ├── term_log.py     # 终端日志
│   │   │       ├── vector_store_log.py  # 向量存储日志
│   │   │       └── screenshot.py   # 截图管理
│   │   ├── agents/             # 13 种专业 Agent
│   │   │   ├── factory.py      # AgentFactory（统一创建）
│   │   │   ├── types.py        # Agent 类型定义
│   │   │   ├── primary.py      # Primary Agent（主协调器）
│   │   │   ├── generator.py    # Generator Agent（任务分解）
│   │   │   ├── refiner.py      # Refiner Agent（任务精炼）
│   │   │   ├── reporter.py     # Reporter Agent（报告生成）
│   │   │   ├── pentester.py    # Pentester Agent（渗透测试执行）
│   │   │   ├── coder.py        # Coder Agent（代码开发）
│   │   │   ├── searcher.py     # Searcher Agent（信息搜索）
│   │   │   ├── reflector.py    # Reflector Agent（错误纠正）
│   │   │   ├── installer.py    # Installer Agent（环境配置）
│   │   │   ├── adviser.py      # Adviser Agent（专家建议）
│   │   │   ├── memorist.py     # Memorist Agent（记忆管理）
│   │   │   ├── enricher.py     # Enricher Agent（信息增强）
│   │   │   └── assistant.py    # Assistant Agent（交互助手）
│   │   └── tools/              # 20+ 专业工具
│   │       ├── executor.py     # 工具执行器（统一调度）
│   │       ├── terminal.py     # 终端命令执行
│   │       ├── file.py         # 文件读写
│   │       ├── browser.py      # 网页抓取
│   │       ├── search.py       # 搜索工具（DuckDuckGo/Google/Tavily/Perplexity/Sploitus）
│   │       ├── vector.py       # 向量工具（search_in_memory/store_guide/search_answer/store_code）
│   │       ├── agent_tools.py  # Agent 委托工具（pentester/searcher/coder/installer/adviser/memorist）
│   │       └── barrier.py      # 屏障工具（done/ask）
│   ├── Api_Spec/               # 接口文件管理（MinIO + 解析器）
│   │   ├── router.py           # 上传/列表/详情/删除 API
│   │   ├── parser.py           # Markdown 多策略解析器
│   │   └── minio_client.py     # MinIO 客户端封装
│   ├── Api_Test/               # 接口测试模块
│   │   ├── router.py           # 智能匹配 + 执行 API
│   │   └── service.py          # 匹配/DSL生成/HTTP Runner/报告/Bug/邮件
│   ├── OneClick_Test/          # 一键测试模块
│   │   ├── router.py           # 一键测试 + Skills 管理 API
│   │   ├── service.py          # 核心服务（意图分析/页面探索/用例生成/浏览器执行/状态隔离/停止控制/429熔断/自动邮件）
│   │   ├── session.py          # 会话状态机管理
│   │   ├── loop_detection.py   # 循环检测器（防止 Agent 无限循环）
│   │   ├── skill_manager.py    # Skills 管理（MinIO存储/GitHub下载/手动上传/便签注入）
│   │   ├── task_tree.py        # 三级任务树（L1 意图 / L2 功能规划 / L3 原子用例）
│   │   └── template_integration.py  # 模板 + LLM 混合用例生成
│   ├── UI_Exploration/         # UI 自主探索引擎
│   │   ├── router.py           # 探索页面 / 探索状态 / 停止探索 API
│   │   ├── service.py          # 探索生命周期编排（启动/状态/取消/清理）
│   │   ├── explorer.py         # v2 Agent 引擎（原生函数调用 + Selenium WebDriver）
│   │   ├── tools.py            # 16 个浏览器自动化工具（支持并行执行）
│   │   └── prompts.py          # Agent 系统提示词与探索策略
│   ├── Page_Knowledge/         # 页面知识库（RAG 记忆层）
│   │   ├── router.py           # 知识库 CRUD + 统计 + Collection 配置 API
│   │   ├── service.py          # 核心服务（精确匹配/语义检索/存储/版本检查/老化管理）
│   │   ├── vector_store.py     # Qdrant 向量存储封装（连接/ensure_collection/upsert/search/reload_config）
│   │   ├── embedding.py        # Embedding 客户端（支持多模型，含 reload_embedding_client）
│   │   ├── diff_engine.py      # 页面差异引擎（字段/按钮/表单/表格变更分类 + 回归推荐）
│   │   └── schema.py           # PageKnowledge 数据结构定义
│   ├── Api_request/            # 提示词模板（集中管理所有 LLM 提示词，含瞬态UI处理规则）
│   ├── Project_manage/         # 多平台项目管理集成
│   │   ├── router.py           # 平台配置管理 API
│   │   ├── service.py          # 配置管理与客户端实例化
│   │   ├── project_router.py   # 项目 CRUD 与远程同步 API
│   │   ├── clients/            # 平台客户端工厂
│   │   │   └── factory.py      # Factory Pattern 统一创建平台客户端
│   │   ├── case_template/      # 用例模板同步
│   │   │   ├── router.py       # 用例模板 API
│   │   │   └── service.py      # 平台字段映射与模板同步
│   │   └── platforms/          # 11 个平台适配器（每个含 client/models/service/router）
│   │       ├── zentao/         # 禅道（v1/v2 API 自适应）
│   │       ├── jira/           # Jira（Cloud & Server）
│   │       ├── pingcode/       # PingCode
│   │       ├── worktile/       # Worktile
│   │       ├── ones/           # ONES
│   │       ├── yunxiao/        # 云效
│   │       ├── tapd/           # TAPD
│   │       ├── eightmanage/    # 8Manage PM
│   │       ├── msproject/      # Microsoft Project
│   │       ├── asana/          # Asana
│   │       └── clickup/        # ClickUp
│   ├── Bug_Analysis/           # Bug 分析服务
│   ├── Build_Report/           # 报告生成服务
│   ├── Build_Use_case/         # 用例生成服务
│   ├── Execute_test/           # 浏览器测试执行服务
│   ├── Dashboard/              # 仪表盘数据
│   ├── Contact_manage/         # 联系人管理
│   ├── Email_manage/           # 邮件管理
│   │   ├── router.py           # 邮件配置 CRUD + 发送记录 API
│   │   └── sender.py           # 邮件发送工厂（dispatch_send + _PROVIDER_MAP，新增服务商只改此文件）
│   ├── Model_manage/           # 模型配置管理 + 供应商管理
│   ├── Test_Tools/             # 任务管理器
│   ├── database/               # 数据库模型与连接
│   └── save_floder/            # 截图等文件存储
├── agent_web_server/           # 前端服务
│   ├── src/
│   │   ├── api/                # API 接口定义
│   │   ├── views/
│   │   │   ├── dashboard/      # 数据可视化仪表盘
│   │   │   ├── test/           # 测试执行
│   │   │   │   ├── FuncTest.vue      # 功能测试（单量/批量 Browser-Use）
│   │   │   │   ├── ApiTest.vue       # 接口测试（三步式：选用例→匹配→执行）
│   │   │   │   ├── OneClickTest.vue  # 一键测试（对话式 AI 全自动测试）
│   │   │   │   ├── PressTest.vue     # 性能测试
│   │   │   │   ├── SecurityTest.vue  # 安全测试
│   │   │   │   └── KnowledgeBase.vue # 页面知识库管理（RAG 记忆层 + Collection 配置）
│   │   │   ├── skills/         # Skills 管理
│   │   │   │   └── SkillManage.vue   # Skills 管理（卡片式 + 上传安装）
│   │   │   ├── case/           # 用例管理与生成
│   │   │   │   ├── CaseGenerate.vue  # 用例生成
│   │   │   │   ├── CaseManage.vue    # 用例管理
│   │   │   │   ├── CaseTemplate.vue  # 用例模板配置（平台字段映射）
│   │   │   │   ├── ProjectManage.vue # 项目管理
│   │   │   │   └── ApiSpecManage.vue # 接口文件管理（卡片式）
│   │   │   ├── report/         # 报告管理
│   │   │   │   ├── RunReport.vue     # 运行测试报告
│   │   │   │   ├── BugReport.vue     # Bug 报告
│   │   │   │   └── MixedReport.vue   # 综合评估报告
│   │   │   ├── model/          # 模型配置
│   │   │   │   ├── ModelManage.vue   # 模型管理
│   │   │   │   └── ProviderManage.vue # 供应商管理
│   │   │   ├── mail/           # 邮件发送与配置
│   │   │   │   ├── SendMail.vue      # 邮件发送
│   │   │   │   ├── EmailConfig.vue   # 邮件配置（Resend / 阿里云 / SMTP 自定义 / CyberMail）
│   │   │   │   ├── ContactManage.vue # 联系人管理
│   │   │   │   └── Contacts.vue      # 联系人列表
│   │   │   ├── prompt/         # 提示词管理
│   │   │   │   └── PromptList.vue
│   │   │   └── project/        # 多平台项目管理集成
│   │   │       ├── PlatformControl.vue    # 平台总控制台
│   │   │       ├── zentao/     # 禅道（Config/Cases/Bugs）
│   │   │       ├── jira/       # Jira（Cases/Bugs）
│   │   │       ├── pingcode/   # PingCode（Config/Cases/Bugs）
│   │   │       ├── worktile/   # Worktile（Config/Cases/Bugs）
│   │   │       ├── ones/       # ONES（Config/Cases/Bugs）
│   │   │       ├── yunxiao/    # 云效（Config/Cases/Bugs）
│   │   │       ├── tapd/       # TAPD（Config/Cases/Bugs）
│   │   │       ├── eightmanage/ # 8Manage（Config/Cases/Bugs）
│   │   │       ├── msproject/  # MS Project（Config/Cases/Bugs）
│   │   │       ├── asana/      # Asana（Config/Cases/Bugs）
│   │   │       └── clickup/    # ClickUp（Config/Cases/Bugs）
│   │   ├── components/         # 公共组件
│   │   │   ├── ProjectSelector.vue   # 项目选择器
│   │   │   └── project/        # 项目管理公共组件
│   │   ├── composables/        # 组合式函数
│   │   ├── utils/              # 工具函数
│   │   │   └── stepsUtils.js   # 测试步骤格式处理（parseSteps/stepsToEditArray/editArrayToJson）
│   │   ├── router/             # 路由配置
│   │   ├── layouts/            # 布局组件
│   │   └── styles/             # 全局样式
│   └── package.json
└── README.md
```

## 快速开始

### 环境要求
- Python 3.11+
- Node.js 18+
- MySQL 8.0+
- MinIO（接口文件存储）
- Qdrant（向量数据库，Docker 部署）
- Chrome / Edge 浏览器

### 后端部署

1. **安装依赖**
```bash
cd Agent_Server
pip install -r requirements.txt
playwright install chromium
```

2. **配置环境变量** (`.env`)
```env
# 数据库
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=ai_test_agent

# 服务端口
PORT=8001

# 浏览器配置
HEADLESS=false

# MinIO 对象存储
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=aitest
MINIO_REGION=cn-beijing-1
MINIO_SECURE=false

# GitHub 代理（一键测试 Skills 下载，可选）
GITHUB_PROXY=https://ghproxy.com/

# Qdrant 向量数据库（页面知识库 RAG）
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION=page_knowledge

# Embedding 服务（页面知识库向量化，可选，也可在页面知识库 Collection 配置界面配置）
EMBEDDING_MODEL=text-embedding-ada-002
EMBEDDING_API_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=your_embedding_key
```

3. **启动 Qdrant**（页面知识库依赖，可选）
```bash
docker run -d --name Qdrant_Ai_Test_Agent \
  -p 6333:6333 -p 6334:6334 \
  -v qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

4. **初始化数据库**
```bash
python -c "from database.connection import init_db; init_db()"
```

5. **启动服务**
```bash
python src.py
```

### 前端部署

```bash
cd agent_web_server
npm install
npm run dev
```

前端默认运行在 `http://localhost:5175`，通过 Vite 代理转发 API 请求到后端 `http://localhost:8001`。

## 使用指南

### 1. 模型配置
进入"模型管理"页面，添加模型时选择对应的 Provider（如 `openai`、`deepseek`、`alibaba`、`minimax`）。系统会根据 `is_active=1` 且优先级最高的规则自动调用对应模型。也可在"供应商管理"页面管理模型供应商的 API Key、Base URL 等配置。

### 2. 功能测试（Browser-Use）
- **单量执行**：在"功能测试"页面选择单条用例执行，Agent 自动启动浏览器完成操作并生成报告。
- **批量执行**：勾选多条用例批量执行，系统生成统一汇总报告，支持实时暂停/恢复/停止。
- 若遇到验证失败，Agent 会自动尝试回溯操作或进行模糊匹配，无需人工干预。

### 3. 接口测试（API Testing）
- **上传接口文件**：在"测试资源模块 → 接口文件管理"页面上传 Markdown 接口文档，系统自动解析并存储到 MinIO。
- **执行接口测试**：在"测试模块 → 接口测试"页面，三步完成：
  1. 勾选测试用例
  2. AI 自动匹配最合适的接口文件，展示推荐结果和接口预览
  3. 配置目标环境（Base URL、Headers），选择执行模式（LLM 增强 / 冒烟测试），点击执行
- 执行完成后自动生成测试报告，失败用例自动创建 Bug 并邮件通知相关联系人。

### 4. 一键测试（OneClick Test）
- 一键测试现在会先把环境标准化为 `target_url / login_url / username / password / extra_credentials / env_name`，再把这份上下文同时提供给探索与用例生成链路
- 页面探索默认由 Browser-Use Agent 执行；FastAPI 服务负责提供系统提示词、环境数据和任务状态编排
- 首先在"一键测试"页面点击「测试环境」按钮，配置被测系统的 URL 和登录账号密码
- 输入自然语言指令，如"帮我测试登录功能"或"全面测试用户管理模块"
- AI 自动分析意图，从数据库获取测试环境（URL + 凭据），拉起浏览器探索目标页面
- 探索完成后，AI 基于真实页面结构规划子任务并生成精准测试用例
- 浏览器关闭，用例展示在右侧面板，可勾选/编辑/调整优先级
- 确认后点击"确认执行"，系统重新拉起浏览器逐条执行
- 每条用例执行前自动清除 Cookies + localStorage/sessionStorage 并导航到目标页面，确保用例间状态隔离
- 执行过程中可随时点击"停止"按钮，系统会立即终止当前任务并关闭浏览器
- 如遇 API 配额耗尽（429），系统自动停止后续用例并尝试切换备用模型
- 测试完成后自动生成测试报告和 Bug 报告，并将整合邮件发送给自动接收 Bug 的联系人

### 5. Skills 管理
- 在"Skills 管理"页面通过手动上传 `.md` 文件安装 Skills（推荐，无需网络）
- 也可通过 GitHub slug（如 `anthropics/webapp-testing`）在线安装（需配置 `GITHUB_PROXY`）
- 安装后的 Skills 会在一键测试执行时自动注入到 AI Agent 的提示词中，增强测试能力
- 支持启用/禁用/删除/查看详情

### 7. 查看结果
- **运行测试报告**：查看详细的执行日志、AI 思维链和步骤详情，状态根据通过/失败数量自动判定。
- **Bug 报告**：测试失败时自动生成 Bug 单，包含关联用例、复现步骤、预期/实际结果和修复建议。
- **综合评估报告**：选择多份运行报告，AI 自动聚合分析生成质量评级和改进建议。

### 8. 邮件通知
- 在"邮件配置"中选择服务商并填写对应凭据：
  - **Resend**：填写 API Key
  - **阿里云**：填写 Access Key ID + Secret
  - **SMTP 自定义**：填写 SMTP 服务器地址、端口、用户名、密码（STARTTLS）
  - **CyberMail**：填写 SMTP 用户名和密码，服务器固定为 `mail.cyberpersons.com:587`
- 可设置"测试模式"，开启后所有邮件强制发送到测试邮箱而非真实收件人
- 综合评估报告可一键发送给指定联系人
- 一键测试完成后自动将测试报告 + Bug 报告整合发送给 `auto_receive_bug=1` 的联系人
- 接口测试失败时自动发送 Bug 通知邮件给自动接收联系人
- **扩展新服务商**：在 `Email_manage/sender.py` 中实现 `_send_via_xxx(config, to_email, subject, html_body)` 函数，并在 `_PROVIDER_MAP` 注册一行，所有发送入口自动支持，无需改动其他代码

### 9. 页面知识库（RAG 记忆层）
- 点击"探索页面"时，系统会复用与一键测试一致的 Browser-Use 探索执行链，并将 `extra_credentials` 一并提供给探索提示词
- 如需中止探索，可点击停止；任务会进入"已取消"终态，后端浏览器会被关闭，前端轮询与 loading 也会同步结束
- 在侧边栏进入"页面知识库"页面，查看统计面板（总记录数 / 向量数 / 老化数 / Qdrant 健康状态）
- 点击"Collection 配置"按钮，配置 Qdrant 连接和 Embedding 服务参数，点击"保存配置"持久化到数据库
- 点击"初始化 Collection"使配置生效；如需从零重建则点击"强制重建"（会删除旧 Collection 后重新创建）
- 知识库列表支持按 URL / 域名搜索、查看知识详情、手动录入新知识、按 URL 删除
- **注意**（Windows 用户）：若使用系统代理，需确保 `localhost` / `127.0.0.1` 在代理排除列表中；框架已在模块初始化时自动注入 `NO_PROXY=localhost,127.0.0.1,::1`

### 10. 数据看板
仪表盘提供全局视角的测试数据可视化，包括测试趋势、Bug 分布、用例覆盖、安全扫描统计、漏洞严重程度分布、安全用例状态等多维度图表，采用三列自适应布局。

### 11. 多平台项目管理集成
- 在侧边栏进入「项目集成」模块，首先进入**平台总控制台**查看所有支持的平台
- **配置平台**：选择目标平台（禅道 / Jira / PingCode / TAPD / ONES / 云效 / Worktile / 8Manage / MS Project / Asana / ClickUp），填写 Base URL、API Token 等凭据，可点击"测试连接"验证
- **Bug 管理**：
  - 列表展示本地所有 Bug 报告（服务端分页），可按状态/严重程度筛选
  - 点击「推送到平台」将 Bug 单推送至已配置的项目管理平台
  - 推送失败时界面会显示平台返回的具体错误原因
- **用例导入**：
  - 从已连接平台拉取远程项目和用例，导入方式支持按产品 / 按套件 / 按 ID 精确导入
  - **最多导入**：设置 limit 数量（0 = 不限制），适合先小批验证
  - **并发数**：控制同时拉取的详情请求数（默认 5），网络好可调高
  - 重复导入会自动跳过已存在的同名用例
- **用例模板**：在「用例模板」页面查看和配置各平台的字段映射关系

### 10. 多平台项目管理集成
- 在侧边栏进入「项目集成」模块，首先进入**平台总控制台**查看所有支持的平台
- **配置平台**：选择目标平台（禅道 / Jira / PingCode / TAPD / ONES / 云效 / Worktile / 8Manage / MS Project / Asana / ClickUp），填写 Base URL、API Token 等凭据，可点击"测试连接"验证
- **Bug 管理**：
  - 列表展示本地所有 Bug 报告（服务端分页），可按状态/严重程度筛选
  - 点击「推送到平台」将 Bug 单推送至已配置的项目管理平台
  - 推送失败时界面会显示平台返回的具体错误原因
- **用例导入**：
  - 从已连接平台拉取远程项目和用例，导入方式支持按产品 / 按套件 / 按 ID 精确导入
  - **最多导入**：设置 limit 数量（0 = 不限制），适合先小批验证
  - **并发数**：控制同时拉取的详情请求数（默认 5），网络好可调高
  - 重复导入会自动跳过已存在的同名用例
- **用例模板**：在「用例模板」页面查看和配置各平台的字段映射关系

### 11. AI 驱动的自动化渗透测试（Pentest_Agent）
- **前置条件**：
  - 确保 Docker 服务运行（`docker ps` 检查）
  - 确保 Qdrant 服务运行（`docker run -d --name qdrant -p 6333:6333 qdrant/qdrant`）
  - 配置环境变量（`.env` 文件）：`PENTEST_USE_DOCKER=true`、`PENTEST_DOCKER_IMAGE=vxcontrol/kali-linux`
- **创建渗透测试 Flow**：
  - 通过 API 或前端界面创建 Flow，输入测试目标（如"全面测试 https://example.com 的安全漏洞"）
  - 系统自动分配 Primary Agent 作为主协调器
- **自动任务分解**：
  - Generator Agent 自动将测试目标分解为 3-7 个有序子任务（如侦察、漏洞扫描、利用测试、报告生成）
  - Refiner Agent 在每个子任务完成后动态调整剩余计划
- **Agent 协作执行**：
  - Primary Agent 根据任务类型选择合适的 Agent（Pentester/Searcher/Coder/Installer/Adviser/Memorist）
  - 每个 Agent 可调用 20+ 专业工具（terminal/file/browser/search/vector/agent_tools/barrier）
- **用例模板**：在「用例模板」页面查看和配置各平台的字段映射关系

### 12. AI 驱动的自动化渗透测试（Pentest_Agent）unning/Waiting/Finished/Failed）
  - 查看当前执行的 Subtask、已完成的任务、发现的漏洞
- **用户交互**：
  - 当 Subtask 调用 `ask` 工具时，Flow 进入 Waiting 状态
  - 用户通过 API 提交输入后，Flow 自动恢复执行
- **停止和完成**：
  - 可随时停止 Flow，系统自动清理 Docker 容器和资源
  - 测试完成后，Reporter Agent 自动生成专业渗透测试报告（Markdown/HTML/JSON）

## 许可证

MIT License

---

**AI Test Agent** - 让自动化测试更智能、更高效！
