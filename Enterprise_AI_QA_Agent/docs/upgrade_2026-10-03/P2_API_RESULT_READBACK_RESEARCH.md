# API 原子任务结果回读〔2026-10-12，基线 cdf51dea〕

本步是7.43等待/恢复架构的结果读取基础，不把它称为完整父子审批恢复。

依据 → 做法：

- `subagent_coordinator.py:75` 恢复running任务直接标worker_unsettled；`:291`只读子会话tool消息JSON。原任务即使ToolJob已经完成，也没有读取入口。新协调器应先核对原子会话，读取原ToolJob结算结果，不重新派发已完成HTTP。
- `runtime_service.py:_to_chat_messages`把输出再写入聊天；`ToolRuntimeService._dispatch`先保存/结算PG ToolJob。二者是不同持久化动作，不能把聊天存在作为原工具完成的必要条件。当前API默认没有专用压缩器；不把可能压缩描述为本轮已复现的生产截断。
- `ToolJobService.list_jobs(session_id)`和`ToolJobRecord.input_payload/output_payload`已有完整原件读取；复用此能力，不新增结果表。
- `ApiCredentialReferenceService.resolve_dispatch`已核对子会话角色、父project/session/turn/task和父派发收据，再读加密固定输入。结果读取沿该校验，匹配原dispatch_ref和任务身份；凭据仍只通过原加密引用恢复。
- `ToolRuntimeService._run_api_test_runner`每次创建模式Runtime；将既有ToolJobService透传至该Runtime和子协调器，不修改模型协议或测试模式。

实际复现使用既有auth_target、生产ToolRuntime、真实隔离PG/MySQL/Redis/RustFS和受控子启动。首轮尝试用save_session(messages=[])清空聊天，但PG消息是追加式保存，该夹具没有制造缺聊天；它实际证明恢复running的profile错误变failed。已保留首轮失败，第二轮改为创建子会话→执行真实ToolRuntime落库→不写聊天→保存子状态；绝不删除PG消息或改生产保存语义。

待实施边界：有ToolJob服务时只从匹配原派发的结算Job取结果；结果缺失/等待/未知不改成成功，也不回退聊天。没有该服务的旧独立调用保留旧消息读取；生产装配始终传服务。恢复running只对已结算子任务进行回读；仍运行、等待审批、缺会话保持原未结算处理。重建已完成任务response_body用于后续绑定。原等待状态、snapshot/outbox自动续跑仍需后续实现，旧失败报告不改写。

## 实现及实际结果

新增worker_result_reader，复用既有ToolJobService读取原件，核对父session/turn/project、真实receipt、角色、固定dispatch及原task请求/attempt。匹配多个Job拒绝歧义，不取最新值；未结算Job不视为成功。有服务的生产路径不回退聊天；没有服务的历史独立调用保留兼容。原加密auth_ref由可信服务恢复，不从公开结果取得明文凭据。

协调器启动时读回已结算子任务；已完成response_body重建依赖输出。恢复读取受现有request_timeout_seconds两倍的共享截止时间限制。派发后先在task上记录child ID，再等待；checkpoint callback仍是进程内context更新，**不是持久snapshot**。ToolRuntime→模式Runtime→子协调器透传既有ToolJobService，无新增表、配置或依赖，模型协议/权限不变。

| 原件批次 | 实际结果与范围 |
|---|---|
| before/reproduction | 1失败13.23秒、1失败9.45秒；前者夹具未清掉追加式消息，后者真正从未写聊天，原Job已login200而父结果全None |
| first/logic/final | 1通过7.63秒；37通过1门控跳过7.84秒；1通过8.30秒。真实隔离存储/HTTP及身份、冲突、重复、待审批、依赖id=0/false |
| regression | 12模块86通过3门控跳过48.98秒，启用真实auth存储与HTTP；与其他批次重叠，不累计 |
| deadline-final | 39通过1门控跳过14.43秒；新增共享恢复截止时间、原执行恢复/派发/模式隔离，含真实存储专项 |
| public-final | 1通过10.07秒；login→refresh→profile均200，三子聊天空，重建全部running任务恢复completed，额外HTTP0，跨父拒绝。保存公开原ToolJob及child投影；实际随机token不在导出中 |
| agent | 2通过2失败57.04秒；真实main/独立Agent CLI，同进程与持久子两种路径都仅login200。完整审批恢复/鉴权仍失败，API重启与终态重投断言未到达 |

verified/evidence-final分别因证据导出误用async generator、SessionRecord.model_dump失败（1失败8.00秒、1失败7.94秒），功能断言此前已通过；均保留原件，修正夹具后public-final通过。SessionRecord是dataclass，采用显式公开投影。compileall实际退出0，属于附加检查。

**已实现且上述范围验证**：原ToolJob回读、请求/身份核验、缺聊天结果恢复、恢复不重发HTTP、加密引用恢复、共享读取截止时间。**仍需补实现**：父工具持久等待、原snapshot/outbox恢复入口及自动续跑；现有waiting_approval终态和过早报告结算未在本批改变。真实默认AI质量按用户安排暂缓；本批外部AI0。完整P0—P5保持active，历史失败报告不改写。
