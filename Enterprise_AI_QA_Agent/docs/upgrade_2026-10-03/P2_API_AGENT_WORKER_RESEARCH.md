# API 内部引用的独立 Agent Worker 验收研究〔2026-10-12〕

基线 `8e7ebeb5`。外部 AI 按用户决定暂缓。本批先验收已有实现，不预设需要修改生产代码。

## 代码依据与验证方案

- `src/cli/run_agent_worker.py` 使用完整 main lifespan、真实 Redis 队列和 AgentTaskDispatcher；不是测试替代的执行器。
- `CoordinatorRuntimeService._start_child_worker`：默认关闭 `coordinator_durable_children` 时在真实 Worker 内启动子 Agent；开启时必须找到 running/claimed 父 AgentTask，提交独立子任务。子任务完成后通知父任务。两条路径不能混为独立子进程。
- `ApiSubagentCoordinator` 注册子会话 receipt 后等其正式终态，内部凭据由 main 注入的服务解析。7.40 只有受控 launcher 专项，尚不能证明真实 Agent 图、权限校验、持久队列及跨进程链路可用。
- `SendMessageRequest.context` 进入 `ExecutionRequest.context` 和 runtime 的 context_bundle。已有测试通过固定 `ApiTestingState(campaign_ready)` 验证父 runner。本批使用相同固定三端点任务，将测试范围限定为执行/认证传输；不冒充文档发现、用户意图质量或正式用例选取。
- `tests/test_execution_worker_cli.py` 已提供隔离 MySQL 配置表、独立 PostgreSQL 库、owned Redis 容器、子进程环境和原件清理；复用该夹具，保持实际全局模型/配置/历史不变。
- `tests/test_api_worker_auth_refresh_live.py` 的真实本地目标使用随机登录/刷新凭据，记录认证匹配布尔值；沿用该目标，不保存明文凭据。

验收调用生产 session API 提交父任务，独立 `run_agent_worker` CLI 执行真实图。仅模型 HTTP 服务返回可控工具调用/摘要，所有生产组件保持原实现。分别验证默认子 Agent 同进程路径及持久化子任务跨进程路径，必要时由已有 outbox 生产机制投递。

通过标准：login→refresh→profile 三次 HTTP 均 200；父报告完成 3 项；三真实子会话、PG Job/任务回执、密文引用一致；当前公开输出和模型请求不包含凭据。按原任务重新投递不得增加业务 HTTP 或模型请求，重启 API 可读原 Job/报告。若失败，保存失败证据并沿实际第一个错误查根因；不以语法检查或受控模型质量替代功能验收。

完整 P0—P5 保持未完成。默认模型、自动续期/Cookie/并发、加密保存边界进程故障和历史迁移仍属其他验收范围。

## 实际结果与研究修正

第一批两条真实 session API 请求均 409、尚无模型/业务 HTTP。根因是 `PostgresAgentTaskStore._lock_session` 返回的项目身份为数据库 UUID，`AgentTaskCommand.project_id` 为字符串，提交和 manual_resume 都直接比较。按 `PostgresSessionStore` 的已有转换，在存储边界转换为字符串或 None，保留真实不同项目的拒绝。首轮修复遗漏 SQLAlchemy RowMapping 不可修改，造成 500；改为构造转换后的映射。相关失败原件全部保留。新增真实 PG 回归覆盖同项目/无项目提交、同项目恢复、跨项目与缺失项目拒绝、恢复幂等、拒绝不新增 Task/Outbox；最终通过。

mapping/observed 两批目标 HTTP0，工具回执表明最初夹具未在消息中显式指定执行 Agent，自动选择结果不暴露 runner。补显式 agent_key 后进入既有审批；夹具遗漏 SessionRecord.mode_key、误将普通审批202和代理审批200混为同一响应码的失败均保留，未改生产权限或降低断言。

真实子 Agent 图进一步证明 API worker 虽使用 `api-executor-worker`，模式却是 default，runner 被隐藏。`ApiSubagentCoordinator._build_worker_spec` 漏传 mode_key；按安全模式派发已有做法补 api_testing，不放宽工具暴露。另 `_resolve_parent_task` 原来只接受 message/coordinator_resume，父工具在 approval_resume 中实际执行时持久子任务会退回同进程；纳入原 running/claimed、原 session/turn 的 approval_resume，终态或异 turn 仍不能作为父任务。契约回归及最终真实 PG 子会话已证明模式与项目身份正确。

**最终独立验收仍失败，不能称完整 Agent API 认证通过。** final 四项2通过2失败，50.39秒：项目身份真实 PG 和模式/审批父身份合同通过；默认同进程子 Agent 路径仅 login200，持久子任务路径 login200→refresh401〔未带Authorization〕，profile未执行。5/6次本地模型物理HTTP、fixture_errors为空；没有外部AI调用。两份实际保存的父报告均0完成/2失败/1跳过。

沿实际代码定位：`ApiSubagentCoordinator._wait_for_sessions` 将 waiting_approval 当成终态，未等到工具结果就进入失败合并；父报告定稿后，用户经既有代理审批继续完成的子登录不会再合并到原父任务。下一认证任务会在尚未得到有效凭据时派发。这是父子审批与 campaign 恢复的生命周期缺口。不能仅把 waiting_approval 从集合删除、延长轮询或自动批准；后续须先复核原 proxy approval、父 AgentTask 等待/恢复、固定派发和权威 Job 结果读取，再设计保留原已执行结果、审批及原预算的恢复方式。本批没有实现这个后续恢复，也未到达独立终态重投/API重启验收断言。

最终十七模块相关回归206通过21门控跳过3排除，60.51秒，含原真实ToolRuntime/PG加密引用三参数；与上轮205批次重叠不累加。既有 Agent/持久子任务/领取快照/启动回收/CLI五模块：第一次默认 Redis 服务关闭连接导致47个setup错误，14通过1跳过；改用已有 owned 独立Redis/PG夹具后61通过1跳过35.96秒，最终代码再跑61通过1跳过35.53秒。没有修改原生产Redis、模型或配置。compileall为附加验证，前端未改/未构建。

新源代码仅三个相关文件；配置仍MySQL，业务PG，既有密文/公开脱敏与原模式权限保留。证据包含真实状态、公开PG快照、目标到清理时的HTTP计数与原失败，生成凭据只在内存中检查。完整目标继续 active。
