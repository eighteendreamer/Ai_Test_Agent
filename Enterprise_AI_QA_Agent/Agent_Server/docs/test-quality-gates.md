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

## 独立框架 Worker 超时诊断

`tests.framework_worker_diagnostics.framework_worker_process` 是轻量 spawn 入口；原 `tests.test_framework_task_control.framework_worker_process` 继续导出同一函数，参数不变。默认保持 WARNING 日志；无论开关状态都不安装或触碰进程级 watchdog。排查 v4 C/H07 的非稳定强杀／接管超时时，可以在确认本地测试 PG/Redis 可用后显式设置 `QA_FRAMEWORK_WORKER_DIAGNOSTICS=1`：

```powershell
# 工作目录 Agent_Server；只使用已有隔离测试环境，不启动生产服务。
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:PYTHONIOENCODING='utf-8'
$previousDiagnostics=$env:QA_FRAMEWORK_WORKER_DIAGNOSTICS
$env:QA_FRAMEWORK_WORKER_DIAGNOSTICS='1'
# 每次选择新的私有日志名，保留原失败，不覆盖或把重跑当成首次全绿。
New-Item -ItemType Directory -Force -Path ../.codex-logs | Out-Null
$log=Join-Path ../.codex-logs ("framework-worker-" + [guid]::NewGuid().ToString('N') + '.log')
try {
  ../.venv-framework-v2/Scripts/python.exe -u -m pytest `
    tests/test_framework_task_control.py tests/test_framework_approval_recovery.py `
    -vv -s -rs --tb=short *> $log
  $result=$LASTEXITCODE
  Get-Content -LiteralPath $log -Encoding UTF8
} finally {
  $env:QA_FRAMEWORK_WORKER_DIAGNOSTICS=$previousDiagnostics
}
exit $result
```

这个开关只被测试子进程 helper 读取，不是生产 Settings 配置，也不会启用模型、目标或生产路由。当前共用该 helper 的原生审批、原结果投影和仅结算 Worker 均可沿原调用使用；其他自建进程入口不会自动获得此诊断。

启用后：

- 沿现有 `configure_logging` 输出 INFO 生命周期事件，以 task／attempt／worker 关联原领取、执行、原生继续、收尾及资源释放；不新建一套业务 Trace。
- 在子进程函数入口、原重型 fixture 模块导入后、作用域退出时记录 `framework_test_worker_entered`／`framework_test_worker_fixtures_loaded`／`framework_test_worker_exited`。后两者的 `operation_seconds` 均为自进入作用域起的累计耗时，能区分 fixture 导入前后；不是完整解释器启动时间或每个业务操作的精确耗时。
- `error_type` 只记录未处理异常类型，不记录 Settings、绑定、凭据或异常正文；原异常／取消原样传播。退出事件不等于业务成功，强杀不会运行 finally；没有后台采样线程，不终止、取消、重试或延长执行。

本轮曾试用原生 `faulthandler.dump_traceback_later`，但当前 CPython 3.11.16 在真实 Worker 和独立导入实验中都出现采样期间 `0xC0000005` 崩溃，故已撤掉该路线，不作为诊断命令提供。官方 [CPython #158200](https://github.com/python/cpython/issues/158200) 也报告了采样读取代码元数据时的崩溃风险，但版本／系统与本机不同，不能据此宣称已确定本机原生缺陷的精确根因。旧失败日志保留，见实施记录 §107。

需要单独检查导入链时，使用现有解释器的标准选项，不执行 Worker 或 pytest fixture；每次保留新日志：

```powershell
$log=Join-Path ../.codex-logs ("framework-import-" + [guid]::NewGuid().ToString('N') + '.log')
../.venv-framework-v2/Scripts/python.exe -X importtime -c "from tests.test_framework_task_control import _run_framework_worker; print('fixture_import_completed')" *> $log
$result=$LASTEXITCODE
Get-Content -LiteralPath $log -Encoding UTF8
exit $result
```

`-X importtime` 自身有测量开销，不是生产性能基线，也不代表所有 spawn 阶段。阶段日志不覆盖解释器启动、spawn 准备、轻量模块自身／日志设施导入和参数反序列化，不提供线程栈或完整 asyncio await 树。原父进程期限、租约、强杀点、owner 判定和原生消息断言保持不变。既有 INFO 日志不承诺自动脱敏全部业务上下文，原始输出仅保留在私有证据目录，归档前另行脱敏。没有捕获失败首因前，即使复验通过，也不能宣称历史 C/H07 超时已经修复。

依据：本机 CPython `multiprocessing.spawn._main` 在调用进程 target 前先反序列化进程对象；沿 [Python 3.11 spawn 约定](https://docs.python.org/3.11/library/multiprocessing.html#the-spawn-and-forkserver-start-methods) 将 target 留在轻量模块；[Python 3.11 命令行说明](https://docs.python.org/3.11/using/cmdline.html#cmdoption-X) 与本机 `--help-xoptions` 确认 `-X importtime`。

## 原生 transcript 的 Session metadata 边界（v4 B／§5.2 子集）

`framework_transcript_asset` 是服务端发布且不面向展示的 metadata 字段：公共 Session Create／Update 均拒绝该键（HTTP 400），包括摘要合法的完整资产、空对象和 null；拒绝时不保存其他偏好／metadata，也不追加偏好变更事件。`session_metadata_write_rejected` 日志只包含保留键名，不记录提交的正文或原生 provider 数据。

SessionDetail 使用既有内部字段过滤机制，不返回该字段；读取不删除或改写存储原件，普通 metadata 更新与可信服务端路径仍可用。Session API 契约测试还通过 Runtime 原有新 Turn 入口核对完整原生前缀，保留内容块、工具调用配对、provider 扩展字段与消息身份，不以脱敏后的展示文本重建运行历史。

```powershell
# 工作目录 Agent_Server；先确认现有测试 PG/Redis 可用，不启动服务。
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:PYTHONIOENCODING='utf-8'
../.venv-framework-v2/Scripts/python.exe -m pytest `
  tests/test_session_project_binding.py tests/test_session_store_value_contract.py `
  tests/test_deep_agent_runtime_adapter.py tests/test_framework_finalization_projection.py `
  -q -rs --tb=short
```

边界用例采用进程内 ASGI／内存 SessionStore 与合成原生消息；消费方回归另覆盖真实 PostgreSQL／Redis 的既有隔离 fixtures，不能把二者混称真实供应商验证。该项不证明快照、导出、追踪或其他 metadata 字段均完成隐私审计，也不为历史存量资产补发来源证明；覆盖锚点、legacy 夹层与完整跨 Turn 请求仍按 B 阶段单独验收。已保存的原始失败／成功日志不可覆盖，见实施记录 §108。

## 原生 transcript 的完成与协议闭合边界（v4 B／§5.1.1）

只有已完成的 Runtime 结果、完成快照与无 pending 状态才能发布下一 Turn 的原生资产。等待审批、部分审批、可恢复、失败或中断不覆盖已有完成资产；原 Turn 的完整待处理消息仍保存在原 checkpoint／pending_turn 中，不截断工具批次后伪装成可导入历史。

发布和新 Turn 导入通过 `framework_transcript.require_closed_transcript` 复用已有 `native_terminal.require_closed_native_messages`：拒绝待工具、孤儿／重复工具结果、无效调用及缺失调用身份；沿既有规则允许不同已闭合批次复用 call ID。不因资产摘要合法而跳过协议检查，不丢字段、改正文或回退到展示文本来获得通过。导入校验不 invoke 图／模型／工具，也不替代同 Turn 的 checkpoint 继续。

```powershell
# 工作目录 Agent_Server；使用既有隔离测试 PG/Redis，不启动服务。
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:PYTHONIOENCODING='utf-8'
../.venv-framework-v2/Scripts/python.exe -m pytest `
  tests/test_deep_agent_runtime_adapter.py tests/test_framework_native_terminal.py tests/test_session_project_binding.py `
  -q -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -m pytest `
  tests/test_framework_approval_graph_input.py tests/test_framework_approval_runtime.py `
  tests/test_framework_message_result_reconstruction.py tests/test_framework_finalization_projection.py `
  -q -rs --tb=short
```

审批生命周期回归运行官方 Deep Agents、真实本地 PostgreSQL／Redis 和受控模型／工具：初次等待、部分审批都保留上一份完成资产，最终批准／拒绝分支收口后才发布完整新资产，并验证下一 Turn 读取的完整原生前缀及工具效果次数。三类结构化日志只记录身份、阶段／原因、数量或异常类型，不记录正文／provider 扩展值；`framework_transcript_published` 仅在 Session 提交成功后记录。

这项修复不改变资产 schema／运行时绑定，不自动修剪或重发历史非法资产；协议不闭合的存量资产需要对账，不能当作完整上下文继续调用。业务覆盖锚点、legacy 夹层、模型协议兼容性和其余 B 阶段出口仍须单独完成。实际红测、夹具修正及验证结果见实施记录 §109。

## 原生 transcript 的业务覆盖与受理边界（v4 B／§5.1.2–4 子集）

`v2.1.0` 是不可变绑定选定的新上下文能力，不是默认路由切换。新版 Worker 同时理解原 `v2.0.0`；`FrameworkWorkerCapabilities.context_transcript_enabled=False` 明确不受理新版本，旧制品原有的严格版本比较也不放行它。旧绑定、旧资产、已冻结请求与恢复记录保留原语义，不通过客户端 context 开关升级格式。无绑定请求中的伪造 `runtime_binding` 不作为执行／发布依据；客户端提供的 `framework_transcript_preparation` 不成为服务端覆盖证明。

新路径由 `FrameworkContextService` 一次产生原生输入、单向展示投影和准备信息：按已受理 `user_message_id` 与所属 Turn 排序，保留晚落库的前驱回答；排除未来输入、queued、工具展示行和不具上下文资格的内容。相同正文但不同身份的消息均保留。已有 schema-2 原生前缀原样导入，只追加其明确覆盖范围后的合法业务消息和当前原身份，不用文本匹配猜覆盖范围。

准备信息随原 graph input／Runtime 结果资产冻结，记录完整业务覆盖映射、原生输入数量／摘要及绑定模型指纹。唯一 finalizer 仍只在完成且协议闭合时发布；schema-2 另外核对原输入前缀未漂移，并把实际业务输出身份映射到本 Turn 原生输出范围。等待审批保留旧资产，同 Turn 继续只读取原 checkpoint／准备信息，不重读 Session 当前档案装配。

claim 前依据真实阶段语义检查资产兼容性：提交时已有 `submitted` 记录，不能用“没有阶段记录”代替新回合判断。尚未提交图的普通消息检查格式／摘要／模型指纹／协议；已冻结图、审批、原结果恢复及 ACK-only 不因 Session 当前档案变化而重新装配。能力不足抛 `TaskDeferred`，保持 queued／attempt 不变；这不等于双制品部署或跨事务竞态已完成验收。

```powershell
# 工作目录 Agent_Server；先确认既有测试 PG/Redis 可用，不启动服务。
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
../.venv-framework-v2/Scripts/python.exe -u -m pytest `
  tests/test_framework_context_transcript.py tests/test_framework_context_runtime.py `
  tests/test_agent_v2_worker_contract.py tests/test_framework_message_stages.py `
  tests/test_runtime_binding_persistence.py tests/test_deep_agent_runtime_adapter.py `
  tests/test_session_project_binding.py tests/test_framework_graph_input_asset.py `
  tests/test_framework_model_policy.py -q -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -u -m pytest `
  tests/test_framework_approval_graph_input.py tests/test_framework_approval_runtime.py `
  tests/test_framework_message_result_reconstruction.py tests/test_framework_finalization_projection.py `
  tests/test_framework_native_terminal.py tests/test_agent_task_dispatch.py -q -rs --tb=short
```

新集成测试使用官方 Deep Agents、官方 OpenAI SDK、本机环回 HTTP、真实 PG／Redis 和受控工具。分别以无原生资产、带合成闭合原生前缀开局，执行两个完整新 Turn；首回合另覆盖逐项批准，校验等待资产不变、原准备信息续用、一次工具效果、真实请求消息顺序与后续原生前缀保留。SDK 测试响应使用唯一 response ID，不拿重复 fixture ID 冒充真实供应商消息身份。它不证明真实供应商接受合成签名，亦不是新版本独立 Worker 强杀或 E3／E4 验收。

schema-1 没有覆盖锚点，旧位置摘要没有身份映射；新路径明确要求迁移，既不猜锚点也不自动删除旧摘要。模型指纹不同明确拒绝；它不替代 provider／transport 的完整协议描述。框架改写已冻结前缀时明确要求摘要覆盖对账，不能继续声明旧映射有效。统一 PromptAssembly、完整摘要迁移／原生摘要对账、跨协议策略、其余隐私输出面和生产接入仍是 B／后续阶段的未完成出口。真实失败及最终验证结果见实施记录 §110。

## 新 Turn 的共享上下文装配（v4 B／§4、§5 子集）

`v2.2.0` 绑定启用 `TurnContextAssembly`；组合根只创建一份服务，legacy 图注册它的四个原节点，新版 Deep Agents 只调用 context → router → permission → prompt 准备链，不调用旧 planner 或顶层模型循环。原生输入继续由 §110 的身份装配负责，平台上下文放入系统 Prompt；当前输入不再经旧 user envelope 重复注入。模型选择漂移、缺少装配服务、缺少已受理投影或选中 Skill 正文均明确失败，不用 metadata／pilot Prompt 兜底。

实际 Prompt、选中 Skill 主正文快照、检索与观察原件、工具可见集合和有序完整 `ToolDescriptor` 随原 graph input 冻结。审批继续与原结果只读重建不重新读取可变 Skill、设置、Registry 工具描述或运行装配链；工具真正执行仍走当前治理与权限检查。`v2.2.0` 不重复启用官方 SkillsMiddleware，当前也不提供动态 Skill/reference 加载及 deferred tool expansion；包装文案不建议调用不存在的 legacy `skill` dispatcher。不能把这一步说成完整 Skills Harness 已完成。

新语义不覆盖 `v2.0.0`／`v2.1.0`。新版可导入合法 2.1 schema-2 原生前缀，2.1 reader 拒绝 2.2 来源；能力关闭的 Worker 在 claim 前拒领，queued／attempt 不变。能力声明不是生产灰度开关。共享节点事件采用 awaited durable ACK，与原 owner-bound SessionEventSink 配合；同步节点工厂的默认调用方式仍保留给既有消费方。新版 context／prompt 事件不输出用户正文或 Prompt 预览，其余隐私面仍待独立审计。

正式项目集成保留真实 UUID FK：先创建专用 project 表，通过真实 ProjectService 创建项目，再建立隔离 Session／task 表；清理顺序先移除依赖表，再只删除 fixture 自建 project 表，不向默认项目表写入。该用例实际发现原结果读取／审批继续／审批收尾的 SQL 原始 Session UUID 与任务 TEXT 身份比较不一致；相关 SQL 读取边界显式投影 `project_id::text`，与既有任务写入端一致，不删除项目检查、不改变数据库 schema。另验证真正改绑其他项目时，普通／审批原结果读取仍被拒绝。

```powershell
# 工作目录 Agent_Server；使用已有 .venv-framework-v2 和已运行的隔离测试 PG/Redis。
$env:RUN_LIVE_POSTGRES_TESTS='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
../.venv-framework-v2/Scripts/python.exe -u -m pytest `
  tests/test_framework_turn_context.py tests/test_framework_context_runtime.py `
  tests/test_framework_context_transcript.py tests/test_agent_v2_worker_contract.py `
  tests/test_framework_message_stages.py tests/test_runtime_binding_persistence.py `
  tests/test_deep_agent_runtime_adapter.py tests/test_session_project_binding.py `
  tests/test_framework_graph_input_asset.py tests/test_framework_model_policy.py `
  tests/test_prompt_assembly_service.py -q -rs --tb=short
../.venv-framework-v2/Scripts/python.exe -u -m pytest `
  tests/test_framework_approval_graph_input.py tests/test_framework_approval_runtime.py `
  tests/test_framework_approval_continuation.py tests/test_framework_message_result_reconstruction.py `
  tests/test_framework_approval_result_reconstruction.py tests/test_framework_message_asset_gap_recovery.py `
  tests/test_framework_approval_asset_gap_recovery.py tests/test_framework_finalization_projection.py `
  tests/test_framework_native_terminal.py tests/test_agent_task_dispatch.py `
  tests/test_framework_delta_replay_order.py `
  tests/test_framework_approval_recovery.py::test_separate_workers_reconcile_native_result_after_hard_kill_and_finish_sibling `
  -vv -rs --tb=short
```

P0 强杀函数现有两个 decision/pause 参数及三个 reconciliation 参数，共六个 node ID；历史只带 `[reconcile_after_commit]` 的单参数 node ID 已不存在。应先 collect 核实，不能把未找到用例的 collection error 当作测试通过。

新版本测试经过真实 Session 入口、官方框架／OpenAI SDK、本机 HTTP、PG／Redis，外部 Memory／Observation／Case／MCP／设置端口与工具为受控样本；包含两个新 Turn、首回合审批、原生前缀不变、冻结描述复用，以及原结果空窗后仅运行唯一 finalizer 和重复交付无额外效果。真实 legacy StateGraph 另验证共享节点及旧 Prompt 契约。既有独立 Worker 强杀回归仍是原 2.0，不证明 2.2 独立 Worker／双制品部署通过。摘要覆盖迁移、provider/transport 来源、完整 Harness、其余隐私面与生产接入仍保留；最终命令、失败归因和证据等级见实施记录 §111。

## Transcript provider／transport 来源契约（v4 B／§5.1.2、§6.1 子集）

v2.2 绑定必须包含非敏感的 `model_provider` 与 `model_transport`。Runtime 初始 state 的来源必须来自实际 `ModelConfigRecord`；`framework_model_protocol` 由服务端覆盖客户端 context，实际配置与绑定不一致时在装配前拒绝。发布的 schema-2/v2.2 原生资产同时保存来源，导入必须比较绑定来源；不得只凭 fingerprint、展示文本或摘要继续。

旧 v2.0/v2.1 绑定仍可缺省这两个字段，序列化时也省略缺失字段，旧 schema-2/v2.1 资产仍按原摘要校验读取；仅 v2.2 资产要求来源完整。新增字段不得改变缺省旧绑定的 fingerprint 或旧资产 digest。

```powershell
# 工作目录 Agent_Server；不启动服务。
$env:PYTHONIOENCODING='utf-8'
../.venv-framework-v2/Scripts/python.exe -m pytest `
  tests/test_framework_context_transcript.py tests/test_framework_turn_context.py `
  tests/test_runtime_binding_contract.py -q -rs --tb=short
# 56 passed，exit 0

# 需真实 PostgreSQL/Redis 与本机环回 SDK fixture 时显式开启：
$env:RUN_LIVE_POSTGRES_TESTS='1'
../.venv-framework-v2/Scripts/python.exe -u -m pytest `
  tests/test_framework_context_runtime.py::test_admitted_context_reaches_sdk_and_next_turn_reuses_the_published_native_asset `
  -q -rs --tb=short
# 8 passed，exit 0
```

该质量门只证明来源字段的冻结、拒绝和受控跨 Turn 消费；不证明真实供应商协议兼容、2.2 独立 Worker 强杀、摘要身份迁移或全量发布。
