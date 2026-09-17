# Enterprise AI QA Agent（御策天检）

`Enterprise_AI_QA_Agent` 是一个面向企业级质量保障场景的多 Agent 编排工作台，目标是参考 `claude_code_ui_Agent` 的运行骨架，构建一个可扩展的：

- 多 Agent 编排系统（Coordinator / Worker 子代理调度）
- 可观测的前后端工作台（会话、事件流、审批、回放）
- 覆盖 API / 性能 / 安全 / 兼容性 / UI 等多方向的 AI 测试平台

当前项目分为两个核心子工程：

- `Agent_Server`：基于 `FastAPI + LangGraph` 的后端运行时与编排服务
- `agent_web`：基于 `Vue 3 + Vite + Naive UI` 的前端工作台（支持 Electron 桌面端打包）

## 项目结构

```text
Enterprise_AI_QA_Agent/
├─ Agent_Server/          # FastAPI + LangGraph 后端
│  ├─ src/
│  │  ├─ main.py          # 应用入口：lifespan 启动流程 + 路由注册
│  │  ├─ api/routes/      # 21 组 REST/SSE 路由（前缀 /api/v1）
│  │  ├─ application/     # 应用服务层（按职责拆分为 36 个子包）
│  │  ├─ modes/           # 14 种测试模式（8 种实装 + 6 种占位）
│  │  ├─ SKILLS/          # 54 个可注册技能包
│  │  ├─ registry/        # Agent / Tool / Model / Skill / MCP / Mode 注册中心
│  │  ├─ core/            # Settings：.env 加载、端口/存储/表名等全部配置
│  │  ├─ contracts/       # 跨层契约定义
│  │  ├─ graph/           # LangGraph 编排链路
│  │  ├─ runtime/         # 运行时状态、事件、快照
│  │  ├─ infrastructure/  # Postgres / MySQL / Memgraph / Redis / RustFS 适配
│  │  ├─ domain/          # 领域模型
│  │  └─ schemas/         # Pydantic 契约
│  ├─ tests/              # pytest 单元与集成测试
│  └─ docs/               # 性能 / 安全测试模式设计文档
├─ agent_web/              # Vue 3 + Vite 前端工作台
│  ├─ src/views/           # 9 个页面：工作台 / 任务池 / 项目 / 知识 / 工具 / 报告 / 设置 / 编排轨迹 / 录制窗口
│  ├─ src/features/        # 插件化功能面板：workbench / flow / recorder / settings / tools
│  ├─ electron/            # Electron 桌面端入口
│  └─ docs/                # VitePress 文档站
├─ databases/              # MySQL 与 PostgreSQL 初始化脚本
├─ docs/                   # 工程规范、复刻文档与开发计划
└─ README.md
```

## 核心文档

开始开发前，建议先阅读以下两份文档：

- [Claude_Code_UI_Agent_全流程复刻规范.md](./docs/Claude_Code_UI_Agent_全流程复刻规范.md)
- [HARNESS_ENGINEERING_开发规范.md](./docs/HARNESS_ENGINEERING_开发规范.md)

这两份文档定义了本项目的核心工程方向：

- 不是只做聊天页面，而是做完整的 Agent 运行骨架
- 前端必须具备运行时可观测性
- 后端必须具备会话、事件、快照、审批、调度与恢复能力
- 所有 Agent / Tool / Runtime 扩展都应走统一协议

## 当前能力概览

### 后端

- 会话创建、读取、消息发送、无头执行（headless execute）
- Agent / Tool / Model / Skill / MCP / Mode 六大注册中心
- LangGraph 编排链路，支持 interrupt / resume 审批中断与恢复
- SSE 事件流输出与事件历史回放（replay）
- Coordinator / Worker 子代理调度
- 工具作业（tool jobs）、产物（artifacts）管理与 RustFS 对象存储
- MCP 连接管理与工具桥接（stdio 命令白名单）
- 向量记忆（pgvector）与 Memgraph 知识图谱 / UI 图谱
- 模型多 Provider 适配（OpenAI / Anthropic / Gemini 等）与 OAuth 授权
- 邮件能力（agently-mail，含腾讯邮箱认证监控）与渠道配对网关（`qq` / `feishu` / `weixin`）
- Docker 容器管理（Redis / RustFS / MySQL / Postgres / Memgraph / 测试引擎）
- **UI 录制链路**：三种浏览器驱动（embedded / cdp-attach / playwright-managed）+ 共用注入脚本 `recorder.js`，事件流幂等落 PostgreSQL、固化成 Memgraph 子图，再经七级定位链回放、转用例草稿、生成置信度分级断言
- **用例驱动治理**：项目（projects）→ 用例草稿/评审/固定版本（test_cases）→ 套件冻结（test_suites）→ 运行条目原子领取与结果入库（test_runs）
- 上传安全扫描（RustFS temp / safe / quarantine 三桶）与安全漏洞台账（`agent_security_bugs`，含复现包与重测）
- 赞助商只读接口（MySQL `system_sponsor_config` → `GET /api/v1/sponsors`）

### 前端

- 会话工作台首页：流式消息渲染、运行状态指示、审批面板、Runtime Event Console
- 任务池、项目、报告中心、知识图谱、工具中心页面
- 编排轨迹（Flow）视图：按会话事件动态生成轨迹节点，支持长文本折叠与下钻
- UI 录制窗口（`RecorderWindowView` + Electron `recorder-window.mjs`）：控制条四按钮状态机、实时时间线
- 插件化设置中心：模型、邮箱、渠道、Docker、存储、平台等
- 顶栏赞助商轮播入口 + 列表弹窗
- 多语言（15 种 locale）
- Electron 桌面端打包（Windows，产物为「御策天检.exe」）
- VitePress 文档站（`npm run dev` 时随应用一起启动）

## 测试模式（modes）

`Agent_Server/src/modes` 下按模式拆分编排实现，`registry/modes.py` 共注册 **14 种**：**8 种已实装**（有专用 Runner 与完整链路），**6 种为占位模式**（manifest 标 `"placeholder": True`，只注册 Skills、`harness_key=placeholder_testing_harness`、`activation_policy=explicit_only`，**没有专用执行链路**）。

### 已实装（8）

| 模式 | key | 说明 |
| --- | --- | --- |
| `api_testing_mode` | `api_testing` | 最完整链路：API 文档解析 → 端点圈定 → 依赖规划 → 前置条件解析 → 执行 → 验证/评估 → 报告，含子代理协调与任务池 |
| `performance_testing_mode` | `performance_testing` | 需求接入 → 负载建模 → k6 / JMeter 引擎执行 → 结果解析 → 失败分析 → 报告，含目标保护（allowlist、VU/RPS/时长上限、smoke 前置） |
| `security_testing_mode` | `security_testing` | 基于 Docker（Kali）运行器的安全测试编排，含攻击链循环与漏洞落库（`agent_security_bugs`） |
| `ui_automation_mode` | `ui_automation` | UI 自动化（Playwright CLI Runtime），并挂接 UI 录制编排（三源检索→审批→录制）与回放执行器 |
| `smoke_testing_mode` | `smoke_testing` | 冒烟测试（方案目录 / 版本 / 运行历史 / 回归候选四表） |
| `compatibility_testing_mode` | `compatibility_testing` | 产品探查 → 环境矩阵 → 用例生成 → 审批策略 → 运行器分发 → 结果聚合 → 报告 |
| `code_review_mode` | `code_review` | 多角色"辩论"式代码评审：源码获取 → 团队组建 → 编排辩论 → 评估 → 验证（含治理规则） |
| `default_mode` | `default` | 默认对话/兜底模式，非测试类问题也能应答 |

### 占位（6）—— 只注册 Skills，尚无专用 Runner

| 模式 | key | 覆盖方向 |
| --- | --- | --- |
| `integration_testing_mode` | `integration_testing` | 集成与依赖测试（服务契约、真实依赖、网络模拟、数据访问） |
| `unit_component_testing_mode` | `unit_component_testing` | 单元 / 组件测试 |
| `mobile_testing_mode` | `mobile_testing` | 移动端测试 |
| `visual_regression_testing_mode` | `visual_regression_testing` | 视觉回归 |
| `accessibility_testing_mode` | `accessibility_testing` | 无障碍测试 |
| `reliability_testing_mode` | `reliability_testing` | 可靠性与韧性（混沌、故障注入方向） |

## UI Explorer Agent 架构

UI 方向已从「UI 测试执行器」收敛为「页面结构理解引擎」：

```mermaid
flowchart TD
    A["Frontend Workbench"] --> B["Project Scope"]
    B --> C["Session Runtime"]
    C --> D["Input Orchestrator"]
    D --> E["Agent / Tool Registry"]
    E --> F["UI Explorer Agent"]
    F --> G["Playwright CLI Runtime"]
    G --> H["ARIA Snapshot"]
    H --> I["Context Tree Builder"]
    I --> J["Semantic Extractor"]
    J --> K["UI Graph Builder"]
    K --> L["Memgraph Project-Scoped UI Graph"]
    K --> M["Artifact: ui_explorer_graph.json"]
    K --> N["Page Knowledge / Observations"]
```

核心约束：

- 主数据源是 Playwright `aria_snapshot()`，不是 DOM 扁平扫描。
- `ui-page-explorer` 只探索和建模，输出 `pages / elements / entities / edges`。
- 不走 `Verification Harness` 和 `Evaluation Harness`，不生成测试用例、不做断言、不判定通过失败。
- Memgraph 图谱节点主要包括 `Page`、`Element`、`Entity`。
- Memgraph 图谱关系主要包括 `CONTAINS`、`BELONGS_TO`、`TRIGGERS_NAVIGATION`、`REVEALS`。

登录与交互探索：

- 登录不是固定流程；Explorer 只有在检测到可见 password input / 登录表单后，才会使用调用方提供的 `login_credentials`。
- `max_interactions` 用于受控点击非导航控件，采集弹窗、抽屉、Tab、展开区等动态 UI 状态。
- 动态状态会写入 `element_reveals_element` 关系，用于表达“哪个控件揭示了哪些 UI 元素”。

大模型的角色：

- 决定探索策略：根据目标、项目上下文、历史图谱选择是否提供登录凭据、探索深度、交互预算。
- 做语义解释：对 ARIA 事实结果进行业务命名、实体归并、页面意图总结和图谱去重建议。
- 不作为事实源：页面结构、可见性、链接、交互结果必须来自 Playwright / ARIA snapshot / Memgraph 图谱证据。
- 不硬编码流程：登录、点击和导航由 Harness 的检测与策略约束执行，大模型只提出可审计的探索意图。

## 技术栈与依赖

### 后端（Python >= 3.11）

FastAPI、Uvicorn、LangGraph、Pydantic v2、SQLAlchemy、psycopg（PostgreSQL）、PyMySQL、neo4j 驱动（Memgraph）、Redis、RustFS（通过 boto3 S3 客户端）、MCP SDK、cryptography。详见 `Agent_Server/pyproject.toml`。

### 前端

Vue 3、Vite 6、Naive UI、Pinia、Vue Router、TypeScript、Electron 31、VitePress。详见 `agent_web/package.json`。

### 存储

| 存储 | 用途 |
| --- | --- |
| PostgreSQL | 运行时数据：`agent_sessions` / `agent_session_messages` / `agent_session_events` / `agent_session_snapshots` / `agent_session_approvals` / `agent_session_resources` / `agent_tool_jobs` / `agent_tool_artifacts` / `agent_memories`（pgvector 向量记忆）/ `agent_mcp_servers` / `agent_projects` / `agent_api_docs` / `agent_security_bugs` / `agent_perf_runs`；冒烟四表 `agent_smoke_plan_catalog` / `_plan_versions` / `_run_history` / `_regression_candidates`；用例驱动八表 `agent_test_cases` / `_case_versions` / `agent_test_suites` / `_suite_items` / `agent_test_runs` / `_run_items` / `_run_attempts` / `_case_results`；录制两表 `ui_recording` / `ui_recording_event` |
| MySQL | 配置数据：`llm_model_config`、`system_email_config`、`system_channel_config`、`system_sponsor_config` |
| Memgraph | 知识图谱、项目级 UI 图谱（`Page` / `Element` / `Entity`）、录制子图（`Recording` / `Action`） |
| Redis | 分布式锁（如邮箱认证锁） |
| RustFS | 产物对象存储与上传安全扫描桶（temp / safe / quarantine） |

初始化脚本位于 `databases/` 目录（`1、MySQL/QA_Agent.sql`、`2、PSQL/public.sql`）。上述表名都可通过 `Agent_Server/.env` 的 Settings 项覆盖。

> **注意**：`databases/2、PSQL/public.sql` 只建 17 张表（会话族、tool jobs/artifacts、memories、mcp_servers、perf_runs、冒烟四表、录制两表），**不含** `agent_projects`、`agent_api_docs`、`agent_security_bugs` 与用例驱动八表。这些表由对应 store（`application/projects/project_store.py`、`application/documents/api_doc_store.py`、`modes/security_testing_mode/security_bug_store.py`、`application/test_{cases,suites,runs}/*_store.py` 等 19 处）在启动或首次使用时 `CREATE TABLE IF NOT EXISTS` 自建。所以"跑完 SQL 脚本就以为建全了"是错的；反过来，只想手工核对结构时也要以代码里的建表语句为准。

## 快速启动

### 0. 准备环境

复制 `Agent_Server/.env.example` 为 `Agent_Server/.env`，按需配置 MySQL / PostgreSQL / Memgraph / Redis / RustFS 连接信息。基础设施容器也可通过设置页的 Docker 管理面板一键拉起。

### 1. 启动后端

在仓库根目录同步 uv 工作区；后端与测试依赖会安装到根目录 `.venv`：

```bash
uv sync --locked
uv run --locked --directory Agent_Server uvicorn src.main:app --reload --port 1032
```

IDE 的解释器选择仓库根目录 `.venv/Scripts/python.exe`，入口选择 `Agent_Server/src/main.py`。也可以在根目录运行 `uv run --locked python Agent_Server/src/main.py`。

工作区以根目录 `uv.lock` 为唯一生效锁文件；后端依赖仍在 `Agent_Server/pyproject.toml` 声明。不要仅逐个安装 LangChain/LangGraph 主包：`langgraph-prebuilt` 等传递依赖也必须按锁文件同步。旧组合 `langgraph==1.0.10` / `langgraph-prebuilt==1.0.10` 曾在导入 `ExecutionInfo` 时失败，即使 `uv pip check` 通过仍可能存在运行时不兼容。

说明：

- 前端开发代理默认指向 `http://127.0.0.1:1032`（可用 `VITE_API_PROXY_TARGET` 覆盖）
- API 统一前缀为 `/api/v1`，健康检查见 `/api/v1/health`

### 1.1 启动 PostgreSQL Outbox Relay

任务提交先写入 PostgreSQL Outbox，再由独立 Relay 投递到 Redis Streams。Relay 不应与 API 进程绑定，生产环境建议作为单独的进程或服务运行：

```bash
uv run --locked --directory Agent_Server python -m src.cli.run_task_outbox_relay
```

只做一次有界投递（适合部署探针或手工恢复）：

```bash
uv run --locked --directory Agent_Server python -m src.cli.run_task_outbox_relay --once --batch-size 100
```

主 Stream 由 `ORCHESTRATION__REDIS_TASK_STREAM` 配置，额外 Stream 通过逗号分隔的 `ORCHESTRATION__REDIS_TASK_OUTBOX_STREAMS` 配置。Relay 会按 Outbox 记录中的 `stream` 精确投递；目标 Stream 不存在或不匹配时保留 Outbox 记录并等待重试，不会静默改投默认 Stream。租约和重试窗口分别由 `ORCHESTRATION__TASK_OUTBOX_LEASE_SECONDS`、`ORCHESTRATION__TASK_OUTBOX_RETRY_SECONDS` 管理。

启用 `ORCHESTRATION__REDIS_TASK_DISPATCH_ENABLED=true` 后，普通/回归 TestRun 的运行记录、RunItem 与首次 dispatch intent 会在同一个 PostgreSQL 事务中提交；Redis 暂时不可用时不会丢失任务意图。

Session/Turn 完成事件也会在同一 PostgreSQL 事务中写入 `compaction_task` Outbox。配置额外 Stream 包含 `qa:tasks:compaction` 后，单独启动低优先级压缩 Worker：

```bash
uv run --locked --directory Agent_Server python -m src.cli.run_compaction_worker
```

压缩 Worker 使用 Redis 热记忆的 event cursor 和稳定 `compaction_key`，只裁剪已成功落入 PostgreSQL 的事件；Worker 重试或并发重复投递不会删除截止点之后的新事件。

测试用例通过评审并激活固定版本时，用例状态、Embedding Job 和 Outbox 意图在同一个 PostgreSQL 事务中提交。单独启动低优先级 Embedding Worker，使用模型设置中已启用的 `embedding_retrieval` 模型生成完整向量：

```bash
uv run --locked --directory Agent_Server python -m src.cli.run_embedding_worker
```

Worker 不读取独立模型配置，也不截断或补零向量。向量先写入 PostgreSQL 权威表，再写入 Redis 版本索引；Redis 候选进入 Agent 上下文前必须按项目、环境、模式、用例生命周期、活动版本和内容哈希回查 PostgreSQL。旧版本任务会以 `superseded` 终止，不会调用模型或反复重试。

Redis 向量索引按 `embedding_version` 独立构建。恢复或模型版本切换时先预览 PostgreSQL 权威库存，再执行重建；只有 Redis 文档数对账和抽样召回都通过后，才允许原子切换在线索引：

```bash
uv run --locked --directory Agent_Server python -m src.cli.rebuild_memory_vectors --embedding-version <version>
uv run --locked --directory Agent_Server python -m src.cli.rebuild_memory_vectors --embedding-version <version> --execute --activate
```

`--execute` 只重建并验证副本，`--activate` 进一步写入 `qa:vector:active:memory` 指针。验证失败时保留原指针和旧索引，检索自动回源 PostgreSQL。验证等待时间和轮询间隔由 `ORCHESTRATION__REDIS_VECTOR_VALIDATION_TIMEOUT_SECONDS`、`ORCHESTRATION__REDIS_VECTOR_VALIDATION_POLL_INTERVAL_SECONDS` 管理。

生产环境通过低优先级 Stream 执行同一套重建与切换逻辑。先启动独立 Worker，再创建 PostgreSQL Job 与 Outbox 意图；Outbox Relay 会将任务投递到 `qa:tasks:vector_rebuild`：

```bash
uv run --locked --directory Agent_Server python -m src.cli.run_vector_rebuild_worker
uv run --locked --directory Agent_Server python -m src.cli.enqueue_vector_rebuild --embedding-version <version> --activate
uv run --locked --directory Agent_Server python -m src.cli.enqueue_vector_rebuild --entity test_case --embedding-version <version> --activate
```

`memory` 和 `test_case` 使用独立的版本索引与活动指针。任务状态持久化在 `agent_vector_rebuild_jobs`，包含 Worker、租约、维度、权威数据量、已复制数量、失败类型和最终活动索引。Worker 只在 `index_activated`、`index_validated` 或空数据终态落库后 ACK；崩溃时由 Redis Pending 接管和 PostgreSQL 租约共同恢复。

资源清理 Worker 会回收过期 Redis 租约，并对已经绑定真实容器 ID 的 Docker
租约调用受控的 `docker rm -f`。测试账号和环境是逻辑租约，过期时只释放占用；
当前 Python Playwright 的会话保存在所属进程内，独立 Cleanup Worker 无法直接关闭它；
带外部绑定但没有适配器的任务会失败并重试/进入死信，不能据此宣称浏览器已释放。
后续独立 CDP/ego-lite Browser Worker 需要实现远程清理适配器：

```bash
uv run --locked --directory Agent_Server python -m src.cli.run_resource_cleanup_worker
```

资源清理进程会将过期租约原子转交到 `qa:tasks:cleanup`，随后使用独立
Consumer Group 按租约绑定的真实外部资源 ID 执行补偿清理。清理失败沿用通用
Redis Worker 的指数退避、Pending 接管和死信机制；不要对任务 Stream 配置
`MAXLEN` 裁剪。

测试执行器在正常结束、异常结束或部分领取失败时统一调用 `retire()`：
未绑定外部资源且没有创建意图的逻辑租约直接释放；已绑定外部资源或分配标识的租约原子写入清理队列后
进入 `cleanup_pending`。过期回收复用同一个交接逻辑，继续占用原槽位和所有层级配额；
清理失败、重试或进入死信均不释放容量。清理结果写入 PostgreSQL
`agent_resource_cleanup_jobs` 后，通过 `lease_token + fencing_token + external_resource_id`
核对旧租约并原子释放配额，最后 ACK。数据库提交后崩溃，重复投递只补做 Redis 收尾。
`GET /api/v1/resource-occupancy` 的 `cleanup_pending_count/cleanup_pending` 单独展示
待清理资源（支持项目过滤），`active_count` 仍只计有效执行租约。
交接失败保留原租约、绑定和配额，由重试或过期回收继续处理；同一所有者重复交接
只产生一条清理消息。绑定必须匹配 `lease_token + fencing_token`，且禁止将尚未清理的
外部资源 ID 覆盖成另一个 ID。配额按 Redis 保存的领取范围结算，不信任调用方重传的范围。
当前浏览器工具仍使用调用级 `release()`，其跨调用会话生命周期尚未迁移到 `retire()`；
这条旧路径不能作为浏览器实例容量已受完整治理的证明。
升级时同步重启 API 和 Cleanup Worker，避免旧消费者 ACK 后不执行新版本配额收尾；
清理队列和重试继续使用已有配置。API 与 Cleanup Worker 启动时初始化新的
`agent_resource_allocations` 表，数据库账号需要建表权限；表名配置为
`DATABASE__POSTGRES_RESOURCE_ALLOCATION_TABLE`。
自动删除仅接受完整且不可复用的 Docker container ID（不接受容器名）；
性能测试的冒烟和正式执行已接入 `DockerResourceManager`：实际选择 Docker（包括
`auto` 后端）时领取独立租约，使用以下顺序：

```text
PostgreSQL 提交创建意图 → Redis 绑定 allocation_id → docker create
  → PostgreSQL 绑定完整 ID → Redis 绑定完整 ID → docker start --attach
```

PostgreSQL 意图写入失败时不创建容器；即使没有取得容器 ID，已准备分配的租约仍保留清理责任。
容器名称含 Run/RunItem/Attempt 的安全片段和随机后缀，labels 保存完整归属标识，
不写入租约凭证。绑定失败不启动；运行中续期失败会取消执行并按完整 ID 清理。
取消和超时会终止 Docker CLI 客户端，保留已捕获的 stdout/stderr（超时结果）并清理容器；
客户端使用线程内 `Popen`，兼容 Windows Uvicorn 的 Selector/Proactor 两种事件循环。
清理失败转交原有 Cleanup Worker，已本地删除的容器也经 PostgreSQL 清理回执确认后释放配额。
因此冒烟结束后若剩余容量不足，正式执行显示 `waiting_resource`，保留冒烟已完成的阶段。
创建/检查/删除命令超时配置为 `ORCHESTRATION__RESOURCE_DOCKER_COMMAND_TIMEOUT_SECONDS=60`；
实际测试执行超时继续采用引擎的 `RunOptions.timeout_seconds`，租约时长仍由
`ORCHESTRATION__RESOURCE_DEFAULT_LEASE_SECONDS` 管理。测试任务的容器为临时资源。

AI 工具 `http-probe-runner` 也通过同一 `DockerResourceManager` 创建临时 curl 容器，
使用现有全局/项目/Run/资源类型配额及 Cleanup Worker，无额外配置。
工具入口传递 Session/Turn/Project 上下文并保留父调用的 Run/Attempt 标识；资源不足时
ToolJob 进入 `waiting_resource`，不会把容量不足判成目标 HTTP 服务故障。
curl 自身超时和容器执行超时均返回失败并保留错误输出，取消执行仍回收容器。
探测与清理进程需要访问同一 Docker daemon，并提前准备镜像目录中的 `helper_http_probe` 镜像。
运行真实验证：`RUN_LIVE_DOCKER_TESTS=1` 下执行
`python -m pytest tests/test_live_resource_leases.py -k http_probe_tool -q`。
该测试使用临时本机 HTTP 服务，覆盖 HTTP 200/503、超时、取消、容量不足和 PostgreSQL 清理回执；
它不包含模型选工具或完整客户端会话链路的验收。

`mock-target-runner` 的跨调用容器复用同一个租约执行入口：ToolRuntime 持有后台监督任务，
从启动到停止持续续租，工具调用返回不会释放容器容量。另一个 API 实例可凭当前 Session
和容器名称查询 PostgreSQL 创建意图，校验归属后仅删除绑定的完整容器 ID；不接受跨会话停止。
会话结束清理复用该入口，资源登记失败也会停止刚创建的容器。API/Worker 正常退出取消监督任务，
强杀后交给过期租约与 Cleanup Worker；删除之后仍等待 PostgreSQL 清理回执才归还配额。
`ORCHESTRATION__RESOURCE_MOCK_TARGET_MAX_LIFETIME_SECONDS=3600` 限制最长存活时间，
与租约 TTL 分开管理；超时销毁后，原目标需重新创建。初始化为分配表补建 namespace/name 索引。
升级前没有持久化分配意图的旧 Mock Target 不支持新入口按名称停止，需在容器管理中核对后清理。
会话资源列表的异常退出实时投影仍待完善，当前以 Redis 租约、清理回执和容器实际状态判断占用。
真实回归使用 `RUN_LIVE_DOCKER_TESTS=1` 执行
`python -m pytest tests/test_live_resource_leases.py -k mock_target -q`；需要镜像目录中的
`helper_mock_target` 镜像，测试仅访问自动分配端口上的临时本机服务。

安全工具准备入口 `security-tool-bootstrap` 的临时容器也接入同一资源生命周期。
原有审批、包/仓库/镜像白名单检查通过后，使用已核验的不可变镜像 digest 创建容器；
启动、就绪检查、安装及复检仅操作已绑定的完整容器 ID。执行期间持续续租，
超时或取消会终止 CLI 并回收容器，进程强杀后由租约过期与 Cleanup Worker 接管。
容量不足返回 `waiting_resource`，不完成 Campaign、不生成终态完成时间。
`cleanup_complete` 仅表示已通过 Docker 清单确认本地容器消失；Docker 不可达或清理失败
不能报告清理成功，配额仍等待 PostgreSQL 清理回执确认后归还。
此入口复用现有 `SECURITY_TOOL_BOOTSTRAP_*` 与
`ORCHESTRATION__RESOURCE_*` 配置，无新增配置或依赖。
真实回归使用 `RUN_LIVE_DOCKER_TESTS=1` 执行
`python -m pytest tests/test_live_resource_leases.py -k tool_bootstrap -q`，
覆盖已安装工具检查、容量等待、未审批、超时、取消、清理失败补偿和执行进程强杀回收。
测试只使用本机已有 Kali 镜像，不安装软件包、不运行扫描；安装失败路径另有模块回归。
安全命令的临时和复用前台容器均接入统一租约：配置为 Docker 后端时，
先领取容量并持久化创建意图，再创建、绑定完整 ID、启动和执行。
TestRun 使用相同配置判定取消前台路径的重复 Docker 预占，
账号和环境租约继续领取。容量不足贯通 ToolJob/RunItem 的 `waiting_resource`；
配额原因沿用资源层返回值（例如 `global:all`），不会当作目标服务故障。
输出、非零退出码、超时及挂载目录里的产物保留；取消和心跳失败会停止执行并进入统一清理。
每次工具调用的产物目录增加随机后缀，避免同一 Session/Turn 同秒调用共享挂载目录。
镜像拉取策略通过 `docker create --pull` 执行，创建和拉取受现有
`ORCHESTRATION__RESOURCE_DOCKER_COMMAND_TIMEOUT_SECONDS` 限制；大镜像建议预拉取，
或按需调整该超时。无新增配置、依赖或数据库结构。
真实回归：`RUN_LIVE_DOCKER_TESTS=1` 下执行
`python -m pytest tests/test_live_resource_leases.py -k security_foreground -q`，
覆盖真实工具调用本机 HTTP 头探测、容量等待、输出与产物、超时、取消、失去租约及强杀回收；
不调用外部目标或模型。后台任务、独立文件传输容器及独立容器管理入口尚需接入。

`SECURITY__SECURITY_RUNNER_DOCKER_CLEANUP_AFTER_RUN=true` 时每条命令结束即清理；
设为 false 时使用受监督的持久容器，仍受会话总寿命限制，不会无限保留。
同时开启 `SECURITY__SECURITY_RUNNER_CONTAINER_REUSE=true` 才会复用容器：
复用键包含 project/session/turn/run/run_item/attempt，同一范围的并发命令串行执行，
不同范围独立申请容量，每个容器使用独立挂载目录。同一范围在不同 Worker 上不会共享容器，
需要保持工具调用的 Worker 归属；仅显式关闭支持跨实例。
复用关闭但执行后清理也关闭时，每次调用创建新的有期限容器。
等待串行执行的调用被取消不影响当前命令；正在执行的命令取消或超时则回收其整个容器。
重复关闭只等待同一次清理完成，不能再次取消已开始的清理交接。
真实回归：`RUN_LIVE_DOCKER_TESTS=1` 下执行
`python -m pytest tests/test_live_resource_leases.py -k security_reusable -q`，
覆盖状态保留、并发串行、等待者取消、Session/Attempt 隔离、保留/清理配置、超时和租约丢失。

SecurityShellSession 和 ExploitWorkspace 的跨调用持久容器使用同一资源管理器，
由 `DockerResourceSession` 持有启动、等待退出、租约续期和回收任务。
执行与文件传输使用完整容器 ID，并校验所属 Session；执行请求需回到持有监督任务的 Worker，
暂不支持跨 Worker 接管仍在运行的命令。关闭请求可通过 PostgreSQL 分配记录在另一实例处理，
校验所属 Session、资源 labels 和完整 ID 后清理；重复关闭保持幂等。
`SECURITY__SECURITY_ATTACK_SESSION_TIMEOUT_SECONDS=900` 是现有会话寿命配置的嵌套环境变量名，
控制持久容器启动后的总存活时间（含空闲），不是单条命令超时，也不是租约 TTL。
该期限到达、命令取消、命令超时或租约失效都会关闭整个容器，后续操作需创建新会话；
进程强杀仍由 Cleanup Worker 接管。配额在 PostgreSQL 清理回执落库后释放。
资源不足时保留 campaign 未完成阶段并返回 `waiting_resource`，不生成终态报告；
再次推进时从该阶段恢复，不重跑已完成阶段。
真实回归：`RUN_LIVE_DOCKER_TESTS=1` 下执行
`python -m pytest tests/test_live_resource_leases.py -k 'security_persistent or mock_target' -q`，
覆盖跨命令状态、文件往返、租约续期、容量等待、跨 Session 拒绝、跨实例关闭、总寿命、
命令超时/取消、租约丢失、优雅关闭和进程强杀后的实际容器回收。

创建成功但尚未绑定 ID 时进程被强杀，Cleanup Worker 使用持久化意图及容器 labels
核对 allocation_id、namespace、资源 ID、fencing token 和原始名称，只按完整容器 ID 删除。
后台扫描复用 `ORCHESTRATION__RESOURCE_CLEANUP_INTERVAL_SECONDS`，每批最多核对
`ORCHESTRATION__RESOURCE_DOCKER_RECONCILE_BATCH_SIZE=100` 个容器，并推进扫描游标。
同一有效所有者的容器不会入队；失去所有者的容器进入原有 cleanup Stream。
清单任务使用 allocation_id 与完整容器 ID 组成独立幂等键：之前“未发现容器”的成功回执
不会跳过随后才创建成功的容器，也不会释放已被新所有者领取的配额。
创建意图在清理后继续保留以识别迟到的容器，不配置自动 TTL；后续归档需先证明无在途创建请求。
意图中的租约凭证仅供内部恢复，禁止写入日志或暴露到管理接口。
清单投递失败时下一轮重新扫描；死信任务保留投递去重标记，处理故障后需重放原任务，
不能靠重复扫描绕过最大重试次数。对账仅覆盖 Cleanup Worker 当前连接的 Docker daemon。
本单元也不代表 P4 的浏览器治理与并发验收全部完成。
Redis 完全丢失后可从 PostgreSQL 意图与 Docker 清单重新投递孤儿容器清理；这不等于恢复了
丢失期间的准入配额，也不保证跨 daemon 恢复。生产必须配置 Redis 持久化；全量丢失后应暂停
新任务准入，完成资源对账和配额恢复后再开放。自动化准入恢复仍待补齐。

### 2. 启动前端

```bash
cd agent_web
npm install
npm run dev        # 同时启动应用（5175）与 VitePress 文档站
npm run dev:app    # 只启动应用
```

默认开发地址：

- 前端：`http://localhost:5175`
- 后端代理目标：`http://127.0.0.1:1032`

### 3. 桌面端（可选）

```bash
cd agent_web
npm run desktop        # 本地运行 Electron 桌面端
npm run desktop:pack   # 打包 Windows 桌面安装目录
```

## API 路由概览

后端在 `src/main.py` 中注册 **21 组**路由（统一前缀 `/api/v1`）：

| 路由 | 职责 |
| --- | --- |
| `sessions` | 会话 CRUD、消息发送、SSE 事件流、事件历史、快照、interrupt / resume 审批、replay 回放、tool-jobs、artifacts、无头执行 |
| `registry` | Agent / Tool / Model / Skill / MCP / Mode 注册中心查询 |
| `projects` | 项目 CRUD 与项目总览（`/projects/{id}/overview`）、历史冒烟运行归集 |
| `case_management` | 用例驱动链路：草稿列表 / 详情 / 版本、`submit-review`、`activate`（启用固定版本）、`archive` |
| `suite_management` | 套件与冻结版本：`/projects/{id}/suites`、套件详情、归档 |
| `run_management` | 运行与运行条目：`/projects/{id}/runs`、`runs/{id}/claim` 原子领取、`run-items/{id}` 的 start / heartbeat / complete |
| `security_bugs` | 安全漏洞列表 / 详情 / 复现包下载 / `retest` 重测 / 状态修改 |
| `recordings` | UI 录制：会话创建·列表·详情·删除、`attach-registry`、`events:batch` 幂等上报、`commands` long-poll、`screenshots` multipart、`control` 指令、`graph` 子图投影、`recorder.js` 下发 |
| `api_docs` | API 文档上传与解析 |
| `attachments` | 附件与上传安全扫描 |
| `reports` | 报告中心 |
| `task_pool` | 任务池 |
| `knowledge` | 知识图谱 |
| `compatibility` | 兼容性测试运行 |
| `integrations` | 集成目录 |
| `settings` | 模型、邮箱、渠道配对、数据导入导出等系统配置 |
| `sponsors` | 赞助商只读列表（`GET /sponsors`，仅返回 enabled 记录） |
| `oauth` | 模型 Provider OAuth 授权（Azure / Google / GitHub / CodeBuddy / TRAE / Codex） |
| `mail` | 邮件能力 |
| `docker` | Docker 容器管理 |
| `health` | 健康检查 |

> 命名注意：`case_management.py` / `suite_management.py` / `run_management.py` 三个模块导出的路由器变量分别是 `test_cases_router` / `test_suites_router` / `test_runs_router`，且它们**不带额外子前缀**，最终路径形如 `/api/v1/test-cases/{case_id}`、`/api/v1/projects/{project_id}/runs`。

## 设计映射

项目当前遵循“注册中心 + 图编排 + 工作台 UI”的三层思路：

1. `registry`
   统一管理 Agent、Tool、模型等元数据注册，后续新增能力优先接入这里。

2. `graph`
   使用 LangGraph 组织运行链路，逐步沉淀可恢复、可审批、可中断、可重放的执行状态机。

3. `ui + api`
   前端不是单纯聊天页，而是工作台；后端不是单一 `/chat` 接口，而是 session shell、event stream、approval、dispatch 的统一运行接口。

## 后端 application 分层

`Agent_Server/src/application` 已按职责拆分为 **36 个子包**，避免所有服务文件堆在同一层：

```text
application/
├─ api_doc_resolution/  # API 文档定位与解析
├─ artifacts/           # Artifact 存储与 RustFS 对象存储适配
├─ capabilities/        # 能力解析与工具暴露策略（capability_resolver / tool_exposure_policy）
├─ compatibility/       # 兼容性 Runner
├─ context/             # Memory、MCP、Observation、Transcript Hygiene
├─ documents/           # API 文档服务
├─ exploration/         # UI 探索支撑、UI 图谱与录制子图写入
├─ flow/                # 编排轨迹投影（前端 Flow 视图的数据来源）
├─ images/              # 镜像目录（测试引擎/环境镜像）
├─ integrations/        # 集成目录服务
├─ intent/              # 意图识别、模式选择策略、安全意图与语义意图
├─ knowledge/           # 知识图谱服务
├─ mail/                # 邮件服务与腾讯邮箱认证监控
├─ mcp/                 # MCP 连接管理、工具桥、服务器存储
├─ model_adapters/      # OpenAI/Anthropic/Gemini 等模型 provider adapter
├─ model_clients/       # 各家模型客户端实现与 Embedding 客户端（embeddings.py）
├─ model_providers/     # Provider 元数据与 OAuth 令牌服务
├─ models/              # 模型运行时与模型兼容性
├─ orchestration/       # 输入编排、Coordinator/Worker 调度
├─ performance/         # k6/JMeter 引擎适配、目标保护、指标存储
├─ permissions/         # 工具权限策略与审批请求
├─ projects/            # 项目总览与历史冒烟运行导入
├─ prompting/           # Prompt submit 与结构化 prompt 组装
├─ recorder/            # UI 录制：assets/recorder.js + drivers（embedded / cdp-attach / playwright-managed）+ 会话编排
├─ registries/          # Registry 聚合查询服务
├─ reporting/           # 报告构建
├─ resources/           # 会话资源与浏览器会话清理
├─ runtime/             # LangGraph turn runtime、工具运行时、工具任务
├─ security/            # 安全测试与上传安全扫描
├─ sessions/            # 会话用例服务
├─ settings/            # 模型/邮件/渠道等系统配置服务
├─ skills/              # Skill 运行、管理与 marketplace
├─ test_cases/          # 用例草稿、评审、启用固定版本
├─ test_runs/           # 运行条目原子领取、心跳与结果入库
├─ test_suites/         # 套件与冻结版本
└─ testing/             # QA 方向识别、测试路由、验证与 UI 探索
```

未进子包的顶层服务另有三个：`docker_management_service.py`、`report_service.py`、`task_pool_service.py`。

原 `test_direction_service.py` 与 `test_router_service.py` 并不是测试用例文件，它们实际参与输入编排；现已改为 `testing/direction_service.py` 与 `testing/router_service.py`，并使用 `QATaskDirectionService` / `QATaskRouterService` 命名。

## 测试

```bash
uv sync --locked
uv run --locked --directory Agent_Server python -m pytest tests

cd agent_web
npm test                   # vitest run（含 tests/recorder/recorder.test.mjs）
npm run build              # vite build（注意：脚本里没有 vue-tsc，类型检查需另行执行）
```

`Agent_Server/tests/` 覆盖性能测试全链路（runner / coordinator / adapter / parser / guard / report）、**UI 录制域**（`recording_store` / `recording_graph_store` / `recordings` 路由 / 契约 schema / 三种 recorder 驱动 / `replay_executor` 纯函数与真实 Chromium live 回放 / P0 端到端串联 / `ui_resource_assessor` / `assertion_suggester`）、邮件、知识图谱、任务池、报告、会话存储、渠道配置等模块。

> 需要真实浏览器的验收用例（`tests/test_replay_live.py`）由 `RUN_LIVE_REPLAY=1` 门控，默认干净跳过而不是失败；要跑真实回放链路请显式设置该变量。

## 当前进度与下一步

已完成并带测试的骨架：`session + event + snapshot + approval` 协议、LangGraph 编排与 interrupt/resume、Coordinator/Worker 调度、8 种实装测试模式（另有 6 种占位待补 Runner）、54 个 Skill、前端工作台可观测性、知识图谱与 pgvector 记忆、用例驱动八表、UI 录制 P0~P2（契约/存储/注入脚本/三种驱动/固化/审批编排/API/桌面端窗口/回放执行器/录制转草稿/断言建议）。

接下来真正欠的：

1. **录制域收口**：手工 GUI 验收（`docs/ui_recording_progress.md` 已列出无法自动化的项）；回放执行器与真实业务页面的定位退化数据回流
2. **P3 ego-lite 驱动 ⛔ 阻塞**：依赖方 ego-lite 目前仅提供 macOS 实现，当前 Windows/Linux 环境无法推进，需要先决定替代方案或等上游
3. **安全测试闭环**：攻击链与漏洞台账已有，但实验/验证与复现包质量仍缺一轮真实目标回归
4. **6 个占位模式补专用 Runner**：`integration_testing` / `unit_component_testing` / `mobile_testing` / `visual_regression_testing` / `accessibility_testing` / `reliability_testing` 目前只注册 Skills，需要各自的 harness 与执行链路才能从"占位"转"实装"
5. **前端类型检查**：`npm run build` 只有 `vite build`，未接 `vue-tsc`，类型回归目前靠人工

> 文档口径已于 2026-08-30 与代码对齐：`docs/` 下进度与方案文档里的 MinIO 已统一改为 RustFS，`recordings` 端点数改为按装饰器实测的 11，`docker compose up -d` 这类项目内并不存在的步骤已换成设置页 Docker 面板。后续改代码请同步这几份文档。

## 开发建议

- 前端改动优先围绕 `session / event / approval / runtime status` 展开
- 后端扩展优先走 `registry + graph + runtime` 这条主线
- 避免把业务逻辑直接写死在页面或单个节点中
- 新增能力前，优先确认是否符合 `Harness Engineering` 约束
- 跨边界契约（路由、Pydantic 契约、事件名、表名、Settings 配置项）改动必须同步所有消费方，含 `agent_web/src/services/api.ts`、i18n 与本文档
