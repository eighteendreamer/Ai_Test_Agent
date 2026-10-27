# LangGraph / Deep Agents 迁移实施记录

日期：2026-10-25 起。当前实施依据：[完整 v4 方案](G:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/docs/langgraph-deepagents-integration-plan-v4-2026-10-27.md)；[v3 方案](langgraph-deepagents-integration-plan-v3-2026-10-26.md)及[v2 方案](langgraph-deepagents-integration-plan-v2-2026-10-25.md)保留历史。

**当前子步：v4 P0 的原生消息比较失败收敛（§54），承接 §52 已提交实现和 §53 原生终态草稿。已复现并修正固定版本 Delta 回放排序根因，保留消息严格比较；只读终态证明不授予应用恢复权。无原结果资产的自动恢复仍未开放；下一步按 §53 材料清单冻结应用投影输入并复用确定性投影器，再闭合 A2-3 projected→任务结算。完整 A2/A3/B/C/D/E/F 均不得用本子步替代；生产默认关闭。**

2026-10-26 附件再复核见 §37；冻结请求恢复见 §38，语义输入冻结见 §39，结算响应绑定及已结算 DTO 只读导入见 §40，语义服务消费原结算见 §41，冻结语义输入的应用编排消费见 §42，orchestrating 安全后继与代码制品指纹见 §43，Deep Agents 图输入资产见 §44。各阶段记录保留当时结果，以本页顶部快照及最新阶段的限定范围为准。

**历史快照（截至 §44，不代替最新 §50）：已有 P0 基础、RuntimeBinding/版本化任务、owner/checkpoint 事务保护、原生决定交付和业务应用证明、v2 审批 API/Worker/逐项前端、已知结果审批/对账自身失租的后继与独立进程强杀恢复，以及根模型账本、主代理/具名子代理/摘要和 owner 作用域内 DTO 请求策略、前置语义分类与预算受理/Worker/生产组装接线、应用层与 HTTP wire 模型证据（OpenAI/Gemini 图内客户端注入）。首次消息阶段准入/只读诊断、submitted/orchestrated 安全后继、语义输入冻结、未发送预留显式转移、已结算 DTO 响应只读导入、语义服务消费入口、orchestrating 阶段的有证明后继及 Deep Agents 图输入资产已局部落实。自动生成绑定及预算、图结果导入、活动时间计量、跨 Turn 上下文、完整图内故障恢复、真实发布与逐模式验收仍未完成；后文各节的验证结论仅适用于其阶段。**

## 1. 阶段与现状

| 阶段 | 本轮状态 | 下一出口 |
| --- | --- | --- |
| P0 依赖与模型契约 | 已锁依赖、建立独立第三方环境；本地 HTTP/SSE 通过真实官方 SDK 验证四传输 | 真实供应商配置、目标部署环境及 workspace wheel 构建仍待验收 |
| P1 状态、输入、预算、版本绑定 | 已完成事件队列移出状态、RuntimeBinding 与 v2 task/outbox 隔离、原生 transcript、v2 Worker 最小消费闭环、owner 与物理 namespace 绑定；§25–28 增加根模型账本、官方主/子代理/摘要及 owner 下 DTO 请求策略、前置语义分类和预算受理/Worker/生产组装接线 | 自动生成绑定/预算、活动时间计量、跨 Turn 上下文实链路与完整制品校验 |
| P2 逐项审批、所有权、对账 | 已接入官方 per_call、owner checkpoint/同步 ToolJob 执行权、receipt/proof、v2 API/Worker/Runtime、持久前端进度，以及已知结果审批和对账自身失租的后继强杀 Harness（§12–24） | 首次 message、未知效果查询/人工对账、其余故障窗口、领域 Runner 执行权及真实浏览器验收 |
| P3 代码审查实链路 | 待实现 | Prompt、Skills、只读探索、受治理子代理、流式事件及质量基线 |
| P4 逐模式迁移 | 待实现 | 每模式公共契约与领域裁决验收 |
| P5 旧实现收敛 | 待实现 | 存量绑定排空、未知操作结清和回滚验收 |

模型解析入口现在可以构造四传输官方对象并记录构建指纹。§26 通过显式 policy 注入连接 §25 的账本，§27 接入生产组装、显式预算受理和 Worker 检查，§28 接通 owner 作用域内 DTO 及输入分类请求，§29–31 保存应用层与 HTTP wire 证据并接入 OpenAI/Gemini 图内官方 HTTP 客户端。无 owner 的旧调用仍沿既有路径。现有可选 Deep Agents 路由仍沿原开关，默认提交入口仍生成 legacy 任务，不得据此开放全系统新路由。

## 2. 依据 → 发现 → 改动

| 依据 / 复现 | 发现 | 改动与边界 |
| --- | --- | --- |
| `test_framework_dependencies.py` 首次 8 failed / 1 passed | 旧声明不满足候选九项依赖 | 更新服务声明与根锁；锁解析新增/升级 11 个包 |
| 四传输真实 SDK 请求捕获；默认 reasoning 经 `.bind_tools()` 后四例失败 | `RunnableBinding` 转发工具绑定不保留先前调用 kwargs | `OfficialChatModelFactory` 将默认参数放进官方模型构造配置；动态逐调用参数仍属于后续策略 |
| 显式 Chat `max_tokens` 请求捕获 | 官方模型会重命名顶层字段 | 框架工厂使用官方 `extra_body` 保持精确 wire 字段；普通 DTO 调用保留既有选择原生客户端的契约 |
| Google 4.4.0 `_common.py` 与四协议 503 请求计数 | Google 的 0 代表默认策略，1 才是总共一次尝试 | 应用 retries 加 1 传给 Google；四协议禁 SDK 重试时均只发送一次 |
| [Claude 流式文档](https://platform.claude.com/docs/en/build-with-claude/streaming)、固定版本官方事件 mapper、七个先失败用例 | Anthropic 1.7.1 mapper 忽略起始 input/cache usage，且不能直接累加多次累计 delta | 独立 `UsagePreservingChatAnthropic` 在事件转换处合并非空累计事实，保留显式零；使用官方 `subtract_usage` 输出增量；保留官方协议和 SDK |
| 取消清理任务关闭流的失败：`ValueError: Token ... was created in a different Context` | ContextVar token 跨 yield 保存会在异任务清理时报错 | 每次推进官方生成器时设置/重置 token，yield 前释放上下文，finally 显式关闭底层流；并行和后续流独立 |
| 冻结制品测试首次缺 manifest 失败 | 只有版本号不能恢复旧代码/锁 | `deployment/legacy-runtime.json` 固定原 revision、Python 与两个原文件 SHA256；尚未部署独立旧 Worker 服务 |

Anthropic 兼容类依赖已锁定的官方内部事件转换点，应在升级时运行同一协议回归，若上游修复则移除本地修正。usage 只在终止 delta 确定 input/cache 计数；断流或取消不代表完整最终用量，P1 账本必须保留不确定尝试。

## 3. 环境与可复现命令

根 `.venv` 保留旧依赖；`.venv-framework-v2` 是本地独立环境且已忽略。以下命令在仓库根运行：

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path (Get-Location).Path '.venv-framework-v2'
uv sync --locked --offline --no-install-workspace
uv lock --check --offline
.venv-framework-v2/Scripts/python.exe docs/verify_framework_contracts_2026_10_25.py --candidate --dependency-dir .venv-framework-v2/Lib/site-packages
```

离线 sync 从本机缓存成功安装 115 个第三方包。另一台机器需先具备缓存或用网络下载完整锁。初次常规 sync 在 build isolation 获取 `setuptools>=68.0` 时 TLS EOF 重试失败，随后改用缓存与 `--no-install-workspace`；没有将失败记为通过。

此方式从源码运行服务/测试，**未构建或安装 workspace wheel**。当前两个环境均没有 setuptools，服务声明还引用预先缺失的 `Agent_Server/README.md`；打包验收未通过，未在本提交中顺手修改此历史问题。

在 `Agent_Server` 目录执行回归：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_dependencies.py tests/test_framework_chat_models.py tests/test_langchain_model_adapter.py tests/test_model_chat_protocol_boundary.py tests/test_model_request_profile.py tests/test_deep_agent_runtime_adapter.py tests/test_deep_agent_tool_replay.py tests/test_langchain_message_adapter.py tests/test_langchain_tool_adapter.py -q -rs --tb=short
```

使用本地 HTTP/SSE 服务、合成响应和标记凭据，经真实官方 SDK 发请求。覆盖四协议 endpoint、headers、默认推理参数、token 字段、工具来回、消息序列化与签名保留、流式终态/usage、错误与禁止隐式重试；不使用供应商真实密钥，不声称真实模型已通过。

本阶段实际结果：上述九文件回归 **182 passed，52.98 秒，无 skip**；候选离线脚本四种逐项审批组合通过（fake model / InMemorySaver / 同进程重建）；`uv lock --check --offline` 通过；七个改动 Python 文件 `compileall` 与 `git diff --check` 通过。后两项只验证语法和 diff 格式，不替代运行/集成测试。

原依赖和制品可由 manifest 指向的 Git revision 及仓库子目录恢复。manifest 仅证明可重建来源，旧 Worker 代码 checkout、独立部署、命令能力隔离和旧恢复验收属于 P1/P2。

## 4. 依赖与交付局限

- 九项候选直接依赖版本与 v2 方案一致；完整间接依赖和制品摘要保存在根 `uv.lock`。
- 已检查新增集成的安装元数据：Deep Agents、Anthropic/Google 集成及 PostgreSQL saver 为 MIT；`psycopg-pool` 为 LGPL-3.0-only。
- 未执行真实供应商、真实 PostgreSQL/Redis 故障注入、跨进程接管、浏览器/压测/安全目标、前端构建或全量系统测试。
- 未将目前官方模型对象视为已纳入调用策略；子代理及摘要实际调用治理尚待 P1/P3。
- 未修改领域 Runner、正式用例状态机及六个占位模式；没有创建生产数据迁移或投递新版任务。

本记录随每个验证过的小阶段更新；必须保持方案目标与实际已实现范围分开。

## 5. P1 第一步：事件资源移出图状态

依据 v2 §3.3 与既有模型流式 ContextVar 作用域模式：事件 sink 现在由 `runtime/execution_context.py` 的执行作用域保存；启动、审批恢复、手动恢复三个公开入口负责建立和释放。图、循环状态对象不再保存 `_event_queue`，事件 ID、最终落库与溢出处理沿用原设施。

TDD：先增加 checkpoint 可序列化、并行隔离、异常清理与三个入口测试，首次因缺上下文模块失败；实现后 **6 passed**。既有七模式和 Deep Agents 回归 **101 passed**。

事件/中断/预算回归 **42 passed、18 skipped**，skip 包含未开启的真实服务和仅支持 PostgreSQL 的参数组合，没有计为通过。随后确认本机 PostgreSQL 健康，实际执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_session_event_delivery.py -q -rs --tb=short
```

结果 **30 passed、2 skipped，19.60 秒**。测试使用 UUID 后缀的独立表并清理自身表，覆盖 PostgreSQL 事件持久化、SSE、顺序、跨进程读写；两个 skip 是内存参数无法承担数据库事务顺序/跨进程测试，对应 PostgreSQL 参数实际通过。

这里验证的是已有事件投递基础设施，**没有验证 v2 checkpoint 的租约 fencing 或逐项审批杀进程恢复**。不能据此宣称 P1/P2 完成。真实供应商、Redis、全系统路由和所有权协议继续待实施。

## 6. P1/P2 已落地的契约边界

- `RuntimeBinding` 固定 session/turn、runtime/state schema、prompt/skill/tool 版本、模型配置指纹和 lockfile SHA256；`thread_id` 强制等于 `turn_id`。旧 dispatcher 解析到 v2 envelope 会在领取任务前拒绝。
- v2 task 将 binding 与 task、outbox 写入同一 PostgreSQL 事务；绑定指纹进入幂等哈希，冲突重试不能覆盖原绑定；v2 使用独立 `qa:tasks:agent:v2` stream。当前已实现绑定复核和独立 CLI 的最小 v2 Worker 闭环，但尚未完成 checkpoint owner/pending writes 与生产放量验收，不能把该 stream 当成已可投递生产流。
- Deep Agents 结果若为 LangChain `BaseMessage`，使用官方 `messages_to_dict/messages_from_dict` 保存/恢复；应用的 role/content 投影仍单独保存。原生 tool call ID、内容块、provider metadata 和签名不会通过摘要重建。
- Deep Agents 的 HITL 显式替换工厂默认的 batched middleware 为 `interrupt_mode="per_call"`。适配器能解析 `tool_approval` interrupt，并将审批决定恢复为单项 `Command(resume={interrupt_id: {"type": "approve"|"reject"}})`；同一 checkpoint 中未决定的 interrupt 继续保留。
- 以上改动已通过 RuntimeBinding 实时 PostgreSQL/Redis 测试 **2 passed**、既有 agent task 集成 **19 passed**、Deep Agents/runtime 相关回归 **60 passed、4 skipped**。ToolJob 已增加基于持久 metadata owner token 的 PostgreSQL heartbeat/observe/settle fencing，真实 schema 测试 **1 passed**；当时尚未覆盖 v2 Worker（后续进度见第 7 节），checkpoint pending writes、跨进程接管完整链路与未知副作用对账仍未完成。

## 7. v2 Worker 最小闭环（本轮）

依据 v2 方案 §8.1 的双 stream/双 Worker 边界，本轮补齐了 v2 Worker 的最小消费闭环：

- `AgentTaskDispatcher.handle_v2()` 只接受 `schema_version=2`，在领取前核对 PostgreSQL 中保存的完整 `RuntimeBinding`；`PostgresAgentTaskStore.claim()` 在 Session/task 行锁事务内再次比较绑定。v1 envelope 无法领取已有绑定的 v2 task。
- 新增 `src/cli/run_agent_v2_worker.py`，只连接 `agent_task_v2_stream` 和独立 consumer group（`ORCHESTRATION__AGENT_TASK_V2_CONSUMER_GROUP`）；legacy CLI 仍只连接旧 stream。consumer group 负责投递记账，schema 与数据库绑定校验负责版本隔离。
- CLI 从当前制品的 `uv.lock` 计算 SHA256，声明 `v2.0.0 / state schema 2` 能力。目前只支持 Deep Agents 的 `message/agent_turn`；版本、锁摘要、schema、runtime 或操作不支持时，在领取前 `TaskDeferred` 并记录结构化日志，不改为 legacy 执行。该摘要证明的是制品锁身份，尚不代替部署环境实际依赖和完整制品验收。
- Worker 将绑定在客户端输入编排之后注入内部 `ExecutionRequest.runtime_binding`；`InputOrchestratorService` 会先删除外部 context 中的同名框架字段。`RuntimeService` 使用该类型化字段定位 Deep Agents，检查 session/turn 身份并将绑定和 namespace 保存进状态。新 Turn 的模式路由变化不会改写绑定选择。
- 受控 LangGraph v2 图尚未实现，对应绑定明确 defer，不使用旧图外层循环冒充 v2 实现。v2 审批/手动恢复事务也未完成；当前 legacy 提交入口遇到绑定的 checkpoint/pending turn 会返回冲突，审批仍保持 pending。`submit_v2` 目前只允许 message，不能把 continuation 错当作普通消息提交。

TDD 记录：最初 v2 handler 测试因缺少 `handle_v2` 出现 **2 failed、1 passed**；新增三项提交契约测试也先复现失败：v2 message store 接受了 manual_resume，legacy stream 接受了两个绑定的 continuation。实现后均通过。

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_agent_v2_worker_contract.py tests/test_runtime_binding_persistence.py tests/test_agent_task_dispatch.py -q -rs --tb=short
```

v2 Harness 使用 UUID 隔离表及 stream，并清理自身数据；真实 PG/outbox/Redis/dispatcher/SessionService 搭配受控 Runtime 回应，不声称是官方框架/模型的端到端测试。覆盖绑定漂移、领取原子校验、版本/依赖不兼容、公开输入隔离、重复投递、终态不重跑、过期未启动任务接管、已启动任务进入 recovery_required、旧 owner 无法结算。CLI 组装为受控替身测试。

上述真实服务回归结果：**39 passed，80.33 秒，无 skip**。另外执行了两个有针对性的回归：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_runtime_interrupt_tool_calls.py tests/test_deep_agent_runtime_adapter.py tests/test_deep_agent_tool_replay.py tests/test_runtime_binding_contract.py tests/test_redis_worker_entrypoint.py tests/test_redis_task_worker.py tests/test_redis_task_queue.py -q -rs --tb=short
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_session_execution_ownership.py -q -rs --tb=short
```

结果分别为 **94 passed，6.55 秒**和 **36 passed，10.30 秒**，均无 skip。后者覆盖既有普通消息、手动恢复与审批路径的失租清理，不代表新增了框架 checkpoint fencing。

本轮 11 个新增/修改 Python 文件的 `compileall`、`python -m src.cli.run_agent_v2_worker --help` 和 `git diff --check` 均通过。CLI help 仅验证入口可导入及参数解析；未执行依赖完整应用 lifespan 的真实 CLI 进程验收。没有配置独立 lint/type-check gate，也未运行全量测试。

扩展检查还复现了一项本轮未改动的历史测试替身失配：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_runtime_input_paths.py -k previously_admitted_parent -q -rs --tb=short
```

实际结果 **1 failed、1 skipped、8 deselected**。失败是 `test_previously_admitted_parent_value_is_not_silently_replaced_on_recovery[False]` 的 `SimpleNamespace` 只提供旧读取方法，缺少 `AgentCaseExecutionAdapter._load()` 当前要求的 `get_execution_detail`，抛出 `AttributeError`；该测试及领域执行适配器不在本轮改动范围。另一个参数因未开启独立 PostgreSQL/MySQL/RustFS 环境跳过。没有将这次检查或全量测试记为通过。

早期 Harness 曾使用默认 v2 stream，读到上一轮已清理数据库的历史任务导致 `KeyError`；现已改为 UUID stream。没有删除默认业务 stream 中的数据。

仍未完成：官方框架 checkpoint owner/pending writes、v2 独立进程崩溃对账与安全续跑、v2 continuation/API 路由、模型配置/Prompt/Skill 版本实物校验、共享调用账本和各模式迁移。现有 API 不自动生产 v2 任务，不能放量或将本轮结果视为 P1/P2 完成。

## 8. owner 与 checkpoint 写入事务（2026-10-26）

依据 v2 §6、已锁定的 `langgraph-checkpoint-postgres==3.1.2` 官方 `_cursor/get_connection/put/put_writes` 源码和 [Psycopg 事务文档](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)，本阶段建立应用所有权与官方存储之间的事务边界。实现集中在 `runtime/postgres_runtime_owner.py`、`application/deep_agents/fenced_postgres_saver.py`，TaskStore、SessionService 和 provider 负责接线，领域 Runner 与裁决服务未改动。

### 实现与契约

- 新增 `<postgres_agent_task_table>_runtime` 表（默认 `agent_turn_tasks_runtime`）。绑定 ID、Session/Turn 和 thread 唯一；v2 初始 message 另有部分唯一索引，一个 Turn 不得提交两份绑定或启动两个初始任务。初始化核对并补登记历史 v2 绑定，不自动领取或续跑历史执行；已有不一致/重复绑定会使初始化失败，需要先对账，不能静默覆盖。
- v2 claim 在 Session → owner → task 的事务中同时分配 Session token、递增 epoch 并捕获不可变 `RuntimeExecutionOwner`。heartbeat 在同一事务同步两份到期时间；任务结算撤销 owner 写权。领取和写入都在拿到锁后用数据库 `clock_timestamp()` 判断租约，避免事务开始时间或等待前的判定过期。claim 事件/日志记录 binding/epoch，不记录 token。
- Worker 通过进程内执行作用域传递 owner；它不进入请求 context、graph state、checkpoint 或公开任务回执。provider 为每次执行创建独立 saver，捕获当次 owner，不在后续写入时借用新 owner。已绑定 Runtime 缺少 owner 时在调用模型前报错。
- `FencedPostgresSaver` 在一条池连接上的显式事务内锁 Session/owner、校验 binding/epoch/token/attempt/worker/到期时间，然后调用 `PostgresSaver(同一连接)` 的官方写入。`put/aput` 与 `put_writes/aput_writes` 共用此边界；旧线程在取消后继续运行也无法借用新代 token。运行时不允许 `setup` 或删除 thread；管理清理接口尚未实现。
- 修正此前仅透传 namespace 的不足：固定版本 LangGraph 会把顶层 namespace 重置为空。v2 saver 在存储边界添加绑定的物理 namespace 前缀，读取 config/parent_config 时恢复图的逻辑路径；线程 ID、原生 serializer、pending writes、历史枚举与 delta history 沿用官方能力。无 namespace 的历史枚举覆盖该绑定的根及子图，不能枚举其它绑定。
- 新增 `DEEP_AGENTS__CHECKPOINT_LOCK_TIMEOUT_MS=5000`、`DEEP_AGENTS__CHECKPOINT_STATEMENT_TIMEOUT_MS=30000`，已同步 `.env.example`。它们覆盖 v2 的提交、领取、heartbeat、结算与 saver 写入事务；领取遇到锁超时明确 defer，不消耗 attempt。当前应用业务表位于 `public`；saver 显式访问该 schema 的 Session/owner，同时在独立 checkpoint schema 存框架状态。数据库角色必须具备这两个 schema 的相应权限。常规 legacy 试点仍沿原 provider 路径，不声称它也获得了 v2 所有权保护。

### TDD 与真实验证范围

先复现了以下失败，再实现对应契约：同 Turn 第二份绑定被接受、同绑定第二个初始任务被接受、claim 未捕获 epoch、Worker 未注入 owner、绑定 Runtime 在无 owner 时进入适配器、等待锁后仍使用旧 `NOW()` 判定、历史 v2 绑定没有 owner 行、历史枚举遗漏子图、领取任务等待 owner 锁无界。最初 provider 的 owner API 测试因参数缺失失败；随后用真实数据库验证实际旧写入和回滚行为。

在 `Agent_Server` 使用 `.venv-framework-v2` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py tests/test_framework_checkpoint_fencing.py -q -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_agent_task_dispatch.py tests/test_coordinator_durable_children.py tests/test_session_execution_ownership.py -q -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_runtime_interrupt_tool_calls.py tests/test_deep_agent_runtime_adapter.py tests/test_deep_agent_tool_replay.py tests/test_runtime_binding_contract.py tests/test_redis_worker_entrypoint.py tests/test_redis_task_worker.py tests/test_redis_task_queue.py -q -rs --tb=short
```

三项最终结果分别为 **39 passed，67.73 秒**、**83 passed，133.32 秒**和 **94 passed，6.51 秒**，均无 skip。本轮 19 个新增/修改 Python 文件的 `py_compile` 和 `git diff --check` 通过；未运行独立 lint/type-check。修改的真实供应商模型/P5 Harness 只同步新表清理，未运行其完整验收。

checkpoint Harness 使用真实 PostgreSQL、官方 saver、独立 UUID schema 和应用 owner 表，覆盖旧 epoch 的同步/异步写入、checkpoint/pending writes 的事务回滚、撤销与写入互斥、等待锁后过期、锁/语句超时及连接复用、取消后的迟到同步线程、DeltaChannel 经 provider 重建后的还原。官方 Deep Agents 用受控工具和 fake model 产生 A/B 两项原生审批：先批准 B，B 只执行一次，A 保持等待；拒绝 A 后才完成。这里直接调用 adapter，**没有把 v2 业务审批 API、receipt、跨进程 kill 或真实供应商验证算作通过**。

新增 owner 表后，早期一次测试 teardown 未包含依赖表，出现 7 个清理错误；另有一次 `RowMapping` 不支持赋值的实现错误，均已修正。相关 Harness 已按依赖顺序清理新表；核验并清理了那 7 份本阶段 UUID 测试残留，没有删除业务表。另一批回归原进程句柄丢失，确认无对应进程后重跑，使用上面的实际完成结果。

### 尚未完成的出口

决定 receipt 与 `__resume__` pending writes 同事务、逐项审批的 v2 提交/队列/API、取消意图的即时 owner 撤销、崩溃后的对账与后继任务、ToolJob 与 framework owner 的端到端关联、独立 Worker 进程杀死/接管、模型共享预算及各模式迁移仍待实施。已有 `running` 任务失租仍进入 `recovery_required`，不会因为新增 owner 表就重跑。新 API 路由仍未放量，P1/P2 未整体完成。全量系统测试、真实供应商、浏览器/压测/安全 Runner 和前端构建未执行；上一阶段记录的历史测试替身失配不计为修复。

## 9. 附件评审复核与方案补全（2026-10-26）

本次按用户所附评审意见复核基线 `790df56ef`，只更新方案和本记录。六类意见作为架构风险成立；其中“尚未接入 per-call、没有 v2 checkpoint fencing、模型只支持 Chat、本地未安装 Deep Agents”等现状描述已经过时，方案第 1.3 节逐项区分当前实现与剩余缺口。

重新核对固定 LangGraph 1.2.12 与 PostgreSQL saver 3.1.2 源码后，补充了决定 receipt 的实施约束：interrupt ID 字典是调用输入，实际任务 `__resume__` 是累计值列表；列表位置不是数据库 write index。runner 在任务结算时提交写入，所以工具已执行但尚无 delivered 证明仍是合法故障窗口。官方 saver 可能 `ON CONFLICT DO NOTHING`，写证明必须确认实际保存的内容，不能只信传入参数。以上均为待实现契约，未新增 receipt 表或能力声明。

另确认适配器的 `ainvoke` 和子代理 `astream_events` 未显式指定方案要求的 `durability="sync"`，当前固定版本默认 `async`；现有提前发出的 `approval.decision_applied` 事件也不能用作消费证明。方案已将它们纳入 P2.2 的契约同步与测试。P2 拆为 owner 基础、决定证明、v2 决定事务与调度、跨进程崩溃对账四个有依赖的出口，保留未具备替代路径前的 v2 审批冲突保护。

实际重跑第 8 节的三文件真实服务回归：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_runtime_binding_persistence.py tests/test_framework_checkpoint_fencing.py tests/test_agent_v2_worker_contract.py -q -rs --tb=short
```

结果 **39 passed，70.96 秒，无 skip**。这证明既有局部契约仍通过，不证明新补的 receipt、业务审批、sync durability 或跨进程恢复已实现。未运行真实供应商、完整系统/模式验收、前端构建、独立 lint/type-check；本次无运行代码变更。

## 10. P2.1 决定交付日志与原生写入事务（2026-10-26）

依据方案 §4–6、§10.3，以及本地课程《Agent测试专项》《Agent轨迹与协议鲁棒性》关于轨迹、幂等及失败恢复的要求，新增交付日志存储端口，接入已有 owner-bound saver。没有开放 v2 审批 API，也没有将此存储阶段算作 P2 整体完成。

### 实现与边界

- 新增 `schemas/framework_approval.py`：不可变 interrupt link、决定 receipt 和规范化内容哈希。link 保存 binding、逻辑 namespace、checkpoint/task/path、interrupt ID、resume 列表位置、源消息/call、工具 schema、参数、范围与 ToolJob 引用。
- `runtime/framework_approval_journal.py` 沿用既有 AgentTask 初始化事务新增 `<agent_task_table>_interrupt` 和 `<agent_task_table>_decision`；外键关联现有 owner 与 Approval，唯一约束防止同一原生调用绑定不同审批。journal 随既有显式 Session/审批删除级联清理，避免阻断原单项/批量删除；checkpoint 管理清理仍未实现。业务决定不复制为另一套审批状态机；queue 端口读取已经记录的 approved/denied 决定并生成官方 approve/reject 值。
- link 端口检查持久化的官方 tool_approval interrupt、任务/path、工具与参数，按已保存 resume 列表长度验证位置，再校验业务 Session/Turn/call/ToolJob/范围。已有映射不能被后续投影覆盖。源 AI 消息身份由可信调用方提供；从 Runtime 原始消息和原生任务自动构建 link、与 Approval 同事务投影的生产接线仍属 P2.2，当前测试按真实 task 写入建立映射，没有将测试准备函数视为运行时已接通。
- `FencedPostgresSaver.put_writes` 在原有 Session → owner 事务内委托官方 saver 写 pending writes，读取实际持久化的目标任务 resume 内容，再写 delivered proof。两者共用一条 psycopg 连接；任一步失败均回滚。proof 包含物理 namespace/checkpoint/task/path/channel/write index、resume 列表位置、决定哈希、校验 owner epoch 和来源，重放保持原证明与时间。
- 增加只读事实对账端口：在有效 owner 事务下读取已存 pending writes 并补 proof；无匹配 resume 时保持 queued，不调用图、模型或工具。`evidence_kind=reconciled` 明确区分补投影，不能冒称本 owner 最初执行了工具。
- `delivered` 不意味着工具成功，`applied_at` 本阶段保持空；工具与权限裁决继续由应用治理负责。`sending`、业务任务/outbox/API/前端的 v2 交付、拒绝结果投影、未知副作用对账与安全后继任务仍待接入。

### 固定版本的前缀问题及根因处理

真实受控 LangGraph 在同一任务依次执行两次 interrupt 时，runner 会在一个混合批次内同时发送 `[decision_0]`、`[decision_0, decision_1]` 两条 `__resume__`。将这一**实际观察到的批次**交给未修改的 PostgreSQL saver 3.1.2，其 `ON CONFLICT DO NOTHING` 只留下短前缀；前一 invocation 已保存的前缀也可能阻止增长。

适配只针对已建立业务 link 的任务：在 owner 行锁事务内验证传入/已存值都是互相一致的前缀，选最长值并通过官方 saver 的特殊通道 upsert 保存，然后核对实际写入再证明。相反值回滚，短前缀重放不能截断长前缀，未映射任务和 scalar/global resume 保持官方契约。没有重写官方 SQL、serializer、checkpoint 或其它 channel。该版本依赖及其真实序列回归已记入方案 §5.1；升级时需重新核验，不能无限保留失去依据的兼容代码。

### TDD 与已运行的验证

首先运行真实 Deep Agents + PostgreSQL per-call 用例，原生 A/B interrupt 已出现，测试因缺少 journal 模块失败。实现后新增失败用例先复现了四项业务范围变化未被识别、无业务映射的 global resume 被误拒绝、多次 interrupt 产生多前缀无法保存、缺失只读对账端口及两种既有 Session 删除受新外键阻断，随后逐项实现并通过。

在 `Agent_Server` 执行（均设置 `RUN_LIVE_POSTGRES_TESTS=1`）：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_receipts.py -q -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_runtime_binding_persistence.py tests/test_framework_checkpoint_fencing.py tests/test_agent_v2_worker_contract.py tests/test_runtime_interrupt_tool_calls.py tests/test_deep_agent_runtime_adapter.py tests/test_deep_agent_tool_replay.py tests/test_runtime_binding_contract.py tests/test_redis_worker_entrypoint.py tests/test_redis_task_worker.py tests/test_redis_task_queue.py -q -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_agent_task_dispatch.py tests/test_coordinator_durable_children.py tests/test_session_execution_ownership.py -q -rs --tb=short
```

三项结果分别为 **27 passed，66.61 秒**、**133 passed，64.44 秒**、**83 passed，129.34 秒**，均无 skip。后两组在新增 Session 删除回归及补齐级联外键前运行；新增两种删除行为由最终 27 项 Harness 验证。新 Harness 使用真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph 与 fake model，覆盖批准/拒绝、双向事务回滚、并发重复证明、任务/namespace/checkpoint/path/决定不匹配、业务范围变更、同任务多位置及前缀增长、旧 owner 写入、保存事实对账和无证据不判交付。旧 owner 测试证明的是 checkpoint 拒绝写入，不证明未受应用治理的 fake tool 也被阻止执行。另一次中间版本的四文件回归为 62 passed，111.25 秒；不与后续回归重复累计。

补齐级联外键后又重跑 `test_runtime_binding_persistence.py test_framework_checkpoint_fencing.py test_agent_v2_worker_contract.py`：**39 passed，58.23 秒，无 skip**。最终只读查询确认本轮 UUID Harness 的 journal 表和 `fence_test_<UUID>` schema 均为 0 残留，没有额外删除业务对象。

九个改动 Python 文件 `py_compile` 通过；四个涉及新表依赖的既有 fixture 已同步 teardown 顺序。真实供应商模型与 P5 Harness 仅更新清理规则，未执行其完整验收。未运行独立 lint/type-check、前端构建、全系统测试、独立 Worker kill/recovery 或目标领域 Runner 验收。

两表位于 public，与现有业务表同库；启动初始化需要新增表/索引/外键权限，checkpoint 继续位于配置 schema。新旧运行时枚举、API 及生产路由未改变。既有框架绑定审批仍由 legacy 提交入口拒绝，不能在 P2.2/P2.3 出口未过时放量；本阶段没有补做取消即时 owner 撤销、共享模型预算、跨 Turn 上下文或各模式迁移。

## 11. 附件复核同步至 bde82d38b（2026-10-26）

本轮交付为方案复核。重新对照附件、当前实现与官方文档，将方案基线更新到 `bde82d38b`，修正“没有 receipt／同事务尚待验证”的过时描述，保留业务 v2 决定受理、串行继续和跨进程恢复未完成的判断。方案第 13 节给出后续各步骤的改动边界、先失败用例和验收前约束；不将本轮文档补全视为迁移完成。

新增审批门控稳定性约束：官方 HITL 的可选 `when` 在恢复时重新求值，固定 1.4.6 源码与在线文档均有依据。动态权限/预算不能使已有 interrupt 被跳过并忽略原拒绝；门控按稳定调用身份保持，执行前治理独立重查当前权限。没有声称当前系统已使用动态 `when` 或已出现实际绕过。

按第 10 节同一命令重跑 `test_framework_approval_receipts.py`：**27 passed，68.71 秒，无 skip**。九个直接依赖实际版本与方案一致，实际导入的 HITL 签名含 `interrupt_mode`。未修改业务代码、依赖或生产路由；未运行全量、前端、供应商、独立进程或领域 Runner 验收。本轮验证范围限于已存在的决定 journal 与原生写入 Harness，P2.2/P2.3 和全系统迁移继续待实施。

## 12. P2.2 内部决定受理事务与串行领取（2026-10-26）

依据方案 §4.3、§8、§10.3 和本地《Agent轨迹与协议鲁棒性》的幂等/恢复要求，新增独立的 `runtime/framework_approval_submission.py`，由 `PostgresAgentTaskStore.submit_approval_v2(command, approval_id, payload)` 委托。复用现有 Session 行锁、任务、attempt、事件和 outbox；没有在原 legacy 审批方法中叠加第二套运行逻辑。

### 行为与存储边界

- 内部提交按 Session → 绑定 owner → 审批的顺序锁定，核对正式项目、活跃 Turn、完整 RuntimeBinding、待审批集合、当前业务参数/范围与已存原生 link。只接受 approved/denied；禁止待取消、已中断、需对账的 Turn 开始新决定。存储入口重新验证完整 v2 命令，防止内部 `model_copy` 修改 Turn 后绕过命令绑定。
- 以审批身份作为幂等边界，不依赖点击 ID：不同 `client_request_id`、原因文本或拟议 task ID 的同一决定复用原任务/receipt；相反决定冲突。已受理决定在 Turn 结清后可返回原回执，但不创建新任务或重新派发。
- 一个事务保存业务决定、冻结 native decision receipt、唯一 continuation task、两条现有受理事件及固定目标 v2 outbox。Session 正在 running 时仍允许其它有效审批排队，不改写当前执行的租约、owner 或 control。业务受理事务与原生写入/proof 事务仍是两个边界，外部副作用不在其中。
- 新增部分唯一索引 `<agent_task_table>_v2_approval_idx`：仅对带 runtime_binding 的 approval_resume 约束 `(session_id, reference_id)`，数据库层也防止同审批重复创建续跑。沿既有初始化方式创建，需要建索引权限；不修改旧 schema 或引入依赖。
- 领取前重查活跃 Turn、业务 link、receipt 和 task 的决定引用。失效/取消的未启动续跑进入 cancelled 终态；决定事实或绑定封套矛盾进入 recovery_required；不分配 attempt/owner，不调用图或工具。提交/复用/拒绝及领取拒绝都有结构化日志。
- 有效当前 Turn 的续跑可越过其它 Turn 尚在 queued/waiting_resource 的普通 message；同 Turn 的续跑仍按受理顺序互斥，既有 claimed/running 执行仍阻止接管。没有重排 legacy 审批或重置终态任务。
- 录制、TestRun case/agent 和代理子审批沿专用入口边界拒绝进入该通用端口。`FrameworkApprovalJournal.require_link_in_transaction` 作为共享校验端口，避免提交模块直接读其私有表名和重复实现匹配规则。

### TDD 与验证边界

先实际运行新测试，四项均因不存在提交端口失败；实现受理后，后续普通消息阻塞续跑的用例单独复现 FIFO 缺口。随后用失败测试分别复现取消/矛盾任务未进入终态、内部命令绕过 Turn 校验、损坏绑定未分类，以及两类专用审批边界遗漏，再实现对应行为。四个事务故障注入点为 receipt、task、event 和 outbox；outbox 使用隔离 UUID 表上的真实 AFTER INSERT 失败触发器，验证此前所有写入一起回滚。

在 `Agent_Server` 运行（均设置 `RUN_LIVE_POSTGRES_TESTS=1`）：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_submission.py tests/test_framework_approval_receipts.py tests/test_runtime_binding_persistence.py tests/test_framework_checkpoint_fencing.py tests/test_agent_v2_worker_contract.py tests/test_agent_task_dispatch.py tests/test_coordinator_durable_children.py tests/test_session_execution_ownership.py -q -rs --tb=short
```

结果 **177 passed，305.94 秒，无 skip**。此结果在最后增加损坏绑定分类与两类专用审批用例之前取得；损坏绑定修正后的新文件回归为 **29 passed，69.32 秒，无 skip**，不与前一组重复累计。

补齐专用入口边界后，按同一环境重跑 `tests/test_framework_approval_submission.py`：**31 passed，76.66 秒，无 skip**，覆盖最终代码的损坏绑定分类、五类专用路由、数据库唯一约束及全部新增提交/领取场景。六个涉及的 Python 文件 `py_compile` 与 `git diff --check` 通过。未运行独立 lint/type-check、前端构建、全系统测试、真实供应商或独立 Worker kill/Runner 验收；本轮没有把它们计为通过。

新增 Harness 使用真实 PostgreSQL/Redis fixture、官方 Deep Agents 产生的 A/B interrupt 与 fake model；手工通过已验证的 journal 端口建立业务 link，再验证受理和领取。它不自动从 Runtime 提取原生身份，也没有将真实业务 ToolJob/Runner、副作用或 v2 Redis 消费链算作已验证。原 receipt 测试仅抽取待决定 link 的准备函数，已有 queue helper 的调用契约保持不变。

仍待实施：Runtime 自动提取稳定源消息/任务身份并原子投影审批；API 与 Dispatcher 接入 v2 端口；受理从长执行的 `_session_locks` 中分离；Worker/Session/Runtime/adapter 的指定决定及无新决定继续执行；sync durability、交付/applied 事件及前端投影；真实受治理工具的端到端恢复。当前 API 仍拒绝绑定回合的 legacy 审批，Worker 能力仍只开放 v2 message，不能将内部端口通过等同于 HTTP 202 或 P2.2 完成。跨进程故障、取消即时 owner 撤销、未知副作用与后继任务继续由 P2.3 验收。

## 13. P2.2 原生身份提取与 adapter 持久化调用（2026-10-26）

依据方案 §4、§5、§13、已安装的 LangGraph 1.2.12 `pregel/main.py`、`pregel/debug.py`、官方 per-call HITL，以及 [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 文档，补齐 adapter 层原生身份提取与执行协议。没有开放 v2 审批 Worker 能力或生产 API 路由。

### 真实框架观测与实现

- 新增 `application/deep_agents/native_interrupts.py`。在 saver 仍打开时，读取 `agent.aget_state(..., subgraphs=True)`，递归已注册子图，提取当前 AI 消息 ID、原始 call ID、工具名与参数。调用身份不从展示消息或同名同参数猜测；缺失、歧义、显式 ID/参数/工具不匹配均要求恢复对账。
- 新增 `NativeToolInterrupt` 内部模型，现有 `FrameworkInterruptLink` 继承同组字段，保留原有字段语义与固定 format_version。bound adapter 返回 `native_interrupts`，并核对其 ID 集合与本次真实调用返回的 interrupt；这是待后续业务投影消费的内部结果，不是已经创建业务 link 的证明。
- `FencedPostgresSaver.aget_interrupt_task` 通过原 owner 事务读取 journal 的原生任务事实，返回实际持久化 `task_path`、interrupt 和 resume 前缀；不把 StateSnapshot 的元组 path 格式化成数据库路径。读取同样不能借用新 owner，日志仅记录 binding/数量/错误类型。
- 实测 checkpoint 的 `channel_values` 可能仅含工具 fan-out，消息依赖 DeltaChannel 历史；因此使用官方状态还原，而不自行解码一个 checkpoint 拼装历史。
- 实测部分恢复后，StateSnapshot 仍包含已完成兄弟任务的旧 INTERRUPT。collector 用同任务匹配的原生 ToolMessage 与已保存 resume 共同排除已结算项；只见 resume、没有工具结果时要求对账，不能将它重建为下一位置的新审批。collector 限于官方 per-call tools 节点，任意自定义图的审批映射需要自己的受控契约；既有 journal 多位置协议未改变。
- 同名同参数的不同 call 独立；跨模型轮次复用供应商 call ID 时按源 AI 消息区分；已批准 B 后仍等待 A 时，A 的 checkpoint/task/path/source/interrupt 身份保持不变。真实注册子图的 namespace 也纳入验证。

### 执行与事件协议

- 新增内部 `continue_from_checkpoint=True` 请求，明确调用 `ainvoke(None)`；与新的 resume 决定互斥，且要求 checkpointer。指定决定继续使用 `Command(resume=...)`。两种继续都不读取/反序列化传入历史；只有初始调用导入消息。
- 带 checkpointer 的普通调用和事件流都显式传 `durability="sync"`，覆盖启动、单项恢复、无新决定继续。无持久化旧试点不传 durability：本次真实回归复现固定版本在无 saver 时指定 sync 会访问不存在的 `_put_checkpoint_fut`；不靠升级依赖或默认内存 saver 改写旧试点行为。
- 实测 `astream_events` 的 root `on_chain_end.output` 不携带审批 interrupt。事件入口改为同时订阅 `updates`/`values`，由 root updates 收集 interrupt，values 保存状态；只看最终 output 会误判为无审批。既有认知子代理事件投影保留。

### TDD 与验收边界

先在官方 Deep Agents + PostgreSQL 上生成 A/B interrupt，确认原生形状后，新测试因缺少 collector 失败。随后先复现部分审批仍识别出 B、六条执行入口未显式 sync/继续读历史/缺少 None 入口，再实现对应行为。新增“只有已消费决定、没有任务结果”的用例先复现错误创建下一位置身份，再加入明确对账。完整 adapter 回归又捕获事件路径丢失 interrupt、无 saver 的 sync 上游异常，两项均用真实固定版本修正。

针对最后两项的定向回归（native adapter 普通/事件路径、只读文件、认知计划、真实子代理及六条调用契约）为 **11 passed，10.62 秒，无 skip**。它不与后续完整回归重复累计。

新增 Harness 使用真实 PostgreSQL/Redis fixture、固定 Deep Agents/LangGraph 和 fake model；没有替代真实供应商测试或完整业务 ToolJob 执行。适配器实际演练：初始 A/B 待审批 → 批准 B → 无新决定继续 → 拒绝 A → 模型结束，B 的 fake 工具只调用一次、历史 user 消息只出现一次。该用例没有业务决定 receipt/ToolJob，不能算“delivered 后完整业务续跑”或跨进程验收。

在 `Agent_Server` 使用 `.venv-framework-v2` 运行最终回归：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_native_interrupts.py tests/test_deep_agent_runtime_adapter.py tests/test_framework_approval_submission.py tests/test_framework_approval_receipts.py tests/test_framework_checkpoint_fencing.py tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py tests/test_runtime_interrupt_tool_calls.py tests/test_deep_agent_tool_replay.py -q -rs --tb=short
```

结果 **192 passed，256.09 秒，无 skip**；新文件 14 项、adapter 新增九项协议验证均在该组中。七个改动 Python 文件 `py_compile` 和 `git diff --check` 通过。本阶段没有新增依赖、配置项或数据库结构；无独立 lint/type-check gate，未执行前端构建、全系统测试、真实供应商、独立 Worker kill 或领域 Runner 验收。

仍待实施：Runtime 消费 native identity，以稳定完整调用键创建/复用等待 ToolJob、Approval 和 link，并在 owner 事务内原子投影且保留已提交决定；Session/Dispatcher/Worker/API 根据 receipt 与工具对账选择指定决定或 None 继续；HTTP 202 与前端状态；跨进程故障、后继任务、取消 owner 撤销和目标 Runner。共享模型账本、跨 Turn 上下文、制品版本校验、正式 AgentCaseExecution 图与各模式迁移也未完成。整体目标和 P2.2/P2.3 继续保持未完成状态。

## 14. P2.2 原生审批业务投影（2026-10-26）

依据方案 §4–6 和第 13 节已验证的原生身份，新增独立 `FrameworkApprovalProjection`。治理服务只准备审批、ToolJob 和 link，不写业务记录；投影服务在 Session → owner 事务内保存/复用四类事实：等待 ToolJob、Approval、原生 link、稳定 `approval.created` 事件。实际工具、模型与图调用不进入此事务。

内部调用键为 binding、Turn、图 namespace、源 AI 消息和供应商 call ID 的规范化哈希；审批 ID 与 ToolJob ID 均从该身份确定性生成。审批 metadata 的 `call_id` 保留原供应商值，`framework_call_id` 保存内部值。link 新增可选 `business_call_id`，存于既有 JSONB；历史 link 缺少它仍按既有契约读取，原生 link 即使移除 metadata 也不能退回 legacy 校验。本阶段没有新增表、列、依赖或生产路由开关。

Runtime 使用 native identities 投影，不依赖展示消息或进程内 batch 推测身份；重投影保留人类决定与终态 ToolJob/输出。Session 收尾在 captured owner 下读取最新审批，避免将并发提交的 approved/denied 覆盖成 pending，也不重复生成随机创建事件。ToolJob 创建复用现有 INSERT ON CONFLICT 与事务端口，不使用全行覆盖；未知执行状态要求对账。

TDD 首轮五项因缺少准备 API 失败；接线用例随后复现 Runtime 缺少注入和 Session 覆盖已提交决定；再分别复现删除元数据降级校验、重投影终态结果错误生成等待记录。修正后，新增文件最终包含 15 项，覆盖零写准备、稳定身份、四事实事务、link/event 后失败全回滚、五并发投影、过期 owner、身份/参数/范围损坏拒绝、空展示消息、重建 batch、并发决定保留及终态结果复用。

在 `Agent_Server` 运行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_projection.py tests/test_framework_approval_submission.py tests/test_framework_approval_receipts.py tests/test_framework_native_interrupts.py tests/test_framework_checkpoint_fencing.py tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py tests/test_deep_agent_runtime_adapter.py tests/test_deep_agent_tool_replay.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py tests/test_lifespan_resource_cleanup.py -q -rs --tb=short
```

最终结果 **258 passed，1 skipped，327.93 秒**。唯一 skip 为 `test_lifespan_resource_cleanup.py:181` 的 MySQL 实集成，未设置 `RUN_LIVE_MYSQL_TESTS=1`；PostgreSQL/Redis 与本阶段原生审批测试实际执行。十个相关 Python 文件 `py_compile`、`git diff --check` 通过；未运行独立 lint/type-check、前端、全量测试、真实供应商、独立 Worker kill 或领域 Runner 验收。

Harness 使用真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph 和 fake model 产生 A/B interrupt。部分场景直接修改隔离业务表模拟已受理决定或已完成结果，用于验证投影边界；不能据此声称 HTTP 并发提交、完整 ToolJob 执行与 v2 消费链通过。

下一接线缺口已确认：旧业务工具桥仍只按供应商 call ID 查 batch 并执行治理，而新 ToolJob 使用完整内部键；必须从官方 `ToolRuntime` 提取源 AI 消息/图 namespace，并在恢复时读取持久化决定，完成两侧身份与结果契约。Dispatcher/Worker/API、receipt 驱动的恢复选择、逐项结果合并、applied/UI 以及 P2.3 等仍待实施，整体迁移继续未完成。

## 15. P2.2 业务工具执行身份与门控恢复（2026-10-26）

依据上一节确认的身份缺口、官方 [Tools/ToolRuntime](https://docs.langchain.com/oss/python/langchain/tools)、[逐项 HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)，以及已安装 LangGraph 1.2.12 `prebuilt/tool_node.py`、`runtime.py`、`pregel/_algo.py` 和 LangChain 1.4.6 `human_in_the_loop.py`，接通业务工具执行身份。没有开放 v2 审批 Worker 能力或 API 路由。

- 新增共享 `FrameworkToolCallIdentity`，原有 native/link 字段保持兼容。工具 callback 接收官方注入的完整 ToolRuntime；审批提取和执行共用当前 AI 消息的精确 call ID/名称/参数匹配规则。固定版本 execution_info 的 namespace 包含当前 tools 任务；只去掉该末尾分量，保留注册子图祖先身份。真实根图和子图测试核对了执行键与保存的 interrupt 键一致，跨模型轮次复用供应商 call ID 会生成不同内部键。
- builder 改为异步，在 owner 事务下读取已有原生 link 的完整调用键，恢复不可跳过的门控身份；同步 `when` 不读取数据库。既有门控不随当前动态策略放宽而消失，新调用仍按当前治理判定。工具执行时按完整身份重查持久化审批，不依赖旧 batch 中的决定；审批在门控恢复后消失则要求对账。
- 先失败测试实际复现了“ToolJob 已完成但图任务未提交，权限收紧后旧 mark_denied 覆盖原结果”。v2 治理拒绝现在经 owner → Approval → ToolJob 行锁事务结算：终态结果复用，只有未启动的 waiting_approval 可转 denied；已 started/running 的未知效果要求对账。沿用原 ToolJob 事务端口并抽取事务内读取，没有修改 legacy 通用 mark_denied 或重置终态任务。
- 新增门控恢复、审批读取和拒绝结算的结构化成功/拒绝日志；不记录工具参数、owner token 或原生消息内容。现有 callback 的消费者已同步，真实场景知识测试仅更新回调签名，未宣称运行其完整浏览器/供应商验收。

TDD 首轮三个实际框架测试失败：身份提取入口缺失，且无 batch 时批准 B 不执行。实现后覆盖根图/子图、原 ToolJob 执行、B 执行一次/A 继续等待、None 继续不重放、策略放宽保留 reject、新 AI 复用 B 不继承旧审批、参数损坏和过期 owner。扩展用例中两次失败源于测试输入：手工 reject 缺少 receipt 冻结的 message；免审批权限误写成 `allow`（本项目取值为 `safe`）。已改用实际 receipt 和项目权限值，不改生产协议来迁就测试。终态覆盖回归另有先失败记录；最终新文件 15 项定向回归通过，29.21 秒。

Harness 使用官方 Deep Agents/LangGraph、真实 PostgreSQL/Redis 与 fake model；受控 handler 经真实 ToolGovernanceService、ToolRuntimeService 和 PostgreSQL ToolJob 完成，不绕过治理。B 的真实 receipt 进入 delivered，applied_at 仍为空。决定由隔离测试表及 journal 端口准备，未通过 HTTP 或 Redis Worker；“已完成 Job 但图未提交”由隔离记录构造，未实际杀死 Worker。

原生 reject 会直接返回框架 ToolMessage，跳过业务 callback。当前测试明确保留其 waiting ToolJob，尚未将它冒称为完整的业务拒绝结果投影；这与当前权限治理在 callback 内阻止执行的结算路径不同。后续仍须接通 receipt 驱动的指定决定/None 选择、拒绝/applied 结果、逐项记录合并、Session/Dispatcher/Worker/API 与前端，再完成 P2.3 跨进程与未知效果验收。全系统迁移继续未完成。

在 `Agent_Server` 运行最终相关回归：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_tool_bridge.py tests/test_framework_approval_projection.py tests/test_framework_approval_submission.py tests/test_framework_approval_receipts.py tests/test_framework_native_interrupts.py tests/test_deep_agent_runtime_adapter.py tests/test_deep_agent_tool_replay.py tests/test_langchain_tool_adapter.py tests/test_agent_v2_worker_contract.py -q -rs --tb=short
```

结果 **189 passed，252.16 秒，无 skip**，包含最终终态保护及 waiting/running 分支。11 个相关 Python 文件 `py_compile` 和 `git diff --check` 通过。没有新增依赖、数据库表/列或配置开关；没有运行独立 lint/type-check、前端构建、全量系统测试、场景知识的浏览器实集成、真实供应商、独立 Worker kill 或领域 Runner 验收。阶段中的 95 项、186 项中间回归与最终用例重叠，不累计为额外覆盖。

## 16. P2.2 指定决定的续跑准备与原生事实对账（2026-10-26）

依据方案 §4–6、§13，继续实现独立的 `runtime/framework_approval_continuation.py`，不将 v2 协议继续叠加到 legacy 的批量恢复方法。在线重新核对官方 [HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) 与 [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)，本地读取固定 1.4.6 的 `_resolve_per_call` 和 1.2.12 的 `_runner.commit`：reject 跳过 callback；resume 写入可能发生在工具返回之后，不能由“尚未 delivered”推断“工具没有执行”。

### 行为与边界

- `prepare(owner, approval_id, decision_id)` 在 Session/owner 锁保护下，重新读取真实 running approval_resume 任务，核对 binding、attempt、worker、lease、reference、冻结审批/决定和当前活跃 Turn。调用方替换 approval/decision ID、持久化范围/参数改变、已请求取消或租约过期，都不能将 receipt 推入 sending。
- 审批/link、原 ToolJob 的稳定业务键/原生身份/参数，以及原 checkpoint/task 的 interrupt/resume 必须一致；共用 journal 校验，不复制原生格式解码。delivered 字段不代替实际原生事实，证据缺失或冲突要求对账。
- 未交付、未启动的等待 Job 可发送一项冻结决定。批准对应终态结果也可发送原决定，由既有治理桥复用结果；running/resume_requested 等未知效果不能继续。已批准却显示原生已消费而 Job 仍等待、拒绝却发现已执行结果，均要求对账。
- 已消费的同一决定修复 delivery proof，原子追加稳定的 `approval.decision_delivered` 事件，选择 `None` 继续；重复准备不会重复追加事件。未消费时只标记 sending，不提前标记 delivered/applied。
- 不可变 `FrameworkContinuationPlan` 的 resume/continue 选择从 receipt 状态派生。准备事务不调用图、模型或业务工具；结束事务后由后续 Runtime/Worker 入口执行，工具治理仍须复核当前策略和 CAS 执行身份。
- 原生拒绝的 ToolJob 拒绝结果及 applied 投影、Runtime/Session/Dispatcher/Worker/API/UI 的成对接线仍未完成。本阶段没有开放 v2 approval_resume 能力、改变生产路由或增加数据库结构/依赖。独立跨进程崩溃与取消即时撤销仍属于 P2.3。

### TDD 与验证

开始前已存在的 7 项测试先实际失败，均为 continuation 模块缺失。实现后的首轮为 6 failed / 1 passed，失败来自测试夹具返回提交回执 `AgentTaskRecord` 后读取不存在的 input；按既有 schema 改为返回实际领取的 `AgentTaskExecution`，未修改生产契约迁就测试。修正后原始 7 项通过，21.07 秒；扩展到 16 项通过，43.35 秒。中间回归与最终回归重叠，不累计。

Harness 使用官方 Deep Agents/LangGraph、真实 PostgreSQL/Redis 和 fake model；实际 v2 提交、领取和 begin 后，使用新 owner 重建 saver 与真实治理工具桥。没有复用捕获旧 owner 的 graph。覆盖 B 批准或拒绝、指定决定/None、终态结果复用、未知效果、篡改身份、原生事实缺失、delivery 修复/事件回滚、租约过期，以及 B 完成后由 A 的新 continuation 收齐两条匹配 ToolMessage、B 不重复执行。缺失 proof 与终态窗口由隔离测试记录构造，并未实际杀死 Worker；不能替代 P2.3 验收。

在 `Agent_Server` 运行最终回归（新文件含 19 项）：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_continuation.py tests/test_framework_tool_bridge.py tests/test_framework_approval_projection.py tests/test_framework_approval_submission.py tests/test_framework_approval_receipts.py tests/test_framework_checkpoint_fencing.py tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py -q -rs --tb=short
```

结果 **146 passed，298.37 秒，无 skip**。4 个改动 Python 文件 `py_compile`、`git diff --check` 通过。未执行独立 lint/type-check、前端构建、全量系统、真实供应商、独立 Worker kill 或领域 Runner 验收；本轮仅实现并验证续跑准备边界，未将这些完整迁移出口记为通过。

## 17. P2.2 原生结果证明、拒绝结算及 applied 投影（2026-10-26）

继续沿方案 §4.2、§5.1 实现 `FrameworkApprovalContinuation.settle`，与 prepare 共用同一个 owner 事务内的前置校验。没有新增第二套原生结果格式：先通过真实官方 saver 观察到 per-call 任务将一条 ToolMessage 写入 messages 通道，再用 journal 中已有官方 JsonPlusSerializer 读取原 checkpoint/task 的结果。

- 只接受原任务/task_path、原 call ID 和工具名匹配的单条 ToolMessage；缺结果、错误调用、持久化任务 error 或不匹配的结果状态不能标记 applied。准备、交付或终态 Job 单独存在均不是应用证明。
- 原生 reject 仍不调用业务 handler；观察到其 error ToolMessage 后，只将未启动的 waiting_approval Job 结算为 denied，保存原生反馈。既有终态保持，发现已执行的拒绝调用要求对账。
- 批准可能得到 completed/partial/failed/denied/cancelled 的领域结果；原生治理响应须和 Job 的终态投影一致。当前权限变严时的治理阻止归为 governance_denied，不能显示为业务工具已执行。applied 只表示观察到决定产生的结果，不表示测试通过或副作用成功。
- receipt 既有 proof JSONB 增加 application，记录原生完整消息哈希、原任务位置、决定哈希、Job 身份/状态/输出哈希和 outcome；不增加表或列。applied_at、业务拒绝结果和稳定 applied 事件共用事务。重复结算保留原时间并验证原证据；已 applied 后消息或 Job 输出改变要求对账。delivery 证明重放只核对自身字段，保留 application，不覆盖已记录结果。
- 共享准备校验提取为同事务方法，prepare 及 settle 均不调用模型/图/工具；没有在旧恢复分支追加重复循环。Runtime/Session、Dispatcher/Worker、API/UI 尚未消费该端口，Worker 仍只声明 v2 message；完整接线及 P2.3 出口仍待实施。

TDD：先运行新结果 Harness，**7 failed，25.75 秒**，全部为 settle 端口不存在；其中两项已在失败前验证真实 messages 通道。初版实现 **1 failed / 6 passed，22.40 秒**；复现确认首次拒绝返回的 Job 时间带时区，store 后续读取按项目约定归一为 UTC 无时区，导致重复结算返回对象不一致。改用既有 ToolJob 的 datetime.utcnow 约定后，结果/续跑两文件 **26 passed，72.15 秒**。之后增加真实任务 error、错误 call/status、状态冲突及 applied 后证据变更回归；中间运行与最终回归重叠，不累计。

Harness 继续使用真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph、fake model 和真实应用治理桥。缺失/损坏输出及提交窗口由隔离记录故障注入；没有实际杀 Worker，没有供应商模型或领域目标副作用。

在 `Agent_Server` 运行最终回归（结果 Harness 含 13 项）：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_results.py tests/test_framework_approval_continuation.py tests/test_framework_tool_bridge.py tests/test_framework_approval_projection.py tests/test_framework_approval_submission.py tests/test_framework_approval_receipts.py tests/test_framework_checkpoint_fencing.py tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py -q -rs --tb=short
```

结果 **159 passed，331.83 秒，无 skip**。3 个改动 Python 文件 `py_compile`、`git diff --check` 通过。追加读取本地《Agent轨迹与协议鲁棒性》§04.5、§7 及《Agent测试专项》的重复执行/轨迹断言要求，当前用例验证实际效果次数与持久状态，没有用最终回答文本替代轨迹。未执行独立 lint/type-check、前端构建、全量系统、供应商模型、独立 Worker kill/recovery 或领域 Runner 验收；没有宣称完整 v2 approval_resume 已可上线。

## 18. P2.2 Runtime / Session 原生审批续跑接线（2026-10-26）

依据方案 §4–5、既有 continuation 服务，以及重新核对的官方 [per-call HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) / [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)，将上一阶段端口接到 Runtime 与已领取任务的 Session 执行入口。没有新增模型工具循环，也没有改变领域 Runner、正式用例或测试裁决。

- `RuntimeService.resume_framework_approval` 接收任务冻结的 approval_id / decision_id。对照捕获 owner 核对 pending_turn 和重建 graph_state 的 Turn、完整 binding 与 namespace；损坏绑定归类为恢复对账错误。实际续跑前调用 prepare，原样将指定 resume 或 `continue_from_checkpoint` 送入官方 adapter；不从会话文本重建原生恢复输入。
- adapter 返回后才调用 settle，以原生任务结果及终态 ToolJob 为依据结算。原生拒绝绕过业务 handler 时同样由该服务生成业务拒绝记录。模型结果、终态 Job 或调用 prepare 单独存在均不提前发 applied。
- 工具结果按稳定业务 call_id 合并，反复投影等待项不重复追加；已结算结果保留。没有剩余 interrupt 时清空 bound batch。展示工具消息使用 binding_id / 完整业务 call_id 派生稳定 UUID，复用 Session 既有按消息 ID 的保存合并，避免 A 续跑时再次追加 B。
- `SessionService.execute_claimed_approval` 校验 owner 与 task/attempt/worker/lease 一致及决定 ID 存在，执行和落库期间绑定同一个 owner；heartbeat 使用既有 `renew_framework_execution` 同时续租任务与 Session。v1 继续走原方法。main 注入 continuation 服务；绑定回合不能借 legacy Runtime 审批入口跳过原生证明。
- Runtime 执行作用域覆盖 prepare 在内的整个调用。新增暂停 prepare 的测试先复现中断被忽略，再对齐既有 Runtime 公共入口的作用域模式；准备中收到中断后，adapter 任务在执行前被取消，不执行工具、不结算 applied。这个本地取消语义不代替后续持久 owner 即时撤销。
- **边界仍未开放**：Dispatcher/API 的 v2 审批路由、受理从长 Session 锁分离、Worker approval_resume 能力及前端决定状态投影仍待实施。当前 v2 Worker 依然只声明 message，直接调用 Session 的真实集成通过不等于完整队列已验收。跨进程 kill / 后继任务、未知副作用对账、即时取消撤销与 ToolJob 权威 fencing 仍按 P2.3 / 相关阶段处理。

### TDD 与验证范围

新 Runtime Harness 首次为 **5 errors，18.90 秒**，均因缺少 continuation 注入入口；实现后 **5 passed，18.95 秒**。随后 Session 的 3 项先失败，复现仍走 legacy 方法且未校验 owner/决定；接线后最初一项因测试在已保存 B 后再次执行同任务失败。这个路径违反活跃审批成员约束，测试改为实际提交后继 A，验证两次 Session 保存只留各一条工具消息，没有放宽业务约束。

完整初次调用用例使用新绑定 Turn，经真实 RuntimeService、受约束的官方 Deep Agents adapter、PostgreSQL saver、治理工具及 Session 保存完成 B→A。测试夹具先显式结束其独立观测 Turn，并补齐模型配置 stub 的既有接口；没有修改生产提交/模型契约迁就夹具。

新增 Harness 共 **14 项**，覆盖批准/拒绝、稳定 sibling 身份、None 重放、后继调用、未知 Job、legacy 入口隔离、owner/决定缺失、任务续租、稳定展示消息 ID、损坏的 graph_state 身份、准备期间取消，以及原生工具结果已持久但应用结算服务故障后的恢复。结算故障注入后，receipt 为 delivered 且未 applied；再次调用发送 None、补齐应用证明，实际工具效果仍只有一次。此窗口为同进程注入，不是独立 Worker kill 验收。

损坏 binding 用例首次复现原生 Pydantic ValidationError；已在 Runtime 边界转换为明确的 FrameworkApprovalRecoveryRequired。当时已启动的较广回归加载的是修正前代码，最终为 **1 failed / 287 passed，431.77 秒**，唯一失败为该错误分类，不计为全绿。修正后的新文件回归 **13 passed，39.93 秒，无 skip**；补齐准备期间取消后 **14 passed，41.74 秒，无 skip**。中间结果重叠，不累计。

最终代码在 `Agent_Server` 运行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_runtime.py tests/test_deep_agent_runtime_adapter.py tests/test_framework_approval_results.py tests/test_framework_tool_bridge.py tests/test_agent_v2_worker_contract.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py -q -rs --tb=short
```

结果 **174 passed，166.74 秒，无 skip**。这是最终代码的受影响边界回归，不与前述旧代码运行的 287 passed 相加。4 个涉及 Python 文件 `py_compile`、`git diff --check` 通过。未运行独立 lint/type-check、前端构建、全量系统、真实供应商模型、独立 Worker kill/recovery 或领域 Runner 验收；本轮使用 fake model 与受控工具效果，未将它们计为通过。本阶段不新增依赖、配置或数据库迁移，不开放新 Turn 路由或 Worker continuation 能力。

## 19. P2.2 HTTP 受理、v2 队列与 Worker 审批能力（2026-10-26）

在 §18 的 Runtime/Session 接线之后贯通正式决定受理入口。复用 `FrameworkApprovalSubmission` 及既有 task/outbox，不建立第二个调度器。

- 在 submission 服务提供只读 canonical binding 查询，由 task store 暴露异步端口。依据 approval → 持久化 link → runtime owner 的原绑定路由，即使 pending_turn 已闭合也能返回原任务回执；缺 link/损坏 binding 不返回 legacy。查询只提供路由事实，决定受理事务重新检查身份、活跃 Turn、项目与冻结决定。
- Dispatcher 根据该绑定创建 `AgentTaskCommandV2`，调用已有原子 v2 决定提交端口，写入独立 v2 stream 的 outbox；旧审批继续使用 v1。重复同一决定返回同任务，改变 reason/client_request_id 不建立新 intent，冲突决定由审批 API 返回 409；格式/对账错误维持 400，缺失资源为 404。
- Session 在长执行锁之外将普通绑定审批交给数据库受理事务。实际 B handler 等待时，HTTP 可以提交 A，原 B 租约/owner 保持，A 仍不能并发领取；B 保存结果时保留 A 原身份及已提交的拒绝状态。录制、TestRun 专用入口和代理审批仍走原领域分支；未配置 durable dispatcher 时不允许绑定审批落入本地旧循环。
- 新增真实 HTTP → outbox → RedisTaskWorker → Dispatcher → Session → 官方 Deep Agents / PostgreSQL → ToolJob 结果链路用例，覆盖 B 批准或拒绝后 A 继续、终态不重领、重复投递不重执行，以及两项 delivered/applied 事件各一份。依据该出口打开 v2 Worker `approval_resume`，manual_resume / coordinator_child / coordinator_resume 仍拒绝。

### TDD 与验证范围

初始 HTTP 文件 **4 failed / 2 passed，22.50 秒**：复现仍生成旧任务/HTTP 400、执行中提交兄弟决定因长锁超时、闭合后只返回普通审批对象 200。接线后原 6 项 **6 passed，21.14 秒**。新增真实队列用例先 **2 failed，9.36 秒**，日志为 `agent_v2_capability_unavailable`；开启已实现操作后文件 **8 passed，25.97 秒**。之后补齐 HTTP TestRun/录制领域边界及未实现操作拒绝用例。

Harness 使用真实 PostgreSQL/Redis、官方框架、fake model 和受控治理工具，不是供应商或领域 Runner 验收。Worker artifact capability 使用与夹具 binding 相同的固定 lock 标识验证兼容路由；它不代替实际两套部署产物、升级排空或 rollback 验收。没有跨进程杀掉框架 Worker；不能从同进程真实队列成功推定 P2.3 通过。

前端目前仍按既有审批面板和事件刷新展示，尚未增加“决定已提交 / 执行中 / 结果已应用”的持久读模型。P2.2 全部、P2.3、共享预算、跨 Turn 原生 transcript、正式 AgentCaseExecution 图、逐模式 Harness 和真实供应商/Runner 验收仍未完成。本阶段不新增依赖、配置或数据库结构，不更改新 Turn 的运行时选择默认值。

在 `Agent_Server` 设置 `RUN_LIVE_POSTGRES_TESTS=1` 后运行：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_http.py tests/test_framework_approval_runtime.py tests/test_framework_approval_submission.py tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
```

最终结果 **141 passed，256.56 秒，无 skip**，包含 HTTP Harness 11 项及新增的 3 项未实现操作拒绝检查。前一轮相同文件集合（未带 -x）为 **1 failed / 140 passed，249.87 秒**，失败在原有 `test_busy_owner_lock_defers_claim_without_consuming_an_attempt` 的 0.8 秒外层等待超时；定向 binding/Worker 两文件为 **30 passed，37.82 秒**，随后上面整组重跑通过。没有改该测试的等待上限、断言或生产锁超时机制；该次超时的具体成因未进一步确认，保留失败事实，不将两次结果相加。

8 个涉及 Python 文件 `py_compile` 与 `git diff --check` 通过。未执行独立 lint/type-check、前端构建、全量系统、真实供应商、独立框架 Worker kill/recovery、真实双产物或领域 Runner 验收；这些未计为通过。前端现有 `resolveApproval` 调用消费 202/409 的通用成功/错误路径，但其批准后的乐观“执行中”标签仍需下一阶段按持久事实修正。

## 20. P2.2 审批持久读模型与逐项前端展示（2026-10-26）

依据方案 §4.2 的三个事实维度，以及 §17–19 已验证的决定回执、结果证明和队列接线，新增只读 `FrameworkApprovalReadModel`。它不调用图、模型或工具，不改写业务状态，也不根据 SSE 历史推断应用结果。

- `SessionDetail.framework_approval_progress` 为独立公共 DTO；读取 pending_turn，已闭合时读取 control.last_turn_id，只展示当前/最近 Turn。录制、TestRun 和代理子审批保留专用入口。无框架审批时返回空列表，旧 inbox 不因展示逻辑要求初始化无关 ToolJob 表。
- 对存在框架审批的 Turn，用一个 SQL statement snapshot 联合读取审批、原生映射、owner、决定回执、continuation task、ToolJob 与 Session 租约。展示 awaiting_decision / decision_submitted / executing / applying_result / applied / recovery_required；不增加持久状态列或第二套调度。
- applied 要求已有 application 证明，且决定哈希、终态 Job、输出哈希及 outcome 一致；terminal Job 无应用证明时仍为 applying_result。缺失决定回执、业务身份改变、已应用输出被修改、运行任务失去 owner/Session 租约时显示 recovery_required，并记录可检索原因。等待实际 handler 调用前的失租也不能显示为普通排队。
- 公共 DTO 只含业务身份、状态、时间和 outcome；不暴露 resume 参数、interrupt ID、owner token 或 proof。每项附数据库 statement_timestamp 的 observed_at，防止延迟到达的 POST 回执覆盖更新的 GET 观察；时间相同时保留当前观察，后续 GET 继续对账。
- 前端使用实际 202 回执，只合并对应审批的进度，避免旧 sibling snapshot 恢复其它审批。异步受理后不再声称工具已执行或拒绝结果已应用；临时工具消息保留未结束标记。并发决定通过读取 revision 使旧 GET 失效，watcher/navigation generation 继续承担停止和切换生命周期，二者不混用。
- `ApprovalDecisionCard` 从原卡片提取，按每项 props 计算录制细节；面板显示全部待审批项，可先操作 B，再操作 A。已决定项单独展示进度，最后一个 pending 消失后工作台仍挂载面板。SSE 连接/重连及 queued/delivered/applied 事件触发持久读取。applied 文案为“结果已记录”；拒绝、治理阻止有各自文案，不将其解释为测试通过。
- 新字段后端默认空列表，TypeScript 为可选字段并兼容旧 payload。未增加依赖、数据库迁移、配置或新 Turn 默认路由；没有改变正式用例、领域 Runner、证据裁决或业务审批枚举。

### TDD 与验证范围

后端初始红灯 **1 failed，9.07 秒**：HTTP GET 缺少 framework_approval_progress。首轮实现 **8 passed，28.15 秒**，同时暴露枚举/时间字符串赋值的序列化警告；公共 DTO 使用赋值验证后消除。补充“已领取但未 dispatch 时 owner 过期”用例先 **1 failed / 8 deselected，7.42 秒**，随后修正按运行 continuation 的 authority 判断。

前端初始 **6 failed / 13 passed**，复现只显示第一项、忽略 202 进度、SSE 重连不刷新。实现后定向 **24 passed**。之后并发响应之间的旧 GET 用例先失败，再将读取 revision 与生命周期 generation 分离；另补 watcher 停止期间回执返回、POST 旧观察晚到等场景，晚到回执也先失败再修正。首次 build 的 TypeScript 类型收窄和测试回执类型不完整错误如实保留，已修正后重新构建，不以 vitest 通过代替类型检查。

Harness 使用真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph、fake model 和受控治理工具；前端使用 jsdom/Vue/Pinia，包含实际工作台父组件的挂载检查。没有运行真实浏览器视觉/端到端验收、供应商模型、独立框架 Worker kill/replacement、真实双产物升级或领域 Runner；不能由本阶段推定这些出口已通过。P2.3、共享预算、跨 Turn 原生 transcript、受控 AgentCaseExecution 图与逐模式 Harness 等仍在整体迁移范围内，尚未完成。

最终后端回归，在 `Agent_Server` 运行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_progress.py tests/test_framework_approval_http.py tests/test_framework_approval_runtime.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
```

结果 **89 passed，163.68 秒，无 skip、无序列化警告**，包括新读模型 9 项。较早运行的同组旧代码结果为 88 passed（新增失租用例当时尚未被收集），不与最终结果累加。

最终前端回归，在 `agent_web` 运行：

```powershell
$taskFiles = @(rg --files src/stores -g 'session*.test.ts')
npm test -- $taskFiles src/components/chat/ApprovalPanel.progress.test.ts
npm run build
```

结果 **9 files / 49 tests passed**；`npm run build` 内含 `vue-tsc --noEmit`，类型检查及 Vite 生产构建通过。6 个涉及 Python 文件 `py_compile`、`git diff --check` 通过。没有执行独立后端 lint/typecheck 或全量系统回归；上述明确列出的范围是本阶段验证结论。

## 21. P2.3 持久中断与独立 Worker 故障 Harness（2026-10-26）

依据方案 §5.3、§6 及既有 Session → owner → task 锁顺序，新增 `FrameworkTaskControl`，供现有 interrupt API 和 `interrupt_if_busy` 提交事务共用。此前只有 `interrupt_requested` 标记，Worker 轮询前仍持有写 checkpoint 的权限；这属于执行权撤销缺失，需要在持久事务边界解决。

- 同一事务中定位当前绑定、撤销 owner、过期租约、结束当前 claimed/running attempt 为 interrupted，并将同 Turn 尚未领取的续跑任务置为 cancelled。终态任务不重置；不分配取消前尚不存在的 attempt。Session、事件、任务和 owner 一起提交或回滚；跟进输入受理失败时，旧 owner 仍有效。
- 优先处理正在执行的任务，其次 pending_turn；保留不属于当前 Turn 的排队消息。新跟进输入排除自身，防止空闲会话提交时取消自己。重复中断通过持久 binding 引用幂等，不重复产生中断事件；绑定损坏不降级为 legacy 控制。
- 保留 pending_turn、审批、冻结决定、receipt 和所有 ToolJob 事实。未知工具效果继续保持 running 等原状态，已 applied 的结果和证明不撤销。中断不是撤销外部副作用；不自动恢复，不新增取消 API 或业务枚举。
- 原 owner 的 checkpoint、pending writes、heartbeat 和任务 finish 被既有 owner 校验拒绝。Session 读取不再因为另有排队消息而将绑定中断投影成“排队中”，也不将旧 waiting snapshot 显示为可直接恢复。该绑定待审批动作隐藏，持久进度显示 recovery_required；已经合法 applied 的项仍显示 applied。前端修正 pending/recovery 的翻译键并补回归。

### TDD、故障注入与实际边界

新文件首轮 **5 failed，16.94 秒**，其中 claimed 状态直接写 checkpoint 的一项测试设置违背既有 running-only 契约，已改为仅 started 参数写入初始 checkpoint；其余失败复现缺少即时撤销。随后新增空闲跟进自取消用例、停止后的 pending 审批进度用例、排队消息覆盖中断投影用例，均先失败再修正。前端 pending/recovery 翻译用例也先失败再修正。

Harness 使用真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph、fake model 和受控工具。两个独立 spawn 进程实际运行 v2 Redis Worker → Dispatcher → Session → Runtime → 官方图/治理工具链：一项在 prepare 前等待，中断后释放，确认无外部工具派发；另一项在工具递增 Redis 外部计数后等待，父进程实际 terminate 并确认非零退出，再过期原 Session 租约，确认任务进入 recovery_required 终态，重复领取仍是原 attempt，外部计数保持 1、ToolJob 结果未知、决定尚未 applied。后者只覆盖效果已产生而工具尚未返回这一真实强杀窗口。

此前两个使用 multiprocessing.Event 的运行在最后强杀场景未结束，确认没有存活子 Worker 后停止了对应 pytest；未将它们计为通过。没有获取卡住线程的栈，不能断言具体卡点；共享 Event 在强杀后可能遗留信号量锁，符合官方 [Python 3.11 terminate 警告](https://docs.python.org/3.11/library/multiprocessing.html#multiprocessing.Process.terminate)。改为 Redis 消息门闩后首轮 **1 failed / 10 passed，39.90 秒**，实际错误是默认客户端 5 秒 socket timeout 短于 BLPOP 等待；依据已安装客户端源码，将每次阻塞设为 1 秒，并用总超时控制整个等待。最终新文件 **12 passed，44.36 秒**，未放宽外部计数、终态不可重领或真实杀进程的断言。

安全对账/后继任务、ToolJob/Runner 原子执行权、旧 Worker 迟到业务结算、其它 resume/proof/业务投影强杀窗口与 A 决定最终继续仍未实现或验收，**P2.3 及整体迁移未完成**。没有新增依赖、数据库结构或配置，不改变新 Turn 默认路由。没有运行真实供应商模型、真实浏览器或领域 Runner；fake model/受控工具不替代这些验收。两次被停止的旧 pytest 不能保证执行了 fixture 清理，没有按测试名前缀批量删除遗留资源。

### 最终验证

在 `Agent_Server` 设置 `RUN_LIVE_POSTGRES_TESTS=1` 后运行：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_task_control.py tests/test_framework_approval_progress.py tests/test_framework_checkpoint_fencing.py tests/test_framework_approval_runtime.py tests/test_framework_approval_http.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
```

后端最终结果 **113 passed，248.11 秒，无 skip**，包括新文件 12 项；不与前述定向结果累计。两次原 Event 运行的中止不记为通过，Redis 门闩首轮客户端超时也不记为全绿。

在 `agent_web` 运行 4 个相关测试文件：`ApprovalPanel.progress`、`sessionFrameworkApproval`、`sessionEventRefresh`、`sessionApprovalLifecycle`，结果 **4 files / 28 tests passed，6.40 秒**。`npm run build`（含 vue-tsc）通过，Vite 构建 8.53 秒；6 个涉及 Python 文件 `py_compile`、`git diff --check` 通过。没有执行独立后端 lint/typecheck 或全量系统回归。

## 22. P2.3 同步工具执行权、未知返回与迟到观察（2026-10-26）

依据方案 §6.2、现有 ToolJob 原子领取/输出 CAS 和 §21 的持久中断，补齐同步工具写入权限。checkpoint 的 owner 校验不会自动保护应用 ToolJob；本阶段在工具存储的实际事务中复用 runtime owner，不新增调度器或第二套任务状态机。

- 新增 `framework_tool_authority.py`，统一 Session → runtime owner → ToolJob 锁顺序、数据库时钟租约、绑定和原执行身份校验。领取冻结 binding、epoch、task、attempt、worker 的非敏感引用，不把 owner token 写入该引用。整行保存不能替换冻结调用 ID、输入、绑定或执行身份。框架投影已有显式 owner 的事务传递该 owner；普通异步路径由 ContextVar 经 `asyncio.to_thread` 捕获原身份。
- 创建、领取、心跳、输出观察、输出结算、finish、child-wait 和附件写入共用校验。缺少 owner 不能将已绑定 Job 当 legacy 写；下一合法协调代次也不能替代原执行结算。原有领域动作准入在其 Project → item/attempt → Job 锁之前校验框架 Session/owner，保留领域固定版本、租约、逻辑操作和消费证据约束。普通 stale reaper 排除绑定 Job，保留未知执行事实。
- 框架直调与治理已领取工具均使用原子 claim；不再走旧的整行 mark_running。实际 handler 前执行短事务派发准入，成功提交后记录 admitted 日志；准入前已撤销的 owner 不进入 handler。**准入提交不与外部请求原子绑定**：已准入后撤销仍不能保证物理请求尚未发送，也不能撤销效果，不能将此实现描述为外部 exactly-once。
- 失租写入原样抛出，不转成失败 Job。工具抛错且无可靠返回检查点时，保留未知 Job 并抛 `FrameworkApprovalRecoveryRequired`，原生图停止，不让模型继续推理或再次发送。工具已经返回并保存观察、后续附件处理期间失租时，原观察保留，旧 Worker 不能结算。
- 迟到返回在普通输出写入被拒绝后，使用既有 inline ToolArtifact 保存 `framework_late_output`。事务验证原 Job 的冻结执行引用，按 Job/执行引用/内容哈希/观察状态生成稳定 ID，`ON CONFLICT DO NOTHING` 追加并去重；标记 `authoritative=False`，不更新 Job、receipt、application proof 或测试裁决。内容经过现有工具输出脱敏路径；没有新表、schema、依赖或配置。这个追加入口不调用普通附件 upsert，不声称任意内部附件修改接口已经改为不可变。

### TDD 与兼容性边界

新文件首轮 **11 failed，31.57 秒**，主要复现缺少执行权校验；原生 adapter 既有异常契约会包装为 `DeepAgentRuntimeError`，其中一项测试相应核对原始错误类型文本，未修改 adapter。初次实现后 **11 passed，30.88 秒**。扩展未领取直调、派发前撤销和下一代结算用例，先 **1 failed / 2 passed，11.74 秒**，暴露直调缺少原子执行引用，修正为共用 claim。unknown claim 用例先 **1 failed，7.27 秒**；效果后 TimeoutError 用例先 **1 failed，7.10 秒**，再修正未知效果不得结算为普通失败。加强后的超时用例先拒绝 A，再让最终 B 产生效果后丢失响应，断言无下一次模型调用。

已有四份框架测试直接在无 owner 上下文中制造 running/terminal Job。新契约使初次受影响回归 **1 failed / 18 passed，51.37 秒**；仅修正这些 setup，在原任务 `framework_owner_scope` 内制造同样事实，保留未知效果/拒绝/终态断言。较早框架集合 **84 passed，208.70 秒**、较早新文件 **17 passed，47.68 秒**不是最终代码验收，不与最终结果相加。

领域动作准入首轮 **1 failed / 15 passed，146.85 秒**，失败在 `test_actual_edited_context_parameters_and_expired_lease_preserve_pending_job` 的最后过期拒绝，原输出为 `DID NOT RAISE CaseActionClaimUnavailable`；定向重跑 **1 passed / 16 deselected，15.11 秒**。代码确认 `_write_item` 同时更新平铺 lease 列和 JSON record，不能将失败归因于写错权威存储。夹具原来用宿主时钟减一秒构造过期，而生产准入用数据库时钟；后续只读探测观察到数据库领先宿主约 1.234 秒，但没有捕获失败当时的时钟/租约，**不足以证明原失败根因**。仅加强夹具：用 `clock_timestamp()-INTERVAL '1 second'` 构造输入，并在数据库中断言已过期。完整重跑 **17 passed，163.21 秒，无 skip**；未放宽拒绝、pending Job 不变或零外部请求断言。

新契约保留无绑定旧路径；已启动但没有冻结执行引用的历史绑定 Job 要求对账，不能自动认领为当前 epoch。持久后台 Runner 的独立执行权、幂等对账/安全后继、其余跨进程强杀窗口、剩余审批最终继续、共享模型预算、跨 Turn 原生 transcript 与逐模式 Harness 仍未完成。新 Turn 默认路由未改变，P2.3 和整体迁移保持未完成。

### 最终验证

在 `Agent_Server` 运行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_tool_authority.py tests/test_framework_task_control.py tests/test_framework_approval_continuation.py tests/test_framework_approval_projection.py tests/test_framework_tool_bridge.py tests/test_framework_approval_progress.py tests/test_framework_approval_runtime.py tests/test_framework_approval_http.py -q -x -rs --tb=short
```

结果 **118 passed，319.38 秒，无 skip**，其中新执行权文件 23 项。包含真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph、fake model 和受控工具；§21 的独立 Worker 强杀 Harness 也随本组回归通过。

同样在 `Agent_Server` 设置 `RUN_LIVE_POSTGRES_TESTS=1`、`RUN_LIVE_PLAN_RUN_CREATION=1` 后运行：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_tool_operation_observation.py tests/test_deep_agent_tool_replay.py tests/test_tool_runtime_heartbeat.py tests/test_tool_executor_safety_gate.py -q -x -rs --tb=short
```

结果 **26 passed，52.23 秒，无 skip**。领域动作准入在 `RUN_LIVE_POSTGRES_TESTS=1`、`RUN_LIVE_DOCUMENT_IMPACT_TASKS=1` 下运行 `tests/test_case_action_admission.py`，最终结果 **17 passed，163.21 秒，无 skip**，失败及夹具调整见上文。以上结果分别报告，不累计重复或较早版本运行。

最后在 `RUN_LIVE_POSTGRES_TESTS=1` 下运行：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
```

结果 **55 passed，68.12 秒，无 skip**。14 个涉及 Python 文件 `py_compile`、`git diff --check` 通过。未运行独立后端 lint/typecheck、前端测试/构建、全量系统或真实模型供应商/浏览器/领域 Runner 验收；本阶段未改前端，受控工具与 fake model 不替代真实领域出口。

## 23. P2.3 已知结果审批的对账与安全后继（2026-10-26）

依据方案 §5.2–5.3、现有 Session → owner → task 锁顺序，以及 continuation 的冻结决定/原生事实检查，新增 `FrameworkApprovalRecovery`。官方 [Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop) 基于 LangGraph interrupt；[LangChain per_call](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) 按 interrupt ID 决定单个调用；[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 保存 thread 执行状态。这些能力不提供应用审批、外部效果与 AgentTask 的共同事务，应用必须实现对账。复核了本地《Agent测试专项》《Agent轨迹与协议鲁棒性》对工具轨迹、重复执行及恢复的要求。

### 实现边界

```text
approval_resume / approval_recover 原执行 failed 或 recovery_required（不可变终态）
    → framework_reconcile（只核对持久事实）
    → approval_recover（有 proof 的新任务）
    → 既有 owner-bound 审批准备、Command/None、工具与应用投影链
```

- `ensure_framework_reconciliation` 复用 Session 锁、现有 task/attempt、确定性 UUID5 和任务/outbox 事务。同一失败原 task/attempt 的并发入队复用同一对账。原执行终态投递在 ACK 前受理；执行结束也检查持久状态受理，覆盖异常和返回 failed 响应。原任务与 attempt 不重置、不重领。
- 对账检查原执行身份、同 Session/project/binding/pending Turn、用户显式中断、冻结决定、原生 interrupt/resume、ToolJob 身份及全部同 Turn Job。只允许终态或真正未启动的 waiting_approval；任何 running/未知效果都阻止后继，包括存在 §22 非权威迟到返回的情况。不调用图、模型或工具，不自动把观察提升为正式结果。
- 后继/outbox、proof、对账 completed/reconciled 及 Session waiting_approval 投影在同一个业务事务提交。proof 保存原 task/attempt、decision ID/hash、原生消费事实和 Job 输出哈希。后继领取与 prepare 均复核已完成父对账及 proof；继续时仍重新读取实际原生事实，不能凭缓存 proof 选择 Command/None。
- 对账和后继优先于同 Turn 排队 sibling；普通未来 Turn 不能替换未结清的绑定 Turn。显式用户中断撤销对账 owner，禁止发布后继。内部对账/后继输入不出现在用户 pending_input_queue。审批进度使用 lateral join 选最新审批执行，避免旧失败任务遮蔽后继或造成重复行。
- Coordinator 查找活跃业务父任务增加 approval_recover，保留 durable 父身份；framework_reconcile 不属于业务派发执行。这项消费方修正不代表 v2 Coordinator 工作流已经迁移或验收。
- `AgentTaskCommandV1` 保留原 operation Literal，拒绝新操作；v2 和任务记录支持新增两项，TypeScript union 同步。没有新数据库表、列、索引、依赖、配置、公共 API 路由或默认路由变化；恢复输入/proof 存既有 JSONB。

### TDD、真实强杀与失败记录

初始新文件 **6 failed，19.11 秒**，复现缺少对账受理；首轮实现 **6 passed，20.84 秒**。Sibling 排序用例先 **1 failed / 6 deselected，7.21 秒**（未抛 TaskDeferred），再修正恢复链优先级；未来输入用例先纠正 fixture 为 queued，再实际复现 claim 未 defer，修正后新文件 **8 passed，28.33 秒**。内部队列用例先纠正字段位于 detail.metadata，之后实际复现内部任务暴露，再修正投影。

独立进程 Harness 复用 §21 的真实 spawn Worker、Redis 门闩、官方框架与真实 PostgreSQL。B 工具产生 Redis 计数并已保存终态 Job/native 结果，Worker 在 applied 投影前等待；父进程 terminate 并确认非零退出，过期原租约。随后分别启动独立 Worker 对账、执行后继、处理 A 的拒绝决定。断言 B 外部计数始终为 1、两项 receipt 均 applied、pending Turn 闭合、原失败 task/attempt 完全保留。该用例单独运行 **1 passed / 8 deselected，28.96 秒**；不代表未知效果窗口也能自动继续。

审查新增 Coordinator 父任务用例先 **1 failed / 1 passed，5.95 秒**（approval_recover 返回 None）；同步消费方后 **2 passed，4.22 秒**。新增 failed 响应/异常两种入口用例先 **1 failed / 1 passed，9.41 秒**，发现只有异常受理对账；将入口收敛到执行结束后的持久状态检查，再定向 **2 passed，9.46 秒**。

首轮大组回归 **1 failed / 166 passed，454.52 秒**：`test_http_approval_resolution_is_atomic_idempotent_and_worker_owned` 的 Worker 返回 0，实际 deferred。根因是扩展框架领取判断时丢失 legacy approval_resume 在 waiting_approval 状态下的原有通行条件；已恢复该条件，不修改旧用例断言。首轮不是全绿；后续完整结果见下节。

### 交付限制与下一出口

当前自动恢复只支持已知结果的审批执行。对账任务自身崩溃后没有新的自动对账后继；首次 message 故障、目标查询/人工处理未知效果、其它 resume/proof/结算强杀窗口、领域 Runner 独立执行权仍待实施。共享调用预算、跨 Turn 原生上下文、受控 AgentCaseExecution 图及逐模式 Harness 等继续属于整体目标，P2.3 与整体迁移均未完成。

新操作复用 schema v2，runtime_version 仍为 v2.0.0；旧 v2 严格解析器可能在能力检查前拒绝新操作。启用恢复链前需要排空/停止旧 v2 消费者并统一更新 Worker，或先完成版本隔离部署，不能声称旧新 v2 同流滚动发布已兼容。生产默认 v2 路由保持关闭，真实双制品/排空/回滚未验收。

未运行真实模型供应商、真实浏览器或领域 Runner；fake model/受控工具不替代这些出口。前端仅同步任务 operation 类型，未改变组件行为。

### 最终验证

在 `Agent_Server` 运行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py tests/test_framework_approval_continuation.py tests/test_framework_approval_progress.py tests/test_framework_task_control.py tests/test_framework_tool_authority.py tests/test_framework_approval_runtime.py tests/test_framework_approval_http.py tests/test_framework_approval_submission.py tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py tests/test_coordinator_claim_snapshot_save.py -q -x -rs --tb=short
```

最终结果 **225 passed，483.00 秒，无 skip**，包括本阶段新文件 16 项、真实独立 Worker 强杀接管、旧审批 HTTP 及 Coordinator 相关回归。该结果覆盖修正后的最终业务代码，不与首轮或定向通过数累计。

在 `agent_web` 运行 `npm run typecheck`（vue-tsc）通过；13 个涉及 Python 文件 `py_compile`、`git diff --cached --check` 通过。未运行独立后端 lint/typecheck、前端组件测试/生产构建或全量系统测试；本轮前端仅同步类型，实际供应商、浏览器、领域 Runner 和发布验收仍按前述范围保留待办。

## 24. P2.3 对账自身失租后继与提交前后强杀（2026-10-26）

上一轮为有效进展：提交 `5b868f927`，225 项受影响回归通过。当前工作区核对该提交后继续方案 §5.3；本阶段解决运行中 framework_reconcile 失租后终态无法继续的问题，不将原任务重置为 queued，也不将 Redis 传输重试当作业务恢复。

### 实现与不变量

- 复用 `FrameworkApprovalRecovery`，将审批原执行与对账前驱分开。首次对账维持旧输入；后续对账 reference_id 固定指向原审批执行，parent_task_id 指向失租对账，JSONB 增加 predecessor_task_id/predecessor_attempt_id，origin_task_id/origin_attempt_id 始终保持原审批身份。没有新表、列、索引、依赖、配置、operation 或公共路由。
- 只对运行任务经实际领取逻辑判定为 recovery_required、execution_owner_expired 且有原 attempt 的对账受理后继。明确返回的 ConnectionError、安全校验拒绝、未知副作用、用户中断不会生成这类重试。这个限定防止确定性拒绝自动产生重试链；不声称为任意基础设施错误提供了自动恢复。
- Session → owner 锁内复核活跃 Turn、binding、project、原审批 attempt 和前驱失租事实；领取同时要求 canonical owner 仍指向前驱 attempt 或当前执行。确定性 UUID5、同 Session 锁及现有任务/outbox 事务，使同一前驱的并发受理只产生一个后继。连续失租保留每一个终态 task/attempt。
- 领取时前驱证明不匹配，任务直接成为 recovery_required，attempt_no 保持 0，并记录 framework_reconciliation_changed；不会在无法证明的身份上分配 owner。旧对账 owner 在后继接管后不能发布审批后继。
- Dispatcher 在终态重投 ACK 前及正常执行结束后为对账检查后继受理。已 completed/reconciled 的对账不建立新对账，其已经原子发布的 approval_recover/outbox 继续使用；未知结果拒绝的对账也不会自动再排队。
- 审批后继仍经过原有 proof、实际 native 消费事实、ToolJob 和 owner 检查，决定是否发送指定 Command 或用 None 继续。用户停止语义、原始审批、已保存工具结果和领域裁决边界保持原契约。

### TDD 与故障证据

新增前 6 项首轮 **3 failed / 3 passed，21.18 秒**：失租对账没有新任务、发布回滚未进入实际事务、无法校验后继的前驱 attempt。另三项验证确定性错误不重试及用户停止。实现后新文件首次 **22 passed，80.97 秒**；此时尚未加入后续两个强杀参数和连续三次失租参数，不作为最终代码验收。

随后扩展已有独立 spawn Worker Harness，保持真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph、fake model 和受控工具：

1. 原审批 B 已完成工具和 native 结果、尚未写 applied 时，先杀掉原 Worker。
2. 对账提交前：在实际同步数据库事务内完成后继/outbox、proof、对账与 Session 写入，再通过同步 Redis 门闩等待；父进程 terminate 并确认非零退出。数据库真实回滚，没有 approval_recover 残留；对账运行失租后，另一个 Worker 执行新对账与审批后继。
3. 对账提交后 ACK 前：应用服务事务已返回，通过异步 Redis 门闩等待，再实际 terminate。重投读取 completed 对账，不再建立对账任务；另一个 Worker 领取已存在的唯一审批后继。
4. 两个场景最终均应用 B 与 A 的决定（A 拒绝）、闭合 pending Turn，B 的 Redis 外部计数始终为 1，原审批失败历史不变。对账提交前场景还核对失租对账终态未被修改。

这两个新增强杀参数定向结果 **2 passed / 22 deselected，55.77 秒**。同步门闩运行在真实事务线程，使用每次 1 秒 BLPOP 与总超时；没有模拟 rollback，也不使用强杀可能遗留锁的 multiprocessing.Event。补充连续三次失租、并发受理、前驱 attempt 篡改、发布注入失败后重试及旧 owner 拒绝，用于最终回归。

### 剩余边界

本阶段完成对账自身运行失租的恢复与这两个提交窗口。首次 message 没有可用 checkpoint、未知效果的外部查询/人工对账、其它 resume/proof/业务结算窗口、领域 Runner 独立执行权仍未完成，P2.3 保持未完成。共享模型预算、跨 Turn transcript、受控 AgentCaseExecution 图、逐模式 Harness 和真实发布继续属于整体目标。

没有新 operation 或绑定版本，但前驱 JSONB 契约及领取校验要求本阶段 Worker；§23 的旧 v2 消费者排空/统一升级限制继续适用。没有更改默认路由，未进行供应商模型、浏览器、领域 Runner 或真实发布验收。没有前端文件改动。

### 最终验证

在 `Agent_Server` 运行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py tests/test_framework_task_control.py tests/test_framework_checkpoint_fencing.py tests/test_framework_approval_continuation.py tests/test_framework_approval_progress.py tests/test_agent_v2_worker_contract.py tests/test_runtime_binding_persistence.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
```

最终结果 **162 passed，393.60 秒，无 skip**。包含恢复文件最终 25 项、三个审批/对账强杀参数、§21 原中断强杀、owner/checkpoint、业务审批继续、进度、绑定、旧任务与 Session 所有权回归。计数不与较早定向结果累加。

5 个改动 Python 文件 `py_compile`、`git diff --check` 通过。没有运行独立后端 lint/typecheck、前端测试/构建、全量系统回归或真实供应商/浏览器/领域 Runner/双制品发布验收；本阶段未改前端或模型适配器。真实 PostgreSQL/Redis 与官方框架的受控工具结果不替代上述实际领域验收。

## 25. P1 根预算与模型尝试账本基础（2026-10-26）

从 `7b72f5590` 的干净工作区继续检查首次 message 恢复。`SessionService.execute_claimed_message` 先读取输入、组装请求；`_run_owned_submission_locked` 将 pending_turn 清空后开始执行，现有自动对账仅覆盖审批执行。首次 message 的模型请求、响应和用量没有共享持久账本，不能以“没有 ToolJob”或“已有某个 checkpoint”推断整个 Turn 可以重跑。依方案 §5.2、7.2，先补这一前置存储契约；首次 message 恢复仍未实现。

### 依据与实现范围

- 当前 `ModelRuntimeService.resolve_langchain_chat_model` 配置模型对象后由框架直接调用，不经过 DTO `invoke`；本阶段不将对象工厂视为已提供真实调用治理。
- 已检查固定 `langchain==1.4.6` 的 `model_call_limit.py`：before_model 检查图状态计数，after_model 增加 thread/run 计数。该能力可继续用于分支本地限额，但不提供应用根 Turn、并行分支、发送意图和未知尝试的原子账本。
- [官方 middleware 文档](https://docs.langchain.com/oss/python/langchain/middleware/custom) 提供模型 wrap 接口；[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 区分 thread checkpoint 与应用持久事实。应用账本沿用 `PostgresDocumentImpactCalls` 的“准备 → 发放发送许可 → 保存响应/未知”模式及现有 owner 锁，无新增依赖。
- 复核本地 `Agent测试专项.html` 的重复执行/幂等测试及 `Agent轨迹与协议鲁棒性.html` 的恢复和预算章节；本阶段同时断言轨迹阶段、预算持久事实、并发与终态历史。

新增 `schemas/framework_model_call.py` 和 `runtime/postgres_framework_model_calls.py`。数据契约包括冻结根策略、分支/逻辑调用/尝试身份、实际执行 owner 的非秘密身份、用途、模型配置/请求摘要、输入估算与输出预留、usage、unknown 原因及数据库时间。

账本 setup 可显式创建 `<agent_task_table>_model_budget` 与 `_model_call` 两表，外键归属现有 RuntimeBinding/owner。setup 不在请求内调用，本阶段未接生产初始化、任务受理事务或任何模型入口，只在 UUID 隔离表的 Harness 中执行并清理。没有修改既有表、配置、默认路由、公共 API、绑定版本或运行时依赖。

### 已验证的不变量

1. 所有变更在同一事务内按 Session → owner 加锁，复用 `assert_runtime_owner` 与锁后数据库时钟；prepare 可在 claimed/running 阶段预留，start 只接受 running。失租 owner 不能冻结、预留、发送或结算。
2. 根预算只冻结一次，包含调用/Token 准入额度、绝对 deadline 和允许的模型配置指纹。重新实例化 store、重新 initialize 或换有效 owner 都不会清空额度或延长 deadline。
3. prepare 对相同 attempt 幂等、不同请求/范围拒绝改写；同根的并行分支及三个独立 spawn 进程共享原子额度。子代理与摘要记录同样归属根 binding，分支 parent 固定。此项只验证存储端口，未证明真实子代理/摘要请求已调用该端口。
4. `ready → invoking` 在提交后只发放一次发送许可；后继不能直接发送旧 owner 的 prepared 尝试。它仍占原预算，未实现自动释放。start 不是供应商的幂等键，也不证明外部请求一定已发送或已停止。
5. `invoking → completed / unknown`：实际 usage 只结算一次，矛盾结果不得覆盖；缺 usage 的完成与 unknown 保留预留。真实用量高于估算时记录真实数值，阻止超额后新增及原已预留尚未发送的请求。额度使用估算/预留作为准入依据，不能保证未知的供应商实际 Token 绝不超上限。
6. 同 branch/logical_call 更换 attempt 必须显式引用唯一 unknown 前驱和相同请求；新尝试再次计数/占额。store 不自动重试，也不提供恢复重试的业务授权。实际重试策略、已知失败结算和未发送预留释放仍待接线设计。
7. 使用真实审批失租路径建立新 framework_reconcile owner，它可将旧 invoking 标为 unknown，保留旧请求的 owner 身份和预算。旧 owner 被拒绝；新 owner 不能冒充旧请求提交 usage；原审批任务/attempt 终态保持不变。
8. 预留或 usage 保存后的注入异常会真实回滚，没有部分额度或半结算。截止时间在预留和发送前复核；已发送请求的结果允许在 deadline 之后入账。

### TDD 记录与剩余出口

先添加契约测试，首轮为 **1 error，7.34 秒**：缺少 `postgres_framework_model_calls` 模块，未当作通过。实现后首批 **19 passed，42.60 秒**。继续添加超估后的未发送调用用例，先出现 **1 failed / 20 deselected，6.20 秒**（没有抛预算异常），据此将根预算复核增加到发送许可事务。真实后继测试还纠正了测试断言：expiry claim 的即时返回不是完整重新读取值，终态对比应使用实际持久 task。

加入独立进程并发及真实后继场景后，账本文件定向结果 **22 passed，53.95 秒，无 skip**。不与此前计数累加。进程通过 Redis 门闩同时进入 PostgreSQL 预留，父进程核对退出码、唯一剩余额度及清理；该场景未发送供应商 HTTP 请求。

本阶段实现账本基础，P1 及整体迁移保持未完成。下一出口是预算随首次任务受理冻结、主图/Deep Agents/具名子代理/摘要每次实际请求共用策略、实际 HTTP 尝试与流式故障验收、配置快照及请求/响应不可变资产、活动执行时长、迟到模型响应的非权威归档。首次 message 的恢复仍必须等待这些持久事实与恢复策略接通，不能将本账本单独作为安全重跑证明。

§23–24 的旧 v2 消费者排空/统一升级限制继续适用；没有真实发布、供应商、浏览器或领域 Runner 验收，也没有前端变更。

### 最终验证

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_calls.py tests/test_framework_checkpoint_fencing.py tests/test_framework_approval_continuation.py tests/test_runtime_binding_persistence.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
```

结果 **100 passed，156.09 秒，无 skip**，包含账本最终 22 项、owner/checkpoint 事务、审批继续、绑定与 Session 所有权回归。不与定向结果重复累计。

两个新 Python 实现与新测试文件共 3 个文件的 `py_compile`、`git diff --cached --check` 通过。没有执行独立后端 lint/typecheck、前端测试/构建、全量系统回归、真实模型/流式调用、浏览器/领域 Runner 或部署库迁移/发布验收；本阶段没有接通这些执行入口，不能将存储端口验证外推为全系统请求预算已生效。

## 26. P1 官方模型请求的显式治理与故障窗口（2026-10-26）

继续 §25 的共享调用策略前置工作。新增 `application/models/framework_model_call_policy.py` 与 `application/deep_agents/model_call_middleware.py`，在 `DeepAgentRuntimeAdapter` 增加可选的显式 policy 注入。必须捕获有效 owner-bound checkpointer，不能在执行中查找“当前最新 owner”代替原 owner。测试使用真实 PostgreSQL/Redis、官方 Deep Agents 与四家协议 SDK，HTTP/SSE 对端为本机合成响应。

### 依据与实现

- [官方 Custom middleware](https://docs.langchain.com/oss/python/langchain/middleware/custom) 及固定 1.4.6 的 ModelRequest/handler 契约：模型 wrap 接入主代理和每个已有具名子代理。结构化输出描述也纳入请求摘要，保留官方 ProviderStrategy/ToolStrategy 的执行路径。
- `ModelRuntimeService.resolve_langchain_chat_model` 在官方模型 metadata 保存构建时配置指纹。指纹复用既有非秘密参数白名单，地址/headers 只保存 hash，凭据不入账。每次请求重新解析配置，对比构建快照及冻结根策略；不支持静默切换模型配置继续原 Turn。
- 请求封套采用官方完整消息序列化、工具 schema 和调用设置；保存 SHA256，不保存 Prompt 正文。根、子分支、原生 task/namespace、用途和请求摘要构成稳定逻辑身份；每次发送产生独立 attempt。封套不是最终 wire 请求资产，后续仍需完整请求/响应证据。
- 输入预留采用 UTF-8 封套长度加显式 framing 估算，输出预留取配置/显式设置较大值。它是保守准入估算，不是供应商 tokenizer，也不保证供应商实际 Token 硬上限。prepare/start 在 SDK 前完成；原生 usage 只结算一次，缺 usage 保留预留。
- 固定 LangChain 1.4.6 摘要默认调用 `model.with_retry`，独立于主模型 wrap。固定 Deep Agents 0.7.13 的摘要向 LangChain 转发自定义 `summarizer`，因此替换同名摘要 slot，保留官方裁剪、Prompt、摘要状态和 backend，将摘要实际 `ainvoke` 单独接入根策略。摘要失败不触发默认三次重试。主分支和每个显式子代理分别注入，未开放自动 GP 子代理。
- 主/子代理和摘要实际调用关闭本地 LangChain 模型缓存，避免缓存命中被记成新的物理请求；不影响供应商 prompt cache 的 usage。实际 HTTP 503 每种传输只观察到一个请求，策略自身不自动重试。
- 发送异常/取消尝试归为 unknown，保留预留。响应已到达但结算确认失败时，不改写持久阶段，也不重发；记录 `framework_model_response_settlement_failed`，由后续对账检查实际是否已提交。失租不能写入 usage，迟到响应资产仍未实现。

### 两个真实失败根因

1. 具名子代理单独运行成功、顺序运行失败。最初 SSE fixture 将任何首包改写为固定文本，丢失原工具调用，已改为忠实输出原 tool_calls/finish_reason。修正后仍出现 `Error: task is not available.`，停止调整测试计数，追到官方 `register_harness_profile`：重复注册合并 excluded_tools，而不是替换。前一 Turn 隐藏的 task/文件工具永久污染同 provider 的后续 Turn。

   依据 [官方 Profile 合并语义](https://docs.langchain.com/oss/python/deepagents/profiles#merge-semantics) 和固定源码，改为全局仅注册禁用默认 GP 的不变 profile；每次建图独立实例化官方工具排除中间件，继续同时过滤 schema 和阻止猜测的禁用工具调用。复用了锁定版本的私有 `_ToolExclusionMiddleware`，未另写一套工具门控；这是需随依赖升级回归的兼容点。已经运行旧代码的进程须排空并重启，空集合重新注册不能清除其污染。

2. 初版缓存测试的 seed 使用 `bind_tools([])`，与 create_agent 的无工具请求不一致，产生假阴性。改为同样的官方 create_agent seed 后，先复现 HTTP 计数 1 而非 2，随后在实际治理边界关闭 cache。具名子代理测试显式先组装禁用能力的回合，再启用同 provider 的只读子代理，避免只在独立进程中偶然通过。

### TDD 与验收边界

新增测试首轮缺少 policy 模块，**1 failed，7.27 秒**。首批框架请求/摘要通过，随后子代理与结构化输出用例暴露上述 fixture/profile 和过宽拒绝问题；结构化输出改为记录官方 schema 描述。两个确定性根因用例修复前 **2 failed / 13 deselected，12.28 秒**，修复后当时的完整文件 **15 passed，45.96 秒**。

补响应结算提交前/后丢失确认、响应可见前取消与失租，首轮 **2 failed / 2 passed / 15 deselected，16.93 秒**：缺少响应结算失败事件。补可检索事件和原异常说明后纳入联合回归。这些是实际 SDK 请求后的故障注入，未把测试中的模拟故障当成供应商真实事故。

首次联合回归 **1 failed / 24 passed，78.98 秒**，独立进程预算测试在 Redis ready 门闩读超时。固定 Redis 源码默认 socket_timeout=5，而测试 BLPOP 等待 30 秒；单独复核 **1 passed，12.15 秒**，确认存在启动速度相关的测试时限错位。将父子门闩连接显式设为 35 秒，保留 30 秒服务端门闩；应用连接超时、业务预算和进程并发断言不变。

**仍未完成的出口**：生产 setup 和初始受理冻结、普通 DTO/受控 AgentCase 的共享策略、动态参数/超时/错误与显式重试策略、完整流式终止/断流语义、活动执行时间、不可变请求/响应和迟到模型证据、首次 message 安全恢复。没有修改公共 API、数据库 schema、路由开关或依赖版本；两个模型账本表仍只在隔离 Harness 显式初始化。

真实供应商、浏览器/领域 Runner、跨 Turn 上下文与双制品发布仍未验收。P1 与整个迁移保持未完成，默认 v2 生产路由保持关闭；§23–25 的旧 v2 消费者排空/统一升级限制继续适用。

### 最终验证

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_policy.py tests/test_framework_model_calls.py tests/test_framework_chat_models.py tests/test_deep_agent_runtime_adapter.py tests/test_langchain_model_adapter.py tests/test_framework_checkpoint_fencing.py tests/test_framework_approval_continuation.py -q -x -rs --tb=short
```

最终结果 **184 passed，229.80 秒，无 skip**，包含新策略最终 23 项、账本 22 项（含三个独立进程共享额度）、四协议 HTTP/SSE、既有 adapter、owner/checkpoint 与审批继续回归。新策略八项正常请求组合明确核对是否走流式端点，不能把普通返回误当流式通过。计数不与此前定向/失败结果累加。

7 个相关 Python 文件的 `py_compile`、`git diff --check` 通过。未执行独立后端 lint/typecheck、前端测试/构建、全量系统回归、真实供应商/浏览器/领域 Runner 或部署验收；未修改前端、依赖版本或生产初始化。方案 §1.3/F4 与 §7.3 已同步区分已验证的显式注入和待接生产出口。

## 27. P1 预算受理事务、Worker 准入与生产组装（2026-10-26）

继续 §26 的生产接线出口，复用现有任务受理事务、根模型账本和不可变 RuntimeBinding。没有新增模型工具循环、运行时类型、绑定版本或默认路由。模型预算不再只能在显式执行 Harness 中初始化和冻结。

### 依据与实现

- `PostgresAgentTaskStore._submit_message_in_transaction` 已把绑定、任务、输入消息与 outbox 放在同一事务。本阶段增加 `submit_v2(..., model_budget=...)` 和账本同步事务端口 `freeze_submission`，在 Session → owner 锁内先冻结预算，再写任务。预算纳入幂等内容摘要；并发受理只能保存一份，重复请求不能放宽调用/Token 额度或延长 deadline。预算冻结或后续 outbox 写入失败会真实回滚全部受理事实。
- 冻结端口与已有 owner 端口共用保存逻辑，校验冻结预算包含根模型指纹。仅未领取的 idle/epoch 0 Turn 可以首次创建预算；已领取/启动的历史 Turn 只接受一致的已有预算，不根据当前配置重建额度。历史缺预算使用可辨识的 `FrameworkModelBudgetMissing`，绑定身份错误仍显式失败。
- 生产 Dispatcher 注入账本，领取非终态且可能执行图的 v2 任务前读取原预算；历史缺预算以 `framework_model_budget_unavailable` defer，保持未领取。终态重复投递及 `framework_reconcile` 纯事实对账不被此预算检查阻塞；它们不因此取得模型调用权。现有显式测试构造可继续不注入账本。
- 应用 bootstrap 在已有 task/owner 初始化后 setup 两张模型账本表，保存 `app.state.framework_model_calls` 并记录初始化事件。受理成功、预算缺失 defer、实际模型调用和结算沿用现有结构化日志。沿用项目启动建表方式，没有在请求中 setup；未执行实际部署库升级验收。
- `RuntimeService` 增加 bound adapter 依赖，按不可变 `request.runtime_binding` 选择。生产组装将根 policy 注入 bound adapter；无绑定的原可选试点使用既有 adapter。两者共用 checkpoint provider、模型解析器和能力配置，provider 沿用原 cleanup 关闭一次。这样既能要求绑定执行具备 owner，又保持旧试点已有语义。

### TDD 与真实链路

1. 受理契约首次 **1 failed，7.13 秒**：`submit_v2` 不接受 `model_budget`。实现事务端口后，账本与当时受理用例 **28 passed，67.08 秒**。
2. Worker 契约首次 **1 failed / 1 passed / 6 deselected，8.44 秒**：Dispatcher 不接受 `framework_model_calls`。接线后受理文件当时 **9 passed，21.85 秒**。
3. 官方 SDK Worker 链路首次 **1 failed / 9 deselected，6.92 秒**：RuntimeService 不接受 bound adapter。接通后文件当时 **10 passed，26.23 秒**。
4. 新增真实官方 SDK 逐项审批及不同 owner 共用原预算，定向 **1 passed / 10 deselected，11.28 秒**。随后补充终态投递和纯对账两项准入边界，计数以最终联合回归为准。

新增 `test_framework_model_admission.py` 使用 UUID 隔离 PostgreSQL 表、Redis stream 与 checkpoint schema，清理自身资源。初始执行覆盖真实 outbox → Redis Worker → Dispatcher → SessionService → RuntimeService → 官方 Deep Agents/SDK → 本机 HTTP 合成响应 → 根账本结算；完成后重复投递不再发 HTTP，无绑定试点选择旧 adapter 且不修改该根预算。

逐项审批覆盖官方 SDK 首次产生 A/B 两项工具调用、实际业务审批投影与新 owner 恢复：批准 B 后仅执行 B，A 继续等待，不新增模型调用；拒绝 A 后发第二次模型请求。最终两条 completed 尝试共计 20 Token，根 max_calls=2 不变，实际模型调用的 owner_epoch 不同。该场景没有完整走 HTTP 审批 API/Redis 投递，不将已有其它层的验收重复算作该用例覆盖。

### 保留的出口与兼容边界

默认 `AgentTaskDispatcher.submit` 仍生成 legacy 任务；本阶段增加的是显式预算受理端口，尚未实现新 Turn 自动冻结实际模型/Prompt/Skill/工具制品及根预算的路由。`SessionService.execute_claimed_message` 的输入编排仍先于 owner 作用域，普通 DTO `ModelRuntimeService.invoke`、语义分类等前置模型请求尚未纳入根账本。不能把上述一条初始实链路外推为完整 Turn 全部调用受治理。

活动执行时长、完整请求/响应不可变资产与迟到证据、完整错误/重试策略、首次 message 安全恢复、跨 Turn 上下文及受控 AgentCase 图仍待实现。默认新路由保持关闭；§23–26 的旧 v2 消费者排空/统一升级限制继续适用。没有恢复未知副作用或给历史已执行任务补发额度。

未运行完整生产 lifespan/部署启动、真实供应商模型、浏览器/领域 Runner、全量系统回归、前端构建或独立后端 lint/typecheck。选定 lifespan cleanup 测试不等于完整应用启动验收；MySQL 参数没有开启环境，已在联合回归中显式 deselect，不作为通过或 PostgreSQL 验证替代。

### 最终验证

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_admission.py tests/test_framework_model_policy.py tests/test_framework_model_calls.py tests/test_runtime_binding_persistence.py tests/test_agent_v2_worker_contract.py tests/test_agent_task_dispatch.py tests/test_framework_approval_runtime.py tests/test_framework_task_control.py tests/test_framework_approval_recovery.py tests/test_deep_agent_runtime_adapter.py tests/test_lifespan_resource_cleanup.py -k 'not mysql' -q -x -rs --tb=short
```

结果 **227 passed / 1 deselected，480.61 秒，无 skip**。包含受理文件最终 13 项、模型策略/账本、版本绑定、v2/旧任务 Worker、审批执行/停止/已知结果恢复（含独立进程强杀）、适配器和选定 lifespan cleanup 回归。该计数不与此前定向结果累加；没有将 deselect 的 MySQL 用例计为通过。

6 个相关 Python 文件 `py_compile`、`git diff --check` 通过。没有新增依赖、配置、数据库表结构或公共 API；生产 setup 开始创建 §25 已有的两张模型账本表，部署角色需具备既有启动初始化所需权限。默认开关及实际部署状态未改变，P1 和整体迁移继续未完成。

## 28. P1 普通 DTO 与输入语义分类共用根预算（2026-10-26）

从 §27 的提交继续检查前置模型调用。新增真实 HTTP 失败样本证实：`SessionService.execute_claimed_message` 的输入语义分类已经发出一次请求，但根账本仍为零，原因是 owner 作用域在输入编排之后才建立。本阶段将作用域提前到输入编排和后续执行共用的边界，始终捕获当次领取的 owner，不通过请求 context 或“最新 owner”查询获取执行权。

### 依据与实现

- 沿用 `ModelRuntimeService.invoke` 的四传输原生客户端与既有 LangChain Chat 选择、请求映射、鉴权、流式回调、HTTP 观察和 DTO 结果契约。bootstrap 把与 bound adapter 相同的 `FrameworkModelCallPolicy` 注入该单例模型服务；owner 作用域内缺少 policy 时在 HTTP 前失败。无 owner 调用保留既有默认策略，不因此自动变为 v2。
- `FrameworkModelCallPolicy.invoke_dto` 只负责 DTO 封套、模型快照复核、Session/Turn 身份和用量读取；与官方图入口共用 `_invoke_intent` 的 prepare/start/send/complete/unknown 流程，没有第二套账本或结算循环。普通 DTO 保存 root/agent 用途，逻辑身份包含 binding、原 task、调用方 logical_call_id 及请求摘要。缺省调用 ID 仍按原服务生成，不构成首次 message 可确定性恢复的证明。
- ProviderClientError 在共享策略记 unknown 后，再由模型服务转换成既有 HTTP 错误结果；取消继续传播并保留预留。响应结算确认失败沿同一既有故障边界处理，不把错误 DTO 误记为正常模型完成。缺失或不完整用量保留预留，显式零可以结算；Anthropic 缓存读取/创建计入输入，Gemini total 包含其余已报告用量，原生 response_summary 不改写。
- owner 下显式禁用 SDK 重试；请求非零隐式重试会被拒绝，后续显式重试授权仍待实现。Google 原生客户端增加可选 max_retries，按固定 SDK `HttpRetryOptions.attempts` 的总尝试语义加 1；没有传选项时保留 SDK 默认。同步更新通用模型运行参数和原有 Google 拒绝契约测试，未扩大文档影响分析领域本身的支持矩阵。
- LangChain DTO 模型在实际发送对象上关闭本地缓存，避免将命中已有缓存记作物理调用。其它旧调用继续原缓存设置。测试先用同配置/参数的原 DTO 请求建立缓存，再验证 owner 下仍产生实际 HTTP。

### 官方签名复核与兼容修复

真实 Anthropic DTO 用配置中的 `temperature=0.2` 首次触发 `TypeError: AsyncMessages.create() got an unexpected keyword argument 'temperature'`。固定 SDK 的生成签名已移除此 kwargs；[官方迁移文档](https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md) 明确允许旧模型通过 extra_body 显式透传。原生客户端按该文档将已配置值放入 extra_body，保留原 wire 字段，不静默丢弃配置。是否被真实目标模型接受仍需供应商验收。

根用量读取依据 [Anthropic 官方输入计数](https://platform.claude.com/docs/en/api/rate-limits) 和既有原生 response_summary 契约；固定 Google SDK `types.py/HttpRetryOptions` 与 `_api_client.py/retry_args` 说明 attempts 包含初次请求。此阶段未升级任何依赖。

### TDD 与定向结果

- DTO 首轮 **1 failed，6.61 秒**：模型服务缺少 policy 注入端口。
- 输入语义分类真实复现 **1 failed / 17 deselected，7.28 秒**：HTTP 次数为 1，账本 reserved_calls 为 0。提前 owner 作用域后纳入实际 PostgreSQL/SessionService 验收。
- DTO 首轮推进暴露上述 Anthropic kwargs 不兼容；修复后发现 SDK 把未使用的可选缓存计数序列化为 None，初版过严用量读取导致 actual_tokens 为零但正确保留了预留。据固定原生字段语义将缺失/None 的可选缓存计数按零读取，核心输入/输出计数仍必须完整。
- 补充用量测试首次因测试未提供 DTO 必填 text 失败（**1 failed / 20 passed，56.98 秒**），修正测试构造，没有改变生产 DTO 要求。随后 DTO、客户端、适配器及文档传输选定回归为 **58 passed / 6 skipped / 6 deselected，58.54 秒**；skip/deselect 是未开启 MySQL/PG/RustFS 文档任务环境的领域用例，没有算作通过。

在此基础上新增五条真实流式取消用例，核对每条只发一次 HTTP、取消传播、unknown 与 Token 预留均持久保留。最终范围和计数以本节最终联合回归为准，不与上述中间结果累计。

### 剩余出口

本阶段覆盖捕获 owner 作用域内的普通模型入口及语义分类，不声称独立后台领域任务、无 owner 的 legacy 调用或全系统路由均纳入该根策略。自动冻结绑定/预算及版本实物、活动时间、完整动态参数/错误/显式重试策略、不可变请求/响应及迟到模型证据、首次 message 恢复、跨 Turn 上下文、受控 AgentCase 图和逐模式验收继续待实现。

没有改变默认开关、schema、依赖、正式用例/Runner 或部署状态。旧 v2 Worker 仍须按 §23–27 排空/统一升级，不能混用不同预算治理行为。完整生产 lifespan、真实供应商、浏览器/领域 Runner、前端构建与独立后端 lint/typecheck 未验收。

### 联合验证

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_dto.py tests/test_framework_model_policy.py tests/test_framework_model_admission.py tests/test_model_clients.py tests/test_langchain_model_adapter.py tests/test_model_completion_http.py tests/test_model_stream_terminal_http.py tests/test_model_request_profile.py tests/test_model_sdk_fault_http.py tests/test_semantic_intent_trace_http.py tests/test_session_execution_ownership.py tests/test_agent_v2_worker_contract.py tests/test_document_impact_transport.py::test_saved_grouped_requests_keep_exact_hash_and_old_transport_policy tests/test_document_impact_transport.py::test_existing_factories_receive_supported_explicit_options tests/test_document_impact_transport.py::test_google_transport_records_verified_explicit_options -q -x -rs --tb=short
```

结果 **312 passed，302.75 秒，无 skip**。其中新 DTO 文件当时 31 项：四原生传输与 LangChain Chat 的普通/流式调用、共用框架根预算、503 不暗中重试或重放、预算耗尽/缺 policy、无绑定旧路径、输入分类实际入账、身份与重试拒绝、本地缓存、六项用量边界和五条真实流式取消。另覆盖既有 SDK 故障、截断/缺终态、普通 DTO、受理审批、框架策略与 Session/Worker 所有权。此命令只选文档任务的五项非 live 契约参数，不代表未开启环境的十二项领域任务已通过。

之后只加强 HTTP 字段断言及 Google 显式重试参数测试，生产实现未再修改；新增 DTO 文件最终 33 项。字段断言覆盖原生路径、headers、temperature 与 Token 字段，Google 无 owner 显式重试 0/1 分别核对真实请求 1/2 次；owner 路径仍拒绝非零隐式重试。

这项补充首轮 **1 failed / 10 passed / 21 deselected，33.71 秒**：新增 Google 测试传入空 messages，被 SDK 的 contents 预检查拒绝，尚未发出 HTTP。仅将测试补为真实用户消息，没有修改生产实现。最终执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_dto.py -k 'owner_bound_dto_request or unbound_google_explicit' -q -x -rs --tb=short
```

结果 **12 passed / 21 deselected，35.38 秒，无 skip**。其中 10 项与上述联合回归重叠，不能累计宣称新增 12 项不同覆盖。10 个相关 Python 文件 `py_compile`、`git diff --check` 通过。没有执行完整生产启动、独立 lint/typecheck、真实供应商或领域环境验收；这些局限不因本机官方 SDK HTTP、PostgreSQL/Redis Harness 通过而消失。

## 29. P1 模型请求/响应证据与迟到响应观察（2026-10-26）

本阶段补齐附件指出的“响应到达但结算确认失败”和“失租后迟到响应”持久事实。复用现有 `ArtifactStorageService`（RustFS），没有新增 blob 后端或把正文写入日志。模型账本记录不可变对象引用和原始执行身份；对象内容与 PostgreSQL 引用分开提交，任一写入确认失败都保留未知状态，禁止据此重发模型请求。

### 实现边界

- `FrameworkModelEvidence` 保存应用层 canonical JSON：请求封套包括完整框架消息、工具 schema、选项和配置指纹；LangChain 响应使用官方 `messages_to_dict` 保存原生内容块、消息 ID、tool call、usage、response metadata 和签名；DTO 响应只保存既有 `ModelInvocationResult` 的非请求摘要字段。响应资产标明 serializer、版本、binding 指纹、attempt、logical call 和 request hash。
- `FrameworkModelObject` 只保存 URI、SHA-256 和长度；读取时重新计算摘要和长度，并复核响应身份。对象内容不含 owner token、API key、认证头或 HTTP 查询凭据。当前实现保存 SDK 应用对象，**不是供应商 HTTP wire body**；HTTP 事件仍只记录安全路由/状态/耗时，wire body 与 SSE 中间块属于后续独立接入。
- `PostgresFrameworkModelCalls.append_response_observation` 是证据追加端口：只接受原始 attempt 的不可变 binding、task、attempt 和 observer token 摘要；允许原 owner 失租后写入，但不需要活跃租约。观察按 binding、attempt、响应类型和对象 hash 去重，`authoritative=false`，不改 phase、usage、预算、ToolJob 或 applied proof。新 owner 仍须先把 invoking 明确分类为 unknown，未知副作用不能因迟到对象自动继续。
- 策略顺序为 `prepare(request asset) → start → provider send → response asset/observation → complete(usage)`。响应资产或其引用无法确认时保留 invoking/预留并抛出可检索事件；不调用第二次 provider。原始资产写入失败可能留下 RustFS 孤儿，必须由对账任务按 URI/hash 清理或认领，不能将孤儿当成已结算事实。
- 生产 bootstrap 仅在 RustFS backend 启用时注入证据服务；未启用时保持现有调用路径并记录 `framework_model_evidence_storage_disabled`。因此当前默认关闭的 v2 路由不会被隐式打开；开启证据前必须配置对象存储和保留/清理策略。

### TDD 与验证

先添加红测验证：请求 hash 与资产身份不可变、重复准备不重复写入、失租迟到响应可追加且不结算 usage、伪造 owner/未发送 attempt 被拒绝、结算与观察并发不互相覆盖、对象篡改被摘要检查拒绝，以及官方 LangChain response 的原生消息往返。实现后执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_evidence.py -q -x --tb=short
```

结果 **14 passed，31.90 秒，无 skip**。模型策略与证据联合回归随后执行：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_evidence.py tests/test_framework_model_policy.py -q -x -rs --tb=short
```

结果 **37 passed，97.49 秒，无 skip**；5 个相关 Python 文件 `py_compile` 和 `git diff --check` 通过。没有执行 RustFS 真实服务、真实供应商、HTTP wire 捕获、全量系统回归或部署升级验收。

### 保留的风险与下一阶段

历史模型调用记录的 `request` 资产字段可为空；兼容恢复不能凭摘要重建原始请求。首次 message 的确定性 attempt 身份、完整请求/响应 wire 资产、流式中间块、活动时间预算、未知外部副作用和跨 Turn transcript 仍未完成。响应观察解决“迟到事实可留存”，不等于 exactly-once，也不把非权威观察提升为工具或模型结果。

## 30. P1 模型 HTTP wire 请求/响应观察（2026-10-26）

本阶段继续 §29 的独立 wire 证据出口。模型应用层响应已经进入 RustFS/账本，但它不能证明供应商实际收到的 JSON 或返回的 SSE 字节。因此新增 `ProviderCallObservation` 的 HTTPX2 event hook：捕获请求/响应体、安全响应头、状态码、流式响应字节、截断标志和响应完整标志；wire 对象与应用层 response 对象分开保存，均只通过 URI、摘要和长度挂到账本。认证、Cookie、token、API key 和 secret 头不会保存，路由只保留四种已知安全路径，未知路径降为 `/<redacted>`。

### Deep Agents 图内接入

固定 `langchain-openai==1.6.2` 的 `ChatOpenAI` 有公开的 `http_async_client` 字段，但已构造的 `root_async_client`/`async_client` 不会因只更新该字段而改变。首次真实图调用暴露 `base_url`/`timeout` 字段名错误；依据固定版本官方模型源码，改用 `openai_api_base`、`request_timeout`、`default_headers`、`default_query` 和 `openai_api_key`，重建 `openai.AsyncOpenAI` 及其 `chat.completions` 资源后再把模型副本交给 handler。绑定失败不再静默降级；异常带类型和上下文向上暴露。Responses API 仍复用同一 root client 的 `responses` 资源，由官方模型路径选择。

其它官方图模型（Anthropic/Gemini）尚未接入等价的 per-instance HTTP client 注入；它们当前仍记录应用层 response，wire 观察只有在 SDK 官方客户端注入方式查证并完成测试后才启用。普通 DTO 入口沿用已有按 transport 的官方客户端路径，图入口本阶段只验证 OpenAI Chat/Responses 的客户端替换。

### TDD 与验证

新增 HTTP 观察单测覆盖普通 JSON、SSE 转发、容量截断、响应完整性和敏感请求头过滤。随后重跑真实 PostgreSQL/RustFS 替身与 Deep Agents 图调用：首次失败是账本没有 wire observation，根因是图内客户端仍指向旧的 `root_async_client`；修正字段与根客户端重建后通过。

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_evidence.py::test_official_response_evidence_survives_expired_owner_and_completion_ack_loss -q -x --tb=short
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_model_http_observation.py tests/test_framework_model_evidence.py tests/test_framework_model_policy.py tests/test_framework_model_calls.py -q --tb=short
```

结果分别为 **1 passed，7.09 秒** 和 **61 passed，141.61 秒，无 skip**。联合结果实际核对请求/响应 wire 对象存在、响应完整、失租后不结算 usage，以及不会因结算确认失败重发 HTTP。未执行 RustFS 真实服务、真实供应商、Anthropic/Gemini 图内 wire 注入、全量系统回归、前端构建或部署验收；因此不能把 wire 观察称为全传输完成。

### 保留的风险与下一阶段

当前观察只捕获一次逻辑调用中的请求体和响应体；多次 SDK 重试会更新观察的 attempt 计数，但尚未建立每次重试独立 wire 对象。大响应按上限截断，截断对象不能用于完整重放。流式响应在消费完成前保持 `response_complete=false`，断流不会被当作完整响应。RustFS 对象和 PostgreSQL 引用仍是分开提交，孤儿对象需由对账任务处理。

历史调用仍可能没有 request/wire 资产；不能从摘要重建。首次 message 安全恢复、活动执行时间预算、Anthropic/Gemini 官方客户端注入、跨 Turn transcript、未知外部副作用和各领域 Runner/模式验收继续未完成。默认 v2 路由与生产开关没有改变。

## 31. P1 Gemini 图内 HTTP wire 注入（2026-10-26）

本阶段补齐 §30 中可由官方公开 API 支持的 Gemini 图内 wire 观察。固定 `langchain-google-genai==4.4.0` 使用 `google-genai` 客户端；其 `HttpOptions` 公开 `httpx_async_client`，因此为每次受治理模型调用创建标准 `httpx.AsyncClient`，重建 `google.genai.Client`，再通过模型副本的公开 `client` 字段交给官方 LangChain 模型。请求级 timeout 和 retry 仍由 `ChatGoogleGenerativeAI` 原有 `_prepare_request` 生成 `HttpOptions`，没有复制一套重试策略。

OpenAI/Anthropic SDK 使用 `httpx2`，Gemini 使用标准 `httpx`。`ProviderCallObservation` 根据实际响应类型选择对应的 `AsyncByteStream` 包装器，避免把两个不兼容的 HTTPX 类型混用。Anthropic 的 LangChain 模型目前只有私有 `_async_client`，没有采用私有属性注入；它仍保留应用层证据，等待官方公开客户端注入契约。

### TDD 与验证

先把现有失租/结算确认失败 wire 证据用例参数化到 OpenAI Chat Completions 与 Gemini。新增 Gemini 用例首轮 **1 failed / 1 passed，17.52 秒**，失败为 `wire_observations` 为空，确认图内模型仍使用旧客户端。实现官方 `HttpOptions.httpx_async_client` 注入和标准 HTTPX 流包装后，同一红测变为 **2 passed，13.30 秒**；再加入 OpenAI Responses 的原生内容块断言，三种图传输定向结果为 **3 passed，18.35 秒**。HTTP 观察单测扩展到两种 HTTPX 后为 **4 passed，4.04 秒**。

最终联合回归：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_model_http_observation.py tests/test_framework_model_evidence.py tests/test_framework_model_policy.py tests/test_framework_model_calls.py tests/test_framework_chat_models.py -q --tb=short
```

结果 **101 passed，227.63 秒，无 skip**。实际覆盖 Gemini 普通请求的 wire 请求/响应对象、完整标志、RustFS 替身和 PostgreSQL 账本；四传输官方模型、OpenAI Responses、流式/工具/策略及既有账本回归继续通过。相关 Python 文件 `py_compile` 与 `git diff --check` 已通过。

### 保留边界

本阶段没有用私有属性强行接入 Anthropic；没有执行真实 Anthropic/Gemini 供应商、真实 RustFS、全量系统、前端构建或部署验收。Gemini 的应用层 SDK 客户端和 wire HTTP 客户端分别由官方模型与观察层持有，结束调用时关闭观察客户端；对象存储和账本仍是分开提交。首次 message 安全恢复、活动执行时间、跨 Turn transcript、未知外部副作用、受控 AgentCase 图和逐模式 Harness 继续未完成。

## 32. 附件复核与剩余实施契约（2026-10-26）

对照干净代码基线 `b8ebb12d6`，重新读取用户附件、方案和 §25–31 实施记录，沿 Session 输入、v2 任务领取、模型账本、adapter、原生消息、checkpoint owner 与审批恢复核对实际入口。本轮为方案复核及补全，仅修改本记录和接入方案，没有新增实现、依赖、配置或数据库迁移。

### 结论与修订

- 六类架构风险成立；附件关于“仍只有批量桥接”“checkpoint 无 owner 保护”“本地未安装 Deep Agents”的现状已过时。v2、legacy 和未来受控 LangGraph 分开判定；当前 Worker 只接已实现的 Deep Agents v2 操作，不把目标架构当现状。
- 保持人的决定、交付 receipt、ToolJob 三维事实；不采纳统一线性状态机。局部同事务 proof 和已知结果恢复不代表外部 exactly-once 或任意中断均可恢复。
- 更新方案 §1.3、§7.3，纳入已经实现的应用请求/响应资产、迟到观察与 OpenAI/Gemini 图内 wire；保留 Anthropic 图内 wire、多次 retry 独立证据、摘要 wire、对象孤儿/保留策略及真实 RustFS 出口。固定 ChatAnthropic 的公开模型字段无 HTTP client 注入项，本次未改用私有属性。
- 新增方案 §14：首次 message 先做只读事实诊断，再建立阶段准入、持久逻辑调用/尝试与安全后继；按六个提交窗口规定故障 Harness，当前不能仅凭 checkpoint 存在或账本缺行自动重跑。模型 ready/invoking/unknown/已存响应、图完成待投影分别处理。
- 补齐活动时长口径、跨 Turn 档案导入、真实 Token 事件、未知效果的目标查询/人工对账、领域 Runner 权限与租约、制品冻结/双环境部署和逐模式验收顺序。上述均标明待实施；不以诊断建议宣称已具备恢复端口或人工处理入口。
- 复核本地《Agent轨迹与协议鲁棒性》§7 与《Agent测试专项》的轨迹、计划依赖、Schema 漂移、异常恢复和成本要求，继续保持公共运行时 Harness 与模式执行/裁决 Harness 分工。正式项目、固定用例版本、终态 RunItem 与新失败回归运行的业务链不变。

在线重新核对官方 [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) 和 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。页面已含新流接口，未据此升级固定依赖或把新示例当当前运行能力。实际 importlib.metadata 检查九个直接包仍为方案所列固定版本；HITL 构造签名包含 interrupt_mode。

### 本次实际验证

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py tests/test_framework_approval_recovery.py -q -x -rs --tb=short
```

结果 **51 passed，226.88 秒，无 skip**。使用隔离 PostgreSQL 表/checkpoint schema、Redis、官方 Deep Agents/LangGraph、fake model 与受控工具；包含旧 owner checkpoint/pending writes 拒绝、namespace/DeltaChannel、逐项原生身份，以及工具/native 结果保存后、对账提交前/提交后 ACK 前的三个独立 Worker 强杀参数。B 外部 Redis 计数保持一次，A 决定最终应用，原终态 task/attempt 保留。此结果不能证明首次 message、未知目标效果或真实供应商恢复已通过，也不与 §31 或历史结果累加。

文档检查：方案全部本地 Markdown 链接目标存在，代码围栏成对，新增 §14 六个子节齐备；`git diff --check` 通过。未运行全量系统、前端构建、独立 lint/typecheck、真实供应商、真实 RustFS/浏览器/领域 Runner 或双制品部署验收；本轮只修订方案，未将这些尚缺实现或环境的出口计为通过。整体迁移及 P1/P2.3/P3–P5 继续未完成，默认路由保持原状态。

## 33. 首次 message 只读事实诊断（2026-10-26）

从干净基线 `4c84a2f82` 继续方案 §14.2 的第一步。此次是首次 message 恢复协议缺少诊断事实的问题，不把原审批恢复器加条件扩展成首次消息重跑器。

### 依据与实现边界

- 复核用户评审附件、方案 §14、当前 task/attempt/owner、模型账本、Session 和官方 checkpoint 表；确认 `DeepAgentCheckpointProvider.open()` 包含 schema/setup，不能作为只读诊断入口。重新核对官方 [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 以及 [PostgreSQL SET TRANSACTION](https://www.postgresql.org/docs/current/sql-set-transaction.html) 和 [隔离级别](https://www.postgresql.org/docs/current/transaction-iso.html)。checkpoint 及 interrupt 不证明平台前置调用可以安全重发。
- 新增 `framework_message_diagnostic.py`：只读 DTO 明确区分实际任务状态与自动恢复判定，允许返回缺失预算/账本汇总的未知值；不返回 Prompt、工具输入/输出正文、owner token 或 HTTP headers。模型应用响应和 wire 观察保留非权威标志、资产引用与截断/完整性限制；没有读取 RustFS 实物，不宣称已验真。
- 新增 `FrameworkMessageDiagnostics`：在一个 `REPEATABLE READ READ ONLY` 事务读取原输入准入、用户消息、执行 attempt、owner/租约、模型预算/尝试、最新物理 namespace checkpoint/pending writes、ToolJob 及原 Turn 投影。返回 DB observed_at 和 MVCC snapshot_id；不取得执行权，不初始化缺失表/池，不执行模型、图、工具、恢复任务或 outbox。读取失败日志只记录身份与异常类型。
- `PostgresAgentTaskStore.diagnose_framework_message(task_id, session_id=...)` 作为内部消费端口，Session 不匹配返回不存在；只接受显式绑定的首次 message。尚未接 HTTP/UI，不代替调用方已有的身份认证。抽取原有 `_v2_input_hash` 给准入和诊断共同使用，保持原 hash 算法与可选预算语义；历史无预算准入也可识别。
- 原 task/attempt、冻结预算和结果没有更新。报告始终保留 `automatic_replay_allowed=false` 与 `stage_admission_unavailable`；这不把 queued/运行中的任务改为 recovery_required。没有 checkpoint/账本行不作为未发送的证明，迟到观察不作为权威响应。首次 message 的阶段准入、安全后继和六个真实提交窗口仍待下一步。

### TDD 与实际验证

先新增 `tests/test_framework_message_diagnostics.py` 并实际运行红测：`1 failed`，缺少 `diagnose_framework_message` 导致 AttributeError。实现后的首轮失败发现 v2 输入 hash 同时包含 binding fingerprint 和可选冻结预算；改为复用现有准入 hash，未更改历史输入或绕过校验。新增 wire 诊断字段的红测也实际触发缺字段失败，随后实现白名单投影。提交前新增根身份损坏的实际红测，出现 `DID NOT RAISE ValueError`，证实记录可将读取重定向到另一 Turn；现已在所有绑定账本/checkpoint 查询之前拒绝该身份矛盾。

新 Harness 共 17 个测试，使用隔离 PostgreSQL 表/namespace、Redis、官方 LangGraph/Deep Agents 和 fake model。覆盖无 checkpoint 输入、四种模型阶段及原预算、坏账本未知汇总、非权威响应/wire 引用与敏感字段排除、Session 范围、根绑定身份损坏、用户消息缺失/输入漂移、失租原 attempt 保留、原生 pending writes/子 namespace 和近似前缀排除、真实原生审批 ToolJob 及已领取执行身份、指定 Turn 投影。并发测试在读到 task 之前提交另一连接的新预算，诊断仍读取旧预算；同时实际核对连接为 `transaction_read_only=on`、`repeatable read`。

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_diagnostics.py tests/test_framework_model_admission.py tests/test_runtime_binding_persistence.py tests/test_framework_checkpoint_fencing.py -q -x -rs --tb=short
```

结果 **53 passed，159.98 秒，无 skip**。这是此次联合回归的最终结果，包含最后的根身份修复；不与前面的 10/14/52 项开发中测试或历史结果累计。

```powershell
../.venv-framework-v2/Scripts/python.exe -m compileall -q src/runtime/framework_message_diagnostics.py src/runtime/postgres_agent_task_store.py src/schemas/framework_message_diagnostic.py tests/test_framework_message_diagnostics.py
git diff --check
```

均 exit 0。没有增加依赖、数据库迁移或启用生产路由；测试文件受仓库既有忽略规则影响，交付时显式纳入本次提交。未执行独立 lint/typecheck、全系统/前端、真实供应商/真实 RustFS/浏览器/Runner 或双制品部署；本步没有这些执行链路。没有完成模型发送阶段身份、首次 message 安全后继、活动时间或真实崩溃重投验收，不能将本步报告为全系统迁移完成。

## 34. 首次 message 阶段准入与稳定模型位置（2026-10-26）

从干净基线 `185225e01` 继续 §14.2 的阶段准入与逻辑位置。根因是应用的首次消息编排发生在图之前，仅靠 checkpoint 无法定位它；原模型调用又将请求 hash 混入位置，同一逻辑位置发生输入漂移会被当作新调用。此步将位置与输入校验分离，建立应用阶段日志，没有复用审批后继操作冒充首次消息恢复。

### 依据与改动边界

- 重新核对官方 [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)、[Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 和 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。逐项审批是框架能力；节点恢复会重入，框架不会自动保存图外编排位置。在线页面已有新接口，继续固定当前九个版本，没有照搬新流式示例或升级依赖。复核本地《Agent轨迹与协议鲁棒性》§7 的调用次数、异常恢复、重复效果和成本约束。
- 新增 `FrameworkMessageStage`、`FrameworkMessageStages` 与 `<agent_task_table>_message_stage` 表。初始化由 task store setup 执行，首次 v2 提交与输入、binding、预算及 outbox 共事务；这是兼容性新增数据库结构。没有补造历史阶段，历史任务缺准入时拒绝执行。
- Session 在编排前冻结来源清单，编排后冻结完整 ExecutionRequest JSON，实际执行从该 JSON 重建请求。来源清单保存可见消息及 metadata 摘要、Session revision 与绑定身份，排除租约凭据；不等于外部 Prompt/Skill/Memory 的实物档案。推进使用既有 Session → owner 的锁序，校验原 task/attempt、输入、owner 和阶段；执行与只读诊断共用冻结摘要完整性校验。
- 阶段为 submitted/orchestrating/orchestrated/runtime_submitted/projected。runtime_submitted 发生在应用调用 Runtime 前，不代表模型已发送；projected 只保存首次 message 返回的 output/snapshot ID，允许该返回仍在等待审批，不表示整个框架 Turn 完成。初始完整 graph state 未冻结。
- `FrameworkModelCallIntent.position_version=1` 标记新位置协议，历史默认为 None。图内位置由 binding/branch/namespace/task/purpose 构造；DTO 由 binding/持久 logical_call_id 构造，均不含请求 hash 或执行 owner task。`prepare_initial` 在 owner 事务内复用原 attempt，并对比不可变意图；输入漂移、多原点或缺少历史位置均要求对账，不能消耗新预算或发第二次 HTTP。
- 顺序调整为持久模型意图/预留 → 请求资产上传 → immutable 引用绑定 → 唯一 start 许可。已有资产复用，已发送重入不再上传。原 owner 的 ready 可取得尚未发出的许可；invoking/completed/unknown 或不同 owner 均不重发，不自动导入响应。原账本 prepare/显式重试存储契约保持，未接入新的自动重试策略。
- owner-bound DTO 缺持久位置在 HTTP 前拒绝，前置语义分类显式使用 `input_orchestration:semantic_intent:v1`。恢复异常向上抛出并记录，不能吞成正常语义降级。未迁移无 owner 的领域模型调用。
- 内部诊断新增阶段白名单；合法阶段报告 stage_recovery_not_implemented，缺失/损坏保留对应问题码与 automatic_replay_allowed=false，不返回冻结请求正文，不取得执行权。

### TDD 与独立进程故障验证

本轮实际红测包含：同位置输入漂移没有抛错、请求资产上传前账本为 0、阶段端口缺失、准入后输入变化仍可执行。完成基础实现后，审查再次加入真实边界红测：冻结后改变原请求导致执行值与持久 JSON 不同（**1 failed，7.90 秒**）；损坏来源清单仍推进阶段（**1 failed，7.86 秒**）。现已从冻结 JSON 重建执行请求，并在阶段读取/推进/诊断统一核对内容摘要，未通过局部 fallback 绕过异常。

新增 `test_framework_model_positions.py` 覆盖 DTO/图内输入漂移、三种发送/结算 ACK 丢失、四并发仅一次 HTTP、缺持久位置、历史缺 position_version 拒绝、意图先于资产及完成重入不再上传。新增 `test_framework_message_stages.py` 覆盖提交事务、来源先冻结、请求冻结/重建、缺准入与输入漂移、资产损坏、失租及独立 Worker 强杀。更新既有任务 fixture 清理新增外键表，以及诊断对新提交阶段的预期；没有改动无关测试。

独立 Worker 使用 multiprocessing.spawn、真实 Redis Stream/PEL、outbox/dispatcher、PostgreSQL、前置语义分类与官方 SDK/本地 HTTP 合成供应商，在下面边界确认 child 已阻塞后 terminate/join，再由 replacement Worker 回收未 ACK 消息：

| 强杀位置 | 模型阶段 | 已观察 HTTP | 重投后结果 |
| --- | --- | --- | --- |
| start 前 | ready | 0 | 原任务/attempt 进入 recovery_required，保留预算/阶段，不发送 |
| start 已提交，HTTP 尚未发 | invoking | 0 | 不把“目标未收到”推断为可重发，保留原事实 |
| HTTP 已返回，结算尚未确认 | invoking | 1 | HTTP 仍为 1，原模型/阶段不变，不补造 usage |

没有创建安全 successor，也没有让 replacement Worker 冒充原 owner。ready/invoking 无法仅凭这些记录证明目标是否收到请求，是后继和响应导入协议必须继续处理的边界。

### 实际验证与保留出口

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_stages.py -q -x -rs --tb=short
```

最终阶段用例 **13 passed，57.27 秒，无 skip**，包含上述三个独立进程强杀参数和最后的请求/摘要修复。此前开发中模型/阶段/诊断/策略/DTO/账本/证据/准入联合回归为 **136 passed，379.01 秒**；该进程在最后阶段修复之前启动，不能当作最终全部实现的验收，也不与最终结果累计。

最终受影响入口联合回归：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_positions.py tests/test_framework_message_diagnostics.py tests/test_framework_model_admission.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py tests/test_semantic_intent_failure_visibility.py tests/test_semantic_intent_trace_http.py -q -x -rs --tb=short
```

结果 **98 passed，180.69 秒，无 skip**，覆盖最终模型位置、历史缺位置、诊断、真实模型准入、分发和既有 Session/语义入口。本次变更的 Python 源码与两个新增测试执行 `python -m compileall -q <相关文件>` exit 0；`git diff --check` / `git diff --cached --check` 均 exit 0。两组最终测试为阶段 13 项与入口 98 项，不将中间 136 项重复累计。

当前尚未实现首次 message 安全后继、旧 ready 预留转移、原响应导入、图完成后投影补偿、活动时间和完整初始图资产；这三个 kill 位置不覆盖方案要求的全部六个窗口。请求资产测试使用既有 RustFS 替身，HTTP 为本地合成供应商，未执行真实付费供应商、真实 RustFS、全系统/前端、浏览器/领域 Runner、独立 lint/typecheck 或双制品部署。九个固定依赖、生产默认开关均未改变，整体 P1/P2.3/P3–P5 继续未完成。

## 35. 编排尚未开始的首次消息安全后继（2026-10-26）

继续基线 `f1ae31c7c` 的首次消息恢复工作。上一阶段已证明“不得盲重发”，本阶段闭合输入入库但尚未进入编排的恢复窗口；没有将整个首次 Turn 当作一条可随意重试的函数。重新阅读原审批恢复器、任务领取/终态、阶段日志、owner fence、模型预算和 Session 投影后，新增独立 `FrameworkMessageRecovery` 与 `message_recover` 操作，复用原 inbox/outbox、原阶段表与租约。官方 [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 明确节点恢复会重入，[Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) 保存图内步骤与 pending writes，均不能为图外输入编排自动提供恢复位置。

### 契约、接线与兼容性

- `ensure_framework_message_recovery` 只受理因 execution_owner_expired 进入 recovery_required 的 message/message_recover 尝试。阶段必须仍为 submitted，原 task/attempt、输入、用户消息、项目、冻结预算和 owner 均可验证；没有显式中断、pending Turn 或已经领取的后续输入。模型账本必须存在且零调用，实际存在的 ToolJob/checkpoint/pending writes 不得有该 Turn 的矛盾事实。安全依据是阶段因果准入及 owner 事务，不能从“表未初始化”或“没有一条模型记录”单独推断安全。
- 后继 ID 绑定前驱 task/attempt；后继 task、谱系、outbox 与事件共事务。并发/重复受理只产生一个子任务。原 task/attempt 终态不重领，原 user_message_id、Turn/binding/input 和预算不重建；原输入消息不会重复插入。
- 任务阶段仍存于原准入行，新增持久 recoveries 谱系并派生 current_task_id；领取后继及阶段 begin 再次核对原准入事实，防止提交后输入、预算、谱系或前驱变化。新 owner 使用更高 epoch；旧 owner 无法推进原阶段。尚未进入编排的后继再次失租，可以保存新的前驱尝试并继续；一般数据库错误/拒绝与已开始阶段不自动生成重试链。
- Worker 处理终态/过期 message 重投时可以创建后继，通过既有 Redis/outbox 调度后进入正常 Session 输入流程。后续排队消息先等待该恢复，后继可越过尚未执行的后续输入；已领取的后续输入会阻止恢复准入，避免倒序执行。审批恢复器未参与这条路径。
- Python v2 command/record、Worker operation 能力与前端 AgentTaskRecord 联合类型同步新增 message_recover；v1 command 仍拒绝该操作。Coordinator 可把实际运行后继当父任务，Session 队列投影排除内部恢复任务，避免伪造第二条用户输入。只读诊断增加 execution_task_id/recovery_count，submitted 使用 stage_recovery_requires_admission；automatic_replay_allowed 始终为 false，诊断快照不授予执行权。
- 阶段 JSON 新写入为 schema_version=2，显式记录谱系；新代码能读取无谱系的 schema 1，并在写入/恢复受理时升级。schema 1 不能携带伪造谱系。没有新增表或 DDL，但存在 JSON 协议升级；旧 Worker 不理解 schema 2/message_recover，同一 v2 stream 须统一部署对应制品，不能以此宣布双环境或滚动部署已验收。历史缺准入或预算不补造。

### TDD 与实际故障轨迹

先运行新增测试，真实失败 **1 failed，8.25 秒**，缺少 ensure_framework_message_recovery。接线后，真实 Worker 测试暴露阶段开始返回成功却没有更新数据库：阶段原 task_id 与后继执行 task_id 不同，UPDATE 仍使用后继 ID。已以原准入身份定位阶段行，保持当前执行身份与原历史分离，没有重置原任务绕过问题。

既有 v2 失租回归还暴露历史未初始化模型账本时查询直接报 UndefinedTable；现已将“缺模型准入存储/预算”显式列为不可恢复原因，保留原终态，而非补建空账本或吞掉任意数据库错误。只读诊断新增谱系断言实际红测 **1 failed，10.69 秒**，缺 execution_task_id；随后实现白名单投影。

收尾审查又加入两条真实边界红测：修改后继引用后 getter 仍返回原阶段（**1 failed，10.16 秒**），以及原输入含首尾空白而 Dispatcher 已保存裁剪后的展示消息时恢复被误拒（**1 failed，8.23 秒**）。前者通过校验 recovery reference、原任务和 Session 范围修复；后者按现有 Dispatcher 的展示消息裁剪规则比较，原始输入和输入摘要保持不变。最终定向回归覆盖了这两项修复。

开发中发现两个测试环境问题并修正：Redis v2 stream 必须使用隔离前缀；冷启动真实框架在一秒资源租约下会失租。独立进程测试只对被杀原 Worker 使用一秒资源租约，确认进程已终止后等待真实 TTL 到期并运行资源回收，后继使用正常 300 秒租约；没有延长已经丢失的旧执行权。

新增 Harness 覆盖原输入/预算/唯一后继、并发受理、真实 outbox/Redis Worker 与重复投递、发布事务回滚、编排已开始/输入漂移/显式中断/缺准入/预算漂移拒绝、后继再次失租及旧 owner 拒绝、排队顺序、提交后 parent/input/stage/proof/version/budget 变化、模型/checkpoint/孤立 pending write 矛盾、只读谱系与 schema 1 读取/升级。相关既有 Worker 替身同步内部恢复端口；没有为生产实现添加 hasattr 兼容降级。

独立进程用 multiprocessing.spawn 启实际 Redis Worker，在 FrameworkMessageStages.begin 前确认 running/submitted 后 terminate/join。replacement Worker 回收未 ACK PEL，保留原 recovery_required task/attempt，提交并领取新任务，经官方 Deep Agents、SDK 与 PostgreSQL saver 执行；本地供应商 HTTP 为 1，根预算仍为原对象且仅一个模型尝试，Token 实际用量 10；重复投递不新增 HTTP。开发中该文件为 **19 passed，73.57 秒**，之后继续扩展缺预算/proof version/schema 1 用例，最终结果以下面的联合回归为准，不累计。

### 验证与保留出口

后端联合命令（Agent_Server）：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_recovery.py tests/test_framework_message_stages.py tests/test_framework_message_diagnostics.py tests/test_agent_v2_worker_contract.py tests/test_framework_model_admission.py tests/test_agent_task_dispatch.py tests/test_session_execution_ownership.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py tests/test_framework_approval_recovery.py -q -x -rs --tb=short
```

该联合命令实际结果为 **191 passed，529.91 秒，无 skip**。它在最后两项边界修复前启动，因此只能作为广泛回归证据，不能单独证明最后修复。随后针对最终源码重新运行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_recovery.py tests/test_framework_message_stages.py tests/test_framework_message_diagnostics.py -q -x -rs --tb=short
```

实际结果为 **55 passed，177.45 秒，无 skip**，覆盖 schema 2、schema 1 读取/升级、谱系引用范围以及裁剪展示消息的最终实现。

前端已在 Agent_Web 执行 `npm run typecheck`（vue-tsc），exit 0。没有修改依赖、公开 HTTP 路由或生产默认开关。

此阶段只实现 submitted 的安全后继，尚未完成编排已开始的语义结果导入、ready 预留转移、模型响应导入、图完成待投影补偿、活动时间、完整初始图资产、跨 Turn 管道及各模式 Harness。真实付费供应商、RustFS、浏览器/领域 Runner、完整前端构建/真实浏览器、独立后端 lint/typecheck 与双制品部署均未执行；不能将此阶段视为六个故障窗口或全系统迁移完成。

## 36. 未发送模型预留的显式转移（2026-10-26）

从干净基线 `bb12f3a77` 继续方案 §14.2/A。现有 `prepare_initial` 已定位原调用，`start` 仅允许原 owner；缺口是有效新 owner 无法显式接管仍未取得发送许可的预留。不能因此重建预算/attempt，也不能把调用重入改为自动接管。本阶段实现独立账本端口，尚未实现已开始编排的应用阶段恢复调用方。

### 依据、实现和兼容边界

重新阅读 `PostgresFrameworkModelCalls`、owner 行锁/任务领取、`PostgresDocumentImpactCalls`、审批后继及原消息恢复器。官方 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 区分图 checkpoint 与应用持久数据；应用发送许可和租约仍由本系统管理。复核本地《Agent轨迹与协议鲁棒性》《Agent测试专项》中的轨迹、幂等、故障恢复和调用成本要求。

- 新增 `transfer_ready(owner, expected=完整前驱记录)`，在既有 Session → owner → call 锁中完成比较与写入。仅新 epoch、同 binding、显式位置、持久 ready 且无任何 started/settled/usage/结果矛盾时允许；旧位置缺 observer 身份也拒绝。有效 owner 本身仍通过原租约设施取得，不根据“最新 owner”查找替换凭据。
- 原模型 attempt、intent、根额度、deadline、请求资产和创建时间不变，当前发送/观察 owner 更新为新执行身份。追加 `FrameworkModelReservationTransfer`，保存前驱/后继非秘密身份、observer 哈希、完整前驱摘要和数据库时间；原 owner 的 start/结算/响应观察被拒绝。schema 2 DTO 校验更高 epoch、连续谱系及尾部 owner；旧 schema 1 无谱系可读，不补造历史 position/observer。
- 同一前驱/目标的并发重复转移只追加一个 link；已提交但确认丢失时，按前驱摘要返回当前事实，start 后也不回退阶段。第二次 owner 更换必须提供立即前驱快照；同一 owner 不允许伪造一次转移。deadline/Token/冻结指纹重新校验，失败真实回滚。
- `MessageModelCallFact` 增加转移次数和原预留 task/attempt/epoch，保留当前 owner；不返回 observer 哈希或 token，不授予自动恢复。没有新增 HTTP、数据库 DDL、依赖或默认开关。扩展 JSON/实际转移 schema 2 不能由旧严格解析器读取，同一 v2 stream 仍须统一制品；本轮没有验证滚动发布。
- `FrameworkModelCallPolicy`、`prepare_initial`、message recovery 的运行行为未放宽：普通重入仍拒绝 prior owner，submitted 后继仍拒绝任何模型事实。账本端口不发送请求、不建后继、不重入已开始的编排/图；后续有阶段准入的调用方必须显式对账后才调用它。

### TDD 与实际 Harness

第一条红测实际 **1 failed，13.14 秒**：缺少 `transfer_ready`。端口首批为 **7 passed，23.53 秒**。随后诊断红测实际 **1 failed / 9 deselected，11.83 秒**，缺少 reservation_transfer_count；实现白名单投影后当时文件 **10 passed，32.28 秒**。这些开发中结果不与最终结果累计。

新文件 `test_framework_model_reservation_transfer.py` 覆盖并发唯一转移/唯一 start、原预算和资产保留、事务失败回滚、重复确认及二次换 owner、已发送/completed/unknown 拒绝、五类伪 ready 矛盾、历史缺位置拒绝、同 owner 拒绝、截止时间不重置、原身份响应拒绝及诊断脱敏。既有治理 Harness 增加可选的“领取但未 begin”入口，以真实领取协议生成尚未执行的 owner，而不是篡改运行任务为可重新领取。

普通 DTO 和官方 LangChain 两条路径使用真实 PostgreSQL/SDK 与本地 HTTP 合成供应商：原 owner 在 claimed 阶段只能 prepare，start 因未运行被拒绝，HTTP 为 0；原领取失租后按既有任务协议重新领取，新 owner 普通调用仍拒绝 prior owner；显式转移后沿原位置发送一次，根调用数仍为 1，usage 为 10；后续重入不再发 HTTP。这是模型端口/调用策略验收，不是运行中首次消息的完整恢复。

独立 `multiprocessing.spawn` 消费者在 `transfer_ready` 提交后写 Redis 门闩并等待，父进程确认数据库 ready/单 link 及子进程存活后 terminate/join。重建 store，在同一仍有效的 owner 租约下使用原快照核对提交，记录不变；原 attempt 仅得到一次 start，调用槽位仍为 1。此项验证进程死亡造成的 ACK 丢失，没有谎称 Worker 再次失租/接管或供应商真实请求验收。

### 验证与剩余出口

在 `Agent_Server` 执行定向最终命令：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_reservation_transfer.py -q -x -rs --tb=short
```

结果 **17 passed，54.52 秒，无 skip**，包含最终独立进程测试。受影响联合回归命令：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_reservation_transfer.py tests/test_framework_model_calls.py tests/test_framework_model_positions.py tests/test_framework_model_policy.py tests/test_framework_model_evidence.py tests/test_framework_message_diagnostics.py -q -x -rs --tb=short
```

联合回归结果 **103 passed，275.38 秒，无 skip**。其运行包含最终生产实现，但启动于最后一个独立进程测试添加前；该新增测试由上面的最终定向 17 项覆盖。两组运行分别报告，不把重复数字累计为不同覆盖。

6 个涉及的 Python 源码/测试文件执行 `compileall` 全部通过；两份文档围栏与本地文件链接检查、`git diff --check` 通过。本轮没有前端实现变更，未重复运行前端检查。

仍未完成：冻结外部编排资产、已开始 message 的阶段恢复准入与转移调用方、响应导入、活动时间、跨 Turn、受控 AgentCase 图和各模式 Harness。未运行真实供应商/RustFS、完整系统/前端/浏览器/领域 Runner、独立后端 lint/typecheck 或双制品部署；本轮不开放生产路由、不把此账本前置端口视为 A 阶段或整体迁移完成。

## 37. 附件再复核、执行契约及发布出口（2026-10-26）

本轮按用户要求验证所贴原方案及附件意见，更新方案 §15 和本记录。以已提交 `d0a4662cc` 为能力基线；开始时工作区已有 SessionService、message recovery/stages 和阶段 Schema 四个源码文件改动，以及被忽略的 orchestrated 候选测试。本轮未修改、暂存或提交这些实现，不把候选当作已完成阶段。

### 核验依据与方案补全

- 重新读取附件全文、v2 方案、已有阶段记录，以及原生 HITL adapter、fenced saver、审批受理、原生消息序列化、Runtime 初始状态、版本能力路由和候选恢复测试。六项问题在原方案形成时均成立；当前须分别判断 legacy、已绑定 v2 和目标能力。
- 官方 [Deep Agents HITL](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop) 确认审批建立在 LangGraph interrupt 上；[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) 确认恢复节点重入；[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 区分 thread checkpoint 和跨 thread store。LangChain HITL 网页抓取失败后，核对[官方文档仓库原文](https://github.com/langchain-ai/docs/blob/main/src/oss/langchain/human-in-the-loop.mdx)，并实际运行固定包中的 per-call。
- 补全审批三组事实、阶段恢复准入、代码制品与各持久 JSON 格式的兼容范围，区分运行 Harness 与测试 Harness。当前 Worker 检查 runtime/state/lock/operation，不足以证明未来 message-stage/model-call/proof 格式的代码读兼容；新增格式的发布必须绑定兼容制品或排空/隔离队列。
- 官方 [Checkpointers conformance](https://docs.langchain.com/oss/python/langgraph/checkpointers#testing-with-the-conformance-suite) 提供存储契约测试，方案增列其兼容性核验及隔离测试要求。实际 metadata 查询确认本环境未安装 `langgraph-checkpoint-conformance`；本轮未安装依赖或声称运行了该套件。既有租约/审批测试不能自动替代完整 saver 契约验收。
- 仍保持 §14.5/15.5 的 A–F 实施顺序、各模式独立 Harness、固定版本用例与终态保护。生产 v2 默认路由保持关闭。

### 本次实际验证

仓库根目录执行既有官方离线契约脚本：

```powershell
.\.venv-framework-v2\Scripts\python.exe docs/verify_framework_contracts_2026_10_25.py --candidate --dependency-dir .venv-framework-v2/Lib/site-packages
```

exit 0。九个实际依赖版本与锁定选择一致。**4 组官方 per-call 场景通过**：剩余项 approve/reject 与相同/不同参数交叉；先批准 B 只执行 B，A 保持等待；无新决定继续不重复 B；同进程重建 Harness 后完成 A，最终工具结果 ID 完整匹配。这是 fake model + 内存 saver + 同进程重建，不是跨进程重启或真实供应商验收。

在 Agent_Server 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_dependencies.py tests/test_framework_approval_receipts.py -q -x -rs --tb=short
```

结果 **37 passed，82.73 秒，无 skip**。包括直接依赖声明/锁和 legacy 制品引用，真实 PostgreSQL 的原生 resume 写入/receipt、事务回滚、位置前缀及 owner 替换；框架模型仍为替身，不累计历史测试数字，也不把它记为全系统通过。

接续前一执行者尚在运行的候选测试，而未重新启动该命令：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_orchestrated_message_recovery.py -q -x -rs --tb=short
```

实际结束为 **1 failed、3 passed，20.03 秒**，因 -x 未执行剩余项：

```text
test_frozen_phase_alone_does_not_override_changed_or_unknown_facts[input]
tests/test_framework_orchestrated_message_recovery.py:104
assert await h.tasks.get(original.id) == terminal and ...
AssertionError
```

源码核对显示该参数先故意改变原 input_payload，然后比较整个 task 与改变前 terminal；前一条“ensure_framework_message_recovery 返回 None”断言已通过。本轮只标记候选未验收，未顺带修改测试或实现；不能从这条失败推断重复副作用，也不能忽略失败宣称全绿。独立 Worker 完成语义调用后强杀、二次失租、诊断及既有 Schema 测试仍须后续闭合。

两份文档执行本地文件链接/代码围栏检查及 scoped `git diff --check`，结果通过。未运行全量测试、lint/typecheck、前端、官方 conformance、真实供应商/RustFS/领域 Runner 或双制品部署；原因是本轮交付为方案复核，且相应完整出口尚未具备验收条件。没有修改依赖、数据库、运行时接口或开关。

## 38. 冻结输入编排结果的首次消息安全后继（2026-10-26）

继续方案 §14.2/A 和已复核基线 `bc08011e1`，接续工作区中的 orchestrated 恢复候选。本轮闭合“输入编排及语义调用已结束、ExecutionRequest 已冻结、Runtime 尚未准入”的窗口，复用原 `FrameworkMessageRecovery`、阶段行、message_recover、outbox 和 owner 协议；没有新增外层循环或重置原任务。本节结果不等于 A 阶段或全系统迁移完成。

### 依据、实现与兼容范围

重新沿 SessionService → InputOrchestratorService/SemanticIntentService → 模型策略/账本 → Runtime 的实际链路阅读，并核对原 submitted 恢复、诊断、阶段 Schema 和 owner 锁。方案 §15.3 及官方 Interrupts/Persistence 已说明图 checkpoint 不能代替图外输入编排恢复；本阶段不更换九个锁定框架依赖。

- source manifest 增加运行输入和 ModeRegistry 摘要。运行输入覆盖 Session 核心设置、业务 metadata 与可见消息；只忽略已知租约/失租控制投影、当前 user 消息 delivery_status，并排除未领取 queued 输入。环境、权限相关 metadata 和消息内容发生变化会拒绝恢复；摘要不含明文正文，不冒充外部 Prompt/Memory/Skill 实物快照。
- 原阶段为 orchestrated 且冻结 ExecutionRequest 身份/摘要正确时，可为因失租进入 recovery_required 的原 message/message_recover 建立后继。原用户输入、冻结预算及 task/attempt 终态保持；新增 link 保存 resume_phase、source/execution/model_calls proof。受理、领取和真正提交 Runtime 前复核，后继只从原 JSON 构造请求，不重复 orchestrate_async。
- 模型事实只允许显式位置的 completed root/agent 调用；ready/invoking/unknown 阻止此恢复，完成的语义请求和用量不重发、不改写。同 Turn 的 ToolJob/checkpoint/pending writes 矛盾检查保留；未开始 Runtime 的阶段因果事实才是本窗口的准入依据。
- 真正恢复冻结阶段后更新当前 owner epoch/attempt；恢复再次失租可以建立第二后继，连续谱系保持，旧 owner 不能推进阶段。诊断同步将 submitted/orchestrated 报为 stage_recovery_requires_admission，automatic_replay_allowed 仍为 false。
- 新阶段写 schema_version=3，旧 1/2 可读；旧 link 缺新增字段默认 submitted。历史 orchestrated 缺新来源证明时不补造资格。本轮无 DDL、公开 HTTP/operation、依赖或开关变化，但扩展 JSON 不能由旧严格解析器读取，同一 v2 stream 必须统一兼容制品；未验收滚动/双制品发布。

### TDD 与根因处理

先修正 §37 的失败测试口径：故意改变 input_payload 后，以注入后的持久行作为“不再变化”的比较基线，并独立断言原终态/attempt 不变，没有修改生产代码去迎合错误快照。

新增诊断红测实际 **1 failed、10 passed，29.66 秒**：orchestrated 仍报告 not_implemented，随后同步内部诊断契约。新增坏模型身份红测实际 **1 failed、12 deselected，7.01 秒**：JSON binding 与数据库键不一致的 completed 记录仍能创建恢复后继。根因在模型账本的通用读取，只验证 DTO 格式而未验证数据库身份。现由 `_decode_call` 统一核对 binding/attempt，供 `_snapshot` 与 `_call` 使用；矛盾记录不能获得发送许可。恢复器将该明确错误转换为阶段需对账，保留拒绝日志，不吞任意数据库故障。

受影响回归实际 **1 failed、15 passed，38.91 秒**，暴露 submitted 后继第一次正常完成编排时也被要求匹配 orchestrated 恢复 proof。现按持久 link 的 resume_phase 区分“从 submitted 开始编排”和“消费原冻结结果”，不按瞬时 phase 单独判断。旧 schema 升级测试实际 **1 failed、24 deselected，6.73 秒**，随后将新写入断言同步为 3，保留旧格式读取验证。

独立 Worker 首次测试实际 **1 failed、15 passed，81.43 秒**，未到达冻结门闩，日志显示资源失租和模型结算被拒绝。一秒资源槽位无法覆盖冷 SDK 的同步初始化；只调整该 Harness 的原 Worker 槽位为十秒，保持心跳，在强杀后等待真实 TTL 再回收，后继使用正常 300 秒。修正后独立用例 **1 passed、15 deselected，31.52 秒**；没有修改生产租约设施绕过失租。

### 实际 Harness 和独立进程轨迹

新增 `test_framework_orchestrated_message_recovery.py` 最终 19 项，覆盖并发唯一后继、冻结请求原样执行且编排必抛、来源/原消息/输入/缺 proof/执行身份/ready/unknown 拒绝、发布后及 Runtime 提交前漂移、只读诊断、模式注册表漂移、坏模型 binding/attempt、执行/模型/proof 版本变化、二次失租与旧 owner，以及独立进程强杀。模型账本既有测试另加两项，直接验证坏身份快照和 start 都拒绝，phase 保持 ready。

独立进程复用实际 Redis Worker、PostgreSQL、官方模型 SDK 和根模型策略，强制进行成功的语义分类，在 freeze_execution 后、submit_runtime 前写门闩。父进程确认 running/orchestrated、HTTP=1、completed 用量=10 及子进程存活后 terminate/join；再回收 PEL，使原 task/attempt 保持 recovery_required，由后继领取新 owner。替代执行将 orchestrate_async 设为必抛，并实际进入官方 Deep Agents/PostgreSQL saver；最终 HTTP=2（原语义 1 + 图模型 1）、根预算仍为原对象、调用槽位 2、实际用量 20、原语义记录完全不变。重复投递保持请求 2，Session 只有原 user 和 assistant。供应商是本地合成 HTTP，不是实际付费供应商；最终图未用领域 Runtime 替身冒充。

### 最终验证

在 Agent_Server 执行恢复/账本联合命令：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_orchestrated_message_recovery.py tests/test_framework_message_recovery.py tests/test_framework_message_stages.py tests/test_framework_message_diagnostics.py tests/test_framework_model_calls.py tests/test_framework_model_reservation_transfer.py -q -x -rs --tb=short
```

结果 **112 passed，309.56 秒，无 skip**。包含最终生产源码、独立进程及模型读取修复；运行启动后又添加了三项 proof 漂移用例，单独执行为 **3 passed、16 deselected，13.75 秒**，并纳入以下最终联合回归：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_orchestrated_message_recovery.py tests/test_agent_v2_worker_contract.py tests/test_framework_model_admission.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
```

结果 **87 passed，103.64 秒，无 skip**，包含新文件最终 19 项及 Worker、预算受理、Session 执行权；两组结果有重叠，不累计为互不重复的覆盖数。10 个涉及的 Python 源码/测试执行 compileall 通过；两份文档本地链接与围栏、git diff --check / cached check 通过。

仍未完成：orchestrating 中实际语义输入/响应资产及 transfer_ready 应用调用方、已发送/未知模型响应导入、图完成待业务投影补偿、活动时间、跨 Turn、受控 AgentCase 图和各模式 Harness。未运行全量测试、独立后端 lint/typecheck、前端、官方 conformance、真实供应商/RustFS/浏览器/领域 Runner 或双制品部署。本轮不开放生产默认路由，整体目标继续。

## 39. 图外语义分类实际输入冻结（2026-10-26）

从 `0dd9b7462` 继续方案 A。上一轮闭合的是编排完成后的冻结请求恢复；本轮为 orchestrating 中的恢复保存实际语义输入，不把保存资产等同于自动恢复授权。

### 依据与实现

先沿 SessionService → InputOrchestratorService → SemanticIntentService → ModelRuntimeService → 模型策略/账本阅读现有实现，并复用阶段事务、模型参数白名单及 expected_parameters/expected_runtime_parameters。现有调用发生在图外，官方 checkpoint 不能替代其应用阶段记录。测试口径对齐本地《Agent轨迹与协议鲁棒性》§7.2–7.3：协议和业务错误分别断言，验证身份、重复确认、故障轨迹及版本变化。

- 新增 `PreparedSemanticInput` schema 1，区分实际请求与明确 skip；完整保存确定性 baseline、system Prompt、user JSON、Trace/稳定调用位置、解析后的模型 key、非秘密模型参数、SDK/运行参数和零重试传输选项。校验请求 baseline 配对、工具为空、选择模型一致、设置完整及版本；不复制模型凭据、原始头和地址。
- `prepare` 与 `enrich_prepared` 分离准备及消费。消费从保存后的 DTO 恢复，不再调用 Prompt 构造或模型选择；冻结配置在发送前及既有 invoke 内核对。prepared 路径的执行异常不隐藏为 baseline，实际失败 DTO/不合法分类输出仍沿原规则记录后回退；旧 enrich 入口保持原 baseline 对象及 fallback 契约。
- 输入编排新增显式 record_semantic_input 回调，SessionService 将其连接原阶段服务。阶段仍为 orchestrating、原 owner 有效且账本尚无模型调用时，才可第一次写入 source_manifest.semantic_input 并更新来源摘要；重复确认相同资产返回原记录，变化、错误身份及旧 owner 被拒绝。新入口声明 semantic_input_required，漏接回调不能继续冻结 ExecutionRequest/调用 Runtime。
- 复用 PostgreSQL JSONB，不新增表、公开 API、任务 operation、依赖或生产开关。顶层阶段 schema 3 不变，嵌套输入单独版本化。旧记录可读，不反向生成历史准备数据；部署仍要求兼容 Worker，不宣称完成制品/滚动升级验证。正文只保存在既有业务存储，日志只输出身份、来源摘要与 skip 原因。

语义阶段没有读取 PromptRegistry、Memory 或 Skill 内容；本轮保存的是此处真正使用的内置语义 Prompt。模式注册表仍是摘要，解析/合并与确定性路由代码制品、后续 Runtime 实际使用的外部资产仍待冻结。

### TDD 与实际故障

首轮红测 **1 failed，6.21 秒**：SemanticIntentService 尚无 prepare，先明确“保存后消费、不能重建”的端口契约。接入时又以模型预留钩子读取阶段，实际 **1 failed、5 deselected，7.09 秒**：阶段缺 semantic_input，原宽泛 fallback 将钩子异常变成 baseline，HTTP=0。随后接通保存回调，并让冻结路径的异常保持可见。

既有回归 **1 failed、115 passed，16.30 秒**，暴露旧 fallback 要求返回原 baseline 对象，拆分准备时改变了引用身份；在旧 enrich 包装入口恢复原契约。数据库约束故障测试首次 **1 failed、6 passed，10.62 秒**，实际失败写入已回滚，但 Harness 错误期待裸 psycopg.CheckViolation；项目 SQLAlchemy 边界返回 IntegrityError，现同时核对包装异常和 orig 类型，不改生产来迎合断言。

新 Harness 覆盖保存后修改 Prompt 构造/选择器仍消费原 DTO、模型/SDK参数漂移拒绝、显式 skip、冻结异常上抛、快照结构矛盾、四传输实际官方 SDK 请求、真实 PG 写入回滚、并列身份/重复确认/失租、预留后不能补造输入及缺少必需回调不能触达 Runtime。模型预留前读阶段，并逐一比较冻结的 system/user 文本与真正 HTTP 请求；仅本地合成供应商，不冒充真实付费服务。

既有独立进程三窗口 Harness 增加实际语义输入断言：start 前、start 后未发 HTTP、HTTP 返回待结算处强杀原 Redis Worker，替代 Worker 回收投递后 HTTP 保持 0/0/1，原阶段与完整准备资产不变；仍保持 recovery_required，不因此重入编排。编排已冻结的安全后继强杀 Harness 也纳入本轮回归。

### 最终验证

在 Agent_Server 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_semantic_input.py tests/test_intent_safety_routing.py tests/test_semantic_intent_failure_visibility.py tests/test_framework_model_dto.py tests/test_semantic_intent_trace_http.py -q -x -rs --tb=short
```

结果 **167 passed，118.63 秒，无 skip**。包含最终生产源码；启动后新增的缺少必需回调用例另行执行并记录于下文。

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_stages.py tests/test_framework_orchestrated_message_recovery.py tests/test_framework_message_recovery.py tests/test_framework_message_diagnostics.py -q -x -rs --tb=short
```

结果 **74 passed，212.33 秒，无 skip**。含实际 PostgreSQL、Redis、独立 Worker、官方 SDK 及原冻结请求的官方 Deep Agents/saver 恢复。前期 152/43 项通过仅作中间结果，不累计为最终额外覆盖。

启动后新增的漏接必需回调用例执行 `-m pytest tests/test_framework_semantic_input.py -k bypass_required -q -x -rs --tb=short`，结果 **1 passed、20 deselected，10.31 秒**。8 个涉及 Python 文件执行 compileall 通过；方案/记录分别检查 36/1 个本地文件链接及代码围栏，通过；`git diff --check` 通过。

下一出口仍为 orchestrating 的安全恢复调用方、transfer_ready 应用接线、原响应导入、完整初始图资产及代码制品、活动时间和跨 Turn。未运行全量、独立 lint/typecheck、前端、官方 conformance、真实供应商/RustFS/浏览器/领域 Runner 或双制品部署；本阶段不具备相应完整验收条件。生产默认 v2 路由保持关闭，整体目标未完成。

## 40. 模型结算响应绑定与已结算 DTO 只读导入（2026-10-26）

接续工作区中的结算候选，按方案 §14.13/v3 §5.1 完成此独立边界。模型调用完成时将唯一 settled_response_id 与 completed/usage 同事务保存；读取验证原调用、冻结请求、选中响应资产、格式和用量，异步读取对象后复核当前 owner。新 owner 可只读原已结算结果，不改原观察者、预算或任务终态。无绑定的历史 completed 不猜测恢复；诊断继续禁止自动重放。新 model-call schema 3 对旧严格读者不兼容，发布仍需兼容 Worker。

本轮实际执行（工作目录 Agent_Server）：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_calls.py tests/test_framework_model_evidence.py tests/test_framework_model_result_import.py tests/test_framework_message_diagnostics.py -q -x -rs --tb=short
```

结果 **84 passed，262.58 秒，无 skip**。此前 v3 复核的 111 项含本功能部分测试，两次有重叠，不累计覆盖数。测试覆盖提交前/提交后 ACK 丢失后重建存储对象读取，未结算观察拒绝，新 owner 只读，原资产错误/身份漂移、重复结算和旧 schema 摘要兼容。真实 PostgreSQL/Redis、官方 SDK 与本地合成供应商；对象存储使用内存 RustFS client 替身，未验真实付费供应商/对象存储。

此端口不代表语义分类调用方、orchestrating 后继或图内结果导入已接通。下一步按 TDD 接入语义服务对原结算结果的消费，并复用现有解析/安全合并；其首条红测已暴露 enrich_prepared 不接受 completed_call，**1 failed、16 deselected，13.42 秒**。测试文件尚不属于本阶段提交。生产路由保持关闭，整体目标继续。

## 41. 语义服务消费原已结算响应（2026-10-26）

从 `d356f66d2` 继续 v3 §5.1/A。原底层只读端口已有，但 SemanticIntentService 无法消费已结算输出，重新 enrich_prepared 会进入正常模型调用。因此在原服务上增加明确 completed_call 分支，使用 ModelRuntimeService.restore_completed；普通入口不自动查账本、不自动重放。未引入第二套分类解析或合并策略。

### 实现与边界

- 新读取入口要求当前有效 owner、共享调用策略、原冻结参数/运行参数及零重试传输契约，重新核对模型配置/SDK/超时/适配器；再委托已有底层端口核对完整结算、原请求/响应资产和用量。不解析凭据，不发供应商请求，不发送流式回调，不变更原预算或调用次数。
- 发送和读取使用同一个 `_framework_invocation_options`，防止流式、适配器和传输参数分别拼装产生漂移。结构化日志记录读取拒绝或已消费的原 attempt/settled_response_id，不输出正文或凭据。
- enrich_prepared 的已完成分支必须提供完整冻结请求；保存的 skip 或无运行参数请求不能附带 completed_call。返回结果复用同一段 response mode、候选解析和保护性合并；不合法的原语义输出仍按既有规则回退 baseline，资产/身份/执行权异常明确上抛，不以 baseline 吞掉恢复错误。
- 新建模型/语义服务对象后仍读取持久原资产；不重建 Prompt、模型选择或原调用位置。接口为内部兼容新增，无 DDL、公开 HTTP/任务 operation 或默认开关变化。

### TDD 与验证

首条红测见 §40：不支持 completed_call，1 failed。实现后的新文件首次执行 **19 passed、1 failed，76.38 秒**；失败为测试构造 ModelTransportOptions 时漏掉必需 timeout_seconds。已改为从原冻结值复制后修改 retries，没有修改生产校验迎合错误 fixture。随后增加重建服务及原流式结果读取检查。

最终在 Agent_Server 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_semantic_result_import.py tests/test_framework_semantic_input.py tests/test_framework_model_dto.py tests/test_intent_safety_routing.py tests/test_semantic_intent_failure_visibility.py tests/test_semantic_intent_trace_http.py -q -x -rs --tb=short
```

结果 **190 passed，207.24 秒，无 skip**，新文件最终 22 项。覆盖四传输真实官方 SDK/合成 HTTP 的原分类消费及重复读取、无效候选 fallback、保护安全意图、原输入/模型/运行配置漂移、缺资产/失租/非已结算事实拒绝、缺 owner/策略/契约/模型及非零重试拒绝，以及 native/官方 LangChain 两种原流式 DTO 的无 Token 重发读取。数据库为真实 PostgreSQL/Redis，响应对象存储仍为内存 RustFS client 替身，不冒充付费供应商或真实对象存储。

两个源码和新测试执行 compileall 通过，文档链接/围栏及 scoped diff check 通过。没有执行全量、独立 lint/typecheck、前端、官方 conformance、真实供应商/对象存储/领域 Runner 或双制品部署；这些不属于本次已闭合的端口验收范围。

下一步仍需将冻结语义消费连接完整 orchestrating 后继准入及 Session/InputOrchestrator，冻结确定性路由/解析合并代码制品，接通 transfer_ready 的应用调用方和相关故障出口。本阶段不创建这类后继、不推进图/阶段、不开放 v2 默认路由；图内结果导入、活动时间、跨 Turn 及后续各模式目标保持未完成。

## 42. 冻结语义输入的应用编排消费（2026-10-26）

在 `ae6551635` 后沿 v3 §5.1/A 继续开发。调用链确认：SessionService 对 message_recover 仅消费 orchestrated 阶段的冻结 ExecutionRequest；orchestrating 尚无后继准入，InputOrchestratorService 也没有跳过重新识别/准备的应用入口。本阶段闭合后一个边界，为后继接线复用原编排提供基础，不开放前一个边界。

### 实现与边界

- 新增内部 `InputOrchestratorService.orchestrate_prepared`，要求明确的原 Turn/trace 身份；实际模型请求还需核对 Session、Turn、trace、原归一化输入及完整冻结参数。请求需要原语义服务；服务不可用、身份或冻结契约缺失时显式报错。
- 持久化的三种 skip 原样使用其 baseline，无论当前服务是否启用/可用，都不重新选择模型。已结算请求显式传入 completed_call，委托 §41 的只读消费，不重新调用 orchestrate_async、识别 baseline 或构造 Prompt。
- 正常首次冻结路径也使用同一入口。通过共享 `_orchestrate_with_intent` 保持原有安全评估、模式选择、领域路由、ExecutionRequest 组装与分类日志；未复制一套解析/合并/路由。恢复错误不得转为 baseline 后继续投影，新增结构化拒绝及消费日志。
- 此入口不独立证明完整 payload、Session 上下文、模式表或代码制品与原 admission 一致。它们仍由下一阶段的恢复准入/阶段事实负责。当前安全评估仍使用既有逻辑，包括当前授权有效期检查；没有把原语义结果当成授权。
- 无 DDL、公开 API、任务 operation、阶段状态机或默认开关变化。当前 Session 的正常冻结调用已使用新入口；orchestrating 后继的 Session 接线与 transfer_ready 调用方仍待完成，不能宣称完整恢复。

### TDD 与实际验证

新测试首条红测：`AttributeError: 'InputOrchestratorService' object has no attribute 'orchestrate_prepared'`，**1 failed，8.01 秒**。实现首次回归又暴露遗漏 json import，**1 failed，5.94 秒**，补齐后联合回归 **173 passed，119.97 秒，无 skip**。增加服务不可用时的原 skip 和错误不投影覆盖，并合入模型 DTO 回归后，在 Agent_Server 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_prepared_orchestration.py tests/test_framework_semantic_input.py tests/test_framework_semantic_result_import.py tests/test_intent_safety_routing.py tests/test_semantic_intent_failure_visibility.py tests/test_semantic_intent_trace_http.py tests/test_framework_model_dto.py -q -x -rs --tb=short
```

结果 **210 passed，216.53 秒，无 skip**；新文件 20 项。四传输实际使用官方 SDK 与本地合成 HTTP，重建服务后两次消费原结算，ExecutionRequest 与首次结果相同，HTTP 请求始终只有 1 次，原调用账本/预算不变。另覆盖原 skip 不重算、禁止重识别/重建准备、身份/内容/冻结配置缺失拒绝，以及语义失败不得进入应用投影。PostgreSQL/Redis 为真实服务，对象存储仍为内存 RustFS client 替身，不代表真实供应商或对象存储验收。

源码与新测试 `compileall` 通过，`git diff --check` 通过。未执行全量、独立 lint/typecheck、浏览器/压测/扫描 Runner、真实模型/对象存储或双制品部署。下一步按 TDD 冻结并验证确定性解析/合并/路由代码制品，接入完整 orchestrating 后继准入与 Session 恢复调用方，区分未预留、未发送、已发送未知及已结算窗口；原 task/attempt、用户消息与根预算保持原身份。

## 43. orchestrating 阶段的安全后继与编排代码制品指纹（2026-10-26）

本阶段沿 v3 §5.1/A 接通“语义输入已实际冻结、图尚未准入”的恢复窗口。该窗口不能依赖 LangGraph checkpoint，因为语义分类和确定性编排仍在图外；恢复依据是应用阶段事实、模型账本和不可变部署制品证明。生产 v2 路由仍关闭。

### 实现与边界

- 新增 `OrchestrationArtifact`，记录编排边界源码、根 `uv.lock`、Python 及 pydantic 版本的 SHA256/版本指纹。`message_source_manifest(semantic_input_required=True)` 在首次语义准备前保存指纹；恢复受理、阶段推进和运行前重新核对。它只证明当前发布代码一致，不是代码归档，也不支持热修改已导入模块。
- `FrameworkMessageRecoveryLink.resume_phase` 增加 `orchestrating`，阶段 Schema 升至 4（旧 1/2/3 仍可读）。后继证明包含来源和模型调用摘要，不包含 ExecutionRequest；历史缺制品或语义输入证明时不补造资格。正常 submitted/orchestrated 恢复路径保持原语义。
- `dto_call_contract` 统一初次发送和恢复准入的请求字节、传输选项与稳定逻辑调用位置。`prepared_model_call` 只接受一个已冻结语义请求：未预留允许首次发送，ready 只转移原预留，completed 只读原 `settled_response_id`；invoking/unknown/身份、参数、所有者或请求哈希矛盾均拒绝自动继续。显式 skip 不产生模型调用。
- `SessionService` 对 orchestrating 后继消费 `PreparedSemanticInput` 和原结算 DTO，调用 `orchestrate_prepared`，不重新执行 `orchestrate_async`、Prompt 构造或模型选择；ready 通过既有 `transfer_ready` 转移预留。随后再次冻结 ExecutionRequest、提交 Runtime 并投影，原 task/attempt、用户消息和根预算身份不变。
- 诊断只读地将 orchestrating 标为 `stage_recovery_requires_admission`，`automatic_replay_allowed` 仍为 false。发布事务失败回滚阶段后继和 outbox；claim/运行前的制品、输入、模型结算和 Schema 证明漂移拒绝且不创建新 attempt。

### TDD 与实际验证

先以红测验证缺模块、缺恢复后继和诊断仍误报 `stage_recovery_not_implemented`，再实现生产接线。期间联合强杀 Harness 首次出现 `run_once()==0`，单跑通过但不足以覆盖联合污染；补入 v2 stream 隔离并重新执行，未修改 Worker 生产调度来掩盖结果。旧强杀用例中“before_start”现按新契约断言有证明的 queued 后继；after_start/after_response 仍断言未知发送不重发。

在 `Agent_Server` 使用真实 PostgreSQL/Redis、官方 SDK 与本地合成 HTTP 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_orchestrating_recovery.py tests/test_framework_orchestration_artifact.py tests/test_framework_message_diagnostics.py -q -x -rs --tb=short
```

结果 **63 passed，161.62 秒，无 skip**。覆盖 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Gemini GenerateContent 四种语义请求的恢复，completed/ready/unreserved/skip 四窗口，制品/输入/请求/所有者/结算/Schema 漂移，发布事务回滚、重复投递、二次失租、只读诊断及独立 Worker 相关契约。另执行旧阶段与强杀回归：新阶段集成测试 **46 passed，126.82 秒**；此前联合窗口中发生的单次不稳定 `run_once()==0` 已重新跑通，不能将那次失败计入通过证据。

源码和测试 `compileall`、`git diff --check` 通过。未执行全量测试、独立 lint/typecheck、真实供应商/对象存储、图内模型结果导入、领域 Runner、双制品滚动部署或跨 Turn；因此本节不宣称完整迁移。下一出口是冻结完整图输入/结果资产，接通 runtime_submitted 和图完成投影补偿，并为各模式建立正式 Harness。

## 44. Deep Agents 图输入资产冻结（2026-10-26）

本阶段沿 v3 §3.1/§5.1 接通图外应用阶段与官方 Deep Agents 图之间缺失的输入证明。此前 `thread_id=turn_id`、原生 transcript 和 checkpoint 已存在，但应用阶段没有保存图实际接收的 Prompt、消息、治理工具集合和能力开关；仅有 checkpoint 不能证明图启动前的输入一致。

### 实现与边界

- 新增 `FrameworkGraphInputAsset`（Schema 1），保存 Deep Agents 运行的 session/turn/trace/model/checkpoint namespace、system prompt、显示消息、完整框架消息序列、运行上下文、业务工具 key、审批工具 key 和能力开关，并对除摘要字段外的完整 JSON 内容计算 SHA256。非 JSON 值直接拒绝，不将任意对象字符串化。
- `RuntimeService._execute_deep_agent_turn` 在调用适配器前生成资产；通过可选回调写入应用阶段。`SessionService` 只为绑定运行传入阶段写入回调，legacy/未绑定路径不改变。`FrameworkMessageStages.record_graph_input` 在 `runtime_submitted` 阶段同 owner 事务中保存资产，重复相同资产幂等，绑定、内容或摘要变化拒绝。
- 资产保存的是实际适配器入口契约，不包含模型凭据或工具处理对象；仍需由官方 checkpointer 保存图内状态和原生消息。当前尚未把 runtime_submitted 的 checkpoint/模型结果导入恢复做成自动后继，也未宣称外部副作用 exactly-once。

### TDD 与实际验证

首条红测为缺少 `framework_graph_input` 模块（1 failed）；实现后先通过资产往返/摘要篡改 5 项，再补真实 PostgreSQL 阶段记录、同资产幂等和变更拒绝，最终 6 项通过。新增 RuntimeService 测试验证资产在适配器调用前生成；相关阶段、适配器和编排恢复联合回归实际结果：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_graph_input_asset.py tests/test_deep_agent_runtime_adapter.py tests/test_framework_message_stages.py tests/test_framework_orchestrating_recovery.py -q -x -rs --tb=short
```

结果 **117 passed，173.05 秒，无 skip**；随后修正资产变更测试为使用合法新摘要并单独复跑 **6 passed，7.80 秒**。源码与测试 `compileall`、`git diff --check` 通过。未执行全量测试、图内模型结果导入、runtime_submitted 故障恢复、真实供应商/对象存储、领域 Runner、跨 Turn 或双制品部署；下一出口是以该资产和 checkpoint/模型账本事实为依据闭合图内发送、已结算导入及完成后业务投影补偿。

## 45. 应用原子提交凭证与阶段 ACK 恢复（2026-10-26）

按 v3 §12.1/§15 和 TDD 继续 A1。原 runtime_submitted 恢复把图前执行事实禁令用于图后；仅有 snapshot 又不能证明最终 Session/messages/events 已提交。现复用既有最终事务，写入有明确 scope 的 `FrameworkProjectionCommit`；不是简单删除 checkpoint/ToolJob guard。

### 实现及真实边界

- message 首次执行和安全后继的最终提交传入不可变 owner/结果引用；Session/messages/completion events 和凭证在同事务、同所有权锁中落库。已有凭证不可替换为不同结果，无新表。记录进入 message stage Schema 6，旧严格读者不兼容；默认开关保持关闭，不代表可滚动发布。
- 证明绑定原输入、task/attempt/epoch、精确快照和 assistant 消息、完成事件、完整已完成模型账本、终态 ToolJob 与 Verification；只读恢复在受理/claim/读取/阶段推进事务重新核对。只恢复该 Turn 已提交的生命周期，不覆盖 Session 其他并发属性，不重建消息/事件或运行图。
- 对历史 completed 且缺 settled_response_id 的账本，只对原事实做摘要。这里读取的是已提交应用输出，不消费模型响应，不补造原响应或授权图推进。ready/invoking/unknown 仍拒绝；模型原结果导入继续要求唯一响应与原资产，联合回归保护该边界。v3 §15 解释了与早先过强建议的区别。
- 非空 ToolJob 行统一转 dict 计算摘要，并纳入 attempt、结果元数据及时间；空应用输出仍为空，使用原快照身份，不从 graph_state 文本猜测。裸 snapshot 不授予恢复资格，Memory/资源清理/外部副作用明确不在证明 scope。

### TDD 与故障证据

接续回归先得到 **1 failed、37 passed，130.74 秒**：新凭证把可选模型原响应绑定错误地加入最终提交前提。正式新用例又以真实 PostgreSQL 非空 ToolJob 复现 RowMapping 序列化错误，**1 failed，8.58 秒**；修复行规范化后暴露原 fixture/DB updated_at 差异，改用真实持久事实比较，随后 completed 无响应绑定用例真实变红（1 failed、1 passed），再修正端口权限边界。

新空输出红测复现恢复从 snapshot.final_response 取错文本（1 failed、1 passed，9.60 秒），已改为返回原空输出。claim 拒绝的现有契约返回 recovery_required 而非 None，测试改为检查真实终态/attempt_no=0，没有修改生产调度。期间启动的旧测试进程仍使用启动时源代码，旧失败不作为最终版本结果。

独立进程 Harness 实际使用官方 Deep Agents、SDK、PostgreSQL saver 和 Redis Worker，在应用最终事务提交后、阶段 ACK 前阻塞并强杀；新 Worker 从 receipt 接管，HTTP 请求保持一次、消息两条、完成事件一次、旧 task 终态不变。单独首次运行 **1 passed、18 deselected，13.53 秒**；最终文件继续加入其它负例，不累计为额外覆盖。

### 验证与下一出口

在 Agent_Server 使用独立解释器、RUN_LIVE_POSTGRES_TESTS=1 实际执行：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_application_projection.py tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_agent_task_dispatch.py tests/test_framework_model_result_import.py -q -x -rs --tb=short
```

结果 **110 passed，360.01 秒，无 skip**。联合运行启动后新加的“读取后、阶段 ACK 事务前漂移”用例先独立执行 **1 passed、24 deselected，7.16 秒**；最终整个新文件再次执行 **25 passed，79.02 秒，无 skip**。以上有重叠，不累计为不同用例数。此前不含模型导入及后加故障测试的中间联合回归 **77 passed，220.15 秒**，只作中间证据。11 个 Python 文件 compileall、暂存索引 Python 语法检查和 git diff --check 通过；文档本地链接及围栏检查通过。

上述运行使用当前工作区，含保留的跨 Turn 草稿，不标记为纯 HEAD 制品验收。额外暂存索引导出验证单独记录，不重置或覆盖用户工作区。

后续再补快照头部漂移红测：只改 version 而保持 graph_state 不变，旧摘要仍允许恢复（**1 failed、25 deselected，14.15 秒**）。已将凭证快照摘要改为数据库实际保存的 id/session/version/created_at/stage/graph_state 全记录；调用方的预期 graph_state 摘要仍独立校验，兼容保存时合法重分配 version 的既有行为。

最后源码的新文件完整回归为 **26 passed，112.72 秒，无 skip**（含快照头部漂移）；先前 110/25 项记录为其变更前的回归，不冒充最终全部源码的联合验收。最终 11 个涉及 Python 文件 compileall 通过。

暂存索引独立导出验证首轮缺少必需配置，setup 阶段 1 error；未作为功能失败或通过，配置仅经子进程环境传递、不复制认证配置文件。配置补齐后的运行 **1 failed、24 passed，130.64 秒**，暴露原 1 秒资源租约在隔离制品冷启动中先于故障注入点丢失。Harness 改为执行时使用 30 秒租约，确认提交后强杀，再由已捕获的真实资源租约通过既有 renew 缩短至 1 秒并等待过期；没有改生产调度或绕过所有权检查。首轮 Pydantic 诊断包含配置输入片段，未复述/写入仓库；后续诊断在内存捕获并遮蔽 input_value 后输出。

修正后从**暂存索引导出的独立源码**执行新文件、既有独立 Worker 编排恢复用例、历史 completed observation 不升格用例，最终 **28 passed，124.87 秒，无 skip**。导出目录不包含未提交 transcript 源码修改；逐字节验证其变更 Python 源码与暂存索引一致。此结果验证本轮代码不依赖草稿，不代表完整发布制品或双制品部署验收。

另实际执行 Session 事件/所有权/值契约回归：`-m pytest tests/test_session_event_delivery.py tests/test_session_execution_ownership.py tests/test_session_store_value_contract.py -q -x -rs --tb=short`。首次漏设 live 开关，结果 71 passed、72 skipped，不作为真实集成通过；明确设置 RUN_LIVE_POSTGRES_TESTS=1 后为 **141 passed、2 skipped，43.84 秒**。剩余两项是仅 PostgreSQL 有意义的事务排序/跨进程用例在 InMemory 参数下主动跳过，对应 PostgreSQL 参数已运行。

范围仍未完成：原最终事务提交前的应用结果资产和部分投影补偿、stage projected 后 Worker 任务结算前的出口、Memory/清理分项事实、图内模型原结果导入、活动时间、完整跨 Turn 和逐模式 Harness。A1 不能替代这些出口。未跑全量、独立 lint/typecheck、前端、官方 saver conformance、真实付费模型/对象存储/浏览器/压测/扫描 Runner 或双制品部署。生产默认关闭，整体 goal 保持 active；下一步继续 A2/A3，而不是重复升级依赖或重新造调度器。

## 46. A2-1 原结果资产与统一收尾（2026-10-26）

沿 v3 §17 实现并验证 A2-1。该阶段只处理“图返回后、应用最终事务前”的原结果输入，不开放失租后的自动后继，不把 A1 阶段 ACK 恢复扩大为图恢复。

### 实现

- 新增 `FrameworkRuntimeResult` / `FrameworkRuntimeResultAsset` 与 `FrameworkRuntimeResults`。资产保存完整 `RuntimeTurnResult` 七字段、绑定/输入/执行摘要、任务谱系、Verification、assistant 身份和固定投影时间；通过现有 message-stage JSONB 与 owner 事务保存，禁止 Python 进程对象进入资产。
- `SessionService` 在首次 finalizer 前冻结资产，随后只从持久原件重建并调用原有 `_finalize_runtime_result`；Verification、assistant、完成事件使用资产中的稳定身份和时间。`FrameworkProjectionWrite` 在最终应用事务内再次核对资产身份、模型/ToolJob 摘要，防止异步投影阶段发生事实漂移。
- message-stage schema 升至 7；应用提交凭证写入时保留更高 schema，避免带原结果资产的阶段被降回 6。没有新增队列、任务类型、DDL 或默认开关。
- 提交事实更正：原跨 Turn transcript 的五个相关文件实际已随 `73530febe` 提交，并非留在工作区未提交；早期隔离索引测试不含它们的记录只说明对应测试时点。原生框架消息作为 RuntimeTurnResult.state 的一部分保存，不代表跨 Turn 完整链路已经接通。

### TDD 与验证

先在隔离 HEAD SessionService 副本复现真实缺口：图完成后进入 finalizer 前没有 `runtime_result_asset`，**1 failed，17.97 秒，KeyError**。实现后在 `Agent_Server` 使用独立 v2 解释器、真实 PostgreSQL/Redis、官方 Deep Agents/LangGraph saver 与本地合成 HTTP 执行：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_agent_task_dispatch.py -q -x -rs --tb=short
```

结果 **107 passed，393.13 秒，无 skip**；此轮启动后才增加的最终事务重验不在该结果中。新增真实数据库漂移测试复现资产初次读取后、最终事务前的竞态：模型事实变化却仍提交，**1 failed，10.19 秒，DID NOT RAISE**。根因是在资产 load 处验证而没有在实际提交事务内再验证；现由 FrameworkProjectionWrite 携带原资产摘要，最终事务锁内核对资产及模型/ToolJob 摘要，不靠写前单次检查。

修复后该三参数测试 **3 passed，15.64 秒，无 skip**；另一重复启动的进程为 **3 passed，19.73 秒**，不累计覆盖。最终工作区完整重跑资产与 A1 投影两个文件：**51 passed，158.12 秒，无 skip**。此前重跑因工具句柄在中断后丢失，结果未回收，不计通过。独立模型结果导入/依赖回归 **37 passed，93.36 秒，无 skip**，启动早于最终事务变更，不替代最终源码回归。

补入“初次读取后原资产被移除”的同类事务负例，复现 **1 failed，8.33 秒，DID NOT RAISE**：分支只在数据库仍有资产时校验，缺失被当成历史兼容。现要求写入方捕获的摘要与数据库资产存在性同时匹配；原无资产旧路径仍兼容，不补造证明。

早期独立暂存索引导出（不含未提交 transcript 草稿）执行资产文件、A1 独立 Worker 强杀和历史 completed observation 不升格：**24 passed，98.29 秒，无 skip**。最终事务竞态修复后三参数版本的独立索引回归为 **27 passed，92.38 秒，无 skip**，早于随后新增的 asset_removed 参数，不冒充最后一次源码全覆盖。索引源码确认 1371 个文件，未带入未提交 transcript 模块。`compileall`（含 src/tests）与 `git diff --check` 通过；独立环境无 ruff（No module named ruff），没有安装 lint 工具或声称 lint/typecheck 通过。

首次独立 Worker 测试还曾得到 **1 failed、15 passed，65.57 秒**：测试把 `tasks.get()` 返回值的 runtime_owner=None 当成可用执行权。该读取接口有意不发放 owner；测试已改为只读阶段资产，不重建或伪造执行 capability。其后资产文件 **18 passed，67.20 秒**；新补测试均以较晚回归为准，不叠加计数。

## 47. A2-2 Runtime 边界回调（2026-10-26）

A2-1 的 finalizer 前置保存仍无法覆盖“图已返回、SessionService 尚未拿到返回值”的窗口。先写红测要求 `RuntimeService.execute_turn(..., on_runtime_result=...)` 在对上层返回前等待回调，旧实现实际报 `TypeError: RuntimeService.execute_turn() got an unexpected keyword argument 'on_runtime_result'`。实现后 RuntimeService 在 `_execute_turn` 返回与自身返回之间执行回调；SessionService 绑定同一结果资产准备逻辑，并保留旧/替身 Runtime 的单次 fallback。

实际运行（独立 v2 解释器、真实 PostgreSQL/Redis）:

```powershell
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py tests/test_deep_agent_runtime_adapter.py::test_runtime_result_callback_runs_before_execute_turn_returns -q -x -rs --tb=short
```

结果 **53 passed，156.74 秒，无 skip**。本次只证明 Runtime 边界回调契约和现有资产/投影竞态防护；未证明图终态到回调内部的任意进程强杀均可恢复，也未开放生产默认开关。下一出口仍是 A2-2 的真实 Deep Agents 独立 Worker 强杀与结果材料完备性核验。

上述测试不等于真实付费模型、真实对象存储、领域 Runner、官方 saver conformance、完整跨 Turn 请求或双制品滚动部署通过；图终态到资产前空窗、失租后部分投影补偿、projected 后任务结算及 Memory/清理仍未完成。生产默认开关保持关闭，整体 goal 继续 active。


## 48. A2-1 快照分项回执与重入校验（2026-10-26）

本轮基线 `d698e194a`，用户新目标附件重新纳入完整 v3 开发范围；先读取目标、当前源码和现有测试，再按 TDD 推进 A2-1 的快照分项。不重新安装依赖，不新增调度器，不打开生产开关，不把本子步标为整个 A2 或整体目标完成。

### 依据与实现

官方 Persistence/Interrupts 在线复核；本地《Agent轨迹与协议鲁棒性》关于重复执行、幂等与恢复的测试原则，以及现有 A1 owner/Session 事务作为模板。实现契约见方案 §19。

- `FrameworkSnapshotProjectionCommit` 和 message-stage schema **8**：快照插入、原资产绑定、实际整行摘要和 snapshot_count 同事务。使用原 JSONB journal，无 DDL。
- `PostgresSessionStore.save_snapshot` 对框架资产路径先验证原意图和 owner；已有匹配回执只读复用，冲突或缺失转对账。首次插入使用 DO NOTHING 并检查 rowcount，不通过 UPSERT 覆盖检查后出现的同 ID 行。旧非框架路径保留原行为。
- 返回实际分配版本的快照供唯一 finalizer 发布事件；最终应用事务再次核对快照回执和实际整行。复用原结果验证函数，避免另写一套资产验证规则。
- 同步 SessionStore Protocol/PostgreSQL/InMemory 签名；内存实现不伪造 owner 事务能力。新增成功/拒绝日志，不记录正文或凭据。
- Worker 在 message/message_recover claim 前按原始格式号检查可读范围，不先解析未知格式。默认读者支持 1–8；测试声明 max=7 的读者及未来 schema 9，任务保持 attempt_no=0。这不是实际旧镜像/双制品部署演练。
- 修正文档提交事实：五个跨 Turn 草稿相关文件已随 `73530febe` 提交，不代表跨 Turn 链路验收。Runtime 回调源码注释改为“缩小空窗”，不再声称关闭图终态→资产窗口。

### 真实红测与回归

1. 首次 finalizer 快照后、最终事务前失败；修改 graph_state 后再次 finalizer，旧实现覆盖漂移：**1 failed，9.91 秒，DID NOT RAISE**。修复后另覆盖 version/created_at 漂移、实际版本分配、并发相同写入、缺行/回执、格式及事务回滚。
2. 不支持的新格式仍调用 claim：**1 failed，8.81 秒，Unsupported format must be deferred before claim**。增加读取原始版本的能力检查，不通过领取后失败替代。
3. 在快照检查与插入之间注入同 ID 行，仍被 UPSERT 覆盖：**1 failed，7.30 秒，DID NOT RAISE**。改为不覆盖式插入并检查实际写入行数；该注入与被测写入同事务，用于稳定复现这一 SQL 窗口，不冒充真实跨 Session 进程竞争测试。

联合回归曾 **1 failed、39 passed，126.78 秒**：已有 asset_removed 故障样本现在形成“快照回执存在但原资产缺失”的非法阶段，测试在事后调用严格 reader 自己失败。改为检查原始数据库记录没有提交凭证，保留生产严格校验，没有放宽缺资产准入。之后中间三文件回归 **68 passed，229.86 秒，无 skip**；此结果早于同 ID 竞争修复，不代替最终源码验证。

在 Agent_Server，使用独立 v2 解释器，以下均显式设置 `$env:RUN_LIVE_POSTGRES_TESTS='1'`：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_snapshot_projection.py tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_agent_task_dispatch.py tests/test_session_store_value_contract.py -q -x -rs --tb=short
```

结果 **150 passed，229.84 秒，无 skip**。命令启动后新增加的读者 max=7 参数由下面最终新文件回归补验，不把它计入此 150 项。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py -q -x -rs --tb=short
```

结果 **53 passed，164.45 秒，无 skip**。包含官方 Deep Agents/SDK、本地合成 HTTP、真实 PostgreSQL/Redis、独立 Worker 的快照事务后强杀和原 A1 提交后 ACK 前恢复；并非真实付费供应商/领域 Runner 验收。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_snapshot_projection.py -q -x -rs --tb=short
```

最终新增文件 **17 passed，45.05 秒，无 skip**，包括 schema 9/读者 8 和 schema 8/读者 7 的 claim 前拒领。上述测试重叠，不累计为不同覆盖数。另从项目根执行 `.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests` 通过；独立环境无 ruff/mypy，未安装，不声称 lint/typecheck 通过。

### 边界与下一步

快照事务后的独立 Worker 强杀证明原资产和实际快照回执均保留，HTTP 请求为一次；仍断言没有最终应用提交凭证、没有 completed 事件、失租后不开放自动后继。不能将该负断言当成完整恢复成功。

后续继续原事件冲突校验、观察稳定身份及实际回执、Verification/消息分项复用与 message_recover 准入，再验收部分投影补偿。图终态到资产前空窗、projected→finish、Memory/清理、完整跨 Turn、统一 Prompt/Skills/Memory、流式/取消、受控 AgentCase、逐模式 Harness 和兼容部署仍未完成。未跑全量、前端、官方 saver conformance、真实付费供应商/对象存储/领域 Runner 或双制品滚动/回滚演练。schema 8 发布仍须先部署兼容读者或隔离消费者；本次 claim 前检查不能 retroactively 改造既有旧镜像。默认开关保持关闭，整体 goal 维持 active。

## 49. A2-1 事件分项回执、最终复核与强杀窗口（2026-10-26）

基线 `025428255`。接续暂停前未提交的事件草稿；用户要求暂停时已将 goal 设为 paused，收到继续指令后核对 goal 已 active，再读取目标附件、规则、实际工作区和方案。实施范围是 §12.1 A2-1 的事件分项，不把局部绿色测试改写为全系统迁移完成。

### 依据与实现

重新在线查阅 [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)，并读取本地 Agent 轨迹课程的幂等/补偿原则。checkpointer 管图状态，store 管跨线程应用数据；节点重入并不替应用 Session 事务提供事件行证明。本次沿已有 Session/owner/message-stage/A1 事务实现，不新增调度层或升级框架。详细契约见 v3 §20。

- 新增 `FrameworkEventProjectionCommits`，使用原 message-stage JSONB。事件行和完整存储行摘要在同一事务，包含 `_harness_context`；合法重复不重计数、不重发 outbox/热事件。业务意图校验与存储整行摘要分开，保留首次自动 enrichment，显式原关联仍须匹配。
- Runtime 已流式持久化的事件允许按冻结原意图首次收录；生成的收尾事件不得凭裸行补造回执。新增行碰撞在插入后再次核对，即使后来出现相同内容的生成事件行，也不能把它当本事务的首次插入。
- 唯一 finalizer 在事件 flush 前构造原投影参数，接通 Runtime、snapshot.saved、verification.completed；completion events 在既有 Session 最终事务内记账。最终事务和 A1 ACK-only 读取复核已收录事件，包含原 Runtime 事件，不只检查最后三条完成事件。
- Protocol/PostgreSQL/InMemory 同步可选参数；InMemory 明确拒绝需要 owner 事务的回执，legacy 普通事件保留原语义。成功/拒绝使用现有结构化日志设施，不打印事件正文、凭据或 lease token。
- 新写入格式为 **message-stage 9**，新 reader 支持 1–9。原结果来源摘要排除新增回执，A1 凭证仍包含带回执的完整来源。历史 6/7/8 格式形状实际走完 ACK-only 后继；stage 9/reader 8、未来 stage 10/reader 9 在 claim 前拒领。格式 Harness 不等于真实双制品部署，兼容读者仍须先行或隔离消费者。

### 红测与中间修正

- 接续前已有真实数据库事件内容漂移红测，旧 `ON CONFLICT DO NOTHING` 静默接受同 ID 的变化。接线后首轮 **4 passed，14.66 秒**，覆盖 payload/type/timestamp/context。
- 本轮再补“生成事件已存在但回执丢失”用例，实际 **1 failed，6.98 秒，DID NOT RAISE FrameworkMessageStageRecoveryRequired**。修复的是生成/流式事件的提交来源契约，而非增加重试或异常吞噬；最终测试另覆盖检查后同 ID 相同/不同内容碰撞、缺行、摘要/绑定/资产/格式漂移、owner 失效、并发收录、事务回滚及最终事务前漂移。
- 中间联合回归 **1 failed、34 passed，106.95 秒**：新增强杀断言用 API 事件投影计算整行摘要，遗漏 API 已拆到顶层字段的 `_harness_context`。改为读取真实数据库整行，与生产回执保持同一证据层，不放宽生产校验。
- 扩展旧格式完整 ACK 验收时 **1 failed，6.75 秒**：拿失租 claim 从原行拼装的返回值当完整终态快照比较；沿既有 Harness，在 claim 后重新读持久终态，再确认后继不改变它。
- 扩展公共/审批回归 **1 failed、140 passed，190.72 秒**：既有审批测试的 `SimpleNamespace` 漏传替身 Runtime 已要求的 `runtime_binding`。修正测试调用方，保留原“原生投影后人的拒绝不被收尾覆盖”断言，不给生产代码加宽松 fallback。

### 最终验证（不是把中间结果累加）

以下均在 Agent_Server，使用独立 `.venv-framework-v2` 解释器，并设置 `$env:RUN_LIVE_POSTGRES_TESTS='1'`：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_event_projection.py tests/test_framework_snapshot_projection.py tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py -q -x -rs --tb=short
```

**101 passed，274.72 秒，无 skip**。覆盖事件、快照、原结果资产、A1 提交及实际进程边界。此后旧格式测试补强为执行后继并核对持久原终态，使用下面第二组重新验收。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_event_projection.py tests/test_framework_approval_projection.py -q -x -rs --tb=short
```

**44 passed，111.10 秒，无 skip**。包括最新 29 项事件用例、旧格式实际 ACK-only 和修正后的原生审批投影 Harness；与上组事件测试重叠，不累计为独立覆盖数。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_agent_task_dispatch.py tests/test_session_store_value_contract.py -q -x -rs --tb=short
```

**134 passed，172.77 秒，无 skip**。覆盖共享 message stage、恢复/调度及 PostgreSQL/InMemory 普通值契约。另项目根 `python -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check` 通过；环境未安装 ruff/mypy，PATH 也无对应命令，未安装新工具，不声称 lint/typecheck 已通过。

### 证据等级、限制与下一步

强杀沿既有独立 Worker Helper 新增 `after_snapshot_event`：真实官方 Deep Agents/SDK、本地合成 HTTP、PostgreSQL/Redis，snapshot.saved 事务已提交且 finalizer 尚未完成。检查实际数据库事件行与回执，强杀后原资产、快照及事件回执保持不变，HTTP 请求为一次，模型预算不重置。没有最终应用凭证、没有 turn.completed，现阶段仍不创建失租后继；这是保留原事实与拒绝不安全恢复的验证，不是完整补偿成功。

普通事件故障样本复用真实 PostgreSQL 和单节点 LangGraph/替身 Runtime；插入竞争用同事务稳定注入，不冒充真实跨 Session 进程竞争。历史格式用形状 fixture，不冒充旧二进制部署；上下文切换用 bind_request_context，不冒充已经开放接管。

下一步仍需观察稳定身份/原内容及实际写入回执、Verification/消息分项复用，再沿既有 message_recover 开放部分投影后继。A2-2 图终态到资产空窗、A2-3 projected→finish、A3 Memory/清理、跨 Turn、统一 Prompt/Skills/Memory、流式/取消、受控 AgentCase、逐模式 Harness 与真实兼容发布均未完成。本轮未跑全量、前端、真实付费供应商/对象存储/领域 Runner、官方 saver conformance、双制品滚动/回滚；生产默认关闭，完整目标继续 active。

## 50. A2-1 观察原资产、Memory 行回执及部分批次重入（2026-10-26）

基线 `5b8835d47`。续接时工作区包含本分项未提交实现；先读取目标附件、规则与实际 diff，继续轮询确认仍存活的 session 32048，未因摘要或等待时间重启测试。该进程最终 **81 passed，227.27 秒**。此前问答回合仅澄清确认事项，不算开发进度；本次继续原开发目标，不重选框架、不升级依赖、不切生产。

### 根因、依据与实现

既有观察 builder 每次分配新 UUID，Memory 同 ID 又使用 UPSERT；故仅固定事件或工具结果不能保证收尾重入。续接前真实红测 **1 failed，6.87 秒**：最终事务前失败后再次 finalizer，原两行变四行。本分项冻结原观察与原请求，使用实际行同事务回执，而非再生成一次 UUID 或加异常重试。

本次再次读取官方 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)，及本地 Agent 轨迹课程幂等/补偿段落。应用回执复用现有 owner/stage、Memory、事件与最终 Session 事务，不把 checkpoint/store 或节点恢复误称跨存储事务。

- 新增 `FrameworkObservationAsset` / `FrameworkObservationCommits`：原 ObservationRecord 与 MemoryWriteRequest 成对冻结，原资产摘要绑定原 Runtime 结果；准备 builder/mapper 只执行一次。空清单可保存，不伪造 observations.persisted。
- 新增 `framework_observation_projection.py`：用原 stage JSONB 保存意图与实际行摘要；行和回执同连接事务。框架写入 `ON CONFLICT DO NOTHING` 并验证实际插入，裸行或检查后碰撞不收录；合法重复只读复用，不覆盖内容/时间。摘要包含 embedding、metadata、mode_key、created_at、updated_at 等实际字段。
- MemoryRuntime 预读已提交项，只为缺失项 embedding/写入，部分失败只补缺项并保留原顺序。完整命中不重新 embedding/发布 Redis；普通 Memory UPSERT 和非框架调用保持原契约。Protocol 与 PostgreSQL 实现、Session 调用方同步。
- observations.persisted 按原 Runtime 资产派生身份和时间，加入原事件事务回执；发布前和最终 Session 事务内复核全部行与原 ID/count。事务期间所有权失效拒绝写入。日志只记录身份、计数和错误类型，不写正文/凭据。
- A1 ACK-only 不增加“当前 Memory 行仍存在”的前置条件；新增用例在最终业务提交后删除派生 Memory 行，真实执行后继仍只补 ACK，不补造行/消息/事件，不执行图。最终事务前的行漂移仍要求对账，维护/backfill 的协调尚未闭合。
- message-stage 新写入 **10**，读者支持 1–10，Worker 能力上限同步。claim 前覆盖 reader 9/stage 10、reader 10/未来 stage 11；这不是旧镜像双制品演练，兼容读者仍须先行或隔离消费者。

中间一次真实失败 **1 failed，7.03 秒** 来自 Python datetime 经 JSON 字符串化后与数据库实际 datetime 比较，导致合法首写被拒；改为 Python 值再走统一 `_hash`，保留字段名诊断但不打印值。修复后单项 **1 passed，6.45 秒**；后续联合回归见下，不将这些中间结果累计为最终覆盖。

### 本次实际验证

以下均在 Agent_Server，使用独立 `.venv-framework-v2` 解释器；数据库组显式设置 `$env:RUN_LIVE_POSTGRES_TESTS='1'` 和 UTF-8 输出。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_observation_projection.py tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py -q -x -rs --tb=short
```

续接的最新联合进程 **81 passed，227.27 秒，无 skip**。该进程已包含并发首次写入、裸行/错误意图/检查后碰撞及 legacy UPSERT 用例，但早于下面新补的空清单和 A1 scope 两项。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_observation_projection.py -q -x -rs --tb=short
```

最终观察文件 **29 passed，82.86 秒，无 skip**，包括最后新增的空清单和 Memory 行删除后实际 ACK-only 后继。覆盖原内容/metadata/创建与更新时间/embedding/缺行漂移、准备及行回执回滚、并发首次/重复写入、批次失败、缓存命中不重 embedding/Redis、owner 失效、原资产/回执/格式错误、最终事务前漂移及实际进程强杀。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_agent_task_dispatch.py tests/test_session_store_value_contract.py -q -x -rs --tb=short
```

**134 passed，170.88 秒，无 skip**；共享阶段/恢复/调度及普通 PostgreSQL/InMemory 值契约通过。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_memory_embedding_runtime.py tests/test_postgres_vector_memory_schema.py tests/test_memory_vector_replica.py tests/test_memory_vector_retrieval.py tests/test_live_compaction_ownership.py tests/test_compaction_record_store.py tests/test_context_compaction.py -q -x -rs --tb=short
```

**61 passed，67.45 秒，无 skip**；embedding/Redis 为受控替身，所有权组使用真实 PostgreSQL/Redis，不代表付费供应商验收。以上组之间有重叠，不累计为独立覆盖数。

另一次扩展命令还选择了 `tests/test_live_memory_vector_retrieval.py`，结果 **1 failed、14 passed，6.63 秒**；真实 embedding 端点返回 **HTTP 402，account balance is insufficient**。本次未预先识别该文件会发出付费供应商请求，后续已停止该外部测试、不重试、不修改其 gate 或伪造通过；上面 61 项是明确不包含该供应商用例的独立回归，不能抹去此次失败。供应商验收仍未通过，后续实际调用需先确认环境与额度。

项目根 `python -m compileall -q Agent_Server/src Agent_Server/tests` 和 `git diff --check` 实际通过；独立环境无 ruff/mypy，PATH 也无命令，未安装，不声称 lint/typecheck 通过。一次串接命令因末尾查询不存在的工具返回 exit 1；随后分别检查编译/差异退出码均为 0，未把查询失败当作编译失败或隐去。

### 证据等级、限制与下一步

独立 Worker 使用实际 Deep Agents/SDK、本地合成 HTTP、真实 PostgreSQL/Redis；工具结果在图返回后注入为受控投影输入，不冒充真实领域工具执行。第一条观察行/回执事务提交后、MemoryRuntime 收到返回值前强杀，原资产/行/回执/根预算保留且 HTTP 一次。仍无最终应用凭证或 completed 事件、失租后仍拒绝自动后继；该负断言不是完整补偿成功。

完整命中不重 embedding/Redis，不等于并发首写前的物理请求 exactly-once；embedding 已发送但行未提交、Redis 副本失败修复、维护/backfill 协调继续归 A3。新增观察回执不改变 A1 scope，不证明全部 Memory/资源效果已完成。

下一步继续 Verification/消息分项与既有 message_recover 准入。A2-2 图终态→资产空窗、A2-3 projected→finish、A3、完整跨 Turn、统一 Prompt/Skills/Memory、真实流式/取消、受控 AgentCase、每模式 Harness、官方 saver conformance、真实对象存储/领域 Runner 和双制品部署仍未完成。未跑全量或前端；付费 embedding 失败已单列，不称真实供应商通过。生产默认开关不变，整体目标继续 active。

## 51. A2-1：消息与 Verification 必须符合冻结原意图

基线 `2b997d012`。真实 PostgreSQL 红测先在原 assistant ID 写入不同正文，再执行唯一 finalizer：旧实现保留错误行并签发 A1 凭证，结果 **1 failed，6.94 秒（DID NOT RAISE FrameworkMessageStageRecoveryRequired）**。根因是 rebase/UPSERT 后只对实际行取摘要，没有证明它对应冻结 Runtime 结果。

将 assistant 构造集中到 TranscriptHygieneService，同一个确定性投影供正常 finalizer 和事务校验使用。Verification 同 ID 精确一致才复用，冲突/重复拒绝，缺失追加；其他 Turn 结果与偏好保留。最终 Session 事务写前校验候选、已有消息和原 user，冻结消息只 INSERT OR REUSE，写后再次读取实际消息/Verification/user，再签发原 A1 凭证。无新表、JSON 字段或持久格式，legacy 消息编辑不变。错误日志仅记录身份及异常类型。

调用链另核对了 `_to_chat_messages`：框架工具身份稳定但每次重新分配 created_at。等待审批及审批继续没有本次 completed message 的原资产化 finalizer，不进入新增检查；本次未通过放宽原资产时间校验处理该静态风险，也不声称工具展示时间已跨审批稳定。

### 实际验证

均在 Agent_Server、独立 `.venv-framework-v2`，数据库组设置 `RUN_LIVE_POSTGRES_TESTS=1`、`PYTHONIOENCODING=utf-8`；续接原进程 handle，未因等待超时重启：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_finalization_projection.py tests/test_framework_event_projection.py tests/test_framework_snapshot_projection.py tests/test_framework_observation_projection.py -q -x -rs --tb=short
# 108 passed，293.54 秒，无 skip
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_agent_task_dispatch.py tests/test_session_store_value_contract.py tests/test_framework_approval_projection.py -q -x -rs --tb=short
# 149 passed，211.23 秒，无 skip
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py tests/test_case_execution_verification.py -q -x -rs --tb=short
# 74 passed，163.53 秒，无 skip
```

组间存在 fixture/范围关联，不将数字累计为全系统覆盖。一次早期四文件运行 **1 failed、24 passed，64.91 秒**，原因是测试把 SessionRecord dataclass 当作 Pydantic 调用 model_copy；读定义后改用 deepcopy，未修改生产实现迎合测试。另组 **31 passed、6 skipped，9.41 秒**；skip 是 test_case_operation_verification 的 opt-in LIVE fixture，要求隔离 PostgreSQL/MySQL/RustFS，不能称该领域真实集成通过。

独立 Worker 在最终 Session 事务前及事务内提交前强杀，实际 Deep Agents/SDK、本地合成 HTTP、真实 PostgreSQL/Redis。工具消息及 not_run Verification 是图返回后注入的受控投影样本，**不是领域 Runner 执行/通过验收**。两窗口保留原资产及已提交分项，没有发布未提交答案/Verification/turn.completed；本阶段仍不开放自动后继。

未跑全量、前端、独立 lint/typecheck、真实供应商/对象存储/领域 Runner、官方 saver conformance 或双制品部署；本阶段未发付费请求。A2-2、A2-3、A3、完整跨 Turn、统一上下文、流式/取消和逐模式迁移仍未完成。下一步沿既有 message_recover 授予仅补缺失应用投影的权限，不重新 invoke 图、模型或业务工具。

## 52. A2-1：有证明的部分投影后继与唯一收尾（2026-10-27）

基线 `f77d646d6`。原资产和各分项虽已落库，最终事务前失租仍无后继；真实红测 **1 failed，10.59 秒（successor is None）**。本次只沿原 message_recover、owner、stage 和 outbox 接入原结果收尾，不新增调度器或公开恢复 API。完整目标仍依第 12 节逐项实施，不缩为当前子步。

### 实现与根因验证

- 新 `finalizing` 恢复证明绑定原输入、根预算、谱系、原结果/来源、模型/ToolJob。准入、claim、应用投影 owner 接管、资产读取及提交前分别复核；只补缺项，已存在实际行必须与原意图/原回执一致。未知模型、running/cancelled ToolJob、裸行、缺行、漂移、取消及后续已执行 Turn 拒绝自动恢复。
- 收尾继续调用唯一 `_finalize_runtime_result`，不运行图、模型、语义分类或 Verification builder。使用原消息/快照/事件/验证/观察身份和时间；部分观察只补缺失行，已完成原 task/attempt 不重置。Session rebase 保留后续排队消息及无关偏好。
- 初版后继遇到 A1 当前 owner 检查，真实 **1 failed，8.40 秒**。修复为验证后原子接管应用投影 owner，原资产保留原 owner，不放宽写入权检查；单项随后 **1 passed，6.72 秒**。恢复证明使用不变的原资产摘要，不 hash 会追加回执的 manifest。
- A1 已提交继续 ACK-only；即使由 finalizing 后继提交，也不要求当前派生 Memory 仍存在。新 stage 格式 **11** 与能力上限同步；旧 1–10 可读，reader10 拒领11，reader11拒领未来12，不据此宣称旧制品部署兼容。
- 空白输入真实红测 **1 failed，7.35 秒（Admitted message differs from the original input）**：受理正文已 `.strip()`，资产保留 raw 请求。校验恢复既有规范化契约，正常/恢复各有用例，不改原输入资产或忽略其他字段。
- 补完整 Dispatcher 红测，claim 后实际 Verification 漂移被误记 failed：**1 failed，30 deselected，7.72 秒**。根因是任务结算丢失已定义的对账异常语义；只对 bound `FrameworkMessageStageRecoveryRequired` 结算为 recovery_required，未知程序错误仍 failed。双用例 **2 passed，30 deselected，11.34 秒**；重复投递不重跑、不派新后继。自动失租后继仍要求 error_code=execution_owner_expired，不能据 recovery_required 泛化为可重试。

### 已取回的实际测试结果

以下从 Agent_Server 使用独立 `.venv-framework-v2`；数据库组显式设置 `RUN_LIVE_POSTGRES_TESTS=1` 和 `PYTHONIOENCODING=utf-8`。续接先轮询原 handle61690/73984，均已终态，没有重启或猜测结果。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py tests/test_framework_finalization_projection.py tests/test_framework_snapshot_projection.py tests/test_framework_event_projection.py tests/test_framework_observation_projection.py -q -x -rs --tb=short
# 191 passed，633.50 秒；早于空白输入最终修正与新 Dispatcher 用例
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_finalization_projection.py -q -x -rs --tb=short
# 61 passed，193.56 秒；包含空白输入修正，早于新 Dispatcher 用例
```

上轮执行记录另有共享恢复/调度/诊断/审批/值契约 **166 passed，286.13 秒**，图前/图输入/fencing/native interrupt **87 passed，244.59 秒**。本次未重新取得这两组原 handle 输出，不凭摘要重建其确切文件列表或命令；最终源码回归另行记录，不以早期记录替代最终改动验证。上述范围有重叠，不累计为独立覆盖数。

最终源码（包含空白输入及 Dispatcher 异常分类）：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_finalization_projection.py tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_agent_task_dispatch.py tests/test_framework_approval_projection.py -q -x -rs --tb=short
# 137 passed，425.72 秒，无 skip
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_runtime_result_asset.py tests/test_framework_application_projection.py tests/test_framework_snapshot_projection.py tests/test_framework_event_projection.py tests/test_framework_observation_projection.py -q -x -rs --tb=short
# 132 passed，418.00 秒，无 skip
```

项目根 `./.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check` 实际通过。两份文档全部本地链接（v3 共29处、进度2处）与代码围栏检查通过。独立环境 `find_spec` 确认 ruff/mypy 均未安装，未新增工具，不声称 lint/typecheck 通过。再次读取官方 Persistence/Interrupts，保留 checkpoint/store、业务事务与恢复权限的区别；没有因在线新流接口示例更改锁定版本。

### 中间失败与证据等级

第一轮共享组 **1 failed、10 passed，41.43 秒**：独立 Worker 的 before_start 用例 run_once 返回0而非1；单独复跑 **1 passed，14.12 秒**，随后完整组166通过。未查明根因，未改生产队列迎合测试，也不声称已修复队列问题。

第一轮七文件 **1 failed、52 passed，215.49 秒**：rollback 旧断言仍期待无后继，已按新恢复契约改为验证完成。扩展图前组 **1 failed、19 passed，77.01 秒**：旧断言将追加回执后的 manifest 与原输入整体比较；改为精确比较 `runtime_result_source(saved)`、原资产 source hash 及已签 A1，不放宽冻结输入。随后87项通过。

六个独立 Worker 强杀窗口先单独验证 **6 passed、28 deselected，98.66 秒**：原资产后、快照后、快照事件后、最终事务前、最终事务内提交前、第一条观察行/回执后。实际 Deep Agents/SDK、本地合成 HTTP、PostgreSQL/Redis；确认原进程死亡后，沿既有 Redis 资源租约过期/reap，再启动独立替代进程。替代进程禁止图/模型/分类/验证再执行；重复投递完成、原身份/结果不变、HTTP仍一次、缺失观察补齐。工具/观察/Verification 为受控投影样本，不冒称领域 Runner 或真实付费供应商验收。

A2-2 图终态→资产空窗、A2-3 projected→任务finish、A3 Memory/清理/未知外部效果、完整跨 Turn、统一上下文、流式取消、AgentCase、逐模式 Harness、官方 saver conformance 与双制品部署仍独立验收。生产默认关闭，无依赖升级或新增付费请求；全量、前端、独立 lint/typecheck、真实对象存储/供应商/领域 Runner 未验收，整体 goal 继续 active。

## 53. A2-2：原生终态只读证据与图返回后强杀审计（2026-10-27）

基线 `9fc8daefe`，续接核对工作区干净及完整目标附件。上一轮已有实际代码、回归和提交，属于 progress；本轮继续完整第 12.1 节 A2-2，而非把原资产后继作为全部故障恢复完成。

### 依据与实现

核对现有 native_interrupts、FencedPostgresSaver、Runtime、Session 的调用链，以及安装版 Pregel.aget_state、ToolCallLimitState、UntrackedValue 源码；在线读取官方 [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers) 中 StateSnapshot、DeltaChannel、精确 checkpoint 和 pending writes 说明。不按最新版示例升级依赖，不再为业务调度另加图或队列。

- `native_terminal.py` 提供只读原生证据：绑定 fingerprint/thread/逻辑根 namespace/精确 checkpoint，验证 loop 来源、无 next/tasks/interrupts、精确原件无 pending writes、完整原生工具配对。按真实官方 codec 对 checkpoint 与物化 state 取摘要；模型/ToolJob/应用事务证明仍各自独立。NativeTerminalRead 的 values 为普通内存映射，不能因 dataclass frozen 就声称深层不可变，后续消费仍须按证明复核原件。
- bound adapter 完成时核对原生持久输出并附 metadata.native_terminal；等待审批不签证明、旧未绑定试点不变。异常保留 FrameworkMessageStageRecoveryRequired，让 Dispatcher 维持对账分类。没有新任务类型、schema/DDL、生产开关或恢复准入；message-stage 仍为11。
- 运行计数的官方 UntrackedValue 不进 checkpoint；证明记录实际 graph schema 的 uncheckpointed_channels，而非硬编码计数名或悄悄丢失字段。只有这些明确非持久通道不与恢复 state 比较；原生消息、其他持久字段和未知输出仍必须一致。它不声称已恢复全部回合预算/限额，根账本另验。
- 工具协议按每个 AI source 的未决调用批次核对，拒绝缺失/重复/orphan/invalid 调用，允许不同闭合批次复用 provider call ID。真实逐项审批先拒绝A、仍等待B、再批准B后才产生原生终态，受控效果只执行B。

### 真实强杀样本及材料审计

新增 `test_framework_terminal_gap.py`，延用原独立 Worker Helper，断点位于实际 adapter 完成之后、Runtime 尚未应用输出/创建 snapshot 之前。实际 Deep Agents/官方 OpenAI SDK、本地合成 HTTP、PostgreSQL/Redis；模型请求/响应通过 FrameworkModelEvidence 保存到跨进程本地受控对象后端，账本有 request 和唯一 settled_response_id。该后端明确不是实际 RustFS，不能算对象存储验收。

独立进程终止且 exitcode 非0后，重新读取原 checkpoint、原对象和预算；HTTP一次、无应用 snapshot/Runtime结果资产/turn.completed，失租任务保持 recovery_required，不创建无原结果的后继。测试 parent 没有伪造 execution owner，也没有调用图或模型；读取官方 DeltaChannel 时仅在 Harness 使用已核对安装版源码的内部 checkpointer config override，按物理 namespace 只读。原生 checkpoint 和完整 state 摘要与 Worker 捕获证据相同。

| 必需材料 | 当前证据与后续动作 |
| --- | --- |
| 原生完整消息、checkpoint/pending writes | 可经官方通道物化并取得精确原生证据；不能直接索引原 checkpoint 的 messages |
| 原模型请求与被结算的唯一响应 | 本地受控对象后端/真实PG账本强杀后仍可读；真实RustFS/供应商另验，不据此自动导入 |
| 初始应用状态/实际投影输入 | graph_input_asset 仍主要是 adapter 输入；所需 Runtime 状态、原模型展示配置等尚未统一冻结 |
| 完整事件清单、子代理 callback | live sink 能保存前缀，但队满后的后缀仅在 Runtime event_log；callback 元数据也未证明能从原生子图等价重建 |
| assistant/snapshot/工具展示身份与时间 | Session 的 assistant UUID 在调用前分配；snapshot UUID/时间、工具展示时间随后生成，当前未全部进入可恢复准备资产 |
| 运行计数 | 官方 UntrackedValue 明确不持久化；不能把原生终态当成全部根预算/运行限额证明 |

因此 A2-2 **未完成**。下一步在首次产生处冻结必要材料、以精确原生终态与原账本作为输入，使正常/恢复复用同一个确定性投影器。保留唯一 finalizer，不另写靠最后文本猜输出的恢复分支，也不通过放开 ensure 假装空窗已闭合。

### TDD 与中间失败

先让真实官方图和PG跑到终态，再导入新读取端口，得到 **1 failed，7.34秒，ModuleNotFoundError**；实现后能够读取精确原生证据。adapter 接线前 **1 failed，6.68秒，KeyError: native_terminal**；异常保留前 **1 failed，6.39秒**，原对账异常被包装为 DeepAgentRuntimeError；均补实现后继续回归。

强杀审计第一次 **1 failed，11.98秒，KeyError: messages**，暴露测试把 DeltaChannel 当直接 channel_values 的错误假设。改为官方通道物化，不修改生产 checkpoint；随后 **1 passed，13.24秒**。

首轮扩展组分别 **1 failed、97 passed，69.99秒** 和 **1 failed、15 passed，50.40秒**；合法审批完成/认知子代理的 run_tool_call_count 是 UntrackedValue，不能要求它出现在恢复 state。按官方通道 schema 区分并显式记录，目标三项 **3 passed，12.54秒**。随后新用例误以为每个最小 Harness 都配置工具限额，**1 failed、16 passed，35.96秒**；改为显式安装官方 ToolCallLimitMiddleware 的独立正例，未为迎合断言修改生产配置。早期一次用例访问 task.owner_token 而非 task.runtime_owner.owner_token 的 AttributeError 亦已修正，不计为生产缺陷。

最终源码回归结果在完成后记录。全量、前端、独立 lint/typecheck、真实供应商/RustFS/领域 Runner、官方 saver conformance、双制品部署未验收；无新增付费请求、依赖或生产路由。A2-3、A3、统一上下文、完整跨 Turn、流式/取消、AgentCase、逐模式 Harness 等全目标继续 active。

## 54. v4 P0：修正并行任务的 Delta 回放顺序，保留原生终态严格比较（2026-10-27）

续接基线 `52e858cbf`（v4 文档）／`9fc8daefe`（§52 代码）。重新阅读 v4、§52–53、当前工作区、固定版本源码及相关 Harness；上一轮提示词调整没有实施进展，本轮实际恢复开发。检查时 Docker／PostgreSQL／Redis 已在运行，本轮没有启动、停止或重启容器。现有 fixture 用 UUID 专用表、checkpoint schema 和 Redis 前缀隔离资源，清理只涉及 fixture 自建数据。

### 真实红测、根因与依据

1. 原独立 Worker 失败样本重新运行：`test_separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling[reconcile_after_commit]`，**1 failed，31.53 秒**。A 的拒绝任务被结算为 `recovery_required`。诊断为 `messages.count:5:5`，第 2、3 条的 content/id/status/tool_call_id 不同；不是缺少消息，而是两个工具结果的顺序反转。
2. 安装版 LangGraph 1.2.12 的 `pregel/_algo.py::apply_writes` 按 `task_path_str(task.path[:3])` 合并并行写；Postgres 3.1.2 的 `_build_delta_channels_writes_history` 按 `(task_id, idx)` 排序。任务 ID 是路径的散列，不能代替任务路径顺序。Deep Agents 的消息 reducer 保持写入顺序，故返回结果与恢复结果发生真实漂移；不能在比较器中将两者判成等价。
3. 官方 [issue #8382](https://github.com/langchain-ai/langgraph/issues/8382)／[修复 PR #8544](https://github.com/langchain-ai/langgraph/pull/8544)确认相同原因。采用其按 `(task_path, task_id, idx)` 读取的规则；不升级锁定依赖、不修改 site-packages、不在应用自行拼接 Delta。官方 [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)及安装版 `BaseCheckpointSaver.get_delta_channel_history` 提供沿 parent_config、读取种子和原生 pending writes 的公共默认实现。
4. 补充确定性回归，让 task ID 与路径故意反序；修复前 **1 failed，9.62 秒**，实际返回 `[['second'], ['first']]` 而非 `[['first'], ['second']]`。这不是导入失败或测试脚手架异常。

### 实现与边界

- `path_ordered_postgres_saver.py` 只扩展固定版官方 SELECT 的读取行形状，带出数据库已有的 task_path／idx，在 Python 按上游规则排序后交回官方反序列化。避免依赖数据库 collation，索引按数字而非字符串排序。单测显式核对固定 SELECT 行形状；升级时必须同步重验，不能无痕继续套用。
- Delta 历史使用官方 BaseCheckpointSaver 默认祖先遍历，不再走固定版本中独立按 ID 排序的优化查询。官方 reducer、分支祖先、逐通道 seed、目标 checkpoint 的 pending-write 排除语义继续复用，没有第二套 Delta 算法。
- ThreadedPostgresSaver 和 FencedPostgresSaver 的 get_tuple/list/history 共用该读取规则；异步仍经线程桥接。写入、owner 锁、同连接事务、审批 receipt/proof 全部保留原实现。provider 就绪日志明确记录排序规则与历史读取器。
- §53 的 native_terminal／adapter／强杀 Harness 草稿经审阅和本轮回归纳入本子步；等待审批不签终态证明，异常保留对账类型。消息顺序、身份、内容块、签名字段、response_metadata 和 usage_metadata 均未豁免。新增不透明签名字段的合成正／负例不等于真实供应商签名验收。
- 保留 UntrackedValue 的显式非持久通道范围；根预算另验。终态证明仍为 `native_graph_terminal_only`，不创建后继、不调用图／模型／工具、不自动重建应用结果。message-stage 仍为 11，无 DDL、依赖、公开审批 API 或生产开关变更。
- 兼容代价：默认祖先遍历比官方优化 SQL 多查询；性能基线及官方 conformance 完整验收仍待完成。后续获准升级到修正版依赖时，须通过相同回放／恢复测试后移除此兼容适配。它不声称能自动纠正旧执行已经固化到后续快照中的错误顺序，也不替存量绑定迁移与双制品演练。

### 实际验证

以下工作目录为 `G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server`，解释器为独立 `.venv-framework-v2`，数据库组设置 `RUN_LIVE_POSTGRES_TESTS=1`、`PYTHONIOENCODING=utf-8`。输出依据本轮实际终端会话，不累计重叠范围，不把历史绿色结果充作当前验收。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_delta_replay_order.py 'tests/test_framework_approval_recovery.py::test_separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling[reconcile_after_commit]' -q -x -rs --tb=short
# 初次修复后：6 passed，43.75 秒；早于后续快照／list／metadata 扩展。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_delta_replay_order.py tests/test_framework_native_terminal.py tests/test_framework_terminal_gap.py tests/test_deep_agent_runtime_adapter.py tests/test_framework_native_interrupts.py tests/test_framework_checkpoint_fencing.py -q -x -rs --tb=short
# 126 passed，181.14 秒，无 skip；包含反序审批与五类消息漂移负例。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_delta_replay_order.py -q -x -rs --tb=short
# 最终扩展源码：7 passed，21.66 秒，无 skip；补齐 list、快照 seed 和旁支排除。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_receipts.py tests/test_framework_approval_runtime.py tests/test_approval_checkpoint_consistency.py -q -x -rs --tb=short
# 43 passed，172.04 秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_model_policy.py tests/test_framework_approval_recovery.py tests/test_framework_application_projection.py tests/test_framework_partial_projection_recovery.py -q -x -rs --tb=short
# 106 passed，515.72 秒，无 skip；包含实际摘要／子代理策略、独立 Worker 审批强杀、A1 及 A2-1 投影恢复。
```

第一组终端会话 16860、确定性最终组 3012、receipt／Runtime 组 57011、完整审批恢复／应用投影组 96371 均取得真实退出码 0；本轮所有实际启动的 pytest 已取回终态，无遗留运行中的测试。原失败会话 68367 和确定性红测 51664 的失败不删除，分别保留上述真实红测记录。

根目录实际执行 `./.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check`，均通过。`find_spec` 确认 ruff、mypy、官方 conformance 包未安装；未新增工具，不声称 lint/typecheck/conformance 通过。全量、前端、真实供应商／RustFS／领域 Runner、部署构建与 E4 未执行；测试最高证据范围为 E2，真实强杀链路使用本地合成 HTTP／受控工具和本地对象后端。

### v4 对应与剩余出口

| v4 阶段／条款 | 记录与代码／测试 | 本轮状态／剩余出口 |
| --- | --- | --- |
| P0，§8.3，H02/H03/H11 | §53–54；native_terminal、path_ordered_postgres_saver、adapter 与严格消息／审批恢复测试 | 原失败已复现并根因修正；反序审批和字段漂移保留严格验证，不把单个绿色用例代表完整审批验收 |
| A1／A2-1，§9.4 | §52 原提交；本轮应用提交／部分投影回归 | 复用原唯一 finalizer，不扩张 ACK-only 或结果恢复权限 |
| A2-2，§8–9，H05 | §53 材料审计与 terminal_gap 强杀 Harness | 只读原生材料可验证；无原结果资产的自动恢复仍未开放，下一步前移应用输入／身份／事件材料并构造同一确定性投影器 |
| H12，§14 | Delta 专用回归、真实 PG、连接重建／命名空间／旁支测试 | 仅局部验证；官方 conformance、长链性能、备份恢复、旧记录与双制品仍待验收 |
| A2-3／A3／B/C/D/E/F | v4 §15 完整目标 | 待实现／按依赖逐项验收，整体 goal 保持 active |

本节与审阅后的 §53 相关代码／测试归入同一中文提交，提交主题为 `【fix】按任务路径恢复原生终态消息顺序`，具体 ID 以本节 Git 历史和交付输出为准。原有 v3 文档草稿不纳入本次提交，不覆盖或丢弃。

## 55. A2-2：图调用前保存投影身份与初始应用状态（2026-10-27）

基线 `449746190`。上一轮仅调整开发提示词，没有实现进展；本轮恢复代码开发，先核对 §52–54、v4 以及六个代码／测试草稿。Docker 与测试依赖已有运行环境，本轮没有启动、停止或重启容器；数据库／Redis 测试仍由已有 UUID fixture 隔离。原 v3 文档草稿保留，不归入本次提交。

### 根因、依据与变更范围

- Runtime 冻结的 context 原先来自应用 context_bundle，真正传给 adapter 的却是 request.context 加 skill_keys；二者不是同一个输入。改为只构建一次 DeepAgentRuntimeRequest，现有冻结字段从该对象读取，随后执行同一对象。测试在实际 adapter 入口精确比较 context、原生消息、Prompt 和 namespace，并验证后续嵌套修改不污染已冻结 JSON；持久化回调失败时不得启动 adapter。
- 图终态前没有保存应用初始状态，也没有保存 Session 预分配的 assistant ID；snapshot 身份与时间在图返回后才分配。现在由 bound message 的 Session 在调用前预留 assistant／snapshot ID、snapshot version 意图及准备时间，Runtime 将初始状态和绑定一同保存为 graph_input_asset v2。正常完成、等待审批、超时和取消的初次 Runtime snapshot 沿用本次预留值。时间表示准备时刻，不能冒称图完成时刻；后续审批执行仍走其原有独立投影契约，不据此宣称工具展示时间已稳定。
- 扩展现有输入资产与阶段日志，不新增存储表、调度器、结果资产种类或第二套 finalizer。v1 与 v2 共用字段／摘要验证，分别保持严格版本及字段契约，统一 parser 选择读者；JSON 往返切断调用方嵌套引用，读取时仍重新校验摘要，不声称 Pydantic frozen 可以使所有嵌套容器不可变。
- `FrameworkRuntimeResults.freeze` 原先将阶段硬写回7，会抹去12格式的能力要求；改为保留已有较高版本。新资产冻结与既有资产加载共用准备身份校验，核对原 binding、assistant ID、snapshot ID／version／created_at。仅校验原投影意图，不混淆数据库实际分配的行头；原 snapshot／event／observation 回执验证继续保留。旧 v1／无准备资产的结果仍按原契约处理。
- 新准备输入要求 message-stage12；Worker 上限同步12。reader11拒12、reader12拒13均在 claim 前判定，原1–11读取契约保留；这只是代码／测试兼容证据，不能代替旧／新真实制品并存部署。新增日志只记绑定、owner、资产摘要和格式版本，不记录上下文正文或凭据。

依据为 v4 §9.2／§14／§16、现有 graph_input／Runtime／Session／结果资产及唯一收尾实现；核对固定版 SDK `langgraph/checkpoint/base/__init__.py` 的图状态／metadata 边界，并重新读取官方 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：图 checkpoint、跨线程 store 和本项目应用投影材料职责不相同。未据在线新版文档升级依赖。

本地参考 `G:\Code_Warehouse\DeepAgent-Studio\借鉴\codex\codex-rs\rollout\src\recorder.rs` 的 SessionMeta 初始身份／时间与 persist／flush 确认边界：借鉴“初始元数据与持久化确认分开”的职责，不移植其 JSONL、内存队列或重试器。本项目继续等待既有 owner 事务确认后再启动图；该参考不证明本项目事件已全部可靠落地。

### TDD 与中间失败

实际行为红测均在本轮取得终态：

1. `test_framework_terminal_gap.py`：官方图返回后、Runtime 尚未生成结果时强杀，输入应有准备材料，实际仍为1，**1 failed，21.92秒**。完成 Session 接线后才具备 v2；没有用原生 checkpoint 冒充应用状态。
2. `test_prepared_result_freeze_preserves_newer_stage_and_original_identity`：原 freeze 把阶段12写回7，新准备资产校验拒绝，**1 failed，7.27秒**。根因修复为保留阶段版本，而不是删除新格式要求。
3. `test_prepared_result_rejects_identity_drift[load-assistant]`：即使所有摘要均按漂移后的数据重算，原加载仍接受非原 assistant ID，**1 failed，8.40秒（DID NOT RAISE）**。新增冻结／加载共用校验，并覆盖 assistant、snapshot ID、version 和准备时间八项负例。

早期输入／adapter／终态组 **82 passed，33.00秒**，结果／快照／阶段组 **71 passed，311.51秒**，输入／终态／恢复／调度组 **61 passed，213.58秒**，审批／Delta／fencing组 **99 passed，429.33秒**。这些批次不累计为独立覆盖数，最终源码回归另列。

扩展回归中的旧真实 Worker 断言仍固定为阶段9／10，分别出现 **1 failed、39 passed，134.87秒** 与 **1 failed、146 passed，581.22秒**。只对实际写入新准备资产的链路改为精确校验12，并增加原预留身份比对；旧替身／无准备资产的7／9／10／11断言保留。一次修改缩进导致收集错误、一次观察测试误引用未定义 asset 导致 **1 failed、28 passed，124.70秒**，均为测试维护错误，不计为目标红测或生产修复；已纠正后重新验证。长测试始终续接原 handle，未因等待超时重启。

### 最终源码验证

以下命令在 `G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server` 使用独立 `.venv-framework-v2`，显式设置 `RUN_LIVE_POSTGRES_TESTS=1`、`PYTHONIOENCODING=utf-8`。v1／v2共用字段抽取、日志及测试维护修正后重新启动最终批次，均取得退出码0，无 skip，不累计为全系统覆盖数：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_deep_agent_runtime_adapter.py tests/test_framework_graph_input_asset.py tests/test_framework_terminal_gap.py tests/test_framework_runtime_result_asset.py tests/test_framework_snapshot_projection.py tests/test_framework_message_stages.py -q -x -rs --tb=short
# 153 passed，328.84秒。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_application_projection.py tests/test_framework_finalization_projection.py tests/test_framework_event_projection.py tests/test_framework_observation_projection.py -q -x -rs --tb=short
# 147 passed，568.13秒。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py tests/test_framework_approval_receipts.py tests/test_framework_approval_runtime.py tests/test_framework_delta_replay_order.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py -q -x -rs --tb=short
# 99 passed，419.92秒。
```

最终批次终端会话9231／79516／91125均取回真实终态；早期及失败批次也全部结束，无遗留运行中的测试。根目录 `./.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check` 实际通过；v4／实施记录的本地链接与代码围栏检查通过。全局检索新准备类型、parser、Runtime可选参数、阶段版本写入和资产读取端，核对 Session／Runtime／结果加载／能力读者同步。

### v4 对应与剩余出口

| v4 阶段／条款 | 本节代码与测试 | 已实现范围／剩余出口 |
| --- | --- | --- |
| A2-2，§9.2，H05/H11 | graph_input schema、Runtime／Session、terminal_gap 与输入资产测试 | 初始应用状态及 assistant／snapshot 投影意图前移；官方图、PG／Redis、独立 Worker 的准备资产持久性及正常身份复用验收；不授予无原结果后继 |
| A2-1，§9.4 | runtime_results 与原投影／部分恢复测试 | 新旧格式沿原资产与唯一 finalizer 收尾，准备身份冻结／读取一致，不重执图或模型工具 |
| 格式兼容，§14／§16，H11 | stage12、Worker capability 与 claim 前版本反例 | 保留旧格式读者并拒领未来格式；上线前仍须先部署兼容读者、制品归档及能力路由，双制品／回滚另验 |
| P0，§8.3，H02/H03/H12 | 原审批强杀、Delta、fencing 回归 | 维持 §54 的严格原生比较，不忽略消息顺序、身份、块或元数据；官方 conformance／长链性能另验 |

本步最高证据为 E2：真实官方 Deep Agents／SDK、PostgreSQL／Redis、独立 Worker 和强杀；模型为本地 HTTP，工具为受控样本，跨进程本地对象后端不是 RustFS。正常 Worker 测试还核对应用结果与准备身份一致、HTTP一次、turn.completed一次；图返回前后材料强杀测试确认没有 Runtime 结果时仍不创建自动恢复后继。

**A2-2仍未完成。** 必需事件溢出后缀／子代理 callback 事实、工具展示身份与时间、正常与恢复共用的完整确定性结果投影器及相应无原资产恢复准入仍待实施。A2-3／A3／B/C/D/E/F 保持 v4 完整范围，不用本准备子步代替阶段完成。生产默认关闭，无依赖升级、付费请求或真实目标测试；全量、前端、独立 lint/typecheck、真实供应商／RustFS／领域 Runner、官方 conformance 与 E4 未验收。实际 find_spec 确认 ruff、mypy、官方 conformance 包未安装，未新增工具。

本节代码／测试与记录归属同一提交，主题为 `【feat】前置冻结框架回合的投影身份与初始状态`，实际 commit ID 以本节 Git 历史和交付输出为准。整体目标继续 active；下一步沿现有事件设施补可靠事实，不新增平行恢复链路，也不提前开放无原资产自动恢复。

## 56. A2-2：必需事件的可等待持久化与子代理事实保存（2026-10-27）

基线 `bd9e6abeb`，续接 §52–55 与 v4；§54 已提交的 Delta task_path 顺序根因修复继续回归，不重新忽略 messages。上一轮仅交付开发提示词；本轮恢复代码实施。接续时两份强杀测试草稿保留并审阅，原 v3 文档草稿不修改、不纳入提交。测试依赖原已运行，本轮没有启动、停止或重启 Docker／服务；数据库与 Redis 使用既有 UUID fixture 隔离。

### 根因、依据与实现边界

- 旧同步事件接口只保证入有界队列。容量耗尽后后缀留在 Runtime.event_log，依赖最终结果刷盘；图返回后、结果冻结前强杀会永久丢失这部分正式事实。这是持久化确认边界缺失，不通过加大队列、补随机身份或新建事件日志修补。
- 沿现有 SessionEventSink 增加 owner-bound 可等待写入，Session 创建 sink 时捕获原 owner；同一 sink 串行确认事件。Runtime 的新框架启动、可观察性、子代理、治理工具、审批等待／继续、完成及停止路径等待既有 PostgreSQL append_event 事务。legacy 同步队列／溢出最终刷盘保留原契约；bound sink 不接受会丢确认的 put_nowait。独立 Runtime 无 Session sink 的测试不据此取得数据库持久化保证。
- SessionStore 协议、Postgres 实现及内存实现同步可选 runtime_owner 参数。沿已有 Session→owner 锁顺序与短事务复核 epoch／token／task／attempt／worker／租约及 Session／Turn 范围，保留原 turn/continuation fence。复用原事件表、序号、计数、outbox 和 Redis 热投影，不新增表、队列、调度器或 finalizer。内存 store 明确拒绝冒充 owner 事务。
- bound 写入复用原事件意图校验：重复 ID 仅在身份、类型、时间、正文和显式 correlation 一致时幂等；首次补充的投递上下文保留，不被新 delivery 覆盖。事务内校验失败回滚，不以 ON CONFLICT DO NOTHING 掩盖漂移。实时事实与结果投影分别使用自己的既有写入权限，不据实时事件授予恢复权。
- Deep Agents callback 同时兼容同步返回和 awaitable；Runtime callback 等待事件确认，并在既有审计 payload 保存四字段 subagent_event（type/name/run_id/parent_run_id），保留原展示字段及审计脱敏策略。adapter 在错误时关闭原生事件流并原样传播失租异常。to_thread 写入的 await 被取消不代表 SQL 已停止，sink 遵循已有重复取消收尾模式，等在途事务结束后才允许执行租约收尾。

依据：v4 §8.1／§9.2／§11／§16、现有 SessionEventSink／事件投影／owner 事务；核对固定 langchain-core 1.6.10 的 `runnables/base.py` 与 `tracers/event_stream.py` 中 v2 AsyncIterator、aclosing 和 consume task 关闭语义，未照在线新流接口升级依赖。官方 [Streaming](https://docs.langchain.com/oss/python/langchain/streaming) 与 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 用于核对流展示、图状态与应用数据职责，不把流事件自动等同应用可靠落库。

本地参考 `G:\Code_Warehouse\DeepAgent-Studio\借鉴\codex\codex-rs\rollout\src\recorder.rs` 的 persist／flush 等待 writer ACK：借鉴“入队不等于持久化确认”，不搬运其 JSONL、重试或第二日志系统。本地课程 `项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html` 的轨迹、重复执行、部分成功与异常恢复测试用于制定本步反例，不把课程示例指标当本项目门槛。

### TDD 与中间失败

1. 原会话61060取回 **1 failed，51.01秒**，失败为 SkillRegistry 缺失，尚未触达目标行为。前一续接已有只读能力 fixture 缺失；本轮补齐真实 SkillRegistry／受控项目根后又遇到 E1 读图 schema 与 E3 producer 不一致，以及测试漏传已有必需参数。这些均为 fixture 维护错误，不计目标红测；读者按 producer 的实际中间件 schema 重建，只读比较完整终态，没有删字段或弱化原生 messages 比较。
2. 有效强杀红测：真实官方图／SDK 返回后注入4对受控 callback，容量1，结果冻结前强杀，**1 failed，15.55秒**，明确失败为 `Emitted audit suffix disappeared before result freezing`。这是8条受控事件，不是8个真实子代理。修复后校验原9条事件的身份、顺序、时间及正文；仍断言无原结果资产不能建立自动恢复后继，模型请求仍一次，原 checkpoint／账本不变。
3. 新事件失败／流关闭反例 **1 failed，7.54秒**：callback 失租后原生迭代器没有在 adapter 返回前关闭。增加 aclosing，保留原失租异常实例，不只包装成通用运行失败。
4. 初次生产修复后 **1 failed、1 passed，29.01秒**，暴露测试直接比较 PG 无时区 UTC 与原带时区时间；改用既有严格 event-intent 规范比较，不忽略实际时间、正文或身份。扩展组又有 **1 failed、93 passed，90.08秒**：老故障注入 wrapper 不接受已存在的 framework_projection 关键字；同步 wrapper 的透传签名，仍在第二条事件处注入失败并验证事务回滚。

补充覆盖：容量1的逐项确认及并发 producer 顺序、四种同 ID 漂移、跨 Session／Turn、显式 turn 冲突、旧 token／epoch、过期与真实替代 owner、重复取消等待真实 to_thread SQL、事务失败回滚、内存伪持久化拒绝。另以真实官方具名子代理、假模型和 PostgreSQL saver 验证 started/completed 原四字段实际入库；这与强杀场景的受控 callback 注入分开登记。

### v4 对应与剩余出口

| v4 阶段／条款 | 本节代码与测试 | 已实现范围／仍不授予 |
| --- | --- | --- |
| A2-2，§9.2，H05/H11 | execution_logging、SessionEventSink、Runtime／adapter、live_event_persistence／terminal_gap | 应用已观察并产生的正式事件逐项确认；子代理 selected metadata 原件可靠保存；不证明 SDK 尚未送达的事件已经保存，不开放无原资产恢复 |
| §8.1，H07 | PostgresSessionStore、SessionStore、owner／重复取消反例 | owner 与 event intent 同事务验证；旧 sink 不能采用新上下文 owner；不是外部操作停止证明 |
| A2-1／P0，H02/H03/H05/H12 | 原审批／Delta／部分投影／finalizer 回归 | 沿唯一结果资产、分项回执和 finalizer 保持严格原意图比较，不重执模型或工具 |
| §11／§16，H10 | adapter 回调、流关闭及 Session SSE／assistant-stream 回归 | 保留同步 callback、legacy 溢出契约、旧事件展示字段；不声明实时 Token／取消／暂停的全量恢复已完成 |

本步最高证据仍为 E2：官方 Deep Agents／SDK、真实 PostgreSQL／Redis、独立 Worker 与强杀；模型为本地 HTTP／假模型，业务工具为受控样本，本地跨进程对象后端不是 RustFS。前端 ExecutionEvent.payload 已为 JsonObject，既有展示字段保留；检索 callback、停止结果构造、存储协议和事务调用消费方完成接线核对，本轮没有前端实现变更。

**A2-2仍未完成。** 完整事件清单／完成边界与终态绑定、工具展示身份及时间、正常与恢复共用的完整确定性结果投影器及无原资产恢复准入仍待实现／验收；本步不能将“已观察事件可靠写入”升格为“所有原生中断均可自动恢复”。A2-3／A3／B/C/D/E/F 保持完整范围，生产默认关闭，无依赖升级、新付费调用或真实目标测试。全量、前端、真实供应商／RustFS／领域 Runner、官方 conformance 与 E4 未验收；实际 find_spec 确认 ruff／mypy／官方 conformance 未安装，未安装新工具。

### 最终验证与提交归属

以下在 `G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server` 使用独立 `.venv-framework-v2`，显式设置 `RUN_LIVE_POSTGRES_TESTS=1`、`PYTHONIOENCODING=utf-8`。四组均在最后一次主源码改动后启动并取得退出码0，不累加为独立覆盖数：

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_live_event_persistence.py tests/test_framework_terminal_gap.py tests/test_deep_agent_runtime_adapter.py tests/test_session_event_delivery.py -q -x -rs --tb=short
# 118 passed，2 skipped，113.99秒。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_application_projection.py tests/test_framework_finalization_projection.py tests/test_framework_event_projection.py tests/test_framework_observation_projection.py -q -x -rs --tb=short
# 147 passed，588.72秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py tests/test_framework_approval_receipts.py tests/test_framework_approval_runtime.py tests/test_framework_delta_replay_order.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py -q -x -rs --tb=short
# 99 passed，429.60秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_graph_input_asset.py tests/test_framework_runtime_result_asset.py tests/test_framework_snapshot_projection.py tests/test_framework_message_stages.py tests/test_assistant_stream_persistence.py tests/test_session_execution_ownership.py -q -x -rs --tb=short
# 136 passed，1 skipped，1 warning，345.15秒。
```

三项 skip 均为混合 memory/postgres fixture 的 **memory 参数**：事务排序、跨进程 HTTP／SSE、Postgres＋Redis 同源投影；对应 postgres 参数均实际通过，并非环境缺失或必需集成项跳过。warning 为现有 Starlette TestClient 使用 anyio BlockingPortal 旧别名的弃用提示，本步未修改第三方或无关测试设施。

四组收集后又仅增强两处测试断言：live_event 增加并发 producer 的原顺序，terminal_gap 增加强杀后的数据库原顺序。对最终测试文件重新运行 `tests/test_framework_live_event_persistence.py`：**16 passed，54.23秒**；`tests/test_framework_terminal_gap.py`：**3 passed，42.55秒**。真实具名子代理持久化用例先前单独为 **1 passed，12.09秒**；这些不重复计入覆盖数。

最终会话49810／55796／17775／67776／79449／89634均取回终态；早期及失败批次也已结束，没有因观察超时重启测试。根目录 `./.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check` 实际通过；v4／实施记录的代码围栏和绝对本地链接检查通过。父仓库默认忽略 tests，沿本项目 `.gitignore` 白名单纳入新回归文件，不强制添加无关文件。

本节代码／测试与记录归属同一提交，主题为 `【fix】确认框架事件持久化后再继续执行`，实际 commit ID 以 Git 历史及交付输出为准。整体目标保持 active；下一步继续 A2-2 的完整材料／完成边界与确定性结果投影，不以本步绿色测试代替阶段出口。

## 57. A2-2：工具消息时间来源与逐项续跑槽位（2026-10-27）

基线 `2c70dfc81`，核对 v4、§52–56、Git 和实际相关实现后续接。上一轮仅调整开发提示词，没有代码实施进展。原 v3 文档未提交修改保留，不修改、不混入本节提交。测试环境已可用，本轮没有启动、停止或重启 Docker／服务，框架数据库／Redis 沿既有 UUID fixture 隔离；没有依赖、DDL、生产开关或公开 API 变更。

### 有效红测与根因

1. 将原 `test_runtime_replay_then_successor_preserves_results_and_transcript_ids` 从只比较 ID 扩为完整 ChatMessage 比较，并覆盖批准／拒绝及后续兄弟审批。真实官方 Deep Agents、PG saver、治理工具与续跑链取得 **1 failed，13 deselected，10.90秒**：同一工具消息 ID／正文相同，但重复续跑重新用 `datetime.utcnow()` 分配 created_at。不能以忽略时间字段恢复绿色。
2. 在原 `test_bound_bridge_loads_current_decision_without_batch_and_executes_original_job` 核对首次 record 与实际已落库 ToolJob 的时间，取得 **1 failed，13 deselected，11.22秒**：首次 started_at 为03:06:24.408551，Job 为03:06:24.423501。首次 record 使用本机派发时钟，而重放采用 Job；completed_at 也存在同样的独立时钟来源。
3. 调用链核对还发现 callback 将既有 call 删除后追加，会移动原待审批槽位，影响后续 ordinal。新绑定复用既有 `_merge_tool_records` 保留槽位；无绑定旧试点保持原顺序契约，不为让新路径通过而修改旧模式断言。

### 实现与事实边界

- `ToolRuntimeService` 直接使用本次结算返回的 Job 的 started_at／completed_at（旧终态缺 completed_at 时沿既有 updated_at 契约），不额外调用 `record_from_job` 重读／重加工 artifacts。覆盖正常、partial、failed、denied、缺 handler、异常、已观察输出交付失败；waiting_children 仍没有完成时间，不把等待伪装为终态。
- 将原应用工具消息渲染抽到既有 `tool_messages.py`，Runtime 启动／审批续跑共用该纯投影函数，没有新增结果资产、调度器或 finalizer。bound 消息保持原 binding/call UUID 分配规则；当前新 record 使用原操作完成时间，JSON／PG 的 UTC 表示按既有无时区业务时间规范化，不用恢复时钟。
- 已发布的同身份终态消息在完整正文及本投影负责的 metadata（含 ordinal）一致时保留其原时间，兼容旧 pending_turn；不反填一个新的“真实发生时间”。冲突、跨 Turn／调用／角色不接受。只检查本次实际投影涉及的身份，不因无关历史重复消息阻止空工具／合法路径。
- 治理在 Job 创建前拒绝是合法分支，不强制补造 Job／时间。无 record 时间时使用原 `tool.execution_completed/denied/failed` 事件的首次对应事实，核对 Turn、调用、工具、Job、状态及 harness；请求事件／其他调用不能供给完成时间。既无时间、原消息、也无相应事件时明确对账，不用“现在”补造。
- 新 E2 用例在工具已暴露后、真实治理前撤销权限，官方图返回真实无 Job 拒绝；容量1的 owner-bound sink 确认 PG 事件，再校验应用消息时间以及原 record／持久事件的只读再投影。无工具派发、无假 denial Job、无 snapshot／应用结果资产；这不是独立进程强杀的无资产恢复验收。

依据：v4 §9.2／§9.4／§16、现有 ToolJob 结算／record_from_job／Session 消息／事件设施。重新核对固定 LangGraph 1.2.12 `types.py::StateSnapshot` 的图状态与 snapshot 时间语义，以及官方 [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：图 checkpoint 时间不冒充应用工具时间，恢复节点可能重入也不授权重执副作用。未采用在线新 stream API 或升级依赖。

本地参考 `G:\Code_Warehouse\DeepAgent-Studio\借鉴\codex\codex-rs\rollout\src\recorder.rs` 的 persist／flush ACK：只借鉴“原事实确认先于继续”，本步继续消费 §56 的既有事件事务，不搬 JSONL／队列。课程 `项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html` 的结果＋轨迹评估用于覆盖重复续跑与拒绝轨迹，不将课程示例当本项目已通过的验收。

### 中间回归与环境问题

- 目标最小组 **4 passed，26 deselected，20.32秒**；工具消息单测扩展后 **26 passed，3.70秒**；首个运行时／桥接／消息组 **49 passed，86.16秒**。不累计重叠范围，也不以早期批次代替最终源码验收。
- 新测试首次从仓库根目录执行出现 `ModuleNotFoundError: src`；改回既有 Agent_Server 工作目录，并修正测试误把实例方法作静态方法调用。属于测试脚手架维护，不算目标红测。
- 首次 adapter 扩展组 **1 failed、91 passed，98.73秒**：`batch_mixed` 是无绑定旧试点，原断言要求最新完成记录在尾部。不能修改该断言掩盖兼容性回退；实现将稳定槽位限定为 bound，新旧契约分别回归。
- 扩大到原 tool_authority 组后 **1 failed、95 passed，400.98秒**：原测试仍要求把失租包装为 DeepAgentRuntimeError。核对已提交 `2c70dfc81` 的 adapter 已明确原样传播 ContinuationLeaseLostError（§56），不是本节时间修复引入的生产行为。同步三处精确异常类型／原因断言，保留副作用一次、late output 仅观察、不得权威落库、不得再次结算等全部原断言；未改回吞包异常或接受多个类型。
- 旧领域回归第一次 **1 failed、28 passed、2 skipped，13.96秒**，缺 `QA_TOOL_WAIT_BATCH_DIR`。仅在进程环境指定唯一系统临时目录后重新执行为 **39 passed、11 skipped，33.61秒**。这些 skip 是旧测试各自的真实领域／PG／MySQL／RustFS 或专用 child-wait 隔离环境未开启，不是通过；没有为了扩大数字擅自设置额外 live 标志或复用未知 Redis 数据库。框架必跑 PG／Redis 强杀组另行执行，不用本旧领域组替代。

### v4 对应与剩余出口

| v4 阶段／条款 | 本节代码与测试 | 已实现范围／仍待验收 |
| --- | --- | --- |
| A2-2，§9.2，H05/H11 | tool_messages、ToolRuntimeService、Runtime／approval_runtime／tool_bridge／新工具消息测试 | 已有工具事实的展示时间来源、绑定身份及逐项续跑槽位；尚不证明缺 record 时可完整恢复展示映射 |
| A2-1，§9.4，H05/H07 | 原结果资产、分项投影及唯一 finalizer 回归 | 仍消费原结果及原消息事实，不重执模型／工具；完整材料重建未开放 |
| P0，§8.3，H02/H03/H12 | 原审批强杀、Delta／fencing／native interrupts 回归 | 保留原生消息身份／顺序／内容块／metadata 严格校验，不用投影修复豁免原生差异 |
| 兼容边界，§14／§16 | legacy adapter、旧 pending、无工具、child wait 和旧结果资产测试 | 不增严格格式或变更 stage12；无绑定旧试点与 legacy 原时钟／顺序契约保留；真实双制品部署另验 |

**A2-2仍未完成。** 本节没有把全部 ToolExecutionRecord／展示映射可靠保存为新的图后材料；完整事件清单／完成边界及终态绑定、工具内容与 artifact 等材料的完整往返、正常与恢复共享的完整结果投影器及无原资产恢复准入仍待实施。工具时间投影函数的抽取不等于完整 RuntimeTurnResult 导出器。最高证据仍为 E2，模型为本地 HTTP／假模型，业务工具为受控样本，本地对象后端不是 RustFS。A2-3／A3／B/C/D/E/F 保持完整目标，不以本节局部修复代替阶段出口；默认生产路由关闭，无新增付费调用或真实目标测试。

### 最终源码验证与提交归属

固定解释器实际查询版本仍为 Deep Agents 0.7.13、LangGraph 1.2.12、langchain-core 1.6.10、Postgres saver 3.1.2。以下均在 `G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server` 使用独立 `.venv-framework-v2`，设置 `RUN_LIVE_POSTGRES_TESTS=1`、`PYTHONIOENCODING=utf-8`。收窄 legacy 顺序兼容后启动最终批次并取回真实终态；不累计重叠范围为全系统覆盖数。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_tool_messages.py tests/test_framework_approval_runtime.py tests/test_framework_tool_bridge.py tests/test_deep_agent_runtime_adapter.py tests/test_framework_live_event_persistence.py tests/test_framework_terminal_gap.py -q -x -rs --tb=short
# 145 passed，180.49秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py tests/test_framework_approval_receipts.py tests/test_framework_delta_replay_order.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py tests/test_framework_tool_authority.py -q -x -rs --tb=short
# 108 passed，448.05秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_application_projection.py tests/test_framework_finalization_projection.py tests/test_framework_event_projection.py tests/test_framework_observation_projection.py tests/test_framework_runtime_result_asset.py tests/test_framework_snapshot_projection.py -q -x -rs --tb=short
# 205 passed，810.12秒，无 skip。
$env:QA_TOOL_WAIT_BATCH_DIR=Join-Path ([System.IO.Path]::GetTempPath()) ('framework-tool-messages-' + [guid]::NewGuid().ToString('N'))
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_tool_message_evidence.py tests/test_tool_runtime_heartbeat.py tests/test_tool_child_wait.py tests/test_tool_wait_batch.py tests/test_tool_operation_observation.py tests/test_approval_checkpoint_consistency.py -q -x -rs --tb=short
# 39 passed，11 skipped，32.50秒。跳过范围见上节，不计作领域集成通过。
```

最终会话49650／99358／1534／29321均退出0，无遗留活跃测试。较早的同投影组为 **205 passed，800.68秒**，其后只有 legacy 槽位分流修改，仍重跑完整最终组；原 tool_authority 精确异常同步后独立组为 **23 passed，85.77秒**，已包含于最终108项，不重复计数。未因观察超时重启测试。

根目录 `./.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check` 实际通过；v4／实施记录的代码围栏与绝对本地链接检查通过。全局检索 `_to_chat_messages`／新共享函数的生产调用和测试，核对启动／等待审批／完成及旧入口消费方；新测试沿 `.gitignore` 既有白名单纳入，不强制加入其他测试或运行产物。前端、schema、事件字段与 Worker 能力格式均未变更。

全量、前端、真实供应商／RustFS／领域 Runner、官方 saver conformance、E4 双制品／回滚未验收；实际 find_spec 确认 ruff／mypy／官方 conformance 未安装，没有安装新工具，compileall 不冒充 lint／类型检查。旧领域11项环境跳过继续保留，不能据三组框架集成通过宣称这些领域条件已满足。

本节代码／测试／记录归属同一提交，主题为 `【fix】稳定框架工具消息的时间与续跑顺序`，实际 commit ID 以 Git 历史及交付输出为准。整体目标继续 active；下一步沿既有事件／图前准备／ToolJob 事实补完整材料与完成边界、共享确定性结果投影，不增加平行恢复链，也不提前授予无原资产自动恢复权。

## 58. A2-2：原生工具消息携带完整应用投影材料（2026-10-27）

基线 `189d3881b`，核对 v4、§52–57、Git 和相关实现后续接。上一轮只调整开发提示词，没有新的代码实施进展。本轮保留原 v3 文档未提交修改，不覆盖、不纳入提交；未升级依赖、修改 DDL、启动／停止服务、开放生产路由、新增付费请求或测试真实业务目标。PG／Redis 使用既有 UUID 隔离 Harness，强杀仅针对测试创建的独立进程。

### 依据、有效红测与根因

- v4 §9.2 要求跨图返回空窗使用的工具记录、时间和展示映射在产生时保存。§57 已固定投影时间／槽位，但治理 callback 仍只向图返回 JSON 字符串，完整 ToolExecutionRecord 只在 Runtime 内存列表；ToolJob 不能代替全部输出加工和原展示字符串。
- 使用真实官方 Deep Agents、owner-bound saver、PG／事件 sink 增加新准备 Turn 的往返测试，首先取得 **1 failed、16 deselected，12.10秒**：工具成功完成且图终态已保存，但新连接读取的 ToolMessage.artifact 为 None。失败点是材料缺失，不是环境或 import。
- 固定 core 1.6.10 的 `tools/base.py::_format_output` 直接保留 ToolOutputMixin；LangGraph 1.2.12 ToolNode 接受 ToolMessage；Deep Agents 0.7.13 `_message_eviction.py::_build_evicted_tool_message` 显式保留 artifact。官方 [Messages / Tool message](https://docs.langchain.com/oss/python/langchain/messages#tool-message) 将 artifact 定义为可供应用使用而不发送给模型的数据。采用这个既有载体，不另建结果存储或恢复链；在线文档仅用于机制说明，API 与行为仍由固定源码及真实测试确认。
- 增加旧能力 Worker 对新格式审批的领取前测试，取得 **1 failed、18 deselected，9.06秒**：原 Dispatcher 只对 message／message_recover 检查 message-stage，approval_resume 会继续 claim；原版本查询也只映射原消息／消息后继，不能读取审批任务所属绑定的格式。修复职责放在既有领取前能力检查，不放到工具执行后补拒绝。
- 本地参考 `G:\Code_Warehouse\DeepAgent-Studio\借鉴\codex\codex-rs\rollout\src\recorder.rs::persist/flush`：借鉴原事实经存储确认再继续的边界，继续复用现有事件 ACK 和原生 sync durability，不搬 JSONL、队列或调度器。本地课程 `项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html` 用于同时验证轨迹、权限、重复执行和结果，而不只检查最终答案。

### 实现与模块边界

1. 新 `FrameworkToolProjection` 是版本1的**原应用材料格式**，scope 固定为 `tool_projection_material_only`。包含绑定指纹、原生 source AI／namespace／provider call 身份、完整 JSON 形态的 ToolExecutionRecord、完整原 ChatMessage、原模型可见 content 字符串及完整性摘要。核对派生业务 call ID、工具、展示身份、Turn／状态／trace／ordinal；NativeToolInterrupt／已持久 link 等子类型按共同 source 身份比较，不把它们的审批附加字段误当 source 漂移。
2. 治理 callback 仍先经过既有权限／审批／ToolJob 执行和必需事件的持久化确认。随后复用 §57 的同一展示投影，捕获实际 record 和完整 ChatMessage，再返回带 artifact 的原生 ToolMessage。原模型 content 不变，原 provider tool_call_id 不变；不是另一个工具循环、结果资产或 finalizer。
3. `LangChainToolAdapter` 只扩展 callback 返回类型为字符串或原生 ToolMessage，继续走官方 StructuredTool；不将 Registry handler 直接交给图，也不把 artifact 拼进文本、additional_kwargs 或供应商请求。原生终态全消息比较／hash 保持不变，artifact 也参与原件比较。
4. 新准备 Turn 由服务端设置 `framework_tool_projection_version=1`，在图前资产 feature_flags 和 initial_state 中一致冻结；使用已有 graph-input schema2，要求 **message-stage13**。新消费者支持到13，未知／不一致格式明确拒绝。无准备的旧试点和历史 pending 不增 marker、不升级格式；新输入伪造相同 context key 会被清除。新 pending 保留自己的 marker，后续逐项审批继续使用同一材料格式。
5. Dispatcher 对 message、审批继续／恢复和 reconcile 都在 claim 前读能力；只读版本查询可通过同 Session／完整绑定的原消息读取格式，不预先解析未知严格 schema，不改变普通 message-stage get 的任务归属，不授予新的恢复权限。旧能力对象 max12 在审批领取前 defer；原任务 attempt 不增加。
6. 实际 PG JSONB 往返测试确认 output 对象键序会改变。这里保存**原展示字符串和原模型 content**，而不是从重排对象重新渲染它们；不靠 sort_keys 改旧输出，也不放宽全文相等断言。完整 JSON record 的对象语义、原消息身份／时间及字符串均往返一致。

### 已取得的端到端证据及未覆盖边界

- 新连接通过官方通道物化读取正常工具和无 Job 的治理拒绝，完整 record、ChatMessage、原模型 content 和原生 archive 都一致；没有先生成结果资产／snapshot。无 Job 拒绝继续采用原已确认事件时间，不补造 Job 或 completed_at。
- 新准备 Turn 经真实图前资产／stage13、pending_turn 和两个独立审批任务，B先执行、A后执行；两个原生材料中的完整展示消息与实际发布消息一致，槽位及原时间不变。旧无 marker 分支也回归。桥接测试继续覆盖 provider 复用调用 ID、新 AI source 与审批归属，不重执已完成兄弟工具。
- 独立进程在真实图／治理工具／PG checkpoint 完成、Runtime 尚未获得适配器结果时暂停；父进程杀掉子进程后，经另一 saver 连接、官方 Delta 通道只读取回原材料。原 ToolJob 和记录时间一致，副作用记录一次，stage 仍为 runtime_submitted13，无 Runtime 结果资产、snapshot 或应用收尾。该用例证明工具材料跨空窗存活，**不证明完整 RuntimeTurnResult 已恢复或准入自动后继**。
- 四种传输各自用官方 SDK 和本地 HTTP 接收实际第二轮工具响应请求：artifact 标记不在 wire body，原工具文本、调用关联和原生签名仍在。另以官方大输出 offload 路径验证 content 变化时 artifact 完整保留。
- **原生 HITL reject 不进入治理 callback，本步不伪造该分支的 artifact**；它继续依赖原审批／ToolJob／continuation 事实。材料不是 ToolJob 副作用证明、owner 证明、应用提交证明或恢复许可；摘要能校验完整性，不是认证签名。跨 Turn 的旧材料不能凭字符串或元数据升级成当前绑定的执行权。
- 仅本步已覆盖的完整工具输出材料有可靠原生落点。完整事件清单／完成边界及终态精确绑定、未进入 callback 的应用材料、正常／恢复共用完整确定性结果投影器，以及无原资产恢复准入仍待实施。保留原强杀测试对“材料不足不得创建恢复后继”的约束。**A2-2仍未完成，A2-3／A3／B/C/D/E/F不收缩。**

### 中间失败与 Harness 同步

- 初次审批材料断言取得 **1 failed、4 passed，25.56秒**：reader 将包含额外审批字段的持久 link 与共同 source 类型直接作对象相等比较。修正为共同原生身份字段比较，保留 namespace／source AI／provider call／参数全部核对。
- 扩大 Worker 回归先后取得 **1 failed、94 passed，139.27秒**和 **1 failed、132 passed，166.36秒**。两个旧真实 Worker fixture 没有当前必需的模型预算表／冻结预算；对照 HEAD 的 semantic-input 保存已调用预算 snapshot，并非本步新增查询。改用现有 admission fixture，以实际绑定冻结预算并沿 fixture 清理；不添加生产 fallback，不更改完成／过期／调用次数断言。简化任务替身同步领取前版本查询端口。
- 独立进程测试准备阶段曾错误生成另一个带随机 client ID 的 payload；原输入校验正确拒绝，**1 failed、15 passed，14.99秒**。改为读取实际已受理的 owner.input。随后测试等待连接的默认 Redis socket timeout 短于 BLPOP，**1 failed、15 passed，19.48秒**；沿原强杀 Harness 建立专用长等待连接，不因观察超时重新运行副作用。最终该独立强杀用例为 **1 passed、15 deselected，16.30秒**，另纳入最终契约组。
- 新测试还修正了误从 ConversationResponse 读取 snapshot、插入测试时把原 copied.schema_version 断言落到新函数的问题，分别为 **1 failed、15 passed，68.33秒**和 **1 failed、23 passed，83.35秒**；这些是本轮测试脚手架错误，不计有效功能红测，原断言保留在正确测试中。
- 扩展投影组取得 **1 failed、146 passed，546.44秒**：真实新准备 Worker 已写 stage13，观察强杀旧断言仍要求12。逐项核对所有精确12断言，仅同步三个真实新准备 Worker 场景为13并要求 feature flag1；历史 prepared schema2／stage12 fixture 和旧 reader max12 断言保留。未改成宽泛 >= 断言或跳过。

### v4 对应、兼容部署与剩余出口

| v4 阶段／条款 | 本节代码与测试 | 已实现／仍待验收 |
| --- | --- | --- |
| A2-2，§9.2，H05/H11/H12 | FrameworkToolProjection、Runtime callback、tool_projection／approval_runtime／tool_bridge | 完整工具 record 与展示映射在原生消息生成时保存，真实 PG／独立进程强杀后只读获取；全结果导出和无资产恢复仍未开放 |
| P0／审批，§7–8，H01/H02/H03 | 官方工具消息、四传输 wire、原生严格比较及逐项审批回归 | 模型 content／调用身份不变，材料不入供应商请求；不是 E3 供应商验收 |
| A2-1，§9.4，H05/H07 | 原结果资产、snapshot／事件／观察与唯一 finalizer 回归 | 原结果仍复用原收尾，不增加第二套资产／投影链 |
| §14／§16，H11/H12 | graph-input flag、stage13、raw version query、Dispatcher／Worker capabilities | 新旧准备／pending 格式区分，消息和审批领取前检查；实际双制品部署／备份恢复／回滚另验 |

**部署限制：** 新格式写者上线前，必须先部署支持绑定级格式检查的兼容读者或隔离旧消费者。真正旧二进制对审批任务没有本节新检查，不能仅凭“stage13已设置”宣称它不会领取；本轮 max12 用例是在当前 Dispatcher 上模拟较低格式能力，不是两份真实制品的滚动演练。当前只做开发验证，未切路由，未批准生产写入新格式。无 DDL不等于已验证滚动兼容。

### 最终源码验证与提交归属

固定解释器实际查询仍为 Deep Agents 0.7.13、LangGraph 1.2.12、core 1.6.10、Postgres saver 3.1.2。以下后端命令均从 `G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server` 运行，使用 `RUN_LIVE_POSTGRES_TESTS=1`、`PYTHONIOENCODING=utf-8`；各批次使用既有 UUID 隔离资源，不累计重叠范围冒充全系统覆盖。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_runtime.py tests/test_framework_graph_input_asset.py tests/test_framework_tool_bridge.py tests/test_framework_chat_models.py tests/test_framework_tool_projection.py tests/test_agent_v2_worker_contract.py tests/test_framework_terminal_gap.py tests/test_deep_agent_runtime_adapter.py -q -x -rs --tb=short
# 206 passed，228.15秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py tests/test_framework_approval_receipts.py tests/test_framework_delta_replay_order.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py tests/test_framework_tool_authority.py tests/test_framework_native_terminal.py tests/test_framework_live_event_persistence.py -q -x -rs --tb=short
# 151 passed，524.22秒，无 skip；此后仅同步测试 fixture／新 stage 断言，生产代码未再变。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_application_projection.py tests/test_framework_finalization_projection.py tests/test_framework_event_projection.py -q -x -rs --tb=short
# 118 passed，479.57秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_observation_projection.py tests/test_framework_runtime_result_asset.py tests/test_framework_snapshot_projection.py -q -x -rs --tb=short
# 87 passed，386.78秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_message_stages.py tests/test_framework_message_recovery.py -q -x -rs --tb=short
# 40 passed，174.62秒，无 skip。
$env:QA_TOOL_WAIT_BATCH_DIR=Join-Path ([System.IO.Path]::GetTempPath()) ('framework-tool-projection-' + [guid]::NewGuid().ToString('N'))
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_tool_messages.py tests/test_tool_message_evidence.py tests/test_tool_runtime_heartbeat.py tests/test_tool_child_wait.py tests/test_tool_wait_batch.py tests/test_tool_operation_observation.py tests/test_approval_checkpoint_consistency.py -q -x -rs --tb=short
# 65 passed，11 skipped，33.88秒。旧领域 PG/MySQL/RustFS／child-wait 专用环境未开启，不计通过。
```

各批完整 stdout／stderr 保留在项目 `.codex-logs/framework-tool-projection-*-2026-10-27.txt`；失败日志不覆盖为成功。新增测试沿 `.gitignore` 白名单纳入，不提交运行产物。真实供应商、真实 RustFS／领域 Runner、前端、全量、官方 saver conformance 和 E4 部署演练未验收；最高证据 E2，业务工具及模型仍为受控样本。ruff／mypy 实际未安装，不安装新工具，compileall 不冒充 lint／类型检查。

续接直接核对六组原日志终态，确认 finalization 最终组为 **118 passed，479.57秒**，修正上面的运行中占位；没有重启已通过测试。当前进程清单无 pytest。根目录再次实际执行 `./.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check`，均通过；v4（12个围栏／30处绝对文件链接）、实施记录（156个围栏／1处绝对文件链接）结构及本地目标存在检查通过。生产调用及格式消费者检索覆盖 tool_projection marker／flag、graph-input parser、message-stage、Worker capability 与 Dispatcher；固定源码复核 `_format_output` 和 `_build_evicted_tool_message`。再次确认 ruff／mypy／官方 conformance 未安装，未宣称通过这些检查。

本节代码／测试／记录归属同一提交，主题 `【feat】在原生工具消息中保存完整投影材料`，实际 commit ID 以 Git 历史及交付输出为准。整体目标保持 active；下一步补未进入 callback 的材料、完整事件完成边界和共享确定性结果投影，不凭本节局部材料扩大恢复权。

## 59. A2-2：审批工具结果消费原生材料，兼容官方大输出 offload（2026-10-27）

基线 `bd85c2a4d`。续接核对 v4、§52–58、实际 Git 和六组原日志，先将 §58 完整代码／测试提交；原 v3 文档修改继续保留且排除于本节提交。§54 已有 P0 根因修复，不根据 v4 的旧快照重复实现排序器。当前沿已运行的 UUID 隔离 PG／Redis Harness 开发，没有启动／停止服务、修改依赖／DDL／生产路由或新增付费调用。

### 有效红测、根因与依据

1. 检查原生拒绝路径确认官方 HITL `reject` 不进入工具 callback，不能假造同样的 artifact。追查共同投影链时发现更前置的问题：§58 虽保存原材料，批准后的 Runtime 仍调用 `record_from_job` 二次加工输出，再覆盖 callback record。给真实审批链增加“禁止第二次加工”断言，取得 **1 failed、22 deselected，12.75秒**，失败点为 Runtime 477行的第二次 `record_from_job`，不是导入或 fixture 错误。
2. 固定版本官方 FilesystemMiddleware 把大输出正文替换为文件预览，但保留 artifact。将同一个真实审批用例接入官方 `tool_token_limit_before_evict=1` 路径后，结算还在对预览执行 `json.loads`：**1 failed、28 deselected，9.99秒**，`JSONDecodeError` 被正确上报为 `FrameworkApprovalRecoveryRequired`。根因是原模型内容与 SDK 当前展示内容职责混用，不能靠放行未知正文或删掉原生比较解决。
3. 查证固定 core 1.6.10 `tools/base.py::_format_output`、Deep Agents 0.7.13 `_message_eviction.py::_build_evicted_tool_message`／FilesystemMiddleware 与 LangChain 1.4.6 HITL `_answer_message`／`awrap_tool_call`；官方 [Messages](https://docs.langchain.com/oss/python/langchain/messages#tool-message)明确 artifact 是应用侧非模型数据，官方 [HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)区分 approve 执行与 reject 跳过工具。使用固定版本机制，不升级 SDK、不修改 site-packages。
4. 本地参考 `G:\Code_Warehouse\DeepAgent-Studio\借鉴\codex\codex-rs\rollout\src\recorder.rs::persist/flush`，只借鉴“先确认保存、再消费原事实”的边界；本项目继续使用原生 task writes 和既有审批 journal，不搬 JSONL／队列。课程 `项目借鉴/llm-testing-course/Agent轨迹与协议鲁棒性.html`用于同时检查结果、轨迹、重复副作用及协议错误；读取时一次终端 GBK 编码错误已通过仅设置 `PYTHONIOENCODING=utf-8`解决，不计功能红测。

### 实现与边界

- 在现有 `FrameworkApprovalContinuation` 内抽出共享的 owned-plan 事实校验，prepare 继续负责原发送／交付投影。新 `read_tool_projection` 只读取：重新核对当前 owner／task／Session／绑定、原决定消费及 applied 事实，读取原调用保留的原生 task writes，完整核对 application evidence，再解析原材料。它不标记 sending、不补 applied、不开图、不创建后继或新存储。
- 原生 task writes 不依赖当前 transcript 是否还保留完整旧工具正文；本节使用已有 journal 的精确 namespace／checkpoint／task／call 读取，不按最后文本或 provider ID 单独猜匹配。材料读者核对 source／工具／call、schema、摘要与 binding；材料 scope 不变，仍不提供执行或恢复许可。
- 结算和只读消费共用 `_application_evidence`：保留原生 result hash、实际 Job 输出 hash、决定／调用及 outcome 证明。存在已验证材料时，模型语义内容取原 `model_content`；当前预览仍是原生消息的一部分，继续参与精确原件比较及 hash。无材料历史分支保持原内容契约，不静默把任意字符串当成功结果。
- 只对 marker1 且 approve 的新材料路径，Runtime 使用原 record，不再重取／重加工 ToolJob artifacts。共享 `build_chat_tool_messages`消费同一原材料的完整 ChatMessage，保留原身份、正文、时间、metadata。先核对完整 record、当前槽位及已发布消息；冲突仍明确对账，不从 JSONB 重排后的字典重新渲染原字符串，不靠排序输出或弱化全文比较通过。
- 测试覆盖真实 stage13 图前准备、两个独立审批任务、B先A后、重复续跑、官方 offload、只读读取不增加事件／回执写入；另覆盖未 applied、错误审批引用、原生结果缺失、Job 漂移及失租。原消息范围／循环／摘要／finalizer 不变，无新增持久字段或格式号。

### v4 对应与剩余出口

| v4 阶段／条款 | 本节代码与测试 | 已实现范围／剩余出口 |
| --- | --- | --- |
| A2-2，§9.2／§9.4，H05/H11 | approval_continuation、Runtime、tool_messages、tool_projection／approval_runtime | 新批准工具的正常消费及只读原材料校验，避免重复输出加工；不是完整 RuntimeTurnResult 导出或无资产恢复 |
| P0／审批，§7–8，H02/H03/H12 | 共用 application evidence、官方 offload 与审批回归 | 原模型内容／SDK预览分别保留，严格原生比较不豁免；旧没有原材料的输出 offload 不据此宣称已可恢复 |
| A1／A2-1，§9.4 | 原应用投影与收尾回归 | 沿原结果资产和唯一 finalizer，不新增平行投影链或扩大 ACK 权限 |
| 格式兼容，§14／§16 | 原 marker1／stage13 消费者及旧无 marker 分支 | 无新序列化字段／DDL；§58新写者上线前兼容读者或隔离旧消费者要求不变，E4另验 |

**A2-2仍未完成。** 本节没有为原生 HITL reject 保存完整原应用展示材料；拒绝仍沿既有 ToolJob／receipt／continuation 分支生成，不伪造 callback artifact。完整事件完成边界和终态精确绑定、正常／恢复共享完整结果投影、无原结果资产的独立 Worker 恢复准入仍待实施；也没有声称已覆盖任意摘要后的整套结果重建。A2-3／A3／B/C/D/E/F保持完整范围。

### 最终源码验证与提交归属

首个消费红测修复后 **1 passed、22 deselected，8.60秒**。增加真实 offload 后两项 **2 passed、27 deselected，13.28秒**。早期工具材料／消息／审批 Runtime／continuation 组 **100 passed，169.37秒**；该组早于最后的 offload 证据修正和真实 prepared 双审批禁重加工断言，不以它替代最终回归。

以下在 `G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server`，使用独立解释器，设置 `RUN_LIVE_POSTGRES_TESTS=1`／`PYTHONIOENCODING=utf-8`。最终生产源码及测试没有再修改；完整 stdout／stderr 保留为 `.codex-logs/framework-tool-material-consumer-*-final-2026-10-27.txt`，红测／早期日志不覆盖。

```powershell
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_tool_projection.py tests/test_framework_tool_messages.py tests/test_framework_approval_runtime.py tests/test_framework_approval_continuation.py tests/test_framework_tool_bridge.py tests/test_framework_chat_models.py tests/test_framework_graph_input_asset.py tests/test_deep_agent_runtime_adapter.py tests/test_framework_native_terminal.py -q -x -rs --tb=short
# 274 passed，325.06秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_approval_recovery.py tests/test_framework_approval_receipts.py tests/test_framework_delta_replay_order.py tests/test_framework_checkpoint_fencing.py tests/test_framework_native_interrupts.py tests/test_framework_tool_authority.py tests/test_framework_live_event_persistence.py -q -x -rs --tb=short
# 124 passed，483.86秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_partial_projection_recovery.py tests/test_framework_application_projection.py tests/test_framework_finalization_projection.py tests/test_framework_event_projection.py -q -x -rs --tb=short
# 118 passed，458.88秒，无 skip。
../.venv-framework-v2/Scripts/python.exe -m pytest tests/test_framework_observation_projection.py tests/test_framework_runtime_result_asset.py tests/test_framework_snapshot_projection.py tests/test_framework_message_stages.py tests/test_framework_message_recovery.py tests/test_framework_terminal_gap.py -q -x -rs --tb=short
# 130 passed，526.78秒，无 skip。
```

最终会话23935／93114／42645／82260均已取回退出码0，早期及红测也已取回终态，没有遗留本轮运行中的测试；未因观察超时重新启动。根目录实际运行 `./.venv-framework-v2/Scripts/python.exe -m compileall -q Agent_Server/src Agent_Server/tests`、`git diff --check`及 v4／实施记录的围栏和绝对文件链接检查均通过；最终文档补齐后再次核对。源码检索覆盖新读端口、共用证据校验及消息投影的所有生产消费方；公共 API／前端契约没有变化。ruff／mypy／官方 saver conformance仍未安装，不声称这些检查通过。未运行全量、前端、真实供应商／RustFS／领域 Runner及 E4部署演练；隔离 PG／Redis／独立进程回归最高 E2，模型和工具仍为受控样本，不累计早期／重叠批次冒充覆盖率。

本节代码／测试／记录归属同一提交，主题 `【fix】从原生材料投影审批工具结果`，实际 commit ID 以 Git 历史及交付输出为准。整体 goal 保持 active；下一步补拒绝分支的原应用材料、完整事件完成边界及共享确定性结果投影，不把本节批准工具的材料消费等同完整 A2-2 恢复。
