# 运行时数据库初始化清单与隔离复验

## 结论

当前新库引导依赖 API、业务组件和独立 Worker 各自的运行时 initializer，应用启动不会执行 Alembic。该机制在本轮隔离环境中能够建立当前代码所需结构，并在 API 重启时保持表集合、行数和配置修订不变；这不等于已有部署的全部历史结构已经纳入显式迁移。

代码依据：

- `src/main.py` 的 lifespan 直接初始化项目、会话、文档、用例、运行、录制等 Store，并初始化五类 MySQL 配置 Store。
- `src/modes/performance_testing_mode/runtime.py` 构造 `PerfMetricsStore`，首次读取基线或保存报告时调用其 initializer。
- `src/cli/run_resource_cleanup_worker.py` 在独立 Cleanup Worker 启动时初始化 `PostgresResourceCleanupStore`。
- `alembic/README` 明确现有四个 revision 只覆盖 Outbox、ToolJob/Artifact、Compaction 和 Run `plan_snapshots`，不构成其余应用表的基线。

因此，不能把 API 启动后缺少性能指标表和清理回执表判为 API 漏建表，也不能为通过清单测试把这两张表强塞进 API lifespan。它们分别由实际消费组件和 Worker 持有。

## 实际验收

新增 `tests/test_runtime_schema_inventory_production.py`，使用：

- 一份现场创建的空 PostgreSQL 数据库；
- 五张随机命名的 MySQL 配置表；
- 完整 `src.main:app` 生产 lifespan；
- 已有 Redis DB 13，不创建 Redis 或 Docker 容器；
- 正式项目 API 与设置 API；
- 正式性能指标 Store 和 Cleanup Store initializer。

验收先确认 PostgreSQL 为空、MySQL 只有空的应用配置表；随后启动完整 API，创建一个项目并修改通用设置。停止并再次启动 API 后，通过正式接口读回项目和设置，同时逐表比较结构和行数。

首次假设“API 启动后 41 张配置表应全部存在”实际失败，缺少：

- `agent_perf_runs`
- `agent_resource_cleanup_jobs`

沿调用链核对所有者后修正验收边界，再运行结果为：

```text
1 passed in 23.29s
```

证据位于 `docs/upgrade_2026-10-03/p6-runtime-schema-inventory-current-20261023-02/` 和对应 JUnit XML。

## 初始化结果

`DatabaseConfig` 声明 41 张 PostgreSQL 表和 5 张 MySQL 配置表。

完整 API 首次启动创建 76 张 PostgreSQL 表：39 张声明表和 37 张派生表。派生表按实际名称分组如下：

| 所属域 | 数量 | 内容 |
|---|---:|---|
| 文档、生成、差异、影响和需求 | 21 | `agent_api_docs_*` 的 assets、versions、submissions、parse、diff、impact、case generation 和 requirement 表 |
| 项目环境 | 3 | environment、snapshot、decision |
| API Agent 持久输入与结果 | 3 | credential、dispatch input、worker result |
| 旧 Smoke catalog 投影 | 5 | plan catalog/version、scope binding、run history、regression candidate |
| Run 计划修订 | 1 | 由正式 Run 表稳定派生的 plan revision 表 |
| ToolJob 用例操作证据 | 3 | operation、effect、consumption 派生表 |
| 录制回放 | 1 | `ui_recording_replay` |

性能指标 Store 与 Cleanup Store 各初始化一张表后，总数为 78，41 张声明表全部存在。API 及组件 initializer 均未创建 `alembic_version`。

MySQL 启动结果为：

- 5 张声明的配置表全部存在；
- 应用配置表有 15 个全局配置键，其中人工修改后的 `workspace.general` 为 revision 2；
- 模型配置表 4 行；
- 赞助配置表 1 行；
- 邮件和渠道配置表均为 0 行。

第二次 API 启动后，PostgreSQL 和 MySQL 的表集合、各表行数、应用配置键及 revision 均与第一次一致。PostgreSQL 项目和 MySQL 通用设置均通过正式 API 读回。

测试结束后独立确认：隔离 PostgreSQL 数据库为 0、随机 `up_cli_*` MySQL 表为 0、Redis DB 13 中 `qa:test:*` 键为 0。本轮没有创建容器。

## 当前分类与后续边界

**已有实现且本轮已验收**：当前代码从空库启动、API 与组件分工建表、默认配置播种、重启幂等，以及配置留在 MySQL、业务项目留在 PostgreSQL。

**已有实现但未覆盖全部部署**：四个 Alembic revision 的有限迁移、旧邮件配置转换、MCP 旧集成迁入、Workspace 旧文件迁入等各有专项，但不能外推到其他未登记历史结构。

**仍需补实现或取得部署证据后再决定**：其余 Store 的冻结 revision、从已部署旧结构到冻结结构的迁移顺序、关闭运行时 DDL 的切换策略。当前没有各部署环境的实际旧 schema 样本，不应凭空生成全量 ALTER 或把 initializer 执行结果 stamp 成 Alembic head。
