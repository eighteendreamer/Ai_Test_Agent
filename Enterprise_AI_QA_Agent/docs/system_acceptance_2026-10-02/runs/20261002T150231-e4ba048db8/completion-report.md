# 系统功能验收：补验与最终归档

源码基线：e614856。产品代码指纹未变化：True。

结论：已完成本轮分层测试及 48 项逐项留档；整系统验收仍为 partial，不能宣称所有功能已完整通过。

## 实际执行结果

- 后端 diagnostic：1650 passed、459 skipped、2 warnings；跳过不算通过。
- 十个独立真实环境分组：331 次断言通过，无跳过/失败。含 PostgreSQL、Redis、MCP stdio、真实 API TCP、SQL 生命周期、Chromium、真实模型工具与持久传输。
- 前端：147 passed、0 failed、0 pending；正式 vue-tsc + Vite build 通过。
- 生产工作台 + 正式 SessionService/HTTP/SSE：三种时区通过；辅助目录为空夹具，无外部模型。
- 项目 UI：创建、搜索、修改、归档四个检查通过；使用正式项目服务/路由和隔离内存存储，归档后历史仍可读取。
- 两张图：119 个文本元素通过画布/卡片边界检查，并已视觉检查；无 foreignObject、外部资源和黑色背景块。

## 失败记录保留

第一次工作台探针因脚本导入路径未进入产品逻辑；改为模块调用后补跑通过。项目 UI 首次使用的精确 accessible-name 选择器与带图标按钮不匹配；依据已保存 DOM 修正后通过。原始日志与失败报告未覆盖。这两次为验收脚本问题，不能列为产品缺陷。

项目 UI 的 agents/tools 目录与资源监控接口未装配，返回的 404 已写入 harness_unavailable_http；这些是测试环境缺口。完整业务 UI、跨主机恢复、真实供应商 webhook、历史迁移、RustFS/Memgraph 容量、Electron GUI 和完整故障矩阵仍未验收。

## 每项功能记录

|编号|功能|范围状态|留档|
|---|---|---|---|
|A01|会话创建与项目绑定|partial|[记录](final-records/A01.md)|
|A02|消息、流式输出与正文对账|partial|[记录](final-records/A02.md)|
|A03|审批、继续与中断|partial|[记录](final-records/A03.md)|
|A04|持久派发、排队与恢复|partial|[记录](final-records/A04.md)|
|A05|任务池与执行流程投影|partial|[记录](final-records/A05.md)|
|A06|会话时间、生命周期与清理|partial|[记录](final-records/A06.md)|
|B01|项目管理与执行总览|partial|[记录](final-records/B01.md)|
|B02|用例草稿生成与来源|partial|[记录](final-records/B02.md)|
|B03|评审、启用与固定版本|partial|[记录](final-records/B03.md)|
|B04|套件冻结版本|partial|[记录](final-records/B04.md)|
|B05|运行领取、执行、结果与证据|partial|[记录](final-records/B05.md)|
|B06|失败项创建新回归运行|partial|[记录](final-records/B06.md)|
|C01|默认 Agent 与工具协议|partial|[记录](final-records/C01.md)|
|C02|代码治理与 SSH 源码|partial|[记录](final-records/C02.md)|
|C03|API 测试与断言|partial|[记录](final-records/C03.md)|
|C04|性能测试与多引擎报告|partial|[记录](final-records/C04.md)|
|C05|安全测试、授权与缺陷复测|partial|[记录](final-records/C05.md)|
|C06|兼容性 runner 与持久回执|partial|[记录](final-records/C06.md)|
|C07|冒烟方案审批与冻结执行|partial|[记录](final-records/C07.md)|
|C08|UI 自动化、录制与回放|partial|[记录](final-records/C08.md)|
|D01|模型配置、流式协议与错误|partial|[记录](final-records/D01.md)|
|D02|OAuth 回调与授权状态|partial|[记录](final-records/D02.md)|
|D03|Skill 管理、重载与路径边界|partial|[记录](final-records/D03.md)|
|D04|MCP 管理与工具目录|partial|[记录](final-records/D04.md)|
|D05|意图识别、权限与工具治理|partial|[记录](final-records/D05.md)|
|E01|项目知识图谱查询与编辑|partial|[记录](final-records/E01.md)|
|E02|长期记忆与向量检索|partial|[记录](final-records/E02.md)|
|E03|上下文压缩与索引重建|partial|[记录](final-records/E03.md)|
|E04|API 文档导入、解析与项目绑定|partial|[记录](final-records/E04.md)|
|E05|附件选择、上传与工件下载|partial|[记录](final-records/E05.md)|
|E06|测试报表与模板渲染|partial|[记录](final-records/E06.md)|
|F01|邮件配置、授权与发送|partial|[记录](final-records/F01.md)|
|F02|渠道凭据、配对与接入策略|partial|[记录](final-records/F02.md)|
|F03|供应商 webhook 接入|partial|[记录](final-records/F03.md)|
|F04|设置、导出、导入与清理|partial|[记录](final-records/F04.md)|
|F05|赞助信息配置与展示|partial|[记录](final-records/F05.md)|
|G01|鉴权、CORS 与请求关联|partial|[记录](final-records/G01.md)|
|G02|Redis 投递、重试与 Outbox|partial|[记录](final-records/G02.md)|
|G03|配额、租约与资源占用|partial|[记录](final-records/G03.md)|
|G04|Docker 与浏览器执行环境|partial|[记录](final-records/G04.md)|
|G05|观测、Token 与多进程指标|partial|[记录](final-records/G05.md)|
|G06|版本迁移与测试质量门|partial|[记录](final-records/G06.md)|
|H01|页面切换与异步生命周期|partial|[记录](final-records/H01.md)|
|H02|流式消息、Markdown 与链接|partial|[记录](final-records/H02.md)|
|H03|多语言与 UTC 时间显示|partial|[记录](final-records/H03.md)|
|H04|正式构建、包拆分与外部协议|partial|[记录](final-records/H04.md)|
|I01|六种扩展模式：目录占位|catalog_only|[记录](final-records/I01.md)|
|I02|全系统联合验收与故障矩阵|partial|[记录](final-records/I02.md)|

完整机器记录：[completion-summary.json](completion-summary.json)。原始运行：[README.md](README.md)。

复跑命令在验收脚本与原 summary.json 的 commands 中保留；复跑创建新目录，不能覆盖旧失败结果。
