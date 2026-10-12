# Git 文档来源当前实现复核

基线：`4874360c`。本轮没有新增 Git 业务代码；先按方案要求核对当前实现，再用真实临时仓库、隔离存储和独立产品进程验收。

## 当前实现结论

Git 来源已经存在，不应重复实现：

- 项目级 Git 配置写入现有 MySQL 配置存储，仓库必须先登记并启用。
- `resolve` 在受限子进程中只读指定仓库、commit/reference 和相对路径，返回 commit、blob、原件哈希、大小、仓库根、策略版本等固定来源。
- `import` 再次按固定 commit/blob 读取；不读取移动分支当前值，不向仓库写入、不 clone、不 checkout、不提交。
- 未指定 `document_id` 时创建独立文档；指定文档和父版本时才追加新版本。上传文件没有经过 Git，也不会借 Git 缓存。
- 项目、仓库、文档、父版本、配置 revision 和 `request_id` 均校验；相同请求可重放，父版本冲突和跨项目来源拒绝。
- 原件、版本快照、解析/比较任务沿现有 PostgreSQL/RustFS 链路保存；Git origin 记录 commit/blob/path/hash，不覆盖旧版本。

## 实际验收

1. `p3-git-current.xml`：**24 passed, 4 skipped**。覆盖 SHA-1/SHA-256、bare/full object、replace refs、特殊文件模式、路径安全、缺 blob、超时/取消清理、幂等、并发、项目隔离、父版本冲突和本地 API 路由。
2. `p3-git-live2.xml`：**26 passed**。启用 `RUN_LIVE_CASE_PLANNING_TESTS=1`，实际使用隔离 MySQL、PostgreSQL、RustFS，覆盖并发重放及正式项目文档版本链。
3. `p3-git-production-current.xml`：**2 passed**，耗时 132.11 秒。启用 `RUN_LIVE_DOCUMENT_GIT_PRODUCTION=1`，真实独立 API、Redis Worker、MySQL、PostgreSQL、RustFS、Chromium；分别验证 Markdown 和 DOCX 的 Git 固定来源导入、丢回执恢复、解析、比较、API 重启和普通上传不依赖 Git。

## 分类

Git 只读来源、独立导入、版本追加和生产流程属于**已有实现且本轮已实际验收**。本轮没有证明默认 AI 的需求影响判断、用户业务评审、生产部署适用性或历史迁移；这些仍是未完成范围。扫描 PDF OCR 和 Docling 容器仍按首期不部署处理。
