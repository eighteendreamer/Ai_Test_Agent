# API父子审批等待与原任务恢复研究〔2026-10-12〕

基线 `742331d`。完整 P0—P5 目标不变，外部AI按用户安排暂缓。7.41两条独立Agent验收已失败，本批继续处理该缺口，不能以SDK修复通过替代完整API闭环。

## 已确认的生命周期缺失

| 当前代码位置 | 实际行为 | 对恢复的约束 |
|---|---|---|
| `api_testing_mode/subagent_coordinator.py:run_all/_dispatch_batch/_wait_for_sessions` | waiting_approval当终态；子会话ID等轮询结束才写回ApiTestTask；恢复running直接失败 | 应先保存固定派发身份与child ID；审批等待不能被认定失败或放行后续依赖；恢复读原子任务，不重新派发 |
| `api_testing_mode/runtime.py:_advance/_execute_campaign` | 仅campaign_ready进入执行；未结算也保存最终报告 | 需要原campaign的续跑入口；真实结算后才生成最终报告，保留历史失败 |
| `runtime.py:_checkpoint_execution_state/_persist_state` | callback是同步函数，仅修改context_bundle，不构成PG提交 | 不能把内存字典当持久检查点；原ToolJob和Session snapshot需要可靠保存边界 |
| `ToolRuntimeService._dispatch` | partial属于终态，按原Job重投直接回读；waiting_approval/resource为非终态 | 不能返回partial后假定原父工具还能自动续跑；等待子任务与工具请求人审批/资源准入有不同语义 |
| `SessionService.resolve_approval` | 代理审批决策转发子会话；不会接管父API工具 | 复用代理审批，不新增父批准请求；父继续必须由子结算触发 |
| `CoordinatorRuntimeService.on_child_task_finished/_deliver_notification` | 自动续跑仅coordinator；waiting_approval启动进程内watcher | API父任务不能靠改Agent名字接入；进程重启不能依赖旧watcher |
| `AgentTaskDispatcher`的coordinator_resume | 调execute_claimed_message，重新orchestrate、execute_turn | API续跑必须恢复原snapshot/turn预算，不能新消息新预算后再让AI重调用父runner |
| `PostgresAgentTaskStore` | 查询出现waiting_children/coordinator_resuming名称，但当前dispatcher结算把普通成功任务标completed | 状态名称不是功能证据，需真正领取/释放/续跑和幂等事务 |
| `RuntimeService._resume_after_approval` | 原审批Job恢复后直接进入模型，原预算已保存 | 可复用原预算、原工具call_id和Job；工具等待子任务应先暂停后再恢复原工具，不能带等待结果进入模型判完成 |

原API工具每次创建新的ApiTestingModeRuntime，凭据Manager不是单例跨项目共享；不能误判成全局认证池缺陷。父引用凭据已由正式PG加密服务保存，7.40局部链路通过；本次失败发生在父结果合并之前，不能重做vault掩盖它。

## 当前拟采用的恢复路径与必须验证的点

按现有原Job/Session快照/AgentTask outbox/租约继续扩展，核心是**暂停原工具，子结算后恢复原工具，再继续原Agent回合**：

1. API coordinator保存本批任务的固定派发ID、child session和attempt，再观察原子状态。审批等待保留任务running及等待原因，不更新为failed，不放行缺凭据的依赖任务。
2. 持久化明确的等待子任务边界。原工具Job不进入partial/completed；原Session快照保存工具call_id、job_id、固定输入、campaign及原预算。父Worker释放执行槽，不延长HTTP超时或占用Worker无限轮询。
3. 原代理审批继续子会话。真实子Job结算或拒绝后，通过既有outbox投递父继续。当前父仍在保存边界时完成的子通知、重复通知和重启后缺watcher需权威状态重读，避免通知丢失。
4. 父继续使用原snapshot、turn、model预算、原Job和已批准的固定输入，不通过普通新消息分支。先核对project/session/turn/task/ref范围，再读原子工具的正式PG结果；模型摘要或截断工具消息不能作为凭据/任务结果来源。
5. 同一批已完成结果只合并一次；恢复时重建已完成任务输出，供依赖传值。先传播原credential_ref再选下一批；只派发未执行任务。未知副作用仍保留recovery_required，不能重发。
6. 真正全部settled后保存最终报告和正常Agent工具消息；终态重新投递只回读历史，额外模型/业务HTTP必须0。旧0完成/2失败/1跳过报告不重写。

这是跨层等待/恢复能力，不能在API里加一个if后声明完成。若新增等待状态，需同步ToolExecutionRecord/ToolJob/Session/AgentTask和前端活跃/终态消费者、状态展示及恢复入口；现有测试模式和Agent操作方式保持。配置仍MySQL；检查点、结果和outbox属PG业务事实。

## 本批现场验证

复用现有 `test_api_agent_worker_live.py` 的真实main API、独立CLI、隔离MySQL/PG/Redis/RustFS和本地模型HTTP。增加QA_API_AGENT_APPROVAL_HOLD_SECONDS，仅为测试观察时机，不是生产配置。子代理审批保留至少3秒，批准前直接读父PG工具报告、真实子Session状态和目标HTTP计数；保存安全ID/计数，不记录凭据。两条启动路径仍严格要求无提前最终报告、login/refresh/profile均200、报告完成3项、重启历史及终态重投无额外调用。

未运行前不将拟采用恢复路径列为已实现；后续实现必须通过这条真实失败样本及原相关回归。

## 现场结果、错误结论纠正及未保留的修复尝试

本批没有API生产修复交付。SDK修复已提交742331d；本节的所有API生产实验修改最终撤回到该已验基线，未提交未验恢复代码。仅增强原验收的安全观察，不改变默认[200,200,200]/完成3项/终态0调用的严格标准。

1. before：2失败2排除57.63秒，夹具只延迟proxy，但父完成后loop先退出，没有等到proxy观测；不是“没有子审批”。observed：2失败2排除54.07秒，新增父审批边界记录，捕获普通父工具审批、无报告；其后仍提前保存失败报告。该观察不能证明子审批等待正确。
2. delay-final：2失败2排除54.96秒，是本批把普通父审批误按proxy取child_id造成KeyError，保留原件。delay-final2：2失败2排除58.73秒；所有审批延迟3秒，记录的仍是**普通父工具审批**，当时尚无子dispatch。不能据此称“子审批暂停边界正确”，先前commentary的这一概括不准确。
3. probe-final：2通过2排除52.80秒，只在普通父审批等待无报告后提前return的窄探针；**不是完整Agent通过**。这个临时return已移除，不保留能绕过完整验收的环境开关。resume-final：2失败2排除52.35秒，真实批准后目标HTTP0；PG证据仍父报告0完成/2失败/1跳过，两个子工具Job waiting_approval，子审批pending、父proxy已建立，但测试在新proxy投影前检测父terminal就退出。
4. layer1/2/3三次生产实验，最终均2失败2排除：142.66/54.17/49.78秒。默认同进程目标HTTP0，持久子HTTP仅login200。只做“API返回等待＋proxy恢复callback”不能打通生命周期：`_checkpoint_execution_state`会重建字典，把刚写入waiting_for_approvals清掉，导致父Job partial终态；原RuntimeService审批恢复没有识别API等待状态和变更后的context_bundle；callback拿子调用参数续父工具又有身份风险。第三次仍同样失败，停止该局部路线，撤回全部4个API生产文件。没有自动批准、提高预算、延长原HTTP超时、重写报告或关闭公开脱敏。
5. 逻辑回归最初因本批改变未知子session语义失败；恢复原拒绝未知行为后45通过19跳过14.39秒。但局部逻辑通过不代替现场，仍撤回生产实验。最终恢复HEAD后的6模块46通过22门控跳过15.25秒：api_task_executor_recovery、api_dispatched_task_contract、api_mode_session_isolation、session_execution_ownership、security_approval_boundary、api_agent_worker_live；live参数未启用，不能当独立完整验收通过。

**下一实现路线已据事实修正**：不能用更多进程内callback或把子proxy当父approval_resume。先实现正式可持久化的原工具等待边界，包含父call_id/job_id、原已批准scope、固定输入/phase、child ID和原预算；RuntimeService审批/普通工具两个入口都要保存更新后的context与wait快照。之后由既有outbox/lease提交原snapshot继续，先权威读取子ToolJob再续父工具。需要重启/通知早到/重复通知/拒绝/unknown及预算的真实验收，才能交付。拟采用路径是待实现设计，不是新功能已完成。

所有场景使用隔离存储、本地模型HTTP，外部AI0，历史API失败和SDK原件哈希保留。源码恢复、输出工件、3份计划和4个保护文件审计见p2-api-approval-wait-validation.json。
