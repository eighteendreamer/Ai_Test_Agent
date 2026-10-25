# LangGraph / Deep Agents 迁移实施记录

日期：2026-10-25。实施依据：[v2 方案](langgraph-deepagents-integration-plan-v2-2026-10-25.md)。

**全系统迁移尚未完成。当前快照更新至 §28：已有 P0 基础、RuntimeBinding/版本化任务、owner/checkpoint 事务保护、原生决定交付和业务应用证明、v2 审批 API/Worker/逐项前端、已知结果审批/对账自身失租的后继与独立进程强杀恢复，以及根模型账本、主代理/具名子代理/摘要和 owner 作用域内 DTO 请求策略、前置语义分类与预算受理/Worker/生产组装接线。自动生成绑定及预算、活动时间计量、跨 Turn 上下文、完整故障恢复、真实发布与逐模式验收仍未完成；后文各节的验证结论仅适用于其阶段。**

## 1. 阶段与现状

| 阶段 | 本轮状态 | 下一出口 |
| --- | --- | --- |
| P0 依赖与模型契约 | 已锁依赖、建立独立第三方环境；本地 HTTP/SSE 通过真实官方 SDK 验证四传输 | 真实供应商配置、目标部署环境及 workspace wheel 构建仍待验收 |
| P1 状态、输入、预算、版本绑定 | 已完成事件队列移出状态、RuntimeBinding 与 v2 task/outbox 隔离、原生 transcript、v2 Worker 最小消费闭环、owner 与物理 namespace 绑定；§25–28 增加根模型账本、官方主/子代理/摘要及 owner 下 DTO 请求策略、前置语义分类和预算受理/Worker/生产组装接线 | 自动生成绑定/预算、活动时间计量、跨 Turn 上下文实链路与完整制品校验 |
| P2 逐项审批、所有权、对账 | 已接入官方 per_call、owner checkpoint/同步 ToolJob 执行权、receipt/proof、v2 API/Worker/Runtime、持久前端进度，以及已知结果审批和对账自身失租的后继强杀 Harness（§12–24） | 首次 message、未知效果查询/人工对账、其余故障窗口、领域 Runner 执行权及真实浏览器验收 |
| P3 代码审查实链路 | 待实现 | Prompt、Skills、只读探索、受治理子代理、流式事件及质量基线 |
| P4 逐模式迁移 | 待实现 | 每模式公共契约与领域裁决验收 |
| P5 旧实现收敛 | 待实现 | 存量绑定排空、未知操作结清和回滚验收 |

模型解析入口现在可以构造四传输官方对象并记录构建指纹。§26 通过显式 policy 注入连接 §25 的账本，§27 接入生产组装、显式预算受理和 Worker 检查，§28 接通 owner 作用域内 DTO 及输入分类请求。无 owner 的旧调用仍沿既有路径。现有可选 Deep Agents 路由仍沿原开关，默认提交入口仍生成 legacy 任务，不得据此开放全系统新路由。

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
