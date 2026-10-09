# 性能基线与运行记录项目隔离调研

基线1ae7af26。前一批报告入库失败/恢复已经验证，本批继续完整P0—P5；Step按用户决定待账号处理。本批不调用模型。

## 查证

- 原`PerfMetricsStore`表无project_id，get_baseline只按URL、成功状态/判定过滤；list_runs可列出全部记录，save_run按run_id无归属条件更新。
- 实际runtime只有一个基线查询/保存消费方；独立analyst使用调用方显式提交的baseline_metrics，不查询此表。没有性能历史列表API消费方。
- `PostgresSessionStore._initialize_sync`已有nullable UUID REFERENCES正式项目、ADD COLUMN IF NOT EXISTS及索引增量升级；采用同一做法。项目用例导入服务也使用UUID外键、项目条件查询。
- 现有SQLAlchemyCursor.rowcount读取数据库实际受影响行数，可判断ON CONFLICT WHERE未接受更新；无需先查后写制造竞争窗口。
- 文档store已有ON CONFLICT DO UPDATE WHERE身份条件，resource cleanup store已有INSERT alias；[PostgreSQL INSERT文档](https://www.postgresql.org/docs/current/sql-insert.html)说明条件更新，[NULL比较文档](https://www.postgresql.org/docs/current/functions-comparison.html)说明IS NOT DISTINCT FROM。SQL行为还通过本环境真实PG两个并发写入验证，不由文档版本代替实测。
- `PerformanceTestingState`已有持久状态/恢复，Correlation的wrap serializer已有不写新None字段的兼容做法。复用这些机制捕获项目，不新建执行器或状态仓库。
- 本地`评测工程化与CICD.html`5.1—5.3要求明确评测版本、保留基线和可追溯结果；本次只处理归属，不宣称已经解决所有环境/负载/多目标/版本之间的基线可比性。

## 真实反例

`p5-perf-project-before`1失败28.10秒。实际API建立A/B两个正式项目，隔离真实PG；用前一批真实JMeter测量保存A结果，在B上下文读取。B取得A的基线和列表，并成功把相同run_id的A判定由pass改成fail。原facts、日志/XML和当时测试源码保留。该反例没有新压测，没有用模拟PG代替数据库，也不声称完成Agent验收。

## 采用做法

指标表追加nullable正式project_id外键和(project_id,target_url,completed_at)索引。未知历史保留NULL，不以URL/时间/最近项目猜归属。get_baseline/list_runs只查询显式项目；缺项目返回无基线/空列表并记录原因，无全局回退。没有归属的历史仍留在数据库及原ToolJob/工件中。

save_run显式接收项目；UUID/正式项目外键拒绝自由名称或不存在项目。原run_id冲突只在项目、计划、引擎、backend、意图及目标身份一致时接受更新，项目包括NULL身份不允许自动转换。受影响行数0返回False，沿已验报告partial/analyzed恢复，不改原结果。没有修改已有同项目结果更新语义；同项目终态写入及历史归属补认仍需后续迁移/治理核验。

性能状态捕获正式会话项目，并随检查点保存；恢复项目不一致时拒绝且不修改原状态。分析/报告向store显式传该身份，顶层/metrics JSON/Prompt同步。旧状态未设置时不新增None字段，序列化不变；缺项目不借父层日志上下文。直接报告测试也明确传项目，不依靠隐式环境取值。

## 已实际验证

- store-verified：2通过65.77秒。实际两个项目同URL，各自保存/读取和列表；跨项目/计划冲突不能改原行，正常同身份重放可接受，未知UUID和自由名称写入失败，独立API重启后读取仍隔离。仓储用例使用前一批真实测量值，特意设置成功资格测试归属，不是重新评价原SLA。
- 最终store-delivery增加两个实际并发跨项目同run_id写入：2通过51.92秒，只有一个接受，另一方不能覆盖，最终归属与获胜调用一致；另一个节点重新验证旧表增量升级。前序工件保留。
- migration：上述第二项使用原表结构，含自定义false/0/null的旧JSON；两次initialize后仅增加NULL项目列，旧整行不变，不能自动领入新项目。fresh表创建在第一项验证。
- mode-verified：1通过91.52秒。真实缓存k6、受管HTTP目标、生产plan_resolved/guard/smoke/load/报告/ToolRuntime、PG/RustFS/独立API重启。A probe→B regression→A regression，同URL共6次引擎；B无跨项目比较，A比较自身实测P95，重放0新增引擎。39次下载原字节校验；7个受管完整容器不存在、7份completed清理回执。固定计划和镜像选择注入，不证明自主Agent或默认镜像配置。
- contract-first：45通过11.22秒；最终22模块expanded：154通过27.30秒、0跳过，既有Starlette/AnyIO警告1条。五项新增契约从真实失败/报告上下文验证项目传递、JSON工件、恢复拒绝、父上下文不借用及旧序列化兼容。
- 修改后的实际k6测量回归1通过53.77秒，四种报告pass/pass/inconclusive/fail保持，PG四行均有正式项目身份；compileall退出0仅为附加检查。此批没有重新运行真实JMeter load、完整App或模型；仓储记录来自旧实测JMeter，不将其称为新引擎验收。

生产历史没有在本环境升级。部署需ALTER/CREATE INDEX权限及正式项目表；旧NULL记录不会自动成为任何项目基线，旧NULL指标ID与新有项目保存发生冲突时保持partial，需明确历史归属治理，不能自动改绑。完整产品/默认模型、容量/流式、安全、UI录制/场景和其余P0—P5仍待推进。当前基线仍沿原成功资格/URL规则选择，负载/环境/多目标/版本可比性不在本批完成范围。
