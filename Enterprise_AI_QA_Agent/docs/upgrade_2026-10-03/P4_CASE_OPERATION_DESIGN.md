# C2c3：逻辑操作与原ToolJob恢复

研究基线253d47f0；先重读用户完整目标、6.42真实失败、case_action_binding、原PG动作领取/租约/祖先检查点、ToolJob原可靠观察恢复、原图/治理/审批、API来源及assessment消费方。参考原postgres_generation_task_store的项目锁、creation_key与完整request_hash核对，以及本地Agent课程04.5。无需新模式或依赖。

根因已经由真实输入证明：相同准备/参数只换provider call_id会新增Job并再次创建；同原Job未知保护没有覆盖另一个调用。仅按请求参数去重会阻止合法二次创建、只读刷新和负向重复。保留原Job执行体系，增加明确的逻辑操作身份；每次原调用/审批仍保存。

采用显式冻结的case-operation-v1，配置在原MySQL规划policy，要求已有binding/admission。完整执行上下文传递该配置；旧缺省字段省略，旧hash/消息/恢复保持。新调用在_case_action内选择item_key和operation_key：恢复同操作保持key及全部输入，新操作用不同key。服务器不能凭HTTP方法证明业务效果，也不能声称能识别模型另起key却实际重复的意图；后续效果状态仍需实现。

PG不可变操作索引关联固定项目、Run/RunItem、原root执行Attempt、执行项及operation_key到唯一原Job；工具和完整业务参数另存hash，变化是冲突，不能借原key改操作。索引按已有PG初始化模式建表，表名从合法原Job表派生并带hash，限制63字符；原项目事务锁和领取事务内插入/读取，和原Job领取一起提交。初始化不回填旧历史或跨库写配置。

原Job可靠终态复用固定输出；新调用保持自己的Job/输入/审批，保存origin Job/输出hash及未新发送回执。HTTP来源标为completed_request，原network观察不改写，不复制工件为新的采集。原可靠观察未交付先用现有recover_observed_output，再重新核对租约/原Job。真正未知保留原running状态，新调用明确需查证、不发送；参数冲突不发送。原结果在当前invalid判定之前恢复，尚未发送的新操作继续原适用/权限核对。

领取与发送分开：原操作回执execution_claimed=true、action_sent=null，表示PG已领取但是否实际发出仍需工具/HTTP证据；复用/拒绝回执execution_claimed=false、action_sent=false。早期实测曾在领取回执填写true，语义审查后更正，旧实测原件不重写。不能把领取commit当作HTTP已发生或效果已经创建。

消费方同步：策略及完整上下文/schema/提示、原Job领取与可靠返回恢复、ToolRuntime/治理投影、assessment来源核对及manifest、项目设置类型/页面与测试。别名记录不可当作独立HTTP证据；操作回执不能当作目标通过、效果创建或清理完成。

先用6.42原参数实际测试新协议下已知结果的原图/审批与未知结果共享runtime，再验证合法两次同参数创建、只读刷新、输入冲突、四路新调用竞争、原提交丢回执、可靠观察恢复、终态/配置改变/旧协议。保存真实原件、MySQL配置、PG索引/原Job/消息、HTTP及服务器状态。默认模型、其他模式、独立生产、执行中效果/多对象清理、修订及完整P0—P5分别验收，不用这一机制冒充全局exactly-once。

## 实际实施与验证

配置及完整上下文/schema/提示、原PG领取/索引、Job可靠返回恢复、图/治理反馈、ToolRuntime投影、assessment manifest及最终Result证据、项目设置类型/组件已同步。新操作索引是原Job的不可变引用，执行状态继续存原Job；初始化创建FK表，不回填旧运行。恢复分支重验原固定身份、当前租约和原输出SHA，不调用新HTTP处理器。

最终源码7模块107通过/3跳过208.80秒；独立main/API/规划与执行CLI/SDK及MySQL/PG/Redis/RustFS场景1通过101.11秒。SDK两次provider调用、两次审批、两个原Job，只新增一次业务GET；最终五项验证引用复用Job并保存case_operation来源，原network证据未改写，别名检查点为completed_request。False/0/null保留。原图有状态场景创建一条并删除同一真实ID，目标预期count=0而实际1仍failed；准备/清理通过没有覆盖目标失败。

已确认并修正的设计错误：初版递归遍历所有JSON中的http_observation_v1，将合法同名业务字段误认成协议，真实GET响应复现失败。现在只处理API服务端指定检查点位置，业务响应原样保留。初期领取回执曾把action_sent写true，实际领取不能证明发送，现为null，旧原始证据保留。

其他失败如实保留：前端夹具保存时丢掉新policy字段，修正夹具后12通过；治理反馈测试缺ToolRegistry导入，修正夹具。wire-fixed的30通过/1失败发生在响应正文为空后测试尝试改写字段，没有保存该次原Job现场，原因未确认。增加改写前的真实输入/Job/HTTP捕获；单独复现1通过，最终完整源码107通过包含此项，不宣称已经定位或修复该次空正文的产品根因。更早240通过/24跳过的扩大回归在最后HTTP路径/反馈/最终结果来源修改前启动，不能计为最后分支的验证。

原失败、全部轮次及命令见IMPLEMENTATION.md、PLAN.md 6.43和p4-case-operation-validation.json。语义评审和模型为受控响应，不是默认外部Agent自主测试；20项完整DeepAgent运行因缺依赖跳过。明确key不能识别模型改key后的语义重复；实际效果/共享消费/多对象清理/只读查证/计划修订和其他模式/完整App仍待实现或验收。
