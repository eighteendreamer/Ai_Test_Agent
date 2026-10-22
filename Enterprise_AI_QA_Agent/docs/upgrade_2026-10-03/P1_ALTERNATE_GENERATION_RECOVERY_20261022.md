# P1 真实普通文档生成与独立恢复验收

核验日期：2026-10-22。原目标保持完整，本记录仅关闭 Agnes 两模块普通 Markdown 生成、配置故障独立恢复和终态重投的技术验收。生成语义质量仍未通过，默认 Step 和完整 P0—P5 未完成。

## 依据与实施边界

用户附件方案要求“文档快照 → 按模块生成 → 草稿 → 评审”，失败模块独立重试，不重写历史依据。本地课程 `项目借鉴/llm-testing-course/测试数据合成与用例自动生成.html`、`线上流量闭环与评测集治理.html`、`评测工程化与CICD.html` 要求保留来源、版本及可复现失败样本，区分结构验证和语义质量。

代码核对：

- `GenerationTaskService._preflight` 对照冻结模型参数；参数变化在发送前失败。
- `generation_retry_strategy` 为没有发送检查点的已知失败选择 `frozen_request`；已有响应使用 `saved_response`，未知发送不自动重发。
- `GenerationTaskService.retry` 创建关联原失败任务的新任务，保留冻结模块、Prompt、Skill、模板和解析快照。
- `parse_frozen` 校验调用终态、JSON 和覆盖引用结构。`generation_review.mapping_state=complete` 不代表语义正确，`review_state` 仍为 `required`。

因此本轮扩展已有 `Agent_Server/tests/test_document_generation_alternate_live.py`，没有改生产策略、请求协议、Prompt、表结构或依赖。原测试被仓库忽略规则排除，本次加入精确白名单，使该实际验收源码可以随证据提交。

## 实际输入与结果

创建普通 Markdown 原件，包含订单查询和订单创建两个章节。查询规则为只返回当前用户订单、不存在时明确报错且不写入；创建规则为登录用户创建自己的订单、标题必填、空标题拒绝、不新增订单、成功返回系统编号。原件 SHA-256 为 `741450626e205789f25d68c736e800f79cc48244c592122f4aee0f867c750857`。

模型从 MySQL 读取已配置的 `agnes-2.5-flash`，复制到本次隔离配置表，仅该副本使用4096输出上限和单次传输策略。两次测试均不改变全局默认 Step 或全局 Agnes 配置。项目、文档、草稿、任务和回执存入隔离 PostgreSQL；原件、解析及生成对象存入现有 RustFS；临时 Redis 只用于 API/CLI Worker 队列。

| 验收项 | 实际证据 | 结果与范围 |
|---|---|---|
| 普通两模块生成基线 | `p1-alternate-document-generation-current-20261022-01.xml`、同名目录 `verification.json` | 1 passed，124.15秒；2份草稿、2次SDK请求、终态重投零新增调用 |
| 成功模块不重做 | 恢复目录 `first-module-before-recovery.json` 与 `draft-version-0.json` | 第一模块版本逐字段不变；重启与恢复后仍只有原版本 |
| 配置变化在发送前失败 | `generation-configuration-drift.json` | 第二模块 `input_budget_or_configuration_rejected`，无调用检查点；SDK总数仍为1 |
| API 重启、失败模块独立恢复 | `generation-retry.json`、`verification.json` | 恢复原副本配置后重启API；同一提交读回原组；新重试任务成功、来源仍为原固定模块 |
| 原失败保留、幂等与终态重投 | `generation-result.json`、`PG_case_gen_tasks.json`、`PG_case_gen_retries.json` | 原组仍为 completed/failed；1条重试关系、共3个任务；完成与失败任务重投均不新增SDK/HTTP |
| 真实外部传输 | `physical/*.sdk-create.json`、`*.send.json`、`*.sdk-stream.json` | 共2次SDK调用，各1次物理请求，HTTP 200，流终态stop；只保存公开正文和私有字段计数 |
| 对象与数据库核对 | `object-0/1/2.json`、`fixed-scope.json`、`PG_case_gen_*.json` | 对象字节SHA/大小与回执一致；导出组、3任务检查点和重试关系与API读回一致 |

独立恢复证据目录为 `p1-alternate-document-recovery-current-20261022-01/`，JUnit 为同名 `.xml`。真实测试结果 **1 passed，138.54秒**。所有版本保持 draft，未评审启用。

## 语义审阅：仍不通过

基线查询草稿此次确实写出了“指定不存在订单编号 → 明确错误 → 再查询确认无副作用”，修正了旧样本将空列表混同不存在订单的情况。但它仍未设置其他用户订单作为隔离验证的对照，不能凭“只查询自身订单”证明数据不会泄露。

恢复样本的质量问题更明确：

- 查询草稿增加 `response.order.user_id`、`response.error_code`、`database.order_count` 等原文没有的字段路径；未提供项目接口/数据库契约，不能作为正式断言依据。
- “不存在的ID（如999999999）”没有实际不存在证据；行数本身也不能证明现有记录未被修改。
- 查询仍未包含他人订单对照；创建草稿的步骤3要求列表仅包含本次订单，而前置只说没有“正在创建中的订单”，并未排除历史订单。这会误判正常系统。
- 来源文本没有规定接口字段名 `title`、数据库表或额外错误返回字段。自然语言上的合理准备建议不能被记作来源已经确认的事实。

两份覆盖映射仍可显示 complete，证明它只检查引用落到已有预期路径，并不验证预期真实、充分或与来源一致。初步审阅见两目录的 `quality-review.json`；这是本次代码/证据审阅，不冒充用户业务评审。

后续应以这些实际草稿作为质量失败样本，审查生成、证据引用与人工评审的职责，明确未覆盖与未确认假设的展示和修改流程。维持 §7.140 已确定的架构审查方向；不继续叠加针对订单的 Prompt 特例，不把引用完整当自动启用条件，不把全部业务 JSON 收紧成固定字段。

## 运行命令与清理

在 `Agent_Server` 目录运行扩展后的测试：

```powershell
$env:RUN_LIVE_ALTERNATE_DOCUMENT_GENERATION='1'
$env:QA_ALTERNATE_DOCUMENT_GENERATION_MODEL_NAME='agnes-2.5-flash'
# 目录须为新的、尚不存在的验收目录
$env:QA_ALTERNATE_DOCUMENT_GENERATION_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p1-alternate-document-recovery-current-20261022-01'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_generation_alternate_live.py -q --tb=short --junitxml=..\docs\upgrade_2026-10-03\p1-alternate-document-recovery-current-20261022-01.xml
```

测试收集通过，`py_compile` 通过；没有前端或生产代码变更，本轮不重跑前端构建。基线是扩展前脚本的实际运行，不能用扩展后脚本冒充原基线命令的逐行复现。

基线临时 Redis 为 `qa-execution-cli-afdbdd6f3063445f`；恢复测试为 `qa-execution-cli-3a442391c090458a`。两者均只绑定本机随机端口，`docker run/logs/rm` 均退出0，当前 `docker ps -a` 无该前缀容器。夹具正常清理后另做只读回查：两个专属PG库都不存在，各5张MySQL测试表都不存在，各5个引用RustFS对象均返回NoSuchKey。本地证据保留，不需要恢复这些隔离测试数据。明细见 `p1-document-generation-cleanup-current-20261022.json`。

第一次独立清理审计脚本错误地捕获原生 `ClientError`，实际存储服务会包装为 `RuntimeError`，所以该审计脚本退出1；这不是清理失败。按当前 `_read_object` 实现改为调用相同底层客户端只读查询后，全部回查通过。没有为此修改生产错误处理。

## 当前未完成项

这次没有验收浏览器产品页、DOCX/PDF真实外部模型、生成过程kill/断网/未知结果恢复、默认Step全量生成或生成草稿的业务执行。以上不能由本次配置故障恢复替代。Step认证按用户确认已完成；最近默认推理记录仍是HTTP 400、`you have no active step plan subscription`，本轮未重发相同探针。

按用例多候选规划与执行、UI正式回执、人手录制DeepSeek、其余API/性能/安全/需求追踪和历史迁移继续按原目标推进。模型适配沿用现有内部DTO和各自原生transport；本轮没有更改Anthropic Messages或OpenAI Responses的边界。
