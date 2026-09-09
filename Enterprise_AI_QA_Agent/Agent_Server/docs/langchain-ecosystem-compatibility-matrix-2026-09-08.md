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
| C3 | C2 + 项目 Provider 适配 + `code_review` 受控工具 | 验证实际集成可行性 | 未进行 | 工具调用、权限、审批、路径隔离、事件、Trace、取消与恢复 | 不绕过现有治理，无第二套外层状态机 |
| C4 | C3 与当前主服务组合对账 | 决定生态包统一升级版本 | 候选环境全量回归与真实默认模型冒烟通过，性能/回滚未进行 | 隔离 C4 环境安装候选生态及项目其余依赖，`pip check`、项目入口导入、Deep Agents 离线 Harness、LangChain/上下文专项、后端全量、真实 Uvicorn 健康检查、数据库默认 Qwen 的 LangChain 与 Deep Agents 调用均通过；长任务性能、LangSmith 外部上报和回滚演练仍待执行 | 质量不降、性能预算达标、可一键回滚 |

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
2. 阶段 1 仍需用户配置 LangSmith Key 后完成真实云端上报、控制台父子树、外部 URL 与性能预算验证。
3. C3/C4 必须在独立环境验证 Provider 适配、受控工具、权限、审批、路径隔离、全量回归和回滚后，才能决定统一生态版本。
