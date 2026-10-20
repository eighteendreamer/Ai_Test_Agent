# Embedding provider 故障与项目目录路径核查（2026-10-20）

## 结论

项目内存在有界、按项目隔离的用例目录路径，但 embedding provider 请求失败时，规划按现有契约返回 `retrieval_failed`，不自动把故障当成零命中，也不调用规划模型。本轮没有更改生产代码或静默降级策略。

本次独立 embedding provider 实际返回 HTTP 402 / 错误码 30001（账户余额不足），没有产生查询向量，也没有进入 PostgreSQL/Redis 向量检索。因此这不是“成功查询但没有相关用例”。已认证可用的 Step 默认模型与该 embedding 账户是不同配置。

## 实际代码路径

空 `candidate_case_ids` 时，`CasePlanningService._recall` 调用 `TestCaseEmbeddingService.retrieve`。`retrieve` 首先调用 `EmbeddingRuntimeService.embed_texts` 生成 query vector；该调用发生在 Redis 查询的异常处理块之外，provider 异常会直接上抛。只有成功得到向量后，Redis 搜索异常才会记 `test_case_vector_redis_fallback` 并继续 PostgreSQL 向量搜索。

向量检索在有 query vector 的情况下落到 PostgreSQL，且最终没有任何命中时，`_recall` 才读取同一 `project_id` 的启用用例目录；未配置 embedding 服务时也使用该目录。该目录通过 `ProjectPlanningSources.case_search` 调用正式用例 Store，按 `updated_at DESC, id ASC` 排序，最多读取策略中的 `max_candidates` 条，并保留 `has_more` 分页提示。

目录搜索是确定性的关键词过滤，不是语义召回：PostgreSQL 对非空 `query` 仅匹配 `case_key` 和标题；空字符串代表有界枚举。查询同时限定 `project_id` 和 `lifecycle_status='active'`，返回候选后再由来源服务读取各自当前完整启用版本。不能据此宣称目录路径在大项目中有与 embedding 相同的召回率。

provider 异常发生在 `retrieval` 阶段，`CasePlanningWorkflow` 将任务保存为 `retrieval_failed`。既有测试 `tests/test_case_planning.py::test_retrieval_failure_no_match_and_unresolved_are_distinct` 明确检查 embedding 故障时规划模型请求数为 0，并把它与无候选、无资料支持而产生的 `blocked` 状态区分开。

## 功能验证

在 `Agent_Server` 执行：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_planning.py -q -k retrieval_failure_no_match_and_unresolved_are_distinct --tb=short
```

结果：**1 passed, 22 deselected, 5.47s**。该专项使用受控 `BrokenEmbedding` 与 `SequenceModel`，验证现行状态机契约；它不调用真实 402 provider，也不证明目录回退在 provider 故障时已实现。

## 处理决定与未完成范围

现有行为区分了基础设施失败与成功但无候选；擅自捕获所有 embedding 错误并转目录会改变已有测试契约，还可能让生产 UI 把检索服务故障伪装成低召回规划。本轮保持显式失败。

真实自动召回仍需在 embedding 账户恢复后，用全新隔离项目与新用例完成“启用用例 → Outbox/Worker → PostgreSQL/Redis 投影 → 空候选规划召回 → AI 选取完整目标和必要前置 → 正式 Run/执行/结果”的闭环。多候选检索还没有真实 Recall@K / MRR 基线。若后续产品明确要求 provider 故障时自动用目录容错，应作为独立行为变更，保留降级原因/警告，限制候选范围，并验证项目隔离、排序、公平性及全链路结果；不能把 HTTP 402 标记为向量召回成功。

课程依据：`项目借鉴/llm-testing-course/Embedding与向量库工程化评测.html` 第 2 章和第 6–7 章将检索质量作为 query 数据集上的 Recall@K、MRR、nDCG 与 hybrid 策略比较问题；因此关键词目录回退只能称为已有候选来源，不能在缺少标注样本评测时当成语义召回等价实现。
