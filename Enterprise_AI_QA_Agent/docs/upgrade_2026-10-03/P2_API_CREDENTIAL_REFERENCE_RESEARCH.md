# API Worker内部凭据与固定任务引用〔2026-10-12〕

基线70a1daf8。上一轮为实际进展：统一来源传播和具体脱敏已提交，生产ToolRuntime/PG失败证据明确将下一步改为内部传输修复。完整目标不变，暂缓外部AI，零外部模型请求。

## 实施前代码依据

- 已重读目标附件。api-test-runner既有Worker负责执行父协调器明确的ApiTestTask；这不是把正式用例编译成模式参数，也不更改Agent选取/理解用例的流程。
- `CoordinatorRuntimeService.dispatch`785—814：创建内部子会话时继承parent.project_id，写入真实parent_session_id、parent_turn_id、task_id、dispatch_role。任意worker.context不能覆盖这些字段。新授权范围只能读取SessionStore中的这些关系。
- `ApiSubagentCoordinator._dispatch_batch/_build_worker_spec`：父端已经固定实际任务/attempt，Prompt目前包含完整任务和credential_session。仅把Authorization删掉会丢失执行能力；仅换auth_ref仍让模型可以改写任务URL/request_body，不能作为完整安全传输。
- `ToolRuntimeService._run_api_test_runner`每次新建模式runtime；实际ToolJob先持久保存公开脱敏输出。父端不能从公开输出重新构造私密凭据。公开消息只能携带task_result/auth_ref。
- `ChannelCredentialCodec`：已用Fernet及现有配置key，加密字符串字典，有明确缺/错key错误；上一轮已实际确认available=True，无需新依赖。存PG业务密文，编辑配置仍原MySQL体系，不把密钥放入PG或Prompt。
- `PostgresBusinessEnvironmentStore`已有派生表名/63字节及标识符约束、参数化SQL和asyncio.to_thread；Session表已有project_id及FK。main先初始化项目、Session后创建ToolRuntime，是追加API凭据业务表的实际位置。
- 既有ToolJob终态复用、unknown副作用不盲重发及ApiTestTask校验保持。新的引用失败发生在HTTP之前，保存错误类别与任务/引用身份，日志不含密文/解密输入。

## 采用做法

新增一个PG内部存储与小型服务，不引入第二套Agent/模型/HTTP客户端。

1. 凭据记录使用原CredentialSession身份，按父会话和正式项目隔离，Fernet密文追加保存。父会话删除由FK回收；原身份内容冲突明确失败，不覆盖历史。
2. 派发记录按父session/turn/原dispatch task identity确定身份，保存加密固定runner输入及内容哈希。相同身份/内容重用，冲突失败。该记录与既有协调器派发/子会话对应，不用模型提供的父身份授权。
3. 新Worker工具参数仅worker_action和dispatch_ref；解析引用必须匹配实际子会话的父关系、task_id、dispatch_role、project_id。拒绝叠加task、checkpoint或credential_session改写固定输入。
4. 可信executor恢复固定task及auth_ref对应凭据，执行业务HTTP后先加密保存新CredentialSession，再返回普通task_result和auth_ref。父端直接从内部服务回读；公开输出/报告/工件不会充当私密传输。
5. 工具Schema及规划契约同步增加可选dispatch_ref；已有一次性task输入保留。生产API Worker不再新建包含凭据的Prompt；历史调用/检查点不原地重写。旧serialized输入不是新引用协议验收的证据。

范围仅本次已确认缺口。自动过期续期、任意多认证流隔离、Cookie提取/并发刷新、真实独立默认Worker与完整App仍需要独立验证。密文存储不是这些能力完成的证明。

## 功能验证要求

- 使用上轮同一真实HTTP失败夹具，经生产ToolRuntime、PG Job/Session及既有工件保存机制再测login→refresh→profile，不能关闭脱敏或放宽断言。报告到底使用PG inline还是RustFS须按实际实现验证，不能预设存储形式。
- 新服务重建后读原引用，原Job终态重投零新增HTTP；独立public不带认证。
- 跨父会话/项目/turn/task、伪造或缺失派发、错误key、缺key、篡改输入在HTTP前明确失败；相同派发重用/冲突保留旧内容。
- PG行密文、Worker Prompt、公开消息/Job/工件检查不含生成的凭据；生成凭据只在内存中检查，不写证据。
- 回归旧一次性task/来源传播/unknown/审批/安全输出；实际启动及独立Worker是后续范围，不能用受控派发器冒充。

## 实际研究修正与结果

新增两个派生PG表：encrypted credentials和frozen dispatch inputs，依赖既有项目及Session表。main在Session初始化后初始化表并注入ToolRuntime；旧数据没有原地迁移/重写，旧一次性task合同保留。新父状态以execution_checkpoint.credential_ref保存来源，明文CredentialSession仅在可信服务内存中；新公开输出不携带该容器。固定派发按父session/turn/dispatch task身份复用，内容冲突不能改写原记录。

首次first/observed失败：控制派发夹具用save_session覆写worker_dispatches，PG故意保留旧协调元数据，导致刷新Worker无法找到receipt。修正夹具使用既有mutate_metadata，未放宽生产receipt检查。receipts批次在三次HTTP已200之后失败于夹具未提供原Job；按生产恢复补tool_job_id后，recovery/scopes仍实际增加三次HTTP。原因是ToolRuntime非case路径会重进终态Job。已补终态回读，校验会话/工具/原输入；拒绝引用不保留Job对象，避免异常处理回泄其公开输出。没有把终态failed改成可重领。

规划operations失败经HEAD原代码复核：历史冻结合同与当前ApiTestTask Schema本来不同，新/旧代码对该历史合同投影完全一致。测试改为与原冻结variant比较；当前注册合同仍由独立合同测试验证。未修改历史合同或令今日Schema覆盖旧快照。

complete父报告失败于“必须是RustFS”的夹具假设。现有ToolJobService将小报告以inline_text索引到PG，实际JSON/Markdown均已保存。按既有机制核对新服务回读、真实正文、SHA和size，保持PG inline机制；没有手动上传制造生产RustFS通过。独立main启动/重启专项另外验证了真实原件对象存储，但不冒充API Worker报告使用RustFS。

父报告还揭示Worker合并model_dump的值会把已验证AssertionSpec降为dict，产生三个Pydantic警告；改为保留验证后的字段原值。新真实Worker用类型断言验证，最终警告消失。

最终十六模块types-final：205通过21门控跳过61.33秒。三个真实存储参数包括核心链路、无依赖public和完整父ToolRuntime报告；实际POST login→POST refresh→GET profile均200，public不带Authorization；旧服务销毁后重建可读原凭据引用、状态和Job，终态额外HTTP为0。覆盖缺/错key、输入覆盖、turn/task/role/project/parent范围、缺引用、固定派发冲突保留、错误/不存在/跨会话Job不泄漏原输出且不发送。原件补采report-originals：1通过7排除9.56秒，现场保存两份PG报告正文及哈希。其他205/204/23通过批次重叠，不相加。

独立production API/main启动及重启HTTP origin专项：1通过22.66秒，新增表可在冷启动顺序初始化，历史检查回读及RustFS原件实际可用。该测试没有启动新的独立API Agent Worker，也没有外部AI请求。

回归扩大中首次遗漏既有QA_STATEFUL_RESEARCH_EVIDENCE_DIR，15个实际场景在保存证据处KeyError，另17通过；全部失败保留。补正确owned目录后继续验证正式用例/操作/审批恢复，不把夹具失败当生产缺陷或通过。

仍未完成：真实独立Agent Worker及默认AI鉴权闭环、任意多认证流隔离、自动过期/401续期、Cookie获取/并发刷新、解密/保存边界的真实进程kill与更广故障组合、历史明文凭据记录的生产迁移审计。完整P0—P5保持active。无新增可编辑PG配置、依赖、模型客户端、全局模型改动或前端构建声明。
