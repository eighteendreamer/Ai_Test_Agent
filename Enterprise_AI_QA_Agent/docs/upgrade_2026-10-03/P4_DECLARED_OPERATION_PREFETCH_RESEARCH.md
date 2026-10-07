# 逻辑计划明确操作后的固定契约读取

基线e4d268e8。先读用户完整目标、AGENTS、原默认PG检查点/四个调用、planning_workflow/stages/tool_contracts/service、PG检查点校验、固定上下文、任务测试与前端政策消费方；参考本地Agent轨迹与协议鲁棒性课程的计划/观察/恢复双重验收。

实际失败：原逻辑prepare.instructions已明确tool=api-test-runner、operation=execute_task；随后模型单独输出同一操作的只读query，用时56.697秒，第四调用在原180秒截止被取消。这证明阶段交接有可消除的资料获取回合，不证明消除此回合就能解决所有默认模型耗时。

现有实现→采用方式：固定seed已有完整tool_contracts/tool_operations；query_tool_contracts核对父hash并返回完整模型；原query阶段每条提交PG且逐步恢复→将逻辑明确选择的操作转为原查询队列，保存origin和logical_hash，不新增模型调用、业务操作或另一套恢复机制。原round.decision仍保存模型的原finish，系统资料读取单独标注，不能伪造模型query。

可编辑开关operation_prefetch_protocol=case-planning-declared-operation-prefetch-v1仍存MySQL，要求固定文档预期/v2阶段栈，Prompt v7与原seed/阶段/查询队列存PG。None省略保证旧快照；原v6和更早Prompt及任务保持。逻辑阶段仅增加固定操作名称导航，不给业务工具参数、不从HTTP方法猜工具。只有prepare.instructions顶层明确字符串tool/operation时预读；没有明确声明仍由模型查询。同一操作只加入一次，保存提供声明的项键；未知操作交原查询反馈，不阻断本来合法的其他路径。

逻辑验证、排序、冻结后，将原EvidencePlanningDecision查询对象保存为pending_decision/phase=query；然后原循环读取固定seed并保存结果，再进入effects。无目标不预读。资料读取不消耗next_round、不延长deadline；超时/unknown模型发送仍由原逻辑处理。完整用例/文档和完整契约不裁剪。新查询反馈明确系统来源，最终模型仍必须引用已读证据才能绑定。

先验：实际失败回放确认操作来自已保存instructions、完整模型及父hash；旧seed/Prompt字节一致；真实上传原件/固定用例/MySQL/PG/RustFS，读取后丢提交回执、新Worker恢复不重复模型且禁读当前注册，原截止/终态保持；缺声明/未知操作保持query反馈。再做新默认模型及独立流程，失败原样保存，不能用受控模型成功替代自主成功。前端开关/降级同步，整个P0—P5保持。


## 最终实际结果

追加查询按原Schema八条分批，明确声明完整保存。最终11专项、旧48/追加22回归、三轮实际业务1专项、前端45与构建通过；首业务回执夹具缺expected及首探针打印编码失败保留。新默认1失败213.76秒，5逻辑轮次，三次把文档16位ID摘要改为64位hash，未到effects/预读/业务。planning_sources.py仍显式digest[:16]而payload暴露完整content_hash，该引用表达需要单独重审，不能据此宣称预读无用或默认超时解决。完整证据及下一步见PLAN6.61。
