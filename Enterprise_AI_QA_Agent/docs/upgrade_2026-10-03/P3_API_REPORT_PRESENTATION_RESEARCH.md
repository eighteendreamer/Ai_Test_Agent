# Agent API 结果呈现与报告页研究

基线 `537a4aba`。本批处理已有 API 测试结果没有进入 Reports 页面、成功任务缺少可审阅请求/响应细节的问题，不改变执行协议。

## 调研事实

- `ReportService.REPORT_MODES` 原来只有 code review、security testing、performance testing；`api_testing` 会话被报告列表过滤，因而 API 测试只能依赖当前对话输出，不能在报告页按历史执行查看。
- API 执行已经生成 `api_test_report_json` 与 `api_test_report_md` 工件，JSON 中有任务、状态、断言和查证字段；因此复用现有工件和 Reports API，不新增 API 专用报告表或第二套查询接口。
- `ReportBuilder._task_summary` 原来只给成功任务状态、耗时和断言，失败任务才有证据片段。补充请求/响应时必须沿已有 `OutputSafetyPolicy` 脱敏，不能把 Authorization、Cookie、password、access_token 或 Set-Cookie 直接展示。
- 前端 `ReportsView` 已有通用报告列表、工件下载和 Markdown 展示，最小对齐方式是在同一报告详情中增加 API 任务卡片；不改变其他报告类型的投影和分页。

## 采用方案

后端将 `api_testing` 注册到报告模式和 `api-test-runner` 工具映射，按当前 ToolJob 绑定 `api_test_report_json`，投影任务统计、summary、findings、evaluation/verification、完整任务列表。失败或当前任务没有对应工件时沿现有“当前尝试无报告”语义，不回退到旧执行结果。

任务摘要增加经过 `OutputSafetyPolicy` 的 request/response、HTTP 状态、断言和 reconciliation 字段。前端只消费 `report_meta.api_tasks`，每条任务展示方法/地址、状态、HTTP 码、请求、响应、断言和查证状态；原始工件仍可下载。没有新增表、配置、依赖或模型协议，配置与运行数据存储边界不变。

## 实际验证

- 后端报告投影、API 会话纳入列表、请求/响应脱敏和未知查证结果回归：`p3-api-report-presentation-backend-final.xml` **23通过、0跳过**。
- 前端 `reportPresentation` 测试：**8通过**；覆盖 API 报告状态、通过/失败等级和 Markdown 工件选择。
- 前端 `npm run build`：typecheck 和 Vite production build **通过**，ReportsView 产物生成成功。
- 初次前端构建发现模板 class 表达式引号解析错误，保留首次失败命令输出；修正后重新构建通过，没有放宽类型检查。

## 边界

本批未调用外部 AI，未新增 MySQL/PG/RustFS schema；报告服务的存储读取沿现有服务。尚未进行独立生产 PG/RustFS 报告页浏览器验收，需在完整产品验收批次中补做。默认 AI API 全流程、自动续期、准备生命周期、复杂 UI 知识和其余 P0—P5 仍未完成。
