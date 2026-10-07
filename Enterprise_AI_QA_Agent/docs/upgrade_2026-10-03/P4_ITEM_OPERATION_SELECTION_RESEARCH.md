# 正式项与准备项的明确操作选择：实现前查证

代码基线07275986。原完整目标已重读；当前工作区只有此前披露的无关文件。研究先于生产修改。

## 已查证事实 → 采用做法

1. `p4-source-identity-default-first/requirements.md`明确api-test-runner.execute_task。实际第二轮固定一个正式目标、自身清理；第三轮只读该操作耗时25.208秒，第四轮技术绑定74.048秒在原截止取消，没有业务HTTP。这些是已抓到的新失败，不按模型记忆推断。
2. `planning_workflow.py`逻辑上下文的available_tools在原预读政策下已保存操作名，但`LogicalFormalCaseItemV3`没有选择字段；`planning_operation_prefetch.py`只遍历prepare.instructions。因此即使AI选择让正式项自行准备和清理，也不能沿该原预读通道取得契约。不能为触发预读虚构prepare。
3. `planning_stages.py`固定业务决定后技术绑定只追加声明；`PlanningStageCheckpoint`与PG `_check_checkpoint`冻结逻辑及hash。最终FormalCasePlanItemV3仍由完整content供Agent理解执行，不能为性能把任意JSON编译成模式参数。
4. 原查询服务支持完整tool或指定operation，基于seed.tool_contracts/tool_operations、哈希与真实native变体；八条批次队列已有实际丢回执/恢复。沿原通道读取、去重和分批，不另建执行器或查询引擎。
5. 本地`Agent轨迹与协议鲁棒性.html`的plan/action/observation、状态/轨迹/幂等恢复原则 → 对新读队列记录来源、固定阶段及原截止；逻辑选择不是执行授权或业务成功。现有协议故障测试作为实际验证模板。

## 本步工程设计

原MySQL配置operation_prefetch_protocol增加独立v2值，要求固定完整来源身份/v2文档预期栈，使用Prompt v9；旧v1和v8及更早Prompt/seed/检查点字节不变。没有新依赖或SQL表列。

新逻辑wire扩展operation_selections，每项含item_key、tool_key、可选operation及evidence_ids。正式项、dependency、prepare均可明确选择；完整tool和具体operation均可读。后端只核对既有执行项、已读业务依据及该项已有引用；不存在的工具/操作通过原查询错误反馈呈现，不将技术读取当业务门卫。没有明确选择则原effects仍可query，不按HTTP方法或用例参数猜工具。

选择单独保存在阶段检查点，与业务决定共同构成新logical_hash；旧None保持原决定hash。PG在effects阶段比较整个原固定stage，禁止删改选择或重新hash换选择。最终执行决定仍用原PlanningDecisionV3，选择只作为只读规划元数据和原round证据。技术阶段收到固定选择及原契约，不获更换逻辑权。

新v2查询根据明确选择去重，origins记录item_keys和业务引用，沿原query/next_offset分批恢复；不得延长deadline、增加模型轮次或调用业务。新wire在query阶段不携带选择；normalize到原pending结构，避免把新metadata强行写入旧执行Schema。

前端在原操作预读设置提供正式项选择入口，保存一致政策及前置关闭清理；显示实际固定选择和原依据。原执行模式、用例content、运行与权限不改。

## 必须实际验证

以原最新失败逻辑/原件为样本，旧v1不会读取正式项，新显式选择只读完整32字段契约；不改旧文件。实际MySQL/PG/RustFS任务需验正式内部产生/清理及prepare→正式清理、重复选择去重、未知操作反馈、八条分批、丢queue/read提交回执、更换Worker/当前配置、原截止与终态不调用，以及固定选择/hash篡改拒绝。旧v6/v7/v8seed/Prompt序列化逐字回放。

实际原生业务仍创建两条、查询一条并清理全部，原JSON/false/0/null不补字段，固定V1在V2后恢复。组件/类型构建通过后，才作新的原默认模型独立API/Worker实测；完整技术绑定/业务证据不通过就保留失败。不重复当前失败任务，不更换默认模型或暗增预算。其余P0—P5范围保持，人工录制/Docker和共享消费等不能由本步代替。


## 实际结果与后续研究依据

40新旧专项、2真实业务、84回归(3另跳过)、实际PG选择篡改事务1、完整tool1通过。前端保存夹具首次2失败52通过，回显修正后54通过，新增实际固定计划展示55通过；构建/实际typecheck退出0，误调用test --help不算校验。详细原命令/失败见PLAN6.63。

新默认1失败217.39秒；三轮43.022/97.453/38.808秒。模型选prepare→正式target，均明确相同execute_task，原队列去重一次实际读取；第三轮已带完整契约仍在deadline取消，0正文/流未结束，0Run/业务HTTP。默认参数未改，预读有效不等于整体更快或自主完成。

进一步代码事实：默认原正式用例在创建时已有source_refs[{source_type:api_doc,source_id:真实文档ID}]，planning_sources.case已把完整source_refs送到模型；原第一轮只是请求该已关联原文。当前_recall只读取完整用例，未把明确关联来源形成可恢复的读取队列。下一步须研究固定来源读取、版本/解析快照与分页、初始检查点/原截止，而不是盲注入全项目文档或读当前配置升级旧任务。本次未实现该后续能力，也未重跑同配置失败。
