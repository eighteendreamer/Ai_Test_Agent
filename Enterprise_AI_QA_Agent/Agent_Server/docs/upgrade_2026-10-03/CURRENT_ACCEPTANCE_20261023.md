# 2026-10-23 当前升级验收进度

本记录依据当前工作树、MySQL 默认模型配置、PostgreSQL/RustFS 隔离验收和真实外部请求更新。完整升级目标仍未完成。

## Step 默认模型复验

用户已确认 Step 账号认证完成，本轮按该前提读取 MySQL 默认配置并执行真实多用例依赖场景。实际配置为：

- model：`step-5-preview`
- transport：`openai_chat_completions`
- base URL：Step Plan OpenAI-compatible Chat Completions 地址
- 全局默认模型身份：复验前后均为 `step-5-preview`

实际命令：

```powershell
$env:RUN_LIVE_DEFAULT_COVERAGE_PRODUCTION='1'
$env:QA_DEFAULT_CASE_SHAPE='multi-case-dependency'
$env:QA_DEFAULT_COVERAGE_DIR='Agent_Server/docs/upgrade_2026-10-03/p2-default-multi-case-step-authenticated-current-20261023-01'
..\\.venv\\Scripts\\python.exe -X utf8 -m pytest tests/test_default_coverage_production.py::test_actual_default_model_plans_and_executes_complete_case -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-default-multi-case-step-authenticated-current-20261023-01.xml
```

结果：`1 failed in 26.52s`。测试只完成了任务提交和首次规划调用，任务状态为 `failed`，没有创建 Run，没有业务 HTTP 请求，因此项目内用例选取、排序、补全、固定执行和结果回执尚未得到默认 Step 真实证据。

失败的物理请求返回 HTTP 400。随后使用同一 MySQL 配置做三个最小脱敏请求：非流式、流式、流式加 `stream_options.include_usage`，三者均返回：

```json
{"error":{"message":"you have no active step plan subscription","type":"request_params_invalid"}}
```

因此当前结论是：认证状态按用户说明记录为已完成；Step Plan entitlement/订阅仍不可用。不能把该结果写成认证失败，也不能据此修改模型协议、切换 Responses 或替换默认模型。测试临时 Redis、隔离 PostgreSQL、MySQL 测试表和 RustFS 工件均由夹具清理，`qa-execution-cli-*` 无残留。

## 已有实现且有实际验收证据

- 模板、项目模板、Excel 行式/分表式及 JSON 导入、预览、错误阻断、评审启用、项目隔离和重启恢复。
- 本轮对模板与导入做了当前工作树的产品复验：构建版 App/Playwright、独立 API、隔离 MySQL/PostgreSQL/RustFS/Redis；实际构造并导入 `rows.xlsx`、`split.xlsx`、`cases.json`、`invalid.xlsx`。覆盖项目模板保存、原件字节读回与下载、预览及错误报告下载、草稿幂等提交、评审/启用、JSON 新版本不改活动版本、编号冲突阻断、跨项目隔离及 API 重启后的数据读回。结果 **1 passed in 63.60s**；页面错误和非预期 API 错误均为0，项目隔离返回404，重启后数据仍可读。证据：`p1-case-import-full-product-current-resume-20261023-01/` 与对应 XML。该证据关闭了这些模板/导入页面流程的验收缺口，不包括未来项目平台数据源接入，也不代表普通需求文档生成闭环通过。
- 普通文档生成当前工作树产品复验通过：`test_ordinary_document_modules_and_independent_saved_response_recovery` 的流式/非流式两组在干净隔离证据目录 **2 passed in 394.38s**。使用真实 Markdown/DOCX/PDF 输入、隔离 MySQL/PostgreSQL/RustFS/Redis、生产 API/Worker 和构建版 App/Playwright，并通过本机受控 HTTP 模型验证；两个页面场景均实现3/3模块完成，提交成功但丢失回执后通过回查恢复且没有重复提交，完整 JSON 草稿查看/人工新版本修订通过，页面错误与网络错误均为0。模块级错误恢复场景保留 `[completed, failed, completed]`，失败模块从已保存响应独立重试完成；模板在请求提交后修改不改变固定生成范围，API 重启后仍可完成。DOCX 的3个模块及文本型 PDF 的1个模块均完成并校验来源映射。JUnit 与逐项证据：`Agent_Server/docs/upgrade_2026-10-03/p1-document-generation-full-product-current-20261023-02.xml` 及同名目录。该模型是本地受控模型，因此证明产品链路、存储、恢复和页面行为，不证明默认 Step 的生成语义质量；该缺口仍未验收。
- 执行中逻辑缺口触发持久 Run 重规划的受控闭环已验证：执行 Agent 收到只读业务响应后引用已结算 ToolJob 的精确 JSON 路径，追加正式前置到未领取条目并保留已领取 Attempt；状态查询可恢复丢失回执，终态重投不重复提交。当前工作树复验 `5 passed in 14.39s`。测试使用脚本化受控 Agent，不证明默认 Step 的推理质量或目标业务测试通过。
- 文档版本、上传原件、解析快照、确定性差异、影响分析和需求追踪专项。
- API 鉴权刷新、依赖传值、未知写入只读查证、进程恢复和报告脱敏专项。
- DeepSeek 真实页面的 Playwright 自动化录制、事件清洗、响应关联和知识投影专项；尚未验收 Electron/人工录制入口或默认 AI 对这份录制知识的实际使用。
- Docker 性能/安全工具的真实调用、原始输出、指标/发现解析、RustFS 证据和报告中心专项。
- Docker 安全报告中心在修复后的独立完整产品复验通过（任务统计 3/2/1、发现 4、原件下载、API 重启恢复和容器清理均有证据）；这仍不等同于 Agent 自主安全规划或默认 Step 驱动验收。
- 模型适配层的独立 transport：Anthropic Messages、OpenAI Chat Completions、OpenAI Responses、Google Gemini 及 LangChain 兼容路径。本轮核查确认 Anthropic 非流式和流式均调用 `client.messages.create`；OpenAI Responses 只由 `openai_responses` transport 选择。内部统一请求/结果契约与各协议客户端分层保留。本轮模型客户端、请求映射、完成状态、HTTP 故障和 embedding 路由回归 **184 passed in 124.23s**；这验证本地映射及错误处理，不证明每家供应商当前服务可用。
- 持久 Run 修订规划回归本轮 **16 passed in 25.37s**。这证明当前工作树的修订规划服务测试通过，不替代默认 Step 的真实模型规划和业务执行验收。

## 正式回执纠正与进程恢复复验

本轮在受控真实 HTTP 目标、隔离 PostgreSQL/MySQL/RustFS、真实 Redis Worker 和生产 API 进程上，分别在回执纠正的两个持久边界杀掉 API：

- `response`：纠正响应已写入 RustFS，恢复后 Run 结果为 `passed`；原始 POST/GET/DELETE 业务副作用各一次。
- `invoking`：纠正请求已写入 RustFS、响应尚未落盘，恢复后结果为 `error` 且保留 `execution_recovery_required=true`；不把未知状态误报为通过。

两种边界都等待原 Run lease 和资源 lease 自然过期，由生产恢复 Worker 接管；终态重投没有新增模型调用或业务请求，临时 Redis 容器、隔离数据库、对象和资源租约均清理完成。

实际命令分别为：

```powershell
$env:RUN_LIVE_EXECUTION_CLI_TESTS='1'
$env:QA_RECEIPT_PROCESS_DIR='Agent_Server/docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-response'
..\\.venv\\Scripts\\python.exe -X utf8 -m pytest 'tests/test_receipt_process_recovery_live.py::test_owned_api_kill_recovers_receipt_without_resending_or_repeating_business[response]' -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-response.xml
# 1 passed in 350.83s

$env:QA_RECEIPT_PROCESS_DIR='Agent_Server/docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-invoking'
..\\.venv\\Scripts\\python.exe -X utf8 -m pytest 'tests/test_receipt_process_recovery_live.py::test_owned_api_kill_recovers_receipt_without_resending_or_repeating_business[invoking]' -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-receipt-process-recovery-current-20261023-invoking.xml
# 1 passed in 356.93s
```

这证明的是当前工作区的回执纠正实现和恢复边界，不等同于默认 Step 模型真实调用成功；默认 Step 仍被供应商 `no active step plan subscription` 阻断在首次规划请求。

## 已实现但默认模型或产品闭环尚未验收

- 默认 AI 的“理解需求 → 当前项目选用例 → 排序 → 判断缺口 → 补正式用例/准备步骤 → 执行 → 结果证据”完整链路。
- 默认 Step 普通需求文档全量生成质量、默认 UI 录制知识使用、默认 Agent API 完整流程。
- UI 登录/聊天、iframe、跨文档、Shadow DOM、场景编辑、元素别名和失败定位的完整产品闭环。
- Agent 自主性能/安全规划、多级加压、TTFT/Token 吞吐和更多进程故障恢复。
- 需求变更更广业务质量、部署适用性、历史迁移和更多格式/故障组合。

## 仍需补实现或复现后才能确认的范围

不能把“未验收”直接判定为“缺实现”。下一阶段先以当前真实失败样本定位：Step 订阅恢复后重跑同一 P2 场景；若规划进入下一阶段，再根据真实轨迹判断是模型输出、协议字段映射、规划校验还是执行器缺陷，再决定是否改代码。其他模块继续保持现有模型适配和项目/数据隔离边界。
