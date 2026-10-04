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
