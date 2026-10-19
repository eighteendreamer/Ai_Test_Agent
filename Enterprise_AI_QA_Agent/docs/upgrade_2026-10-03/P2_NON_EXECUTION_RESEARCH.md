# P2 未执行证据与对象身份语义调研

2026-10-19，基线61c52751。完整P0—P5继续进行中。

## 实际依据与修复范围

live5现场的实际物理请求已包含完整ApiTestTask Schema，required为task_id/method/path；因此“未提供完整入参契约”的初步推测被否定。模型漏填path后，API原生运行时在创建executor之前返回invalid_task_payload/http_request_sent=false。后续修改原操作输入被正确拒绝；使用新操作身份后POST/DELETE各一次。

ToolOutputObservation目前只保存输出哈希、状态和时间；capture_returned_evidence只看声明路径与检查，未表达原生处理器已经确认没有发起业务操作的状态。这是原生执行边界与通用证据之间的状态缺失，不能靠重复提示、忽略failed或通用重试解决。ToolJobService.observe_output和PostgresToolJobStore._observe_job_output_sync已有可靠返回与效果证据同事务保存能力，应扩展该边界，而非另建恢复平台。

采用做法：原生处理器只在明确的输入校验/私有输入解析失败分支，通过服务端ToolExecutionContext记录未执行原因；ToolRuntime将其写入原输出观察。该标记不从模型参数、工具公开输出里的布尔值或业务错误状态推导。返回丢失时仍unknown。v1/v2效果捕获保留原观察并标记not_executed，覆盖统计保留该操作及原因，但不把它当成可能产生对象的调用；没有任何实际产生来源时仍阻塞。历史记录不重新投影、不重写。

另一处独立根因：object_path_tokens=[]合法地提取整个返回，哈希比较严格使用这个完整值。live5创建和清理输出含不同method/path/状态等字段，不能作为同一订单的稳定身份。既有effect_task提示仅要求“指向真实输出”，未说明跨操作身份匹配的语义。明确Schema描述和固定任务提示：应依据项目文档与实际工具输出契约提取同一业务身份，而非整个带运行元数据的输出；根值确为身份时仍可用[]。不按HTTP方法猜字段，也不在执行中替换冻结声明。

专业信源：本地《Agent轨迹与协议鲁棒性》的工具参数契约、轨迹及幂等章节强调结果与实际工具轨迹分别验证；具体做法以本项目ToolOutputObservation、原生API验证分支和已捕获live5失败为准。

## 验证计划与当前边界

先保留live5公开失败输入/输出为回归样本，复现未执行状态缺失。随后验证原生输入失败不发HTTP、公开输出/模型参数不能伪造服务端未执行证明、原返回丢失仍unknown、仅未执行不能通过，以及真实创建/查询/清理及新Store恢复。再以默认Step重新规划新运行，不改旧计划或历史失败。受控样本通过不等于默认模型完整P2验收。

## 实际修改及验证〔同日〕

新增可选ToolOutputObservation.non_execution，由原生处理器通过本次调用独立的ToolExecutionContext传递，只有明确的输入校验/私有输入解析失败分支写入；观察要求failed，旧观察序列化仍保持原形状。与输出、效果捕获使用同一PG事务；未改配置存储、数据库表或模型传输。HTTP请求是否成功、业务断言和对象覆盖继续分别判定。通用上下文每次调用清空本字段，模型参数及公开输出中同名字段不能设置它。

真实失败回归样本为tests/fixtures/api_task_missing_path.json：保留live5公开入参/输出、三条原操作信封及固定范围，重复声明只保存一份，读取时核对还原后的原始摘要。修改前运行tests/test_case_non_execution.py为 **2 failed、2 passed、1 skipped，8.38秒**，确认缺状态和捕获错误。修改后首次相关回归 **28 passed、42 skipped，8.97秒**，跳过的实时环境不计通过。

设置RUN_LIVE_PLAN_RUN_CREATION=1及RUN_LIVE_CASE_EFFECT_RELATIONS=1，运行tests/test_case_non_execution.py和tests/test_case_effect_relations.py：**37 passed，57.66秒**。包括真实MySQL配置/PG运行/RustFS原件及HTTP，未执行失败后创建/查询/清理、原响应提交回执丢失、工件交付失败、新Store读取、失败终态重投无新增HTTP，以及v2正式结果覆盖。已知未执行保留原因；没有任何成功产生来源仍blocked，实际创建/删除后失去返回仍unknown，覆盖不足仍blocked。材料目录为p2-non-execution-recovery-faults-20261019/和p2-non-execution-relations-20261019/，原工件未作为源码提交。

对象身份说明只进入分任务Prompt及其输出Schema。首次扩大回归 **1 failed、72 passed、16 skipped，29.90秒**：公共Pydantic字段描述改变了旧v10提示工厂。纠正方式是撤回对公共字段的修改，将描述限定到任务Schema；相关回归 **35 passed、3 skipped，1.31秒**。新任务快照保存task_prompt_revision=2，Prompt版本带.r2；旧快照默认revision=1且不新增序列化字段，仍按原Prompt与版本恢复。没有增加来源轮数、改写旧Prompt或禁止根值身份。

设置RUN_LIVE_CASE_PLANNING_TASKS=1，运行tests/test_planning_effect_tasks_live.py、test_planning_effect_tasks.py、test_planning_reference_schema.py、test_planning_evidence_references.py：**52 passed，30.41秒**，含v1/v2真实存储恢复、已完成声明不重发及原额度/期限保持。材料p2-object-identity-prompt-recovery-20261019/。样本去重还原后单独回归 **9 passed、3 deselected，6.96秒**。

设置RUN_LIVE_PLAN_RUN_CREATION=1，运行tests/test_tool_operation_observation.py、test_case_operation_verification.py、test_agent_case_assessment.py、test_api_request_body_contract.py、test_tool_runtime_heartbeat.py：**78 passed、1 skipped，76.44秒**。覆盖原输出中断/恢复、真实声明查证、断言、无请求体语义和心跳；跳过的是独立外部默认模型验收，不计通过。

前端沿用现有CaseEffectCoveragePanel的通用JSON树，无需改产品渲染。由真实v2正式结果提取case-non-execution.actual.json，新增断言验证未执行原因可见，且对象仍是实际2个/覆盖2个。首次夹具误写到src/tests导致导入失败、0项执行；改到已有tests/fixtures后，`npm test -- src/features/projects/CaseEffectCoveragePanel.test.ts`为 **6 passed**；`npm run build`的vue-tsc与Vite构建通过。此处是组件及构建验证，不当作完整浏览器验收。

兼容范围：可选JSON观察及任务Prompt修订字段，无SQL表变更或新配置；旧观察/快照仍按原形状读取。历史结果没有重写；生产历史迁移与投影核对仍在总体未完成清单中。

新的真实默认Step完整八步场景p2-default-full-non-execution-8192-live6-20261019/已结束：**1 failed，466.16秒**，Worker正常退出，规划任务failed。使用当前MySQL配置的8192输出额度，隔离规划期限600秒，没有采用16K输出覆盖或修改全局默认时限。5次真实模型回复均正常stop，无鉴权失败、截断或超时；没有创建Run，没有业务HTTP。单目标场景不能替代多候选/补入正式依赖验收。

原始响应核对：第3轮产生声明和第4轮清理声明均选中稳定身份路径["task_result","response_body","order.id"]，此前整输出身份错误未再出现。第4轮清理检查引用T5中的200，但同一原文行含GET和DELETE两个200，按唯一字面值规则拒绝。第5轮将状态检查改为用例期望路径，另在T引用中加入excerpt，违反本次实际下发的v2 Schema（T的excerpt只能为null），最终结构解析失败。因此不能将此轮称为服务端Schema/解析器不一致，也不能忽略原文歧义后判定通过。后续须调研引用目录粒度、合法细化来源方式和反馈可用性，再确定根因修复，不能增加盲重试或静默修正模型输出。原失败与旧结果保留。
