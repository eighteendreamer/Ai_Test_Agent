# LangChain 生态依赖兼容与环境隔离矩阵

日期：2026-09-08
适用解释器：`E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe`
状态：进行中（当前主服务组合已验证；Deep Agents 候选组合尚未建立隔离环境验证）

## 1. 结论与边界

当前主服务继续锁定已经通过全量回归和真实默认模型会话验证的组合，不为安装 Deep Agents 直接升级：

| 包 | 项目声明/当前环境 | 当前状态 | 证据 |
|---|---:|---|---|
| Python | 3.11.15 | 已验证 | 指定解释器实际输出 |
| `langchain` | 1.2.3 | 已验证 | 包元数据、后端全量测试、真实会话 |
| `langchain-core` | 1.2.7 | 已验证 | 包元数据、后端全量测试、真实会话 |
| `langgraph` | 1.0.10 | 已验证 | 包元数据、后端全量测试、真实会话 |
| `langsmith` | 0.10.18 | 已验证（本地适配） | 观测契约测试；真实外部上报尚未验证 |
| `deepagents` | 未安装、未声明 | 阻塞 | 当前索引未返回发行包；官方主线依赖高于项目锁定组合 |

当前 Provider SDK 基线：

| Provider SDK | 项目约束 | 当前环境 | 状态 |
|---|---|---:|---|
| `openai` | `>=1.60.0` | 1.109.1 | 现有 Provider 路径和默认模型真实会话已验证；LangChain Provider Adapter 尚未验证 |
| `anthropic` | `>=0.40.0` | 0.111.0 | 包元数据已验证；真实 Provider 会话未在本批执行 |
| `google-genai` | `>=1.0.0` | 1.75.0 | 包元数据已验证；真实 Provider 会话未在本批执行 |

这份矩阵只证明“当前组合在当前开发环境可运行”，不等价于干净环境可重复安装。干净环境解析、安装和真实运行仍是 P0-06 的退出条件。

## 2. 依据到决策

| 依据来源 | 已核对事实 | 本项目决策 |
|---|---|---|
| `Agent_Server/pyproject.toml` | 主服务锁定 `langchain==1.2.3`、`langchain-core==1.2.7`、`langgraph==1.0.10`、`langsmith==0.10.18` | 保持当前生产候选基线，不在阶段 0 顺手升级 |
| `python311-runtime-freeze-2026-09-08.txt` | 开发环境包含大量非主服务工具包 | 完整快照只作取证，不作为主服务 lock 输入 |
| 指定解释器包元数据 | 四个生态包版本与项目声明一致 | 当前组合标记为“已验证” |
| LangChain 官方 Deep Agents Quickstart | 安装包名为 `deepagents`，入口为 `from deepagents import create_deep_agent` | 不自创包名或 API；仅在官方包可解析后建立试点 |
| Deep Agents 官方仓库 `libs/deepagents/pyproject.toml`（2026-09-08 拉取） | 主线 0.7.13 要求 Python `>=3.11,<4.0`、`langchain>=1.4.0`、`langchain-core>=1.6.2`、`langsmith>=0.12.2`，许可证 MIT | Python 版本兼容，但当前 LangChain/LangSmith 组合不兼容；必须在隔离环境先做升级矩阵，不能直接加入默认或可选依赖组 |
| `pip index versions deepagents` | 当前配置的软件包索引返回 `No matching distribution found for deepagents` | 当前环境不安装 Deep Agents；先查明索引源/镜像同步状态，再验证候选版本 |

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

“已隔离（设计结论）”表示依赖边界和处置路线已确定，不表示当前共享开发环境的 `pip check` 已通过。验证完成必须满足：新建最小主服务环境、仅安装项目声明依赖、`pip check` 通过、导入冒烟通过、服务真实启动与会话通过。

## 4. 后续兼容验证矩阵

| 矩阵编号 | 环境/组合 | 目标 | 状态 | 必须执行的验证 | 通过标准 |
|---|---|---|---|---|---|
| C0 | 当前 Python 3.11 + 当前四包锁定版本 | 保存可回归基线 | 已完成 | import、compileall、全量 pytest、前端测试/构建、真实默认模型会话 | 已通过；外部 LangSmith 除外 |
| C1 | 干净 Python 3.11 + `pyproject.toml` 默认依赖 | 证明主服务可重复安装 | 未进行 | 安装、`pip check`、四包版本、import、全量 pytest、FastAPI 健康检查和真实会话 | 全部通过，无共享工具冲突 |
| C2 | 隔离 Python 3.11 + Deep Agents 候选版本及其官方下限 | 确认依赖解析与导入 | 阻塞 | 解析发行版本、安装、`pip check`、`create_deep_agent` import、无模型最小构建 | 包索引可用且全部通过 |
| C3 | C2 + 项目 Provider 适配 + `code_review` 受控工具 | 验证实际集成可行性 | 未进行 | 工具调用、权限、审批、路径隔离、事件、Trace、取消与恢复 | 不绕过现有治理，无第二套外层状态机 |
| C4 | C3 与当前主服务组合对账 | 决定生态包统一升级版本 | 未进行 | 全量回归、真实会话、性能、回滚演练 | 质量不降、性能预算达标、可一键回滚 |

## 5. 可复现命令与本次结果

```powershell
& "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -c "import importlib.metadata as m, sys; print(sys.version.split()[0]); [print(name, m.version(name)) for name in ('langchain', 'langchain-core', 'langgraph', 'langsmith')]"
& "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -m pip check
& "E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe" -m pip index versions deepagents
```

本次结果：

- 包元数据读取成功：Python 3.11.15、LangChain 1.2.3、LangChain Core 1.2.7、LangGraph 1.0.10、LangSmith 0.10.18；OpenAI 1.109.1、Anthropic 0.111.0、Google Gen AI 1.75.0。
- `pip check` 失败：共 8 条，均来自上述三个共享环境附加工具。
- `pip index versions deepagents` 失败：当前索引无匹配发行包。
- 第一次版本探测使用 `langgraph.__version__` 失败，因为该模块未公开此属性；已改用 `importlib.metadata.version('langgraph')` 并成功。该失败不代表 LangGraph 导入失败。

## 6. 阶段 0 剩余出口

1. 建立 C1 干净主服务环境并完成真实运行验证。
2. 明确当前 pip 索引配置为何无法解析 `deepagents`，不得记录或提交索引凭据。
3. 在隔离环境完成 C2 后，才确定 `deepagents` 的版本区间并写入可选依赖。
4. C2 如果要求升级 LangChain/LangGraph/LangSmith，先形成独立升级批次和回滚证据，不能与 Deep Agent 业务接入混为一个变更。
