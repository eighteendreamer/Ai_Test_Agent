# P1 固定生成草稿的评审与修订入口

2026-10-22。本轮补齐生成任务详情到已有用例版本对话框的连接。完整 P0—P5 继续进行；本轮没有改变生成质量、模型协议、配置存储或数据库结构。

## 调研、复现与实施

已重读用户附件方案，核对当前 `CaseGenerationTask.vue`、`useGenerationTask.ts`、`TestCaseContentDialog.vue`、项目页的对话框接线及现有版本评审产品测试。

| 分类 | 当前事实与采用做法 |
|---|---|
| 已有实现 | 任务页可读取固定收据、完整 JSON、原响应；`readVersion` 已校验项目、用例、版本号和内容哈希，并丢弃旧路由的迟到响应 |
| 已有实现 | 用例对话框支持指定初始版本、完整 JSON 编辑、追加草稿、来源/生成记录继承及显式评审/启用；复用它，不新增另一套编辑器 |
| 确认缺口 | 任务页没有进入该对话框的操作；只有原始读取动作，无法就当前固定草稿直接进入修订 |
| 本轮补实现 | 增加“评审与修订此草稿”；返回已校验的用例记录，指定回执版本打开对话框；页面身份变化关闭它，归档/权限未知继续只读 |
| 保留的约束 | 保存只追加新版本，任务回执仍引用原版本；提交评审和启用仍为独立显式操作 |

真实失败样本来自 `p1-alternate-document-recovery-current-20261022-01/` 的生成组、固定范围和查询草稿。首次组件测试纠正了其错误的 complete 断言后，实际失败为 `Missing action: 评审与修订此草稿 …`，据此补入口。4 项回归覆盖打开 V1 时已有 V2、修订追加 V3 并继承来源、归档只读、哈希不符拒绝和项目切换迟到响应。

同时更正 [上一份生成恢复报告](P1_ALTERNATE_GENERATION_RECOVERY_20261022.md)：基线映射为 complete，恢复查询样本是 incomplete；其中3条技术预期引用的路径为 null，2个来源候选均有 `expected_field_invalid`。原有结构校验已识别这些问题，不能据此说所有映射都已通过。原始草稿、回执和质量审阅未修改。评审修订入口并不自动修复模型的业务理解。

## 实际功能验收

使用现有 `document_generation_product_scenario.py` 扩展浏览器操作，不替换正式页面或 API；实际启动生产 API、独立解析/生成/重试 CLI，连接隔离 MySQL、PostgreSQL、Redis 和现有 RustFS。模型 HTTP 使用本地受控响应，未请求外部 Step 或 Agnes。

创建包含注册、订单、查询三模块的 Markdown 原件。完整测试保留原有独立模块故障恢复、终态重投和丢失提交响应场景，再通过构建版 App 的实际路由进入固定生成任务：

1. 读取固定 V1 的完整 JSON 和原响应。
2. 从新入口打开已有版本对话框，初始选择确为收据中的 V1。
3. 编辑 JSON，实际 POST 返回201，得到新 V2。
4. 对照 API 读回：V2 与返回内容一致；`manual_edit.source_version_id` 指向 V1；V1 哈希保留，原任务回执仍引用 V1；没有自动启用。
5. 浏览器错误为0；受控模型共6次调用，其中原模块场景3次、产品场景3次，修订不新增模型请求。

首轮 **1 failed，151.08秒**：将原生 select 中折叠的 option 错当作可见元素；现场已保存 V2。该错误属于验收断言，改为核对实际 POST201、选中的版本 ID 和持久读取。首轮证据与失败 JUnit 保留为 `p1-generation-review-product-current-20261022-01/` 及同名 `.xml`。

新隔离环境重跑 **1 passed，125.90秒**。证据为 [产品结果](p1-generation-review-product-current-20261022-02/product.json)、[完整服务结果](p1-generation-review-product-current-20261022-02/summary.json)、同名 `.xml`、页面截图、网络及构建/进程 stdout/stderr。`npm run build` 的 vue-tsc 与 Vite 均通过（Vite9.15秒）；另行类型检查通过。最终前端定向回归 **3 files / 28 tests passed**。

```powershell
# agent_web
npm test -- src/features/tools/plugins/GenerationTaskReview.test.ts src/features/tools/plugins/GenerationTaskProduct.test.ts src/features/projects/TestCaseAuthoring.test.ts
npm run typecheck

# Agent_Server；产品测试内执行 npm run build
$env:RUN_LIVE_DOCUMENT_GENERATION='1'
$env:RUN_LIVE_DOCUMENT_GENERATION_PRODUCT='1'
$env:QA_DOCUMENT_GENERATION_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\docs\upgrade_2026-10-03\p1-generation-review-product-current-20261022-02'
..\.venv\Scripts\python.exe -X utf8 -m pytest 'tests/test_document_generation_production.py::test_ordinary_document_modules_and_independent_saved_response_recovery[False]' -q --tb=short --junitxml=..\docs\upgrade_2026-10-03\p1-generation-review-product-current-20261022-02.xml
```

本轮只运行非流式参数；流式、DOCX/PDF 和更多故障组合没有借本轮宣称通过。当前全工作树还含其他升级中的变更，本轮提交只选固定草稿评审入口及相关测试、证据和记录。

## 清理与未完成边界

两轮临时 Redis 分别为 `qa-execution-cli-b0a71651319c4a02`、`qa-execution-cli-cd25e70366fb49d7`，只绑定本机随机端口。夹具 run/port/logs/rm 全部退出0，独立只读回查确认两容器、两个专属 PG 库和各5张 MySQL 表都已不存在。成功轮10个引用 RustFS 对象全部返回不存在；首轮执行了夹具 finally 清理，但没有另存完整对象清单，不将它说成逐对象独立复查。见 [清理回查](p1-generation-review-product-current-20261022-02/cleanup.json)。

尚未验收：真实用户业务评审、修订后的真实执行、默认 Step 当前完整生成/执行、更多格式/模型/故障和生产迁移。Step 账号认证按用户确认已完成；最近保存的实际推理请求仍为 HTTP400、无有效 Step Plan 订阅，本轮没有在外部状态不变时重发。现有适配层继续按 transport 保留 Anthropic Messages、OpenAI Chat/Responses 和 Gemini，不新增单一协议层。

下一步仍按完整目标调查默认 Agent 规划/补全和真实工具执行、UI 录制/正式回执、API/性能/安全、需求变更质量与环境适用性、全阶段请求追踪及历史迁移。此处只关闭一个确认的产品连接缺口。
