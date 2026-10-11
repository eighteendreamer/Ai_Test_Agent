# 安全报告最终字段与现有报告中心：真实产品验收

本轮基线 `2f404e68`。上一批已证明现有响应头工具、Docker、PG、RustFS原始流及重启下载；本批继续验证报告汇总与前端，不把上一批工具级证据当完整产品验收。

## 依据和真实复现

- `SecurityTestingModeRuntime._complete_campaign/_finalize_failed_state` 在最终 verification/evaluation/settlement 填入 report 之前，已经生成Markdown/HTML/JSON工件。P4投影路径的顺序正确，但三处重复渲染不易保持一致。
- `ReportService.list_reports_page` 硬过滤 `mode_key=code_review`；`ReportsView` 仅识别通用 `report_markdown/report_html`、使用会话状态和校验进行评分，没有原件下载入口。性能工件实际类型是 `performance_report_markdown/html`、`performance_metrics_json`。
- 现有SessionStore协议、内存和PG实现已支持单模式过滤、分页与游标；PG过滤在LIMIT前，采用相同位置增加可选多模式过滤，不在Python分页后过滤。保留旧单模式调用，空列表返回空。
- 复用既有统一工件、`__content_text`元数据、现有sessions原件路由和Markdown安全渲染，不新增报告系统、测试模式、配置、表、依赖或邮件投递。课程《Agent轨迹与协议鲁棒性》3.1的schema漂移以及“工具能调通但业务含义理解错”在此落实为报告最终语义与原件一致、读取历史字段时不改写。

新 `test_security_report_product.py` 复用上一批真实工具夹具，调用当前缓存Kali和真实本地HTTP两次GET/一次连接失败，生成3个真实ToolJob。通过现有证据服务和发现汇总服务把这批结果放进报告恢复边界（`post_recon_stage=4`），经现有 `security-scan-runner` 工具入口生成报告；不是AI选取、规划、完整扫描campaign或默认模型验收。

第一批 **1 failed in 28.68s**：实际3任务/2完成/1失败/4发现已一致，但下载JSON的三项最终字段均为空，与工具输出不一致，生产报告列表也没有该安全会话。完整工具输出、实际PG工件、下载原件和报告列表保存在 `p5-security-report-before`。

## 根因处理

把三条现有报告路径归并到 `_materialize_report`，在最终字段写齐后统一序列化并补报告日志。JSON的结算/核验/评估、计数与工具输出一致；报告工件引用仍按原流程生成，JSON不嵌入自身后来生成的工件内容，不以此声称整个输出逐字等同。

报告中心改为三种既有模式的数据库查询。代码审查子报告会话及其元数据/批量工件读取保持原方式；安全和性能使用最新持久JSON投影必要摘要、结算/发现数量/性能verdict。投影错误有日志，不回退成旧成功报告，不进行扫描或调用模型。正文绑定同一ToolJob，识别性能工件名称；安全活动及证据核验按原报告数据展示。缺少最终信息、inconclusive或指标入库失败不能显示良好。安全报告显示真实结算状态，不把空闲会话当成报告空闲；原会话状态和历史不改写。原件下载走既有session/artefact归属路由。三个既有语言文件的报告中心文案同步。

## 实际验证与局限

最终 **1 passed in 55.52s，0 skipped**，见 `p5-security-report-final3`。本轮运行npm build/vue-tsc，构建版完整App/router、Chromium和独立生产main/真实隔离MySQL/PG/RustFS/Redis。安全报告正文3任务/1失败/4发现，等级需关注、状态部分完成；点击JSON原件下载的计数及最终三字段相同。12个default会话不挤占三模式分页，2页返回安全、性能和代码审查各一；空性能报告显示进行中。再次重启独立API、点击页面刷新后结果一致，没有增加目标GET。计划停API期间后台会话轮询500单列保留，恢复后非预期API错误0、pageerror0。

首轮修复验证1通过50.04秒。扩展场景的第一批因新增测试使用 `node:fs` 而项目tsconfig不含Node类型，在build中失败42.84秒；改用既有JSON夹具导入，无依赖或tsconfig改动。下一批1通过55.47秒，但实际截图状态仍显示会话空闲，按最终结算补显示后才得到最终55.52秒。旧失败和中间证据保留，不覆盖。

12模块回归 **150 passed、33 skipped in 12.17s**；跳过为既有外部PG/生命周期门控场景，本批真实隔离PG分页已在产品验收执行。后续报告投影5项通过0.49秒，包括原真实性能metrics工件兼容读取；其第一批错误假定旧工件已有metrics_persistence，1失败4通过，改按历史字段缺省读取，原文件不改写。前端14项通过，使用真实安全报告夹具证明partial/未知不能良好、同一任务正文和代码审查行为；性能评分合同测试不是本轮真实压测产品验收。compileall退出0，最终build日志及页面/网络/PG/原件在相应批次。

默认Step按用户决定继续等待实名认证。本批完成安全响应头结果的报告阶段与产品展示验收；安全其他扫描器、完整默认Agent、权限/超时/存储故障组合，真实性能报告完整App，容量/AI流式、录制知识/场景/清洗遗漏、需求业务质量和生产历史迁移等仍未完成。无推送或PR。

实际命令在Agent_Server内设置 `RUN_LIVE_SECURITY_REPORT_PRODUCT=1`、`QA_SECURITY_EVIDENCE_DIR=G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p5-security-report-final3`，运行 `..\.venv\Scripts\python.exe -m pytest tests/test_security_report_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p5-security-report-final3.xml`。后端回归、投影、前端原始日志见同前缀；最终测试内部实际执行npm build，不能仅凭compileall报完成。

## 2026-10-11 独立复验

在不改生产代码的前提下，使用新的隔离存储和新的证据目录再次运行 `test_security_report_product.py`，实际构建前端并启动独立 API。结果为 **1通过、64.13秒**：报告计数3/2/1/4、状态partial；原件 JSON 下载字段与最终报告一致；报告中心过滤和分页、未就绪报告进行中状态、API 重启后内容相等及零新增目标 GET 均通过。完整 App 页面和浏览器下载无 pageerror，真实工具事实、PG/RustFS/容器清理及跨会话404保存在 `p5-security-report-current/`。

这次复验仍只覆盖已有响应头工具和报告产品链路；不扩大为其他安全工具、完整默认 Agent 或生产部署迁移验收。
