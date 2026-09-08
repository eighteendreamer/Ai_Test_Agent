# LangChain 生态官方文档归档

归档日期：2026-09-08（Asia/Shanghai）

本目录只收录 LangChain 官方文档站 `docs.langchain.com` 与官方 GitHub 组织 `langchain-ai` 的一手资料，用于 `Enterprise_AI_QA_Agent` 后续地层升级、架构设计和 API 查证。网页内容已转换为本地 Markdown 快照；每个文件开头保留原始 `Source` URL。

> 官方在线文档会持续更新。实施前应先阅读本地快照，再通过文件中的 `Source` 链接核对当前版本。不得仅凭快照猜测尚未归档的 API。

## 生态分层

官方当前将主要组件定位为：

| 层 | 官方定位 | 本项目建议关注点 |
| --- | --- | --- |
| LangChain | Agent framework；提供模型、工具、消息、`create_agent` 和 Middleware 抽象 | 统一模型/工具协议、动态工具选择、上下文工程、Guardrail 和跨切面 Middleware |
| LangGraph | 低层编排运行时；负责 durable execution、streaming、human-in-the-loop 和 persistence | 保留现有可审计状态机，升级 checkpoint、interrupt、stream、subgraph 和 time travel 能力 |
| Deep Agents | 基于 LangChain 与 LangGraph 的高阶 Agent harness | 为长任务引入规划、文件系统、上下文卸载、Skills、子代理和持久记忆；不应无依据替换全部现有专用 Harness |
| LangSmith | Tracing、Evaluation、Prompt、Deployment 平台 | 统一 trace、数据集、实验、线上评测和质量门，映射现有 session/turn/tool/evidence/test-run 体系 |

## 目录索引

### `LangChain/`

- `langchain_overview.md`：生态定位与 `create_agent`
- `langchain_install.md`：官方安装方式与 Provider 独立包
- `langchain_agents.md`：Agent、模型、工具、动态工具和状态
- `langchain_middleware_overview.md`：Middleware 生命周期及与 LangGraph 的组合方式
- `langchain_middleware_built_in.md`：Summarization、HITL、Retry、Tool Selector、Filesystem、Subagent 等内置 Middleware
- `langchain_context_engineering.md`：上下文选择、压缩与持久/临时上下文边界
- `langchain_runtime.md`：Runtime context、store、stream writer、execution/server info
- `langchain_guardrails.md`：PII、安全策略与 Human-in-the-loop

### `LangGraph/`

- `langgraph_overview.md`：LangGraph 定位和能力边界
- `langgraph_thinking.md`：工作流拆解、状态和节点设计方法
- `langgraph_graph_api.md`：StateGraph、分支、循环、Send、Command
- `langgraph_persistence.md`：Checkpointer、Store、线程和记忆
- `langgraph_interrupts.md`：中断、审批与恢复
- `langgraph_streaming.md`：流式模式与事件输出
- `langgraph_time_travel.md`：Checkpoint replay 与 fork
- `langgraph_application_structure.md`：应用目录、依赖、`langgraph.json` 和部署结构
- `langgraph_test.md`：图与节点测试
- `langgraph_local_server.md`：本地 Agent Server、Studio 和 SDK

### `DeepAgents/`

- `deepagents_overview.md`：Deep Agents 定位与 `create_deep_agent`
- `deepagents_quickstart.md`：快速开始
- `deepagents_customization.md`：模型、工具、提示词、Middleware 和后端定制
- `deepagents_subagents.md`：子代理、隔离上下文与委派
- `deepagents_human_in_the_loop.md`：敏感工具审批
- `deepagents_skills.md`：Skills 目录和渐进式加载
- `deepagents_github_readme.md`：官方仓库 README 快照

### `LangSmith/`

- `langsmith_observability_concepts.md`：Project、Trace、Run、Thread、Tag、Metadata
- `langsmith_trace_with_langchain.md`：LangChain/LangGraph 自动 Tracing 和手工埋点
- `langsmith_langgraph_observability.md`：LangGraph 与 LangSmith 可观测性接入
- `langsmith_evaluation.md`：离线评测和线上评测总览
- `langsmith_evaluation_concepts.md`：Dataset、Example、Experiment、Evaluator、Feedback
- `langsmith_evaluation_quickstart.md`：SDK/UI 评测快速开始
- `langsmith_manage_datasets.md`：数据集、切分和导入导出
- `langsmith_analyze_experiment.md`：实验分析、比较和基线

根目录的 `langchain_docs_index_llms.txt` 是官方文档索引快照，可用于发现尚未归档的页面。

## 与当前系统的初步映射

| 当前模块 | 官方生态对应 | 后续查证重点 |
| --- | --- | --- |
| `src/graph/*` | LangGraph Graph API / Runtime | State reducer、Command/Send、Checkpointer、Subgraph、Streaming |
| `application/runtime/runtime_service.py` | LangGraph durable execution + Deep Agents harness | 当前循环与官方 runtime 的职责边界，避免双重状态机 |
| `graph/nodes/permission_gate.py`、`core/safety_gate.py` | Interrupts、HITL Middleware、Guardrails | 审批 ID、恢复幂等、安全上下文不能被客户端伪造 |
| `application/context/*` | LangChain Context Engineering、Deep Agents context management | 摘要、文件卸载、短期/长期记忆与现有 PostgreSQL/pgvector 的映射 |
| `registry/tools.py`、`registry/skills.py`、MCP 模块 | LangChain Tools、Deep Agents Skills、MCP | 动态工具注册、渐进式披露和工具执行权限 |
| Coordinator/Worker | Deep Agents Subagents + LangGraph Subgraphs | 父子 trace、上下文隔离、并发限制、失败传播和终态 |
| Session/Event/Snapshot | LangGraph Checkpointer、Threads、Streaming | 是否适配官方 checkpoint，而不是并行维护两套不可对账状态 |
| Test Case/Suite/Run/Evidence | LangSmith Dataset/Experiment/Feedback | 版本化用例、失败样本回流、实验基线和结果证据关联 |
| Runtime Event Console | LangSmith Trace/Run + 本地 SSE | 本地实时事件与云端/自托管 Trace 的 ID 对齐及脱敏策略 |

## 使用原则

1. 先基于现有代码确认职责，再用对应官方文档核对 API 和版本行为。
2. LangGraph 继续承担确定性、可恢复、可审计的底层编排；Deep Agents 优先作为可插拔 Harness 或 Subgraph 评估。
3. LangSmith 初期应以可选 Tracing Adapter 接入，不能让外部观测服务故障阻断测试执行主链。
4. 上传 LangSmith 的输入、输出、工具参数和 Metadata 必须经过现有敏感信息清洗；默认不得上传密钥、认证材料、用户隐私和安全测试原始凭证。
5. LangSmith Dataset/Experiment 必须保留本项目的 `project_id`、用例固定版本、套件冻结版本、运行环境和证据引用，不能另建不可追溯的自由文本体系。
6. 任何依赖或 API 落地前，重新核对官方在线文档、当前锁定版本和许可证。

