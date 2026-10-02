# 本轮系统功能验收记录

执行目录：20261002T150231-e4ba048db8；整体：failed；产品代码指纹未变化：True。

通过的是实际执行断言；整项功能验收仍为 partial。诊断组跳过的集成条目不是通过。

## 后端分组

|分组|状态|结果统计|
|---|---|---|
|diagnostic|partial|{'passed': 1650, 'failed': 0, 'skipped': 459, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|session-store|passed|{'passed': 63, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|migration-core|passed|{'passed': 24, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|postgres-core|passed|{'passed': 33, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|redis-core|passed|{'passed': 30, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|metrics-core|passed|{'passed': 40, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|mcp-core|passed|{'passed': 27, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|api-execution|passed|{'passed': 95, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|sql-lifecycle|passed|{'passed': 8, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|browser-core|passed|{'passed': 10, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|
|model-browser|passed|{'passed': 1, 'failed': 0, 'skipped': 0, 'xfailed': 0, 'xpassed': 0, 'not_run': 0}|

前端：{'success': True, 'numPassedTests': 147, 'numFailedTests': 0, 'numPendingTests': 0}。浏览器时间探针：未执行。

## 按功能留档

|编号|功能|状态|记录|
|---|---|---|---|
|A01|会话创建与项目绑定|partial|[执行记录](features/A01.md)|
|A02|消息、流式输出与正文对账|partial|[执行记录](features/A02.md)|
|A03|审批、继续与中断|partial|[执行记录](features/A03.md)|
|A04|持久派发、排队与恢复|partial|[执行记录](features/A04.md)|
|A05|任务池与执行流程投影|partial|[执行记录](features/A05.md)|
|A06|会话时间、生命周期与清理|partial|[执行记录](features/A06.md)|
|B01|项目管理与执行总览|partial|[执行记录](features/B01.md)|
|B02|用例草稿生成与来源|partial|[执行记录](features/B02.md)|
|B03|评审、启用与固定版本|partial|[执行记录](features/B03.md)|
|B04|套件冻结版本|partial|[执行记录](features/B04.md)|
|B05|运行领取、执行、结果与证据|partial|[执行记录](features/B05.md)|
|B06|失败项创建新回归运行|partial|[执行记录](features/B06.md)|
|C01|默认 Agent 与工具协议|partial|[执行记录](features/C01.md)|
|C02|代码治理与 SSH 源码|partial|[执行记录](features/C02.md)|
|C03|API 测试与断言|partial|[执行记录](features/C03.md)|
|C04|性能测试与多引擎报告|partial|[执行记录](features/C04.md)|
|C05|安全测试、授权与缺陷复测|partial|[执行记录](features/C05.md)|
|C06|兼容性 runner 与持久回执|partial|[执行记录](features/C06.md)|
|C07|冒烟方案审批与冻结执行|partial|[执行记录](features/C07.md)|
|C08|UI 自动化、录制与回放|partial|[执行记录](features/C08.md)|
|D01|模型配置、流式协议与错误|partial|[执行记录](features/D01.md)|
|D02|OAuth 回调与授权状态|partial|[执行记录](features/D02.md)|
|D03|Skill 管理、重载与路径边界|partial|[执行记录](features/D03.md)|
|D04|MCP 管理与工具目录|partial|[执行记录](features/D04.md)|
|D05|意图识别、权限与工具治理|partial|[执行记录](features/D05.md)|
|E01|项目知识图谱查询与编辑|partial|[执行记录](features/E01.md)|
|E02|长期记忆与向量检索|partial|[执行记录](features/E02.md)|
|E03|上下文压缩与索引重建|partial|[执行记录](features/E03.md)|
|E04|API 文档导入、解析与项目绑定|partial|[执行记录](features/E04.md)|
|E05|附件选择、上传与工件下载|partial|[执行记录](features/E05.md)|
|E06|测试报表与模板渲染|partial|[执行记录](features/E06.md)|
|F01|邮件配置、授权与发送|partial|[执行记录](features/F01.md)|
|F02|渠道凭据、配对与接入策略|partial|[执行记录](features/F02.md)|
|F03|供应商 webhook 接入|partial|[执行记录](features/F03.md)|
|F04|设置、导出、导入与清理|partial|[执行记录](features/F04.md)|
|F05|赞助信息配置与展示|partial|[执行记录](features/F05.md)|
|G01|鉴权、CORS 与请求关联|partial|[执行记录](features/G01.md)|
|G02|Redis 投递、重试与 Outbox|partial|[执行记录](features/G02.md)|
|G03|配额、租约与资源占用|partial|[执行记录](features/G03.md)|
|G04|Docker 与浏览器执行环境|partial|[执行记录](features/G04.md)|
|G05|观测、Token 与多进程指标|partial|[执行记录](features/G05.md)|
|G06|版本迁移与测试质量门|partial|[执行记录](features/G06.md)|
|H01|页面切换与异步生命周期|not_executed|[执行记录](features/H01.md)|
|H02|流式消息、Markdown 与链接|partial|[执行记录](features/H02.md)|
|H03|多语言与 UTC 时间显示|not_executed|[执行记录](features/H03.md)|
|H04|正式构建、包拆分与外部协议|not_executed|[执行记录](features/H04.md)|
|I01|六种扩展模式：目录占位|catalog_only|[执行记录](features/I01.md)|
|I02|全系统联合验收与故障矩阵|partial|[执行记录](features/I02.md)|

所有完整 case nodeid、阶段结果、skip 原因及命令日志保存在本目录。详见 summary.json；当前不作为上线批准。

Docker 破坏/强杀组、完整应用 HITL、邮件渠道真实写入、RustFS、Memgraph 容量与 Electron GUI 本轮未执行，避免触碰未经隔离的业务数据。
