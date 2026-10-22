# 2026-10-22 当前验收进度：P1 产品链路、P2 持久重规划与 P3 未知写入恢复

完整升级目标尚未完成。本记录依据当前工作树实际执行结果更新，不把旧通过记录、受控模型或底层专项等同于默认 AI 完整验收。

## 本轮调研与范围

- 重新读取用户指定的 `pasted-text-1.txt`、仓库 `AGENTS.md`，核对生成模板仅参与生成、执行读取固定用例的约束。
- 阅读本地课程《测试数据合成与用例自动生成》《评测工程化与CICD》《Agent轨迹与协议鲁棒性》中来源、版本、轨迹与重复副作用的章节；只采用与当前实现和实验一致的原则，不照搬课程中的产品版本或统计数字。
- 对照模板服务、导入服务/存储、容器注册、项目页面与现有产品验收脚本：配置走 MySQL；用例、版本、导入来源/批次、任务与会话走 PostgreSQL；文件原件与派生工件走现有 RustFS。未改全局模型或生产数据。
- 本轮未新确认必须修改的产品代码缺陷；已有实现通过实测的部分转为“指定范围验收通过”。未完成的产品范围继续保留，不以“未验收”推断“没有实现”。

## P1：模板与 Excel/JSON 导入

实现依据：`template_service.py` 使用 `ApplicationConfigStore`；`containers.py` 将其绑定为 `MySQLApplicationConfigStore`，导入目录绑定 `PostgresCaseImportStore`；`ProjectsView.vue`、`TestCaseTemplateEditor.vue`、`CaseImportDialog.vue` 提供产品入口。

实际命令（`Agent_Server`）：

```powershell
$env:RUN_LIVE_CASE_IMPORT_FULL_PRODUCT='1'
$env:QA_CASE_IMPORT_FULL_PRODUCT_DIR='docs/upgrade_2026-10-03/p1-case-import-current-audit-20261022-215248'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_import_full_product.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p1-case-import-current-audit.xml
# 1 passed in 72.95s
```

浏览器运行实际 App、路由和构建产物，API 为独立生产进程。验收在项目页面创建两项目，读取默认模板、保存项目模板，并核对 MySQL 配置。实际创建与上传每行一条用例 Excel、用例/步骤分表 Excel、完整 JSON 和含错误行的 Excel。原件及错误报告均通过页面下载后比对；原件哈希全部一致。

三种有效格式提交后，PostgreSQL 中有3条用例、4个版本、4条来源和5个批次；无效文件可预览但不能提交。相同批次提交幂等，编号冲突不覆盖旧用例；JSON 的嵌套字段、`false`、`0`、`null` 保留。页面评审/启用、保存新草稿、修改模板、API 重启后读取均通过；旧启用指针与历史版本不被模板更新改写。跨项目读取来源返回404；页面异常与非预期 API 错误均为0。

`CaseImportDialog.test.ts` 另有5项 Vitest 测试通过，覆盖列映射、预览失效、错误/冲突禁提和提交响应丢失后回查；该单元测试不代替上述浏览器验收。

## P1：普通文档模块生成、页面与独立恢复

实现依据：`document_generation_sources.py` 以固定解析快照划分范围；生成服务固定模板和请求，独立 CLI 推进模块；产品页面提供模块进度、固定任务详情、原响应及草稿修订。这里只验证工程链路，业务覆盖质量另行评审。

实际命令（`Agent_Server`）：

```powershell
$env:RUN_LIVE_DOCUMENT_GENERATION='1'
$env:RUN_LIVE_DOCUMENT_GENERATION_PRODUCT='1'
$env:RUN_LIVE_DOCUMENT_GENERATION_FORMATS='1'
$env:QA_DOCUMENT_GENERATION_EVIDENCE_DIR='docs/upgrade_2026-10-03/p1-document-generation-current-audit-20261022-215457'
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_generation_production.py::test_ordinary_document_modules_and_independent_saved_response_recovery[True]' -q --tb=short --junitxml=docs/upgrade_2026-10-03/p1-document-generation-current-audit.xml
# 1 passed in 153.14s
```

实际创建商城 Markdown（注册、订单、查询及全局规则）、带表格的 DOCX、文本 PDF，上传原件，由独立解析进程生成快照，再由独立生成 CLI 发出真实 HTTP 请求至受控模型服务。Markdown 首轮为“完成/失败/完成”，失败模块保留原始长响应，以 `saved_response` 策略独立恢复完成；恢复与终态重投没有额外模型请求。更改实时模板及配置不替换已冻结依据，API 重启后仍读取原任务。

实际 App 页面故意丢失提交响应，刷新后通过原请求身份回查；模块进度从0/3更新为3/3。固定详情读取完整草稿与原响应，人工编辑生成新版本，保留原版本和固定任务引用。DOCX 的3模块、PDF 的1模块均完成；源范围逐字符拼接一致，解析表格进入请求，PDF 结构能力限制显式保留；对象工件哈希校验通过。页面异常为0。

此轮只选择流式参数组合；非流式组合没有在本轮重跑。受控返回中有来源规则映射，因此不能据此声称任意真实模型已具备充分业务覆盖。

历史备选模型记录 `p1-alternate-document-generation-formats-live-20261022-03` 的 JUnit 为1通过，模型为 `agnes-2.5-flash`。它记录 Markdown 两模块、API 重启、固定请求独立恢复、DOCX/PDF 生成和检查通过，原失败与成功兄弟版本保留，终态重投额外 SDK 调用为0。本轮只复核该证据，没有重跑备选模型，也没有将其当作默认 Step 通过。

## P3：未知写入结果的真实进程恢复

实现依据：`executor.py` 为超时写入保留 `unknown`；`task_pool.py` 记录显式只读查证，保留原写入失败结果；`subagent_coordinator.py` 从持久化 Worker 会话恢复。本轮复用已有 `test_api_unknown_write_recovery_live.py`，不新增第二套恢复机制。

```powershell
# Agent_Server
$env:RUN_LIVE_API_UNKNOWN_WRITE_RECOVERY='1'
$env:QA_API_UNKNOWN_WRITE_RECOVERY_DIR='docs/upgrade_2026-10-03/p3-unknown-write-current-audit-20261022-215854'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_api_unknown_write_recovery_live.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p3-unknown-write-current-audit.xml
# 2 passed in 42.92s
```

在合并 Worker 结果前、保存写入失败后两处分别强制终止测试协调子进程；新进程从实际 PostgreSQL 恢复。目标端先提交、后延迟响应，客户端实际超时；业务表不设业务键唯一约束，不能借目标幂等掩盖重发。两场景都只有一次 POST 和一次 GET，查证获得同一资源ID，业务表均只有1条；原 ToolJob 未改变，终态重投新增请求为0。查证状态为 `effect_present`，原写请求仍是 `failed/unknown`，依赖写请求未执行。

Worker 启动器受控，没有外部模型。此证据不覆盖默认 AI 的查证决策、正式回执纠正、独立 Agent Worker 进程或完整页面流程。每场景12张临时表在清理后复查不存在，未使用 Docker。

## P2 追加：持久重规划的实际 CLI 中断恢复

`test_run_revision_planning.py` 实际8通过（17.33秒）；新增 `test_run_revision_planning_process_live.py` 的最终轮2通过（78.39秒）。后者使用正式 API、独立规划 CLI、MySQL/PostgreSQL/RustFS/Redis 和受控 HTTP 模型，分别在 Run 修订提交前、提交后任务确认前强制终止进程，再重启 API/CLI。最终只有1条修订，原 Attempt、检查点、失败结果及证据、Suite 和领取时快照保留；实际终态重投没有新增模型请求。

原计划用显式候选通过真实 SDK 生成；重规划查询新上传 Markdown 和项目用例后补入前置。每场景共3次模型HTTP（原计划1、重规划2），业务执行未启动。此证据不覆盖自主触发、语义召回、外部默认 AI 或完整重规划提交页面。

普通/回归规划及 Worker 回归23通过；工作树前端任务/API测试13通过，Vue类型检查和构建通过。另从暂存区导出源码独立复验，后端14通过（包含服务/进程/启动测试），前端相关测试12通过。工作树及暂存区复验的7个测试容器/PG库、35张MySQL表和6份上传原件已独立回查清理。范围、失败记录、命令和证据见 [P2持久重规划验收](P2_DURABLE_REPLANNING_CURRENT_20261022.md)。

## 尚未完成项的当前分类

| 方向 | 已有实现与本轮证据 | 仍待验收或调研 |
| --- | --- | --- |
| 模板、导入、生成 | 默认/项目模板、两种 Excel 与 JSON 完整页面验收通过；普通文档模块进度、已存响应恢复及多格式工程链路通过 | 默认 Step 普通文档生成及更广业务覆盖质量、用户业务评审 |
| 按用例执行 | 现有项目内选取、固定版本、逻辑依赖和效果证据实现；明确请求后的持久重规划及两个实际进程提交边界恢复通过 | Agent 自主发现缺口并触发修订仍需补实现；默认 AI 完整选取→补全→执行、完整产品流程仍未通过；默认 Step 最近实际诊断为无活动订阅 |
| 正式回执与恢复 | 有工作树实现与已有专项证据；P3未知写入恢复已通过 | 回执纠正阶段与默认 AI UI 的完整正式通过、未提交代码的独立交付核对；不能用本轮 P3 替代 |
| UI 录制与知识 | 既有采集/知识/场景实现，本轮未扩验 | DeepSeek 人工操作全链路、清洗遗漏、跨文档/Shadow DOM关联、默认 AI 使用知识并断言、失败定位与报告 |
| Agent API | 文档实际存储/依赖专项、未知写入真实子进程恢复通过 | 默认 Agent 鉴权/依赖/异常/幂等/报告的完整流程与独立 Worker 进程 |
| Docker性能、安全 | 已有工具及报告历史证据，本轮没有重新验收 | 保持未关闭，后续按实际工件与前端报告复核范围 |
| 需求变更与追踪 | 已有上传、解析、差异、评审及增量链路，本轮未扩验 | 更广默认模型质量、业务/环境适用性、更多格式与故障组合 |
| 请求追踪与工程交付 | 已审查独立 SDK transport 和字段映射；本轮91项模型客户端/请求配置/完成契约测试通过 | 全阶段发送追踪、更多断网/进程恢复、生产迁移与投影、两份旧 Worker 原件及大量既有未提交改动 |

具体产品缺陷或缺实现须在后续调研/复现后确定；上述待验收范围不能直接改名为“待开发”。模型仍保留 Anthropic Messages、OpenAI Chat Completions、OpenAI Responses、Google 的独立传输；本轮不改协议、依赖和 MySQL 默认项。Step 账号按用户确认为已认证，后续 API 订阅错误与认证状态分别记录，详见 `p2-p3-current-verification-20261022.md`。

## 工件与交付边界

`p1-p3-current-audit-evidence-20261022.json` 保存本轮结果摘要、关键源码和原始证据文件的 SHA-256。对应三个 JUnit 文件记录4项生产专项全部通过；Vitest 5项和模型91项为单独范围，不合并夸大为生产场景数。构建与前端类型检查由两项产品测试实际执行，退出码均为0。

P1两项验收使用各自临时 Redis 容器、隔离 PostgreSQL 数据库及 MySQL 配置表；夹具正常完成清理。随后 `docker ps -a --filter name=qa-execution-cli-` 没有残留容器。原始截图、日志和工件保留本地证据目录；提交仅选择相关测试/记录，不夹带无关改动、凭据或原始日志。未部署、未推送、未创建 PR；完整目标保持未完成。
