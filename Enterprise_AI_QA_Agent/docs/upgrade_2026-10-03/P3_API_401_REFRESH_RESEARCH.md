# Agent API 401 续期与安全重放研究

基线 `75efee85`。本轮只验证现有边界和可复用基础，不把 401 自动续期猜测成已实现，也不在没有明确关系时重放请求。

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

因此本轮结论是：**自动 401 续期/安全重放仍需补实现**，但实现边界已经由实际代码和 HTTP 事实确定；不能用当前的显式 refresh 顺序替代自动闭环，也不能通过扩大重试次数解决。无数据库表、配置或模型协议改动，外部 AI 调用为 0。
