# 工作区旧配置迁移的并发与进程中断验收

本轮按当前实现验证此前未验收的两项边界，没有修改业务代码、配置键或数据库结构。

## 依据与方法

`WorkspaceSettingsService.initialize()` 顺序迁移 `workspace.general` 和 `channel.advanced`，数据库已有记录时直接跳过旧文件。`MySQLApplicationConfigStore.save(..., only_if_missing=True)` 使用既有 `(scope_key, config_key)` 主键和 `INSERT ... ON DUPLICATE KEY UPDATE config_key=config_key`，且每个配置单独提交。因此本轮验证重点是数据库写入竞争和两个提交之间的进程退出，而不是补一个新的迁移锁或整体事务。

新增 `tests/test_workspace_migration_process_live.py` 复用既有 `Processes` 子进程管理。测试现场创建两份旧 JSON 文件和随机 MySQL 表，子进程执行正式迁移服务；测试 Store 子类只在已完成读取或提交的边界添加文件屏障，不替换 SQL、事务或返回值。三个场景分别为：

1. 两个独立 Python 进程都确认两类配置不存在，再放开写入。最终每项 revision 都为1、旧文件字节不变。
2. 两个进程已经观察到通用配置缺失时，经正式服务保存数据库配置，再放开迁移。数据库的语言与动画设置保留，两项 revision 仍为1，旧文件未覆盖数据库值。
3. 第一项配置已提交、第二项尚不存在时强制终止迁移进程。经正式服务修改数据库通用配置，再把对应旧文件破坏为非法 JSON；以全新进程重启迁移并再重复一次。数据库通用配置保持 revision=2，损坏旧文件不再读取，渠道配置补迁为 revision=1，重复启动没有增加 revision。

## 实际结果

```powershell
$env:RUN_LIVE_UPGRADE_CONFIG_TESTS='1'
$env:QA_WORKSPACE_MIGRATION_PROCESS_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p6-workspace-migration-process-current-20261023-01'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_workspace_migration_process_live.py tests/test_workspace_settings.py tests/test_settings_event_loop.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p6-workspace-migration-process-current-20261023-01.xml
```

**14 passed in 15.74s**，包含3个新增真实进程场景、原有真实 MySQL/HTTP/重启测试和事件循环回归。新增测试文件 `py_compile` 通过；环境未安装 ruff，未执行 Python lint。

证据目录按 `concurrent`、`concurrent-with-save`、`process-kill` 分别保存初始化脚本、子进程 stdout/stderr、`verification.json` 和 `cleanup.json`；JUnit 位于同名 XML。独立只读核对三个随机 MySQL 表均不存在，所有本轮子进程已终止，见顶层 `cleanup-verification.json`。本轮没有使用 Redis、没有创建容器，没有改正式配置表。

这些测试覆盖当前 MySQL 环境中的指定竞争窗口和客户端进程中断，不能外推为数据库服务在提交期间宕机、所有配置 Store 的历史迁移或完整 API 多实例启动验收。旧文件仍按现有规则保留。无需为这些已通过的边界增加新业务机制。
