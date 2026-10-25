# LangGraph / Deep Agents 迁移实施记录

日期：2026-10-25。实施依据：[v2 方案](langgraph-deepagents-integration-plan-v2-2026-10-25.md)。

**全系统迁移尚未完成。当前已完成 P0 基础、RuntimeBinding/版本化任务契约、原生 transcript、owner/checkpoint 事务保护及决定交付日志的同进程真实数据库验证；仍不构成业务 v2 审批 API、跨进程故障恢复或模式切换验收。**

## 1. 阶段与现状

| 阶段 | 本轮状态 | 下一出口 |
| --- | --- | --- |
| P0 依赖与模型契约 | 已锁依赖、建立独立第三方环境；本地 HTTP/SSE 通过真实官方 SDK 验证四传输 | 真实供应商配置、目标部署环境及 workspace wheel 构建仍待验收 |
| P1 状态、输入、预算、版本绑定 | 已完成事件队列移出状态、RuntimeBinding 与 v2 task/outbox 隔离、原生 transcript、v2 Worker 最小消费闭环、owner 与物理 namespace 绑定 | 共享实际调用预算、跨 Turn 上下文实链路与完整制品校验 |
| P2 逐项审批、所有权、对账 | 已接入官方 `per_call`、owner checkpoint 事务、receipt/proof、v2 决定原子受理与串行领取的内部端口 | 自动原生调用映射、API/Worker/Runtime/adapter 继续执行、交付与应用投影，以及跨进程故障链 |
| P3 代码审查实链路 | 待实现 | Prompt、Skills、只读探索、受治理子代理、流式事件及质量基线 |
| P4 逐模式迁移 | 待实现 | 每模式公共契约与领域裁决验收 |
| P5 旧实现收敛 | 待实现 | 存量绑定排空、未知操作结清和回滚验收 |

模型解析入口现在可以构造四传输官方对象。对象构造不提供共享调用账本、预算或恢复治理；现有可选 Deep Agents 路由仍沿原开关。不得据此开放全系统新路由。

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
