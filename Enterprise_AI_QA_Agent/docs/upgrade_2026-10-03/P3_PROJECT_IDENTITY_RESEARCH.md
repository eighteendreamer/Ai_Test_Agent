# API 项目候选正式身份〔2026-10-12〕

基线74a283ac。调研 `ApiDocRecord.project_id`、`ApiDocsService.list_documents`、`ApiProjectLocator`、API mode state 和 Campaign。原候选按 project_name 分组，正式项目表中的同名项目会被合并；运行 Campaign 也只保存 project_name，不能满足项目隔离合同。

修复沿现有字段和存储实现：`ApiTestingRequestState`、`ProjectCandidate`、`DocumentCandidate`、`ApiTestCampaign`增加可兼容的 project_id；正式上下文 project_id进入请求，文档目录在候选前按project_id过滤，候选按project_id分组，文档选择和Campaign继续保存该ID。无project_id的历史文档保留旧兼容分组，但正式有项目绑定时不会读入这些文档。没有新表、配置、依赖或模型协议。

实际 `test_api_project_locator_identity.py`、`test_test_case_embedding_pipeline.py`、`test_api_mode_session_isolation.py`、非live API Agent回归共 **16 passed, 1 skipped, 2 deselected, 7.36s**。证明同名不同ID不合并、绑定ID过滤目录、召回和会话隔离合同保持；未调用外部AI，正式多候选默认模型质量仍待验收。
