# API 未知写入的显式只读查证研究

基线 `0f492ff9`。本批处理“写请求已经发出但结果不确定”之后的只读查证，不自动猜测查询接口，也不重试原写请求。

## 现状与依据

- `ApiTaskExecutor` 已将写请求超时、连接异常和 5xx 标为 `request_outcome=unknown`；两个协调器禁止自动重试，`ApiTestingEvaluationPolicy` 已归类 `unknown_side_effect`。已有真实 HTTP 测试证明只发送一次，并提示“需要只读查证”。
- `ApiTaskPool` 当前把所有失败依赖统一变为 skipped，因此即使用户已经在计划中声明了一个只读查询，也没有合法入口执行它。系统没有可靠的通用资源查询路径；按 HTTP 方法、任务标题或 URL 猜测会把错误接口当成查证。
- 正式用例/运行链路的效果证据强调来源和对象关系必须由计划明确声明。API 任务已有完整 JSON 保存和两协调器共用状态，所以增加同一任务契约字段，不另造数据库状态机。

## 最终契约

只读查证任务显式填写：

- `reconciliation_for`：指向一个可能 `unknown` 的写任务；
- `reconciliation_observation`：`effect_present`、`effect_absent` 或 `inconclusive`，由用例/计划作者依据业务断言选择；
- 任务方法必须是 GET、HEAD 或 OPTIONS，且依赖的普通前置仍需先完成。

查证任务不会依赖未知写任务本身，否则原有失败依赖传播会跳过它；它通过 `reconciliation_for` 关系在目标写任务进入 unknown 后放行。若未知任务不是 unknown，查证任务不执行并进入 skipped。查证成功只记录观察状态，**不把原写任务改成 completed，不自动重试或放行其原有业务依赖**。没有明确查证任务时，旧的阻断行为保持。

查证执行失败、断言未通过或没有明确观察结果，目标保持 unknown 并标记 `inconclusive`；报告同时显示目标、查证任务和仍需人工处理的状态。关系、方法和目标在任务池初始化时校验，非法写方法不能到达 HTTP。

没有新增表、配置、依赖、模型协议或前端路由。任务字段随现有 Campaign JSON/检查点保存；配置仍在 MySQL，运行状态与证据仍在 PostgreSQL，原件仍在 RustFS。

## 验收边界

将使用真实本地 HTTP 和两种现有协调器验证：未知写只发送一次；显式 GET 查证实际发送；`effect_absent`/`effect_present`/`inconclusive` 均可保存；原写任务仍是 unknown，终态不重投，依赖仍不继续；非法查证方法在发送前拒绝。没有显式查证时保留原先的 unknown 阻断测试。

本批不实现自动发现资源查询、自动生成查询 URL、自动决定对象存在/不存在、原写请求安全重放或默认 AI 自主查证。默认 AI 项目选取→排序→补全、完整正式用例、准备生命周期、执行中修订和其余 P0—P5 仍未完成。
