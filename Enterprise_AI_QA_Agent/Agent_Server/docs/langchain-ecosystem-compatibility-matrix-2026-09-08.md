# LangChain 生态依赖兼容与环境隔离矩阵

首次建立：2026-09-08
最近验证：2026-09-09
适用解释器：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`
状态：进行中（当前主服务组合与 Deep Agents C2 候选组合已验证；真实 LangSmith 外部上报和 C3/C4 尚未完成）

## 1. 结论与边界

当前主服务继续锁定已经通过全量回归和真实默认模型会话验证的组合，不为安装 Deep Agents 直接升级：

| 包 | 项目声明/当前环境 | 当前状态 | 证据 |
|---|---:|---|---|
| Python | 3.11.15 | 已验证 | 指定解释器实际输出 |
| `langchain` | 1.2.3 | 已验证 | 包元数据、后端全量测试、真实会话 |
| `langchain-core` | 1.2.7 | 已验证 | 包元数据、后端全量测试、真实会话 |
| `langgraph` | 1.0.10 | 已验证 | 包元数据、后端全量测试、真实会话 |
| `langsmith` | 0.10.18 | 已验证（本地适配与 SDK 协议） | 观测契约、root→node 协议测试；真实外部上报尚未验证 |
| `deepagents` | 0.7.13（主服务未安装/未声明） | 候选已验证，主服务阻塞 | C2 隔离环境通过；官方主线依赖高于项目锁定组合 |

本次干净环境导入验证还发现，应用真实启动链直接使用的 `dependency-injector`、`python-magic`、`playwright` 原先未在项目声明中列出，已补入 `pyproject.toml`。这三项不是 LangChain 生态依赖，但属于应用可执行性的必要直接依赖，不能依赖共享开发环境“恰好已安装”。

当前 Provider SDK 基线：

| Provider SDK | 项目约束 | 当前环境 | 状态 |
|---|---|---:|---|
| `openai` | `>=1.60.0` | 1.109.1 | 现有 Provider 路径和默认模型真实会话已验证；LangChain Provider Adapter 尚未验证 |
| `anthropic` | `>=0.40.0` | 0.111.0 | 包元数据已验证；真实 Provider 会话未在本批执行 |
| `google-genai` | `>=1.0.0` | 1.75.0 | 包元数据已验证；真实 Provider 会话未在本批执行 |

这份矩阵区分主服务 C1 与 Deep Agents C2：C1 证明当前主服务可重复安装，C2 只证明候选生态组合可安装和 Harness 可执行，不等价于主服务已经升级。真实 LangSmith 云端上报仍未验证。

## 2. 依据到决策

| 依据来源 | 已核对事实 | 本项目决策 |
|---|---|---|
| `Agent_Server/pyproject.toml` | 主服务锁定 `langchain==1.2.3`、`langchain-core==1.2.7`、`langgraph==1.0.10`、`langsmith==0.10.18` | 保持当前生产候选基线，不在阶段 0 顺手升级 |
| `python311-runtime-freeze-2026-09-08.txt` | 开发环境包含大量非主服务工具包 | 完整快照只作取证，不作为主服务 lock 输入 |
| 指定解释器包元数据 | 四个生态包版本与项目声明一致 | 当前组合标记为“已验证” |
| LangChain 官方 Deep Agents Quickstart | 安装包名为 `deepagents`，入口为 `from deepagents import create_deep_agent` | 不自创包名或 API；仅在官方包可解析后建立试点 |
| Deep Agents 官方仓库 `libs/deepagents/pyproject.toml`（2026-09-08 拉取） | 主线 0.7.13 要求 Python `>=3.11,<4.0`、`langchain>=1.4.0`、`langchain-core>=1.6.2`、`langsmith>=0.12.2`，许可证 MIT | Python 版本兼容，但当前 LangChain/LangSmith 组合不兼容；必须在隔离环境先做升级矩阵，不能直接加入默认或可选依赖组 |
| `pip index versions deepagents` | 当前配置镜像返回 `No matching distribution found`；官方 PyPI 可解析 `0.7.13` | 镜像同步问题与版本兼容问题分开处理；主环境仍不安装 Deep Agents |
| 官方 PyPI 组合 dry-run | `deepagents==0.7.13` 要求 `langchain>=1.3.18`，与主服务 `langchain==1.2.3` 冲突 | 保留 P0-07 阻塞；先完成隔离升级矩阵和 C4 回滚证据，不修改主环境 |

官方来源：

- <https://docs.langchain.com/oss/python/deepagents/quickstart>
- <https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/pyproject.toml>

## 3. 当前开发环境冲突隔离结论

`pip check` 当前失败，但冲突包 `browser-use`、`mem0ai`、`mitmproxy` 均不在 `Agent_Server/pyproject.toml` 的主服务声明中。它们属于共享开发环境中的其他工具链，不允许通过降级主服务依赖来“清零”错误。

| 冲突工作负载 | 实际冲突 | 所属边界 | 处置状态 | 处置结论 |
|---|---|---|---|---|
| `browser-use==0.11.1` | `openai`、`pypdf`、`python-docx` 不满足其约束 | 浏览器自动化附加工具 | 已隔离（设计结论） | 后续放入独立工具环境；不得进入主服务 lock |
| `mem0ai==1.0.0` | 要求 `protobuf>=5.29,<6`，当前为 7.35.1 | 记忆附加工具 | 已隔离（设计结论） | 后续放入独立工具环境；不得为其回退主服务传递依赖 |
| `mitmproxy==11.0.2` | `asgiref`、`cryptography`、`h11`、`pyOpenSSL` 冲突 | 安全代理工具 | 已隔离（设计结论） | 使用独立安全测试环境；不得污染 API 服务环境 |

“已隔离（设计结论）”表示依赖边界和处置路线已确定，不表示当前共享开发环境的 `pip check` 已通过。C1 已在最小主服务环境完成安装、`pip check`、导入冒烟、服务启动和真实会话；共享环境冲突仍保留为附加工具隔离债务。

### 3.1 C4 基础依赖升级影响与最小升级边界

完整候选解析得到的“最新版集合”不等于 Deep Agents 的全部硬性要求。根据候选包元数据、项目直接导入点和实际回归，升级边界如下：

| 依赖 | 当前基线 → C4 首次解析 | 是否为 Deep Agents 链路硬要求 | 项目影响面 | 当前证据 | 决策 |
|---|---|---|---|---|---|
| `openai` | 1.109.1 → 3.10.0 | 是，`langchain-openai==1.6.1` 要求 `openai>=2.45,<4` | OpenAI Chat/Responses、Embedding、Qwen 等 OpenAI-compatible 路径；2.x/3.x 改用 `httpx2`，自定义 HTTP Client 是重点风险 | 单元/全量回归通过；真实默认 Qwen 的 LangChain 与 Deep Agents 调用通过；OpenAI 原生未实测 | 候选先固定 `2.45.0`，不直接跳 3.x；补 OpenAI 原生 Chat/Responses/Embedding 与错误/流式测试 |
| `anthropic` | 0.111.0 → 1.4.0 | 是，`langchain-anthropic>=1.7` 要求 `anthropic>=0.120,<2` | Messages、stream、tool use、错误类型；1.x 官方迁移说明包含 HTTP 层切换及废弃 API 删除 | Mock 契约与全量回归通过；真实 Anthropic 未实测 | 候选先固定 `0.120.0`；真实 Provider 通过前不升 1.x |
| `google-genai` | 1.75.0 → 2.22.0 | 是，`langchain-google-genai>=4.3.7` 要求 `google-genai>=2.20,<3` | Gemini GenerateContent、Embedding、tool schema、usage、AFC/stream 行为 | Mock 契约与全量回归通过；真实 Gemini 未实测 | 候选固定 `2.20.0` 起测；必须补真实 GenerateContent/Embedding/工具/流式 |
| `mcp` | 1.28.0 → 1.30.0 | 否，项目宽松约束使解析器选择新版 | stdio/SSE/Streamable HTTP、Session 初始化、协议协商、健康检查 | 全量回归通过；真实三传输矩阵未在 C4 执行 | 保持 `1.28.0`，待独立 MCP 升级批次验证 |
| `fastapi` | 0.140.0 → 0.141.1 | 否 | 37 个源码/测试文件涉及 API、lifespan、SSE、上传与错误响应 | C4 全量回归和真实 Uvicorn health 通过 | 保持 `0.140.0`；不和 Deep Agents 同批升级 |
| `uvicorn` | 0.34.0 → 0.52.4 | 否 | 服务启动、lifespan、SSE/长连接、优雅退出 | C4 真实启动/health/正常关闭通过，长连接未测 | 保持 `0.34.0`；不和 Deep Agents 同批升级 |
| `pydantic` | 2.13.4 → 2.13.5 | 否（现有版本已满足） | 56 个源码/测试文件涉及 DTO、设置、序列化 | C4 全量回归通过 | 保持 `2.13.4` |
| `playwright` | 1.49.1 → 1.62.0 | 否 | UI 自动化驱动和浏览器二进制必须版本匹配 | C4 全量回归未覆盖真实浏览器二进制 | 保持 `1.49.1`；UI 工具链单独升级并重新安装/验证浏览器 |

官方依据：

- OpenAI Python SDK HTTPX2 迁移：<https://github.com/openai/openai-python/blob/main/httpx2.md>
- Anthropic Python SDK v1 迁移：<https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md>
- Google Gen AI SDK 变更记录：<https://github.com/googleapis/python-genai/blob/main/CHANGELOG.md>
- MCP Python SDK 版本策略：<https://github.com/modelcontextprotocol/python-sdk/blob/main/VERSIONING.md>
- FastAPI 发布记录：<https://fastapi.tiangolo.com/release-notes/>

最小组合 dry-run（2026-09-09）已通过：保留 `fastapi==0.140.0`、`uvicorn==0.34.0`、`mcp==1.28.0`、`pydantic==2.13.4`、`pydantic-settings==2.14.2`、`playwright==1.49.1`，只把 Deep Agents 强制链路固定为 `deepagents==0.7.13`、LangChain/Core/LangGraph/LangSmith 候选版本、`langchain-openai==1.6.1`、`openai==2.45.0`、`anthropic==0.120.0`、`google-genai==2.20.0`。该结果只证明依赖可解析；必须建立独立 C5 环境并重复全量、真实 Provider、长任务和回滚验证后才能修改主环境。

## 4. 后续兼容验证矩阵

| 矩阵编号 | 环境/组合 | 目标 | 状态 | 必须执行的验证 | 通过标准 |
|---|---|---|---|---|---|
| C0 | 当前 Python 3.11 + 当前四包锁定版本 | 保存可回归基线 | 已完成 | import、compileall、全量 pytest、前端测试/构建、真实默认模型会话 | 已通过；外部 LangSmith 除外 |
| C1 | 干净 Python 3.11 + `pyproject.toml` 默认依赖 | 证明主服务可重复安装 | 已完成 | 安装、`pip check`、四包版本、`src.main` import、FastAPI 健康检查、真实默认模型会话和 Flow | 全部通过；生成 25 条事件和 1 个 Snapshot；运行时依赖已补齐 |
| C2 | 隔离 Python 3.11 + Deep Agents 0.7.13 官方依赖 | 确认依赖解析、Provider 扩展和 Harness 最小执行 | 已完成（候选环境） | PyPI 解析、安装、`pip check`、`create_deep_agent` import/构造、工具可绑定离线调用 | 通过；候选快照已保存；不代表主服务已升级 |
| C3 | C2 + 项目 Provider 适配 + `code_review` 受控工具 | 验证实际集成可行性 | 进行中（实际集成工作在 C4 环境验收） | 工具调用、权限、批准/拒绝、路径隔离、事件、Trace、取消已验证；等待审批时重启恢复已通过，完整执行中恢复未完成 | 不绕过现有治理，无第二套外层状态机 |
| C4 | C3 与当前主服务组合对账 | 决定生态包统一升级版本 | 候选全量与真实模型/官方 HITL/PostgreSQL 重启恢复通过；正式长任务性能与主环境升级回滚未完成 | 2026-09-10 追加两个官方 Checkpoint 包，未改动原 C4 其余依赖；最新全量 823 passed/13 skipped，实际默认模型 agnes-2.5-flash、真实 Uvicorn/PG/RustFS 与 LangSmith 工具节点已验证 | 不据此修改主环境锁定版本；继续补齐数小时任务、并发接管及性能预算 |

## 5. 可复现命令与本次结果

```powershell
& "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -c "import importlib.metadata as m, sys; print(sys.version.split()[0]); [print(name, m.version(name)) for name in ('langchain', 'langchain-core', 'langgraph', 'langsmith')]"
& "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -m pip check
& "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -m pip index versions deepagents
```

本次结果：

- 包元数据读取成功：Python 3.11.15、LangChain 1.2.3、LangChain Core 1.2.7、LangGraph 1.0.10、LangSmith 0.10.18；OpenAI 1.109.1、Anthropic 0.111.0、Google Gen AI 1.75.0。
- `pip check` 失败：共 8 条，均来自上述三个共享环境附加工具。
- 当前配置镜像上的 `pip index versions deepagents` 失败：无匹配发行包；改用官方 PyPI 索引后解析到 `deepagents==0.7.13`。
- 官方 PyPI 组合 `pip install --dry-run --ignore-installed` 失败：`deepagents==0.7.13` 要求 `langchain>=1.3.18`，与主服务锁定的 `langchain==1.2.3` 产生 `ResolutionImpossible`；该命令未修改环境。
- C4 完整项目依赖 dry-run 失败：项目包 `enterprise-ai-qa-agent-server==0.1.0` 自身固定 `langchain==1.2.3`，与候选 `langchain==1.4.0` 产生 `ResolutionImpossible`；该命令未修改环境。
- 去除项目旧生态固定项后，官方 PyPI 完整候选 dry-run 解析通过；候选集合包含 `deepagents==0.7.13`、`langchain==1.4.0`、`langchain-core==1.6.2`、`langgraph==1.2.11`、`langsmith==0.12.2`，同时解析到 OpenAI 3.10、Anthropic 1.4、Google GenAI 2.22、MCP 1.30、FastAPI 0.141 等版本。该结果只证明依赖可解析，不证明项目运行兼容；命令未修改环境。
- C4 隔离环境 `C:\Users\32734\AppData\Local\Temp\enterprise-ai-qa-c4-20260909-b` 安装候选依赖后，`pip check` 通过，`src.main`/适配器导入通过，Deep Agents 官方 Harness 使用实现 `bind_tools` 的离线替身调用通过（2 条消息），生态/上下文专项 `40 passed`，后端全量 `781 passed, 10 skipped, 1 warning`（27.07s）。候选 freeze 已保存为 `Agent_Server/docs/python311-deepagents-c4-freeze-20260909.txt`。
- C4 候选第一次使用普通 `FakeMessagesListChatModel` 调用失败：该类继承的 `BaseChatModel.bind_tools` 直接抛出 `NotImplementedError`。Deep Agents 默认装配规划、文件系统、子代理等工具，并由 LangChain Agent Factory 调用模型的 `bind_tools(...)`，所以这是测试替身缺少工具绑定能力，不是依赖冲突。实现该协议的离线替身调用通过。
- C4 真实运行验证通过：候选 FastAPI/Uvicorn 完成应用生命周期初始化，`GET /api/v1/health` 返回 200、`postgres_ok=true`、`memory_backend=postgres_pgvector`，随后正常关闭；数据库默认 `qwen3.8-max-0902` 经升级后的 `ChatOpenAI` 适配器实际返回 `C4_OK` 并保留 usage；同一真实模型交给 `create_deep_agent` 后返回 `DEEPAGENT_C4_OK`，证明生产候选模型实现了 `bind_tools` 并可被 Deep Agents Harness 使用。以上未输出 API Key。
- C4 尚未验证 LangSmith 外部上报、长任务性能/取消/恢复和依赖回滚，故不能据此升级主环境或开放 Deep Agents 主路径。
- 第一次版本探测使用 `langgraph.__version__` 失败，因为该模块未公开此属性；已改用 `importlib.metadata.version('langgraph')` 并成功。该失败不代表 LangGraph 导入失败。
- 干净 C1 环境的运行时依赖快照已保存为 `Agent_Server/docs/python311-main-service-c1-freeze-2026-09-08.txt`；该文件不包含项目自身的 editable git 行，避免把本地路径误当成可复现依赖。
- C2 候选环境快照已保存为 `Agent_Server/docs/python311-deepagents-c2-freeze-2026-09-08.txt`。候选组合为 Deep Agents 0.7.13、LangChain 1.4.0、LangChain Core 1.6.2、LangGraph 1.2.11、LangSmith 0.12.2，并额外安装 `langchain-openai==1.6.1`。
- C2 第一次用 OpenAI provider 构造 agent 时因未安装 `langchain-openai` 失败；补装官方 provider 包后因环境未提供 OpenAI key 无法构造真实 OpenAI client。随后使用只在命令中定义的 tool-capable fake chat model，完成 `create_deep_agent` 构造和一次离线 `invoke`，结果为 2 条消息，证明 harness 图和工具绑定路径可执行。该测试不证明真实 Provider 网络调用。
- 2026-09-09：锁定版 `langsmith==0.10.18` 实际 SDK 录制客户端测试通过 root 与 node 两层请求；未设置 `LANGCHAIN_TRACING_V2` 时 root 也已发送，敏感值未进入请求。

## 6. 当前剩余出口

1. P0-07 仍阻塞：不得把 `deepagents` 写入主服务 optional extra；需先完成阶段 2—3 协调升级和 C4 回滚证据。
2. 阶段 1 已验收（以主工程方案最新台账为准），LangSmith Key 已配置；DA-E4 已补齐真实工具父子 Trace。不能把历史“未配 Key”的记录当作当前阻塞。
3. C3/C4 的工具、权限、审批、路径隔离、全量及等待审批时重启恢复已有证据；尚需正式数小时任务、并发接管、依赖回滚演练，才可决定主环境统一升级。

## 7. 2026-09-10 官方 HITL / PostgreSQL 增量

- `pip install --dry-run --index-url https://pypi.org/simple langgraph-checkpoint-postgres` 只提出新增 `langgraph-checkpoint-postgres==3.1.2` 和 `psycopg-pool==3.3.1`，未升级 C4 现有 Provider 或框架包。
- 上述两个包仅安装在 C4；`pip check` 通过，主 Python 3.11 环境未安装 Deep Agents。复现文件：`deepagents-c4-checkpoint-requirements-20260910.txt`，引用原 C4 freeze 后叠加两条固定依赖。
- Windows psycopg 异步连接不支持 Proactor，实际使用官方同步 PostgresSaver 与项目既有线程 IO 模式；不改全局事件循环，不重写 checkpoint SQL 或序列化。
- 当前全量：主环境 `803 passed, 33 skipped, 1 warning in 46.87s`；C4 `823 passed, 13 skipped, 1 warning in 33.65s`；两环境入口导入与编译通过。
- 真实服务批准/拒绝验收都包含“等待审批后终止进程、重启再裁决”，同一个 ToolJob 到 completed/denied；快照、Artifact、模型结果和 Trace 已回读。执行中的副作用幂等、跨 Worker 与长时性能仍未验收。

## 8. 2026-09-10 DA-E5 工具执行持久化增量

- 未升级任何依赖、未修改数据库 schema、未新增环境变量；主环境依赖组合保持不变，C4 `pip check` 继续通过。
- Deep Agents 治理路径为每个 `session_id + turn_id + call_id` 建立稳定 UUIDv5 ToolJob；PostgreSQL 使用 insert-if-absent 和条件 `UPDATE ... RETURNING` 原子领取。legacy 路径默认行为不变。
- completed/partial/failed/denied/cancelled 终态结果直接从 PostgreSQL 回放；running/resume_requested 等无终态证据的执行不自动重跑，必须先对账外部副作用。
- Windows 双进程真实 PostgreSQL 争抢测试通过：同一 Job 只有一个进程领取成功；进程退出后的未知结果转为 resume_requested 且不可再次领取。超时阈值改用数据库 `now()` 计算，避免无时区应用时间与 TIMESTAMPTZ 的解释偏差。
- 最新全量（最终代码状态重跑）：主环境 `811 passed, 34 skipped, 1 warning in 39.46s`；C4 `831 passed, 14 skipped, 1 warning in 31.60s`；前端 `33 passed`；两环境编译和 `src.main` 入口导入通过；C4 启用 Deep Agents/PostgreSQL Checkpointer 的真实 health 启动检查通过。
- 本批外部默认模型/HITL HTTP 复验因执行许可被安全审查拒绝，未执行且不沿用上一批结果冒充本批证据；DA-E5 仍需 Session/turn 跨 Worker 所有权、任意取消恢复、Stage 级对账以及 4/24 小时长时验证。

## 9. 2026-09-10 审批续跑租约增量

- 依赖版本与数据库 schema 不变；Approval 现有 JSONB metadata 保存续跑 owner、token、数据库时钟到期时间和完成标记。
- 多 Worker 对同一已裁决 Approval 通过 PostgreSQL 条件 UPDATE 原子领取；活动任务心跳续租，token 条件完成；取消/崩溃不误标完成，租约到期后可重新领取。
- 配置统一为 `ORCHESTRATION__APPROVAL_CONTINUATION_LEASE_SECONDS=120` 和 `ORCHESTRATION__APPROVAL_CONTINUATION_HEARTBEAT_SECONDS=30`，心跳周期必须严格短于租约。
- 真实 PostgreSQL 完整并发测试 `4 passed`；最终全量为主环境 `815 passed, 34 skipped`、C4 `835 passed, 14 skipped`、前端 `33 passed`，双环境入口/编译、C4 pip check 与真实 health 均通过。
- 当前只完成 Approval continuation 的所有权。Session/Snapshot/Event 写入尚未用相同 token 做 fencing，因此暂不自动扫描/接管到期任务，也不宣称通用跨 Worker exactly-once。

## 10. 2026-09-10 审批续跑写入 fencing 增量

- 失败先行：旧 Worker 在租约被新 Worker 接管后仍可无条件覆盖 Session，内存回归复现 `new-owner -> stale-owner`。
- 修复：Approval continuation 路径的 `save_session`、`save_snapshot`、`append_event` 显式携带 approval id 与 lease token；InMemory 使用既有锁校验，PostgreSQL 使用数据库时钟和带行锁的 token/expiry 校验。
- 失去租约的写入抛出 `ContinuationLeaseLostError`，续跑任务停止最终化并记录结构化日志；普通会话写入契约保持兼容。
- 验证：fencing 目标回归及既有审批续跑回归 `3 passed`；真实 PostgreSQL 并发文件 `4 passed`；代码编译通过。
- 边界：该批只覆盖审批续跑的 Session/Snapshot/Event 写入；外部记忆、TestRun Stage 和通用 turn owner 尚未统一，未开启过期 continuation 自动扫描。
- 真实运行补验：首次 PostgreSQL Session 创建暴露 `ON CONFLICT WHERE` 空参数类型无法推断，已改为同事务 `FOR UPDATE` 租约校验并恢复原 upsert；随后 health 200、Session 创建 200、数据库默认模型消息 200，返回 `DA_E5_FENCING_OK`，终态 completed，31 events、10 flow stages，服务正常关闭。

## 11. 2026-09-10 普通 Turn 跨 Worker 所有权增量

- 两个 Service 同时读取 idle Session 的失败回归证明原进程内锁会双跑；新增 Session metadata 租约，PostgreSQL 条件 UPDATE 只允许一个 worker claim。
- 普通 turn 的 Session/Snapshot/Event 写入携带 token；旧 token 在接管后不能落库。loser 复用既有 busy 策略，不新增调度器。
- 配置新增 `ORCHESTRATION__TURN_EXECUTION_LEASE_SECONDS=120` 与 `ORCHESTRATION__TURN_EXECUTION_HEARTBEAT_SECONDS=30`；API 输出过滤 owner/token/expiry。
- Deep Agents 专项 `27 passed, 20 skipped`（含内存 Store 过期完成拒绝）；真实 PostgreSQL 并发 `5 passed`；Session 回归 `20 passed`；真实默认模型返回 `DA_E5_TURN_OWNER_OK`，终态 completed、30 events、无租约字段泄露。
- 最终全量：主环境 `818 passed, 35 skipped`，C4 `838 passed, 15 skipped`，前端 `33 passed`；主/C4 编译与入口导入、C4 `pip check` 全部通过。
- 手工 resume、Coordinator、服务启动扫描、外部 Memory fencing 和 4/24 小时 soak 仍未完成，DA-E5 不关闭。

## 12. 2026-09-11 手工 resume 跨 Worker 所有权增量

- `resume_session` 现在从最新 Snapshot 读取原 `turn_id`，复用通用 turn lease、heartbeat 和 fenced finalize；取消不主动完成租约，允许到期后恢复 Worker 接管。
- 双 Service 同时恢复同一 interrupted checkpoint 的失败先行回归通过，`runtime.resume_turn` 只调用一次；恢复/中断/审批 continuation 筛选回归 `15 passed, 4 skipped`。
- Coordinator 子会话、服务启动扫描、外部 Memory fencing、执行中 Stage 对账与 4/24 小时 soak 仍未完成，DA-E5 不关闭。
