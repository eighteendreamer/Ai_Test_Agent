# 来源引用与内容摘要的一致身份契约

基线5b5490ac。完整目标、原默认5条实际物理消息及PG反馈、planning_sources/service/stages/workflow、声明源编译、固定Agent上下文、PG检查点校验、前端引用显示与旧任务测试均先读后改。

三个原失败候选将document:<源ID>:<16位摘要>写成document:<同源ID>:<完整64位content_hash>，与原已读payload完全对应，但不等于原ID，原严格校验正确拒绝。planning_sources.py对document和recording显式使用digest[:16]，planning_tool_contracts.py对工具接口及操作使用完整digest，且两者都显示完整content_hash。当前协议存在两种不同的引用表达；不能在结果校验里按前缀或相似文字兜底。

已有实现→采用方式：工具依据已使用完整payload摘要身份，固定源结果由json_text确定性计算→新可选source_identity_protocol=case-planning-source-full-hash-v1让文档/录制同样使用完整摘要生成ID。业务source_id、version、payload和content_hash不变；case版本ID及工具ID不变。新身份只作用于新计划固定政策下的只读查询，旧服务缺省仍digest[:16]，旧seed/计划/运行不重新编号。

开关存原MySQL规划政策，要求已有固定文档预期/v2阶段栈，可独立于明确操作预读开启；新Prompt v8、政策/查询/完整ID存PG，原件RustFS。阶段版本选择从重复嵌套条件提取为一个共同函数，避免继续增加三处不一致版本门卫；native导航按已验证固定阶段及原v2/v3能力选择，旧Prompt逐字保留。未知身份协议明确报错，不静默采用当前配置。

统一身份在来源创建时生效，查询结果、模型上下文、逻辑引用、技术绑定、原文预期证明、Agent上下文/Result和PG恢复均使用同一完整ID；不转写模型结果、不模糊匹配、不更改历史hash。完整JSON/原文/模型保持。

先验证真实失败回放在旧协议仍拒绝、采用相同原payload的新完整ID后对应引用可精确解析；短ID/改完整hash/外国项目拒绝。新旧查询在实际MD/DOCX/PDF原件及真实数据库里对照同payload/版本；MySQL开关变化、提交丢回执/换Worker/终态恢复保持原seed和截止。实际原生业务与前端政策同步后，再做新默认完整流程。失败原样保留，受控模型不能代替自主成功；P0—P5未完成范围保持。


## 实际结果与下一步依据

24来源/预读专项、82回归(另3跳过)、1实际原生业务、48组件及vue-tsc/Vite通过。补验三格式PG首次3失败源于同步调用使用expected_parameters持久夹具，查原调用后改为原同步夹具，3通过12.02秒；保留原失败。原生业务三轮受控模型、5真实HTTP、两个原对象全覆盖且0剩余，不代替默认模型能力。

新默认严格1失败206.94秒；四轮18.666/61.435/25.208/74.048秒，原180秒总时限。引用完整ID与逻辑冻结通过，无校验反馈；逻辑选一个正式目标自身清理，无prepare。旧prefetch只扫描prepare.instructions，所以不触发；证据阶段另25.208秒读取execute_task，再在0正文时截止取消。四逻辑/SDK/物理、四200头、末流未结束、0Run/业务HTTP。完整原件/JSON/接口未截断，默认参数未改。

后续来源：实际requirements.md明确api-test-runner.execute_task，现逻辑FormalItem Schema没有选定工具操作字段，逻辑上下文还排除了工具导航；原预读只有prepare.instructions。应先研究一个独立版本的只读操作选择如何覆盖正式项与prepare、绑定原固定导航和业务引用、冻结逻辑/hash并沿原查询队列恢复，不增加虚构prepare或改用例content。源件/内容hash/版本和实际阶段成本必须保持可核对。这是待研究的正式项能力缺口，不是本批已实现功能。详细命令/结果/限制见PLAN6.62和IMPLEMENTATION。
