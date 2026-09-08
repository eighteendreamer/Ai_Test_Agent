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

截至 2026-09-08：

| 阶段 | 名称 | 状态 | 已完成度 | 当前结论 | 测试状态 |
|---|---|---:|---:|---|---|
| 准备项 | 官方文档归档 | 已完成 | 100% | 35 份官方资料已归档并建立索引 | 文档存在性已核对 |
| 0 | 契约、依赖和可回滚基线 | 进行中 | 90% | 已落地生态依赖声明、LangSmith 配置、TraceContext、Flag、完整环境快照、运行时直接依赖补齐、干净 C1 复现、Deep Agents C2 候选验证和冲突隔离结论；生态升级版本决策与全量契约固化仍未完成 | 现有环境、干净 C1 环境和隔离 C2 候选 Harness 均通过；共享开发环境 pip check 仍受非主服务工具冲突影响 |
| 1 | LangSmith 非阻塞观测 | 进行中 | 85% | 已落地 Turn/Graph/Model/Tool/Worker Trace、No-op 降级、本地 Run 引用、Flow 可选深链接和 errors_only 语义；真实外部上报与外部树层级仍未完成 | 2026-09-08 观测专项 20 项、前端 32 项、后端全量和真实默认模型链路通过；真实 LangSmith 上报未执行 |
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

这些冲突发生在生态升级代码实施前，必须作为“既有环境债务”单独记录。处置结论已确定为将 browser-use、mem0ai、mitmproxy 隔离到独立工具环境，不通过降级主服务依赖消除共享环境冲突。详细证据和兼容矩阵见 `Agent_Server/docs/langchain-ecosystem-compatibility-matrix-2026-09-08.md`。阶段 0 完成仍需在干净主服务环境证明 `pip check` 和真实运行通过。

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
| P0-10 | 固化现有事件和状态契约 | schemas、契约测试 | 未进行 | 消费方引用已全局核对 |

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

本批完成项：P0-01 至 P0-06、P0-08、P0-09；P0-05 已完成候选矩阵；P0-07 仍阻塞于主服务生态协调升级，P0-10 仍未完成。
当前测试结果（2026-09-08）：现有开发环境 `test_observability_contracts.py` 10 passed；后端全量 739 passed、8 skipped、1 warning（26.20s）；`compileall -q src tests` 通过；现有开发环境真实 FastAPI 链路通过。C1 干净 Python 3.11 venv 安装通过，`pip check` 通过，`import src.main` 成功，C1 后端全量 739 passed、8 skipped、1 warning（26.52s），真实 FastAPI 健康检查、默认模型会话、Events、Snapshot 和 Flow 成功（25 events、1 Snapshot）。C2 独立环境安装 Deep Agents 0.7.13 及官方依赖成功，`pip check` 通过，`create_deep_agent` 构造与 fake tool-capable model 离线 invoke 成功。共享开发环境 `pip check` 仍返回 8 条非主服务工具冲突；默认索引 `pip index versions deepagents` 无匹配，但官方 PyPI 可见 0.7.13。
当前阻塞：主服务不能直接声明 Deep Agents extra，因为官方 0.7.13 要求 LangChain 至少 1.3.18、Core 至少 1.6.1、LangGraph 至少 1.2.11，和当前锁定组合不兼容；还需阶段 2—3 完成协调升级、Provider 真实链路、事件契约全量固化和回滚验证。
回滚点：恢复 pyproject/config/契约变更；因为 Flag 默认关闭，不影响旧路径。
最近提交：`0df3050`（补充 Run 引用契约测试）。

### 14.4 阶段 1：LangSmith 非阻塞观测

状态：进行中
目标：为当前运行链建立完整调用树，同时确保 LangSmith 永远不是执行主链硬依赖。

前置条件：

- 阶段 0 已完成。
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

本批完成项：P1-01 至 P1-07、P1-10、P1-11；P1-08/P1-09 已完成代码接入但等待外部环境验证；补齐 `errors_only` 成功/失败语义。
当前测试结果（2026-09-08，`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`）：`compileall` 通过；观测/Flow 专项 20 passed；后端全量 739 passed、8 skipped、1 warning（24.26s）；前端 32 passed；`npm run build` 成功（3154 modules transformed，保留既有主 chunk 约 2.5 MB 警告）；真实 FastAPI 启动、健康检查（`postgres_ok=true`）、默认模型会话、事件历史、completed Snapshot 和 Flow 查询均通过；真实模型回复为“观测策略回归成功”。
第一次真实验证发现同步 `planner` 节点被错误 `await`，已修复包装器并完成回归。LangSmith 真实外部上报未执行（当前配置默认关闭且未提供 LangSmith API Key），因此本批不宣称外部父子树和 URL 可访问性已验证。
当前阻塞：真实 LangSmith 环境验证尚未完成；P1-08/P1-09 需外部环境复验后才能满足阶段退出条件。前端 Trace 深链接和 `errors_only` 已完成；`sampled` 由 LangSmith SDK 的 `tracing_sampling_rate` 实现，根 Trace 决定采样且子 Run 跟随，不再自研第二套采样器。
回滚点：关闭 LANGSMITH_ENABLED；删除 Adapter 接线不影响本地 Event/SSE。
最近提交：待本批代码提交。

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

