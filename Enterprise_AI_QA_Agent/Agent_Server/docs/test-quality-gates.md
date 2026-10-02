# 后端测试分组与执行证据

在 `Agent_Server` 目录使用现有环境运行，不自动安装或升级依赖：

```powershell
uv run --no-sync python scripts/run_test_quality_gate.py --profile diagnostic
uv run --no-sync python scripts/run_test_quality_gate.py --profile postgres-core
uv run --no-sync python scripts/run_test_quality_gate.py --profile session-store
uv run --no-sync python scripts/run_test_quality_gate.py --profile sql-lifecycle
uv run --no-sync python scripts/run_test_quality_gate.py --profile redis-core
uv run --no-sync python scripts/run_test_quality_gate.py --profile browser-core
uv run --no-sync python scripts/run_test_quality_gate.py --profile docker-core
uv run --no-sync python scripts/run_test_quality_gate.py --profile model-hitl
```

| 分组 | 固定测试范围 | 环境与执行含义 |
| --- | --- | --- |
| diagnostic | 当前 `tests/` 的全部可收集用例，包含本机未入 Git 的用例 | 所有声明的 live 开关关闭；仍有未加开关的本机 Redis 用例，不能当作离线套件。跳过/排除/xfail/未执行记 `partial`，不是全部通过。 |
| postgres-core | `test_live_compaction_ownership.py` | 真实 PG/Redis 的压缩领取、续租、所有权与向量写入契约；不代表全部数据库或模型链路。 |
| session-store | `test_session_store_value_contract.py` | 内存与真实 PG 的值隔离、计数、正文、汇总回执、正常备份时间排序/筛选与 UTC 连接契约；项目和会话使用隔离表，未覆盖历史部署时间迁移或完整模型业务。 |
| sql-lifecycle | `test_lifespan_resource_cleanup.py` | 启用 PG/MySQL 开关，只读查询验证正式 lifespan 的池释放、共享所有权及无关配置保留；业务初始化用成功替身，另有受控异常/取消用例，不代表完整业务启动或生产关闭故障复现。 |
| redis-core | `test_live_redis_task_delivery.py`、`test_live_hot_memory_store.py` | 真实 Redis 队列、毒消息、保留和热记忆契约；按既有测试启用 PG 开关，PG 连接也须可用。 |
| browser-core | `test_live_browser_lifecycle.py` | 已安装 Chromium 的生命周期、作用域与边界契约；不代表前端 GUI、Electron 或真实模型端到端。 |
| docker-core | `test_live_resource_leases.py` | 本机 Docker、PG/Redis、测试镜像和资源租约；执行已有集成测试和其测试资源清理。 |
| model-hitl | `test_live_deep_agents_hitl.py` | 已配置模型、应用依赖与本机服务的 HITL；会实际调用模型并启动测试服务，仅在对应验收环境执行。 |

这些是显式核心分组，不是完整发布矩阵。录制、回放、Memgraph、向量重建、容量/soak、真实邮件供应商等需要继续定义和执行独立分组。单组成功不能自动批准发布；所有报告均保留 `release_ready: false`，没有汇总器将它们包装成发布通过。

局部回归使用独立 `targeted` 分组，不允许替换固定分组的测试范围：

```powershell
uv run --no-sync python scripts/run_test_quality_gate.py --profile targeted --target tests/test_integration_route_status.py
```

`targeted` 的 live 开关也全部关闭；测试路径和 pytest node ID可重复传入，但不接受 pytest 选项。没有暗藏的 Gemini、租约或其他排除项。环境分组遇到 skip、xfail、xpass、deselected、未执行或空套件，均不会返回通过；失败和收集错误保留 pytest 非零退出码。`diagnostic` 的部分覆盖允许退出 0，只表示实际执行的断言未失败，报告状态仍为 `partial`；不得把此退出码用作发布审批。

每次在 `artifacts/test-gates/<UTC时间>-<分组>-<唯一ID>/` 保存：

- `report.json`：Git HEAD、工作区是否有变更、平台/Python、已安装依赖版本、非敏感 live 开关、实际 pytest 参数、逐条 setup/call/teardown 结果、跳过原因、收集错误、退出码和所选测试文件哈希。清理失败优先于 call 成功；中断未执行的条目不会变成通过。
- `sources.json`：源码、测试、脚本与 pyproject 的 SHA-256 清单，额外列出未入 Git 的本机测试。不能仅凭 commit 号声称本机结果可由干净 checkout 重现。
- `junit.xml`：pytest 的标准 CI 结果。JSON 不复制失败堆栈、捕获日志、局部变量或凭据；JUnit/控制台失败信息按测试工具原行为输出，归档前遵守项目脱敏规则。

先保存 `running` 元数据，执行结束再写终态；进程被强杀留下 `running`，不能当作成功。重跑创建新目录，不覆盖原失败记录。配置凭据、`.env` 和模型密钥不进入版本清单；运行环境的完整隔离与配置签名仍需后续治理。

CI 可上传整个单次结果目录，并检查对应分组的 `gate_exit_code` 和 `status`。当前没有新增或宣称已运行 GitHub CI：本项目是父 Git 仓库中的子目录，其 `.github/workflows/code-governance.yml` 不位于 Git 根目录，且内容仅做治理扫描；实际托管 CI 的入口、运行器和完整发布矩阵仍待接入。

存储时间验证沿用 `storage_utils.ensure_utc_datetime` 的既有约定：无时区时间表示 UTC，带时区时间转换为对应的 UTC 时刻。应用 PostgreSQL 池通过 libpq `options=-c timezone=UTC` 固定连接时区，避免服务器/角色默认时区改变无时区 UTC 的含义；不修改数据库全局配置。内存排序和时间筛选采用同一比较规则，保留已有返回字段格式。此前在非 UTC 连接中写入的历史记录不会被自动纠正，需要结合原始事件来源核查，不能一律平移 8 小时。依据：[PostgreSQL 时区解释](https://www.postgresql.org/docs/current/datatype-datetime.html#DATATYPE-TIMEZONES) 与 [libpq options](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNECT-OPTIONS)。

依据：项目内 `项目借鉴/llm-testing-course/评测工程化与CICD.html` 的数据/标准/基线版本化与结果归档要求；pytest 官方 [公开 hooks/API](https://docs.pytest.org/en/stable/reference/reference.html) 和 [JUnit XML 输出](https://docs.pytest.org/en/stable/how-to/output.html#creating-junitxml-format-files)。不新增报告插件依赖。


前端会话事件的时间消费可以独立重放。先在 agent_web 运行 npm test 和 npm run build，再在 Agent_Server 运行：

```powershell
uv run --no-sync python -m scripts.frontend_time_browser_probe --output artifacts/test-gates/<本次唯一目录>/browser.json
```

探针使用已安装 Chromium，随机 loopback 端口、独立内存 SessionStore、正式 SessionService 创建/历史/SSE 路由和正式构建产物；启动目录/健康/工具任务列表为成功夹具，没有调用真实模型或供应商。UTC、上海、纽约各创建一个正常会话，比较实际 SSE 日志显示与同一事件 HTTP 历史的 UTC 时刻，并记录页面、HTTP、异步循环错误和产物 index SHA-256。异步循环错误保留原输出并使报告 partial/退出 1；成功仍 release_ready:false。用新目录保留每次结果，不覆盖失败记录。冻结的普通 API 样本位于 agent_web/tests/fixtures/session-created-utc.json，组件回归不伪造时间生成逻辑。
