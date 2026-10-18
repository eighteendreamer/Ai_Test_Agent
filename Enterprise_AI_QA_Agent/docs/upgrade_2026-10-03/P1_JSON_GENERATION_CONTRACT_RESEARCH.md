# JSON 用例生成请求契约：实际重复输出与回归

本轮基于 `7cc48164` 及工作区已有改动；完整 P0—P5 仍未完成。附件原方案已完整读取。范围是普通文档/模板生成输出契约，不更改执行模式、正式回执、模型传输协议或规划预算。

## 依据与根因

`p1-generation-default-profile-current` 是真实 API、解析/生成 CLI、MySQL、PostgreSQL、Redis、RustFS 和当前默认 Step 的隔离 profile 对照，非受控模型。首次模块 HTTP200，但 8192 completion tokens 后以 length 结束；首个公开文本出现在约101.804秒，最终2049字符。实际测试1失败168.44秒，模块 failed/queued/queued，无草稿。配置漂移、API重启、独立恢复、终态重投断言均未执行到，不能算已验收。

`original-1.json` SHA256 为 `454984fd88a8dab3b406940689d5454328d01b9259e711274d779f62f1d3ce3c`。首条完整用例同时包含外层 preconditions/steps/assertions/test_data/cleanup 和 content_json 内的完整业务内容，第二条输出中途截断。安全公开字段已提取到 `Agent_Server/tests/fixtures/default_generation_duplicate_truncated.json`，附来源和正文哈希；仅在回归测试中提取首个闭合对象做无损比较，生产不修补或接受截断响应。

代码依据：

- `generation_pipeline._build_request` 原来把 `_ModelGeneratedPayload.model_json_schema()` 同时给 Prompt 和 ModelOutputFormat；GeneratedTestCaseDraft 同时继承旧业务字段与可空 JSON。
- `template_service.default_generation_template` 已要求业务内容放入 content_json；默认示例已包含步骤、验证点和清理。
- `_CaseBusinessContent.business_content()` 在 JSON 非空时优先读取它；外层旧业务字段供历史兼容。
- `PreparedCaseGeneration` 固定实际 invocation/Prompt版本及SHA；invoke_frozen读取固定请求，不根据新代码重建历史请求。
- 本地课程“测试数据合成与用例自动生成”区分生成与验证，要求从真实失败出发；“线上流量闭环与评测集治理”要求脱敏、去重、保留上下文和判据。这里只采用与项目契约一致的原则，不把课程中的外部产品版本描述当已证实事实。

结论：生成请求与历史兼容 DTO 的职责混用，确实要求模型可输出两套业务结构。这是已确认的契约问题；它不能单独解释隐藏推理耗时，修复后仍需真实验收。

## 实施与兼容

新 JSON 作者请求从现有模型 Schema 做局部投影，移除五个外层旧业务字段及其不再使用的步骤/断言定义，content_json改为必填、非空对象，沿用原字段约束和任意嵌套 JSON。治理、执行说明及生成元数据保留；Prompt中的Schema与原ModelOutputFormat使用同一份结构，仍由既有provider适配器映射，不增加SDK/依赖/协议。

模板或增量上下文选择JSON契约并写结构化选择日志。新直接JSON生成Prompt为json-v3；新冻结JSON请求分别为document-v2/incremental-v2/frozen-v2。无JSON要求的旧格式生成继续使用原契约。旧存储的请求、Prompt版本、序列化、内容哈希及旧/重复字段读取不变；不删除公开API字段、不迁移旧记录。

真实模型对照可通过既有ModelRequestProfile选择隔离MySQL副本参数，记录原默认和实际调用指纹；全局MySQL配置必须不变。当前对照为low/json_object、8192 tokens/原120秒任务预算，不据此声称原默认profile已通过。不增加Token、期限、SDK重试，也不修改API地址。

## 验证记录

首轮命令：`..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_generation_json_output_contract.py tests/test_test_case_generation_pipeline.py tests/test_document_generation_stream.py tests/test_case_json_templates.py tests/test_model_request_profile.py -q --tb=short --junitxml ../docs/upgrade_2026-10-03/p1-json-output-contract-regression.xml`，62通过、3门控跳过，25.57秒。

实际失败样本证明：投影前后 business_content、治理及说明完全一致；自定义中文/嵌套/false/0/null保留；原截断结果在旧/新冻结请求均拒绝；旧scope逐字段和哈希验证通过，重发旧请求仍使用原invocation。首轮非实际MySQL项目页面验收，不能覆盖它。

增量来源、生成任务、失败响应保存及重试专项命令：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_incremental_generation_sources.py tests/test_generation_tasks.py tests/test_generation_retries.py -q --tb=short --junitxml ../docs/upgrade_2026-10-03/p1-json-contract-storage-regression.xml
```

实际 **43通过，0跳过，400.88秒**。这些生产API/CLI和MySQL/PG/RustFS/Redis隔离集成测试使用受控HTTP模型，证明新 frozen invocation 可被保存/读取，JSON字段投影未破坏增量来源冻结及原响应重试行为；不代表Step默认模型质量。

首次默认Step文档整体验收生成三个模块并独立重试成功，但在正式断言后因验收代码违反路由响应契约（将 `TestCaseRecord` 当成含 `case` 字段的包装对象）失败；按 `case_management.py` 的 `response_model=TestCaseRecord` 修正，仅改验收断言，原件保留。

新隔离项目相同请求/配置/预算重跑：`RUN_LIVE_DEFAULT_DOCUMENT_GENERATION=1`，使用用户已认证默认 Step 的**隔离 MySQL profile 副本**，low/json_object、8192输出tokens、600秒组deadline及原120秒单调用deadline，全局模型配置不改。运行命令同首轮并将新证据放到 `p1-generation-json-contract-current2`。实际 **1通过、0跳过、345.35秒**。Markdown 实际经过解析任务、API、独立 worker 和 MySQL/PostgreSQL/Redis/RustFS；原件、解析快照、scope、每次HTTP和公开响应均留存并按 RustFS 大小/SHA核验。三个模块全部有完整正式响应和草稿；中间人为隔离配置变化时仅对应模块在发送前被拒绝；恢复模型参数后API重启继续，失败模块通过新 frozen-request 重试完成。最终9个版本均有 `content_json`、解析来源、固定scope/module、模型/Prompt/Skill 元数据；草稿仍待评审且未执行业务测试。重复投递完成任务0额外 SDK 请求，其他断言实际 SDK调用共3，全局默认模型保持相等。

人工复核9条公开草稿而非仅检索关键词：创建模块覆盖登录后合法标题、空标题拒绝及写入必须认证，含 false/0/null 初始值；查询模块覆盖本人数据隔离、分页20条及超限、其他用户数据不泄露和空列表；注册模块覆盖首次唯一邮箱注册、重复拒绝和未登录负向。每条只使用 `content_json`，无旧外层业务重复。未提供的路径/方法/状态码/元素、注册首次写入与全局“写操作须登录”之间的业务解释均明确警告/待评审；未把草稿或任务测试写成已执行/已通过。

该真实默认模型文档闭环现在通过，局限是仍用隔离 `low/json_object` profile，而非全局原始profile；验证的是后端/持久化/worker/恢复链路，不是完整项目管理前端页面验收，也未自动判定业务实现。P1全部功能和P0—P5目标仍未完成。
