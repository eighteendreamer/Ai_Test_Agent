# LangGraph / Deep Agents 迁移实施记录

日期：2026-10-25。实施依据：[v2 方案](langgraph-deepagents-integration-plan-v2-2026-10-25.md)。

**全系统迁移尚未完成。当前提交已完成 P0 基础、RuntimeBinding/版本化任务契约和原生 transcript；仍不构成跨进程审批恢复、checkpoint fencing 或模式切换验收。**

## 1. 阶段与现状

| 阶段 | 本轮状态 | 下一出口 |
| --- | --- | --- |
| P0 依赖与模型契约 | 已锁依赖、建立独立第三方环境；本地 HTTP/SSE 通过真实官方 SDK 验证四传输 | 真实供应商配置、目标部署环境及 workspace wheel 构建仍待验收 |
| P1 状态、输入、预算、版本绑定 | 已完成事件队列移出状态、RuntimeBinding 与 v2 task/outbox 隔离、原生 transcript | 共享实际调用预算、v2 Worker、checkpoint namespace 实际绑定 |
| P2 逐项审批、所有权、对账 | 已接入官方 `per_call` middleware 与单项 `Command(resume=...)` 投影；业务审批事务和 fencing 待实现 | 真实 PostgreSQL/Redis 跨进程故障链，以及 checkpoint / pending writes / ToolJob 旧 owner 拒写 |
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
- v2 task 将 binding 与 task、outbox 写入同一 PostgreSQL 事务；绑定指纹进入幂等哈希，冲突重试不能覆盖原绑定；v2 使用独立 `qa:tasks:agent:v2` stream。v2 Worker 尚未实现，因此不能把该 stream 当成已可投递生产流。
- Deep Agents 结果若为 LangChain `BaseMessage`，使用官方 `messages_to_dict/messages_from_dict` 保存/恢复；应用的 role/content 投影仍单独保存。原生 tool call ID、内容块、provider metadata 和签名不会通过摘要重建。
- Deep Agents 的 HITL 显式替换工厂默认的 batched middleware 为 `interrupt_mode="per_call"`。适配器能解析 `tool_approval` interrupt，并将审批决定恢复为单项 `Command(resume={interrupt_id: {"type": "approve"|"reject"}})`；同一 checkpoint 中未决定的 interrupt 继续保留。
- 以上改动已通过 RuntimeBinding 实时 PostgreSQL/Redis 测试 **2 passed**、既有 agent task 集成 **19 passed**、Deep Agents/runtime 相关回归 **60 passed、4 skipped**。尚未覆盖 checkpoint pending writes、跨进程 owner fencing、ToolJob 对账和 v2 Worker。
