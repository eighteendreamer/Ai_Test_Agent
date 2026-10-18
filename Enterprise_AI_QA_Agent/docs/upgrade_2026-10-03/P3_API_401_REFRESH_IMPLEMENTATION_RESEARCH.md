# API 401 安全续期与只读重放实施调研

日期：2026-10-18。本文先于实现建立边界，基于现有代码、已保存的 `P3_API_401_REFRESH_RESEARCH.md`、本地接口测试课程和真实 HTTP 复现。

## 已确认事实

1. `ApiTaskExecutor` 已把 HTTP 响应、断言、请求结果状态和 HTTP 观察保存到 `ApiTestTask`；401 本身是已知响应，不是未知副作用。写请求的超时、连接失败和 5xx 仍按现有 `unknown` 语义处理。
2. `ApiTaskPool` 和本地/Worker 两个 Coordinator 是执行边界。只在 Executor 中添加 401 重试会让两条路径产生不同任务、凭据和 Job 轨迹，因此续期必须由两个 Coordinator 共同使用同一任务契约。
3. `DependencyPlanner` 当前把登录/刷新作为显式认证任务，并通过 `auth_propagation` 传播 `auth_ref`；它没有“目标401后才运行刷新”的关系。把刷新任务仅追加到目标 `depends_on` 会使目标在刷新前无法执行，形成错误流程。
4. 现有私有凭据/Worker 原结果存储和 `execution_checkpoint` 已能绑定项目、父 turn、子会话、ToolJob 并在恢复时拒绝重投；续期状态可以追加到任务检查点，不需要第二套凭据协议。
5. 本地课程 `接口测试-通用篇.html` 要求分别覆盖 token 失效、鉴权失败、重复提交和幂等；`Agent轨迹与协议鲁棒性.html` 要求把工具轨迹、权限、重试和副作用同时验收。它们支持“只读、显式授权、最多一次”的边界，不能证明任意 401 都能安全重放。

## 采用的契约

用例 JSON 在目标任务上增加两个可选字段：

- `auth_refresh_task_id`：同一项目、同一 URL origin、`execution_mode=auth` 且 capability 为 refresh 的正式任务身份。
- `allow_401_replay`：显式允许该目标在首次收到非预期 401 后进行一次续期和重放，默认 `false`。

执行前校验：目标必须是 GET/HEAD/OPTIONS；目标不能把 401 作为预期状态；刷新任务不能依赖目标或与目标形成依赖环；目标与刷新任务必须有共同的已声明认证祖先。刷新任务不进入正常首轮执行，而是以 `deferred` 状态登记；目标首次收到401后才释放它。没有契约、预期401、写方法、超时、连接异常、5xx或 unknown outcome 时不自动续期。

## 持久与恢复语义

目标任务的 `execution_checkpoint.auth_refresh` 保存：契约版本、目标/刷新身份、原始401的状态/响应/断言、首次目标 attempt、刷新状态、刷新 task attempt、重放 attempt、原目标 Worker 会话及当前阶段。Worker 派发继续使用现有冻结输入和私有原结果引用；每次释放、派发、完成都先经过已有 checkpoint 回调。

- 首次401后保存 `pending`，目标暂不标记最终失败，刷新任务保持 deferred。
- 刷新完成后保存 `refresh_completed` 和新的私有 `auth_ref`，目标只重放一次。
- 重放完成保存 `replay_completed`；重放再次401保存 `replay_failed`，不进入通用重试。
- 进程在任一阶段退出时，新 Coordinator 读取检查点及已完成 Worker Job：已完成的登录/刷新/目标不会重发；只继续缺少的阶段。刷新终态重投沿现有 ToolRuntime Job 规则回读。
- 目标最终报告保留原始401和重放结果的结构化审计字段，公开投影继续沿现有输出安全策略脱敏。

## 不采用的做法

- 不在 `ApiTaskExecutor` 内按状态码猜 refresh URL。
- 不把 refresh token 自动写入普通日志、Prompt、公开 Job 或向量索引。
- 不用通用 `max_retries` 代替一次明确的认证恢复。
- 不把写请求、预期401/403、超时或未知副作用请求纳入重放。
- 不将刷新任务加入目标的普通依赖边，避免“必须先刷新才有机会观察401”的流程错误。

## 验收设计

先做代码契约测试，再做真实本地 HTTP：旧 access token 访问只读接口得到401，显式 refresh task 使用真实 refresh token 取得新 token，原目标只读接口第二次返回200；记录实际请求序列和零外部AI调用。负向用例验证预期401不刷新、POST不刷新、刷新失败不重放、第二次401不重放。Worker 复用同一序列，检查公开结果无凭据。最后在登录后、401后和刷新后分别强制进程终止，使用新服务恢复并断言历史 Job 与 HTTP 请求数不增加。

该契约只覆盖自动401安全恢复；它不替代默认AI用例选择、排序、补全、完整产品流程或完整P0—P5验收。
