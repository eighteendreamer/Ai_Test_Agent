# API Worker 私有结果与刷新凭据传值调研

基线：`2f8e371c`。原始升级方案全文已读，范围为 Agent API 的响应提取、依赖传值、鉴权与恢复；不改变模型协议，不增加外部 AI 调用。

## 事实与复现

- `runtime._execute_dispatched_task` 用 `OutputSafetyPolicy` 生成 Worker 公开结果；`refresh_token` 必须脱敏。
- `ApiWorkerResultReader` 校验原派发/Job，但只返回公开 `output_payload`；`ApiSubagentCoordinator` 用这份数据构造 `_task_outputs`。公开视图不适合作为执行数据源。
- `PostgresApiCredentialStore` 已使用通道加密配置、项目/会话范围和追加式不可变写入保存凭据与冻结输入；可复用相同设施保存原始执行结果，不增加依赖。
- `ToolRuntimeService.execute` 在业务调用前已注入真正的 `tool_job_id`，终态重投返回原 Job；结果应绑定该身份，不能绑定模型生成的工具调用文字。
- 本地课程 `接口测试-通用篇.html` §6.2 要求实际使用返回凭据调用受保护接口；`Agent轨迹与协议鲁棒性.html` §04 要求同时核验轨迹、隔离和重复执行。
- 新测试实际启动本地 HTTP：登录返回独立的 Access/Refresh Token，刷新接口仅接受请求体中的正确 Refresh Token，受保护接口仅接受刷新后的 Access Token。
- 首次测试夹具未设置 ready/blocked，2失败、0请求；依现有 TaskPool 语义修正夹具后，`p3-private-refresh-reproduction.xml` 为 **1通过1失败**：直接执行通过；Worker 实际请求为 login 200 → refresh 401，服务端确认发送了 `[REDACTED]`。该失败是修复依据，不是成功验收。

## 采用做法与验证边界

1. 在现有加密 PG 存储增加原始执行结果表。追加写、内容哈希、同身份冲突拒绝，保存 dispatch/child/原 ToolJob 身份；公开结果只携带不透明引用。
2. Worker HTTP 结果产生后先保存原件，再生成公开投影。没有有效原 Job 身份的私有派发在 HTTP 前明确失败。
3. Reader 先校验原 Job 和冻结派发，再按父会话/项目加载原件并核对绑定；私有引用缺失、失效或冲突不能降级拿脱敏内容继续传值。
4. Coordinator 重建后读取已完成生产者的原结果，恢复输出与凭据，不重置原 Job/终态任务，不重新发送登录或刷新。
5. 父任务公开快照与报告保留引用并脱敏；原执行对象只用于服务端数据依赖。
6. 实测即时 login→refresh→profile、完成登录后暂停/PG快照/新服务继续、结果身份越权/篡改/密钥缺失、原 Job 终态重投零 HTTP；同时回归原认证/报告/恢复测试。

这是自动401恢复的前置缺口。该批不将显式 refresh 顺序声称为自动401续期，不代表默认 AI 自主规划或完整 P0—P5 已完成。独立进程终止验收另列，受控暂停不能替代真实进程中断。
