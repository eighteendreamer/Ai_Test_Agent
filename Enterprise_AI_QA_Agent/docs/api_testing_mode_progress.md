# API Testing Mode 开发进度

> 最后更新：2026-08-20
> 参考方案：[api_testing_mode_test_plan.md](./api_testing_mode_test_plan.md)

## 一、当前结论

API Testing Mode 已从骨架实现进入可运行、可验证状态。此前仍标记为“待完善”的动态登录、InputBinding、重试、验证/评估、OpenAPI/Postman 解析、报告产物和前端适配均已在当前代码中实现；本文件不再把这些能力列为待办。

当前版本的质量门仍以真实运行结果为准：单元测试覆盖模式内部组件和运行时契约，外部 API、真实凭证、PostgreSQL/RustFS/Redis 等依赖仍需在对应环境执行集成或容量测试。

## 二、能力状态

| 能力域 | 状态 | 当前实现依据 |
|---|---|---|
| 模式注册与入口 | ✅ 已完成 | `manifest.py`、`agent.py`、`tools.py`、`skills.py`，入口为 `api-test-runner` |
| 项目与文档发现 | ✅ 已完成 | `project_locator.py`、`api_docs_service.py`，支持附件、URL、项目绑定和候选澄清 |
| 文档解析 | ✅ 已完成 | `doc_parser.py` 与文档服务支持 Markdown、OpenAPI/Swagger JSON/YAML、Postman Collection |
| 文档索引治理 | ✅ 已完成 | `doc_parser.py` 提供内存缓存、缓存失效、跨文档端点去重和请求体 schema 提取 |
| 范围与选择 | ✅ 已完成 | `endpoint_scope_service.py`、`selection_resolver.py`，支持 core/all/manual/single 和澄清状态 |
| 前置条件与凭证 | ✅ 已完成 | `precondition_resolver.py`、`credential_manager.py`，支持 Bearer/API Key/Basic/Cookie |
| 动态登录 | ✅ 已完成 | `executor.py` 在认证任务成功后提取 token 并创建动态凭证会话 |
| 依赖与数据传递 | ✅ 已完成 | `dependency_planner.py`、`coordinator.py`，支持 auth 优先、InputBinding、资源锁 |
| 并发与重试 | ✅ 已完成 | `coordinator.py`，读请求并行、写请求串行，并按 `ExecutionPolicy.max_retries` 重试可重试失败 |
| HTTP 执行与断言 | ✅ 已完成 | `executor.py`，覆盖状态码、JSON 字段、响应头、正文和响应时间断言 |
| 验证与评估 | ✅ 已完成 | `verification.py` 的 5 类质量门；`evaluation.py` 的覆盖率、失败分级、质量分数和建议 |
| 报告与证据 | ✅ 已完成 | `report_builder.py`、`report_template.py`，输出结构化/Markdown 报告和任务证据产物 |
| 运行时集成 | ✅ 已完成 | `runtime.py` 已接入澄清、执行、报告、验证、评估、产物元数据和跨 turn 状态 |
| Agent 协同 | ✅ 已完成 | `subagent_coordinator.py` 与 manifest 中的 API clarifier、analyst、planner、executor、failure analyst |
| 前端展示 | ✅ 已完成 | API 模式输出沿用结构化报告、事件控制台和凭证交互；Flow/Worker 下钻另有专门回归测试 |

## 三、测试与验证证据

### 后端

使用指定解释器：

```text
E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe
```

依赖安装：

```text
cd Agent_Server
python -m pip install -e .[dev]
```

项目声明的生产依赖已覆盖源码直接使用的 FastAPI、Uvicorn、LangGraph、Pydantic、HTTP 客户端、模型 SDK、SQLAlchemy、PostgreSQL、Neo4j 驱动（Memgraph）、MySQL、Redis、MCP、加密和 Excel 依赖，对象存储走 boto3（S3 协议访问 RustFS）；`dev` extra 另外声明 `pytest` 与 `pytest-asyncio>=1.4.0`。

性能协调器测试已改为 `pytest.mark.asyncio` + `await`，不再依赖 pytest-asyncio 1.4.0 已移除的 `event_loop` fixture。

已覆盖的重点测试文件包括：

- `test_api_mode_skills.py`
- `test_api_doc_resolution.py`
- `test_api_doc_project_binding.py`
- `test_project_management_api.py`
- `test_project_overview.py`
- `test_session_project_binding.py`
- `test_performance_coordinator.py`

### 前端

已新增 `npm test` 和 `npm run test:watch`，使用 Vitest、Vue Test Utils、jsdom。`src/features/flow/flow.test.ts` 覆盖：

- Worker 详情抽屉点击下钻并发出 child session ID；
- 无 child session 时不显示下钻操作；
- metadata 与 graph state 的 Worker 合并及 parent turn 过滤；
- Worker 状态映射、节点 ID；
- Flow 窗口 URL 编码；
- Flow BroadcastChannel 发布与订阅。

## 四、仍需关注的限制

这些是当前真实边界，不再与已实现功能混列：

1. API 外部系统、真实凭证和网络异常的端到端验证需要目标服务和测试数据，单元测试中的 httpx 替身不能替代真实环境结果。
2. PostgreSQL 大规模生命周期容量测试属于独立的 live 测试，不应与默认单元测试结果混合解读。
3. 前端目前有 Flow 单元/组件行为测试，但没有浏览器级跨视图 E2E 和视觉回归门禁。
4. 当前 TypeScript 全量类型检查仍有仓库既有基线错误，包括 ES2020 配置与 `.at`/`replaceAll` 的不一致、部分 Pinia 推导错误、第三方类型缺失；这不阻断当前 Vite 构建和 Vitest 运行，但应作为独立技术债处理。
5. 指定 Anaconda 环境的 `pip check` 仍报告 browser-use、mem0ai、mitmproxy 等其他工作负载的既有版本冲突；这些冲突不属于本项目 `pyproject.toml` 的直接依赖解析结果。

## 五、后续工作

1. 在 CI 中固定执行 `Agent_Server` 的 `.[dev]` 安装、后端全量 pytest 和 `agent_web` 的 `npm test`/`npm run build`。
2. 单独治理前端 TypeScript 基线错误，完成 `moduleResolution`、目标库和第三方声明的统一。
3. 为 Flow/Worker 下钻补充浏览器级 E2E，验证真实路由切换、SSE 重连、子会话返回和移动端布局。
4. 对真实 API 环境建立脱敏、版本化的接口评测集，并记录执行环境、凭证来源、通过标准和证据引用。

## 六、变更记录

| 日期 | 变更 |
|---|---|
| 2026-08-20 | 后端 `dev` extra 补充 `pytest-asyncio>=1.4.0`，并在指定 Python 3.11 环境完成 editable 安装核验。 |
| 2026-08-20 | 性能协调器测试移除 `event_loop` fixture 依赖，改用 pytest-asyncio 1.4.0 支持的异步测试写法。 |
| 2026-08-20 | 前端接入 Vitest 测试命令，新增 Flow/Worker 下钻与窗口联动专门测试。 |
| 2026-08-20 | Flow 轨迹改为按 turn 事件动态投影：只生成实际观测到的阶段和阶段边，保留未知 phase，并让 Worker 来源节点随实际数据生成。 |
| 2026-08-20 | 重写本进度文档，按当前代码、测试和实际限制更新状态，删除已完成能力的陈旧待办。 |
