# 只读纠正响应方式：查证后实施

基线cd85f815；完整P0—P5保持active。上批p5-ui-observation-default5/positive/correction-response.json为真实ProviderClientError/Request timed out，三个物理请求均非流式，SDK最终无可靠响应。原运行error与原件保留，不重新用旧意图发请求。

## 现有依据

- `agent_case_receipt.py` 持久化intent→调用→完整response，旧未知不可重发，保存响应即权威；原预算仍只消耗一次。当前调用直接invoke，未选择响应流。
- `ModelRuntimeService.stream_handler/_send_with_client` 已支持原模型客户端的流式汇总；仍经过字段映射、模型/SDK/超时参数核对。OpenAI Chat/Responses、Anthropic、Google均有stream实现，不应新增Step专用客户端。
- MySQL模型配置已有capabilities.streaming及可编辑override，`model_invocation_parameters`冻结该能力；不增加PG可编辑配置。
- `generation_pipeline.invoke_frozen` 与 `PreparedCaseGeneration` 已在新阶段冻结response_transport_protocol并用serializer省略旧记录的空字段；不把chunk当完整响应。这是直接复用依据。
- `CaseReceiptCorrectionCheckpoint` 是PG业务检查点，新增可省略协议只属于实际调用证据；旧意图继续未知、旧response继续回读，不能因新代码自动重新发送。

## 采用方式及验证边界

新纠正阶段依据既有模型streaming能力选择冻结协议，能力关闭时沿原非流式调用。选择发生在intent保存前；回读intent后按其协议调用原ModelRuntime，不临时更换模型或增加预算/超时/重试。仅计数公共文本chunk，完整模型结果入RustFS并追加PG响应检查点，原终态/工具禁用/证据校验不变。断流、length及意图中断仍不可当完成或再次执行。

需要实测：真实SDK/SSE完整响应、非stream能力、截断/缺结束标记、同一旧检查点原样回读和未知禁止重发；再使用真实默认Step对已捕获只读请求复验，生产进程中断另验。传输通过不冒充完整多候选Agent/App验收。

## 已执行结果与剩余问题

本地课程《Agent轨迹与协议鲁棒性.html》150行强调轨迹、工具、权限、幂等及恢复同时验证；本批据此同时核对物理发送、实际POST/GET/DELETE、原预算、检查点及终态重放，未采用课程示例统计阈值作为产品事实。

- 真实SDK/SSE五传输：Chat、Responses、Anthropic、Google、LangChain Chat。完整、缺终态、length三种响应共十五参数，加旧实际检查点兼容，共16通过24.78秒。业务操作均实际发生，纠正与重放不额外执行业务。模型答复由受控HTTP提供，不是外部模型业务质量验收。
- 最终十模块回归159通过5门控跳过124.63秒，包含实际PG/MySQL/RustFS回执链路、原非流式能力声明和已有SDK/生成/规划契约；新增读取真实默认失败回执的回归，数字路径仍被拒绝。此前158通过5跳过为较早版本，不累加。
- 默认模型历史新任务：default-fixed一失败106.66秒，流式请求完成但expected_path_tokens返回数字0。只补公开字符串类型说明和Schema description，普通Prompt v6、场景v7，不转换候选、不改原结果。
- default-paths一失败113.57秒，finish_reason=length，prompt_tokens8154、completion_tokens8192，公共content非空事件0，私有字段只计数32805字符，不保存私有推理。说明取得HTTP/SDK流终态不能证明生成了可用回执；8192额度耗尽是观察事实，尚不能把复杂度、模型设置或某句Prompt认定为唯一根因。
- default-paths加载的是当时已冻结请求，后续整理Prompt句子不能冒充该默认任务已验证最终源码。最终源码由受控回归验证，默认质量继续待验。
- 初始夹具失败均保留：content_supplier过早访问agent；Google显式重试配置不受原适配器支持，测试改回已有默认传输，生产限制未放宽；默认夹具两个注册key断言错误在真实发送前失败，不列作供应商失败。

按用户暂缓真实AI验收的安排，后续没有新发默认供应商请求。继续验证生产进程边界，保留原自然90秒条目和300秒资源租约；不为加速测试重置锁、不增加原8轮/8192、不重发未知请求。生产中断结果另在方案7.38、JUnit和本批审计登记。没有新表、依赖或前端变动；配置仍MySQL，业务检查点PG，原件RustFS。


生产流式中断最终2通过693.48秒：invoking正式error/recovery_required，response正式passed；原租约后生产清理及恢复Worker、当前配置变化和终态零重发均核对。模型响应为受控HTTP；这项证明恢复机制，不证明默认外部模型纠正质量。
