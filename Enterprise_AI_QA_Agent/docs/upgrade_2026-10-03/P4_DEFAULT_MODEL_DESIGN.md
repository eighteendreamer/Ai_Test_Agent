# 默认模型影响判断：查证与真实样本

研究基线：0aa271f1，2026-10-05。完整P0—P5仍未完成。

## 本轮依据与做法

- `impact_analysis_context.py`：请求、项目策略、默认模型依次选择；冻结完整Skill/Prompt及非敏感模型参数。实际只读检查默认模型仍为step-5-preview、输出8192、原生OpenAI适配器、SDK超时60秒。
- `impact_calls.py`、`impact_response_protocol.py`：保存调用意图及真实响应后才解析；引用必须符合固定正文、字符位置、哈希和用例字面JSON路径。保留截断/未知；不能把模型声明当语义通过。
- `test_document_difference_storage.py`、`test_document_impact_tasks.py`：复用已验证的隔离MySQL/PG及实际RustFS、真实原件和解析/比较进程；不改生产默认模型配置。
- `p2-agent-assessment-model-diagnostics.json`：历史执行回执耗尽8192输出却正文为空。该证据属于执行评估，不预设影响模型必然出现同一失败，也不盲重跑或提高输出预算。

先取一条代表性调用的证据：实际Markdown V1/V2包含保留期修改、优惠规则删除、导出规则新增及不变的未登录拒绝条件；完整8步用例的关键验证位于最后一步，另外保留负向及优惠用例。初始探针向一个真实差异单元提供全部固定原文、完整用例和来源，没有预填模型答案。记录实际SDK HTTP请求正文（不记录端点和认证头）、物理发送次数/时间、PG检查点、RustFS对象原字节及引用核验。

这只是单元诊断，不作为自动协调全范围验收。后续根据真实失败定位协议/Prompt/适配器根因，再跑自动补读、跨节规则传递、完整范围判断及人工语义基线。单元通过也保留pending_review与deployment unknown。实际结果和失败写入新的独立证据目录，禁止覆盖此前失败。

本轮仅在新项目的隔离MySQL设置任务总时限600秒；模型输出、SDK超时、适配器及全局模型配置保持原值。任务业务及版本写PG，原件与大结果写RustFS。各资源只清理本测试验证归属的表和对象。

## 首轮实际失败与取证修正

首轮126.06秒、1失败：固定输入准备及真实存储通过，调用120秒超时后保存`outcome_unknown / dispatch_TimeoutError`。原始目录`p4-default-model-first/`不重写。该测试拦截`httpx.AsyncClient.send`，没有覆盖实际SDK，截获0次不能解释为未发送。

直接检查现有`OpenAIChatClient._make_client`创建的客户端类型及MRO，确认OpenAI3.14.0的`AsyncHttpxClientWrapper`继承`httpx2.AsyncClient`。修正为拦截实际SDK客户端类的send方法并原样调用原方法；不替换供应商响应，不修改重试/超时。保存实际传输类及物理请求正文。下一诊断使用新项目/新任务，原未知调用不自动重发；是否发生SDK重试以捕获的真实尝试为准。

修正取证后的第二轮125.80秒、1失败：一个逻辑调用截获两次相同POST，第一次ReadTimeout60.156秒，第二次59.078秒时被调用总预算取消；仍保存unknown，没有模型响应或语义结论。第二次trace未捕获BaseException中的CancelledError，只有耗时，没有HTTP状态；从明确的总预算取消链路解释这个限制。后续取证显式记录取消，不回填修改原文件。

## 从实际请求确认的引用契约缺口与修复

首轮真实请求的三个完整用例均未包含对应`version_record_sha256`；`fixed_case_catalog`只提供case_id/version_id，完整用例读取只有case/source_links。响应Schema却强制要求该记录哈希。它与正文content_hash不同，不能要求AI自行计算完整版本记录的canonical SHA-256。该缺口由保存的真实请求和清单逐值对照确认，不把本轮超时归因于此，也不虚构模型曾返回错误哈希。

新任务冻结`impact-call-v3`：沿用清单内已经核验的版本记录哈希与正文哈希，随用例目录提供；Prompt明确复制记录哈希，完整用例仍必须实际补读。引用验证保持不变，目录存在不代表正文已读。旧v2 seed/请求及目录形状保留，已存请求/输入hash不修改。没有修改模型参数、数据库Schema或公开API。用真实失败输入回放证明旧请求不变、新请求包含确切哈希，再以真实文件/HTTP/存储专项核验新引用及旧请求恢复。

## 当前实际结果与下一步

引用修复后第三轮复杂默认模型探针仍1失败、126.86秒：两次相同POST，第一次ReadTimeout，第二次显式记录cancelled_without_response，原调用unknown。当前三轮均无响应，不能评价规则/用例语义，也不能把超时根因归给引用哈希。

按项目连续失败换路线规则，停止重复复杂探针，调用`SettingsService._test_api_key_connection`已有简短ping/pong。实际一次HTTP、2037ms、返回pong，默认配置前后一致。这证明基本服务响应，不证明复杂输入可用；直接只读方法诊断也不等于正式设置API验收。后续研究真实自动协调的单元请求及跨节补读/耗时，保留完整用例和固定来源，不靠缩减目标、提高全局预算或把连接成功包装成语义通过。

引用修复的真实失败输入回放3通过；实际MD/DOCX/PDF、两适配器、HTTP及MySQL/PG/RustFS相关回归102通过0跳过389.54秒；独立生产API/解析/比较/影响Worker的MD场景1通过0跳过68.54秒，6次物理消息与完整冻结请求匹配，结果保留partial/pending_review/unknown。回放3项包含在102内。失败、成功和连接诊断均分别保留原文件；具体命令见IMPLEMENTATION.md。

## 本轮自动协调的研究与真实输入〔基线770f7159〕

直接研究impact_workflow.execute/_unit、impact_calls.build_request/invoke、impact_batches.build/read_difference、共享document_operation_task_handler及既有真实HTTP协调专项，再复用同一real_scenario的实际原件、三条完整8步JSON和固定来源。新测试test_document_impact_default_workflow.py通过真实默认ModelRuntimeService执行既有工作流和共享Handler，不预填候选、不替换提供商。使用隔离项目MySQL总期限600秒；单调用120秒、SDK60秒、输出8192、重试及源默认配置保持原值。Handler负责租约心跳；本测试在同一pytest进程运行Handler，不能当作独立Worker进程或生产历史迁移。

源码确认清单与推理单元当前一一对应：全部原文段、全部raw/normalized/section/table差异、全部完整用例依次执行。这个实际样本为2段、6条差异、3条用例，共11单元；当前先发现规则，再逐用例提供完整候选集合及其原文区间。续读带上此前完整消息、完整模型响应和新固定读取，不靠摘要替代历史。

实际请求http-request-1.json为18948字节，SDK消息system正文2132字节、user正文14968字节，user内analysis_protocol约6020字节。这里UTF-8字节不是Token计数。第一次物理尝试ReadTimeout，SDK第二次尝试返回HTTP200及完整响应；原响应请求补读新版和差异，但将当前单元ID中d27写成4b27。保存响应后，现有核验正确拒绝unit_mismatch。另一个原文单元返回有效read并自动续读；续读请求48920字节，随后两次尝试未取得响应。以上是实际行为，不证明输入大小造成超时。

当前OpenAIChatClient.invoke使用非流式chat.completions.create；同一客户端已有stream方法。本轮没有切换流式或改模型输出参数。后续可沿已有机制研究首响应/完整完成/读取空闲耗时，但必须冻结新策略并保存部分流和明确终态，不能把HTTP200或流式片段当完整候选，也不能据此推断供应商未执行原未知请求。

下一步方向已写入PLAN3.5.9：固定身份由后端绑定、短引用严格转换并继续事实核验；证据清单与推理任务分开研究；预算与阶段调度联合核对；参考planning_workflow已有validation_feedback和轮次/检查点，处理已收到完整响应的可纠正协议问题。新协议需要版本化，v2/v3请求及coordinator-v1结果不重写。先回放本轮真实失败，再验证补读、错误/未读引用及恢复，最后真实模型及人工业务基线。没有证据时不宣称需求评审、矩阵、增量草稿闭环。

最终1失败0跳过607.72秒，Handler实际运行601.281秒。6逻辑调用、11物理发送，其中5次ReadTimeout、2次HTTP200并保存完整响应、4次取消无响应。最终11单元状态为1rejected、4unknown、6unprocessed/task_deadline；候选规则0、结果partial/pending_review/deployment unknown。三个用例没有实际调用模型，无法评价其业务语义。completed只表示部分结果发布，测试在全范围业务断言失败；原固定输入、响应、未知及未处理范围全部保留。

失败前已实际核对终态重投/历史读取不发模型、不重建/上传和终态不重领；源默认参数前后一致。所有原件、对象、物理请求及逻辑请求/响应/核验另存p4-default-model-automatic-first/，失败XML/log保留。兼容回放另3通过0跳过0.36秒，不重复此前102/1项回归作为本轮成绩。没有新生产代码、依赖、Schema或前端改动，本轮新协议只处于设计阶段，未重跑前端/浏览器、独立生产Worker或生产历史迁移。
