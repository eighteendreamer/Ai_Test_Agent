# 规划输出完整性：真实失败与采用做法

核对基线：4a77cf98（业务源码同bb7cb3a8）。本步骤属于P2用例规划及P0模型发送核验，完整P0—P5目标不变。

## 依据 → 决策

- `p4-tool-navigation-default/PG-planning.json`：第二轮已收到响应，`mode=ok`、`finish_reason=length`、completion_tokens=8192；可见正文为空。第三轮原请求包含`validation_feedback.candidate_response=""`，证明工作流把无正文响应当成JSON修正输入重新请求。三份物理请求身份和SHA不同，不能称为SDK重复发送。
- `src/application/models/model_runtime_service.py::_invoke_with_client`：SDK成功返回即赋`mode=ok`，同时保留finish_reason/stop_reason；mode不是业务正文完整性判断。不修改全局failure modes，以免改变自由Agent等消费方的语义。
- 本地已安装官方SDK的`openai/types/chat/chat_completion_chunk.py::Choice`及`chat_completion.py::Choice`：length表示达到请求指定输出Token上限。不能把HTTP200或合法JSON外形当成完整输出，也不能臆测隐藏推理内容。
- `src/application/documents/impact_calls.py`已有先保存响应、分类truncated/empty的实现：规划采用相同明确条件（finish_reason为length/max_tokens，或stop_reason为max_tokens），空白正文另行分类。不扩展为未经查证的跨提供商通用错误映射。
- `planning_workflow.py`：持久响应阶段解析错误一律进入下一修正回合；截断与无内容应先分类，普通非空、未声明截断的结构错误继续既有修正。真实输入不足仍可查询；本步骤不限制合法资料查询/补全。
- `case_planning_task.py`与任务Store：seed固定、响应检查点先保存、终态不可重领。新seed冻结响应处理协议；旧seed缺字段按原行为恢复，序列化仍省略该字段，避免改变旧请求/seed形状。

## 实施及验收

新建同步/持久规划冻结`case-planning-response-completion-v1`。在response阶段、JSON解析前检查上述高置信度事实。截断或空正文保存具体轮次、原元数据及正文，返回明确planning_failed，不自动修正或创建运行；日志记录协议、分类和停止原因。预算不增加，旧任务不改写。此协议是代码算法版本，非用户可编辑策略；可编辑规划参数仍MySQL，固定seed/检查点/结果仍PG，原件仍对象存储。

先回放真实失败正文及元数据，再验证：length即便外形合法也不计计划完成；普通坏JSON仍可修正；旧seed仍按旧语义恢复且不增加默认序列化字段。真实文件、MySQL/PG/RustFS和受控HTTP/SDK验证持久响应恢复、原截止时间、终态无新调用。保留所有原默认模型失败证据。

这一步解决无完整输出时错误启动修正的根因，不证明默认模型可以产生完整计划或执行成功。提供商为何生成8192Token而无可见正文仍需另外观察；不存储或展示隐藏推理内容，不提高输出/时间预算掩盖失败。

## 实际结果

6项失败样本/旧seed回放通过；实际规划回归50通过1跳过55.08秒；独立受控生产2通过108.14秒。新补充的同步正文保存检查首次失败（KeyError: response_text，3.76秒），说明仅保留持久checkpoint不能覆盖同步最终计划；拒绝轮新增实际正文保存后，最终专项9通过10.20秒，含流式和非流式真实HTTP/SDK及PG回执丢失恢复。原失败文件保留，没有在日志输出正文。

默认新任务1失败203.43秒。固定协议实际入PG，前两轮合法query（27.332/55.453秒），第三轮96.745秒后180秒总截止中断；没有完整第三轮响应，没有运行或业务HTTP。该试验没有触发截断拒绝，并在最终同步正文保留字段补齐前运行；最终保存专项单独实测。模型参数/预算未变化，未重发历史任务。完整升级仍未完成，下一步需继续实际观察流字段和阶段时耗，核验自主计划及执行。
