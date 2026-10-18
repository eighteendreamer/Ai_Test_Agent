# 普通需求全量生成：默认模型、传输边界与独立恢复

基线1a59d832。完整P0—P5仍未完成；本批不修改执行模式、正式回执校验或规划预算。

## 已有实现及依据

已读document_generation_sources、generation_pipeline、generation_task_service/config、PostgresGenerationTaskStore、生产路由、生成CLI以及既有普通文档生产/页面验收。普通文档不需要伪造需求变更评审：解析完整快照→冻结完整原文/模板/模型/Skill→按不重叠章节建模块→独立领取→保存响应→草稿和receipt同一PG事务。MySQL保存编辑策略和模型；RustFS保存原文/范围/响应；PG保存组、任务、失败、版本及重试引用。既有p1-document-generation-product是受控HTTP模型；不能据此声称默认Step质量通过。

本地课程“测试数据合成与用例自动生成”和“线上流量闭环与评测集治理”要求来源、版本、去重与人工质量治理。本批检验冻结依据及实际模块结果，章节范围完整不等于业务验证点全部覆盖；生成草稿也不等于测试执行通过。

新验收文档为注册、任务创建和任务查询三个模块，包含唯一性、未登录拒绝、空标题、false/0/null默认值、仅查询本人任务和每页20条边界；未提供路径/状态码/元素，不允许生成时猜测。模型采用实际当前默认配置，不替换响应，不修改全局配置或增加8192/120秒。参数变化故障只在隔离MySQL发生；它不是进程中断或供应商故障。

## 第一批真实结果与传输问题

首轮夹具错误：models.upsert返回ModelConfigPublic，参数指纹需要完整ModelConfigRecord；改为读取实际保存配置。7.97秒失败、SDK0，原件不覆盖，不当生产Bug。

修正后p1-generation-default-current-verified 1失败146.88秒。第一模块failed/call_outcome_unknown，其余两模块仍queued，草稿0。1次SDK create对应2个物理请求，字节SHA相同、间隔约60秒，SDK最终CancelledError/119.278秒；没有观察到响应头或HTTP状态。旧探针只记录每次发送及SDK最终错误，没有保存每次send的异常类型，因此“ReadTimeout”只可作为由60秒默认SDK超时/重试实现和时间间隔推断的原因，不能伪称该异常已直接抓到。

代码依据：ModelRuntimeService._send_with_client只有存在stream_handler时走原provider client.stream；生成Worker调用invoke_frozen没有handler，所以明明模型声明streaming能力，实际请求仍非流式。原生成任务在120秒取消并保留invoking，不自动重发。等待完整非流式响应时，SDK可能在中途重复发送，给外部生成重复成本并丢失原结果。

## 根因改动及兼容边界

新普通文档请求沿现有ModelRuntimeService.stream_handler；由冻结的MySQL模型capabilities.streaming选择，全部provider仍走既有client/适配器。不添加第二框架、Step特判、输出推理字段或新的测试模式。收到完整终态后才保存响应/解析/提交草稿；部分文本不入草稿，超时未知仍保留。

PreparedCaseGeneration新增可空response_transport_protocol=case-generation-stream-response-v1。它是PG/RustFS冻结行为身份，随原哈希保存，不是新编辑配置。旧请求缺字段时序列化仍省略，哈希和原非流式行为不变；非流式能力模型及增量请求保持原入口。模型参数或runtime变化仍在原preflight拒绝，不偷偷改供应商、预算或默认profile。

新流式日志仅计公开文本chunk和字符数，不保存私有推理。测试SDK探针另存每次send的响应头/错误类型/耗时，转发真实响应、不读body、不保存头部或凭据；旧失败探针事实保持不补造。

## 验收要求

用真实API、独立解析/生成CLI、隔离MySQL/PG/RustFS/Redis和当前默认Step核验三个模块、配置漂移不发送、原失败不可覆盖、单模块新重试、API重启、草稿版本/来源/模型/Prompt/Skill、终态重投无额外发送及项目404。保存实际输出再逐条评审业务内容；不能用包含关键词作为完整质量证明。

不同供应商真实质量、完整App生产部署、长文档、跨模块充分性、真实断网/kill组合、普通文档全部业务覆盖及其他完整P0—P5保持待办。第一批未知任务不重置、不再发送；修复后验收必须创建新隔离任务。

## 修复后真实结果与交付范围

p1-generation-default-stream仍1失败147.60秒，但此次仅1次SDK/1次物理HTTP200；3.689秒出现首个流式chunk，原119秒附近收到length终态。3611个chunk、completion_tokens=8192，公开content字符0，私有推理只计数不保存正文。任务在response_saved后以response_validation_rejected失败，其余模块queued；原未知任务未重发。保存并下载RustFS scope/公开响应2份，大小和SHA256一致，原件公开text为空。不是字段映射遗漏：当前公开字段确实没有正文，不能把reasoning当作测试用例。

旧任务/实际增量存储及恢复48通过288.87秒，涉及md/docx/pdf、配置预算、原件篡改、结果未知不重发、草稿原子提交和单模块新重试；受控响应仅验证契约。新普通文档生产专项2通过136.79秒，分别streaming=false/true：真实API/解析与生成CLI/MySQL/PG/RustFS/Redis，三模块completed/failed/completed，过大响应先存原件后拒绝，新任务使用saved_response恢复成功，HTTP仍3次；API重启、模板变化冻结、false/0/null、项目隔离及终态重投无新发送。已有完整App专项不在本批重复运行，不能声称本批App或默认业务质量通过。

本地真实native SDK超时实验使用等待响应头的HTTP服务，1 SDK对应2次物理发送，捕获两个ReadTimeout和最终APITimeoutError；两个body字节完全相同。它验证新增每次attempt探针，不能倒填前批未直接捕获的错误。最终合同/实际SDK7通过6.80秒；初次适配器扩大回归60通过15门控跳过14.50秒，两个本轮测试夹具serialization警告随后修正，最终7项及48项无该警告。各批有重叠，不能相加当成功功能数量。

本批新增传输行为身份与探针，不更改全局MySQL模型、输出Token/期限/SDK重试，也不新增依赖、表或迁移；前端不消费内部PreparedCaseGeneration新字段，未改前端、未运行本批构建。compileall与SHA审计另见交付记录。完整默认普通文档生成尚未通过，后续须从实际生成职责和模型能力复核，停止相同任务/参数盲重跑或扩大预算；跨模块充分性、独立恢复默认质量、更多格式故障和完整P0—P5保持未完成。

## 2026-10-19 默认 Step 普通文档生产复验

在确认 Step 账号已认证后，使用新的隔离目录 `p1-generation-default-current-20261019` 执行 `tests/test_document_generation_default.py`。结果为 **1 通过、0 失败、0 跳过，285.94 秒**。本次没有替换模型、修改全局配置或使用受控响应：实际配置为 `step-5-preview`、`openai_chat_completions`、流式能力开启；隔离结束后全局默认模型参数一致。

这次实际走过 Markdown 原件上传、解析快照、范围冻结、三模块 Worker、配置漂移拒绝、API 重启、单模块新重试、RustFS 响应读取、PG 版本读取和终态重投。模块顺序为：注册完成、任务创建因故意配置漂移失败后以 `frozen_request` 新任务恢复完成、任务查询完成；共保存 9 个草稿版本，实际 SDK 调用 3 次，终态重投没有新增调用。物理请求均收到 200，流式 SDK 迭代正常结束并返回 `stop`；每个草稿都有独立 `content_json`、执行说明、文档解析快照来源、模型/Prompt/Skill 元数据和可读响应。

保存的真实内容覆盖了本样本文档的关键事实：唯一邮箱首次注册、重复邮箱拒绝、未登录写入拒绝、合法任务创建的 `done=false`/`retry_count=0`/`deleted_at=null`、空标题拒绝、仅返回本人任务、每页 20 条上限及空结果合法性。测试没有把关键词存在当作全部业务质量证明；长文档、更多格式、复杂跨模块规则、用户业务评审、正式部署和其他供应商仍需单独验收。

因此，本轮把“普通 Markdown 默认 Step 的三模块生成、模块进度和独立恢复”从“尚未验收”更新为**已实际验收**；“普通文档全量生成”的跨格式、长文档、业务充分性和完整项目页面仍属于待验收，不能据此宣称 P1 或完整 P0—P5 全部完成。
