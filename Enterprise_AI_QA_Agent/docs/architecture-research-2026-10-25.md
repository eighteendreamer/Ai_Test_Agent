# 全系统架构调研与升级证据台账

## 1. 范围、基线与判定口径

- 任务依据：用户附件 `goal-objective.md`（2026-10-25），覆盖模型适配、Harness、上下文、记忆、长任务、编排、LangChain 与企业测试流程。
- 源码起始基线：`aba18e65c`；工作区起始无未提交修改。提交已明确删除错误指导文档，本调研不将其作为依据，也不恢复该文件。
- 本文随验证持续更新。候选表示尚待复现；已确认表示有受控复现；已排除只限记录的条件；已修复与已验证必须有实际代码和测试证据。
- 本轮只使用本地源码、隔离 fixture/mock 与官方资料；不读取真实凭据，不调用付费模型，不连接生产业务数据。数据库、真实供应商和部署容量尚未验证。
- 不以历史测试计数作为当前验收，不将工具执行成功或模型自述视为业务测试通过。

## 2. 依据与执行计划

| 顺序 | 依据来源 | 采用做法与产物 |
|---|---|---|
| 1 | 根目录 `AGENTS.md`、`README.md`、两层 `pyproject.toml`、`agent_web/package.json`、CI | 固定依赖及可执行命令；建立覆盖清单 |
| 2 | 当前生产入口、服务、持久化与消费方；既有测试及调研 | 建立真实调用链与根因索引，先验证再判断 |
| 3 | `G:/Code_Warehouse/DeepAgent-Studio/借鉴` 中对应实现；官方文档 | 逐机制对照，明确适用边界，保留 Python 服务及现有业务能力 |
| 4 | 最小隔离复现与正常对照 | 保留命令、样本、输出、验证限制；确认或排除候选 |
| 5 | 上述证据 | 输出职责、契约、数据流、状态归属、扩展示例、迁移/回滚与验收；重大变更先确认 |

## 3. 模块覆盖清单

| 模块 | 已检查路径 | 尚未检查 / 原因 | 状态 |
|---|---|---|---|
| 模型与协议 | 配置 DTO、注册中心、ModelRuntimeService、LangChain 适配器、客户端分派 | 协议 wire 测试、全部调用方、端点与能力差异待验证 | 进行中 |
| Harness / LangGraph | `build_agent_graph`、`AgentLoop.run_turn/stream_turn` | 生产入口到持久化、审批、中断和事件消费待逐项核对 | 进行中 |
| 上下文工程 | 提示词既有测试与调用边界 | 完整预算、工具消息关联、压缩与缓存待验证 | 待验证 |
| 记忆 | 向量召回测试中的 PG 回查及过滤 | 写入、权限、冲突、失效、注入与实际使用待验证 | 待验证 |
| 长任务 / 编排 | 图编译未配置 checkpointer；循环由 AgentLoop 驱动 | 任务领取、恢复、幂等、资源、子任务失败归并待验证 | 候选 |
| 测试业务 | README 的用例版本、套件冻结和执行链 | 沿真实服务逐层复核和测试；课程原文待查阅 | 待验证 |
| 前端 / CLI | 构建、测试命令；Worker 运行说明 | 同次运行状态解释、SSE 游标与取消待核对 | 待验证 |
| 依赖与配置 | 项目声明与已安装版本 | lock 一致性、可选 Deep Agents 实际启用边界待核对 | 进行中 |

## 4. 环境实测

2026-10-25，仓库根目录 `.venv/Scripts/python.exe`，Python 3.11.16。通过 `importlib.metadata.version` 实际读取：

| 包 | 已安装版本 | 声明 |
|---|---|---|
| langchain | 1.4.0 | ==1.4.0 |
| langchain-core | 1.6.2 | ==1.6.2 |
| langchain-openai | 1.6.2 | ==1.6.2 |
| langgraph | 1.2.11 | ==1.2.11 |
| openai | 3.14.0 | >=1.60.0 |
| anthropic | 1.5.0 | >=0.40.0 |
| google-genai | 2.23.0 | >=1.0.0 |
| pytest / pytest-asyncio | 9.1.1 / 1.4.0 | >=8.3.0 / >=1.4.0 |

依据：`Agent_Server/pyproject.toml` 与本地安装元数据。版本符合声明不代表协议能力已验证。

## 5. 根因索引

| 编号 | 模块 / 标题 | 状态 | 当前证据 | 下一步 |
|---|---|---|---|---|
| AR-M01 | OpenAI 路径仍允许显式 Responses，与任务 Chat Completions 限制不符 | 候选（静态路径已找到） | `schemas/model_config.py:ModelTransport`、`model_clients/registry.py`、`test_model_clients.py:test_resolve_client_dispatches_by_transport` | 受控验证可达性，枚举配置/持久化/UI/文档消费方，先形成迁移影响 |
| AR-H01 | LangGraph 节点状态与外层循环/会话快照的恢复边界 | 候选 | `graph/builder.py` 返回 `graph.compile()`；`AgentLoop.run_turn` 在 Python while 中重复 `ainvoke` | 核对实际恢复语义，区分审批续跑与强杀恢复 |
| AR-M02 | LangChain 根据模型名隐式切换到 Responses | 已确认，待修复 | 本地实际 HTTP：配置为 Chat，但包含 `codex` 的模型名发送 `/v1/responses`；普通模型名与原生路径对照通过 | 在共享模型工厂显式固定 Chat，覆盖普通调用、流式及对其他 Harness 暴露的模型 |

## 6. 验证记录

### V01：模型既有协议基线

- cwd：`Agent_Server`；解释器：`../.venv/Scripts/python.exe`。
- 命令：`python -m pytest tests/test_model_clients.py tests/test_langchain_model_adapter.py tests/test_legacy_model_adapter.py tests/test_model_request_profile.py tests/test_model_completion_contract.py tests/test_model_completion_http.py tests/test_model_stream_terminal_http.py tests/test_model_failure_lifecycle.py -q --tb=short --junitxml=../docs/architecture-research-2026-10-25-evidence/model-baseline.xml`。
- 实际：**162 passed，53.28s**，退出码 0。包含真实安装 SDK 对本机 HTTP fixture 的 Chat、Responses、Anthropic、Google 与 LangChain 路径，不代表真实供应商验收。
- 这组测试会验证现有 Responses 可以工作，因此全绿不能证明符合任务的 Chat-only 约束。

### V02 / AR-M02：配置协议被 SDK 模型名推断覆盖

- 根因与链路：`ModelRuntimeService._uses_langchain → LangChainModelAdapter._build_model → ChatOpenAI._use_responses_api`。共享工厂没有设置 `use_responses_api`，安装版本 1.6.2 的默认值为 None；其 `_model_prefers_responses_api` 对名字包含 `codex` 返回 True。
- 触发：开启 LangChain 适配器，transport=`openai_chat_completions`，model_id=`codex-fixture-model`。该名称是受控失败样本，不表示真实模型支持何种协议。
- 复现：`python -m pytest tests/test_model_chat_protocol_boundary.py -q --tb=short --junitxml=../docs/architecture-research-2026-10-25-evidence/model-routing-before.xml`。
- 实际：**3 failed，6 passed，8.91s**。流式和非流式均捕获路径 `/v1/responses` 而非 `/v1/chat/completions`；另一失败验证共享工厂暴露模型仍处于自动协议推断。普通名称、原生 Chat 对照通过。
- 影响 / 严重度：高。模型切换可使请求到错误端点，错误结构变成供应商失败，破坏 Chat-only 契约；运行记录仍以配置 transport 表达，难以仅凭配置解释实际协议。
- 业务后果：测试规划、报告等任一复用此工厂的调用可能失败；不推断发生过生产数据损失。
- 官方依据：[ChatOpenAI 参考](https://reference.langchain.com/python/langchain-openai/langchain_openai) 的模型名路由说明与 `use_responses_api` 字段；本地已安装 SDK 源码复核。适用原则是协议由系统配置拥有；不借用 Codex 自身的 Responses 选择。
- 治理设计：在现有共享 `_build_model` 设置 `use_responses_api=False`，让正常、流式、structured output 和 `build_model` 都继承同一协议；不增加旁路或第二套协议配置。`ModelRuntimeService` 现有 `model_completion_observed` 继续记录 transport 与 completion_status。
- 验收：上述 9 项全过，相邻模型基线无回归；SDK 自动推断被显式配置覆盖。测试不声称真实 codex 模型能接受 Chat 请求。
- 限制：此修复不消除 AR-M01 的显式 Responses 配置入口；移除该公共枚举及存量配置须独立迁移评估。

### V03：上下文、记忆与执行恢复现状

- 上下文实际顺序由 `graph/nodes/context_builder.py` → `router.py` → `prompt_assembler.py` → `model_invoker.py` 形成：会话输入/模式状态，观察记忆，项目用例向量命中，长期记忆，技能/MCP/tool 描述，系统提示和 runtime messages，最后送入 `ModelInvocationRequest`。`PromptAssemblyService` 会保留 section 级内容，`ModelRuntimeService` 记录 provider 请求摘要和 usage。
- 预算有两道当前保护：`finalizer.py` 在已取得 provider prompt usage 且达到 context window 90% 时停止下一轮；`ContextCompactionService` 在 70% watermark 或无 usage 时的消息数量阈值触发摘要，原消息仍留在会话存储，摘要记录 `covers_until_index`。这说明“压缩覆盖”与“原始历史”职责已分离；但摘要模型失败会返回 None 并继续原路径，需在产品事件中保持可见。
- 一次图循环由外层 `AgentLoop.run_turn` 重复 `graph.ainvoke`，而 graph 本身 `compile()` 未传 LangGraph checkpointer。项目的审批/手动 resume 依赖 `pending_turn` + PostgreSQL snapshot 的自有协议，不能等同于 LangGraph 原生 thread checkpoint。官方持久化文档区分 checkpointer（单 thread 短期状态、故障恢复）和 store（跨 thread 长期记忆）；官方中断文档要求 checkpointer + thread_id 才能在节点位置恢复。
- 记忆链路已可追踪：`MemoryRuntimeService.retrieve_for_turn` / `retrieve_observation_context` → Redis 只返回候选 id → PostgreSQL 按 session/project/environment/mode 过滤并 hydrate；`tests/test_memory_vector_retrieval.py` 的 7 项覆盖验证了过滤、版本、Redis 故障回退和数据库故障不误报。现有 `router` 和 `context_builder` 都是图节点，外层每次 loop re-entry 会重新经过它们；是否需要“每轮重召回”应由稳定的 context snapshot 决定，而不是默认重复 IO。
- 官方依据：[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)。本项目不直接替换自有 PostgreSQL 事实表；目标是明确“会话 checkpoint / 长期 memory / 业务知识 / 测试用例向量”四种存储责任，并让每次注入带来源和预算证据。

### V04：长任务、测试运行和前端验证

- `tests/test_test_run_lifecycle.py` 实际结果：**48 passed，16 skipped，9.52s**。测试覆盖 RunItem/Attempt checkpoint、租约过期重领、旧 lease 拒写、取消只影响非终态、并发 claim 不重复、启动恢复扫描；跳过项是依赖外部 PostgreSQL 的集成场景，不能宣称跨进程生产恢复全绿。
- `tests/test_session_event_delivery.py` 同批运行于上述命令并通过；事件流覆盖游标追加顺序、重复 event id 幂等、最终响应与完成事件原子性、跨存储 SSE 回补和写入失败取消 runner。事件事实在本地 PostgreSQL，Redis 主要承担任务调度。
- `tests/test_agent_task_dispatch.py -k 'killed_worker_pending or separate_worker_process_executes_persisted'` 在当前环境为 **4 skipped，15 deselected，6.03s**，原因是 live PostgreSQL 门禁；不能把“kill 后恢复”写成已验证。
- `tests/test_runtime_input_paths.py` 为 **4 passed，5 skipped，1 failed，10.13s**。失败是 `test_previously_admitted_parent_value_is_not_silently_replaced_on_recovery[False]` 的测试夹具 `SimpleNamespace` 缺少生产依赖 `get_execution_detail`（`agent_case_execution.py:58`），属于夹具漂移证据，不据此判定生产恢复缺陷；应先修测试夹具或改成真实 store fixture，再复验。
- 前端 `npm run typecheck` 通过；`npm run test` 实际结果 **77 files / 564 tests passed，62.10s**。一次误用 Jest 参数 `--runInBand` 失败，随后按 `package.json` 的真实脚本重跑通过；该失败不计为产品缺陷。

## 7. 由证据推导的目标架构

```text
API / CLI / UI
  → Admission（项目边界、幂等、输入模式、命令）
  → WorkItem / Execution Kernel（唯一父链和状态归属）
  → Scenario Pack（API/UI/性能/安全等声明、步骤、工具、Verifier）
  → ModelPort（Chat Completions 统一契约 + 供应商扩展）
  → Context Snapshot（预算、来源、脱敏、压缩覆盖）
  → Tool Port（权限、审批、lease、副作用幂等键、证据）
  → Event Store / Evidence / Verdict（业务事实）
  → SSE / Reports / Workbench projection
```

### 层职责

| 层 | 负责 | 不负责 |
|---|---|---|
| Admission | 校验 project_id、输入模式、命令幂等、权限 | 模型推理、测试通过判定 |
| Execution Kernel | WorkItem、Execution、Turn、Step、Attempt 的生命周期；租约、取消、checkpoint、恢复 | 具体测试场景知识 |
| Scenario Pack | 场景 manifest、planner、tool binding、deterministic verifier、结果投影 | 自建第二套任务状态机 |
| ModelPort | 模型/供应商/transport 显式选择；消息、工具、流式终态、usage、错误统一 | 业务状态迁移、自动换模型 |
| Context Snapshot | 预算分配、来源/版本、压缩、可解释注入 | 长期记忆事实写入 |
| Memory / Knowledge | 会话摘要、长期记忆、项目知识、用例向量分隔存储和召回证据 | 直接改变终态 |
| Tool Port | schema、权限、审批、资源 lease、幂等与副作用记录 | 让模型自述覆盖工具事实 |
| Evidence / Verdict | 原始响应、断言、日志、截图、环境和规则证据；确定性裁决 | 把“工具成功”直接转成“测试通过” |

### 关键流程与状态

1. **模型切换**：`model_key → immutable ModelConfigSnapshot → ModelPort`。切换重新做能力矩阵和契约测试；任务续跑若参数快照变化则拒绝静默续写（现有 `expected_parameters` 已有雏形）。
2. **工具调用**：模型只产出 ToolCall；Kernel 写 intent（含 idempotency key）→ policy/approval → Tool Port 执行或查询既有结果 → Evidence → 再把 ToolResult 回送模型。超时重试只对无副作用或已对账的操作开放。
3. **上下文构建**：固定 section 顺序并为每块记录 `source_id/source_type/version/char_or_token_cost/trust`；先预算系统约束和工具契约，再填历史、记忆和检索，超预算按策略裁剪并发出事件。
4. **恢复**：短期 graph checkpoint 用 `thread_id=execution_id`，业务 PostgreSQL snapshot 仍是事实源；恢复先检查 step intent/evidence，已完成副作用只重放结果，未知副作用进入 reconcile/人工处理，不重新猜测。
5. **测试通过**：`TestResult` 只能由 verifier/evidence 产生；Agent 计划、工具 exit code、模型判断分别落为输入证据，不直接写 Pass。回归运行保留原失败运行并创建新 run。

### 扩展示例

- 新增 OpenAI-compatible 供应商：只增加 provider profile、能力/请求映射和 contract fixture；不改 Kernel/Scenario Pack。
- 新增非 Chat 协议：新增明确的 transport adapter 和隔离矩阵；由于本系统当前约束为 Chat Completions，不能从模型名或 SDK 默认值隐式切换。
- 新增测试模式：新增 Scenario Pack manifest、planner、verifier 和 UI projection，注册一次；不得在 `SessionService`、`tool_runtime_service` 和前端状态表各加一套生命周期。

## 8. 分阶段迁移与验收

| 阶段 | 目标 | 迁移/回滚 | 验收 |
|---|---|---|---|
| P0 | 固定 Chat 协议、冻结 ModelPort/ContextSnapshot/Evidence DTO；修复测试夹具漂移 | 现有 `openai_responses` 保留为显式隔离配置；默认 LangChain flag 可关，回滚为旧适配器 | 模型 HTTP fixture 矩阵；所有消费方搜索；`runtime-input` 无夹具失败 |
| P1 | Kernel 引入 Step checkpoint + tool intent/evidence；把现有 session/run 作为事实投影 | 双写只限新表投影，旧表仍为事实；按 execution_id 灰度 | kill/reclaim、approval resume、重复副作用、SSE cursor、旧 lease fencing |
| P2 | Scenario Pack 收敛模式 runtime 和任务池；统一 WorkItem projection | 先 API 场景，失败回退旧 runner；不迁移已完成历史 | 新场景只触碰 pack；任务/报告/回归关联同一 execution/run/evidence |
| P3 | 预算、记忆来源证据和容量验证 | 配置灰度、按项目回滚 | P50/P95/P99、TTFT、token throughput、错误率、context budget 事件和跨项目权限隔离 |

重大事项：移除 `openai_responses` 公共枚举、数据迁移、切换全部模式到新 Kernel、接入真实 LangGraph checkpointer 或新增外部服务均属于破坏性/基础设施变更，需单独确认后实施。

## 9. 当前交付边界

- 本轮已实施且验证：LangChain Chat transport 显式固定 `use_responses_api=False`；新增受控回归测试；新建本调研台账。
- 本轮未实施：删除 Responses adapter/配置、统一所有任务表、引入 checkpointer、改造 Scenario Pack、测试夹具修复。原因是这些会改变公共协议、数据或核心执行架构，目标文件要求先形成证据和方案。
- 尚未验证：真实供应商协议、多实例部署容量、live PostgreSQL kill/recovery、实际生产模型配置、所有成熟 Agent 参考的功能对等。不得以本轮通过的本地 fixture 替代这些证据。
