# Enterprise AI QA Agent · LangChain 生态深度升级工程方案

版本：v1.0
日期：2026-09-08
适用环境：E:\PyThon\Anaconda_PyThon\envs\Python3.11
适用范围：后端运行时、Agent 编排、模型与工具适配、轨迹观测、评测与前端执行轨迹展示

## 1. 方案结论

本项目不采用“重写成 Deep Agents”或“用 LangSmith 替换现有 Flow”的路线，而采用渐进式底层升级：

    现有业务事实与安全治理继续保留
            ↓
    LangChain：统一模型、消息、工具、结构化输出与 Middleware
            ↓
    LangGraph：继续承担确定性编排、循环、中断、恢复和 Subgraph
            ↓
    Deep Agents：作为复杂任务的可插拔 Harness/Subgraph
            ↓
    LangSmith：旁路承载 Trace、评测、Prompt 与质量反馈
            ↓
    现有 Flow：继续面向产品用户展示本地会话执行轨迹

硬约束：

1. PostgreSQL 中的 Session、Turn、Event、Snapshot、Approval、Test Run 和 Evidence 仍是业务事实源。
2. LangSmith 故障不得阻断 Agent 执行、SSE、本地事件写入或恢复。
3. Deep Agents 不得绕过 Tool Registry、Permission Service、Safety Gate、Project Scope、Artifact Storage 和测试治理。
4. 不同时期长期维护两套不可对账的 Agent Loop、Worker 状态机或权限系统。
5. 所有上传 LangSmith 的输入、输出、工具参数和 Metadata 先经过字段白名单与敏感信息清洗。

依据：

- 项目现有代码和测试。
- 项目借鉴/langchain生态官方文档/ 下的官方 Markdown 快照。
- [LangChain Agents](https://docs.langchain.com/oss/python/langchain/agents)
- [LangChain Middleware](https://docs.langchain.com/oss/python/langchain/middleware/overview)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)
- [LangSmith Observability](https://docs.langchain.com/langsmith/observability-concepts)
- [LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)

## 2. 当前系统基线：已从代码核对的事实

### 2.1 依赖基线

项目声明文件为 Agent_Server/pyproject.toml。当前显式声明了 langgraph>=0.2.0，但没有显式声明 LangChain、LangSmith 或 Deep Agents。

实际开发环境为：

    Python 3.11.15
    langgraph       1.0.10
    langchain       1.2.3
    langchain-core  1.2.7
    langsmith       0.10.18
    deepagents      未安装

因此，第一阶段必须先解决“声明依赖与实际环境漂移”，不能直接安装 Deep Agents 并开始迁移。当前环境 pip check 还存在既有第三方依赖冲突，实施前必须记录并隔离，不能把它们误判为 LangChain 升级引入的问题。

后端命令统一使用：

    & "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -m pytest

### 2.2 当前执行图

代码入口为 Agent_Server/src/graph/builder.py：

    context_builder
      → router
      → planner
      → permission_gate
      → prompt_assembler
      → model_invoker
      → tool_executor / finalizer
      → responder

图使用 StateGraph(AgentGraphState) 编译，但不是通过完整的 LangGraph Server 或 Checkpointer 主导整个生命周期。当前 AgentLoop 反复调用 graph.ainvoke(current_state)。循环、重试、中断、错误恢复和部分恢复状态由项目自己的 AgentLoop、RuntimeService、ErrorRecoveryCascade 和 RuntimeControlRegistry 管理。因此不能假定 Deep Agents 会自动接管这些职责。

### 2.3 当前模型与工具系统

模型调用位于 Agent_Server/src/application/models/model_runtime_service.py，其下仍是项目自己的 Provider Client、OAuth 和 Provider Profile 适配。

工具调用不是简单的 LangChain Tool 列表，而是由以下能力共同组成：

- ToolRegistry
- ToolRuntimeService
- PermissionService
- SafetyGate
- Skill Runtime
- MCP Runtime
- Tool Job / Artifact
- Approval 与 Project Scope

LangChain Tool 只能作为协议适配层，不能取代现有工具治理体系。

### 2.4 当前轨迹和 Flow

当前轨迹链路为：

    append_graph_event()
      → AgentGraphState.event_log
      → Session Event Store
      → SSE / REST
      → FlowProjectionService
      → Vue Flow

核心文件：

- Agent_Server/src/runtime/execution_logging.py
- Agent_Server/src/application/flow/projection_service.py
- Agent_Server/src/api/routes/sessions.py
- agent_web/src/features/flow/stages.ts

Flow 是产品级会话轨迹投影，不是 LangSmith UI 的替代品。

| 视图 | 主要用户 | 事实来源 | 主要职责 |
|---|---|---|---|
| 本地 Flow | 测试人员、业务用户、审批人员 | 本地 Session/Event/Snapshot | 会话阶段、审批、恢复、工具、证据和回放 |
| LangSmith | 开发、运维、评测人员 | Trace/Run/Dataset/Experiment | 调用树、Token、耗时、模型输入输出、错误和评测 |

## 3. 目标架构

### 3.1 分层职责

    L0 业务契约层
       Session / Turn / Approval / Evidence / TestRun / TraceContext

    L1 Provider Adapter
       现有 ProviderClient + LangChain ChatModel 适配器

    L2 LangChain Primitive
       Message / Tool / Structured Output / Middleware / Runtime Context

    L3 Agent Harness
       现有 RuntimeService + Deep Agent Adapter + Skills + Subagents

    L4 LangGraph Runtime
       StateGraph / Checkpoint / Interrupt / Streaming / Subgraph

    L5 应用编排
       mode、coordinator、worker、测试运行和安全策略

    L6 Observability & Evaluation
       本地 Event/SSE + LangSmith Trace/Dataset/Experiment

    L7 Persistence & Infrastructure
       PostgreSQL、MySQL、Redis、Memgraph、RustFS、MCP

### 3.2 建议新增模块

    Agent_Server/src/application/observability/
    ├── trace_context.py
    ├── trace_redaction.py
    ├── langsmith_adapter.py
    ├── local_trace_projector.py
    └── observability_service.py

    Agent_Server/src/application/langchain/
    ├── model_adapter.py
    ├── tool_adapter.py
    ├── message_adapter.py
    ├── middleware_registry.py
    └── compatibility.py

    Agent_Server/src/application/deep_agents/
    ├── mode_adapter.py
    ├── code_review_agent.py
    ├── subagent_bridge.py
    └── result_normalizer.py

新增模块必须依赖现有契约，不允许把 LangSmith SDK、Deep Agents 类型渗透到领域模型和 API DTO。

## 4. Trace 与业务数据设计

### 4.1 TraceContext

统一上下文至少包含：

    session_id
    turn_id
    trace_id
    parent_trace_id
    project_id
    mode_key
    agent_key
    case_id
    case_version_id
    suite_version_id
    test_run_id
    environment
    runtime_version

业务 project_id 不直接等于 LangSmith Project 名称。LangSmith Project 使用环境配置，例如 enterprise-ai-qa-agent-dev/staging/prod；业务 ID 独立放入 metadata。

### 4.2 映射关系

| 当前对象 | LangSmith 对象/字段 | 规则 |
|---|---|---|
| session_id | Thread 或 metadata | 保留业务 ID，不依赖外部 ID 作为主键 |
| turn_id | Root Run metadata | 每轮独立可定位 |
| trace_id | correlation metadata | 本地和外部追踪的关联键 |
| LangGraph node | Child Run | 节点名保持稳定 |
| ModelRuntimeService.invoke | LLM Run | 记录模型、Provider、耗时、Token 摘要 |
| ToolRuntimeService | Tool Run | 参数经过脱敏和摘要化 |
| Coordinator Worker | Nested Run | parent_trace_id 关联父子关系 |
| Test Run | Dataset/Experiment metadata | 外部评测不替代本地运行事实 |
| Artifact | artifact_id 引用 | 不上传敏感文件原文 |

### 4.3 事实边界

    LangGraph Checkpoint：Agent 执行状态
    PostgreSQL Snapshot：产品审计、恢复和回放快照
    PostgreSQL Event：本地实时事件和业务事件
    LangSmith Trace：外部调用观测记录
    LangSmith Dataset/Experiment：评测样本与实验比较

任何一方不可单独成为另一方的替代品。

## 5. 配置和依赖治理

### 5.1 配置模型

在 Agent_Server/src/core/config.py 增加独立配置域：

    class LangSmithConfig(BaseModel):
        enabled: bool = False
        project: str = ""
        endpoint: str = ""
        api_key: SecretStr | None = None
        workspace_id: str = ""
        api_key_env: str = "LANGSMITH_API_KEY"
        workspace_id_env: str = "LANGSMITH_WORKSPACE_ID"
        tracing_mode: str = "off"
        sample_rate: float = 1.0
        capture_inputs: bool = False
        capture_outputs: bool = False

实际字段命名和 SDK 参数已按锁定版 SDK 核对；密钥通过 `LANGSMITH__API_KEY` 进入 Pydantic `SecretStr`，也兼容由 `LANGSMITH_API_KEY` 注入的进程变量。配置模板统一放在 `Agent_Server/.env.example`，本机 `.env` 默认关闭且不提交密钥。不能凭记忆复制 API。

### 5.2 依赖分层

建议在 pyproject.toml 建立可选组，版本号在兼容性测试后填写：

    [project.optional-dependencies]
    langchain-ecosystem = [
      "langchain==<经过验证的版本>",
      "langchain-core==<经过验证的版本>",
      "langgraph==<经过验证的版本>",
      "langsmith==<经过验证的版本>",
    ]
    deep-agents = [
      "deepagents==<经过验证的版本>",
    ]

不能长期使用无上限的 >= 作为生产基线。锁定文件要记录 Python 版本、平台、直接依赖、传递依赖和许可证。

### 5.3 阶段 0 的环境交付物

1. pip freeze 快照。
2. pip check 输出和既有冲突清单。
3. LangChain 生态版本兼容矩阵。
4. 现有后端测试基线。
5. Deep Agents 安装后的导入与最小运行验证。

## 6. 分阶段实施计划

### 阶段 0：契约、依赖和可回滚基线

工作项：

- 建立 TraceContext、ToolExecutionContract、ApprovalContext、EvidenceContext。
- 记录 AgentGraphState 入口、输出和事件类型。
- 生成 Python3.11 环境依赖快照。
- 补充显式 LangChain/LangGraph/LangSmith 依赖约束。
- Deep Agents 单独作为可选依赖，不进入默认启动路径。
- 确定 langsmith_enabled、langchain_model_adapter_enabled、deep_agents_code_review_enabled 三组 Flag。

验收：关闭所有 Flag 时，现有后端和前端测试结果与基线一致。

### 阶段 1：LangSmith 非阻塞观测

只接入以下边界，不改变业务执行结果：

- RuntimeService.execute_turn
- AgentLoop.run_turn
- Graph 节点
- ModelRuntimeService.invoke
- ToolRuntimeService
- CoordinatorRuntimeService

LangSmith Adapter 负责：

- 根 Turn Trace。
- Graph Node 子运行。
- Model、Tool、Worker 嵌套运行。
- 本地 trace_id 写入 metadata。
- 字段白名单和敏感信息清洗。
- 超时、连接失败、配额失败时旁路降级。

验收：

- LangSmith 开启时，本地 Event/SSE/Snapshot 仍完整。
- LangSmith 不可用时业务仍按原有规则运行。
- 一个本地 trace_id 可以定位到外部 Trace。
- 密钥、Cookie、Authorization 和完整安全测试凭证不会进入 Trace。

### 阶段 2：LangChain 模型与消息适配

保留现有 Provider 管理，增加：

    ModelRuntimeService
      ├── LegacyProviderAdapter
      └── LangChainModelAdapter

迁移顺序：

1. 统一消息结构。
2. 统一 Tool Schema。
3. 统一 Structured Output。
4. 统一 Streaming 事件。
5. 统一 Retry/Timeout/Usage。
6. 在非关键模式灰度切换。

验收：旧适配器和 LangChain 适配器的工具调用、终态、错误分类和 Token 统计满足兼容测试。

### 阶段 3：Middleware 化横切能力

优先迁移：

- Context Compaction
- Retry 与 Timeout
- Token Budget
- 动态 Prompt
- PII/Secret Redaction
- 输出 Guardrail
- 观测 Callback

暂不把以下能力完全移出业务层：

- PermissionService
- SafetyGate
- ApprovalScopeService
- Security Target Guard
- Project Scope
- Test Run 原子领取与终态治理

这些能力仍必须在业务边界强制执行。

### 阶段 4：Deep Agents code_review 试点

只迁移 code_review：

    mode_key == code_review
      → DeepAgentModeAdapter
      → Deep Agent Harness/Subgraph
      → 结果归一化为 AgentGraphState
      → 现有 finalizer/responder

Deep Agent 使用的工具必须由现有 ToolRegistry 生成，文件、代码或外部系统操作必须经过现有权限和安全门。

试点必须覆盖：

- 多步计划。
- 工具失败恢复。
- 上下文压缩或卸载。
- Skill 渐进式加载。
- 子代理委派。
- LangSmith 嵌套 Trace。
- 本地 Event 与 Flow 投影。

### 阶段 5：Coordinator/Worker 对齐

统一边界：

    Coordinator
      → 子任务契约
      → 权限/资源范围
      → Subagent
      → 子结果归一化
      → Worker Session/Event/Snapshot

同一模式不得长期同时运行两套不可对账的 Worker 状态机。

### 阶段 6：LangSmith 评测闭环

LangSmith Dataset/Experiment 用于 Prompt 回归、模型比较、工具选择准确率、Agent 轨迹质量和失败样本分析。

本地 Test Case/Test Suite/Test Run 仍负责正式版本治理、原子领取、证据入库和回归运行。两边通过 project_id、case_id、case_version_id、suite_version_id、test_run_id、environment、runtime_version 关联。

### 阶段 7：按模式迁移

    code_review
      → default
      → api_testing
      → ui_automation
      → compatibility_testing
      → smoke_testing
      → performance_testing
      → security_testing

性能和安全模式最后迁移，因为它们有严格的环境、目标授权、资源限制、容器生命周期和证据链要求。

## 7. 前端 Flow 升级方案

### 7.1 保留当前数据通路

不让 Vue 前端直接依赖 LangSmith SDK，继续使用：

    /sessions/{session_id}/events
    /sessions/{session_id}/flow
    /sessions/{session_id}/snapshots

后端 FlowProjectionService 继续将本地事件投影为产品 Flow。

### 7.2 增加 Trace 关联信息

在 Flow Inspector 或 Snapshot 面板增加可选字段：

- trace_id
- LangSmith Trace 状态
- 后端生成的外部 Trace 深链接
- 当前 Node Run ID（允许时）
- Token/耗时摘要

LangSmith 未启用或不可用时，仍显示本地信息。

### 7.3 契约同步

新增事件字段必须同步更新后端 Pydantic Schema、FlowProjectionService、前端 ExecutionEvent、stages.ts 和相关测试。禁止只把前端必须展示的业务状态保存到 LangSmith metadata。

## 8. 安全和数据治理

默认禁止进入 LangSmith：

- API Key、OAuth Token、Cookie、Authorization。
- 用户密码和证书。
- 安全测试原始凭证。
- 未授权的完整页面 DOM、截图或上传文件原文。
- 未脱敏的测试目标和生产数据。

默认允许外发：

- 业务 ID。
- 节点名、工具名、Agent 名。
- 错误类型和错误摘要。
- Token、耗时、重试次数。
- Artifact ID、Evidence ID 引用。
- 截断和脱敏后的摘要。

外部观测调用必须设置超时，失败时记录结构化本地日志，不阻塞主执行链，也不能吞掉脱敏失败、上报失败和重试耗尽信息。

## 9. 测试与验收体系

### 9.1 新增单元测试

    Agent_Server/tests/test_trace_context.py
    Agent_Server/tests/test_trace_redaction.py
    Agent_Server/tests/test_langsmith_adapter.py
    Agent_Server/tests/test_langchain_model_adapter.py
    Agent_Server/tests/test_langchain_tool_adapter.py
    Agent_Server/tests/test_deep_agent_mode_adapter.py

覆盖 ID 映射、父子 Trace、脱敏、LangSmith 失败降级、重复上报、权限仍生效和 Deep Agent 结果归一化。

### 9.2 集成测试链路

    消息 → Graph → Model → Tool → Event/SSE → Flow
    消息 → Graph → Model → Tool → LangSmith Trace
    审批 → Interrupt → Snapshot → Resume → Trace 延续
    Coordinator → Worker → 子 Session → 父子 Trace

### 9.3 验证命令

    Set-Location G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server
    & "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -m pytest

    Set-Location G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\agent_web
    npm test -- --run
    npm run build

必须区分代码回归失败与环境缺依赖，不能用“环境问题”掩盖代码失败。

### 9.4 性能验收

LangSmith 开启前后比较：

- 首 token 延迟 TTFT。
- 完整响应延迟。
- 工具调用额外耗时。
- SSE 推送延迟。
- Trace 上报队列积压。
- 内存和连接数。

## 10. 回滚和发布策略

Feature Flags：

    LANGSMITH_ENABLED=false
    LANGCHAIN_MODEL_ADAPTER_ENABLED=false
    LANGCHAIN_TOOL_ADAPTER_ENABLED=false
    DEEP_AGENTS_CODE_REVIEW_ENABLED=false

回滚层级：

    关闭 Deep Agents Flag → 现有 code_review Harness
    关闭 LangChain Model Adapter → 现有 ProviderClient
    关闭 LangSmith → 本地 Event/SSE/Snapshot

不能通过删除本地事件或 Snapshot 逻辑来回滚外部观测。

每阶段进入下一阶段前，必须满足：测试通过、旧新路径结果对账、Trace 可关联、脱敏测试通过、失败降级通过、性能预算达标、回滚证据齐全。

## 11. 里程碑与交付物

| 里程碑 | 交付物 | 不包含 |
|---|---|---|
| M0 | 依赖快照、兼容矩阵、契约和 Flag | 不改执行逻辑 |
| M1 | LangSmith Adapter、脱敏、旁路 Trace | 不迁移模型和 Deep Agents |
| M2 | LangChain Model/Message Adapter | 不改变业务路由 |
| M3 | Tool Adapter、Middleware Registry | 不绕过权限安全 |
| M4 | code_review Deep Agent Harness | 不迁移其他模式 |
| M5 | Worker/Subagent Bridge | 不保留双重长期状态机 |
| M6 | Dataset/Experiment/Feedback 流程 | 不替代本地 Test Run |
| M7 | 逐模式灰度迁移 | 安全/性能最后进行 |

## 12. 第一批实际实施任务

下一次开发只做以下内容：

1. 用 Python3.11 环境生成依赖和 pip check 基线。
2. 在 src/core/config.py 增加 LangSmith 配置域，默认关闭。
3. 新增 TraceContext 和脱敏策略单元测试。
4. 新增 LangSmith Adapter 接口和 No-op 实现。
5. 在 RuntimeService/AgentLoop 建立根 Trace 入口，但不改变执行结果。
6. 为 Model、Tool、Worker 增加可选子 Trace。
7. 验证 LangSmith 不可用时本地 Event、SSE、Snapshot、Flow 全部继续工作。
8. 通过后再进入 LangChain Model Adapter，不在同一批次安装 Deep Agents。

这批任务完成前，不修改 build_agent_graph 的业务节点拓扑，也不替换现有 ModelRuntimeService 或 ToolRuntimeService。

## 13. Definition of Done

一个生态升级阶段只有满足以下条件才算完成：

- 代码、配置、依赖版本与文档一致。
- 使用指定 Python3.11 环境完成实际验证。
- 相关单元、集成、前端和构建测试实际运行。
- LangSmith 关闭、开启、故障三种状态均有测试。
- trace_id 能关联本地事件和外部 Trace。
- 脱敏策略有真实失败样本回归测试。
- Deep Agents 不绕过权限、安全和证据链。
- 本地 Flow 在 LangSmith 不可用时仍正常展示。
- 没有遗留双重状态机、临时兼容分支或未声明 TODO。
- 每个阶段都有可执行的回滚方式。
+
## 14. 阶段执行状态与测试台账

本节是本方案的唯一执行状态源。第 6、11、12 节用于描述路线和交付物，本节用于记录每个阶段实际做到了哪里。状态只允许使用：

- 未进行：尚未修改该阶段代码，阶段测试未执行。
- 进行中：已开始产生阶段交付物，但尚未满足全部退出条件。
- 已完成：全部任务、测试、对账、文档和回滚验证均已完成。

状态更新规则：

1. 每次开发结束必须更新“当前状态、完成项、未完成项、测试结果、阻塞项、最近提交”。
2. 测试结果必须包含日期、环境、命令、通过/失败/跳过数量和错误摘要。
3. “代码已写完”不等于“已完成”；只要测试、对账、脱敏或回滚验证缺一项，状态仍为进行中。
4. 未实施阶段的测试结果必须写“未执行”，不能沿用其他阶段的结果。
5. 某阶段发生需求调整时，先更新本节的范围和退出条件，再修改代码。
6. 阶段完成后原则上不回写历史结果；新增回归失败以新记录追加，保留原始证据。

### 14.1 总体状态看板

截至 2026-09-09：

| 阶段 | 名称 | 状态 | 已完成度 | 当前结论 | 测试状态 |
|---|---|---:|---:|---|---|
| 准备项 | 官方文档归档 | 已完成 | 100% | 35 份官方资料已归档并建立索引 | 文档存在性已核对 |
| 0 | 契约、依赖和可回滚基线 | 进行中 | 90% | P0-01～P0-06、P0-08～P0-10 已完成；P0-07 因 Deep Agents 与当前主服务生态版本不兼容而阻塞，阶段仍不能关闭 | 现有环境、干净 C1 环境、隔离 C2 候选 Harness、后端/前端契约回归和真实默认模型链路均通过；共享开发环境 pip check 仍受非主服务工具冲突影响 |
| 1 | LangSmith 非阻塞观测 | 进行中 | 98% | 真实 LangSmith 上报、父子树、本地 Run 对账、敏感数据扫描、故障降级、开关基线、并发阶梯和短时 soak 已完成；正式业务阈值、直接队列深度和 30 分钟持续窗口仍未确认/完成 | 观测专项 19 项、前端 33 项、后端全量 747 项、真实默认模型链路、云端 Trace、并发 1/2/3/5 阶梯均通过；短时 soak 9/10 + 重试通过 |
| 2 | LangChain 模型与消息适配 | 未进行 | 0% | 尚未建立新旧适配器 | 未执行 |
| 3 | LangChain 工具适配与 Middleware | 未进行 | 0% | 尚未改造横切能力 | 未执行 |
| 4 | Deep Agents code_review 试点 | 未进行 | 0% | deepagents 尚未安装 | 未执行 |
| 5 | Coordinator/Worker 与 Subagents 对齐 | 未进行 | 0% | 等待阶段 4 稳定 | 未执行 |
| 6 | LangSmith 评测闭环 | 未进行 | 0% | 尚未建立 Dataset/Experiment 映射 | 未执行 |
| 7 | 按模式灰度迁移 | 未进行 | 0% | 等待阶段 1—6 完成 | 未执行 |

“完成度”只用于进度展示，不参与是否完成的判定。

### 14.2 当前基线测试记录

测试环境：

    Python：E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe
    Python 版本：3.11.15
    Node/Vite：使用 agent_web 当前安装环境
    日期：2026-09-08

| 检查 | 命令 | 结果 | 证据摘要 |
|---|---|---|---|
| Python 编译 | python.exe -m compileall -q src tests | 通过 | exit code 0 |
| 后端测试 | python.exe -m pytest -q | 通过 | 739 passed，8 skipped，1 warning，24.84s；观测/Flow 专项 20 passed |
| 前端测试 | npm test -- --run | 通过 | 2 files passed，32 tests passed，3.62s |
| 前端构建 | npm run build | 通过但有警告 | 3154 modules transformed，18.77s；主 chunk 约 2.5 MB |
| Python 依赖一致性 | python.exe -m pip check | 未通过 | 发现 browser-use、mem0ai、mitmproxy 的既有版本冲突 |
| 完整环境快照 | python.exe -m pip freeze | 通过 | 已保存为 Agent_Server/docs/python311-runtime-freeze-2026-09-08.txt |
| 干净主服务 C1 安装 | 临时 Python3.11 venv + pip install -e . | 通过 | `pip check` 通过；运行时快照已保存为 Agent_Server/docs/python311-main-service-c1-freeze-2026-09-08.txt |
| 干净环境应用导入 | C1 venv + `import src.main` | 通过 | 应用入口导入成功；此前缺失的 dependency-injector/python-magic/playwright 已补齐声明 |
| 干净环境真实链路 | C1 venv + uvicorn + HTTP API | 通过 | health 200、postgres_ok=true、Session/Message/Flow/Snapshot/Events 均成功；25 events、1 Snapshot |
| Deep Agents C2 候选解析 | PyPI + 独立 C2 venv | 通过 | `deepagents==0.7.13` 安装成功；LangChain 1.4.0/Core 1.6.2/LangGraph 1.2.11/LangSmith 0.12.2；`pip check` 通过 |
| Deep Agents C2 Harness | C2 venv + fake tool-capable chat model | 通过 | `create_deep_agent` 构造成功；离线 `invoke` 成功，2 messages |
| 生态包元数据 | python.exe + importlib.metadata | 通过 | Python 3.11.15；LangChain 1.2.3；Core 1.2.7；LangGraph 1.0.10；LangSmith 0.10.18 |
| Deep Agents 索引解析 | python.exe -m pip index versions deepagents | 未通过 | 当前软件包索引返回 No matching distribution found；未修改环境 |

后端警告：

    StarletteTestClient 使用 httpx 的方式已标记 deprecated，建议依赖迁移时单独核对；
    本阶段不顺手升级，避免扩大范围。

pip check 已知冲突：

- browser-use 0.11.1 要求 openai>=2.7.2,<3.0.0，当前为 openai 1.109.1。
- browser-use 0.11.1 要求 pypdf>=5.7.0，当前为 5.6.0。
- browser-use 0.11.1 要求 python-docx>=1.2.0，当前为 1.1.2。
- mem0ai 1.0.0 要求 protobuf>=5.29.0,<6.0.0，当前为 7.35.1。
- mitmproxy 11.0.2 与当前 asgiref、cryptography、h11、pyOpenSSL 存在版本冲突。

真实运行链路（2026-09-08）已验证两次：启动 `uvicorn src.main:app --host 127.0.0.1 --port 18124` 成功；`GET /api/v1/health` 返回 200 且 `postgres_ok=true`；创建 Session、发送消息、调用数据库默认模型、获取事件历史、Snapshot 和 Flow 均返回 200；最近一次验证生成 24 条事件和 1 个 Snapshot。LangSmith 未启用，因此本次不证明外部 Trace 已成功上报。

这些冲突发生在生态升级代码实施前，必须作为“既有环境债务”单独记录。处置结论已确定为将 browser-use、mem0ai、mitmproxy 隔离到独立工具环境，不通过降级主服务依赖消除共享环境冲突。详细证据和兼容矩阵见 `Agent_Server/docs/langchain-ecosystem-compatibility-matrix-2026-09-08.md`。干净主服务环境的 `pip check` 和真实运行已通过；阶段 0 目前仅剩 P0-07 的生态协调阻塞，不能因此提前关闭阶段。

### 14.3 阶段 0：契约、依赖和可回滚基线

状态：进行中
目标：建立可重复安装、可测试、可回滚的基线，避免后续出现“开发机能运行、项目声明无法复现”。

前置条件：

- 指定 Python3.11 环境可用。
- 当前主分支后端和前端基线测试已执行。
- 官方文档归档可读。

具体任务：

| ID | 任务 | 目标文件/位置 | 状态 | 完成判据 |
|---|---|---|---|---|
| P0-01 | 核对解释器和已安装版本 | Python3.11 环境 | 已完成 | 版本记录进入本文 |
| P0-02 | 运行 compileall、pytest、前端测试和构建 | Agent_Server、agent_web | 已完成 | 结果进入 14.2 |
| P0-03 | 保存直接/传递依赖快照 | Agent_Server/docs/python311-runtime-freeze-2026-09-08.txt | 已完成 | 当前开发环境完整 `pip freeze` 已保存；不冒充主服务 lock |
| P0-04 | 处理或隔离 pip check 冲突 | 兼容矩阵文档 | 已完成（隔离决策） | 8 条冲突均已归因并确定独立环境边界；共享环境仍不通过 |
| P0-05 | 建立 LangChain 生态兼容矩阵 | Agent_Server/docs/langchain-ecosystem-compatibility-matrix-2026-09-08.md | 已完成（候选矩阵） | 当前四包、Provider 基线、C1 主服务和 C2 Deep Agents 候选组合均有证据；主服务升级决策仍属于后续阶段 |
| P0-06 | 显式声明 LangChain/LangSmith 依赖 | Agent_Server/pyproject.toml | 已完成 | 声明、安装、`pip check`、应用导入和 C1 真实链路均通过；C1 快照已保存 |
| P0-07 | 将 deepagents 放入可选依赖组 | Agent_Server/pyproject.toml | 阻塞 | C2 候选已验证，但与当前主服务生态版本不兼容；必须先完成阶段 2—3 的协调升级和回滚证据，不能声明一个无法解析的 extra |
| P0-08 | 新增 TraceContext 契约 | application/observability/trace_context.py | 已完成 | 类型、校验、序列化和契约测试已通过 |
| P0-09 | 新增观测 Feature Flags | core/config.py、配置示例 | 已完成 | 默认关闭且配置校验通过 |
| P0-10 | 固化现有事件和状态契约 | `Agent_Server/docs/session-event-flow-contract-2026-09-08.md`、schemas、前后端契约测试 | 已完成 | Event/Snapshot/Flow 单一数据通路、递归 JSON payload、轮次筛选、只读投影、SSE 序列化和 LangSmith 引用边界均有文档与回归证据 |

测试计划：

- 配置默认值、环境变量映射和非法值测试。
- TraceContext 必填字段、父子关系和序列化测试。
- 所有 Flag 关闭时全量回归。
- 干净环境安装和 import smoke test。
- pip check 或隔离环境一致性检查。

退出条件：

- 项目依赖声明与验证环境一致。
- 现有冲突已解决或有明确隔离方案。
- 所有 Flag 默认关闭。
- 全量回归不低于 14.2 基线。
- 尚未改变任何生产执行语义。

本批完成项：P0-01 至 P0-06、P0-08 至 P0-10；P0-07 仍阻塞于主服务生态协调升级。
当前测试结果（2026-09-08，本批 P0-10）：

- 后端契约专项：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe -m pytest -q tests/test_session_flow_projection.py`，9 passed。
- 前端 Flow 契约专项：`npm test -- --run src/features/flow/flow.test.ts`，1 file / 10 passed。
- 后端编译与全量回归：`compileall -q src tests` 通过；`pytest -q`，740 passed、8 skipped、1 warning（23.31s）。
- 前端全量回归与构建：2 files / 33 passed；`npm run build` 通过（3154 modules transformed）；保留既有主 chunk 大于 500 kB 警告，未将其误报为失败。
- 真实可执行链路：使用指定 Python3.11 启动 `uvicorn src.main:app --host 127.0.0.1 --port 18126`；`/api/v1/health` 200 且 `postgres_ok=true`；创建 Session、使用数据库默认模型发送真实消息、查询 Events/Snapshots/Flow 均 200，得到 24 条事件、1 个 Snapshot、9 个 Flow stages，并包含 `runtime.turn_completed`。

当前阻塞：主服务不能直接声明 Deep Agents extra，因为官方 0.7.13 要求 LangChain 至少 1.3.18、Core 至少 1.6.1、LangGraph 至少 1.2.11，和当前锁定组合不兼容；还需阶段 2—3 完成协调升级、Provider 真实链路和回滚验证。P0-10 已完成，不再是阶段 0 阻塞项。
回滚点：恢复 pyproject/config/契约变更；因为 Flag 默认关闭，不影响旧路径。
最近提交：`ef1a9bc`（P0-10 契约固化）；`99f95e8`（契约文档格式修正）。

#### P0-10 实施批次记录（2026-09-08）

- 当前状态：已完成。
- 本批目标：冻结 Event、Snapshot、Flow、SSE 和前端类型的真实跨层契约，为后续 LangChain/Deep Agents/LangSmith 适配提供稳定边界。
- 实际修改文件：`Agent_Server/docs/session-event-flow-contract-2026-09-08.md`、`Agent_Server/tests/test_session_flow_projection.py`、`agent_web/src/types.ts`、`agent_web/src/features/flow/flow.test.ts`。
- 完成任务 ID：P0-10。
- 未完成任务 ID：无（P0-07 仍为阶段级阻塞，但不属于本批范围）。
- 依赖或契约变化：`ExecutionEvent.payload` 前端类型由一层标量字典扩大为递归 JSON object；后端 JSONB、历史 API、SSE、Flow 投影均保持原有运行语义，未新增事件总线。
- 失败：首次专项测试错误地取了创建 Session 时产生的首条事件，已按稳定 Event ID 修正测试；该失败不是产品代码失败。
- 跳过：未执行真实 LangSmith 外部上报；没有 API Key，因此不能宣称外部 Trace 可访问。
- 回滚验证：默认关闭 Feature Flag 的旧路径全量回归通过；本批仅扩大前端类型与增加契约测试，可回滚至上一提交。
- 已知限制：LangSmith 真实网络上报、Deep Agents 主服务接入和外部 Trace 树仍分别留在阶段 1/4，不计入 P0-10 完成。
- 下一步：本批已提交；按方案先完成阶段 1 的真实 LangSmith 验证，或在用户批准并完成兼容升级矩阵后开始阶段 2 Model Adapter，不直接跳入 Deep Agents。

### 14.4 阶段 1：LangSmith 非阻塞观测

状态：进行中
目标：为当前运行链建立完整调用树，同时确保 LangSmith 永远不是执行主链硬依赖。

前置条件：

- 阶段 0 的可执行基线与 P0-10 契约固化已完成；P0-07 的生态版本阻塞单独保留，不得被误判为已关闭。
- LangSmith 项目、API Key 存储方式和数据驻留策略已确认。
- 脱敏字段白名单已评审。

具体任务：

| ID | 任务 | 目标文件/位置 | 状态 | 完成判据 |
|---|---|---|---|---|
| P1-01 | 定义 ObservabilityPort 与 No-op 实现 | application/observability | 已完成 | 关闭功能时零外部调用 |
| P1-02 | 实现 LangSmith Adapter | langsmith_adapter.py | 已完成 | SDK 隔离在适配器内部 |
| P1-03 | 实现输入输出脱敏与截断 | langsmith_adapter.py / OutputSafetyPolicy | 已完成 | 敏感样本测试通过 |
| P1-04 | 建立 Turn Root Trace | RuntimeService.execute_turn | 已完成 | 每个 turn 一个根运行 |
| P1-05 | 建立 Graph Node 子运行 | graph 节点边界 | 已完成 | 节点层级和状态正确 |
| P1-06 | 建立 Model Run | ModelRuntimeService.invoke | 已完成（本批） | Model 调用通过观测适配器创建独立子 Trace；Provider 调用语义保持不变 |
| P1-07 | 建立 Tool Run | ToolRuntimeService | 已完成（本批） | Tool handler 执行通过适配器创建独立子 Trace；权限、审批、Job 和错误语义保持不变 |
| P1-08 | 建立 Worker Nested Run | CoordinatorRuntimeService | 进行中（本批已实现边界） | Worker 边界复用现有父上下文并创建子 Trace；需真实 LangSmith 环境确认外部树层级 |
| P1-09 | 本地保存外部 Run 引用 | Event/Snapshot metadata | 进行中（本批已实现） | 已将 run_id、trace_id、dotted_order、URL 写入本地执行上下文和事件；需真实上报确认引用可访问 |
| P1-10 | 前端增加 Trace 深链接 | Flow Inspector/Snapshot 面板 | 已完成（可选链路） | 有 URL 时显示安全外链；未启用时不显示且本地 Flow 不受影响 |
| P1-11 | 增加超时、降级和结构化日志 | Adapter/主链边界 | 已完成 | 外部失败不阻断业务；errors_only 成功/失败语义已覆盖测试 |

测试计划：

- No-op 模式零网络调用。
- LangSmith 成功上报的调用树测试。
- DNS、超时、401、429、5xx 和 SDK 异常降级测试。
- 输入、输出、Authorization、Cookie、Token、密码和安全目标脱敏测试。
- Event/SSE/Snapshot 数量与关闭前对账。
- 多轮、审批恢复和 Worker 父子 Trace 测试。
- 开启前后 TTFT、完整耗时、内存和连接数对比。

退出条件：

- 本地 trace_id 能定位外部 Trace。
- 外部 Trace 的 Run 层级与实际执行一致。
- LangSmith 不可用时全量业务测试仍通过。
- 敏感字段未泄漏。
- Flow 仍以本地事件为事实源。
- 有一键关闭和回滚验证。

本批完成项：P1-01 至 P1-07、P1-10、P1-11；P1-08/P1-09 已完成代码接入和本地 SDK 协议验证，但仍等待真实外部环境验证；补齐 `errors_only` 成功/失败语义并修复根 Trace 未上报问题。
当前测试结果（2026-09-09，`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`）：`compileall` 通过；观测契约专项 19 passed（0.25s）；后端全量 747 passed、8 skipped、1 warning（17.86s）；前端 33 passed；`npm run build` 成功（3154 modules transformed，保留既有主 chunk 约 2.5 MB 警告）；最新真实 FastAPI 启动、健康检查（`postgres_ok=true`）、数据库默认模型会话、事件历史、Snapshot 和 Flow 查询均通过（41 events、1 Snapshot、9 stages），事件中包含 `runtime.turn_completed` 与 `turn.completed`。
新增锁定版 LangSmith SDK 协议验证（[证据文件](../Agent_Server/docs/python311-langsmith-sdk-protocol-2026-09-09.txt)）：在本地隔离 HTTP 端点收到 `/runs/multipart` 的 root Turn 与 `router` 子 Run 两批请求；子 Run 的 `parent_run_id` 指向 root，`trace_id` 可见，敏感输入、API Key 和密码值未出现。该结果证明当前 SDK 适配顺序和脱敏边界可执行，不等价于 LangSmith 云端可访问性。
第一次真实验证发现同步 `planner` 节点被错误 `await`，已修复包装器并完成回归；本批进一步发现并修复“未设置 `LANGCHAIN_TRACING_V2` 时 root trace 不会 post、只有子 trace 上报”的适配器根因。LangSmith 真实外部上报未执行（当前配置默认关闭且未提供 LangSmith API Key），因此本批不宣称外部父子树和 URL 可访问性已验证。
当前未完成项：开启前后 TTFT、完整耗时、内存、连接数和 Trace 上报队列积压的性能预算对比尚未执行；P1-08/P1-09 的真实云端父子树、Run 对账和敏感数据验证已通过。前端 Trace 深链接和 `errors_only` 已完成；`sampled` 由 LangSmith SDK 的 `tracing_sampling_rate` 实现，根 Trace 决定采样且子 Run 跟随，不再自研第二套采样器。
回滚点：关闭 LANGSMITH_ENABLED；删除 Adapter 接线不影响本地 Event/SSE。
最近提交：`720649e`（补齐 LangSmith 根轨迹故障降级验证）。

#### 阶段 1 根 Trace 与环境配置批次记录（2026-09-09）

- 当前状态：已完成。
- 本批目标：确保锁定版 LangSmith SDK 在应用未设置全局 `LANGCHAIN_TRACING_V2` 时仍会上报 Turn root，并验证 root→node 父子关系、脱敏和环境变量配置路径。
- 实际修改文件：`Agent_Server/src/application/observability/langsmith_adapter.py`、`Agent_Server/src/core/config.py`、`Agent_Server/tests/test_observability_contracts.py`、`Agent_Server/.env.example`；本机 `Agent_Server/.env` 已追加同名非敏感配置项，默认关闭且密钥为空。
- 完成任务 ID：P1-01、P1-02、P1-03、P1-04、P1-05、P1-06、P1-07、P1-10、P1-11（代码与本地协议证据）；P1-08/P1-09 的真实外部部分未完成。
- 依赖或契约变化：`LangSmithConfig.api_key` 使用 Pydantic `SecretStr` 从 `LANGSMITH__API_KEY` 读取；适配器优先使用该配置，仍兼容 `LANGSMITH_API_KEY` 进程变量，不把密钥放入 metadata。
- 通过：实际 SDK fake client 测试 19 passed；root 与 node 均产生请求，node `parent_run_id` 指向 root；`errors_only` 错误字段、传输失败降级和脱敏断言通过。
- 跳过：开启前后性能预算对比；真实 LangSmith 云端上报、控制台树层级和外部 URL 已在后续批次完成。
- 回滚验证：LangSmith 默认关闭时后端全量 747 passed，真实 FastAPI 默认模型会话仍成功；关闭观测不会改变本地 Event/SSE/Snapshot/Flow。
- 已知限制：本地协议端点只验证 SDK 请求形状和适配器行为，不证明云端鉴权、项目权限、数据驻留或网络重试策略。
- 下一步：用户在 `Agent_Server/.env` 写入 LangSmith Key 后，执行一次 full 模式真实会话并核对控制台父子树、外部 URL、本地 trace_id 对账和性能预算；通过后再进入阶段 2 Model Adapter。

#### 阶段 1 真实 LangSmith 云端验证批次记录（2026-09-09）

- 当前状态：进行中。
- 本批目标：使用用户配置的 LangSmith Key，在 full 模式启动真实服务并完成一次数据库默认模型会话；核对云端父子 Trace、本地外部 Run 引用、项目 URL 和敏感数据边界。
- 实际修改文件：`Agent_Server/.env`（本机忽略文件，仅将 `LANGSMITH__ENABLED` 从 `false` 改为 `true`、`LANGSMITH__TRACING_MODE` 从 `off` 改为 `full`；没有读取、打印或提交 Key）。
- 完成任务 ID：P1-08、P1-09（真实云端部分）；P1-11 的业务旁路继续通过。
- 未完成任务 ID：性能预算的多样本统计、阈值评审和上报队列积压监测；本批完成一对初步基准，不将其误记为性能门禁通过。
- 执行环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`；服务端口 `18131`，验证结束后已正常关闭。
- 执行命令：启动 `python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 18131`；调用 `/api/v1/health`、创建 Session、`POST /sessions/{id}/messages`、Events、Snapshots 和 Flow；再用锁定版 LangSmith SDK 按 `project_name` 与外部 `trace_id` 查询 Run。
- 通过：health 200 且 `postgres_ok=true`；消息调用成功；本地 26 条事件、1 个 Snapshot、10 个 Flow stages，含 `runtime.turn_completed`；本地 `observability.trace_linked` 记录 `run_id=01a083e5-a9b4-7d70-8d47-7d016f529c77` 与外部 Trace ID。
- 云端结果：项目 `enterprise-ai-qa-agent-dev` 查询到 20 个 Run、1 个根 Run `enterprise_ai_qa_agent.turn`，根 Run 状态 `success`；父子树包含 `LangGraph`、`context_builder`、`router`、`planner`、`permission_gate`、`prompt_assembler`、`model_invoker`、`enterprise_ai_qa_agent.node.model_call`、`finalizer`、`responder` 等节点；外部 Run URL 可由 SDK 生成并访问。
- 敏感数据检查：对本次 20 个云端 Run 的序列化输入、输出和元数据扫描，未发现实际 API Key、Bearer 凭证、Cookie、密码或密钥值；通用字段名如 `authorization_status` 属于业务安全状态，不是凭证泄露。
- 失败：第一次按本地业务 `trace_id` 查询云端返回 0 条；根因是本地业务 Trace ID 与 LangSmith SDK 生成的外部 Trace ID 不同。随后使用本地 `observability.trace_linked.external_trace_id` 查询成功；这验证了本地对账字段的必要性，不是上报失败。
- 跳过：开启前后性能对比；本批只证明一次真实云端成功路径和数据边界，不据此宣称性能预算达标。
- 回滚验证：服务已正常 shutdown；将 `LANGSMITH__ENABLED=false`、`LANGSMITH__TRACING_MODE=off` 即可回到本地 Event/SSE/Snapshot/Flow 路径，既有降级专项 19 passed 已覆盖。
- 下一步：执行开启/关闭观测的成对性能基准；性能预算达标后才关闭阶段 1并进入阶段 2。

#### 阶段 1 开关前后初步性能基准批次记录（2026-09-09）

- 当前状态：进行中。
- 本批目标：在同一 Python3.11、同一数据库默认模型和同一短消息下，分别关闭和开启 LangSmith，记录 TTFT、完整运行时间、事件数量、进程内存和本地连接数。
- 实际修改文件：`Agent_Server/.env`（本机忽略文件，仅在两轮之间切换 `LANGSMITH__ENABLED` 与 `LANGSMITH__TRACING_MODE`；验证结束保持 `true/full`，Key 未读取或输出）。
- 关闭观测样本：Session `2d77d42a-5e4f-4c32-910e-971ba3d690ce`；37 events；首 `assistant.stream.started` 约 14,666.11 ms；首 delta 约 14,674.34 ms；`turn.completed` 约 16,284.03 ms；进程工作集 240.50 MB；本地连接 1；包含 `runtime.turn_completed`。
- 开启观测样本：Session `8f21dfa4-48fa-45e9-8da1-264e91806119`；25 events；首 `assistant.stream.started` 约 9,583.10 ms；首 delta 约 9,589.88 ms；`turn.completed` 约 9,736.05 ms；进程工作集 258.11 MB；本地连接 1；包含 `runtime.turn_completed` 和 1 个 `observability.trace_linked`。
- 通过：两种开关均能启动服务、连接 PostgreSQL、调用默认模型并完成本地事件/快照链；full 模式确实产生外部 Trace 关联事件。
- 结果解释：本对样本的 full 模式内存比 off 高约 17.61 MB；耗时指标未显示稳定的观测开销方向，原因是单次真实模型响应存在明显网络/模型波动，不能据此宣称性能预算达标或 LangSmith 无开销。
- 跳过：多样本 P50/P95/P99、TTFT 分布、持续运行内存、连接池变化和 Trace 上报队列积压；方案没有预先冻结数值阈值，必须先补齐基线窗口与阈值再验收。
- 回滚验证：关闭样本和开启样本均完成；服务均收到正常 shutdown；将 `.env` 切回 `LANGSMITH__ENABLED=false`、`LANGSMITH__TRACING_MODE=off` 即可关闭外部观测。
- 下一步：补充至少 5 轮开关交替样本，记录 P50/P95/P99 和错误率；再按性能预算评审是否满足阶段 1退出条件。性能统计通过前不进入阶段 2。

#### 阶段 1 五轮开关交替性能统计批次记录（2026-09-09）

- 当前状态：进行中。
- 本批目标：补齐 full/off 各 5 个真实会话样本，统计 TTFT、完成时间、内存范围和错误情况，避免单轮模型波动被误判为观测开销。
- 实际修改文件：`Agent_Server/.env`（本机忽略文件；采样期间按模式切换，完成后恢复 `LANGSMITH__ENABLED=true`、`LANGSMITH__TRACING_MODE=full`）。
- 测试环境：指定 Python3.11、PostgreSQL 默认模型、单进程 Uvicorn；full 端口 `18134`、off 端口 `18135`；两轮服务均正常 shutdown。
- 样本结果：full 新增 4 轮均为 25 events、`trace_linked=1`；off 新增 4 轮均无 `trace_linked`，事件数 24/24/24/39；两种模式请求错误数均为 0，均包含 `runtime.turn_completed`。
- 统计口径：P50 使用中位数；P95 在 5 个样本上采用排序后第 4 个值，作为小样本近似，不宣称生产级置信区间。
- Full 统计（含前一轮样本）：TTFT 均值 11,421.06 ms、P50 9,583.10 ms、P95 11,735.48 ms、范围 3,721.29–23,520.94 ms；完成时间均值 11,699.26 ms、P50 9,736.05 ms、P95 12,159.50 ms、范围 3,954.38–23,848.41 ms；进程工作集范围 258.11–262.13 MB、均值 260.48 MB。
- Off 统计（含前一轮样本）：TTFT 均值 17,158.19 ms、P50 14,666.11 ms、P95 24,955.25 ms、范围 7,727.99–29,762.72 ms；完成时间均值 17,735.78 ms、P50 16,284.03 ms、P95 25,321.74 ms、范围 7,980.35–30,139.45 ms；进程工作集范围 240.08–243.20 MB、均值 241.39 MB。
- 结果解释：本批未观察到 full 模式的稳定时延增幅；full 内存均值比 off 高约 19.09 MB（约 7.91%），需要持续窗口验证。由于样本量小、模型/网络抖动大且尚未定义阈值，不能据此宣称性能预算达标。
- 跳过：Trace 上报队列积压的独立监测、持续 30 分钟内存曲线、P99 和并发负载；这些需要明确压测窗口与阈值，不能用 5 次串行请求替代。
- 回滚验证：关闭模式 5 轮和 full 模式 5 轮均通过；当前本机 `.env` 已恢复 full，若需回滚可切回 `false/off`。
- 下一步：由项目负责人确认 TTFT、完整耗时、内存和队列积压阈值；确认后执行带阈值的性能验收或批准阶段 1关闭。阈值确认前不进入阶段 2。

#### 阶段 1 full Trace 上传闭环与相对门槛评审（2026-09-09）

- 当前状态：进行中。
- 本批目标：对 5 个 full 性能样本逐一使用本地 `external_trace_id` 查询 LangSmith，确认本地事件、云端 Run 数量、根 Run 和成功状态一致，并建立仅用于本次接入验收的相对性能门槛。
- 云端对账结果：5/5 个 full 会话均存在 `observability.trace_linked`；每个外部 Trace 均查询到 20 个 Run、1 个根 Run、20/20 success；因此本批未发现 Trace 丢失、根 Run 缺失或异步上传积压。查询使用锁定版 LangSmith SDK，服务验证后已正常关闭。
- 临时相对门槛（不是业务 SLA，待项目方确认）：full 错误率必须为 0；`observability.trace_linked` 覆盖率必须为 100%；full TTFT/完成时间 P95 不超过 off 基线 P95 的 1.2 倍；full 进程工作集均值增幅不超过 15%；每个本地外部 Trace 必须至少对应 1 个成功根 Run。
- 对门槛的当前结果：错误率 0；Trace 关联 100%；full TTFT P95 11,735.48 ms < off P95 24,955.25 ms；full 完成时间 P95 12,159.50 ms < off P95 25,321.74 ms；full 内存均值比 off 高约 7.91%，低于临时 15% 门槛；5 个 full Trace 均完成云端对账。上述相对门槛全部通过，但不替代正式 SLA。
- 未完成：Trace 上报队列的直接深度/积压指标、持续运行内存曲线、并发负载下 P95/P99，以及正式业务阈值确认。当前 SDK 版本的旁路上传不提供项目内可直接读取的队列深度，因此不能伪造该指标。
- 回滚验证：本批只读查询云端和本地历史数据，没有修改业务代码；`.env` 当前保持 `LANGSMITH__ENABLED=true`、`LANGSMITH__TRACING_MODE=full`，切回 `false/off` 可回滚。
- 下一步：等待项目方确认临时相对门槛是否可作为阶段 1接入门槛；若确认，再补一轮持续/并发专项并关闭阶段 1，否则按项目方给定阈值复验。阶段 2仍未开始。

#### 阶段 1 并发可执行性与 Trace 对账批次记录（2026-09-09）

- 当前状态：进行中。
- 本批目标：在 `LANGSMITH__ENABLED=true`、`TRACING_MODE=full` 下，以 3 个并发真实会话验证服务可执行性、终态、事件完整性和云端 Trace 对账。
- 执行环境：指定 Python3.11、单进程 Uvicorn（端口 `18137`）、PostgreSQL 默认模型；验证后正常 shutdown。
- 通过：3/3 会话创建和消息请求成功；3/3 包含 `runtime.turn_completed`；事件数为 25、25、40；3/3 有 `observability.trace_linked`；业务错误率 0。
- 性能观察：并发请求 wall time 为 18,868.26–45,788.49 ms；`turn.completed` 为 9,392.98–32,969.00 ms。该结果用于并发可执行性和波动观察，不替代正式并发 P95/P99 压测。
- 云端对账：3/3 外部 Trace 均可查询；云端 Run 数分别为 20、20、42；每组均有 1 个根 Run，所有 Run 状态均为 `success`。最高 42 Run 的会话说明工具/节点分支会改变树规模，但没有出现上报失败。
- 失败：无业务失败；首次云端对账脚本连接已关闭的临时端口，随后启动只读服务重试成功，该脚本问题不计入系统错误率。
- 跳过：持续 30 分钟 soak、并发阶梯、P99、队列深度和资源上限测试；需要项目方确认并发目标与持续时间，不能擅自扩大外部模型调用量。
- 回滚验证：full 模式并发链路和服务 shutdown 均通过；切换 `.env` 到 `false/off` 可回滚本地观测路径。
- 下一步：确认正式并发目标、P95/P99 和 soak 阈值；阈值确认前阶段 1保持进行中，阶段 2不启动。

#### 阶段 1 真实服务可执行性复验批次记录（2026-09-09）

- 当前状态：进行中。
- 本批目标：在 LangSmith 默认关闭的前提下，用指定 Python3.11 启动真实 FastAPI，调用数据库默认模型完成一轮会话，并核对本地事件、终态、Snapshot 和 Flow，证明观测旁路未破坏主执行链。
- 实际修改文件：无（仅运行验证；服务使用的临时端口为 `18130`，验证结束后已正常关闭）。
- 完成任务 ID：P1-11（业务主链失败降级和关闭路径的运行时复验）。
- 未完成任务 ID：P1-08、P1-09 的真实 LangSmith 云端上报、外部父子树和 URL 可访问性。
- 执行命令：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 18130`；随后调用 `/api/v1/health`、`POST /api/v1/sessions`、`POST /api/v1/sessions/{session_id}/messages`、`GET /events/history?limit=0`、`GET /snapshots?limit=0` 和 `GET /flow`。
- 通过：health 200 且 `postgres_ok=true`；创建会话、默认模型消息、Events、Snapshot、Flow 均返回 200；得到 41 条事件、1 个 Snapshot、9 个 Flow stages；原始事件含 `runtime.turn_completed` 和 `turn.completed`；Uvicorn 收到正常关闭信号并完成 application shutdown。
- 业务结果：模型返回安全策略拒绝说明，因为测试提示要求模型无证据地声称“验证成功”；该结果符合当前系统的证据约束，不能把它记为模型按字面回复成功。
- 失败：首次脚本将 PowerShell 的事件数组包成单元素数组，导致事件类型读取为空；重新读取原始 JSON 后确认是验收脚本解析问题，不是服务或事件存储失败。
- 跳过：LangSmith 云端调用、控制台父子树、外部 URL 和开启观测性能对比；当前 `Agent_Server/.env` 中 `LANGSMITH__API_KEY` 为空且 `LANGSMITH__ENABLED=false`。
- 回滚验证：默认关闭观测路径可启动、可调用、可落库；未改动代码和数据结构，不需要回滚。
- 下一步：先由用户在本机 `.env` 配置 LangSmith 项目和 Key，再复验 P1-08/P1-09；在阶段 1 退出条件满足前不进入阶段 2。

#### 阶段 1 并发阶梯、短时 soak 与资源观察批次记录（2026-09-09）

- 当前状态：进行中。
- 本批目标：补齐并发 `1、2、3、5` 阶梯，记录真实默认模型会话的终态和 Trace 关联；执行连续低速请求观察短时稳定性、进程工作集和旁路 Trace 是否持续落地。
- 执行环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`、单进程 Uvicorn `127.0.0.1:18140`、PostgreSQL 默认模型、`LANGSMITH__ENABLED=true`、`LANGSMITH__TRACING_MODE=full`；测试结束前服务仍保持运行，待本批收尾后发送 Ctrl+C 并核对正常 shutdown。
- 并发阶梯结果：每级 3 个真实会话，共 12 个会话；1/2/3/5 级均为 3/3 `runtime.turn_completed`、3/3 `observability.trace_linked`，业务错误率 0。按事件时间计算的 `turn.started→runtime.turn_completed` 小样本统计如下：并发 1 的 P50 34.03s、P95/P99 近似 36.74s；并发 2 的 P50 34.79s、P95/P99 近似 52.49s；并发 3 的 P50 22.20s、P95/P99 近似 31.42s；并发 5 的 P50 35.62s、P95/P99 近似 50.11s。每级仅 3 个样本，P95/P99 只能作为最大值近似，不能视为生产容量结论。
- 并发事件与资源：每个成功会话均能在本地事件中找到终态和 Trace 关联；事件数随工具/节点分支变化，未发现因并发导致事件终态缺失。测试过程中服务进程工作集约 96.18 MB（单点采样），不足以替代正式资源曲线。
- 短时 soak 结果：连续创建 10 个低速真实会话；前 9 个均为 HTTP 200、25 events、1 个 `runtime.turn_completed`、1 个 `observability.trace_linked`；第 10 个初次客户端编排在创建后未提交消息，形成 `idle` 会话，根因是客户端测试编排超时/中断而非服务错误；随后新增 `gate-soak-retry` 重试成功（HTTP 200、25 events、终态和 Trace 均存在）。因此有效请求为 10/10 成功，另有 1 个无消息的测试残留会话，不计入业务错误率。
- Trace 队列验证：锁定版 SDK 未提供项目内可靠的队列深度读取接口；本批使用本地 `observability.trace_linked` 与云端 Run 查询作为间接对账依据。该方法能证明已关联 Trace 可查询，不能宣称直接队列无积压。
- 通过：并发阶梯业务错误率 0；12/12 并发会话终态和本地 Trace 关联完整；短时有效请求 10/10 成功；未观察到 LangSmith 旁路阻断主链。
- 未完成：正式业务并发目标和资源上限尚未由项目方确认；未执行完整 30 分钟持续窗口，因此不能把短时 soak 宣称为 30 分钟 soak；P99 仍为小样本近似；队列深度无直接指标。
- 回滚验证：本批未修改代码或依赖；将 `.env` 切换到 `LANGSMITH__ENABLED=false`、`LANGSMITH__TRACING_MODE=off` 即可回滚观测旁路。服务收尾时必须确认 `Application shutdown complete`。
- 下一步：完成服务正常 shutdown 取证；由项目方确认正式并发/资源/持续时间阈值后，决定阶段 1 是否关闭。阶段 2 继续保持“未进行”，不安装 Deep Agents、不升级 LangChain 生态版本。

#### 长任务 L0/L1：统一执行时间模型与现有 TestRun 持久化边界（2026-09-09）

- 当前状态：进行中。
- 本批目标：基于现有 `TestRun`、`TestRunItem`、`TestRunAttempt`、租约、心跳、审批等待和恢复机制，增加长任务可审计的活动执行时长、等待时长和墙钟时长；不新建重复任务表，不改变 LangChain/LangGraph/LangSmith 版本和现有执行路径。
- 实际修改文件：`Agent_Server/src/schemas/run_management.py`、`Agent_Server/src/application/test_runs/timing.py`、`Agent_Server/src/application/test_runs/run_store.py`、`Agent_Server/tests/test_test_run_timing.py`。
- 依赖或契约变化：Run、Item、Attempt JSONB 记录新增 `active_duration_ms`、`waiting_duration_ms`、`paused_duration_ms`、`wall_clock_duration_ms` 及活动/等待起止字段；旧记录缺失字段时由 Pydantic 默认值兼容读取。LangSmith 暂不改造，后续使用这些本地事实字段对齐阶段 Run 时长。
- 实现规则：进入 `running` 设置 `active_started_at`；进入 `waiting_approval` 结算活动时长并开始等待计时；审批恢复结算等待时长；完成或阻断时结算未闭合计时并写入墙钟时长；Run 汇总从持久化 Item 聚合活动/等待/墙钟时长。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest tests/test_test_run_timing.py tests/test_test_run_lifecycle.py tests/test_test_run_postgres_claim.py -q`；`python -m pytest -q`。
- 通过：定向测试 28 passed；后端全量 749 passed、8 skipped、1 warning；编译通过；`git diff --check` 通过。
- 失败：无。
- 跳过：尚未实现暂停/恢复专用状态字段和 4/24 小时真实长任务压测；当前已有审批等待计时先纳入 `waiting_duration_ms`，正式 pause 状态将在任务状态机扩展批次实现。
- 已知限制：PostgreSQL 运行级刷新为按 Item JSONB 聚合，长任务高频心跳仍会产生写放大；本批未改变事件归档、Artifact 外置和 LangSmith Trace 分段策略。
- 回滚验证：未修改数据库表结构，只扩展 JSONB 记录和 Schema 默认值；回滚代码即可读取旧记录，旧记录字段缺失自动按 0/None 处理。
- 下一步：补充长任务专用暂停/恢复语义、阶段 Checkpoint 与 Attempt 恢复测试，再把 `run_id/run_item_id/attempt_id/thread_id` 绑定到分段 LangSmith Trace；阶段 2 继续保持未进行。

#### 长任务 L2：Attempt 检查点持久化（2026-09-09）

- 当前状态：进行中。
- 本批目标：复用现有 `TestRunAttempt` JSONB 记录增加最近检查点，避免新增重复任务表；检查点必须受当前租约保护，失效 Worker 或旧版本不能覆盖新 Attempt 状态。
- 实际修改文件：`Agent_Server/src/schemas/run_management.py`、`Agent_Server/src/application/test_runs/run_store.py`、`Agent_Server/src/application/test_runs/run_service.py`、`Agent_Server/src/api/routes/run_management.py`、`Agent_Server/tests/test_test_run_lifecycle.py`。
- 新增契约：`POST /api/v1/run-items/{item_id}/checkpoint`；请求包含 `lease_token`、`checkpoint_key`、`checkpoint_payload` 和可选 `checkpoint_version`；Attempt 持久化 `checkpoint_version`、`checkpoint_key`、`checkpoint_payload`、`checkpoint_at`。
- 一致性规则：默认版本为当前版本加一；显式版本必须严格大于已保存版本；保存前校验 Item 状态和租约；保存同时刷新 Attempt 心跳时间；检查点事件写入现有 TestRun 事件通路。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest tests/test_test_run_lifecycle.py tests/test_test_run_timing.py -q`；`python -m pytest -q`。
- 通过：定向测试 14 passed；后端全量 750 passed、8 skipped、1 warning；编译通过。
- 失败：无。
- 跳过：本批未实现独立 pause 状态、检查点恢复执行器和 4/24 小时真实压测；当前只完成检查点写入与租约保护。
- 回滚验证：无数据库结构变更；旧 Attempt JSONB 记录缺少检查点字段时按默认值兼容读取，回滚代码不会破坏历史记录。
- 下一步：实现从最近检查点恢复的新 Attempt 流程，并增加 Worker 强杀/租约过期后的恢复回归；随后接入分段 LangSmith Trace 和 `thread_id` 关联。

#### 长任务 L2：租约过期后的检查点恢复（2026-09-09）

- 当前状态：进行中。
- 本批目标：让租约过期后的新 Attempt 继承最近有效检查点，同时保留旧 Attempt 的历史身份，避免复用旧 Attempt 或从头盲重跑。
- 实际修改文件：`Agent_Server/src/schemas/run_management.py`、`Agent_Server/src/application/test_runs/run_store.py`、`Agent_Server/tests/test_test_run_lifecycle.py`。
- 实现规则：`recover_expired` 仍将旧 Attempt 标记为 `expired` 并把 Item 放回队列；下一次原子 claim 创建全新 Attempt，复制最近过期 Attempt 的 checkpoint 字段，并写入 `recovered_from_attempt_id`；新 Attempt 使用新的 lease token，旧 token 不能继续操作。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest tests/test_test_run_lifecycle.py tests/test_test_run_timing.py -q`；`python -m pytest -q`。
- 通过：定向测试 15 passed；后端全量 751 passed、8 skipped、1 warning；编译通过。
- 失败：无。
- 跳过：恢复后尚未自动执行“从 checkpoint_payload 继续未完成阶段”，目前由新 Attempt 暴露恢复上下文，执行器消费逻辑待下一批接入。
- 已知限制：当前只继承最近一个 expired Attempt 的检查点；检查点 payload 的业务 schema 和幂等动作确认仍由具体模式执行器负责。
- 回滚验证：无数据库结构变更；旧 JSONB Attempt 记录缺少恢复字段时按默认值兼容读取。
- 下一步：在 `execution_service` 中消费恢复检查点，增加恢复阶段事件和幂等步骤跳过规则，再进行 Worker 强杀恢复测试；随后接入 LangSmith 分段 Trace。

#### 长任务 L2：执行入口消费恢复检查点（2026-09-09）

- 当前状态：进行中。
- 本批目标：让新 Attempt 领取到的检查点真正进入测试执行上下文，使具体模式适配器能够基于已完成步骤、证据引用和恢复来源继续执行；不在通用层擅自跳过业务步骤。
- 实际修改文件：`Agent_Server/src/application/test_runs/run_store.py`、`Agent_Server/src/application/test_runs/run_service.py`、`Agent_Server/src/application/test_runs/execution_service.py`、`Agent_Server/tests/test_test_run_lifecycle.py`。
- 实现规则：新增 `get_latest_attempt` 读取当前条目最新 Attempt；执行入口在启动后加载检查点，将 `attempt_id`、`recovered_from_attempt_id`、版本、key、payload 和时间放入 `trusted_context_bundle.execution_checkpoint`；存在检查点时记录 `test_run_execution_checkpoint_loaded` 日志。模式适配器负责依据自身业务契约消费该上下文，通用层不做误判式跳步。
- 兼容修复：执行服务对旧版测试替身/非 TestRunService 实现使用可选方法调用，保持现有调用方兼容；真实 TestRunService 始终提供该读取能力。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest -q`。
- 通过：后端全量 751 passed、8 skipped、1 warning；编译通过。
- 失败：首次全量测试发现 7 个旧测试替身没有 `get_latest_attempt`，根因是新增服务协作方法未对旧替身做兼容；已改为可选调用并复验通过，不是生产路径失败。
- 跳过：尚未实现模式级 checkpoint payload schema、幂等动作跳过和 Worker 强杀真实恢复压测；本批只完成恢复上下文传递。
- 回滚验证：无数据库结构变更；旧 Attempt 和旧测试替身均可继续工作。
- 下一步：为 `CaseExecutionAdapter` 增加明确的恢复上下文协议和步骤幂等判定，再执行 Worker 强杀、进程重启和长任务恢复专项。

#### 长任务 L2：模式适配器恢复上下文协议（2026-09-09）

- 当前状态：进行中。
- 本批目标：将持久化检查点从执行入口明确传递到 `CaseExecutionAdapter` 的可信上下文，使具体模式能够基于自身步骤和幂等契约决定恢复策略；通用层不自动跳过测试步骤。
- 实际修改文件：`Agent_Server/src/application/test_runs/case_execution.py`、`Agent_Server/src/application/test_runs/execution_service.py`、`Agent_Server/src/application/test_runs/run_service.py`、`Agent_Server/src/application/test_runs/run_store.py`、`Agent_Server/tests/test_case_execution_adapter.py`。
- 实现规则：执行入口读取最新 Attempt；存在检查点时写入 `trusted_context_bundle.execution_checkpoint`；适配器仅复制安全白名单上下文到 `ToolExecutionContext.context_bundle`；旧测试替身无 `get_latest_attempt` 时保持兼容。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest tests/test_case_execution_adapter.py tests/test_case_execution_service.py tests/test_test_run_lifecycle.py -q`。
- 通过：定向测试 46 passed；编译通过。
- 失败：无生产路径失败；初次全量测试发现 7 个旧测试替身缺少新读取方法，已通过可选调用兼容处理并在前批全量回归中验证。
- 跳过：尚未实现模式级步骤幂等判定、自动跳过和 Worker 强杀真实恢复；当前只完成恢复上下文协议传递。
- 已知限制：`checkpoint_payload` 的字段语义仍由各测试模式定义，通用层不会猜测 `step`、`action` 或证据状态。
- 回滚验证：无数据库结构变更；旧适配器和旧 Attempt 记录可继续执行。
- 下一步：为至少一个实际模式定义版本化 checkpoint payload schema 和幂等步骤判定，随后执行 Worker 强杀/进程重启恢复实测；LangSmith 分段 Trace 仍待后续批次。

#### 长任务 L2：API 测试模式恢复上下文接入（2026-09-09）

- 当前状态：进行中。
- 本批目标：为已有 `api_testing` 模式接入版本化恢复上下文，确保持久化 Attempt 检查点可以传递到现有 API Runner；不在本批自动跳过 HTTP 请求，避免恢复时重复外部副作用。
- 实际修改文件：`Agent_Server/src/application/test_runs/case_execution.py`、`Agent_Server/src/modes/api_testing_mode/runtime.py`、`Agent_Server/tests/test_case_execution_adapter.py`。
- 实现规则：`CaseExecutionAdapter.build_invocation` 将白名单 `execution_checkpoint` 放入 Runner 参数和 `ToolExecutionContext`；`ApiTestingModeRuntime._execute_dispatched_task` 将其恢复到 `ApiTestTask.execution_checkpoint`。现有 API 测试运行时已有 checkpoint 字段和阶段回调，因此复用现有状态机。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest tests/test_case_execution_adapter.py tests/test_case_execution_service.py tests/test_api_mode_skills.py -q`；`python -m pytest -q`。
- 通过：定向测试 39 passed；后端全量 751 passed、8 skipped、1 warning；编译通过。
- 失败：无。
- 跳过：尚未定义 API 请求级幂等键和“请求已完成”判定，因此恢复执行仍由 Runner 根据 checkpoint 业务语义决定，不自动跳过或重放请求。
- 已知限制：当前检查点只传递到 API Runner；还未将每个 HTTP 请求的幂等结果、响应 Artifact 和断言阶段拆成可恢复子阶段。
- 回滚验证：无数据库结构变更；未提供 checkpoint 时行为与原路径一致。
- 下一步：为 API task 定义版本化 checkpoint payload 和请求结果幂等协议，再进行真实 Worker 强杀/进程重启恢复测试；LangSmith 分段 Trace 仍待后续批次。

#### 长任务 L2：API 请求级幂等标识（2026-09-09）

- 当前状态：进行中。
- 本批目标：为已有 `api_testing` 任务定义稳定的逻辑请求身份，确保 Attempt 重试不会因为尝试次数变化而生成不同幂等键；本批不自动跳过 HTTP 请求。
- 实际修改文件：`Agent_Server/src/modes/api_testing_mode/campaign_state.py`、`Agent_Server/src/modes/api_testing_mode/runtime.py`、`Agent_Server/tests/test_api_mode_skills.py`。
- 实现规则：`ApiTestTask.idempotency_key` 由 `task_id + method + full_url/path` 构成，明确排除 `attempts`；恢复检查点继续保留任务上下文；Runner 输出携带幂等键，便于后续结果和证据对账。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest tests/test_api_mode_skills.py tests/test_case_execution_adapter.py -q`；`python -m pytest -q`。
- 通过：定向测试 31 passed；后端全量 752 passed、8 skipped、1 warning；编译通过。
- 失败：无。
- 跳过：尚未建立“幂等键对应的已完成响应、断言结果和 Artifact 完整证据”存储，因此不能安全地自动跳过请求；当前仍由 Runner 正常执行。
- 已知限制：幂等键未包含请求体摘要，适用于当前固定 `task_id` 语义；若未来同一 task_id 允许动态请求体变更，必须先扩展版本化输入摘要并同步契约。
- 回滚验证：不提供 checkpoint 时行为与原路径一致；幂等键是附加输出字段，不改变现有状态判定。
- 下一步：将 API 响应、断言和 Artifact 摘要写入版本化 checkpoint payload，并在只读/幂等请求上增加“证据完整才可跳过”的判定；随后执行 Worker 强杀恢复实测。

#### 长任务 L2：API 请求证据完整性恢复判定（2026-09-09）

- 当前状态：进行中。
- 本批目标：在 API 测试任务恢复时，仅当检查点具备完整且匹配的请求证据才复用结果；缺少证据时继续真实请求，避免把状态字段误当成已完成。
- 实际修改文件：`Agent_Server/src/modes/api_testing_mode/executor.py`、`Agent_Server/src/modes/api_testing_mode/runtime.py`、`Agent_Server/src/modes/api_testing_mode/campaign_state.py`、`Agent_Server/tests/test_api_task_executor_recovery.py`。
- 复用条件：`completed_request.idempotency_key` 必须匹配当前任务；状态必须为 `completed`；必须存在整数 HTTP 状态、响应体和断言列表；复用时恢复响应头、响应体、断言结果、耗时和完成时间。
- 检查点增强：API 模式的 campaign checkpoint 增加 `completed_task_ids`、当前任务幂等键，以及任务完成时的完整 `completed_request` 证据。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest tests/test_api_task_executor_recovery.py tests/test_api_mode_skills.py tests/test_case_execution_adapter.py -q`；`python -m pytest -q`。
- 通过：定向测试 33 passed；后端全量 754 passed、8 skipped、1 warning；编译通过。
- 失败：首次专项测试发现恢复分支缺少 `deepcopy` 导入，根因是新分支使用了深复制但未同步 import；已修复并重新通过。
- 跳过：尚未把所有 API 请求结果写入独立 Attempt checkpoint 表；当前通过现有 checkpoint payload 传递，且只在证据完整时复用。
- 已知限制：当前请求幂等键不包含请求体摘要；若未来同一 task_id 允许动态请求体变化，必须扩展输入摘要后才能继续复用。
- 回滚验证：缺少 `completed_request` 或证据不完整时自动回到原始 HTTP 执行路径；不改变现有失败和断言判定。
- 下一步：执行真实 Worker 强杀/进程重启恢复测试，确认新 Attempt 能携带完整 API 证据并避免重复请求；完成后再设计 LangSmith 长任务分段 Trace。

#### 长任务 L2：恢复领取审计事件（2026-09-09）

- 当前状态：进行中。
- 本批目标：让普通 Claim 与恢复 Claim 在事件和日志中可区分，为后续 Worker 强杀、租约过期和 LangSmith 对账提供明确证据。
- 实际修改文件：`Agent_Server/src/application/test_runs/run_service.py`。
- 实现规则：`test_run_items_claimed` 日志增加 `recovered_count`；Claim 事件增加 `recovered_attempts`，包含 `run_item_id`、新旧 Attempt ID 和 checkpoint 版本；首次领取不生成恢复项，保持事件结构兼容。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m compileall -q src`；`python -m pytest -q`。
- 通过：后端全量 754 passed、8 skipped、1 warning；编译通过。
- 失败：无。
- 跳过：尚未进行真实独立 Worker 进程强杀；当前恢复行为已由 InMemory 生命周期测试覆盖，真实多进程接管测试待运行环境准备后执行。
- 回滚验证：仅增加日志和事件 payload 可选字段，不改变领取、租约和状态迁移。
- 下一步：继续补充 Stage/Tool/Model 子 Run 与 PostgreSQL 多进程接管专项；真实独立 Worker 强杀和 4/24 小时 soak 仍需在具备可控外部运行环境后执行。

#### 长任务 L2：服务重启后的 Attempt 接管回归（2026-09-09）

- 当前状态：已完成（生命周期级集成测试）；真实独立进程验收未完成。
- 本批目标：验证服务重新初始化时会执行过期租约恢复，并由新 Worker 创建新 Attempt 继承最近检查点；旧 Worker 的 lease token 不得继续写入。
- 实际修改文件：`Agent_Server/tests/test_test_run_lifecycle.py`。
- 测试场景：第一服务实例领取条目并保存 checkpoint；推进时钟使 lease 过期；创建第二个 `TestRunService` 实例并调用 `initialize()`；旧 token heartbeat 必须返回 409；新 Worker 领取后 Attempt ID 必须变化，`recovered_from_attempt_id`、checkpoint version 和 payload 必须一致。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m pytest tests/test_test_run_lifecycle.py -q`（工作目录 `Agent_Server`）。
- 通过：14 passed。
- 失败：无。
- 未覆盖：真实 OS 进程强杀、PostgreSQL 事务并发、网络中断和 4/24 小时 soak；这些需要可控外部运行环境，不能用内存 Store 结果替代。
- 下一步：先实现 LangSmith 分段 Trace 契约与失败隔离，再安排 PostgreSQL 多进程接管专项。

#### 长任务 L2：TestRunItem 分段 LangSmith Trace（2026-09-09）

- 当前状态：已完成（代码、真实服务主链与回归测试）；本批不重复执行云端查询。
- 本批目标：以 `TestRunItem` 为 bounded Trace 单位，把长任务拆成可关联的观测片段；Trace 不覆盖审批等待、租约等待或结果落库，避免把 wall-clock 时长误报成 Agent 执行时长。
- 实际修改文件：`Agent_Server/src/application/observability/trace_context.py`、`Agent_Server/src/application/observability/langsmith_adapter.py`、`Agent_Server/src/application/test_runs/case_execution.py`、`Agent_Server/src/application/test_runs/execution_service.py`、`Agent_Server/src/application/runtime/tool_runtime_service.py`、`Agent_Server/src/graph/nodes/model_invoker.py`、`Agent_Server/src/main.py`、`Agent_Server/tests/test_observability_contracts.py`、`Agent_Server/tests/test_case_execution_service.py`、`Agent_Server/tests/test_case_execution_adapter.py`。
- 实现规则：稳定上下文新增 `run_item_id`、`attempt_id`、`thread_id`；新增 `enterprise_ai_qa_agent.test_run_item` Trace；Trace 仅包住 `CaseExecutionAdapter.execute`；Item 上下文继续传播到 Tool/Model 子 Run；工具执行和断言评估分别复用现有 `trace_node` 形成 Stage/Assertion 子 Run；输入只传 run/item/attempt/mode 摘要，输出只传状态、摘要和 job ID，并沿用既有脱敏与失败隔离；LangSmith 不参与业务恢复和结果判定。
- 通过：`python -m compileall -q src`；定向测试 55 passed；后端全量测试 758 passed、8 skipped、1 warning；真实 Uvicorn + PostgreSQL 默认模型链路 health 200、消息 200、事件 67 条、Flow 11 stages，服务正常关闭。
- 失败：无。
- 未覆盖：本批未重复执行真实 LangSmith 云端上传；LangSmith 限流/断网专项、PostgreSQL 多进程接管和 4/24 小时 soak 仍未完成。Stage/Assertion 子 Run 已建立最小边界，完整证据输出和更细粒度阶段拆分仍待后续批次。
- 下一步：增加 LangSmith 客户端断开、重复提交专项，并安排 PostgreSQL 多进程接管与 4/24 小时长任务验收；保持本地 TestRun 作为唯一恢复事实源。

#### 长任务 L2 旁路故障隔离批次（2026-09-09）

- 当前状态：已完成（代码路径与回归测试）；真实网络断开、限流和多进程接管仍未完成。
- 本批目标：验证 LangSmith Trace Scope 创建失败时不改变 TestRunItem 业务结果，确保观测系统是旁路而非执行事实源。
- 实际修改文件：`Agent_Server/src/application/test_runs/execution_service.py`、`Agent_Server/tests/test_case_execution_service.py`。
- 完成任务 ID：L2 观测失败隔离；重复回调既有幂等契约未修改。
- 根因修复：执行服务新增安全 Trace Scope 边界，仅隔离 Scope 进入和正常退出异常；Adapter 执行体内异常仍交由既有业务错误处理，避免吞掉真实测试失败。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，Python 3.11.15。
- 执行命令：`python -m pytest tests/test_case_execution_service.py -k "observability" -q`；`python -m pytest tests/test_observability_contracts.py -q`；`python -m pytest -q`；`python -m compileall -q src`（工作目录均为 `Agent_Server`）。
- 通过：旁路定向 2 passed；观测契约 21 passed；后端全量 759 passed、8 skipped、1 warning；编译通过。
- 失败：无。
- 跳过：真实 LangSmith 断网/限流、PostgreSQL 多进程 Worker 接管、4/24 小时 soak。
- 回滚验证：关闭 `LANGSMITH__ENABLED` 或将 `LANGSMITH__TRACING_MODE=off` 后仍走原有本地执行路径；本批未改依赖和数据库 schema。
- 已知限制：客户端在 Scope 退出时的异常只记录日志，不提供上传重试队列；长任务恢复仍以本地 Attempt/Checkpoint 为准。
- 下一步：补充可控断网/限流替身与重复提交回归，随后进行 PostgreSQL 多进程接管和长时 soak 验收。

#### 长任务 L3：幂等专项与 PostgreSQL Live 前置检查（2026-09-09）

- 当前状态：幂等代码路径已完成并通过；PostgreSQL 多进程/Live 验收阻塞于测试夹具前置条件，阶段不关闭。
- 本批目标：确认重复审批回调、重复完成提交不会重复执行；探测真实 PostgreSQL 并发测试是否具备接管验收前置条件。
- 现有证据：`tests/test_case_execution_service.py -k approval` 通过 3 项；`tests/test_test_run_lifecycle.py -k "idempotent or concurrent_workers"` 通过 3 项。既有断言包括重复批准返回同一 `result_id` 且 Adapter 仅执行一次、重复完成不生成新结果、冲突完成载荷被拒绝。
- 执行命令：`$env:RUN_LIVE_POSTGRES_TESTS='1'; python -m pytest tests/test_live_postgres_concurrency.py -q`（工作目录 `Agent_Server`）。
- 失败：2 项 Live PostgreSQL 测试失败，未计入通过。其一为随机 `project_id` 不存在，触发实际数据库外键 `agent_test_runs.project_id -> agent_projects`；其二为 approval 并发测试预期的自定义 approval 表未被当前 SessionStore 完整采用，最终成功 CAS 数为 0 而非 16。
- 失败根因与解除条件：需先在测试夹具中创建并清理真实 project/suite 父记录，并核对 `PostgresSessionStore` 的表名配置与初始化/查询路径；修复后才能执行多进程 Worker 接管、租约过期恢复和长时 soak。禁止通过关闭外键、改默认表或降低断言绕过。
- 未完成：真实多进程强杀恢复、4 小时长任务、24 小时低速 soak、LangSmith 断网/限流真实网络验证。
- 下一步：修复 Live PostgreSQL 测试夹具并单独提交；随后重新执行同进程并发、跨进程接管和恢复对账。

#### 长任务 L3：PostgreSQL 跨进程接管验证（2026-09-09）

- 当前状态：已完成（受控跨进程接管）；真实独立 Worker 服务强杀和 4/24 小时长时验收仍未完成。
- 本批目标：修复 Live PostgreSQL 测试夹具的嵌套配置错误，并验证 Worker 进程退出后，另一进程能回收过期租约、创建新 Attempt 和恢复 checkpoint。
- 实际修改文件：`Agent_Server/tests/test_live_postgres_concurrency.py`、`Agent_Server/tests/test_live_postgres_capacity.py`。
- 根因修复：测试原先把 `postgres_*` 字段写入 `Settings` 顶层，Store 实际读取 `Settings.database`，导致隔离表配置未生效并误用默认业务表；现统一通过 `DatabaseConfig.model_copy` 更新嵌套配置。
- 跨进程场景：Windows `spawn` Worker A 领取条目并持久化版本 1 checkpoint 后直接退出；Worker B 以租约过期后的时间回收并重新领取；父进程核对 PostgreSQL 中的新 Attempt、`recovered_from_attempt_id`、checkpoint key/version/payload。
- 测试环境：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`，本机 PostgreSQL。
- 执行命令：`$env:RUN_LIVE_POSTGRES_TESTS='1'; python -m pytest tests/test_live_postgres_concurrency.py -q`；`python -m pytest tests/test_live_postgres_concurrency.py -q`；`python -m pytest -q`；`python -m compileall -q tests`（工作目录 `Agent_Server`）。
- 通过：Live PostgreSQL 3 passed，其中跨进程接管回收数为 1、Attempt 从 1 增至 2且恢复关联完整；默认关闭 Live 开关时 3 skipped；后端全量 759 passed、9 skipped、1 warning；测试编译通过。
- 失败：修复前 2 项 Live 测试失败，证据已记录于上一批；修复后无失败。
- 回滚验证：所有表均使用随机后缀并在 finally 清理；未修改生产表、业务代码、数据库 schema 或依赖。
- 已知限制：本测试验证真实 PostgreSQL 和两个独立 Python 进程，但尚未启动完整 Worker 服务、发送 OS 强杀信号或执行实际外部 API 工具；这些留给独立 Worker E2E。
- 下一步：建立可参数化的 4 小时/24 小时 soak harness，先用短时档验证统计、资源采样、错误率和清理，再由项目方安排长时运行窗口。

#### 长任务 L4：PostgreSQL 生命周期短档容量基线（2026-09-09）

- 当前状态：短档已完成；4 小时/24 小时 soak 未进行。
- 本批目标：验证修复后的容量 harness 能在隔离表上完成 claim、start、heartbeat、complete 全生命周期，并形成后续 soak 可比较的延迟基线。
- 执行命令：`$env:RUN_LIVE_POSTGRES_CAPACITY='1'; $env:RUN_LIVE_POSTGRES_CAPACITY_SIZES='100'; $env:RUN_LIVE_POSTGRES_CAPACITY_WORKERS='4'; python -m pytest tests/test_live_postgres_capacity.py -q -s`（工作目录 `Agent_Server`）。
- 通过：1 passed；100 个条目全部完成，吞吐 86.56/s；claim P50/P95/P99 为 5.90/90.62/90.62ms，start 为 8.65/11.71/13.34ms，heartbeat 为 7.23/9.60/12.43ms，complete 为 9.64/15.18/39.73ms。
- 失败：无。
- 回滚与清理：随机后缀 Run/Item/Attempt/Result 表均由 finally 清理，未写默认业务表。
- 结论边界：该结果只证明 100 条短档生命周期容量，不证明数小时稳定性、内存无增长、连接无泄漏或 LangSmith 长时上传无积压。
- 下一步：增加按 wall-clock 循环、定期采集进程 RSS/数据库连接数/错误率和延迟分位数的 soak harness；以 1—5 分钟短档验证后，才能安排 4/24 小时档。

### 14.5 阶段 2：LangChain 模型与消息适配

状态：未进行
目标：使用 LangChain 标准消息、模型和结构化输出能力，逐步减少自定义 Provider 协议代码，但保留现有模型配置、OAuth 和业务错误语义。

前置条件：

- 阶段 1 已完成，能观测新旧模型路径。
- 现有 ModelInvocationRequest/Result 契约已冻结。
- 目标 Provider 的官方 LangChain 集成和版本已核对。

具体任务：

| ID | 任务 | 目标文件/位置 | 状态 | 完成判据 |
|---|---|---|---|---|
| P2-01 | 定义 ModelPort | application/langchain | 未进行 | 旧新实现共享业务接口 |
| P2-02 | 实现 Message 双向转换 | message_adapter.py | 未进行 | system/user/assistant/tool 无损转换 |
| P2-03 | 实现 LangChainModelAdapter | model_adapter.py | 未进行 | 至少一个 Provider 跑通 |
| P2-04 | 保留 LegacyProviderAdapter | model_runtime_service.py | 未进行 | 可按 Flag 回退 |
| P2-05 | 统一 Tool Call 转换 | schemas/tool runtime | 未进行 | call id、name、args 保真 |
| P2-06 | 接入 Structured Output | 目标业务节点 | 未进行 | Schema 错误可观测且可恢复 |
| P2-07 | 对齐 Streaming | model stream handler/SSE | 未进行 | chunk 顺序和终态正确 |
| P2-08 | 对齐 Usage/Error | adapter/错误映射 | 未进行 | Token 和错误类型兼容 |
| P2-09 | 按 Provider 建立契约测试 | tests/test_langchain_model_adapter.py | 未进行 | 旧新结果可对账 |

测试计划：

- OpenAI、Anthropic、Google 及项目实际启用 Provider 的消息契约测试。
- 文本响应、单工具、多工具、无工具、流式、中断和结构化输出测试。
- OAuth、缺少密钥、超时、限流、上下文超限和协议错误测试。
- Legacy/LangChain 双跑对账；不比较自然语言逐字一致，比较结构化语义和终态。

退出条件：

- 至少一个非关键模式灰度稳定。
- 旧路径仍可通过 Flag 回退。
- 工具调用、错误分类、Token 和 SSE 不发生未声明破坏。
- ModelRuntimeService 的业务 DTO 未被 LangChain 类型污染。

当前测试结果：未执行。
当前阻塞：依赖阶段 1。
回滚点：关闭 LANGCHAIN_MODEL_ADAPTER_ENABLED。
最近提交：无。

### 14.6 阶段 3：LangChain 工具适配与 Middleware

状态：未进行
目标：标准化工具暴露与横切能力，减少重复代码，同时保留业务权限、安全和测试运行治理。

前置条件：

- 阶段 2 已完成。
- ToolRegistry、ToolRuntimeService、PermissionService 的契约已冻结。
- 所选 Middleware API 已按锁定版本核对官方文档。

具体任务：

| ID | 任务 | 目标文件/位置 | 状态 | 完成判据 |
|---|---|---|---|---|
| P3-01 | 实现 LangChainToolAdapter | application/langchain/tool_adapter.py | 未进行 | Registry 工具可安全转换 |
| P3-02 | 建立 Tool 输入输出 Schema 对账 | registry/schemas | 未进行 | 参数和错误不丢失 |
| P3-03 | 建立 Middleware Registry | middleware_registry.py | 未进行 | 顺序、开关和作用域明确 |
| P3-04 | 迁移通用 Retry/Timeout | Middleware | 未进行 | 不与业务重试叠加 |
| P3-05 | 迁移 Redaction/Observability | Middleware | 未进行 | 与阶段 1 策略一致 |
| P3-06 | 评估 Context Compaction | context service/Middleware | 未进行 | 不产生双重摘要 |
| P3-07 | 评估 Dynamic Prompt/Token Budget | prompting/Middleware | 未进行 | Prompt 结构和预算可追踪 |
| P3-08 | 保留业务安全强制层 | Permission/Safety/Approval | 未进行 | Middleware 无法绕过 |

测试计划：

- 工具 Schema、默认值、枚举、嵌套对象和非法参数测试。
- allow/deny/approval 三种权限路径。
- 工具超时、取消、重复调用、并行安全和 Artifact 测试。
- Middleware 顺序与重复执行测试。
- MCP、Skill 和普通 Registry 工具一致性测试。

退出条件：

- LangChain Tool 只能调用 Registry 已暴露工具。
- 权限和安全结果与旧路径一致。
- 不存在双重 Retry、双重压缩或双重审批。
- 工具事件和 LangSmith Run 可对账。

当前测试结果：未执行。
当前阻塞：依赖阶段 2。
回滚点：关闭 LANGCHAIN_TOOL_ADAPTER_ENABLED，并恢复旧工具暴露路径。
最近提交：无。

### 14.7 阶段 4：Deep Agents code_review 试点

状态：未进行
目标：在低外部副作用的 code_review 模式中验证 Deep Agents 的规划、文件系统、上下文管理、Skills 和 Subagents 能力。

前置条件：

- 阶段 1—3 已完成。
- Deep Agents 版本和许可证已核对。
- 安装后全量测试无依赖回归。
- code_review 当前真实输入、输出和失败样本已建立基线。

具体任务：

| ID | 任务 | 目标文件/位置 | 状态 | 完成判据 |
|---|---|---|---|---|
| P4-01 | 安装可选 deepagents 依赖 | pyproject/lock | 未进行 | 默认启动不强依赖 |
| P4-02 | 实现 DeepAgentModeAdapter | application/deep_agents | 未进行 | 不泄漏框架类型 |
| P4-03 | 桥接现有 Tools | tool adapter | 未进行 | 权限和审计仍生效 |
| P4-04 | 桥接现有 Skills | skill runtime | 未进行 | 渐进加载且版本可追踪 |
| P4-05 | 限定文件系统后端 | project scope/artifact | 未进行 | 不越过项目目录 |
| P4-06 | 建立 Subagent 配置 | code_review_agent.py | 未进行 | 角色、工具、预算明确 |
| P4-07 | 结果归一化 | result_normalizer.py | 未进行 | 回到 AgentGraphState/RuntimeTurnResult |
| P4-08 | 接入本地 Event 与 LangSmith | observability/flow | 未进行 | 两套视图均可追踪 |
| P4-09 | 建立新旧 code_review 对账 | tests/fixtures | 未进行 | 质量、稳定性、耗时有比较 |

测试计划：

- 单文件、多文件、大仓库、无可审查变更和损坏输入。
- 规划、多工具、子代理、上下文卸载、失败恢复和取消。
- 路径穿越、越权工具、敏感文件和审批。
- 本地 Flow、LangSmith Trace 和最终报告一致性。
- 真实代码评审失败样本回归。

退出条件：

- code_review 质量不低于旧路径。
- 无权限、安全、路径或证据链回归。
- Deep Agents 失败时可回退旧 Harness。
- 没有形成第二套外层 Agent Loop。
- 试点连续稳定后才能讨论其他模式。

当前测试结果：未执行。
当前阻塞：deepagents 未安装，且依赖阶段 1—3。
回滚点：关闭 DEEP_AGENTS_CODE_REVIEW_ENABLED。
最近提交：无。

### 14.8 阶段 5：Coordinator/Worker 与 Subagents 对齐

状态：未进行
目标：把 Deep Agents Subagents 作为受控 Worker 实现，统一父子任务、权限、状态、Trace 和失败传播。

前置条件：

- 阶段 4 已完成且试点稳定。
- CoordinatorRuntimeService 的 Worker 生命周期契约已冻结。
- 子任务并发、深度和预算限制已定义。

具体任务：

| ID | 任务 | 状态 | 完成判据 |
|---|---|---|---|
| P5-01 | 定义统一 WorkerExecutionContract | 未进行 | 现有 Worker 和 Subagent 共用 |
| P5-02 | 实现 SubagentBridge | 未进行 | 父子 Session/Trace 可关联 |
| P5-03 | 对齐取消、中断和超时 | 未进行 | 父任务终止能可靠传播 |
| P5-04 | 对齐 ApprovalProxy | 未进行 | 子 Agent 不绕过父审批 |
| P5-05 | 对齐 Artifact/Evidence | 未进行 | 产物归属和引用正确 |
| P5-06 | 对齐并发、深度和预算 | 未进行 | 超限有确定性终态 |
| P5-07 | 移除被替代的重复状态机 | 未进行 | 不保留长期双轨 |

测试计划：

- 父子 Trace、子 Session、并发 Worker、部分失败、超时和取消。
- 审批转发、重复通知和终态不可重领。
- Worker Artifact、Evidence 和结果聚合。
- 崩溃恢复和幂等。

退出条件：

- 一个 Worker 只有一个权威状态源。
- 父子任务可以从本地和 LangSmith 双向定位。
- Coordinator 原有模式回归通过。
- 重复实现已删除或有明确淘汰期限。

当前测试结果：未执行。
当前阻塞：依赖阶段 4。
回滚点：按模式切回现有 Coordinator Worker。
最近提交：无。

### 14.9 阶段 6：LangSmith 评测闭环

状态：未进行
目标：把本地版本化测试资产映射到 LangSmith Dataset/Experiment/Feedback，形成模型、Prompt、Agent 轨迹的可重复质量评估。

前置条件：

- 阶段 1 的 Trace 稳定。
- 本地 Test Case/Suite/Run 版本契约已冻结。
- 数据脱敏和外发范围已批准。

具体任务：

| ID | 任务 | 状态 | 完成判据 |
|---|---|---|---|
| P6-01 | 定义本地用例到 Dataset Example 的映射 | 未进行 | case_version_id 不丢失 |
| P6-02 | 定义 Test Run 到 Experiment 的映射 | 未进行 | 环境和基线可追溯 |
| P6-03 | 建立 Evaluator Registry | 未进行 | 规则/模型/人工评估分离 |
| P6-04 | 建立 Feedback 回流 | 未进行 | 不覆盖原始运行结果 |
| P6-05 | 建立失败样本治理 | 未进行 | 脱敏、去重、上下文和通过标准齐全 |
| P6-06 | 建立模型/Prompt 比较实验 | 未进行 | 可重复并可定位版本 |
| P6-07 | 建立 CI 质量门候选 | 未进行 | 阈值有基线证据 |

测试计划：

- 数据集幂等同步、版本变化和删除策略。
- Experiment 可重复执行。
- Evaluator 超时、异常和部分失败。
- Feedback 回流不覆盖历史结果。
- project_id/case_version_id/test_run_id 全链路追踪。

退出条件：

- LangSmith 数据能回查本地固定版本。
- 本地正式 Test Run 仍是唯一业务事实。
- 失败样本治理符合项目测试工程规则。
- CI 质量门先观察后阻断，阈值有统计依据。

当前测试结果：未执行。
当前阻塞：依赖阶段 1，部分能力依赖阶段 2—5。
回滚点：停止同步和实验，不删除本地测试资产。
最近提交：无。

### 14.10 阶段 7：按模式灰度迁移

状态：未进行
目标：基于前述适配层和评测证据逐个迁移业务模式，最终减少重复自研能力而不破坏专业测试治理。

前置条件：

- 阶段 1—6 达到各自退出条件。
- 每个模式有旧路径基线、真实失败样本和回滚 Flag。
- 灰度范围和责任人明确。

迁移顺序与专项目标：

| 顺序 | 模式 | 重点验证 | 状态 | 测试结果 |
|---:|---|---|---|---|
| 1 | code_review | Deep Agents 规划、文件与子代理 | 未进行 | 未执行 |
| 2 | default | 通用对话、工具选择、上下文 | 未进行 | 未执行 |
| 3 | api_testing | 契约、鉴权、幂等、并发、证据 | 未进行 | 未执行 |
| 4 | ui_automation | 浏览器状态、录制、回放、断言 | 未进行 | 未执行 |
| 5 | compatibility_testing | 环境矩阵、部分失败、聚合 | 未进行 | 未执行 |
| 6 | smoke_testing | 快速终态、失败判定、回归 | 未进行 | 未执行 |
| 7 | performance_testing | TTFT、吞吐、P95/P99、资源限制 | 未进行 | 未执行 |
| 8 | security_testing | 授权目标、隔离、审批、证据和清理 | 未进行 | 未执行 |

每个模式必须完成：

1. 旧路径基线。
2. 新路径离线回放。
3. 新旧双跑对账。
4. 小范围灰度。
5. 错误率、质量、性能和成本观察。
6. 回滚演练。
7. 扩大流量或停止迁移的评审结论。

退出条件：

- 全部模式都有明确迁移结论。
- 被替代的旧代码有删除计划并通过回归。
- 不存在没有 Owner 的双轨实现。
- 安全和性能模式通过专项验收。
- 文档、配置、运行手册和告警同步完成。

当前测试结果：未执行。
当前阻塞：依赖阶段 1—6。
回滚点：按模式和项目切回旧路径。
最近提交：无。

### 14.11 阶段 2 进入前兼容预检（只读，2026-09-09）

本预检不代表阶段 2 已开始，也不改变当前依赖和运行路径；目的仅是依据现有代码和 LangChain 官方文档，确定阶段 2 的真实切入点、必须验证的兼容面和禁止采取的捷径。

#### 现有代码事实

| 检查项 | 当前事实 | 对阶段 2 的影响 |
|---|---|---|
| 业务模型调用入口 | `ModelRuntimeService.invoke(model_key, ModelInvocationRequest)` | 适配器应实现该业务服务依赖的稳定端口，不能改 API DTO |
| 消息契约 | `UnifiedMessage` 同时包含 role、parts、tool_call_id、tool_calls；请求还保留原始 `messages` | 做双向转换并保留原始结构，不能只转换纯文本 |
| Provider 选择 | `ModelRegistry` 从数据库解析 provider、transport、api_base_url、headers、auth_type 和能力 | LangChain 模型实例必须由数据库配置驱动，不能在代码中硬编码模型或密钥 |
| 现有传输 | Anthropic Messages、OpenAI Chat Completions、OpenAI Responses、Google Gemini Generate Content，以及大量 OpenAI-compatible Provider | 每种 transport 必须单独做契约测试；不能把 OpenAI-compatible 直接宣称等同于 OpenAI 原生行为 |
| 认证与错误 | `ModelRuntimeService` 负责 API Key/OAuth 解析并将 ProviderClientError 映射为业务结果 | LangChain 适配器不得绕过 OAuth、超时、错误分类和现有重试边界 |
| 流式链路 | 当前通过 `stream_handler` 将 ProviderClient 流式 chunk 推送到本地 SSE | 适配器必须验证 chunk 顺序、tool-call chunk、终态和 token usage，不以 `invoke` 通过替代流式验证 |

#### 官方 API 对齐结论

LangChain 官方模型文档明确将 `invoke`、`stream`、`bind_tools` 和 `with_structured_output` 作为标准模型能力；消息是跨 Provider 的标准输入输出单元，工具调用结果通过 `tool_call_id` 关联。依据：

- [LangChain Models](https://docs.langchain.com/oss/python/langchain/models)
- [LangChain Messages](https://docs.langchain.com/oss/python/langchain/messages)
- [LangChain Structured Output](https://docs.langchain.com/oss/python/langchain/structured-output)
- [Chat model integrations](https://docs.langchain.com/oss/python/integrations/chat/index)

据此，阶段 2 的实现边界确定为：

1. 在 `application/langchain/` 定义内部 `ModelPort` 和消息转换器；LangChain 的 `BaseMessage`、`AIMessage`、`ToolMessage` 只能停留在适配器内部。
2. 先以一个实际启用且支持工具调用的 Provider 做端到端验证，再扩展其他 Provider；不得一次性替换所有 transport。
3. `bind_tools` 只负责协议绑定，工具执行仍由现有 ToolRegistry、PermissionService、SafetyGate 和 ToolRuntimeService 完成。
4. `with_structured_output` 只在目标 Provider 的能力和错误行为完成实测后启用；解析失败必须映射回现有错误/恢复契约。
5. LangChain 适配器与 LegacyProviderAdapter 双跑对账时，比较结构化语义、工具调用、终态、错误分类、usage 和 SSE 顺序，不比较自然语言逐字一致。

#### 依赖与兼容决策

- 当前 `pyproject.toml` 没有声明 `langchain-openai`、`langchain-anthropic` 或 `langchain-google-genai`；本预检不新增这些依赖，也不把它们作为“已兼容”记录。
- 当前主服务锁定的 `langchain==1.2.3`、`langchain-core==1.2.7`、`langgraph==1.0.10` 和 `langsmith==0.10.18` 继续保持不变；任何升级必须先建立 C4 组合、全量回归、真实默认模型会话和回滚证据。
- 当前数据库默认模型实际为 Qwen OpenAI-compatible 路径；该路径尚不能直接套用 `ChatOpenAI` 的 Provider 语义，必须先核对 `api_base_url`、额外请求头、tool schema、usage 和 streaming 行为。

#### 阶段 2 进入条件（当前未满足）

| 条件 | 状态 | 证据/缺口 |
|---|---|---|
| 阶段 1 外部 LangSmith Trace 完成 | 未满足 | `.env` 中 LangSmith Key 为空，P1-08/P1-09 未完成 |
| 选定 Provider 官方集成包和版本已锁定 | 未满足 | 尚未对实际 Qwen 兼容端点完成安装与协议验证 |
| ModelPort/消息转换契约测试设计完成 | 进行中 | 本预检已冻结边界，代码和测试尚未创建 |
| Legacy/LangChain 双跑真实对账 | 未进行 | 必须在实现后执行 |
| 依赖升级后的 C4 回滚证据 | 未进行 | 阶段 0 P0-07 仍阻塞 |

结论：阶段 2 继续保持“未进行”，但其实现切入点和兼容验证清单已冻结；在阶段 1 云端验证完成前，不安装新的 LangChain Provider 包、不升级主服务生态版本、不修改现有模型执行路径。

## 15. 每次实施后的记录模板

后续每完成一个开发批次，在对应阶段下追加：

    批次：
    日期：
    当前状态：未进行 / 进行中 / 已完成
    本批目标：
    实际修改文件：
    完成任务 ID：
    未完成任务 ID：
    依赖或契约变化：
    测试环境：
    执行命令：
    通过：
    失败：
    跳过：
    警告：
    失败根因：
    回滚验证：
    已知限制：
    提交：
    下一步：

禁止只写“测试通过”而不记录命令和数量；禁止阶段未满足退出条件时把状态改为已完成。

状态维护补充规则：

1. 每个任务只允许使用“未进行 / 进行中 / 已完成 / 阻塞”；“阻塞”必须同时写明证据、解除条件和不采取的危险捷径。
2. 一个阶段的目标、输入、任务、输出、测试、退出条件和回滚证据必须能够一一对应；无法对应的工作不得计入完成度。
3. 环境快照、依赖锁和兼容矩阵是三个不同交付物：快照用于取证，锁用于复现，矩阵用于选型，禁止混用结论。
4. 真实外部系统未验证时，单元测试或 Mock 只能记为“代码路径通过”，不得把阶段标为已完成。

