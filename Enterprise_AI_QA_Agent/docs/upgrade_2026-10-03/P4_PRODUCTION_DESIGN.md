# P4 正式 API 与独立影响 Worker

研究日期：2026-10-05；当前研究基线：d4a3d636。本节对应方案的生产入口接入；完整升级范围继续保留。

## 依据与采用做法

| 已核对来源 | 采用做法 |
|---|---|
| `document_differences.py`、`document_operation_calls.py`、`APIAuthenticationMiddleware` | 复用全局 Bearer 鉴权、UUID 路径、项目/文档归属检查和 404/409/503 恢复提示；不另外建立权限体系 |
| `main.py`、`AppContainer` | 在用例、解析及比较初始化后装配影响服务；可编辑策略由现有 MySQL 配置服务初始化，业务表继续 PostgreSQL |
| `document_operation_worker_runtime.py`、`document_difference_worker_runtime.py` | 共用资源生命周期；将已有比较来源构造抽成同一函数，影响 Worker 只读固定来源，不启动 API、浏览器、录制或测试执行器 |
| `DocumentImpactWorkflow`、`DocumentImpactTaskHandler`、`PostgresDocumentImpactStore` | 保留现有输入/范围/调用/核验/结果检查点、租约心跳及终态；新增入口不改变旧 canonical 输入和请求 |
| `run_task_outbox_relay.py`、`RedisTaskWorker` | PG 同事务 Outbox → 独立 relay → Redis → 独立影响进程；同身份提交恢复和终态重投不调用模型 |
| 本地课程 `Agent轨迹与协议鲁棒性.html` | 同时验轨迹与结果，实际中断进程，再核对未知请求是否被重发 |

API 与 Worker 使用 `build_document_impact_runtime` 同一装配函数。可编辑模型指导、输入容量、分批及调用预算分别留在原 MySQL 四类策略；不在环境变量另设业务策略。环境仅负责既有连接、派发开关及 transport stream 登记。

## 公开边界

项目下新增四类影响 settings 的 GET/PUT，以及文档下的 `impact-tasks` 提交/分页/原请求查询/任务查询、`impact-tasks/{id}/result`、`impact-snapshots` 分页/固定结果查询。任务 POST 返回 202；固定输入仍由 Worker 准备，API 不执行分析或工具。

结果读取调用原完整事实核验：保存对象大小/hash、冻结输入及范围、实际请求中的规则交付、原候选与引用、覆盖状态。按 task 读取可用于只读核对已保存的结果检查点；按 snapshot 查询只读取已发布历史。不在查询时重选当前用例、Prompt、模型或发送 HTTP。

关闭派发仍可重放原提交与读取历史；新提交返回现有 503 恢复提示。新 Worker 要求 stream 已登记；未知 stream 在建立数据库/对象资源前失败。旧缺少自动协调协议的任务保持原明确失败与历史读取规则。

前端同步独立影响 API 客户端及 DTO，公开候选、原文/用例引用、单元状态和完整 JSON 值保留；固定 seed 作为只读 JSON 审计元数据。此阶段未接正式页面/任务池入口，不能宣称完整产品流程已验收。

## 独立运行

在 Agent_Server 工作目录运行，确保既有派发开关启用且 `qa:tasks:document_impact` 已登记于 `ORCHESTRATION__REDIS_TASK_OUTBOX_STREAMS`：

```powershell
..\.venv\Scripts\python.exe -m src.cli.run_task_outbox_relay
..\.venv\Scripts\python.exe -m src.cli.run_document_impact_worker
```

两个命令各自运行在独立进程；`--once` 用于单轮处理，影响 Worker 可用 `--stream` 选择已登记传输流。解析及比较继续使用各自既有 Worker。部署模板同步登记三类文档 stream；不修改用户实际 .env。

## 实测设计及已发现的错误假设

真实 MD/DOCX/PDF 上传 V1/V2 → 独立解析/比较 CLI → 完整 12 步跨模式用例草稿 → API 固定影响提交 → 独立 relay/Worker → API 固定结果。读取 MySQL 策略和 PG seed/Outbox，并核对 RustFS 原件及结果原始字节。

同时验证 API 重启、后续配置/用例变化、项目隔离、未知请求、无效参数、关闭派发、终态重投；使用有效 Bearer 访问并实际验证错误 Bearer 被拒绝。中断测试真实终止在途 Worker，保留 Redis pending，明确使本测试任务租约过期后由新进程恢复；检查第一调用只有一次物理发送且状态 unknown，其余单元可完成并保留 partial。

首轮 1 失败（28.97 秒）：测试创建其他项目用例时错误地使用空来源列表，实际 API 返回 422；按既有最少一项来源契约改为人工来源。

次轮 4 失败、1 通过（208.54 秒）：四种正常分析流程已保存并返回结果，但测试错误地预设 complete。实际比较总是保留 `extracted_text_positions` 坐标警告，DOCX/PDF 另有格式能力限制；既有协调协议将依据限制保留为 partial。修正验收为明确检查这些限制及各声明单元 analyzed/无缺口，未修改生产状态计算。真实进程中断恢复在次轮已通过。失败日志/XML/实际进程输出保留，后续当前源码验证另记。

受控 HTTP 提供商验证传输与协议，不证明真实默认模型业务判断。正式 App、任务池、需求评审/稳定需求身份/矩阵/增量草稿、生效判断、生产历史迁移和其余 P0—P5 仍须继续。

## 最终当前源码验证

20通过0跳过281.38秒；另135项相关回归通过0跳过542.48秒，包含三格式正式比较。五个生产场景、物理HTTP与完整冻结请求、实际原始对象/PG记录均保留。前端6项API边界测试和包含vue-tsc的构建通过。首批18通过2跳过及补跑SQL池8通过另存，最终20项已启用两个live开关。未测试真实默认模型、正式App/任务池或生产历史迁移；完整范围继续，详见PLAN第六节及IMPLEMENTATION。
