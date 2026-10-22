# P2 持久重规划：当前验收与交付边界

日期：2026-10-22。完整升级目标继续未完成。这里验收的是收到明确重规划请求后的候选生成、修订及恢复，不代表执行 Agent 已能自主发现缺口并触发请求。

## 调研结论与实现依据

重新读取用户指定方案、AGENTS.md，并沿 API、规划服务、任务存储、Worker、修订构造器和页面调用链核对：

- `RunRevisionPlanningService` 已把原 Run、固定目标版本、条目和结果封装为持久规划输入；复用现有 `CasePlanningWorkflow`，不另建规划器。
- `PlanningTaskHandler` 在候选完成或终态重投时调用 finalizer。候选与 Run 修订属于不同事务；必须先查询已提交历史，才能恢复丢失的确认。
- `build_run_plan_revision` 只更新尚未领取、无 Attempt/租约/结果的条目。已领取项继续使用原快照，不能通过给候选添加前置来修复正在执行的目标。
- 当前源代码没有从执行 Agent 图或工具路径提交重规划请求的调用点。这是仍需补实现的触发边界。
- 前端已有手工候选修订页面；本次同步重规划请求/结果类型、API 方法及任务确认状态，不宣称已提供自主触发或完整重规划提交页面。

存储沿用现有约定：MySQL 配置，PostgreSQL 任务/计划/Run/证据目录，RustFS 原件。新任务字段使用现有 JSONB；不新增生产表或模型协议，不修改全局默认模型。

测试设计参考本地《Agent轨迹与协议鲁棒性》《Agent测试专项》的固定依据、幂等及终态不重放要求。提交前边界复用 `test_case_plan_run_creation.py` 已验证的表锁与自有 backend 观察方法。提交后边界使用测试专属 PostgreSQL 触发器和事务咨询锁，只阻塞任务确认写入，返回 NEW 原值；依据为 [PostgreSQL Trigger Functions](https://www.postgresql.org/docs/current/plpgsql-trigger.html)、[CREATE TRIGGER](https://www.postgresql.org/docs/current/sql-createtrigger.html) 与 [Advisory Lock Functions](https://www.postgresql.org/docs/15/functions-admin.html)。触发器只存在于本测试隔离数据库。

## 实际场景

每个进程场景从五份实际 JSON 文件创建草稿、评审并启用。显式候选通过正式 SDK 和本地 HTTP 模型生成原计划；实际 API 创建 Run，领取两个条目，一个保存运行检查点，另一个通过完成 API 保存受控失败结果和证据。这个失败结果用于核对历史保留，不是实际业务测试结果。

随后创建并上传 Markdown，读回 RustFS 原件核对字节；实际 API 提交重规划任务。独立正式 CLI `src.cli.run_case_planning_worker` 读取 MySQL 配置、固定用例、文档及 PostgreSQL 任务，通过 SDK 发出两轮真实 HTTP 请求，生成补入前置的候选。

| 中断点 | 数据库确认的状态 | 恢复行为 |
| --- | --- | --- |
| `before_apply` | 候选完成，Run 修订 INSERT 被表锁阻塞；其他连接只见原 Run 与空修订历史 | 终止 CLI、中止精确观察到的自有 backend，再重启 API/CLI；复用候选提交修订 |
| `after_apply` | 修订 1 已提交；任务确认 UPDATE 被测试触发器阻塞，任务仍显示 pending | 终止 CLI、中止该自有 backend，再重启 API/CLI；读取已有修订，补保存确认 |

两个场景均核对：

- 修订后共5个条目；新增前置加入左目标依赖，原目标版本保留。
- 原两个 Attempt、运行检查点、原失败结果及证据、原 Suite 保留；既有条目允许展示位置和更新时间变化。
- 每个 Attempt 仍从归档读取领取时的原计划；当前 Run 保留两份快照。
- 只有1条修订、1个原运行派发意图、1个重规划 outbox 意图。
- 每场景共3次模型 HTTP：原计划1次、重规划2次。恢复及实际终态消息重投新增请求为0，CLI 均输出 `case_planning_processed=1`。
- 修订后真实 claim 只取到新增前置；尚未满足依赖的查询目标仍等待。

没有启动业务执行 Worker；不据此判断业务接口、准备动作、默认 AI 规划质量或完整 UI 执行已通过。

## 命令与结果

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_RUN_REVISION_PLANNING='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_revision_planning.py -q --tb=short --maxfail=1 --junitxml=docs/upgrade_2026-10-03/p2-revision-planning-current-20261022.xml
# 8 passed in 17.33s

$env:RUN_LIVE_RUN_REVISION_PROCESS='1'
$env:QA_RUN_REVISION_PROCESS_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p2-revision-process-current-20261022-03'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_revision_planning_process_live.py -q --tb=short --maxfail=1 --junitxml=docs/upgrade_2026-10-03/p2-revision-process-current-20261022-03.xml
# 2 passed in 78.39s

$env:RUN_LIVE_CASE_PLANNING_TASKS='1'
$env:RUN_LIVE_REGRESSION_PLANNING='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning_tasks.py tests/test_regression_planning_tasks.py tests/test_regression_planning_worker.py tests/test_planning_worker_runtime.py -q --tb=short --maxfail=1 --junitxml=docs/upgrade_2026-10-03/p2-revision-bridge-regression-current-20261022.xml
# 23 passed in 74.27s
```

在 `agent_web` 执行 `npm.cmd test -- src/features/projects/RunRevisionPlanningTaskPanel.test.ts src/services/projects.plan-revisions.api.test.ts src/features/projects/CasePlanningTaskPanel.test.ts`：13通过，2.41秒。`npm.cmd run build` 的 Vue 类型检查及 Vite 构建均退出0。

进程测试首轮1失败，21.47秒：原计划走了未配置嵌入模型的语义召回，未进入修订。按本项范围改为显式候选，并补充新前置用例查询后，第二轮2通过，77.91秒；随后新增原失败结果保留、快照/派发数量及 CLI 实际消费断言，最终轮2通过，78.39秒。原失败、此前通过与最终证据分别保留在 `p2-revision-process-current-20261022-01/`、`-02/`、`-03/`，不覆盖失败记录。

## 清理与交付

实际只读回查确认工作树三轮及暂存区复验共7个 Redis 容器、7个 PostgreSQL 专属数据库、35张 MySQL 测试配置表均不存在，6份上传原件返回结构化 `NoSuchKey`。首轮失败发生在上传前；不把它计为已验证对象删除。详见 `p2-revision-process-cleanup-current-20261022.json`。清理审计最初两次因 Docker 错误大小写匹配、应用层包装 S3 异常而退出；改用大小写无关匹配及既有底层 S3 API 后通过，没有执行额外清理。

提交范围为这条已有持久重规划链路、同步契约、测试及验收记录。分阶段规划、环境选择等同文件内其他既有未提交修改不混入。功能测试运行在当前工作树；不能据此声称整个工作树已提交或全升级已交付。原始请求、完整运行记录及日志保留本地，提交摘要及哈希，不提交包含测试租约的原始记录。

为验证拆分提交不依赖其他未提交源码，把暂存区树导出至临时目录，以原虚拟环境和隔离真实存储执行服务、进程及 Worker 启动测试：**14通过，112.63秒**（含上述8项服务、2项进程及4项启动测试；是复验，不累加为新功能数）。相关源码取自导出树，API/CLI 子进程也从导出目录启动；见 `p2-revision-staged-source-check-current-20261022.json` 和同前缀 JUnit。前端亦从相同暂存区导出，相关测试12通过；原工作树另有未纳入提交的环境选择测试，因此数量与工作树13项不同。导出检查不改变生产配置。

导出前端首轮构建退出2：只导出网页目录，漏掉测试引用的27份已跟踪文档 JSON。按暂存区原字节补入这些依赖后，完整 Vue 类型检查与 Vite 构建退出0；没有排除测试或修改源码规避错误。相关 stdout/stderr 和两次结果均保留，见 `p2-revision-staged-web-check-current-20261022.json`。

## 继续未完成

1. Agent 根据实际执行证据自主提出缺口并触发重规划；尚未领取部分与已领取目标的恢复语义须分开，不重置完成项或盲目重发未知写入。
2. 默认 AI 完整项目选取→排序→补全→执行，以及 UI 正式回执、知识使用及其他模式；本轮没有再次请求外部默认模型。
3. 重规划提交入口的完整产品页面、更多并发/断网组合和生产历史迁移。
4. 原目标所列 UI 人工录制、Docker 完整报告、需求变更业务质量、全阶段请求追踪及其他未验收范围。

Step 账号认证按用户确认记录为完成；此前最小实际请求返回无有效 Step Plan 订阅，不能写成未认证。Anthropic 保留 Messages 调用，其他 SDK transport 和字段映射保持独立；当前没有新证据支持统一为某一家协议。
