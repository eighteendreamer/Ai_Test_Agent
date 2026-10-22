# P2：规划期间领取目标的修订冲突

日期：2026-10-22。完整升级继续未完成；执行 Agent 自主发现缺口并触发重规划仍需接入。本次是在接入前，通过实际存储发现并修复修订提交边界的错误成功报告。

## 调研与失败复现

沿 `AgentCaseExecutionAdapter`、`ToolRuntimeService`、`PostgresCaseActionAdmission`、`RunRevisionPlanningService`、`CasePlanRunService` 和 `build_run_plan_revision` 核对调用链。执行图当前没有提交重规划任务的调用点；现有重规划服务可直接复用，但不能把服务验收等同于自主触发完成。

修订事务与领取事务已经通过 `lock_plan_projection` 协调。问题发生在事务内的映射：候选中某正式用例已经被领取时，构造器直接保留旧 RunItem，跳过候选依赖映射，却仍保存整个候选快照并报告 applied。因此“没有改写历史执行”不能证明“候选已经应用”。

实际复现流程：五份 JSON 文件生成正式用例并评审启用，上传 Markdown 到 RustFS，创建原计划和 Run，提交持久重规划任务；受控模型生成给左目标补前置的候选。在候选完成、修订确认之前，通过真实 PostgreSQL 的完成/领取接口解除原前置并领取左目标。旧实现返回 applied，而目标仍保留旧依赖。

首轮结果：`1 failed, 8 deselected in 11.07s`，断言为期望 rejected、实际 applied，见 `p2-revision-claim-race-before-20261022.xml`。原失败状态保留在同名本地目录，未覆盖。

## 修复及兼容范围

修复放在现有构造器中，调用方继续在原事务锁内执行：

- 对已领取或已有执行记录、仍出现在候选中的条目，比较其归属步骤、准备/清理阶段、准备消费范围及实际映射后的依赖和数据路径。
- 候选改变这些执行定义时，抛出明确冲突；现有 finalizer 保存 rejected 和候选，不部分更新 Run，也不自动重发业务操作或重置条目。
- 理由和证据引用的重新说明不等于业务操作改变，允许保留；前置集合顺序不影响比较。
- 既有候选对未领取条目的修改仍可应用。已领取项继续从领取时的归档恢复，原 Attempt、检查点、失败结果、证据和 Suite 保留。

结构投影依据为现有 `build_agent_case_context` 的归属与阶段规则。没有新增规划器、审批、全运行暂停机制、模型协议、生产配置或数据库迁移。冲突意味着需要基于当前状态重新规划；当前尚未提供 Agent 自动处理该冲突的闭环。

## 实际验证

在 `Agent_Server` 运行：

```powershell
$env:RUN_LIVE_RUN_REVISION_PLANNING='1'
$env:QA_REVISION_BRIDGE_OUTPUT_DIR='docs/upgrade_2026-10-03/p2-revision-execution-conflict-final-20261022'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_run_revision_planning.py -q --tb=short --maxfail=1 --junitxml=docs/upgrade_2026-10-03/p2-revision-execution-conflict-final-20261022.xml
# 16 passed in 28.20s

$env:RUN_LIVE_PLAN_RUN_CREATION='1'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_case_plan_run_creation.py -k 'revision and not process and not redis' -q --tb=short --maxfail=1 --junitxml=docs/upgrade_2026-10-03/p2-revision-execution-conflict-regression-20261022.xml
# 7 passed, 40 deselected in 9.53s

..\.venv\Scripts\python.exe -X utf8 -m py_compile src/application/test_runs/plan_revision.py tests/test_run_revision_planning.py
# 退出0
```

新增8个组合：目标在规划期间被领取/完成，分别改变依赖、准备指令、输入路径或仅重述理由与补充证据。前三类共6个组合明确拒绝且 Run 完全不变；后者2个组合允许给尚未领取的另一目标补前置，同时保留已领取/完成项。均核对候选保留、Suite 不变、归档解析、终态重投及没有额外模型调用。完成/失败数据为显式受控结果，用于核对历史保留，不代表业务接口测试通过。

前一轮缩小回归为10通过、17.42秒；扩展变体后才运行上述最终16项。7项原有回归覆盖内存/实际存储修订、队列顺序、回滚及 PostgreSQL 领取/取消锁。两个测试文件的其余40项没有在本轮执行，原有独立 CLI 中断场景没有重跑；本次没有前端变更，未重复构建前端。

另外从提交 `4e06041cb3cc74c54c76ac7618eed5d5435ac46f` 导出已跟踪后端及测试，只覆盖本次两个源码文件，以原虚拟环境和实际隔离存储复验：**23 passed, 40 deselected in 36.54s**。这是同一范围的交付隔离复验，不累加为新场景。源码 Git blob 和结果见 `p2-revision-conflict-export-20261022.json`，不依赖工作树其他未提交改动。

## 临时资源与容器说明

本轮所有运行均未启动 Docker 容器，也未请求外部模型。MySQL 保存隔离配置表，PostgreSQL 保存隔离业务/任务表，RustFS 保存本次 Markdown。失败、缩小回归、扩展回归和隔离导出复验共40组带分配记录的夹具；独立只读回查确认680张 PostgreSQL 表、40张 MySQL 表均不存在，40份上传原件均返回结构化 `NoSuchKey`，`docker ps -a --filter name=qa-execution-cli-` 为空。此数量限于有分配清单的重规划夹具；不把未记录清单的其他回归资源计入，详见 `p2-revision-execution-conflict-cleanup-20261022.json`。

用户询问的 `qa-execution-cli-da0faae73ae8438f` 来自既有 `tests.test_execution_worker_cli.isolated_runtime`：运行本机已缓存镜像中的 `redis-server`，给默认 AI 选取、规划、执行及重投验收提供隔离队列；没有在该容器运行模型。镜像为 `eighteenyear/bigdata-pytorch:2.6.1`，用作 Redis 验收偏重。当时未提前解释用途；后续创建临时容器前应说明用途和清理范围。历史精确资源关联及清理见本地 `orphaned-test-fixture-cleanup-current-20261022.json`；本轮再次查询确认该容器不存在。

原始运行 JSON 含测试租约，保留本地并以哈希关联，不提交原始内容。交付只包含本次构造器、回归及说明；未部署、未推送。默认 Step 完整闭环、正式回执/UI 知识、Agent API 全流程、Docker 性能/安全报告、需求变更业务质量与全阶段请求追踪等仍按总表推进，不能由这23项替代。
