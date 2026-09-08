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
        api_key_env: str = "LANGSMITH_API_KEY"
        tracing_mode: str = "off"
        sample_rate: float = 1.0
        capture_inputs: bool = False
        capture_outputs: bool = False

实际字段命名和 SDK 参数必须在锁定版本后核对官方文档，不能凭记忆复制 API。

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

