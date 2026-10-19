# Agent API 401 续期与安全重放研究

基线 `75efee85`。本文件前半部分记录的是自动续期实现前的历史调查；下方状态更新记录后续实现与验收。阅读时不要将历史基线结论视为当前缺口。

## 代码事实

- `ApiTaskExecutor` 会保存 401 的响应、断言和请求观察；它不会根据状态码猜 refresh URL，也不会自动重放。
- `CredentialManager` 已能保存 Token 有效期、Cookie 属性和刷新响应产生的新 `CredentialSession`。
- `DependencyPlanner` 和两个 Coordinator 已能沿明确认证依赖传播最新 `auth_ref`；这与“运行中收到 401 后选择哪个刷新任务”是不同问题。
- `ApiTestTask` 目前没有 refresh task 身份、允许重放标记或原始 401 与重放结果的持久结构。仅增加一个 `if status == 401` 会把负向未授权用例和写请求误判为需要刷新。
- Worker 路径必须和本地 Coordinator 使用同一冻结任务、凭据引用、原始 Job 和恢复检查点；只修本地 Executor 会让两条执行方式产生不同语义。

## 真实复现

`tests/test_api_401_refresh_gap.py` 使用本地真实 HTTP 服务和现有 `ApiTaskExecutor/CredentialManager`：

- 旧 Token 请求 `/profile` 得到 401 时，实际只发送 1 次 `/profile`，不猜 refresh、不重放；
- 显式执行已有 `/auth/refresh` 任务取得新 Token，再显式执行 `/profile`，实际链路为 401 → refresh → 200；
- 写请求收到预期 401 时只发送 1 次，不能因为 401 自动刷新或重放；
- 测试结果 `p3-api-401-refresh-gap.xml/log`：**1通过，0失败，1.91秒**；事实保存在 `p3-api-401-refresh-gap/verification.json`。

## 正确的实现边界

后续实现必须把“安全重放”作为冻结计划契约，而不是状态码启发式：

1. 任务显式引用同项目、同认证来源的 refresh task；refresh task 必须是认证能力并通过现有凭据范围校验。
2. 只有明确允许的只读任务可以最多重放一次；预期 401/403 的负向断言直接保留原结果。
3. 写请求、超时、连接异常和 unknown outcome 绝不自动重放；没有明确幂等契约时，刷新本身也不能被隐式重复。
4. PG 检查点需要同时保存原 401、刷新 Job/credential ref、重放 Job、次数和最终结果；恢复时按原身份继续，终态重投不能再次发送。
5. 本地 Coordinator 和 Worker Coordinator 共用相同状态协议；凭据仍通过现有加密引用传递，不能进入 Prompt、公开 Job 或报告。

因此原始基线结论是：**自动 401 续期/安全重放仍需补实现**，但实现边界已经由实际代码和 HTTP 事实确定；不能用当时的显式 refresh 顺序替代自动闭环，也不能通过扩大重试次数解决。无数据库表、配置或模型协议改动，外部 AI 调用为 0。

## 后续状态更新（2026-10-19）

原缺口已由提交 `6c205ca4`（`【feat】实现API 401安全续期与进程恢复`）实现。`ApiTestTask` 必须显式绑定同一认证来源的刷新任务并允许重放；仅 GET/HEAD/OPTIONS、非预期401进入一次刷新和一次重放，预期401、写请求、刷新失败、第二次401及不确定结果按冻结状态机结束，不猜 URL 或重发副作用请求。本地 Coordinator 与 Worker Coordinator 共用持久检查点，刷新完成后恢复不会再次发送刷新请求。

当前工作区复核运行 `tests/test_api_401_refresh_automatic_live.py` 中不依赖隔离服务的场景，结果 **7 passed、4 deselected，8.63秒**。已保存的隔离真实进程工件 `p3-api-401-auto-process-final2.xml` 为 **3 passed、0 failed、0 skipped**；重新读取三个 `pause-1/2/3/facts.json`，分别在登录后、首次401后、刷新后强制结束进程，恢复后都只完成冻结的 `/login → /profile → /auth/refresh → /profile` 四个请求；原 Job 未改变、公开凭据不存在，终态重投新增 HTTP 为0。此处 live 工件不调用外部AI，证明的是固定任务与真实持久Worker恢复，不证明默认AI自主建立刷新关系。

因此当前分类应为：**自动401安全续期及显式关系/跨进程恢复已有实现并通过专项验收**；默认Agent自主识别和建立该关系、Cookie刷新、并发刷新去重、错误凭据负向与更广完整App仍需分别验收。本状态更新不改变 MySQL 配置、PostgreSQL 业务状态或模型协议。
