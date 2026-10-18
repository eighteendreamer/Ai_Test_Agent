# 默认真实逻辑失败与正式用例内清理的契约缺口

基线c7d20303及已实测的分阶段工作区。依据为本批实际默认模型PG检查点、完整八步用例、原件、三个物理请求及原业务范围；不改写原任务，不重复其未知第三轮。

## 已证明的事实

- 默认第一轮读实际文档，第二轮返回完整JSON，finish_reason=stop。其意图是prepare_orders创建两条、default_query查询第一条并清理两条；prepare.instructions明确“不提前删除”。然而cleanup_sources写在prepare_orders上并指向自身，现有normalize_preparation_scopes因此拒绝。第三轮总时限中断，没有可靠完成正文，业务HTTP为0。
- PreparationPlanItem才有cleanup_sources；FormalCasePlanItem无此字段。normalize_preparation_scopes和frozen_preparation_scopes只把prepare识别为独立清理项。正式目标自己清理外部准备对象时，没有同等显式来源关系可填。
- CasePlanItemBase只有一个effect_evidence，EffectEvidenceDeclaration.role只能produced或cleanup。select_when只筛选这一份声明；不能把一个正式用例合法的不同产生和清理操作分别归为两种角色。不能拿已验的“独立prepare创建→正式目标查询→独立prepare清理”替代这个场景。
- 本批受控两阶段生产正常完成4回合，真实部分/全部清理分别blocked/passed，旧协议和恢复回归通过。它证明阶段协议及独立准备/清理链路可用，不证明默认模型或正式项内混合效果已经可用。

代码依据：schemas/case_planning.py的正式/准备项与单个effect_evidence；schemas/case_effects.py的单角色声明；application/test_runs/preparation_scopes.py的cleanups筛选和自引用拒绝；application/runtime/case_effect_evidence.py的compile_declaration/validate_effect_declarations。原失败文本保存在p4-staged-planning-default-first/PG-planning.json的validation_feedback.candidate_response。

## 判断与下一步研究边界

准备项自引用是实际非法声明，应保留拒绝；但只提示模型“不要自引用”不足以说明正式目标负责清理时应该怎样表达。当前存在正式清理关系和项内多角色声明的架构缺口。不能自动删掉cleanup_sources、把整个准备项解释为cleanup，或人为改目标JSON/造正式用例来让断言通过。

下一步先枚举原声明编译、实际ToolJob观察、对象来源/拥有者、清单、清理覆盖、Agent上下文和页面的消费方，再决定新增版本协议。需要区分执行项之间的依赖边和同一项内实际操作的效果来源，避免把项内合法先后操作误变成依赖自环。完整正式JSON及自身说明仍由Agent理解执行，声明只负责可靠返回的证据分类；不按HTTP方法猜创建/清理，不把业务用例编译成模式参数。

拟研究按固定实际操作选择条件描述多份产生/清理声明，以及正式项清理外部来源的显式关系。任何采用方案都必须保留旧序列化/hash/Prompt、原unknown和终态；新配置MySQL、新冻结关系PG、原件/大证据RustFS。源码各消费方和前端契约成对同步，不能先放宽旧校验。

验收必须使用真实正式用例：同一项内创建后查询再清理；正式目标消费并清理独立准备；两条来源只清一条；真实不同操作被错误角色声明；未知创建/清理不重发；共享消费完成后清理；项目/版本隔离与原预算恢复。还需另建默认模型任务验证其自主规划与执行。以上尚未实施，不将本研究或本批受控通过作为这些需求已完成。


## 18f9fccc消费方核对与采用设计

本次继续读取原返回编译/capture、PG对象证据查询及SHA校验、整个运行清单覆盖、assessment结果合并、运行拥有者/阶段、固定执行上下文、失败回归裁剪和前端覆盖面板。代码事实：PG每个origin_job_id保存一份不可变JSONB；目前capture、上下文与覆盖只接受v1；回归仅检查prepare清理来源；前端覆盖面板仅显示case-effect-coverage-v1。JSONB已有容纳扩展数据的结构，不代表新事件语义及数据库事务已经验证。

拟采用每项多份稳定效果声明，清理来源固定到产生项/声明；同项来源不成为depends_on自环。逻辑阶段确定正式清理归属，技术阶段追加操作匹配、对象字段和完整固定预期；正式JSON及自身说明不改写。实际ToolJob保持原身份，多声明引用同一原可靠返回；未知与矛盾匹配保留原因，覆盖读取所有原操作。需要按新协议同步全部消费方并保留旧v1路径，实施与实际功能验收顺序已写入完整PLAN 2.3.2。

本次指定5项回归通过6.41秒，两项为无Git上传/原件，三项为原真实规划证据离线回放。没有实现新Schema、事件协议、正式清理执行或新默认模型任务；不把设计可行性写成实际功能可用。共享消费完成证据仍独立待实施。


## d81b25e8之后的v2实施与实际验证

完成消费方查证后采用独立FormalCasePlanItemV2/PreparationPlanItemV2和PlanningDecisionV2，旧v1不增默认字段。范围、返回、覆盖和两阶段协议必须同时v2；新Prompt v5冻结PGseed。case_effect_relations.py在原可靠返回上保存多声明，case_relation_coverage.py按固定来源/拥有者投影整个运行原操作；原PG JSONB/assessment/Result沿用实际服务，未加表/列。完整用例不拆、不改，证据字段不编译业务操作。

六项真实MySQL/PG/RustFS/native HTTP场景：正式项内部全清/部分清、正式项清理外部prepare、未知创建/未知删除不再发、覆盖预算不足。另四项原实际返回故障回放和真实PG阶段提交丢回执恢复。最新12项通过24.86秒；旧四模块61项通过54.27秒；38项前端组件/类型检查/构建通过。初两轮均2失败8通过是未知重试测试预期错误：实际PG将原Job保持running、别名denied/outcome_unknown，不再发送。按现有语义修正测试，未放宽未知保护。

实际默认任务logic 15.647/106.085秒后正确冻结prepare创建、target清理；effects 56.697秒读取原生execute_task，随后0.984秒总deadline取消。四逻辑/SDK/物理请求，215.55秒严格测试失败，未创建Run/业务HTTP。原默认候选自引用缺口得到新表达，技术绑定与完整自主业务仍未通过；不继续原样重跑。尚需研究完整固定预期如何引用文档原文依据及阶段耗时；当前声明只读自身content或prepare.instructions，新受控手工用例有明确字段，不能据此保证所有原用例均适用。共享消费完成仍false，多清理通道组合/其他模式/完整App另验。

具体命令和原失败保留在IMPLEMENTATION最新条目，源文件/字节/默认消息/三份方案核对见p4-formal-effects-validation.json。

## 7.86 效果证据与最终业务回执的职责边界核查〔2026-10-19〕

本轮沿 ToolJob 原始返回、效果证据捕获、PG 持久化、恢复和 Agent 最终回执逐段核对，并运行：

```text
cd Agent_Server
..\.venv\Scripts\python.exe -m pytest tests/test_case_effect_evidence.py tests/test_case_cleanup_coverage.py tests/test_case_effect_relations.py -q
```

结果为 **20 passed、40 skipped，6.70 秒**。跳过项均为显式 `LIVE` 的 MySQL/PostgreSQL/RustFS/native HTTP 场景；本轮没有将跳过项计作通过。

已从代码确认的职责边界：

- `PostgresToolJobStore._observe_job_output_sync` 在一个 PostgreSQL 事务中更新原始 `ToolJob.output_payload` 与 `tool_output_observation`，随后调用 `PostgresCaseEffectEvidence.capture` 写紧凑效果投影；任一写入失败会使事务整体回滚。投影以 `origin_job_id` 为主键，并引用原 ToolJob。
- `capture_returned_evidence` / `capture_relation_return` 从同一可靠返回派生对象清单、声明检查、操作选择及哈希，并校验固定执行绑定和原始输出哈希。查询时再次校验原 Job、逻辑操作、观察、意图与证据投影一致。
- 有可靠返回但后续结果交付中断时，`recover_observed_output` 使用已保存的 ToolJob 输出结算，不重复调用工具；没有可靠返回时保留未知状态。对象投影用于恢复后的效果清单/清理覆盖，不由 Agent 自由文本回执代替。
- Agent 最终回执负责完整用例的业务断言；效果证据负责从原始工具输出中归档可追溯的副作用对象并计算清理覆盖。两者的部分字段语义重叠，但证据来源、恢复边界及运行级覆盖职责不同。

据此，不能以“最终回执已包含业务断言”为理由删除效果声明或其 PG 投影。当前可考虑的优化只限于确认同一断言是否被重复存储，以及是否能减少不丢失来源、哈希、对象清单、未知状态和清理覆盖的字段；这不是已确认的生产缺陷，本轮不改代码。

本轮检查覆盖了源码、确定性测试和当前协议的投影/恢复逻辑，未重跑真实数据库/native HTTP 场景，也未证明运行级共享对象消费完成、所有工具 handler 都经过相同观察事务，或旧/新协议在完整 App 和默认自主 Agent 中均可用。下一步应先核对 `observe_output` 的全部调用入口、ToolJob 状态迁移及共享消费者终态证据，再决定是否有可安全消除的重复字段或缺失边界；不得据本轮结果把 P2 默认 Step `produced` 超时视为已解决。

## 7.87 清理前消费关闭与覆盖报告状态核查〔2026-10-19〕

继续核对 `observe_output` 的调用集合、原子动作领取和共享消费关闭实现。生产代码唯一调用点是 `ToolRuntimeService.execute` 在 handler 返回、结果归一化和输出安全清洗后，对 `completed/partial/failed` 结果调用 `ToolJobService.observe_output`；另一个直接调用点只在测试辅助代码。对普通无 ToolJob 或未获得可靠返回的分支，不会伪造输出观察。

读取 `PostgresCaseConsumption` 与 `PostgresCaseActionAdmission` 后确认：当固定 plan 启用 `case-consumption-phase-v1` 时，清理动作领取前需提交当前正式目标的消费关闭回执；服务端在原清理动作领取事务内重验目标业务断言、原普通动作的项目/运行/计划/条目身份、终态及可靠返回观察，并核对共享消费者已有终态 RunItem、对应 Result 和全部已启动 ToolJob。缺少或无效关闭证据时动作以 denied 终止，不发送清理工具；关闭记录和清理领取同事务保存。该门禁证明的是显式消费阶段结束，不把 RunItem 标成完成。

清理覆盖投影 `relation_cleanup_coverage` 仍对每项及总结果恒定输出 `consumption_completion_verified=false`，并不查询上述关闭记录。结果有意区分“对象逐个有清理证据”和“报告可证明消费阶段完成”；但当前覆盖报告不能呈现已通过的关闭回执，产生潜在的报告/状态语义缺口。Plan 的 `case-cleanup-waits-v1` 依赖映射、动作门禁和报告字段是否应该共享同一受信证明，还需核对 Result/前端消费方与运行终态定义后才能决定契约，不能简单把布尔值改为 true。

相关确定性回归运行结果：

```text
cd Agent_Server
..\.venv\Scripts\python.exe -m pytest tests/test_case_consumption_phase.py tests/test_case_consumption_external.py -q
```

**5 passed、23 skipped，4.15 秒**；跳过项依赖隔离 MySQL/PostgreSQL/RustFS/HTTP，不计作通过。下一步应沿消费关闭记录的生产读入口、case Run Result/报告 DTO、前端覆盖展示以及 plan dependency closure 回读核对事务之后的持久状态；使用原完整 live 场景证明共享消费者完成、部分/全部清理、关闭回执丢失及终态重投。确认数据契约后再决定是否把关闭证明加入覆盖报告。P2 默认 Step 的 produced 超时与本边界独立，仍未解决。
