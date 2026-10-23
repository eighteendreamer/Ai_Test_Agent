# Worker CLI 验收显式复用 Redis

用户询问的 `qa-execution-cli-da0faae73ae8438f` 来自 `tests/test_execution_worker_cli.py::isolated_runtime`。旧夹具在未传入 Redis URL 时自动启动本机缓存的 `eighteenyear/bigdata-pytorch:2.6.1` 镜像，只使用其中的 Redis，为 API/Worker 验收隔离任务队列。这个隐式回退过重，而且测试调用方容易遗漏环境变量。本轮只修改验收夹具，未修改产品配置、供应商协议或测试模式。

## 调研和修复

- 依据：该夹具已有 `redis_url` 参数、`QA_ISOLATED_RUNTIME_REDIS_URL` 环境变量以及带随机前缀的 Stream/Consumer Group。复用这个现有能力，删除 Docker 创建、端口查询和删除分支，不增加新服务。
- 先用缺失、空字符串、纯空白三种配置复现。回归在配置读取边界截住资源分配，旧实现三项均失败，未启动容器或创建数据库。
- 修复后必须明确指定测试 Redis，缺失配置直接报告失败，发生在读取应用配置、创建目录/数据库/进程之前。测试服务 URL 不回退到产品 Redis URL。
- 随机队列名在资源创建前固定，初始化失败的清理也只针对显式测试 Redis 中的随机队列。
- 搜索全部调用方：已有 `redis_url=` 和环境变量入口保留。各验收脚本对旧 `docker.json` 的引用仅用于扫描并复制已有文件，没有直接读取依赖；夹具不再生成该文件，其他实际 Docker 测试仍保存自己的命令证据。

## 实际验证

```powershell
$env:RUN_LIVE_EXECUTION_CLI_TESTS='1'
$env:QA_ISOLATED_RUNTIME_REDIS_URL='redis://127.0.0.1:6379/13'
$env:QA_APPROVAL_MODEL_BUDGET_CLI_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p2-execution-explicit-redis-current-20261023-01'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_execution_runtime_config.py tests/test_execution_worker_cli.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p2-execution-explicit-redis-current-20261023-01.xml
```

结果：**6 passed in 174.34s**。

三项无配置边界，加三个完整生产 API lifespan/独立 Worker CLI 场景：正常完成、模型预算耗尽、只读正式回执纠正。实际使用随机 MySQL 配置表、隔离 PostgreSQL 数据库、RustFS 原件和现有 Redis DB 13；模型和业务目标均为本机受控 HTTP 服务。

三个业务场景均确认审批跨 API 重启保留，创建订单的已确认结果跨重启保留，实际操作顺序为 POST → GET → DELETE，最终业务订单数为0。正常及纠正场景正式结果为 passed；预算场景按预期为 error，并保留原预算。终态重投新增模型调用为0，新增业务操作为0。不能将这些受控结果外推为默认 Step 的理解/规划质量。

两个改动 Python 文件实际编译通过，`git diff --check` 通过。环境未安装 ruff，未执行 Python lint；未改前端，未重复构建前端。

## 清理与边界

本轮未创建容器，结束后 `docker ps -a --filter name=qa-execution-cli-` 为空。MySQL/PostgreSQL/RustFS 由原夹具完成 teardown；本轮未另做所有存储的独立清理审计。

Redis DB 13 开始为0键，夹具完成后随机任务队列已删除，但遗留3个本轮 session 事件键、配额缓存和 agent fencing 计数。根据持久快照核对3个 session 身份，确认全部5个键属于本轮后精确删除，最终为0键，未使用 FLUSHDB。详见本地证据目录的 `cleanup-verification.json`。共享夹具的事件/配额/fencing 收尾仍需调用方核对，本改动不把这项历史清理边界宣称为已经自动化。

升级整体仍未完成，默认 Step 最近的真实失败为订阅不可用，认证状态按用户说明为已完成。
