# 性能报告绑定最新执行：真实工具、数据库与产品验收

本轮基线 `653e8e6d`。按用户决定不调用待实名认证的默认 Step；沿现有 k6、ToolRuntime、任务/工件、报告中心与权限路径推进。

## 依据与先复现

- `ReportService._prepare_test_report` 只按 JSON 工件的 `created_at` 选择最新报告。`PerformanceTestingModeRuntime._handle_smoke` 在冒烟失败时返回失败及原始工件，不生成性能 JSON 报告。两者组合会把上一次成功报告误当作当前结果。
- `ToolJobService`、内存和 PG 工具库已有稳定任务身份、创建时间、状态和摘要；工件持有 `tool_job_id`。复用这一关联，不补造失败报告，不修改历史工件。
- 最新任务批量查询沿 `PostgresSessionStore` 的 `DISTINCT ON` 写法、现有工具库参数化批量工件读取以及 `asyncio.to_thread` 实现。只查询当前可见报告页的会话与现有报告工具；不逐会话访问数据库，不读取全部历史任务。
- 性能报告来源是 `performance-test-runner`，安全报告来源是 `security-scan-runner`。`http-probe-runner`、各扫描子工具、编译器等不能夺走报告主体；`perf-result-analyzer` 目前返回分析字段而不保存报告工件，不把它冒充报告持久化工具。
- 原课程关于失败结果不可覆盖、环境及运行依据可追溯的要求，在此落实为新执行与旧报告区分、保留历史原件、失败不能被成功掩盖。未引入依赖、表、配置、测试模式或新执行入口。

`test_performance_report_product.py` 复用 `test_performance_evidence_live.py` 的实际工具验收夹具。真实缓存 k6、受控 http-echo 目标、隔离 MySQL/PG/RustFS/Redis、独立生产 main，以及本轮构建的完整 Vue App/Chromium；没有拦截或伪造报告 API。

修复前 **1 failed in 48.12s**。先有真实压测 pass，再有真实无汇总的冒烟 failed；PG 两个任务不同，但报告接口仍绑定第一个任务，页面仍显示“良好”和 pass 正文。`p5-perf-report-before/report-product-observations.json`、对应报告列表、任务/PG、截图保留该事实；不是仅从代码猜测。

## 根因处理与跨层契约

工具库协议、内存、PG、应用服务同步增加批量最新任务查询。按 `(session_id, tool_key)` 和 `(created_at, id)` 选取，历史任务迟到更新不改变新旧执行顺序。报告中心先确定当前任务，再仅读取该任务的 JSON；任务没有报告、尚未终态或报告无法解析时，不回退旧成功报告。代码审查既有子报告及历史无新字段工件兼容读取保留。

报告 `report_meta` 新增可选 `job_status`，前端既有通用字典类型消费。评分与状态依据当前工具任务：失败/拒绝、取消、等待、恢复/部分交付、报告完成分别呈现；缺失报告的 completed 不能良好；真实 inconclusive 继续需关注。会话的 idle 不再覆盖已完成的性能报告。核心无报告分支记录任务、状态、会话与预期工件类型，不输出原始流或凭据。

历史工件继续在原件区下载。实际截图复核发现卡片缺乏任务归属，容易把旧 pass 原件当成本次结果；补“本次执行／历史执行”、任务 ID 与生成时间，三种语言同步。报告摘要、历史原件和业务结论各自沿当前已有数据展示，不改写历史。

## 验证批次与边界

第一批修复实测 **1 passed in 70.98s**；四个真实工具场景及生产 API 重启完成。扩展批次 **1 passed in 70.04s**：正常 pass、缺汇总、坏汇总、冒烟异常退出、queued、cancelled、负载异常退出 inconclusive、第二次存储调用故意失败后的 partial evidence/inconclusive、重启读回。等待/取消由真实任务库创建和取消，不启动压测；异常退出及存储故障使用实际引擎结果，在现有 analyzed 检查点保存报告，不重新运行引擎。存储故障是明确注入第二次上传异常，第一次真实 RustFS 原件已保存，不冒称整个存储服务断网。

最终补任务归属标识的产品批次 **1 passed in 69.27s，0 skipped**，另存 `p5-perf-report-final2`。实际页面验证历史标识及对应任务 ID；每个场景复位详情滚动位置后截图，避免原件下载滚动掩盖最新状态。浏览器与实际 HTTP 下载 JSON 的 verdict/metrics 一致，旧成功原件保留；终态读回不增加引擎调用；同尺寸篡改返回 409，恢复后 200，跨项目下载 404。实际容器删除按完整 ID 检查；API 单进程不负责清理队列，本批不把容器删除当容量租约复用验收。

受影响十模块回归 **84 passed, 6 skipped in 39.90s**；跳过为既有外部门控场景。最初加入迁移模块的批次收集失败：当前 `.venv` 缺 `alembic.config`，完整错误日志保留，未把该批记为通过。本轮没有 DDL/迁移，真实 PG 查询在产品验收实际执行；未声称迁移测试通过。报告投影 **7 passed in 0.59s**，前端两个报告测试文件 **11 passed**；原安全完整产品回归 **1 passed in 56.48s**。最终产品测试内部实际执行 `npm run build`（vue-tsc/Vite），compileall 退出 0 仅为附加检查。

完整性能 Agent、默认模型、容量与 AI 流式阶段、其余安全工具/故障、录制知识与场景/清洗遗漏、需求业务质量/历史部署迁移等仍未完成。此批从已验的 guard 和 analyzed 检查点开始，不是 AI 自主规划或全阶段验收。无推送或 PR。

实际命令：在 Agent_Server 设置 `RUN_LIVE_PERF_REPORT_PRODUCT=1`、`QA_PERF_EVIDENCE_DIR=../docs/upgrade_2026-10-03/p5-perf-report-final2`，执行 `..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_performance_report_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p5-perf-report-final2.xml`。安全回归使用 `RUN_LIVE_SECURITY_REPORT_PRODUCT=1` 和同轮 `p5-perf-report-security-regression` 目录；所有命令结果、跳过、失败和原件保留。
