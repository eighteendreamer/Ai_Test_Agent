# P4 文档版本、解析与差异当前验收

日期：2026-10-23

## 本轮真实验收

命令：

```text
RUN_LIVE_DOCUMENT_DIFFERENCE_PRODUCTION=1
pytest tests/test_document_difference_production.py -q --tb=short --maxfail=1
```

结果：**3 passed，176.06 秒**，参数为 Markdown、DOCX、文本型 PDF。

每种格式都使用真实 API、独立解析/比较进程、MySQL 配置、PostgreSQL 任务与快照、RustFS 原件和结果对象。测试实际验证：

- 首次上传创建版本，上传新版本保存独立原件；两个原件字节和 SHA-256 可从 RustFS 读回。
- 解析任务和差异任务使用独立 Worker，API 重启后按同一 `creation_key` 幂等读回。
- 原文差异、归一差异、章节/表格或分页来源位置、解析器版本和警告均保存；比较结果不经过 AI 改写。
- 受限 CPU 进程失败会保存退出码和 RustFS 失败工件，任务状态为 `failed`，而不是伪造空比较结果。
- 终态重复派发不会增加任务或重写结果；禁用派发时历史仍可读，新请求返回明确的 503。
- 没有调用模型；上传文档不依赖 Git。

证据目录：`p4-document-difference-production-current-20261023-01/`，按 `md`、`docx`、`pdf` 分目录保存。测试结束后隔离 Redis、PostgreSQL、MySQL 表和 RustFS 对象均已清理。

## 已确认能力边界

- Markdown 解析器提供文本/章节来源。
- DOCX 使用 `python-docx`，提供正文段落和表格；页眉页脚、脚注、批注及视觉布局会明确警告。
- 文本型 PDF 使用 `pypdf`，提供分页文本；OCR、表格、标题结构和复杂布局会明确标记为不支持或不可靠。
- 解析器和配置变化追加新快照，不覆盖旧快照。

## 仍未完成

1. Git 作为独立可选来源的真实产品入口、只读仓库路径/commit/blob 证据及明确关联为已有文档新版本的完整页面验收。
2. 需求影响分析、增量草稿、追踪矩阵与更多业务变更场景的默认模型质量验收。
3. 扫描 PDF/OCR、Docling HTTP 扩展和更多解析故障组合；首期不应把这些能力写成已支持。

