# P1 普通文档生成产品与格式复验

日期：2026-10-23。此记录只描述本轮真实复验，不覆盖默认 Step 模型的可用性，也不删除此前失败证据。

## 调研结论

此前 `p1-document-generation-product-formats-current-20261023-01` 的非流式产品场景曾在 Worker 领取任务后 60 秒超时。失败日志只出现 `case_generation_task_claimed`，没有进入模型调用；当时不能据此判断为模型协议、RustFS 或数据库实现缺陷。

对比成功/失败 Worker 日志并沿 `document_operation_worker_runtime`、`RedisTaskWorker`、`GenerationTaskService.execute` 和 `PostgresGenerationTaskStore` 调用链检查后，未发现足够证据支持直接修改代码。因此先按原参数组合做可观测复验。

## 实际复验

在 `Agent_Server` 目录执行：

```powershell
$env:RUN_LIVE_DOCUMENT_GENERATION='1'
$env:RUN_LIVE_DOCUMENT_GENERATION_PRODUCT='1'
$env:RUN_LIVE_DOCUMENT_GENERATION_FORMATS='1'
$env:QA_DOCUMENT_GENERATION_EVIDENCE_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p1-document-generation-product-formats-repro2-current'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_document_generation_production.py -q --tb=short --maxfail=1 -k 'ordinary_document_modules_and_independent_saved_response_recovery' -s
```

结果：`2 passed in 317.21s`。

本轮两个参数化场景均完成：

- 非流式和流式真实生产 API、独立解析 Worker、独立生成 Worker；
- 产品页面提交响应丢失后的原请求回查；
- 三模块生成、保存响应后的独立重试、终态重投不新增模型请求；
- DOCX 三模块和文本 PDF 一模块解析、生成、覆盖映射和 RustFS 工件哈希核对；
- 页面构建、类型检查和最终页面错误检查。

本轮证据目录：

`p1-document-generation-product-formats-repro2-current/`

## 状态边界

本轮把“普通文档生成产品与 DOCX/PDF 格式”更新为**最近一次完整复验通过，历史一次失败仍保留并需后续回归观察**。这不等于任意真实模型的业务质量已经通过；本次仍使用受控 HTTP 模型。默认 Step 的真实请求仍停在首次规划请求，账号认证按用户确认记录，供应商返回的是无活动 Step Plan 订阅/额度不可用。
