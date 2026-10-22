# P2 Run 计划修订：实际页面、API 进程与持久化恢复

日期：2026-10-22。完整 P0—P5 继续进行。本轮只关闭人工 Run 修订的页面到生产 API、提交边界恢复验收缺口，不代表 Agent 自主修订或默认模型完整执行通过。

## 调研依据与采用做法

| 依据 | 本轮采用做法 |
|---|---|
| 用户确认的方案：新缺口可修订尚未执行部分，保留原计划和已完成证据 | 实测增加、调整、停用未领取条目，同时保留已领取项及检查点 |
| `plan_run_service.py`、`plan_revision.py`、`run_store.py` | 复用已保存 CasePlan、冻结版本校验、事务修订、CAS、幂等键与快照归档，不新建修订引擎 |
| `RunPlanRevisionPanel.vue`、`ProjectsView.vue` | 使用真实路由和已构建 App；核验待确认请求保存、原键重试、历史确认、条目与进度刷新 |
| `test_case_plan_run_creation.py` 的 writer 进程中断验收 | 将原来的 writer/ASGI 测试扩展为独立 Uvicorn 进程、HTTP、浏览器提交和服务重启 |
| `test_case_version_reviews_product.py`、`test_document_full_product.py` | 复用隔离运行环境和构建版浏览器夹具；生产路由、服务和数据库存储不打补丁 |
| 本地课程 `Agent轨迹与协议鲁棒性.html`、`评测工程化与CICD.html` | 同时核验轨迹、幂等、半成功恢复和固定版本；仅返回成功状态不足以验收 |

新增 `Agent_Server/tests/test_run_plan_revision_product.py`。本轮没有修改生产逻辑、公共契约、数据库 schema、生成/执行 Prompt 或模型适配器。`.gitignore` 仅为新增测试加白名单，原有未提交改动保留。

## 实际输入与路径

每个场景写入并读取 5 份 JSON 文件，经实际 API 创建草稿、提交评审并启用固定版本。文件、完整版本和输入计划保存在证据目录。用生产 CasePlan 模型、实际 MySQL 规划配置快照及 PostgreSQL PlanStore 保存两份受控计划，再从生产 API 重读，并通过正式启动端点创建同一 Run。

原计划含前置、两个共享准备消费者、独立目标及清理；候选改为保留前置和独立项、修改未领取目标依赖、停用另一未领取目标、增加独立检查。修订前经实际 API 领取前置及独立项，并为前置保存运行状态和检查点。

候选内容由测试明确给定，不由 AI 生成。这使测试定位于修订和恢复的产品功能；没有使用受控模型替代默认模型质量验收。隔离模型配置只供 API 启动，断言实际模型请求与业务 HTTP 请求均为 0。

### 提交前中断

1. 浏览器在运行详情填写已保存的候选编号和原因，提交正式修订请求。
2. 对本测试专属 revision 表加 SHARE 锁，通过 PostgreSQL 活动会话和阻塞关系确认 API writer 已阻塞于最后的 history INSERT；未提交 Run/Item 投影对其他连接不可见。
3. 终止实际 Uvicorn 进程；精确终止仍等待表锁的自有 PostgreSQL backend，确认其退出，再释放测试锁。
4. 重启完整 API，确认原条目、Attempt、计划快照及空修订历史均保留。页面重新打开后从 sessionStorage 恢复同一请求，用完全相同的 key 和内容提交成功。

Windows 上终止 TCP 客户端不会立即唤醒等待 heavyweight lock 的 PG backend，这一事实沿用之前的实验结论。本场景验证的是 API 中断加上精确的数据库事务中止；不把 API 退出等同于数据库自动回滚。最终轮原请求在 Vite 代理侧返回 500、正文为空，已完整记录状态、正文和代理日志；浏览器断开为主动注入的故障。

### 提交后丢失响应

浏览器拦截器把请求真实发送到 API，取得 HTTP 200 后向页面注入响应丢失；页面保存待确认请求并锁定输入。终止和重启 Uvicorn 后，页面重开从实际修订历史确认同一幂等键，清除本地待确认记录并刷新到修订 1。直接重放原请求只返回该修订。

### 两个场景共同确认

- 当前快照为候选计划；已领取项及其 Attempt、原快照身份、检查点和首次 Suite 不变。
- 新增 1 项、更新 1 项、停用 1 项；共 5 条 RunItem，含 1 条取消历史；页面显示一致。
- 当前事务只保存 1 条修订历史、2 份计划快照、1 个原始 Run dispatch 意图；重放不增加这些记录。
- 同 key 修改原因、使用过期 revision 分别收到 409；没有部分写入。
- 修订后的实际 claim 只领取新独立项；仍等待前置的目标不可提前领取。
- 浏览器 JavaScript 错误为 0。没有启动执行 Worker，Result 数为 0；本轮不验证业务工具执行、终态或 Redis reclaim。已有 Redis/Worker 专项不能代替默认 Agent 执行验收。

## 实际命令与结果

在 `Agent_Server` 执行：

```powershell
$env:RUN_LIVE_RUN_PLAN_REVISION_PRODUCT='1'
$env:QA_RUN_PLAN_REVISION_PRODUCT_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p2-run-revision-product-current-20261022-02'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_plan_revision_product.py -q --tb=short --maxfail=1 --junitxml=..\docs\upgrade_2026-10-03\p2-run-revision-product-current-20261022-02.xml
```

结果 **2 passed，112.57 秒**。两个场景各实际执行 `npm run build`，包括 Vue 类型检查和 Vite 构建，均成功。首轮功能测试 **2 passed，122.23 秒**；随后补充原始代理响应记录、保存输入原文件及提交前恢复投影的完整断言，再执行上述最终轮。两轮原始证据均保留，没有失败测试。

在 `agent_web` 执行 `npm.cmd test -- src/features/projects/RunPlanRevisionPanel.test.ts src/features/projects/CasePlanDialog.test.ts`：**95 passed，4.05 秒**。未重新运行完整后端测试库；本轮没有修改后端生产实现或原有后端测试。

证据为同目录下 `p2-run-revision-product-current-20261022-01/`、`-02/` 及相应 XML。每个场景保留 `input.json`、`result.json`、实际 API/构建/代理的 stdout 和 stderr、页面文本/ARIA、网络记录、恢复前后截图和 Docker 日志。最终轮另保存五份原始 JSON 文件。

源码及说明文档的 `git diff --cached --check` 通过；浏览器原始 `last-page.txt` 含表格抽取产生的行尾 tab，完整证据目录的 whitespace 检查因此有报告。这些原始输出按实际字节保留，没有整理成新的页面证据。

## 隔离与清理

复用 `isolated_runtime`：配置落在同一 MySQL 服务的专属随机表；业务落在同一 PostgreSQL 服务的专属随机数据库。临时 Redis 使用已缓存镜像中的 `redis-server`，只映射 localhost 随机端口，关闭持久化；不新增模型容器。测试结束后夹具删除本轮专属资源。

独立只读回查确认，两轮合计 **4 个 Redis 容器、4 个 PostgreSQL 测试库、20 张 MySQL 测试配置表均不存在**。每轮各有 `cleanup-and-secret-audit.json`；按当前已配置凭据完整值及 token 边界扫描首轮31、最终轮41份文本文件，未发现匹配值。本场景没有上传工件对象，不能据此声称 RustFS 功能或对象清理已验收。

## 当前分类与下一步

| 类别 | 当前结论 |
|---|---|
| 已有实现且本轮通过 | 人工提交已保存候选、修改未领取投影、固定已领取依据、历史查询、原键恢复、实际 API 重启及页面核对 |
| 已有实现但本轮未验收 | 页面生成/审阅候选的模型链路、Worker 续接后的真实业务执行、更多网络/事务故障、其他测试模式 |
| 仍需补实现 | Agent 执行中自主提出新缺口、沿现有规划服务生成候选并提交 Run 修订；不能把本次人工路径称为自主修订 |
| 继续查证/验收 | 未知副作用的自动只读查证、完整准备状态；默认 AI 的项目选取→排序→补全→执行，以及其他七方向的既定未完成项 |

Step 认证按用户确认为通过；本轮没有重复调用未改变的外部状态。此前实际调用返回无有效 Step Plan 套餐，不应写成未认证。本轮也没有更换模型协议：Anthropic 仍调用 `client.messages.create`，`await_provider_io` 仅包裹 I/O，OpenAI Responses 为独立 transport。
