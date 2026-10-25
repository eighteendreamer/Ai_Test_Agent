# 御策天检全系统架构审查与升级优化方案

> 版本：v1.0（2026-10-25）  
> 基线：`d32d00f05`（重构测试项目管理页面为 naive-ui 交互）  
> 范围：`Agent_Server`（FastAPI / LangGraph / PG / Redis / Worker）、`agent_web`（Vue 3 / Pinia / Naive UI / Electron）、相关文档与成熟 Agent 参考实现  
> 性质：架构审查 + 目标设计 + 迁移方案。本文不修改任何代码；所有结论以当前工作区源码为准，并标注证据位置与未验证边界。

---

## 摘要（给决策者）

- **执行面已走了一半**：HTTP 聊天入口已经 202 入队，经 outbox/Redis 到 Agent Worker；但 `SessionService` 仍保留内部内联路径，且所有执行类型没有统一接纳契约。
- **真正阻碍扩展的不是缺模块，而是对象与生命周期过多**：6 套任务生命周期、4 个模式协调器、2 套 RunStore、3 套模式 TaskPool、11 套前端状态映射；任务中心只是 5 张表的 UNION。
- **长任务地基“测试强、Agent 弱”**：RunItem/Attempt 有租约、心跳与回收；Agent Turn 只有 turn 末快照，强杀后不能续跑，且没有工具副作用账本。
- **完成语义不可审计**：没有一等公民 Evidence；通过可能来自 Agent 自述、确定性断言或语义模型，UI 无法区分。
- **场景扩展靠改主链**：14 个模式、45 个硬编码 Agent、模式 runtime 由 if/elif 分发；新增一个测试场景要改多处，而不是装一个包。
- **目标**：一个执行内核 + Scenario Pack + 测试工作台；P0 先做“契约冻结、死代码清理、输入四模式 + 停止、统一任务投影”，P1 做“Step checkpoint + Evidence/Verdict + 事件流”，P2 做“场景包化与结构收敛”。
- **本文不实施代码**。需要用户拍板的 5 项在第 15.4 节。

阅读路径：决策者看摘要 + 第 1、13、14 章；架构评审看第 3–12 章；实施负责人看第 13–15 章与附录。

---

## 审查口径

- 审查目标不是“再加一层 Harness”，而是回答三个问题：
  1. 为什么能力已经很多，扩展一个新测试场景仍然要改主链？
  2. 为什么长任务地基（PG 事实表、outbox、租约、RunItem/Attempt）不弱，但普通会话仍无法可靠恢复、无法插话、无法停止？
  3. 为什么 UI 功能齐全，却仍像开发者控制台，而不是测试工作台？
- 审查方法：当前源码 + 现有设计文档 + 成熟通用 Agent 本地实现三方对照；不以旧文档或历史结论替代当前事实。
- 证据规则：每条判断尽量给出 `文件:行号`；无法从源码确认的部署拓扑、真实并发、生产数据分布，单独标为“未验证”。
- 本文把“问题”和“方案”分开：第 5 章是可复现的架构缺陷，第 6 章之后是目标设计与迁移路径。不得用“已规划”代替“已实现”。
- 术语约定：
  - **Kernel**：执行本体、状态机、契约与端口，不含具体测试场景。
  - **Scenario Pack**：一个测试场景（API、UI、性能、安全、兼容性、代码评审等）的声明式扩展包。
  - **Workbench**：面向测试人员的工作台 UI，不是调试控制台。

---

## 0. 事实校正：旧判断与当前代码的差异

上一轮评估中有几条结论已经过时，若不校正会误导方案。先逐条对齐事实。

| 旧判断 | 当前事实 | 证据 |
|---|---|---|
| “正常对话在 API 进程内执行，只有排队/后台任务走 Worker” | **HTTP 主入口已全部走 Worker**：`POST /messages` 直接 202 返回 `AgentTaskAccepted`，由 dispatcher 入 outbox/Redis，再由 `run_agent_worker` 消费。 | `src/api/routes/sessions.py:148-157`；`src/application/orchestration/agent_task_dispatcher.py:78-136`；`src/cli/run_agent_worker.py:18-35` |
| “普通 turn 默认不走 Agent Worker” | **不成立**。仍存在内联路径，但它是 `SessionService.send_message` 供 coordinator 子会话/内部冻结执行使用，不是 HTTP 主入口。 | `src/application/sessions/session_service.py:1352-1387`；唯一生产调用方在 `coordinator_runtime_service.py:1433` |
| “前端停止按钮没接上” | **成立但需精确**：输入框本身未禁用，禁用的是发送；`interruptCurrentTurn` 已有 store action，但全前端没有组件调用；Composer 中没有停止按钮。 | `agent_web/src/components/ChatComposer.vue:192,269-274,307`；`agent_web/src/stores/session.ts:1231-1245`（grep 无 `.vue` 调用） |
| “6 个 `src/core/*` 模块零业务引用” | **基本成立**：`tool_router`、`prompt_cache`、`agent_manifest`、`orchestration_strategies`、`guardian`、`permission_dsl` 全仓零引用；`unified_router` 只在自身 docstring 出现；`world_state` 仅单测引用；`hybrid_search` **源码中并不存在**，只存在于方案文档。 | `src/core/` 清单；`src/core/unified_router.py:68`；`tests/test_world_state_boundaries.py:4` |
| “AgentLoop.stream_turn 没有调用方” | **成立**：`src/` 与 `tests/` 均无调用；生产只走 `run_turn`。 | `src/application/runtime/agent_loop.py:45-96,98-215`；`events.py:3,32` 仅为注释 |
| “快照只在 turn 收尾保存” | **成立**：唯一 `save_snapshot` 在 turn 返回后；turn 开始时 `pending_turn` 被清空，因此进程强杀后不能从中间继续，只能被标记 interrupted。 | `src/application/sessions/session_service.py:2455,1690-1701`；`src/runtime/postgres_session_store.py:2131-2165`；`tests/test_agent_task_dispatch.py:906-933`（测试断言“恢复=放弃”） |
| “任务池 5 类对象” | **成立**：`session / case_planning / document_difference / document_impact / case_generation`，且“池”是对 5 张无关表的 UNION，不是统一任务表。 | `src/schemas/task_pool.py:26`；`src/runtime/postgres_task_pool_store.py:70-138` |
| “模块没有 migration，靠自建表” | **部分过时**：项目已有 Alembic（4 个迁移版本），但仍有 80 处 `CREATE TABLE IF NOT EXISTS`，两套机制并存。 | `Agent_Server/alembic/versions/`（4 个迁移文件）；`grep -c "CREATE TABLE IF NOT EXISTS"` = 80 |
| “ProjectsView 2612 行” | **当前仍成立**；最近只把项目目录/表单拆成 `ProjectCatalog.vue` 与 `ProjectFormModal.vue`，详情与运行仍在原文件。 | `agent_web/src/views/ProjectsView.vue` 2612 行；`src/features/projects/ProjectCatalog.vue` 423 行 |

**校正后的总体判断**：执行面的“Worker 化”已经完成了一半，真正缺的是**统一执行本体、Step 级持久化、证据终态和场景扩展契约**；继续按八层各自造对象只会放大大体量。

---

## 1. 总体判断

### 1.1 可以保留的强项

- 测试执行侧的持久化在同类系统中属于扎实水平：`TestRun → RunItem → Attempt`、原子领取、lease/fencing、心跳、过期回收、outbox、Redis Consumer Group 都已存在。
- 会话侧已有 Agent Worker、审批恢复、turn lease 与事件表；不是“纯内存 Demo”。
- 领域资产完整：14 个测试模式、用例版本/评审/启用、套件冻结、文档差异/影响、录制、知识库、效果证据。
- 前端已有桌面优先的骨架、Pinia 状态、SSE、审批与运行看板，具备改造成工作台的基础。
- 已有大量真实链路测试：后端 526 个测试文件，前端 77 个测试文件 / 564 个用例。

### 1.2 核心问题（一句话）

**系统不是缺能力，而是缺“收敛后的运行本体 + 场景扩展契约 + 证据裁决”；能力被复制在 4 套生命周期、4 套协调器、2 套运行存储和 11 套前端状态映射里，导致复杂度和维护成本随场景数量线性甚至平方增长。**

### 1.3 三个方向

1. **一个内核**：统一 WorkItem / Execution / Turn / Step / Attempt / Evidence；现有 session/run/plan/doc 表仍是事实源，新对象只做**投影**，不复制事实。
2. **场景包**：测试场景由声明式 Manifest + Runtime Adapter + Verifier + UI Panel 组成，新增场景不改内核。
3. **测试工作台**：执行面（会话 + Inspector）、调度面（统一任务中心）、资产面（项目/用例/文档/环境）、设置四分；停止、插话、排队、审批、证据都成为一等交互。

### 1.4 与通用 Agent 的边界

本系统不是通用编码 Agent。可借鉴的是控制面、执行面、steering、恢复、工具治理与工作台形态；**不可照搬**以文件/仓库为中心的 primitive 体系。我们的 primitive 是：

```text
Scenario（测试场景）
  ├── Case / CaseVersion / Suite / SuiteVersion
  ├── Plan / Run / RunItem / Attempt
  ├── Action / Assertion / Verification
  ├── Evidence / Verdict / Evaluation
  └── Environment / Credential / Resource
```

---

## 2. 现状基线（量化）

### 2.1 后端体量

| 项目 | 数值 | 证据 |
|---|---:|---|
| Python 文件 | 775 | `find src -name "*.py" \| wc -l` |
| 测试文件 | 526（约 6.8 MB） | `find tests -name "test_*.py"` |
| 测试模式 | 14 包，33,704 LOC；7 个有 runtime，6 个 placeholder | `src/modes/`；`registry/modes.py:5-18,29-44` |
| 硬编码 Agent | 45 个 `AgentModule` | `src/registry/agents.py:16-878` |
| 工具注册表 | 2,374 行 `dict` 目录 | `src/registry/tools.py` |
| 路由 | 35 个 | `src/main.py:1059-1093` |
| 配置字段 | 约 290 个，637 行，11 个配置类 | `src/core/config.py` |
| 生命周期装配 | `_initialize_lifespan_resources` 约 790 行；29 个 setter 调用；134 个 `app.state` 赋值 | `src/main.py:213-1002` |
| setter 注入面 | 57 个 `def set_*` | `src/` grep |
| 两套容器 | `main.py` 与 `containers.py` 并行 | `src/containers.py` 426 行 |
| 原始 DDL | 80 处 `CREATE TABLE IF NOT EXISTS`；Alembic 仅 4 个迁移版本 | 源码 grep；`alembic/versions/` |

### 2.2 最大文件（复杂度热点）

| 文件 | 行数 | 当前职责 | 风险 |
|---|---:|---|---|
| `application/runtime/tool_runtime_service.py` | 6,052 | 工具执行 + 7 个模式 runtime 托管 + 协调器依赖 | 事实上的“第二主链” |
| `application/test_runs/run_store.py` | 4,124 | 运行状态机；内存与 PG 两套完整实现 | 双实现漂移 |
| `modes/security_testing_mode/runtime.py` | 4,009 | 安全 campaign 状态机 | 模式内再造主链 |
| `application/orchestration/coordinator_runtime_service.py` | 3,588 | 子 Agent 调度/生命周期 | 与 dispatcher 重复 |
| `application/sessions/session_service.py` | 3,241 | 会话 turn + 审批 + 恢复 + 内部执行 | 聊天与测试执行共用枢纽 |
| `runtime/postgres_session_store.py` | 2,647 | 会话持久化 | 与 store 协议耦合 |
| `application/runtime/runtime_service.py` | 2,199 | turn 编排 | 与 AgentLoop 职责重叠 |

### 2.3 执行链（当前）

```text
HTTP POST /messages
  → 202 AgentTaskAccepted
  → AgentTaskDispatcher.submit
  → Outbox (PG) → Relay → Redis Stream
  → run_agent_worker
  → SessionService.execute_claimed_message
  → RuntimeService
  → AgentLoop.run_turn
  → graph.ainvoke（9 节点，线性 + 1 个循环边）
  → tool_executor（串行 for 循环）
  → finalizer / responder
  → turn 结束：唯一 snapshot + 终态事件
```

**关键事实**：

- ReAct 循环在 graph 外：`AgentLoop.run_turn` 反复调用整张图，因此 context_builder / router / planner / permission_gate 每轮重跑（`agent_loop.py:59-96`；`graph/builder.py:74-154`）。
- `stream_turn` 是死路径（无调用方）。
- 工具在同批次内串行，只在每次调用前检查中断（`tool_executor.py:66-75`）。
- 审批在批次内创建，但批次继续；finalizer 再挂起（`tool_executor.py:518-529,45-49`）。
- SSE 每连接轮询 PG，0.5s 间隔，200 条批，15s keepalive（`api/routes/sessions.py:376-430`；`core/config.py:216-218`）。
- Redis Streams 只承载任务，不承载 turn 事件；turn 事件直接写 PG（`infrastructure/redis_task_queue.py:114,208`；`runtime/session_event_sink.py:46-58`）。
- 强杀恢复：lease 过期 → session interrupted；没有 Step checkpoint；`pending_turn` 在 turn 开始时清空，因此不能继续（`session_service.py:1690-1701,2455`；`postgres_session_store.py:2131-2165`）。

### 2.4 领域/生命周期数量

| 生命周期 | 存储/状态 | 终态判定 |
|---|---|---|
| Session/Turn | `SessionStatus` 6 值；AgentTask 11 值 | Agent 结束 + 审批/取消 |
| 用例规划 | `agent_case_plans` 5 值 + `agent_case_planning_tasks` 4 值 + phase | Agent 结果 + plan decision |
| 运行计划修订 | 复用 planning task 表 + `TestRunPlanRevisionRecord` | 服务端修订事务 |
| 文档差异/影响/解析 | 4 个队列，各 4 状态 + phase | 解析/模型输出 |
| 用例增量生成 | 独立 generation store 4 状态 + retry 表 | 生成 receipt |
| 测试运行 | `TestRunStatus` 4 / `RunItemStatus` 11 / `RunAttemptStatus` 10 / `TestResultStatus` 5 | 服务端 receipt + 步骤最差值 |
| 工具任务 | `ToolJobStatus` 12 | 工具执行 |
| 验证 | `VerificationStatus` 4 | 确定性/语义校验 |

**结论**：没有统一 `TaskId` 或父链；任务中心只能靠 SQL UNION 拼 5 张表（`postgres_task_pool_store.py:70-138`）。前端因此出现 11 套状态映射（TaskPool、Reports、EventConsole、RuntimeStatus、ToolActivity、ToolJob、Verification、Smoke、CodeReview、ChatTimeline、FlowCanvas）。

### 2.5 证据与完成语义

- 没有一等公民 `Evidence` 聚合/表，至少 5 种形状：
  - `RunEvidenceRef`（`schemas/run_management.py:230`）
  - `VerificationEvidence`（`schemas/session.py:111`）
  - `CaseToolEvidenceReference`（`application/test_runs/case_execution.py:248`）
  - `PlanningEvidence`（`schemas/case_planning.py:277`）
  - `EffectEvidenceDeclaration`（`schemas/case_effects.py:20`）
- 结果 JSONB 内嵌 `evidence_refs`；验证结果同时嵌入 result 和 session metadata（`execution_service.py:1248-1288`）。
- 终态是混合裁决：Agent 自报步骤状态，服务端在确定性校验不一致时覆盖；语义校验仍信任模型（`agent_case_assessment.py:497-511,486-489`）。
- 没有 `AgentStatus / ExecutionStatus / TestStatus / EvaluationStatus` 的类型化分层；实际是 8+ 个独立枚举。

### 2.6 前端体量

| 项目 | 数值 | 证据 |
|---|---:|---|
| Pinia stores | 3 个，2,027 行 | `app.ts` 229 / `generalSettings.ts` 274 / `session.ts` 1,524 |
| `session.ts` 状态/动作 | 28 state / 26 getters / 22 actions | `stores/session.ts:539-1522` |
| 底部控制台 tab | 8 个 | `App.vue:144-153` |
| 主时间线事件上限 | 最近 24 条 | `ChatTimeline.vue:63` |
| 任务池刷新 | 5s 轮询 | `TaskPoolView.vue:123` |
| API 聚合 | 31 行 spread + 14 个模块，2,761 非测试行 | `services/api.ts` |
| 类型文件 | 3,017 行 / 316 导出类型 | `types.ts` |
| 项目详情 | 2,612 行 | `ProjectsView.vue` |
| 路由 | 16 条，最深 4 段；无 `/tasks/:id` | `router/index.ts` |

---

## 3. 目标架构总览

### 3.1 一句话目标

**把“测试场景能力”从主链代码里抽出来，变成可声明、可验证、可版本化的 Scenario Pack；把执行、事件、证据收敛成一套可恢复的内核；把 UI 从控制台改成以工作对象为中心的测试工作台。**

### 3.2 目标图

```text
┌───────────────────────────── 客户端（测试工作台） ─────────────────────────────┐
│ 执行面：会话时间线 + Harness Inspector   调度面：统一任务中心 /tasks/:id          │
│ 资产面：项目 / 用例 / 套件 / 文档 / 录制 / 环境      设置：模型 / 权限 / 配额      │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │ REST 命令 + SSE/事件游标
┌───────────────────────────────────▼───────────────────────────────────────────┐
│                          API / Admission（控制面入口）                         │
│  认证 → 项目边界 → 命令幂等 → WorkItem 接纳 → 输入模式（steer/queue/interrupt）  │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │ Outbox（PG）→ Redis Streams
┌───────────────────────────────────▼───────────────────────────────────────────┐
│                          Execution Kernel（控制面）                            │
│  WorkItemProjection │ Execution/Step/Attempt 状态机 │ Checkpoint │ 事件账本    │
│  Evidence/Verdict   │ 输入队列 / 审批 / 配额 / 资源租约 │ 恢复与对账             │
└───────────┬───────────────────────┬───────────────────────────┬───────────────┘
            │                       │                           │
┌───────────▼─────────┐  ┌──────────▼─────────┐  ┌──────────────▼─────────────┐
│ Agent Worker        │  │ Test Run Worker    │  │ Document/Generation Worker │
│ 模型 + 工具 + 审批   │  │ 浏览器/设备/Docker  │  │ 解析/差异/影响/生成         │
└───────────┬─────────┘  └──────────┬─────────┘  └──────────────┬─────────────┘
            │                       │                           │
┌───────────▼───────────────────────▼───────────────────────────▼───────────────┐
│                           Harness Ports（适配器边界）                         │
│ ModelPort │ ToolGateway │ EnvironmentPort │ ArtifactStore │ MemoryPort         │
│ VerifierRegistry │ ScenarioRuntimeRegistry │ Policy/Quota │ Observability      │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │
        PostgreSQL 事实表 / Redis 调度 / 对象存储（截图、HAR、视频、报告）/ Memgraph
```

### 3.3 设计原则

1. **少建对象，多建投影**：不为每个业务再造一套 Task；用 `WorkItemProjection` 统一读模型，源表保持事实权威。
2. **控制面只做接纳与裁决，执行面只做执行**：API 不跑长任务；执行失败由控制面发现、恢复或标记。
3. **完成由证据裁决**：Agent 文本结束不是通过；测试通过必须绑定 verifier 与 evidence。
4. **场景即包**：新增测试场景只新增 Manifest、Adapter、Verifier、UI Panel 与 fixtures，不改内核。
5. **先删后加**：每个新抽象必须替换或合并至少一个旧抽象；未接线的脚手架要么接线，要么删除并标注 `planned`。
6. **所有跨边界契约有版本**：`contract_version` / `schema_version`，消费者不兼容即拒绝。
7. **测试域优先**：所有设计先满足用例版本、套件冻结、运行条目、证据、环境和回归；不为通用 Agent 的假设让路。

### 3.4 五条硬不变量

1. 同一 Execution 单写者：lease + fencing token；所有状态写入校验 token 与版本。
2. 至少一次投递 + 幂等副作用：队列可重复，工具副作用必须经 ledger 去重。
3. 每个 Step 边界可 checkpoint：不是只在 turn 结束。
4. 用户输入零丢失：`steer / queue / interrupt` 三种语义显式区分，有持久化身份与去重。
5. 终态由证据裁决：Agent 自述只能产生 `StepCompleted`，不能产生 `TestPassed`。

---

## 4. 已确认架构问题

> 编号 A-xx 为架构问题，不是单个代码 bug。级别：P0 = 阻碍扩展/正确性；P1 = 长任务与体验；P2 = 收敛与观测。

### A-01：没有统一执行本体，六套生命周期靠 UNION 拼接

**级别：P0；范围：任务模型、API、UI。**

- 现象：session、case_planning、document_difference、document_impact、case_generation、test_run 各自有表、状态、路由与详情页；“任务池”只是 SQL UNION（`postgres_task_pool_store.py:70-138`）。
- 影响：任何跨生命周期能力（统一搜索、父子链、统一详情、统一取消、统一证据）都要逐套实现；新增一种任务会继续放大 N² 成本。
- 根因：先有业务表，后补“任务”概念；没有在领域层定义 WorkItem/Execution。
- 方向：定义 `WorkItemProjection`（读模型）+ `ExecutionEnvelope`（执行契约），源表继续作为事实；先做 SQL VIEW，后续再决定是否物化。禁止再建“第 7 套任务表”。

### A-02：测试场景扩展是改代码，不是装包

**级别：P0；范围：14 个模式、Agent/Tool 注册、验证。**

- 现象：
  - 模式 runtime 由 `tool_runtime_service` 中的 if/elif/硬编码 handler 分发（`:2894,2902,3905` 等），没有统一 Protocol。
  - 45 个 Agent 以字面量 `AgentModule(...)` 写死在 `registry/agents.py:16-878`。
  - 3 个模式各自维护内存 task pool 与独立 `TaskStatus`（api 8 值、performance 7、security 7）。
  - 7 个 runtime 各自实现恢复/持久化/摘要；6 个 placeholder 仍出现在目录。
- 影响：新增一个测试场景要改模式包、注册表、工具运行时、前端状态映射、验证器；场景之间无法用同一套契约测试。
- 根因：模式 Manifest 只描述“元数据”，没有绑定 Runtime/Verifier/UI/Context；注册是手工列表。
- 方向：Scenario Pack v2：Manifest + Runtime Protocol + Verifier + Evidence Schema + Context Fragments + UI Panel；注册表由 Manifest 自动发现，内核零 switch。

### A-03：主链服务巨型化，双实现漂移

**级别：P0；范围：`tool_runtime_service`、`run_store`、`session_service`、`coordinator`。**

- 证据：`tool_runtime_service.py` 6,052 行；`run_store.py` 4,124 行，内存实现约 1,235 行、PG 实现约 2,668 行，状态机写了两遍；`session_service.py` 3,241 行；`coordinator_runtime_service.py` 3,588 行。
- 影响：任何状态机改动需要双改；测试只能覆盖其中一条；新人无法判断“真正入口”。
- 根因：按“存储实现”而不是“端口 + 适配器”组织；内存实现不是测试替身，而是第二产品实现。
- 方向：先冻结一个 `RunStore` 契约测试集，内存实现只做 fake；把 tool runtime 拆成 ToolGateway / ScenarioHost / ApprovalBridge；把 session service 拆成 Admission / Execution / Query 三个用例服务。

### A-04：控制面与执行面只分了一半

**级别：P0；范围：Worker/Admission。**

- 现状：HTTP 主入口已走 Worker，但 `SessionService.send_message` 仍有内联执行路径，供 coordinator 子会话与冻结用例执行使用（`session_service.py:1352-1387`；`coordinator_runtime_service.py:1433`）。
- 影响：两套接纳、两套租约、两套错误语义；内部路径绕过 Worker 的恢复与背压；测试与生产路径不一致。
- 根因：Worker 化只改了 HTTP 入口，没有把“所有执行必须接纳”上升为不变量。
- 方向：定义 `AdmissionService`；所有执行（HTTP、子 Agent、测试用例、文档任务）都必须产生 durable admission；inline 仅允许测试配置显式开启。

### A-05：没有 Step checkpoint，强杀只能放弃

**级别：P0；范围：会话 turn、Agent 执行。**

- 证据：唯一 `save_snapshot` 在 turn 结束（`session_service.py:2455`）；turn 开始清空 `pending_turn`（`:1690-1701`）；测试明确断言 kill → `recovery_required` + interrupted，`runtime.calls == []`（`tests/test_agent_task_dispatch.py:906-933`）。
- 影响：长模型/长工具被强杀后只能整轮重来或人工处理；重复副作用没有账本可查；长任务“地基强”只对 TestRun 成立，对 Agent Turn 不成立。
- 根因：快照语义是“turn 完成快照”，不是“执行检查点”；没有 tool effect ledger。
- 方向：StepCheckpoint（模型请求摘要、上下文摘要、工具结果引用、审批点）+ EffectLedger（幂等键、状态、结果引用）；从最近完成 Step 续跑；恢复后先查 ledger 再决定重放。

### A-06：工具批次串行、中断粗粒度、审批与执行耦合

**级别：P1；范围：工具执行。**

- 证据：`tool_executor.py:66` 串行 for；中断只在每次调用前检查（`:67-75`）；审批在批次内创建但继续执行后续工具（`:518-529`），finalizer 再挂起（`:45-49`）。
- 影响：浏览器等待、Docker 构建等长工具拖慢整批；用户停止要等当前工具完成；审批语义与批次顺序耦合，难以做并行与抢占。
- 根因：缺少 ToolGateway 的 per-tool 策略（`cancel | block`）与批次调度器。
- 方向：ToolDescriptor 增加 `interrupt_behavior`、`risk_level`、`side_effect_key`；批内按依赖/策略调度；审批作为 gateway 的一步，不是 graph 的旁路。

### A-07：事件面是 PG 轮询，没有统一事件契约

**级别：P1；范围：SSE、Inspector、任务中心。**

- 证据：SSE 每连接 0.5s 轮询 PG，200 条批（`sessions.py:376-430`；`config.py:216-218`）；Redis Streams 只有任务（`redis_task_queue.py:114,208`）；turn 事件直接写 PG（`session_event_sink.py:46-58`）。
- 影响：单机可接受，多实例延迟与连接数线性增长；无法按 run/task 子树订阅；任务中心只能 5s 轮询；Inspector 无法实时。
- 根因：事件只作为“历史回放”，没有作为“可订阅的运行流”。
- 方向：PG 事件账本 + Redis Stream 扇出（先单机、后多实例）；统一 `EventEnvelope`（seq、kind、work_item_id、execution_id、step_id、payload、schema_version）；SSE 按游标回补。

### A-08：上下文一次组装，工具输出引用化不彻底

**级别：P1；范围：上下文工程。**

- 证据：压缩只在 turn 开始前（`runtime_service.py:210-212`）；`world_state` 未接主链；`prompt_cache` 零引用；工具输出超限才给预览（tool job 落全文）。
- 影响：长任务上下文在 Step 中途膨胀；稳定前缀不保证，缓存命中不稳定；工具全文进入上下文的路径仍存在。
- 根因：上下文是“对话消息拼接”，不是“类型化片段 + 预算”。
- 方向：ContextAssembler：稳定前缀（身份/契约/工具排序）→ World State 增量 → 任务状态 → 证据索引 → 最近尾巴 → 历史摘要；每 Step 预算检查；工具输出统一 `summary + ref`。

### A-09：记忆写入过宽，缺少分层与置信度

**级别：P1；范围：记忆。**

- 证据：每轮固定写 user goal、assistant outcome、global semantic、所有 completed tool result（`memory_runtime_service.py:663`）。
- 影响：向量库成为“全量日志”，长期记忆噪声大；无法判断过期/冲突；项目隔离依赖调用方自觉。
- 根因：没有工作/情景/长期三层，没有写入显著性门槛。
- 方向：三层记忆 + 显著性/置信度/来源/ TTL / 冲突合并；写入时机改为 Step（工作）、turn/里程碑（情景）、评审/显式固化（长期）。

### A-10：完成语义混合，证据不是一等公民

**级别：P0（对测试工作台是正确性问题）；范围：验证/评估。**

- 证据：5 种 evidence 形状，无 Evidence 表/聚合；结果 JSONB 内嵌引用；确定性校验覆盖 Agent 自报，但语义校验仍信任模型（`agent_case_assessment.py:486-511`）；没有 4 类状态的分层类型。
- 影响：同一“通过”可能来自 Agent 自述、确定性断言或模型判断，UI 无法区分；回归对比与审计缺少稳定主键；验收无法证明“完成”的依据。
- 根因：证据是结果字段的附属物，不是运行的一等产物。
- 方向：`Evidence` 聚合 + `Verdict`；四轴状态：ExecutionStatus / TestStatus / VerificationStatus / EvaluationStatus；语义判定标注 `model_derived=true` 与置信度，不能与确定性通过混排。

### A-11：变更闭环是五段手工链

**级别：P1；范围：文档 → 影响 → 用例 → 回归。**

- 证据：difference → impact → requirement review → generation → regression 各自独立端点与 ID；文档上传不触发 difference（`routes/api_docs.py:107`）；没有 orchestrator。
- 影响：用户需要手工搬运 5 次；无法回答“这次文档变更最终影响了哪些用例、哪次回归验证了它”；学习闭环断裂。
- 根因：缺一个 ChangeWorkItem 聚合，只有点对点 API。
- 方向：`ChangeWorkItem` 状态机：变更 → 差异 → 影响候选 → 用例修订 → 评审/固定版本 → 新回归 → 对比；链接现有事实，不复制。

### A-12：模块边界被侵蚀，装配靠 790 行 setter

**级别：P0；范围：依赖与组合根。**

- 证据：`core/unified_router.py` 导入 `application`/`registry`（倒置）；`infrastructure → application` 12 处；`runtime ↔ graph` 双向；`main.py:213-1002` 装配；57 个 `set_*`；`containers.py` 与 `main.py` 两套容器；配置 290 字段。
- 影响：循环依赖只能靠 setter 事后补洞；测试无法独立组装子系统；任何新服务都可能要求改 `main.py`。
- 根因：层边界未用依赖规则固化；组合根是“过程式大函数”。
- 方向：分层规则 + CI import gate；`bootstrap/` 按子域组合根，返回构造完成的容器；构造器注入优先，setter 只保留跨生命周期钩子；合并/删除第二容器。

### A-13：死脚手架与文档超前，增加认知负担

**级别：P0；范围：`src/core`、文档。**

- 证据：`tool_router`（176 行）、`prompt_cache`（126）、`agent_manifest`（68）、`orchestration_strategies`（319）、`guardian`（111）、`permission_dsl`（80）零引用；`unified_router` 仅 docstring；`world_state` 仅单测；`stream_turn` 死路径；`hybrid_search` 不存在。
- 影响：新人按文档接线会接错；代码搜索出现“看似有、实际无”的能力；评审无法判断计划与实现差。
- 根因：以“先建层”推进升级，没有“接线/删除”的完成定义。
- 方向：建立 `docs/architecture/CAPABILITY_STATUS.md`（implemented/wired/planned/deleted）；未接线模块二选一：P0 接线，或删除并标注；文档不再把 planned 写成完成。

### A-14：执行面 UI 无法表达运行中意图

**级别：P0；范围：Composer、Timeline、审批、队列。**

- 证据：发送在 busy 时被阻断（`ChatComposer.vue:192,307`）；无停止按钮；`interruptCurrentTurn` 无组件调用（`session.ts:1231-1245`）；队列只在底部 Events tab（`App.vue:158-167`、`EventConsolePanel.vue:19,202-226`）；时间线只保留 24 条（`ChatTimeline.vue:63`）并过滤 tool/system（`:101-105`）。
- 影响：用户看到长任务跑偏时无法停止、无法补充要求、无法看到队列；审批与失败只出现在控制台；工作台不可用。
- 根因：前端把“运行”当作“聊天请求”，没有输入模式与执行事件模型。
- 方向：Composer 永远可用；`发送/插话` + `排队` + 独立 `停止`；队列 chip；审批/插话/中断/失败成为时间线一等事件；Inspector 展示 Step/租约/检查点/证据；时间线按游标分页，不截断。

### A-15：任务中心不是任务中心，状态映射重复 11 套

**级别：P1；范围：TaskPool、Reports、各面板。**

- 证据：`TaskPoolView` 5 类 + 5s 轮询（`:123`）；session 行跳到 `/reports`（`:91`），其他深链 4 段路由；前端 11 处状态映射；无 `/tasks/:id`。
- 影响：同一状态在不同页面文案/颜色不一致；用户无法从一个任务跳到统一详情；新增任务类型要改多处。
- 根因：没有 WorkItem 读模型与统一状态字典。
- 方向：一个后端投影 + 一个前端 `statusCatalog`（由共享 JSON 生成 TS/Python）；`/tasks/:id` 统一详情；源详情作为 section 深链。

### A-16：验收偏功能，缺架构不变量门禁

**级别：P1；范围：测试体系。**

- 证据：526 个后端测试、564 个前端用例，覆盖真实链路；但没有 import 规则、契约兼容、场景 conformance、kill/steer 竞态的统一门禁；恢复测试明确只验证“放弃”。
- 影响：重构后“测试全绿”不代表边界与恢复正确；同一缺陷会在不同模式重复出现。
- 根因：测试按功能组织，没有按架构不变量组织。
- 方向：新增五类门禁：依赖规则、契约 schema、场景 pack conformance、执行不变量（kill/steer/幂等）、事件回放。测试名与验收项一一对应。

### A-17：Schema 演进双轨，启动期 DDL 风险

**级别：P1；范围：存储。**

- 证据：Alembic 仅 4 个迁移版本；80 处 `CREATE TABLE IF NOT EXISTS`；`main.py` 启动期执行 schema 初始化；项目规则明确“不新增 CREATE TABLE IF NOT EXISTS，等待 migration”。
- 影响：schema 变更不可审计、不可回滚；多实例启动竞态；新旧字段漂移；测试库与生产库结构可能不同。
- 根因：migration 机制只覆盖部分新表，旧 store 仍自建。
- 方向：Alembic 成为唯一 schema 源；启动只做 `schema_version` 握手；新表必须 migration；旧 DDL 分批迁移（先只读校验，再切换）。

### A-18：子 Agent 协调双实现、权限边界不清

**级别：P1；范围：Coordinator / Dispatcher。**

- 证据：4 个模式 coordinator + 中央 coordinator（3,588 行）+ dispatcher（1,076 行）都做子任务领取/监听/reaper；`perf` coordinator 定义但 grep 仅见类定义、无实例化；`coordinator_runtime_service` 与 dispatcher 各有 reaper/child watcher。
- 影响：子 Agent 生命周期有两套事实；取消/恢复/配额行为不一致；模式 coordinator 复制权限与并发逻辑。
- 根因：协调器按模式复制，而不是“一个通用子运行编排器 + 场景策略”。
- 方向：`ChildRunOrchestrator` 单实现；场景只提供并发/角色/工具策略；子运行角色只能收窄父权限，不能扩权。

---

## 5. 目标运行模型（Kernel）

### 5.1 对象层级

```text
WorkItem（用户可交付；投影，不复制事实）
  └── Execution（一次可恢复执行；kind adapter 决定语义）
        └── Turn（一次 Agent 采样循环；测试运行可只有 1 个 Turn）
              └── Step（一次模型调用 / 一批工具 / 一条用例步骤+断言）
                    └── Attempt（Step 的重试）
                          └── Evidence（证据，绑定 Step 与 Verifier）
```

### 5.2 映射表（现有业务 → 新对象）

| 现有对象 | WorkItem.kind | Execution.kind | Step 粒度 | 事实源（不迁移） |
|---|---|---|---|---|
| Session Turn | `chat_turn` | `agent_turn` | 模型调用或工具批次 | session / session_events / snapshot |
| 用例规划 | `case_plan` | `planning` | 规划回合 | agent_case_plans / planning_tasks |
| 运行计划修订 | `run_revision` | `planning` | 修订回合 | TestRunPlanRevision |
| 文档差异 | `document_change` | `document_diff` | 解析/比较阶段 | difference task store |
| 文档影响 | `document_change` | `document_impact` | 影响候选阶段 | impact task store |
| 用例生成 | `case_generation` | `generation` | 模块生成 | generation task store |
| 测试运行 | `test_run` | `test_execution` | RunItem 的 Attempt/步骤 | run_store |
| 回归 | `test_run`（parent 关联） | `test_execution` | RunItem | run_store |
| 子 Agent | `child_run` | `agent_turn` | 模型/工具 | agent_task_store |

### 5.3 状态四轴

| 轴 | 取值 | 产生者 | 用途 |
|---|---|---|---|
| `ExecutionStatus` | queued / running / waiting_input / waiting_approval / waiting_resource / cancelled / failed / completed | 控制面状态机 | 调度、UI 进度、恢复 |
| `TestStatus` | not_run / passed / failed / blocked / skipped | Verifier 汇总 | 测试结论 |
| `VerificationStatus` | verified / disputed / unverified / not_applicable | 确定性 verifier | 证据是否支撑结论 |
| `EvaluationStatus` | not_evaluated / accepted / rejected / needs_review | Evaluator/人工 | 质量门与回归准入 |

规则：

- Agent `StepCompleted` 只推进 `ExecutionStatus`，不直接写 `TestStatus`。
- **不单独造第五套 `AgentStatus`**：Agent 的模型/工具进度由 `ExecutionStatus` + `Step/Attempt` 表达；确需展示“Agent 正在思考/调用工具”时，从 Step 类型派生 UI phase，不落独立状态字段。
- `TestStatus=passed` 必须有至少一条 `VerificationStatus=verified` 的证据；否则只能是 `unverified` / `blocked`。
- 语义判定必须标 `model_derived=true`、`confidence`、`rubric_version`，不能与确定性断言合并统计。
- `WorkPhase`（业务阶段）由 kind adapter 从四轴派生，不单独存储两套真相。

### 5.4 统一执行契约（示意）

```python
class ExecutionEnvelope(BaseModel):
    schema_version: Literal[1]
    work_item_id: str
    execution_id: str
    kind: Literal["agent_turn", "planning", "document_diff", "document_impact",
                  "generation", "test_execution"]
    project_id: str
    parent_execution_id: str | None
    attempt_of: str | None
    input_ref: str              # 冻结输入（消息/版本/计划/文档快照）
    lease_token: str | None
    deadline_at: datetime | None
    budget: Budget
    created_at: datetime
```

`StepRecord`、`Checkpoint`、`Evidence`、`Verdict`、`EventEnvelope`、`InputCommand` 都带 `schema_version`，定义在共享契约包；Python/TS 由同一 JSON Schema 生成，禁止两端手写两份。

---

## 6. Scenario Pack：测试场景的扩展契约

### 6.1 包的组成

```text
scenarios/api_contract/
  scenario.yaml          # 元数据 + 能力 + 权限 + 预算 + UI 绑定
  runtime.py             # ScenarioRuntime 实现（plan/execute/verify/resume）
  verifier.py            # 断言/证据映射
  context.py             # 场景上下文片段提供者
  evidence.schema.json   # 证据结构
  ui.panel.ts            # 结果/计划面板（可选）
  fixtures/              # 金样输入/期望输出/失败样本
  conformance.yaml       # 契约测试清单
```

### 6.2 Manifest 契约

```yaml
key: api_contract
version: 2
capabilities:
  - api.validation
  - api.documentation.read
agents: [api-testing-agent, api-executor-worker, api-failure-analyst]
tools:
  - key: api-test-runner
    risk: medium
    interrupt: block
  - key: http-request
    risk: low
    interrupt: cancel
context:
  fragments: [project, api_contract, environment, prior_failures, approvals]
  max_tokens: 12000
execution:
  runtime: api_contract
  plan: required
  approval: medium
  retry: {max_attempts: 2, backoff: exponential}
verification:
  verifiers: [status_code, schema_match, business_rule]
  evidence: evidence.schema.json
  semantic: optional
ui:
  panel: ApiRunPanel
  result_fields: [request, response, assertions, evidence]
conformance:
  - plan_minimal
  - verify_pass_and_fail
  - inject_network_error
  - resume_after_kill
```

### 6.3 Runtime Protocol

```python
class ScenarioRuntime(Protocol):
    kind: str
    async def plan(self, ctx: ScenarioContext) -> PlanDraft: ...
    async def execute_step(self, step: StepInput, ctx: ScenarioContext) -> StepResult: ...
    async def verify(self, step: StepResult, ctx: ScenarioContext) -> list[Evidence]: ...
    async def summarize(self, execution: ExecutionView) -> ScenarioSummary: ...
    async def resume(self, checkpoint: Checkpoint) -> ResumePoint: ...
```

内核只依赖 Protocol，不按 `mode_key` 做 if/elif。注册表从 `scenario.yaml` 自动发现；加载失败即启动失败，不能静默降级。

### 6.4 扩展成本目标

| 动作 | 当前 | 目标 |
|---|---|---|
| 新增一个测试场景 | 改 modes + registry + tool_runtime + 前端状态 + 验证器 | 新增 1 个目录 + 1 个 adapter + 1 个 verifier + fixtures |
| 新增一个断言类型 | 改 `verification_service` 的 if/elif + 模式 verifier | 注册一个 Verifier，不改内核 |
| 新增一个结果面板 | 改 App/ProjectsView/状态映射 | 注册一个 `ui.panel`，任务详情自动挂载 |
| 模式下线 | 删除代码 + 手工清注册 | Manifest 标记 `retired`，启动拒绝路由 |

### 6.5 现有 14 个模式的迁移策略

| 类别 | 模式 | 迁移 |
|---|---|---|
| 已有 runtime | api_testing, ui_automation, performance, security, compatibility, code_review, smoke | P2 逐个包化；先 API/UI，后 security/performance |
| Placeholder | mobile、default 等 6 个 | 标记 `planned`，不注册可执行入口；要么补齐 conformance，要么从产品目录隐藏 |
| 共用能力 | 用例规划、文档链、报告 | 抽为 kernel services，不再归某个模式 |

---

## 7. Harness Ports：控制面职责

### 7.1 Admission 与输入模式

统一命令：

```text
start | start_or_steer | steer(expected_turn_id) | queue | interrupt | resume
```

规则：

- 每条输入有 `client_message_id`（幂等）与 `acceptance_order`（排序）。
- `steer` 只允许作用于指定 turn；turn 已结束时返回 `NotSubmitted(turn_ended)`，客户端自动转新 turn，不静默丢弃。
- `queue` 持久化，turn 结束后作为新 turn 执行；审批等待期间 steer 先排队，审批解决后投递。
- `interrupt` 若存在未投递 steer，返回 `needs_confirmation` 或保留输入，不能静默吞掉。
- 输入不能改变当前 turn 的模型/模式/权限；只对后续 turn 生效（参考 Codex `turn_input.rs:133-142`、`turn.rs:419-434`）。

### 7.2 Worker 与调度

- 所有执行类型使用同一 `ExecutionDispatcher`：PG outbox → relay → Redis Stream → kind worker。
- Worker 只消费 `ExecutionEnvelope`，按 `kind` 找 adapter；adapter 不直接改业务表，必须经控制面 ports。
- 执行前再次校验 lease/attempt/版本；写库失败不得 ACK。
- 子运行是正式执行：有 parent、角色、预算、证据；角色只能收窄父权限。

### 7.3 Checkpoint 与 Effect Ledger

```text
Step 开始：写 checkpoint（输入摘要、上下文摘要、预算、tool ledger 游标）
Step 完成：写 StepRecord + Evidence + 更新 checkpoint
Step 失败：写 Attempt 失败；按策略重试
进程恢复：读最近 checkpoint → 校验 lease → 查 effect ledger → 续跑/补偿/终止
```

`EffectLedger` 键：`(execution_id, tool_key, side_effect_key)`；值：`started/succeeded/failed/unknown` + 结果引用。恢复时：

- `succeeded` → 复用结果，不重放；
- `started` → 先探测外部状态，再决定补写或标记人工；
- `unknown` → 不允许自动重放有副作用的工具。

### 7.4 ToolGateway

统一流水：

```text
Agent 请求 → Schema 校验 → 项目/环境边界 → Policy/风险 → 审批 → 资源/配额
→ 执行（per-tool interrupt/cancel）→ 结果归一化 → Evidence/事件 → 审计
```

ToolDescriptor 增加：`risk_level`、`interrupt_behavior`、`side_effect_key`、`evidence_kind`、`retention_policy`。工具定义按 key 稳定排序，保证 prompt 前缀稳定。

### 7.5 事件与订阅

- `EventEnvelope`：`seq`（每 execution 单调）、`event_id`、`work_item_id`、`execution_id`、`step_id`、`type`、`payload`、`schema_version`、`created_at`。
- PG 为权威账本；Redis Stream 做扇出；SSE 先订阅后启动执行，按 `Last-Event-ID` 回补，终态必须可达。
- 事件分层：控制事件（必达）、进度事件（可合并）、调试事件（可采样）。UI 不得只依赖可丢弃事件判断终态。

### 7.6 Evidence / Verifier / Evaluator

- `Evidence`：`id, execution_id, step_id, kind, source, content_hash, uri, summary, payload_ref, created_at, retention, provenance`。
- `Verdict`：`evidence_ids, verifier_key, verifier_version, result, confidence, model_derived, reason, created_at`。
- 确定性 verifier 优先；语义 verifier 只能产出 `disputed`/`needs_review`，不能单独把测试判为 `passed`。
- Evaluator 读取用例版本、计划、证据、基线，产出 `EvaluationStatus`；回归准入由 Evaluation 决定，不由 Agent 自述。

### 7.7 Context 与 Memory

**Context 固定顺序**：

1. 稳定前缀：身份、执行契约、场景 Manifest、工具定义（稳定排序）。
2. World State 增量：项目/环境/权限/资源/任务阶段，带版本与哈希。
3. 任务状态：计划、进度、待办、阻塞。
4. 证据索引：摘要 + 引用，不塞全文。
5. 最近对话尾巴。
6. 历史摘要。

每 Step 检查预算；压缩在 Step 边界，不在 turn 开始一次性做；工具输出统一 `summary + ref`；prompt cache 只对稳定前缀生效，断点显式标记。

**Memory 三层**：

| 层 | 内容 | 生命周期 | 写入 |
|---|---|---|---|
| 工作记忆 | 当前 execution/step 状态 | execution TTL | 每 Step |
| 情景记忆 | 会话摘要、关键结论、失败模式 | session/project | turn 收尾、里程碑 |
| 长期记忆 | 跨项目测试经验、页面/API 知识 | 长期，带 TTL | 评审通过/显式固化 |

长期记忆必须含：来源证据、置信度、项目/环境隔离、过期与冲突合并策略。

### 7.8 资源与配额

沿用现有 resource lease，但纳入统一 Execution：浏览器、Docker、测试账号、环境、设备按 `project + resource_type` 申请；租约与 Step 绑定，长工具独立续租；释放写入 cleanup 队列与事件。

---

## 8. 测试域架构

### 8.1 资产生命周期

```text
项目
  → 需求/文档/API/录制/知识
  → 用例草稿（版本）
  → 评审（通过/拒绝）
  → 启用固定版本
  → 套件冻结（版本）
```

规则：

- 用例版本不可变；评审与启用是事件，不是改字段。
- 套件只引用固定版本；冻结后不可漂移。
- 资产状态与执行状态分离：草稿用例不因一次运行失败而“失败”。

### 8.2 执行生命周期

```text
意图 → 计划（候选/评审/修订） → 运行 → RunItem 领取 → Step/Attempt
→ 证据 → Verdict → TestStatus → Evaluation → 回归
```

规则：

- 已领取/已执行条目保留原快照；修订只影响未执行部分（现有行为保留）。
- Attempt 不复用；每次重试新 ID。
- 终态由 verifier/evaluator 裁决；Agent 自述只写 Step。
- 运行失败可创建回归，但不得覆盖原结果（现有约束保留）。

### 8.3 ChangeWorkItem（变更工作项）

```text
文档版本上传
  → 差异（结构化）
  → 影响候选（用例/需求/接口）
  → 用例修订（新草稿版本）
  → 评审 + 固定版本
  → 新回归运行
  → 与基线对比
```

一个聚合、一个详情页、一组链接；每阶段有入口条件、允许动作、终态、证据、超时/恢复。现有 difference/impact/generation/run 表保留为事实，ChangeWorkItem 只链接。

### 8.4 环境与证据

- 固定环境快照继续使用：目标配置 + revision + sha256；部署状态保持 `unknown`，不得由快照推断已部署。
- 证据必须带环境身份（environment_id、snapshot_id、hash）与时间。
- UI 断言/接口断言/DOM/截图/HAR/控制台日志/退出码统一映射到 Evidence kind，不各自定义结构。

### 8.5 统一审批

当前存在两类审批：会话工具审批，以及 TestRun 的 RunItem 等待审批。用户看到的是两个入口、两套状态、两套恢复路径。目标不是合并业务语义，而是统一**审批契约**：

```text
ApprovalEnvelope
  approval_id, project_id, execution_id, step_id, kind (tool | run_item | change)
  requested_by, risk_level, scope (once | step | execution | capability)
  payload_ref, expires_at, status (pending | approved | denied | expired | cancelled)
  decision_by, decision_at, reason, idempotency_key
```

规则：

- 审批是 Execution 的 Step，不是旁路；审批未决时执行进入 `waiting_approval`，checkpoint 记录审批身份。
- `scope` 必须可收窄，不能因一次批准而扩权；跨运行复用审批必须被拒绝。
- steer 在审批等待期间只能排队，不能绕过审批；interrupt 不得静默丢弃待审批项。
- 两类审批在 UI 统一为一个“待处理”队列，点击后定位到对应 Execution 的审批 Step。
- 审批结果与工具执行、RunItem 状态写入同一事件账本，可回放、可审计。

---

## 9. 工作台信息架构（UI）

### 9.1 四个面

| 面 | 目标 | 主要页面 |
|---|---|---|
| 执行面 | 看一次执行、随时干预 | `/sessions/:id`（时间线 + Inspector） |
| 调度面 | 看所有工作项 | `/tasks`、`/tasks/:id` |
| 资产面 | 管测试资产与版本 | `/projects/:id/overview|cases|suites|runs|documents|environment` |
| 设置 | 模型、权限、配额、场景 | `/settings/*` |

### 9.2 执行面

- 两栏：时间线 + 可折叠 Inspector。
- Composer 永远可用；`发送/插话` 与 `排队` 可选；独立 `停止`；队列 chip 在输入框上方。
- 审批、插话、中断、失败、恢复都是一等时间线事件；不再藏在底部控制台。
- Inspector：阶段、Step、工具、租约、checkpoint、证据、Trace 深链；原始日志降为 debug 抽屉。
- 时间线按游标分页/回补，不截断 24 条；支持按 Step 折叠；长任务可跳转到最早未读。

### 9.3 调度面

一张高密度行表：任务、类型、阶段、状态（四轴缩写）、项目、负责人、等待原因、尝试、证据数、最后心跳、操作。  
统一 `/tasks/:id`：概览、计划、步骤、证据、日志、源详情深链。  
任务池从 5s 轮询改为事件驱动 + 断线回补；无事件时降级轮询。

### 9.4 资产面

- `ProjectsView` 拆路由与组件：目录、概览、用例、套件、运行、文档、环境各自页面；禁止继续堆单文件。
- 用例详情以版本为中心：内容、评审、运行历史、证据；不只展示 JSON。
- 运行详情以证据为中心：需求 → 用例版本 → 步骤/断言 → 证据 → Verdict；失败可直接创建回归。
- 环境页：目标配置 + 快照 + 部署状态未知 + 使用记录。

### 9.5 状态与文案单一来源

- 后端生成 `status_catalog.json`（四轴 + 业务阶段 + 颜色/文案键）。
- 前端由 JSON 生成 TS 常量；后端由 JSON 生成枚举校验。
- 删除 11 套手写映射；新增状态必须改 catalog，CI 校验两端一致。

---

## 10. 模块边界与“减法清单”

### 10.1 目标分层

```text
kernel/           纯契约、状态机、ID、错误码（不依赖业务与基础设施）
domain/           测试资产、计划、运行、证据的领域模型与策略
application/      用例服务：admission、execution、query、verification、evaluation
adapters/         postgres、redis、model、browser、docker、object-store、memgraph
workers/          agent / test / document / generation / cleanup 入口
api/              FastAPI 路由（只做传输与鉴权）
bootstrap/        按子域组合根（runtime、testing、documents、recording、observability）
```

依赖规则：`kernel ← domain ← application ← adapters/workers/api/bootstrap`；禁止反向；`graph/` 视为 agent adapter 的实现细节，不再承载运行时职责。

### 10.2 CI 门禁

- `import-linter`：禁止 `core → application`、`infrastructure → application`、`runtime ↔ graph` 循环。
- 文件预算：单文件 > 1,500 行必须拆分或登记例外；新增例外需架构评审。
- 契约测试：共享 schema 生成 Python/TS，snapshot 兼容性检查。
- 场景 conformance：每个 pack 必须通过统一清单。
- 执行不变量：kill/steer/幂等/回放。

### 10.3 删除/合并清单

| 对象 | 处理 | 理由 |
|---|---|---|
| `core/tool_router.py` | 接线到 ToolGateway，或删除 | 零引用，与工具批次重复 |
| `core/prompt_cache.py` | 接入 ContextAssembler，或删除 | 零引用 |
| `core/agent_manifest.py` | 由 Scenario Pack Manifest 取代 | 零引用，职责重叠 |
| `core/orchestration_strategies.py` | 由 ChildRunOrchestrator 策略取代 | 零引用 |
| `core/guardian.py` / `permission_dsl.py` | P0 接线或删除；权限并入 Policy | 零引用 |
| `core/unified_router.py` | 删除（或移到 application 并接线） | 基础层倒置依赖 |
| `core/world_state.py` | 改为 ContextFragment provider 或删除 | 仅单测 |
| `containers.py` | 合并进 `bootstrap/` | 第二装配面 |
| `RunStore` 内存完整实现 | 改为契约 fake | 双状态机漂移 |
| 3 个模式 task pool | 合并为一个 TaskStore | 重复状态与清理 |
| 4 个模式 coordinator | 合并为 ChildRunOrchestrator + 策略 | 重复 reaper/watcher |
| `stream_turn` | 删除或正式接入 | 死路径 |
| 6 个 placeholder 模式 | 标记 `planned` 并从可执行目录隐藏 | 避免“看似可用” |

---

## 11. 数据、契约与 Schema 演进

### 11.1 契约版本化

| 契约 | 版本字段 | 消费者 |
|---|---|---|
| WorkItemProjection | `schema_version` | API / Task UI |
| ExecutionEnvelope | `schema_version` | Dispatcher / Worker |
| StepRecord / Checkpoint | `schema_version` | Worker / Recovery |
| Evidence / Verdict | `schema_version` | Verifier / UI / 回归 |
| EventEnvelope | `schema_version` | SSE / Inspector / Task Center |
| InputCommand | `schema_version` | API / Admission |
| ScenarioManifest | `contract_version` | Registry / Kernel / UI |

规则：新增字段向后兼容；删除/改语义升版本；消费者遇到未知版本拒绝，不做“尽量解析”。

### 11.2 迁移策略

1. 启动只做 schema 版本握手，不在 lifespan 里建表；缺失版本直接失败并输出迁移命令。
2. 新表/新列一律通过 Alembic；禁止新增 `CREATE TABLE IF NOT EXISTS`。
3. 旧 store 的 80 处 DDL 分批迁移：先加迁移与影子校验，再切换，最后删除 DDL；每批必须可在测试库回滚。
4. WorkItemProjection 起步为 SQL VIEW（不新增事实表），验证查询性能后再物化；物化表只存投影与版本，不存业务事实。
5. 新表（P1 起，均经 Alembic）：
   - `execution_checkpoints`（execution_id, step_seq, state_ref, context_digest, lease_token, created_at）
   - `tool_effect_ledger`（execution_id, tool_key, side_effect_key, status, result_ref, updated_at）
   - `evidence`（execution_id, step_id, kind, content_hash, uri, summary, provenance）
   - `verdicts`（evidence_ids, verifier_key/version, result, confidence, model_derived）
   - `input_commands`（client_message_id, mode, expected_turn_id, acceptance_order, status）
   - `work_item_projection`（source_kind, source_id, parent_id, phase, four-axis summary）
6. 所有写入带 `project_id`、`lease_token`、`version`；跨项目读写必须显式校验。

### 11.3 一致性

- Outbox → Redis → Worker 至少一次；ACK 前必须完成事实写入；终态事件与事实同一事务。
- 事件账本与投影可重建；投影不是权威。
- 回归运行不得覆盖原结果；对比是新增对象。

---

## 12. 与成熟 Agent 的对照

| 模式 | 参考实现 | 当前差距 | 采纳方式 |
|---|---|---|---|
| Typed steering | Codex `protocol/src/turn_input.rs:133-142`，`turn.rs:419-434` | 只有 queue/reject/interrupt_then_retry；无 expected_turn_id | 采用四模式 + 持久化队列 + turn 边界 drain |
| Per-tool interrupt | Claude `restored-src/src/Tool.ts:406-416`，`StreamingToolExecutor.ts:221-240` | 批次统一检查，长工具无法取消/必须等待 | ToolDescriptor 增加 `cancel/block` |
| Durable journal | Grok `xai-grok-session-events/src/types.rs:420-430`；Codex `rollout/src/recorder.rs:1156-1172` | 只有 turn 末快照 | Step checkpoint + 启动对账 |
| Tool result references | Claude `toolResultStorage.ts:1-3,377-400,769-790` | 超限才落 ToolJob；前缀不稳定 | 摘要 + 引用 + 稳定排序 + 冻结替换决策 |
| Approval pipeline | Codex `tools/orchestrator.rs:1-10`，`tools/registry.rs:294-338` | 审批与批次执行交错 | Gateway 内统一 approval→执行→升级 |
| Worker authority | Codex `agent/role.rs:1-5` | 子运行可配置，但无“只能收窄”不变量 | 子运行角色收窄父权限 |
| Replayable stream | Warp `agent_events/driver.rs:17,50-70` | PG 轮询，无游标扇出 | EventEnvelope + 游标 + 回补 |
| Inspector IA | Warp `agent_management/view.rs:146-180`，`conversation_details_panel.rs:1418+` | 底部 8 tab + 24 条轨迹 | 时间线 + Inspector + Task Detail |
| Versioned test assets | Warp `drive/workflows/arguments.rs:8-27`；`integration_testing/workflow/step.rs:16-20` | 用例有版本，但场景/参数未统一 | Scenario Pack + typed params |
| 不照搬 | Codex 118 crates / 6,457 行 protocol；Claude 文件 Agent primitive；Warp 2,400 行单体视图 | — | 只借边界，不借字段与规模 |

---

## 13. 迁移路线

> 原则：strangler（绞杀者）而非大爆炸；每阶段可独立上线、可回滚；不破坏现有用例驱动链路与 `<untrusted_content>` 隔离。

### P0：收敛与可操作性（2–3 个迭代）

| 编号 | 交付 | 验收 | 回滚 |
|---|---|---|---|
| P0-1 | `docs/architecture/CAPABILITY_STATUS.md`；死模块接线或删除；import-linter 门禁；文件预算 | 零死引用；CI 依赖规则通过；文档不再把 planned 写成完成 | 保留删除前分支/标签 |
| P0-2 | 冻结 WorkItem/Execution/Step/Attempt/Evidence/InputCommand/EventEnvelope/ScenarioManifest 契约；WorkItem SQL VIEW；统一状态字典 | 任务中心一个投影、一个状态目录；前端 11 套映射归一 | VIEW 可回退到旧 UNION |
| P0-3 | 输入四模式 + 持久化队列 + 停止按钮 + 队列 chip + 时间线一等事件 | 模型流中/工具中/turn 结束竞态全部通过；输入零丢失；停止有实际受理状态 | feature flag 关闭 steer，保留 queue |
| P0-4 | 所有执行统一 Admission；内部 inline 仅测试开关；Worker 接管测试 | API 重启不中断长任务；kill worker 可接管；无第二接纳路径 | 开关切回 inline（仅短期） |

### P1：可恢复与证据化（4–6 个迭代）

| 编号 | 交付 | 验收 | 回滚 |
|---|---|---|---|
| P1-1 | StepCheckpoint + EffectLedger + 恢复协议 | 模型/工具/审批三个位置强杀后从最近 Step 续跑；重复副作用 0 | 关闭 checkpoint，回退转整轮重试 |
| P1-2 | Evidence/Verdict 表 + 四轴状态 + 确定性优先 | 通过必须带证据；语义判定标 model_derived；UI 可下钻 | 双写旧 result 字段；读路径开关 |
| P1-3 | ContextAssembler + 工具引用 + 稳定前缀 + Step 预算 | 长任务上下文占比可控；缓存前缀稳定；压缩后任务可继续 | 回退旧消息拼接 |
| P1-4 | 事件账本 + Redis Stream 扇出 + SSE 游标 + Inspector | 多实例 <1s 推送；断线回补；终态可达 | 回退 PG 轮询 |
| P1-5 | 记忆三层 + 显著性/TTL/冲突 | 记忆命中准确率可测；过期可识别 | 关闭长期记忆写入 |

### P2：场景包与结构收敛（6–8 个迭代）

| 编号 | 交付 | 验收 | 回滚 |
|---|---|---|---|
| P2-1 | Scenario Pack v2 + Runtime Protocol + 自动注册 | 新增场景不改内核；7 个现有 runtime 逐个通过 conformance；placeholder 明确隐藏 | 旧 registry 双读 |
| P2-2 | 合并 4 个 coordinator → ChildRunOrchestrator；3 个模式 task pool → TaskStore；RunStore 双实现 → 契约 + fake | 子运行取消/恢复/配额一致；状态机单实现 | 按模式开关回退 |
| P2-3 | 拆分 tool_runtime / session_service / run_store；bootstrap 组合根；删除 setter 债 | 单文件预算达标；无循环依赖；测试可独立组装 | 原文件保留包装层 |
| P2-4 | ChangeWorkItem 聚合与详情 | 一次文档变更可追踪到用例版本与回归结果 | 保留旧端点 |

### P3：工作台与规模化（持续）

| 编号 | 交付 | 验收 |
|---|---|---|
| P3-1 | 四区 IA、`/tasks/:id`、项目路由拆分、证据中心 | 一个任务一个详情页；URL 可深链；项目详情不再巨型文件 |
| P3-2 | 多实例 SSE、配额、SLO、chaos/soak | 4h/24h 长稳；租约丢失/接管/重复副作用指标达标 |
| P3-3 | 场景生态与评测 | 新增场景成本 ≤ 1 个包；质量门以 Evidence/Evaluation 为准 |

### 13.1 阶段退出条件

- 任一阶段未通过“执行不变量 + 场景 conformance + 事件回放”三项，不得进入下一阶段。
- 每个 P0/P1 交付必须同时更新 `CAPABILITY_STATUS.md` 与验收证据目录。
- 不允诺“全量测试绿”代替恢复/竞态/证据验收。

---

## 14. 验收与指标

### 14.1 必测不变量

| 不变量 | 测试 |
|---|---|
| 单写者 | 双 worker 抢同一 execution；旧 lease 写入被 fencing 拒绝 |
| 输入零丢失 | 模型流中 steer、工具中 steer、turn 结束瞬间 steer、两条 steer 顺序/去重 |
| 可恢复 | 模型请求前/后、工具前/后、审批等待中 kill；从最近 Step 续跑 |
| 副作用幂等 | 恢复时工具已成功/未知；重复执行次数为 0 |
| 终态证据 | 无证据不能 passed；语义判定不能单独 passed |
| 事件回放 | 断线后按游标补到终态；重复事件不改变状态 |
| 场景契约 | 每个 pack 通过同一 conformance 清单 |
| 依赖规则 | import-linter 零违规；文件预算 |
| Schema | 版本握手；旧客户端拒绝未知版本 |

### 14.2 目标成功标准（Before / After）

| 维度 | 当前 | 目标 | 验证方式 |
|---|---|---|---|
| 新增测试场景 | 改 modes + registry + tool runtime + 前端 + 验证器 | 新增 1 个 Scenario Pack 目录 | 记录改动文件数与 conformance |
| 强杀恢复 | Agent turn 放弃/整轮重来；TestRun 可接管 | 所有 execution 从最近 Step 续跑 | 三类 kill 点测试 |
| 运行中输入 | queue / reject；无停止按钮 | steer / queue / interrupt + 0 丢失 | 四类竞态测试 |
| 完成语义 | Agent 自述 + 混合校验 | 四轴 + Evidence/Verdict | 通过项证据覆盖率 100% |
| 任务中心 | 5 表 UNION + 6 套入口 | 1 个投影 + `/tasks/:id` | 统一状态目录一致性 |
| 状态映射 | 11 套前端手写 | 1 份 catalog 生成两端 | CI 快照 |
| 实时事件 | 0.5s PG 轮询 | 事件流 + 游标回补 | 多实例延迟与断线测试 |
| 死脚手架 | 6 个零引用 + 3 个仅注释/仅单测/不存在 | 0 个“文档有、代码无” | import/引用扫描 |
| 单元边界 | 790 行装配 + 57 setter | 子域 bootstrap + 规则门禁 | import-linter + 文件预算 |

### 14.2 指标

| 类别 | 指标 |
|---|---|
| 执行 | 队列等待 P50/P95；Step P95；恢复成功率；重复副作用次数（目标 0）；租约丢失次数 |
| 交互 | 停止受理延迟；steer 在下一步生效比例；输入丢失（目标 0）；队列可见率 |
| 上下文 | 每 Step 上下文占比；压缩前后 token；缓存命中率；工具引用化比例 |
| 证据 | 通过项证据覆盖率（目标 100%）；Verifier 与 Agent 分歧率；语义判定占比 |
| 场景 | 新增场景改动文件数；conformance 通过率；placeholder 暴露数（目标 0） |
| UI | 任务详情可达率；状态映射一致性；时间线回补成功率 |
| 稳定性 | 4h/24h soak；多实例 SSE 延迟；资源回收成功率 |

---

## 15. 风险、约束与非目标

### 15.1 风险

| 风险 | 缓解 |
|---|---|
| 一次性大改导致回归 | strangler + feature flag；每阶段独立上线 |
| WorkItem 投影成为第二事实源 | 只读投影 + 源表事实；投影可重建；禁止在投影上写业务状态 |
| Step checkpoint 体积膨胀 | 存引用与摘要，不存全量上下文；定期 compaction |
| 场景包抽象过度 | 先迁移 2 个模式验证，再推广；不追求“所有模式同构” |
| 迁移 80 处 DDL 破坏现有库 | 影子校验 + 备份 + 分批；启动只握手 |
| 事件多实例一致性 | PG 权威 + Redis 扇出；游标回补；终态必达 |
| 语义验证被误当确定性 | 强制 `model_derived` 标记；UI 分栏；质量门只接受确定性证据 |

### 15.2 与现有规则的冲突与处理

项目规则当前要求：

- 不新增 LangGraph 节点；
- 不新增硬编码 Agent；
- 不新增独立 coordinator；
- 不新增 `CREATE TABLE IF NOT EXISTS`；
- 保留 `<untrusted_content>` 与用例驱动链路。

本方案的处理：

- **不新增 LangGraph 节点**：P0/P1 不改图；P2 才评估用显式 Step 执行器替代“图外 while”，届时需修订规则。
- **不新增硬编码 Agent**：Scenario Pack Manifest 取代 `registry/agents.py` 字面量。
- **不新增独立 coordinator**：ChildRunOrchestrator 合并现有实现，不增加第 5 套。
- **不新增原始 DDL**：所有新表走 Alembic；WorkItem 起步为 VIEW。
- **保留 `<untrusted_content>` 与用例链路**：作为不可协商的回归基线。

### 15.3 非目标

- 不做通用编码 Agent；不引入文件/仓库为中心的 primitive 体系。
- 不追求把 LangGraph 换成自研大框架；先收敛边界，再评估替换。
- 不做全站 UI 一次性重写；P0 只做执行面，P2 再做资产面。
- 不为“未来可能的多租户/多区域”预建抽象；先满足单产品多项目、多实例。
- 不把“Agent 自述完成”包装成测试通过。

### 15.4 需要用户拍板的决策

1. **P0 范围**：是否接受“先做输入模式 + 统一任务投影 + 死代码清理”，暂不碰场景包？
2. **LangGraph 去留**：P2 是否允许把“图外 while”替换为显式 Step Executor（会触及现有节点约束）？
3. **WorkItem 投影**：接受 SQL VIEW 起步，还是直接物化表（需 Alembic）？
4. **数据库迁移窗口**：80 处 DDL 迁移是否允许分批在测试库先行、生产灰度？
5. **多实例时间线**：P1 是否必须支持多 API 实例 SSE，还是先单实例、保留契约？

### 15.5 本次审查边界

**已核实（静态证据）**：

- 当前工作区源码、配置、表结构与测试文件；关键路径均给出 `文件:行号`。
- 现有架构文档与项目规则；与现状冲突处已在第 0 章校正。
- 本地成熟 Agent 参考实现的关键代码位置；只提取边界与机制，不做功能对等比较。
- 观察到的测试规模：后端 526 个测试文件；前端 77 个测试文件 / 564 个用例（最近一次全量运行结果，非本次重跑）。

**未验证（不能据此宣称已通过）**：

- 实际部署拓扑：API/Worker/Relay 的进程数、实例数、Redis/PG 容量与多实例行为。
- 生产数据分布：任务量、长任务比例、审批等待时长、失败模式、租户/项目规模。
- 运行时行为：本次未启动服务、未执行真实模型/工具/浏览器；所有恢复、竞态、性能结论都需要 P0/P1 验收测试证明。
- 多实例 SSE、Redis Streams 扇出、4h/24h 长稳、浏览器/Docker 池上限。
- 场景包迁移后的真实扩展成本；本文给出的“1 个目录”是设计目标，不是已验证结果。
- 旧 DDL 迁移对现有数据库的兼容性与停机窗口。

本文是架构审查与迁移方案，不是发布验收报告。任何“已完成”表述都以代码/测试证据为准；未验证项在实施阶段必须补证。

---

## 16. 附录：证据索引

### 16.1 执行与持久化

- `Agent_Server/src/api/routes/sessions.py:148-157` — HTTP messages → dispatcher。
- `Agent_Server/src/application/orchestration/agent_task_dispatcher.py:78-136` — 任务提交。
- `Agent_Server/src/cli/run_agent_worker.py:18-35` — Agent Worker 消费。
- `Agent_Server/src/application/sessions/session_service.py:1352-1387` — 内部内联路径。
- `Agent_Server/src/application/runtime/agent_loop.py:45-96` — 图外 ReAct 循环。
- `Agent_Server/src/application/runtime/agent_loop.py:98-215` — 死路径 `stream_turn`。
- `Agent_Server/src/graph/builder.py:74-154` — 9 节点与边。
- `Agent_Server/src/graph/nodes/tool_executor.py:66-75,518-529` — 串行工具与审批。
- `Agent_Server/src/api/routes/sessions.py:376-430` — SSE 轮询。
- `Agent_Server/src/core/config.py:216-218,248-250` — 轮询/租约配置。
- `Agent_Server/src/application/sessions/session_service.py:1690-1701,2455` — pending_turn 清空与 turn 末快照。
- `Agent_Server/src/runtime/postgres_session_store.py:2131-2165` — lease 过期 → interrupted。
- `Agent_Server/tests/test_agent_task_dispatch.py:906-933` — kill 后放弃的验收断言。

### 16.2 领域与运行

- `Agent_Server/src/application/test_runs/run_store.py:2644,2900,2924,2953,3437,3522` — claim/heartbeat/checkpoint/complete/expire/cancel。
- `Agent_Server/src/schemas/run_management.py:16-42,230` — 状态枚举与 evidence ref。
- `Agent_Server/src/application/test_runs/agent_case_assessment.py:486-511` — 确定性/语义裁决。
- `Agent_Server/src/schemas/task_pool.py:26` — 5 类任务。
- `Agent_Server/src/runtime/postgres_task_pool_store.py:70-138` — UNION 任务池。
- `Agent_Server/src/modes/*/manifest.py` — 模式 Manifest。
- `Agent_Server/src/registry/modes.py:5-18,29-44` — 硬编码模式注册。
- `Agent_Server/src/registry/agents.py:16-878` — 45 个 Agent 字面量。
- `Agent_Server/src/application/runtime/tool_runtime_service.py:2894-2915,3905` — 模式 if/elif。
- `Agent_Server/src/application/test_runs/run_store.py:220-1455,1456-4124` — 双实现。

### 16.3 证据与变更

- `Agent_Server/src/schemas/session.py:104-123` — VerificationStatus/Evidence。
- `Agent_Server/src/schemas/case_planning.py:277` — PlanningEvidence。
- `Agent_Server/src/application/test_runs/case_execution.py:248` — CaseToolEvidenceReference。
- `Agent_Server/src/application/test_runs/execution_service.py:1248-1288` — 验证结果双写。
- `Agent_Server/src/api/routes/document_differences.py:31`、`document_impacts.py:66`、`run_management.py:317,333` — 手工变更链。

### 16.4 边界与装配

- `Agent_Server/src/main.py:213-1002,1059-1093` — 790 行装配与 35 路由。
- `Agent_Server/src/containers.py` — 第二容器。
- `Agent_Server/src/core/unified_router.py:18-24,68` — 倒置依赖/仅 docstring。
- `Agent_Server/src/core/tool_router.py`、`prompt_cache.py`、`agent_manifest.py`、`orchestration_strategies.py`、`guardian.py`、`permission_dsl.py` — 零引用。
- `Agent_Server/src/runtime/postgres_agent_task_store.py:248` — “TaskEnvelope 字段”注释，实际无统一契约。
- `Agent_Server/alembic/versions/`（4 个）与 80 处 `CREATE TABLE IF NOT EXISTS`。

### 16.5 前端

- `agent_web/src/App.vue:144-153,155-201,268-329` — 8 tab 控制台。
- `agent_web/src/components/ChatComposer.vue:192,269-274,307` — busy 发送阻断/无停止。
- `agent_web/src/stores/session.ts:590-602,719-722,1231-1245` — busy/queue/interrupt。
- `agent_web/src/components/ChatTimeline.vue:63,101-105` — 24 条与 tool/system 过滤。
- `agent_web/src/views/TaskPoolView.vue:50-56,79-93,123` — 5 类/5s 轮询/跳转。
- `agent_web/src/views/ProjectsView.vue` — 2,612 行。
- `agent_web/src/router/index.ts` — 16 路由，无 `/tasks/:id`。
- `agent_web/src/features/workbench/plugins.ts` — 仅 idle/conversation 两个插件。

### 16.6 参考实现

- `DeepAgent-Studio/借鉴/codex/codex-rs/protocol/src/turn_input.rs:133-142` — 四类输入模式。
- `DeepAgent-Studio/借鉴/codex/codex-rs/core/src/session/turn.rs:419-434` — turn 边界 drain。
- `DeepAgent-Studio/借鉴/codex/codex-rs/core/src/tools/orchestrator.rs:1-10` — 审批/沙箱/重试流水线。
- `DeepAgent-Studio/借鉴/claudecode/restored-src/src/Tool.ts:406-416` — per-tool interrupt。
- `DeepAgent-Studio/借鉴/claudecode/restored-src/src/services/tools/StreamingToolExecutor.ts:221-240` — 取消/阻塞策略。
- `DeepAgent-Studio/借鉴/claudecode/restored-src/src/utils/toolResultStorage.ts:1-3,377-400,769-790` — 工具结果引用与稳定替换。
- `DeepAgent-Studio/借鉴/grok-build/crates/codegen/xai-grok-session-events/src/types.rs:402-430` — Phase/中断对账。
- `DeepAgent-Studio/借鉴/warp/app/src/ai/agent_events/driver.rs:17,50-70` — 事件流重连/子树过滤。
- `DeepAgent-Studio/借鉴/warp/app/src/ai/agent_management/view.rs:146-180` — 运行列表。
- `DeepAgent-Studio/docs/system-harness-audit-2026-10-21.md` — 审查格式与问题分级参照。

---

## 17. 结论

本系统的下一阶段不应继续“补八层”，而应完成三件事：

1. **收敛**：一个执行本体、一个事件契约、一套证据裁决、一套任务投影；删除或接线所有死脚手架。
2. **包化**：把 14 个测试场景从“主链 if/elif + 硬编码注册”迁移为 Scenario Pack；新增场景不改内核。
3. **工作台化**：让用户能停止、插话、排队、看队列、看步骤、看证据；任务中心与资产中心成为真正的工作对象，而不是开发调试面板。

完成这三件事后，系统的扩展成本才会从“每加一个场景改 N 处”降为“新增一个包”；长任务能力才会从“TestRun 很强、Agent 很弱”变成同一套不变量；UI 才会从“控制台”变成测试工作台。
