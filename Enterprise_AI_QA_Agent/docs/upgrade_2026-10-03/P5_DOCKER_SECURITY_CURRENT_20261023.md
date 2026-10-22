# P5 Docker 性能与安全当前验收

日期：2026-10-23

## 性能工具

`tests/test_performance_docker_measurement.py` 已真实通过（1 passed）。现有 Docker/k6 工具实际运行正常、smoke、缺失 P99、SLA 违规四种场景：报告分别为 `pass`、`pass`、`inconclusive`、`fail`。k6 stdout/stderr、退出码、P99、PostgreSQL 指标记录、RustFS 原始工件大小/SHA-256 均已核对；缺失指标没有转成通过，阈值违规保留失败证据。目标容器和性能执行容器均清理，当前无 `qa-perf-*` 或 `qa-execution-cli-*` 残留。

证据：`Agent_Server/docs/upgrade_2026-10-03/p5-docker-performance-current-20261023-01/`。

## 安全工具

`tests/test_security_evidence_live.py` 已真实通过（1 passed）。受控目标的三种结果为：

- 缺失安全响应头：任务 `completed`，4 个 finding，工具退出码 0；
- 完整响应头：任务 `completed`，0 个 finding，工具退出码 0；
- 不可达目标：任务 `failed`，0 个 finding，工具退出码 1。

API 重启后结果仍可读，终态工具回放没有新增请求，跨 session 下载返回 404，RustFS 原始工件下载和哈希核对通过。Kali/安全工具容器、Redis、隔离数据库和对象均由测试清理。

证据：`Agent_Server/docs/upgrade_2026-10-03/p5-docker-security-current-20261023-01/`。

## 报告中心产品验收（2026-10-23 修复后）

`Agent_Server/tests/test_security_report_product.py::test_real_security_report_artifact_and_built_app` 已真实通过（`1 passed in 70.39s`）。测试使用现有安全工具产生缺少响应头、完整响应头和不可达目标三种结果，并在独立 PostgreSQL、RustFS 和构建后的前端中核对：

- 当前安全 `ToolJob.output_payload["report"]` 与 `report_json` 原件一致，报告列表正确显示任务总数 3、完成 2、失败 1、发现 4；
- 报告正文通过现有 artifact content 接口从 RustFS 按当前 `report_markdown` 懒加载，页面能显示“任务总数：3”“失败：1”和“缺少 X-Frame-Options”；
- 前端下载的 JSON 与工具最终回执中的统计、结算、验证和评估字段一致；
- API 重启后报告正文和评级仍可读取，无计划外 4xx/5xx；
- 测试结束后安全工具、Redis、隔离数据库、RustFS 对象及 `qa-execution-cli-*` 容器均已清理。

根因是报告工件采用 `__storage_mode: path_only` 时，报告服务原先只对 API 模式使用当前任务回执，安全模式无法从元数据解析 JSON；同时前端只读取内联 `__content_text`，没有读取 RustFS 正文。修复保持原件在 RustFS、任务和结构化投影在 PostgreSQL，不把完整正文复制到 MySQL。

代码验证：后端报告服务 `11 passed`；前端 sessions API `2 passed`；`npm run typecheck` 和 `npm run build` 均退出码 0。

证据：`Agent_Server/docs/upgrade_2026-10-03/p5-docker-security-current-20261023-rerun-fixed/`。

本轮再次独立运行完整报告产品验收：`1 passed in 74.74s`。JUnit：`Agent_Server/docs/upgrade_2026-10-03/p5-docker-security-current-20261023-codex-rerun.xml`。新证据确认报告投影 `ready=true`，任务总数/完成/失败/发现分别为 3/2/1/4；浏览器下载的 JSON 与回执一致，API 重启后页面可读，无页面错误或非计划 HTTP 错误。临时 Redis 与三台安全执行容器均已清理，按容器 ID 复查为空。未验证 Agent 自主规划或默认模型驱动的安全测试。

本轮独立证据目录：`Agent_Server/docs/upgrade_2026-10-03/p5-docker-security-current-20261023-codex-rerun/`（完整数据库快照和运行日志保留在本机，未纳入本次提交）。

## 仍未完成

本轮证明的是 Docker 工具、原始输出、解析、证据和报告边界，不是 Agent 自主调用质量。仍需：真实 Agent 驱动性能/安全流程、真实业务目标、多级加压、AI 流式 TTFT/Token 吞吐、完整前端报告页面和更多中断/恢复组合。

