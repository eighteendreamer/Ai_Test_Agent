# P4 单元响应协议与核验：实施前查证

研究基线：`232e6ca6`，2026-10-04。上一步已保存固定请求和响应；本步接结构化补读/结束决定、候选引用核验及独立持久核验记录。自动协调、全范围发布、需求评审和生产 Worker 继续接入。

## 依据 → 做法

| 查证位置 | 事实与对齐方式 |
|---|---|
| `planning_service.py:_parse/_system_prompt`、`case_planning.py:PlanningDecision` | 项目已有 typed JSON 决定和补查询→再推理协议，允许围栏/说明文字；影响分析使用自己的 read/finish 协议，继续用 Pydantic/标准库，不加框架 |
| `planning_workflow.py` | 响应先保存再解析，错误反馈不代表成功；影响核验只读取已存响应，不再次调用模型 |
| `impact_calls.py:build_request/read_request/read_response` | 固定输入/清单、完整消息和响应已有不可改对象检查点；新请求附带版本化响应 Schema，旧已存请求/未知重试不得改写 |
| `impact_batches.py:read_range/read_difference/validate_*_citation` | 原文位置是固定原文的 Python 半开字符区间，用例路径是字面 token；复用真实切片/完整版本核验 |
| `document_differences.py` | normalized 差异的坐标属于归一文本，不能据此证明读过原文该位置；raw 与 section/table 的原文范围可以计入实际已提供范围 |
| `postgres_document_operation_store.py:_owned`、`postgres_document_impact_calls.py` | 任务行锁、活租约/attempt/worker/token及对象引用补偿已有；核验记录独立表，关联原调用，不修改调用/响应记录 |
| 本地课程 `Agent轨迹与协议鲁棒性.html`、`评测工程化与CICD.html` | 协议/结构错误、版本和证据追踪必须实测；结构正确与业务语义正确分开，不把引用存在当成影响判断正确 |

## 协议

每次返回单个 `impact-response-v1` JSON 对象，明确原单元及 read/finish。

- read：请求固定分段、完整用例、指定差异或原文范围，不执行测试工具。读请求必须属于本次固定范围且能通过既有读取预算；不把尚未读取的资料当作引用依据。
- finish：保存本单元的 analyzed/evidence_insufficient 声明、总结、规则变化候选、受影响用例/验证点、理由和未解决项；它是模型声明，不证明完整项目覆盖。
- 候选有局部 key、added/modified/removed/uncertain 分类、原文引用和用例影响；影响引用完整固定用例的真实 JSON 路径或自身说明。生效建议只作为候选，部署事实仍 unknown，语义状态仍 pending_review。
- 缺来源/部分解析警告由后端带入核验结果，不能只依赖模型记住它们。新增/删除/不确定匹配交后续评审，不直接启用、停用或执行。

解析延续围栏/说明文字兼容，但拒绝重复 JSON 键、NaN/Infinity、损坏的整体 JSON 和多个 JSON 对象造成的歧义；不从损坏对象内部寻找看似有效的候选。错误只记录安全代码/说明，原响应已独立保留，不把原文塞进日志。

核验先确认单元身份，再核对固定引用及实际交付范围。已经保存在 input 中不等于该次模型已读到：以不可改 request 的 primary/additional_fixed_reads（含续读的完整旧消息）计算实际提供范围。目录、标题、章节元数据不算正文；连续已提供区间可合并。normalized 差异先请求原文再引用。未提供的用例版本不得被列为受影响用例。核验只确认引用事实，不校验业务结论正确性。

## 持久与恢复

RustFS 保存核验正文；PG 独立 impact_response_checks 表关联调用，唯一 `(call_id, validator_version)`。检查点固定任务、调用、请求/响应/清单哈希、核验版本、对象引用与创建时间。核验正文固定运行时版本、结构决定/拒绝问题、来源局限及待评审/部署未知状态。原 call 的 response_saved 保留，不追加可变字段改变历史 JSON/hash。

保存核验须有任务活租约，并核对原调用 response_saved 及三个固定哈希；任务行锁内提交。相同版本恢复先读原检查点，不解析、不重调模型、不抓当前模板/策略/来源。新校验实现需新版本追加记录，不原地改旧结论。PG回执不确定保留对象并抛出检查点异常；确认拒绝或竞争失去的对象只在PG无引用查询后清理。终态可查询历史记录，不能新写核验或重领。

## 验收与边界

真实 MD/DOCX/PDF、12步JSON、两种适配器、本地实际HTTP、隔离MySQL/PG/RustFS：验证请求Schema、补读与继续、引用深层字段/负向值/原文位置、跨项目/未知身份/未读引用、错误JSON/重复键/归一位置、截断/空/工具回执、并发保存、丢失实际PG回执及新Store恢复、对象篡改与旧attempt/终态拒绝。保存响应后核验须保持HTTP次数不变；后续文档/用例变化不得替换固定依据。

核验组件不会启动自动协调或发布完整分析结果；实际默认模型判断、独立影响进程、生产API/前端/迁移和需求矩阵另验。本步不添加可编辑策略或新依赖，已有策略仍MySQL，业务记录PG，大正文RustFS。

## 提交阶段协议冻结的复核修正

首轮31项功能88.02秒通过后复核：如果准备每个请求时直接读取当前model_json_schema，代码升级会让同任务的不同单元改变协议。改为新任务seed冻结完整Schema、指令和hash；build_request校验快照并原样使用。旧seed协议None不补造，已保存调用及其显式未知重试继续按原请求恢复；新单元/新续读另建任务。原Input、旧调用对象/schema/hash不变；本轮另实际回放上轮MD/DOCX/PDF×两适配器6份已提交请求。新增边界随后进入最终相关回归。

原对象写入与未知提交处理提取为impact_checkpoint_io共享函数，原ImpactCallCheckpointUnavailable导入保留兼容；新响应组件使用公开read_fixed_scope，不访问调用服务的私有对象/提交方法。没有扩大为新的编排框架。

扩大回归首轮141项中138通过、3失败（284.92秒）。三失败来自旧提交测试对整个seed字符串搜索content_json：新响应Schema合法包含该字段名，被误当成用例正文。改为核对实际cases引用的字段集合/哈希，仍保留原件下载禁止钩子及后续变更后固定输入的实际断言；没有放宽产品核验。失败XML/log/业务证据保留，修正后另跑最终相关回归。

请求另提供固定用例/差异身份目录，模型可以据此提出补读；只有count不足以定位其他固定版本。目录身份不计作已读取内容，实际引用仍要求完整用例/原文进入该次请求历史。目录及正文都计入原字节/估算额度，超限拒绝，不截断为完成。

最终12模块141通过0跳过291.57秒，新专项40项（16项解析/历史请求、24项真实文件/HTTP/数据库/对象存储）。详见PLAN第六节、IMPLEMENTATION及p4-responses-final.xml/log/目录；独立规划CLI包含在回归中，impact仍无独立进程/默认模型/生产迁移或正式页面验收。
