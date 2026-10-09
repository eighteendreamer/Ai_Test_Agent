# 模板修改后的固定用例版本：现有完整 App 验收补强

## 查证与分类纠正

`PLAN.md` 6.87、`P1_IMPORT_FULL_PRODUCT_RESEARCH.md`、`test_case_import_full_product.py` 已有构建版完整 App、独立生产 main、两项目、三种导入、错误行/冲突、评审启用、新版本及重启验收。因此手工模板与 Excel/JSON 入口应归为“已有实现且已有完整产品验收”，不是待新增功能。原目标列表中的历史状态不能直接当当前状态。

本轮先复验当前版本：临时探针跑完整生产 App 源码、独立 main 与真实 MySQL/PG/RustFS/Redis，三种导入 3 passed in 74.24s。探针初次按钮严格名称遗漏图标字符失败，原 ARIA 日志保存；修正后行布局先 1 passed in 27.82s。它们是 Vite 开发服务器上的完整 App，不能声称构建包验收。查清原有完整验收后，把新增检查并入原测试；临时重复入口移除，原探针源码只作不可变证据保留，不留下第二套测试流程。

## 依据与采用做法

- 项目默认/项目模板 `TestCaseTemplateService` 使用 MySQL 的 `test_case.generation_template`；`TestCaseVersionRecord`、`case_store.py` 使用 PG 固定版本和内容哈希。修改模板只能影响后续生成。
- 原 `test_case_import_full_product.py` 已实际启用 JSON 用例 V1、再保存 V2 草稿，但此前没有在这两个版本存在后再次修改项目模板。
- 在原场景中增加约 20 行：页面修改写作指导 → 等待真实 PUT200 → 核对 MySQL 修订增加 → 核对 case 和 V1/V2 完整记录、内容哈希、active_version_id 不变；随后沿原主进程重启比较新模板、原用例与版本。
- 不调用模板生成或默认 Step，不改用例结构、执行模式、原哈希或生产代码。引用课程原文 `评测工程化与CICD.html` 的版本化/可追溯要求，以及 `线上流量闭环与评测集治理.html` 的原始信号脱敏、补齐上下文和通过标准要求；不将生成/导入成功算实际测试通过。

## 最终实际验证

Agent_Server 内设置 `RUN_LIVE_CASE_IMPORT_FULL_PRODUCT=1` 和 `QA_CASE_IMPORT_FULL_PRODUCT_DIR=../docs/upgrade_2026-10-03/p1-template-fixed-version-app-verified`，执行：

```text
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_import_full_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p1-template-fixed-version-app-verified.xml
```

实际 **1 passed in 52.25s，0 skipped**。原 `product_browser` 本轮实际 `npm run build` 后运行构建资源，独立生产 API 启动和重启，真实 Chromium 操作；MySQL 保存模板/导入映射，PG 最终 3 用例/4版本，RustFS 原件上传/下载一致。完整 JSON 的未知字段与 0/false/null、分表步骤排序、混合错误行不可提交、编号冲突不可覆盖、同批次提交重放、另一项目隔离、新草稿不修改 V1、修改模板不修改 V1/V2、重启读回均通过。

主动停 API 期间的后台轮询 500 单列 `planned_restart_response_failures`，代理原 stderr 保存；恢复后 `unplanned_api_errors=[]`、`page_errors=[]`。构建、preview、两个 main 的 stdout/stderr、全部网络响应、ARIA、截图、真实原件、PG 行和完整 facts 原字节保存。项目根 ` .\.venv\Scripts\python.exe -m compileall -q Agent_Server/tests/test_case_import_full_product.py` 退出0，仅附加检查。

## 对整体目标的意义与剩余

本批是当前版本复验和新增不可变性检查，未新增生产功能，不把已验证旧功能再次计为首次完成。普通文档模块生成/恢复已有6.97受控验收，真实默认模型业务质量待账号处理；模板/导入其他容量故障、正式部署和历史迁移仍需核验。完整 Agent 选取/补全/执行、准备与未知效果只读查证/运行修订、人工 DeepSeek 业务、清洗遗漏/场景别名/回放报告、完整 Docker Agent/前端与安全、更多需求业务评审、全阶段HTTP与旧Worker原件仍未完成。
