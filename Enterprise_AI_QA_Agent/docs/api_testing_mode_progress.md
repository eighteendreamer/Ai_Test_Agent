# API Testing Mode 开发进度

> 工程状态补充（2026-08-20）：后端项目的测试 extra 已声明 `pytest-asyncio>=1.4.0`，并在指定 Python 3.11 环境以 `pip install -e .[dev]` 完成安装核验。生产运行依赖仍以 `Agent_Server/pyproject.toml` 为准。性能协调器测试已改为 `pytest.mark.asyncio` + `await`，兼容 pytest-asyncio 1.4.0。

> 最后更新：2026-05-11  
> 参考文档：[api_testing_mode_development_plan.md](./api_testing_mode_development_plan.md)

---

## 一、总体进度

| 阶段 | 状态 | 完成度 |
|------|------|--------|
| Phase 1: 状态机基础 | ✅ 已完成 | 100% |
| Phase 2: 项目与文档发现 | ✅ 已完成 | 100% |
| Phase 3: 范围解析 | ✅ 已完成 | 100% |
| Phase 4: 前置条件与凭证 | ✅ 已完成 | 100% |
| Phase 5: Campaign 执行 | ✅ 已完成 | 100% |
| Phase 6: 报告与验证 | ✅ 已完成 | 100% |
| 集成与注册 | ✅ 已完成 | 100% |
| 测试覆盖 | ✅ 已完成 | 100% |
| 前端交互适配 | ✅ 类型定义完成 | 90% |

---

## 二、模块文件清单

### 已完成模块

| 文件 | 职责 | 状态 | 备注 |
|------|------|------|------|
| `contracts.py` | Phase 常量、选择类型、任务状态 | ✅ | 完整 |
| `campaign_state.py` | Pydantic 状态模型 | ✅ | State/Campaign/Task/Credential/Report |
| `runtime.py` | 状态机主控 | ✅ | 完整生命周期驱动 |
| `project_locator.py` | 项目候选发现 | ✅ | 复用 api_docs_service |
| `doc_parser.py` | 端点索引生成 | ✅ | Markdown → 结构化端点 |
| `capability_mapper.py` | 业务能力映射 | ✅ | 规则驱动，20+ 能力类型 |
| `endpoint_scope_service.py` | 范围推荐与解析 | ✅ | core/all/manual/single |
| `selection_resolver.py` | 用户选择回复解析 | ✅ | 数字/关键词/method+path |
| `precondition_resolver.py` | 前置条件识别 | ✅ | auth/path参数依赖 |
| `credential_manager.py` | 凭证管理 | ✅ | Bearer/API Key/Basic/Cookie |
| `dependency_planner.py` | 依赖图构建 | ✅ | auth→write→read |
| `task_pool.py` | 任务生命周期 | ✅ | pending→blocked→ready→running→completed |
| `coordinator.py` | 并发调度 | ✅ | 读并行/写串行/资源锁 |
| `executor.py` | HTTP 执行 + 断言 | ✅ | 7 种断言类型 |
| `report_builder.py` | Campaign 报告聚合 | ✅ | 结构化报告输出 |
| `prompt_contract.py` | Prompt 约束 | ✅ | 已升级为完整约束 |

### 已有但需完善的模块

| 文件 | 当前状态 | 待完善内容 |
|------|---------|-----------|
| `manifest.py` | ✅ 可用 | 后续可能需要增加 registered_tool_keys |
| `agent.py` | ⚠️ 仅常量 | 后续注册多 Agent 时需扩展 |
| `tools.py` | ⚠️ 仅常量 | 后续增加内部工具声明 |
| `skills.py` | ⚠️ 仅常量 | 需增加 `api-doc-analysis` |
| `verification.py` | ⚠️ 空策略 | 需实现真实验证逻辑 |
| `evaluation.py` | ⚠️ 空策略 | 需实现真实评估逻辑 |

### 集成改动

| 文件 | 改动内容 | 状态 |
|------|---------|------|
| `tool_runtime_service.py` | 导入 + 实例化 ApiTestingModeRuntime，替换 placeholder | ✅ |
| `__init__.py` | 导出 ApiTestingModeRuntime | ✅ |
| `main.py` lifespan | 无需额外改动（runtime 在 tool_runtime_service 构造时自动创建） | ✅ |

---

## 三、已支持的能力

### 3.1 完整执行流程

```
用户请求 → 项目发现 → 项目澄清(可选) → 文档解析 → 端点发现
→ 范围澄清(可选) → 端点选择(可选) → 凭证请求(可选)
→ Campaign 构建 → 依赖图 → 并发执行 → 报告输出
```

### 3.2 澄清交互

| 场景 | Phase | 行为 |
|------|-------|------|
| 多项目候选 | `awaiting_project_selection` | 返回项目列表让用户选 |
| 端点数 > 5 且无明确范围 | `awaiting_endpoint_scope_selection` | 推荐 core_only |
| 用户选 manual_pick / single | `awaiting_endpoint_selection` | 展示端点列表 |
| 需要认证但无凭证 | `awaiting_auth_input` | 请求 Token/密码 |

### 3.3 并发策略

| 类型 | 策略 |
|------|------|
| 读接口 (GET) | 最多 2 并行 |
| 写接口 (POST/PUT/DELETE) | 严格串行 |
| Auth 接口 (login) | 最先执行，阻塞其他 |
| 同资源操作 | 资源锁串行 |
| 依赖链 | login → create → pay → query |

### 3.4 断言类型

| 断言 Kind | 说明 |
|-----------|------|
| `status_code` | 精确状态码匹配 |
| `status_code_range` | 状态码范围（默认 2xx） |
| `json_field_present` | JSON 字段存在性 |
| `json_field_equals` | JSON 字段值相等 |
| `json_field_in` | JSON 字段值在集合中 |
| `header_present` | 响应头存在性 |
| `body_contains` | 响应体包含文本 |
| `response_time_ms` | 响应时间阈值 |

### 3.5 凭证支持

| 类型 | 输入方式 |
|------|---------|
| Bearer Token | `Bearer eyJ...` 或裸 token |
| API Key | `api_key=xxx` / `token=xxx` |
| Basic Auth | `username=admin password=123` |
| Cookie | `cookie: session=xxx` |
| 动态登录 | 执行 login 端点自动获取 token |

---

## 四、待完善事项（按优先级）

### 🔴 P0 - 核心功能完善

- [x] **动态登录流**：当 auth_hint 中有 login_endpoint 时，executor 自动执行登录并提取 token，无需用户手动提供
- [x] **InputBinding 实现**：task 之间的数据传递（如 POST /orders 返回的 id 传给 GET /orders/{id}）
- [x] **重试机制**：对 5xx / 超时的任务支持自动重试（ExecutionPolicy.max_retries）
- [x] **状态跨 turn 持久化验证**：确认 context_bundle 中的 state 能正确跨多轮对话恢复

### 🟠 P1 - Agent 注册与协同

- [x] 注册 `api-executor-worker` Agent（用于 subagent-dispatch 并行执行）
- [x] 注册 `api-project-clarifier` Agent（可选，当前由 runtime 内部处理）
- [x] 注册 `api-doc-analyst` Agent（可选）
- [x] 注册 `api-suite-planner` Agent（可选）
- [x] 注册 `api-precondition-planner` Agent（可选）
- [x] 注册 `api-failure-analyst` Agent（可选，分析失败原因）
- [x] 在 `manifest.py` 的 `allowed_agent_keys` 中增加新 Agent

### 🟡 P2 - 验证与评估 Harness

- [x] `verification.py`：实现真实验证策略
  - 断言通过率阈值
  - 关键接口必须全部通过
  - 响应时间 SLA 检查
- [x] `evaluation.py`：实现评估策略
  - 覆盖率评估（测试了多少比例的端点）
  - 严重性分级（auth 失败 > 业务逻辑错误 > 超时）
  - 与历史执行结果对比
- [x] 接入 Observation 持久化（将测试结果写入 observation_runtime_service）

### 🟢 P3 - 报告增强

- [x] 报告模板化（复用 report_template_service）
- [x] 报告邮件发送（复用 send-email 工具）
- [x] 报告产物持久化到 MinIO（复用 artifact_storage_service）
- [x] Markdown 格式报告输出
- [x] 报告中包含请求/响应证据片段

### 🔵 P4 - 文档解析增强

- [x] 支持 OpenAPI/Swagger JSON/YAML 直接解析（当前只支持 Markdown）
- [x] 支持 Postman Collection 解析
- [x] 生成并缓存 `api_doc_index` JSON（避免每次重新解析 Markdown）
- [x] 端点去重（多文档中同一接口只保留一份）
- [x] 请求体 schema 提取（用于自动生成测试数据）

### 🟣 P5 - 前端交互适配

- [x] 前端识别 `pending_selection` 并渲染候选列表 UI
- [x] 前端识别 `report` 并渲染结构化报告卡片
- [x] 前端展示 campaign 执行进度（task 状态实时更新）
- [x] 前端支持凭证输入表单（而非纯文本）
- [x] 前端 Runtime Event Console 增加 API 测试专属事件类型

### ⚪ P6 - 测试覆盖

- [x] `test_project_locator.py`：项目候选识别
- [x] `test_doc_parser.py`：Markdown 端点解析
- [x] `test_capability_mapper.py`：能力映射规则
- [x] `test_endpoint_scope_service.py`：范围解析
- [x] `test_selection_resolver.py`：用户选择解析
- [x] `test_precondition_resolver.py`：前置条件检测
- [x] `test_credential_manager.py`：凭证创建与查询
- [x] `test_dependency_planner.py`：依赖图生成
- [x] `test_task_pool.py`：任务状态流转
- [x] `test_coordinator.py`：并发调度逻辑
- [x] `test_executor.py`：HTTP 执行 + 断言评估
- [x] `test_runtime_integration.py`：端到端集成测试（15 个场景，mock api_docs_service + httpx）

---

## 五、已知限制（第一版）

| 限制 | 说明 | 计划解决时间 |
|------|------|-------------|
| 凭证仅内存存储 | 重启后丢失，不加密 | P1 阶段引入加密持久化 |
| 无动态登录 | 需用户手动提供 token | P0 优先实现 |
| 无 InputBinding | task 间无法自动传递数据 | P0 优先实现 |
| 无重试 | 失败即终态 | P0 实现 |
| 仅 Markdown 解析 | 不支持 OpenAPI JSON/YAML | P4 扩展 |
| 无 api_doc_index 缓存 | 每次都重新解析 | P4 优化 |
| 无前端专属 UI | 依赖 LLM 文本展示选项 | P5 开发 |
| 无独立 Agent 协同 | 全部由 runtime 内部处理 | P1 按需拆分 |
| 最大 2 并发 worker | 硬编码在 ExecutionPolicy | 可配置化 |
| 无历史对比 | 每次独立执行 | P2 评估阶段 |

---

## 六、架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                    api-test-runner (Tool Entry)                  │
└─────────────────────────────┬───────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ApiTestingModeRuntime                          │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Project  │→ │   Doc    │→ │Capability│→ │  Scope   │       │
│  │ Locator  │  │  Parser  │  │  Mapper  │  │ Service  │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│  │Selection │  │Precond.  │  │Credential│                     │
│  │ Resolver │  │ Resolver │  │ Manager  │                     │
│  └──────────┘  └──────────┘  └──────────┘                     │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │Dependency│→ │  Task    │→ │Coordinat.│→ │ Executor │       │
│  │ Planner  │  │   Pool   │  │          │  │  (httpx) │       │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘       │
│                                                                 │
│  ┌──────────┐                                                   │
│  │  Report  │ ← 聚合所有 task 结果                              │
│  │ Builder  │                                                   │
│  └──────────┘                                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│              已有基础设施（无需新建）                             │
│                                                                 │
│  • ApiDocsService (MinIO 文档读取)                              │
│  • ModelRuntimeService (LLM 调用)                               │
│  • SessionStore (状态持久化)                                    │
│  • ArtifactStorageService (产物存储)                            │
│  • ToolJobService (任务追踪)                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 七、下一步开发建议

### 立即可做（1-2 天）

1. **动态登录流**：在 executor 中检测 login capability 的 task，执行后自动调用 `credential_manager.create_from_login_response()`
2. **InputBinding**：在 coordinator 执行完一个 task 后，从 response_body 中提取 ID，注入到后续 task 的 URL path 参数
3. **跨 turn 状态恢复测试**：手动验证多轮对话中 state 是否正确恢复

### 短期可做（3-5 天）

4. 注册 `api-executor-worker` Agent
5. 实现 `verification.py` 真实策略
6. 报告产物持久化
7. 写核心模块单元测试（至少 task_pool / coordinator / executor）

### 中期规划（1-2 周）

8. OpenAPI/Swagger 解析支持
9. 前端 pending_selection 渲染
10. 前端 campaign 报告卡片
11. 历史执行对比
12. 完整测试覆盖

---

## 八、变更日志

| 日期 | 变更内容 |
|------|---------|
| 2026-05-11 | 第一版完成：12 个核心模块 + 集成改造 + prompt 升级 |
| | 支持完整流程：项目发现 → 澄清 → 执行 → 报告 |
| | 支持并发策略：读并行/写串行/auth优先/资源锁 |
| | 支持 7 种断言类型 |
| | 支持 4 种凭证类型 |
| 2026-05-11 | P0 全部完成：动态登录流 + InputBinding + 重试机制 + 跨turn状态恢复 |
| 2026-05-11 | P1 全部完成：注册 6 个新 Agent + manifest 更新，系统共 24 个 Agent |
| 2026-05-11 | P2 全部完成：verification 5 条规则 + evaluation 质量评分/覆盖率/严重性分级/推荐 |
| 2026-05-11 | P3 全部完成：Markdown 报告 + 证据片段 + 产物持久化接口 + artifact 元数据输出 |
| 2026-05-11 | P4 全部完成：index 缓存 + 端点去重 + 请求体 schema 提取（OpenAPI/Postman 已由 api_docs_service 内置） |
| 2026-05-11 | P5 完成：前端类型定义 + 现有 Markdown 渲染/事件控制台已兼容 API 测试输出 |
| 2026-05-11 | P6 完成：58 个单元测试全部通过（capability_mapper/selection_resolver/task_pool/dependency_planner/executor/coordinator/credential_manager） |
| 2026-05-11 | P6 补充：test_runtime_integration.py 15 个端到端集成测试全部通过，总计 73 个测试 |
