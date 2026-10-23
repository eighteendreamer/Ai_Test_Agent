# 当前 PostgreSQL 与全新初始化基线对账

## 范围和方法

本轮取得了一份实际历史部署样本：当前应用配置指向的 PostgreSQL `QA_Agent.public`。对该数据库只执行只读 catalog 查询，不读取业务行正文，也不调用任何 initializer。对照基线仍由 `tests/test_runtime_schema_inventory_production.py` 在随机隔离数据库中，通过完整生产 API lifespan、性能指标 Store 和 Cleanup Store initializer 现场建立。

结构快照比较以下语义：

- 表是否存在；
- 列名、类型、空值约束、默认值、identity 和 generated 定义；
- 主键、唯一键、外键和检查约束；
- 索引定义和部分索引谓词。

`TIMESTAMPTZ` 与 catalog 返回的 `timestamp(6) with time zone` 视为同一类型；`ALTER TABLE` 造成的列物理顺序差异单独记录，不判为业务结构缺口。

## 实际结果

生产初始化基线共 78 张表。当前库共有 114 张表，其中：

- 基线有、当前库没有：36 张；主要是文档解析/差异/影响/需求、用例导入与规划、项目环境、API Agent 输入结果、Run 派生表和录制回放等较新结构。这些表均有当前 API Store initializer，当前样本显然没有运行过包含这些初始化入口的完整现有 lifespan。
- 当前库独有：72 张；名称全部属于 `cap_*`、`soak_*`、`p5_test_*`、`up_ass_*` 真实测试隔离表。这里只记录事实，本轮没有在用户未授权的情况下删除这些历史表。
- 共同表中语义完全相同：37 张。
- 另有 18 张只存在时间戳默认精度写法或列物理顺序差异，归为兼容物理差异。
- 共同表中存在 5 张真实语义差异。

5 张真实差异为：

| 表 | 当前样本缺口 | 当前实现的收敛入口 |
|---|---|---|
| `agent_sessions` | 缺 `state_revision` | API lifespan 的 `PostgresSessionStore.initialize()` 补列 |
| `agent_turn_tasks` | 缺 `parent_task_id`、`root_trace_id`、`result_payload` 及两个索引 | API lifespan 的 `PostgresAgentTaskStore.initialize()` 补列/补索引 |
| `agent_perf_runs` | 缺 `project_id`、外键和项目基线索引 | 首次读取性能基线或保存性能报告时由 `PerfMetricsStore.initialize()` 补齐 |
| `agent_test_runs` | 缺 `plan_snapshots` | API lifespan 的 Run Store initializer 补列；Alembic `20261019_0004` 也显式覆盖该列 |
| `agent_test_run_items` | 同名租约索引的旧谓词只有 `claimed/running`，缺 `waiting_resource` | 原 initializer 使用 `CREATE INDEX IF NOT EXISTS`，无法替换旧同名索引，本轮修复 |

因此，前四项证明当前样本尚未运行到已有升级入口，不是缺实现。`agent_test_run_items` 是实际迁移缺陷：同名索引已存在时，PostgreSQL 不会因为 `CREATE INDEX IF NOT EXISTS` 更新其谓词。

## 根因修复

`PostgresTestRunStore.initialize()` 现在先从 `pg_catalog` 读取项目自有租约索引的定义和谓词：

- 当前索引已覆盖 `(run_id, lease_expires_at)` 和 `waiting_resource` 时保持不变；
- 已有同名旧索引缺列或缺状态时，在同一初始化事务中删除并按当前定义重建；
- 记录 `test_run_lease_expiry_index_upgraded` 结构化日志；
- 只处理由当前 RunItem 表名派生的明确索引，不扫描或改写其他索引。

真实隔离 PostgreSQL 先构造旧谓词。修复前复现结果为：

```text
1 failed in 18.50s
assert 'waiting_resource' in "(status = ANY (ARRAY['claimed'::text, 'running'::text]))"
```

修复后两个独立 Store 并发初始化，最终谓词包含 `waiting_resource`，结果 `1 passed in 8.75s`。

## 验证

实际命令和结果：

```text
RUN_LIVE_POSTGRES_TESTS=1 pytest tests/test_waiting_resource_claim_boundary.py tests/test_test_run_duration_transitions.py
13 passed in 13.15s

pytest tests/test_test_run_postgres_claim.py
15 passed in 7.83s

RUN_LIVE_POSTGRES_TESTS=1 pytest tests/test_schema_migrations.py tests/test_waiting_resource_claim_boundary.py tests/test_test_run_duration_transitions.py tests/test_test_run_postgres_claim.py
59 passed in 10.51s

RUN_LIVE_RUNTIME_SCHEMA_INVENTORY=1 pytest tests/test_runtime_schema_inventory_production.py
1 passed in 23.52s
```

扩展回归第一次出现 6 项 `plan_snapshots` 缺列失败；根因是共享真实 PostgreSQL 测试夹具仍建立旧 Run 契约，而业务 Store 已固定写入 `plan_snapshots`。同步夹具后同一组 13 项通过。该失败没有被跳过。

五个 Python 改动文件通过 `py_compile`；当前虚拟环境未安装 `ruff`，因此未执行 lint。

结束后独立确认本轮 `live_wait_*`、`live_duration_*` 测试表和 `up_execution_cli_*` 隔离数据库均为 0。本轮未修改 `QA_Agent` 的业务结构或数据，未创建容器。

## 部署顺序结论

当前样本给出了可执行的迁移前置顺序，但本轮仍不直接改生产库：

1. 先只读导出各部署的同类结构快照，并识别测试遗留表与正式表。
2. 对现有四个 Alembic revision 使用离线 SQL 审核，不 stamp 未覆盖对象。
3. 在备份副本或克隆库运行当前 API/组件 initializer，核对前述 36 张缺表和 4 类缺列是否按已有入口收敛。
4. 专门验证 RunItem 旧索引替换的锁等待、执行计划和回滚窗口。
5. 所有部署样本收敛后，再冻结其余 Store revision 和关闭运行时 DDL；不能把当前单机样本直接外推到其他部署。

该顺序基于当前真实结构和现有代码路径，不需要上传文件与 Git 共用缓存，也不会把 initializer 结果伪装成 Alembic head。
