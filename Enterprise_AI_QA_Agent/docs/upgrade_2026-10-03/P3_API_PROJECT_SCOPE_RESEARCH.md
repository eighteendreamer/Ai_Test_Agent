# API项目身份从候选到恢复的核验〔2026-10-12〕

基线b9d1ab60。完整P0—P5 active，外部AI暂缓。调研 `ApiDocsService.list_documents(project_id)`、`ApiProjectLocator`、API mode state/Campaign、文档解析与旧快照恢复。

发现并修复的实际缺口：发现阶段没有保存 `documents_by_project` 为真实候选，选择时会用项目名筛入同项目全部缓存文档；同名项目容易混淆。恢复状态的项目/文档/Campaign身份也没有在读取正文前重核。另一个入口允许嵌套 `api_testing_request.project_id` 伪造正式身份。

修复采用现有正式绑定：`context_bundle.project_id` 是唯一会话身份；请求只从绑定上下文读取，不接受普通arguments或嵌套模型请求捐赠project_id。发现结果保存真实DocumentCandidate，选择只消费候选明确的doc_ids；文档目录先按正式project_id过滤，Campaign保存project_id。恢复/推进前通过目录get_document校验所有已声明文档的project_id，项目或文档冲突在读取正文前拒绝并记录 `api_project_scope_rejected` / `api_document_project_scope_rejected`。旧无ID文档保留兼容路径，但不会替代正式绑定。

实际测试：`test_api_project_scope_live.py` 内存与 `RUN_LIVE_API_PROJECT_SCOPE=1` 隔离PG/RustFS共 **6 passed, 4.28s/9.09s**；两同名项目和实际Markdown上传、对象回读、真实解析、Campaign构建、旧状态恢复、跨项目伪造（0正文读取）均通过。正式身份回归与召回回归另有16通过；前端 `npm run build` 成功7.56秒。所有业务HTTP请求0，外部AI0。

仍未由此证明默认AI多候选排序质量、正式Run完整查证和完整产品流程；这些继续待验收。
