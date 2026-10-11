# 2026-10-09 性能响应提取、状态与依赖传值研究

基线d3e36b09；上一轮实际修复原件持久化/冒烟和退出判定，属于进展。本轮继续完整P0—P5；默认Step按用户决定不调用。

## 现状与依据

- K6EngineAdapter._build_default_function对每个目标都声明每个Correlation.extract为const，同一关联变量在多目标重复声明。into_header从未读取，smoke.expect_status从未读取；status check固定200—399。_validate_smoke仅在stdout搜索expect_extract，不检查实际响应或check结果。先用真实引擎保存反例，不把源码推断称实际缺陷复现。
- PerfPlan已有targets、Correlation(extract/from_path/into_header)、SmokeConfig(expect_status/expect_extract)，RawMetrics保留raw_data；共享runner/存储/现有Job无需另建配置或业务存储。API模式已按任务身份保存来源、嵌套path读取、凭据只在运行时应用header，是身份/证据做法的参照，不复制业务规则清单。
- 本地课程接口测试-通用篇6.2第457—466行要求响应变量能实际用于后续受保护接口；性能测试-通用篇6.1/6.2要求单用户完整业务链和逐级压测。本批先验证短时响应/关联，不称容量基线。
- 官方[关联](https://grafana.com/docs/k6/latest/examples/correlation-and-dynamic-data/)示例先读实际响应再用于后续请求；[Response.json](https://grafana.com/docs/k6/latest/javascript-api/k6-http/response/response-json/)提供selector；[Rate](https://grafana.com/docs/k6/latest/javascript-api/k6-metrics/rate/)及[Checks](https://grafana.com/docs/k6/latest/using-k6/checks/)说明check失败不导致进程失败，必须读取结果或配阈值；[Params](https://grafana.com/docs/k6/latest/javascript-api/k6-http/params/)提供请求header。官方页面当前v2.3.x，本地缓存v2.2.0，须实际运行核验兼容，不升级镜像。Markdown页面工具获取失败，换普通HTML取得正文；不是生产失败。

## 复现设计

用现有DockerResourceManager、隔离MySQL/PG/RustFS/Redis及独立生产API登记项目会话，另用标准库ThreadingHTTPServer提供本测试/login、/missing、/profile。动态凭据只留目标和VU内存，不写脚本、stdout或证据；记录是否提供/是否真正验收及SHA-256。四场景：有效提取不打印、缺值但引擎stdout含同名文字、期望201却返回200、登录提取并传后续Authorization。保存原脚本/汇总/执行/烟测和服务端事实后再决定设计。无Agent/模型调用、外网压测或安全扫描。

## 实际反例 → 采用做法

p5-perf-correlation-before实际1失败39.32秒，四场景分别false/true/true/false，与true/false/false/true相反。有效响应确实完成3次却被认为未提取；不存在值但实际handleSummary输出ticket时通过；期望201却实际200仍通过；两目标引擎107，SyntaxError: Identifier 'ticket' has already been declared，目标没有请求。不能修成打印变量名或简单吞异常，这是生成器与结果契约错位。

采用k6现有check和原summary.root_group.checks的passes/fails，按目标/关联索引命名，不新增私有证据协议/控制台字符串。声明的状态和提取逐条核验，缺少check保持未采，不凭stdout推断。官方[expectedStatuses](https://grafana.com/docs/k6/latest/javascript-api/k6-http/expected-statuses/)及Params.responseCallback使明确负向401与HTTP错误率语义一致；check失败仍由独立分析判定，不用进程exit0冒充业务正确。

Correlation增加可选from_target/into_target/header_prefix；未设置字段序列化省略，原三字段输入保持。未指定来源沿第一目标，header未指定消费方沿紧邻下一目标；明确名字必须唯一存在且消费排在来源后，不向所有后续接口广播凭据。多个来源/消费方用明确目标名，变量名作为对象键而非JS标识符。值只在单次VU iteration对象存活，不全局共享、不打印；0/false不是缺失，null/undefined/空字符串不能提供必要header。缺提取和无合法header准备跳过受影响消费目标，后续独立目标继续，保留原失败checks；不虚构登录操作或路径。

共享读取器从标准checks投影状态、成功/失败计数、来源目标及提取是否成功，smoke与report用同一事实。SLA与业务核验分开：确定check失败为fail，必需check缺失为inconclusive，原件/进程失败规则保留。JMeter仍用其原产物链，不静默替换k6；JMeter真正的提取/断言及JTL证据继续单独验证，不能把k6专项称两引擎全面完成。当前不新增表/配置/依赖，不改变Agent按用例理解的执行入口。

## 实际验证与清理流程

初次四场景verified1通过38.73秒；20模块expanded140通过44.36秒。扩展full-verified1失败102.39秒：前10场景正确，第11场景resource_pending_reap。DockerResourceManager.run finally按完整ID删除后retire；RedisResourceLeaseManager.retire明确保留配额到PostgresResourceCleanupStore持久回执，原测试未启动独立Worker。不是响应逻辑错误，不能扩大池/直接释放名额。

按现有src.cli.run_resource_cleanup_worker --once逐次调用真实生产清理，保存stdout/stderr、14份PG分配与completed回执、Redis占用1→0及完整ID不存在。worker-verified最终1通过155.86秒：12smoke与2load；动态凭据只在目标和VU内存，服务端记录哈希及真实鉴权接受。缺依赖跳过/profile但/plain仍实际执行；显式login→profile、Bearer前缀、0/false与null/空串/坏JSON、实际401均核对。两load引擎exit0/SLA通过，提取失败仍reportfail，现有PG两行pass/fail保存。

新增判定结构化日志后delivery-contract140通过38.25秒；真实measurement-regression1通过65.82秒；evidence-regression1通过89.65秒，重启读回request_validation、24份原件下载、跨项目/篡改/终态重投/8个容器删除。compileall0附加，不代替功能。初次pool失败与原件保持。无Agent/真实模型/正式App/JMeter/容量/安全验收；默认Step按用户决定暂缓。三份方案及原253/155/153/413/200/408/151工件审计见p5-perf-correlation-validation.json。

## 2026-10-11 交付补验

上次 `full-verified` 失败后，按现有生产 CLI 启动 `src.cli.run_resource_cleanup_worker --once`，没有扩大资源池或直接释放租约。最新 `p5-perf-correlation-worker-verified2` 实际 **1通过105.35秒**：12个 smoke 变体和2个 load/report 变体全部执行；14个 allocation 与14个 PostgreSQL cleanup receipt 一一对应，状态均为 `completed`，每次 Redis 四类占用从1回到空，容器完整ID均已删除。`load-chain` 报告通过，`load-missing` 在引擎退出0且SLA通过时仍因提取检查失败报告失败。

本次启动过程中发现此前录制质量提交的三个文件末尾含字面量 `\\n`，生产 API 导入时产生 SyntaxError。删除该非法行后，`test_recording_quality.py test_recorder_session_service.py` **19通过6.29秒**，单文件 `py_compile` 通过，修复提交为 `4f6c492d`；原性能失败批次保留未覆盖。

修复后既有回归重新通过：`test_performance_docker_measurement.py` **1通过43.04秒**，`test_performance_evidence_live.py` **1通过53.09秒**。证据目录分别为 `p5-perf-measurement-regression2`、`p5-perf-evidence-regression2` 和 `p5-perf-correlation-worker-verified2`。这些是缓存 k6、受控本地 HTTP、隔离存储和现有 Worker/ToolRuntime 的验收，不等同默认 Agent、外部模型、持续容量或完整 App 验收。