# API 任务依赖领取与路径前置研究

基线：`5156e19c`。本专项只处理 API Testing Mode 的确定性执行门槛，不把 API 路径启发式扩大为通用业务规则推理。

## 调研依据

1. 正式用例 RunItem 已在 `src/application/test_runs/dependencies.py` 使用 `TopologicalSorter`，拒绝缺失引用、自引用、重复依赖和循环；`dependencies_ready` 还要求前置状态与实际输出满足条件。
2. API 的 `ApiTaskPool` 原来只从字典中取存在的依赖：
   `if dep_id in self._tasks`。因此缺失依赖被静默丢弃；已持久化为 `ready` 的子任务也不会重新按 `depends_on` 检查。
3. API 的 `bind_path_parameters` 原来在完整 URL 上使用同时支持 `{id}` 和 `:id` 的模式。完整 URL 中的 `:8000` 会被识别为参数，且没有输出时无法区分端口和缺失业务参数。
4. `ApiTestCoordinator` 与 `ApiSubagentCoordinator` 共用同一任务模型，但分别在本地执行和 Worker 派发前应用绑定。两处都必须在产生 HTTP 或派发 Worker 前阻断未解析的路径占位符。
5. 正式 RunItem 的缺失引用测试 `tests/test_run_item_dependencies.py` 已证明“创建运行前拒绝”是项目既有语义；本专项不复制业务依赖检查，只把相同的图完整性门槛用于 API task pool。

## 复现结果

新增回归首次运行前，API pool 对以下图均未报错：不存在的依赖、自引用、循环、重复任务身份和重复依赖；子任务保存为 `ready` 时会先于父任务发 HTTP。真实本地 HTTP 复现记录在 `p3-api-dependency-before.xml` 与 `.log`。

## 采用的修复

- `ApiTaskPool` 初始化和每次依赖协调时，使用标准库 `graphlib.TopologicalSorter` 校验任务身份、依赖存在性、自引用、重复依赖和循环；拒绝发生在任何业务 HTTP/Worker 派发之前，并留下结构化日志。
- `ready_tasks()` 先重新协调依赖，所以恢复或外部修改后，保存的 `ready` 标签不能越过未完成前置；前置失败仍按现有语义传播为 `skipped`。
- 路径参数只在 URL path 上解析，端口不再被当成 `:parameter`。绑定后仍有 `{id}` 或 `:id` 时，本地协调器将任务置为失败，Worker 协调器不派发任务，错误明确指出缺少参数；不发送猜测 URL，也不增加重试。
- Campaign 保存 `PreconditionAnalysis.to_dict()`，使人工数据缺口、来源和解析细节随冻结计划可查；没有引入新表或配置项。
- 旧任务仍可读取；只有在其实际执行/恢复时发现缺失输入才生成失败证据。已完成任务和历史结果不重写。

## 功能验收

`p3-api-dependency-final.xml`：**11 passed**，包括：

- 依赖图五类错误在状态变化前拒绝；
- 两种协调器均不允许保存的 `ready` 绕过前置；
- 创建订单 → 实际返回 `id=0` → 查询 `/orders/0`，真实本地 HTTP 顺序和次数正确；
- 未解析路径不发 HTTP、不派发 Worker，并在报告中呈现缺口；
- 终态再次调用不产生额外 HTTP。

相关回归 `p3-api-dependency-verified4.xml`：**70 passed, 3 skipped**；加入 Campaign 前置摘要后的完整回归 `p3-api-dependency-regression2.xml`：**101 passed, 5 skipped**。覆盖 API 文档解析依赖、真实创建/查询 HTTP、重试、未知写入、结果回读、工具等待、项目范围和模式隔离。跳过项是既有真实存储门控，不作为通过计数。

本专项没有调用外部 AI；模型协议、测试模式、权限、MySQL 配置、PostgreSQL 业务存储和 RustFS 原件链路未改变。
