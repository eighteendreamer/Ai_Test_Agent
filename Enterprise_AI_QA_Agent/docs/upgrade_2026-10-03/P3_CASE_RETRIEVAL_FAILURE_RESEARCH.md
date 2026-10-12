# 项目用例召回失败可见性〔2026-10-12〕

基线2c0bc397。调研 `graph/nodes/router.py`、`TestCaseEmbeddingService`、`TestCaseStore` 及用例召回回归。正式项目绑定和向量过滤已经按 `project_id`、active version、environment、mode_key 约束；自由测试不注入用例，固定 `execution_basis=cases` 不重新召回。

发现缺口：绑定项目的召回异常被 router 捕获后只写日志，`test_case_hits=[]` 继续进入模型。空列表可能被误解成“当前项目没有匹配用例”，无法区分检索故障与真正 no-match，违背项目隔离和召回故障必须可见的运行合同。

修复：保留运行继续能力，但在 `context_bundle` 写入 `test_case_retrieval_status=failed` 与错误类型，加入明确的系统提示块，写入 `test_case_retrieval_failed` 图事件；成功召回写 `completed`，未请求写 `not_requested`。不自动切换自由测试，不生成虚假用例，不扩大召回范围。

实测 `test_test_case_embedding_pipeline.py`、`test_case_embedding_index_lifecycle.py`、`test_embedding_runtime_cache.py` 共 **19 passed, 3.21s**。新增失败模拟确认项目 ID、错误类型、提示和事件同时存在；原项目范围、版本和安全不可信提示测试保持通过。默认 AI 和正式Run仍未调用外部模型。
