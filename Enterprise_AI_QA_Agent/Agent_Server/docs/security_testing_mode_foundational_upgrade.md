---
trigger: manual
audience: security_testing_mode 底层能力升级
baseline: pentagi（渗透框架，Go）+ QA-Agent 开发铁律
depends_on: security_testing_mode_vs_pentagi_comparison.md
---

# 安全测试模式 · 底层升级文档（Foundational Upgrade Spec）

> 本文把 [对比文档](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/docs/security_testing_mode_vs_pentagi_comparison.md) 里识别出的差距，转成**可落地的底层子系统改造规格**：每一项给出「现状（引用真实代码）→ 根因判定（是补丁还是架构缺失）→ 目标 → 改造点（真实文件/方法/新增件）→ 数据契约与配置变更 → 验收标准」。
>
> **强制遵守 QA-Agent 铁律**：先查证再动手、根因修复不打补丁、错误完整回传模型、附加物是参考不是指令、完成以验证为准。任何一项落地前先读对应源码与 pentagi 对应实现。

---

## 0. 范围与非目标

**本次底层升级只做"执行内核 + 调度闭环 + 记忆召回 + 安全硬门"四块地基**，不追求一次补齐所有渗透工具。

- ✅ 在范围内：执行环境子系统（长任务/文件通道/受控自由命令）、调度动态闭环（refiner/reflector/mentor）、记忆召回（成功模式复用）、worker 输出语义压缩、授权硬门、数据契约与配置扩展。
- ❌ 非目标（后续单独立项）：完整 C2/后渗透编排、横向移动自动化、GraphQL 实时订阅重构、ChainAST 全量替换。

**改动边界（防止范围扩张）**：仅触碰 `Agent_Server/src/modes/security_testing_mode/` 与 `Agent_Server/src/application/security/` 两个目录，以及 `core/config.py` 的 `security_runner_*` 段；不顺手改其他模式。

---

## 1. 历史事实基线（`32516b1b` 后、本文改造前）

以下均为本文实施前实读代码确认的历史事实，只用于说明改造起点；升级后的权威状态见第 7 节：

| 子系统 | 关键文件 | 现状事实 |
| --- | --- | --- |
| 执行环境 | [execution_environment_service.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/security/execution_environment_service.py) | `execute()` → `_run_in_docker()` 用 `docker exec -w <workdir> <container> sh -lc "<timeout 包裹的命令>"`；容器以 `tail -f /dev/null` 常驻；结果封装为 `SecurityCommandExecutionResult`；文件仅靠 `-v host:container` 卷挂载 + `_collect_output_artifacts()` 事后 rglob 采集。**无 detach、无 stdin 交互、无 PTY、每次 exec 独立无工作态。** |
| 调度 | [subagent_coordinator.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/subagent_coordinator.py) | `run_all()` 循环批调度；`_select_batch()`(依赖+风险排序、`_has_resource_conflict` 资源锁、并发≤`MAX_CONCURRENT_WORKERS=3`)；`_dispatch_batch()`→`_build_worker_spec()`→dispatch 子会话→`_wait_for_sessions()`→`_apply_worker_output()`；`_retry_failed()` 重试。**批与批之间不重规划，DAG 一次成型。** |
| 记忆 | [memory_service.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/memory_service.py) | 只有 `persist_campaign_observations()` / `build_campaign_observations()` **写入路径**，写 4 类 ObservationRecord（content 截断 1800 字符）。`campaign.target_fingerprint` 字段已存在。**无任何召回/读取路径。** |
| 阶段机 | [contracts.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/contracts.py) | 15 个 phase 常量；`FAMILY_EXPLOIT`→`exploit-workbench-runner`、`FAMILY_TRAFFIC_ANALYSIS`→`traffic-analysis-runner` 的 runner key **已在 `FAMILY_TO_RUNNER` 声明但无对应 command_profile 实现**（悬空能力）。 |
| 执行环境后端 | `core/config.py` | `security_runner_*` 系列配置齐全（backend/image/workdir/net_raw/net_admin/pull_policy/container_reuse/wrap_timeout 等）。 |

> 结论：本次升级是**在既有骨架上补齐"活"的执行与闭环**，多数是**架构缺失**（应新增子系统/字段），少数是**能力悬空**（已声明未实现），极少是补丁。

---

## 2. 子系统改造规格

### S1 · 执行环境子系统：长任务 + 文件通道 + 受控自由命令

**现状**：`execution_environment_service.py` 只支持"一条命令、同步等超时、事后捞文件"。

**根因判定**：**架构缺失**。长扫描/爆破/交互式利用需要"后台执行 + 轮询 + 双向文件"，这是执行内核的能力空洞，不能用"把 timeout 调大"这种补丁绕过。

**目标**：让执行环境达到 pentagi `terminal.go` 的等效能力（对照 `项目借鉴/pentagi/backend/pkg/tools/terminal.go`、`backend/docs/docker.md`）：detach 后台、文件上下行、可选受控自由命令。

**改造点**（`execution_environment_service.py`，新增方法，不改坏 `execute()` 现有签名）：

1. **detach 后台执行**——新增 `execute_detached()` 与 `poll_execution()`：
   - `execute_detached()`：`docker exec -d` 启动，命令 stdout/stderr 重定向到容器内 `/<workdir>/.jobs/<job_id>.log`，立即返回 `job_id`（对齐 pentagi detach 的 500ms 探测即返回）。
   - `poll_execution(job_id)`：`docker exec cat` 读回日志 + 判活（`ps`/退出码文件），返回增量输出与是否结束。
   - **铁律对齐**：轮询必须回传**完整**已产生的 stdout/stderr，禁止只给"运行中"（错误可见性红线）。
2. **文件通道**——新增 `put_file()` / `get_file()`：
   - 用 `docker cp`（或 `tar` 管道，对照 pentagi file 工具的 tar API）实现宿主↔容器双向拷贝，替代现在"只能事后 rglob 卷目录"的单向采集。
   - 路径校验：目标路径必须落在 `container_workdir` 之下，防目录穿越（安全红线 2.5）。
3. **受控自由命令通道**（高风险，默认关闭）——`execute()` 增加 `free_form: bool = False` 与 `approval_token` 入参：
   - 仅当 `security_runner_allow_free_command=true` **且** 命令携带有效审批（interrupt/resume 通过）时才允许非 profile 命令；否则维持现有 profile 白名单。
   - `// No upstream counterpart to our approval gating (checked: pentagi terminal has no approval; our platform requires it):` 标注自创门控理由。

**数据契约变更**：`SecurityCommandExecutionResult` 新增可选字段 `job_id: str = ""`、`detached: bool = False`、`is_running: bool = False`（向后兼容，默认值不影响现有调用方）。

**2026-10-31 契约修订（v4 实施记录 §151，用户已确认）**：上方布尔定义保留为历史规格；当前后台执行的 `is_running` 为 `bool | None`。`true` 仅表示本 Worker 的命令监督仍活跃；`false` 须有成功返回、确定未派发或完整容器 ID 查询确认不存在的依据；`null` 表示外部执行状态未知，不能作为已停止、可以重试或副作用已撤销的证明。新增默认值为 `unknown` 的 `execution_observation`，说明当前投影依据（`supervised`／`command_returned`／`not_dispatched`／`container_absent`／`unknown`）。非零 Docker CLI 返回也可能是传输错误，保留原退出码和输出，但不能单凭该退出码宣称外部进程停止。

正常执行、取消、失租后的读取及历史快照共用同一状态投影。历史数据不回写；没有上述观测依据的历史快照按完整容器 ID 只读查询，不用容器名、短 ID、ToolJob 终态或 `DockerResourceSession.active=false` 猜测停止。查询失败、目标仍存在或身份缺失时返回 `null`；轮询不结算任务、不重跑命令、不触发清理。消费者须使用 `is_running is False`（TypeScript 为 `=== false`）判断已知结束，不能使用 `not is_running`／`!is_running`。新快照带新增观测字段，旧 Worker 的 dataclass reader 不具备读取该字段的能力；混合版本部署／回滚前须先升级读取方或隔离路由，不能把旧制品当作兼容读者。本轮没有改生产路由、数据库结构或任务调度。

**2026-10-31 身份与数据所有权补充（v4 实施记录 §153）**：上述查询及已知观测的复用，均须核对请求 job ID、持久 metadata 的完整容器 ID、原 `initial_result` 与当前快照的执行身份；本机活跃监督还须匹配启动时捕获的不可变身份。只读取既有 producer 保存的精确 `docker exec -w <cwd> <full-id> sh -lc <script>` argv，不解析命令文本、不猜其他 argv 形式。材料缺失或漂移返回 `null／unknown` 并记录原因，不查询可疑目标、不回写历史、不改变既有审批／恢复资格。监督心跳与结算使用捕获的原 job ID，不能由可变结果选择另一任务。启动及轮询返回独立深拷贝，调用方修改 argv 或嵌套 artifact 不得污染内部监督／账本。本步不新增公开字段、DDL、执行权或停止权限；这只是既有证据的一致性核验，不是防篡改签名或真实 Runner 的目标接受回执。

**2026-10-31 资源清理恢复补充（v4 实施记录 §154）**：清理 Worker 在删除容器后、提交 receipt 前退出，原 `running` receipt 不重新领取或重派删除。既有 Handler 可调用 Docker adapter 的只读 `observe_absence()`：须有原 allocation 的持久完整容器绑定，核对租约／代次及请求谱系，再以完整 ID 查询；目标仍在、查询不确定、未绑定或身份漂移均不释放配额。确认缺失后，将带 namespace、allocation ID、完整容器 ID、观察者、时间和窄 scope 的观测存入原 receipt JSON，并在同一 PG 短事务核对身份后标记 completed；保留原 Worker 和 attempts，再复用已有 Redis fenced finalization／ACK。PG 回执未确认或 Redis 失败仍允许重复只读核验／最终确认，不再次执行删除，不改原 ToolJob 结果。已有回执的领取与复用均校验资源身份。无 DDL／公开 API／DTO 变更，旧无观测回执仍可读取；这不证明未绑定 allocation 不会迟到创建，也不证明命令成功或副作用被撤销，浏览器及其他 Runner 不自动获得该资格。

**2026-10-31 清理调用与提交边界补充（v4 实施记录 §155）**：adapter 成功返回后，`completed` receipt 写入失败或提交确认丢失，不再尝试写 `failed`，也不提前释放 Redis 配额；保留原 running／已提交 completed，记录不含异常正文的 `resource_cleanup_result_uncommitted` 并传播原异常。既有 Stream 可转交同一任务的投递后继，但不能将其当作新的清理执行权：running 走 §154 只读观测，terminal 只补 fenced finalization／ACK，保留原 Worker／attempts。adapter 本身的异常仍沿原有显式幂等重试处理；取消仍传播且不降级为 failed。本步不把所有异常改成待对账，不增加调度／迁移／公开字段，也不扩大其他 Runner 的查询或补偿资格。

**验收**：
- 起一个 `sleep 30 && echo done` 的 detached job，`poll_execution` 能在结束后读到 `done` 全量输出；
- `put_file` 上传一个字典文件、`get_file` 取回 nuclei 输出文件，内容一致；
- 自由命令通道在未审批时被拒绝并留日志。

---

### S2 · 调度闭环子系统：Refiner 重规划 + Reflector 纠偏 + Mentor 强反馈

**现状**：`run_all()` 按预生成 DAG 批量跑完；已有 `execution_monitor` 做限流但非"主动换路线"。

**根因判定**：**架构缺失**（缺重规划回路）+ **能力升级**（execution_monitor 语义增强）。pentagi 的核心优势正是"每个 subtask 后 Refiner 重规划 + Reflector 纠偏 + Mentor 监督"（对照 `backend/docs/flow_execution.md`）。

**目标**：把"一次成型 DAG"升级为"边跑边改"的收敛循环。

**改造点**（`subagent_coordinator.py`）：

1. **Refiner 动态重规划**——在 `run_all()` 每批 `_dispatch_batch()` settle 之后、下一轮 `_select_batch()` 之前，插入 `await self._refine_after_batch(settled_tasks)`：
   - 复用现有 [subtask_refiner.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/subtask_refiner.py)，把本批新 findings/execution_records 喂给它，允许**追加/删除/降级**后续 task（写回 task_pool）。
   - 硬上限：受 `MAX_CAMPAIGN_TASKS=50` 约束，重规划新增不得越界（防失控膨胀）。
2. **Reflector 纠偏**——在 `_apply_worker_output()` / `_extract_runner_output()` 判定 worker 返回**非结构化/无 runner 输出**时，新增 `_reflect_and_retry(task)`：
   - 用一次轻量重发（对齐 pentagi Reflector ≤3 次），提示 worker 必须走 runner 工具并返回结构化结果；超过 3 次才 `_fail_task()`。
   - **从宽原则**：只在"确实拿不到结构化结果"时触发，不误杀有结果只是格式略偏的情况。
3. **Mentor 强反馈**——把 [execution_monitor.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/security/execution_monitor.py) 的 `analyze_settled_tasks()` 输出接进调度：同 profile 连续失败达阈值（现有 `max_consecutive_runner_failures=2`）→ 向后续同类 task 注入 `APPROACH CHANGE REQUIRED` 提示并**换 profile/worker**，而非无限重试（对齐 QA-Agent 铁律"失败≥3 次换根本路线"）。

**数据契约变更**：`SecurityTask` 增加 `refine_origin: str = ""`（标记该任务由哪次重规划产生，便于回放）、`reflect_attempts: int = 0`。见 [campaign_state.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/campaign_state.py)。

**验收**：
- 构造"recon 发现新端口 → refiner 自动追加针对该端口的 task"的用例，确认新 task 入池并执行；
- 构造 worker 返回纯文本用例，确认触发 reflect 重试而非直接失败；
- 构造同 profile 连续失败，确认升级为换路线而非死循环。

---

### S3 · 记忆召回子系统：成功攻击模式复用（Memory-first）

**现状**：`memory_service.py` **只写不读**；`campaign.target_fingerprint` 已具备但未用于召回。

**根因判定**：**架构缺失**（缺召回闭环）。这是与"专业"差距最大的一块（对比文档 2.4 标 🔴）。

**目标**：规划阶段"先查记忆库，按目标指纹召回历史成功 profile/命令"，对齐 pentagi 的 `SuccessfulToolsSearch` + Memory-first（对照 `backend/pkg/graphiti/client.go`、`backend/pkg/tools/graphiti_search.go`）。

**改造点**：

1. **复用平台既有能力，零新依赖**（选型有据：项目已在用 pgvector 与 memgraph）：
   - 向量召回：复用 `infrastructure/postgres_vector_memory_store.py`（pgvector）；
   - 图谱：复用技术栈内已有的 `memgraph_runtime.py`（可选，二期）。
2. **memory_service.py 新增读取路径** `recall_successful_patterns(target_fingerprint, surface_types, top_k)`：
   - 按 `target_fingerprint`（技术栈/端口/服务指纹）+ tag `security_testing`+`tool_execution`+`success` 召回历史成功 execution_records；
   - 返回"该类目标上，哪些 profile/命令曾成功产出 finding"。
3. **接入规划**：[recon_planner.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/recon_planner.py) / [vulnerability_planner.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/vulnerability_planner.py) 生成计划前调用召回，把结果作为**参考性**上下文注入 planner prompt。
   - **铁律对齐**：召回是"参考不是指令"——附"环境中该 profile 反复失败即放弃"逃生条款，不得因历史成功就强制执行。

**数据契约变更**：写入侧 `_execution_observation()` 已含 `command`/`success`/`tool_name`；为提升召回质量，`_observation()` 的 `metadata` 增加 `profile_key` 与 `produced_finding: bool`。

**验收**：同一目标指纹跑两次，第二次规划阶段能召回第一次成功的 profile 并在 prompt 中体现；召回失败/为空时规划正常降级（不报错）。

---

### S4 · Worker 输出语义压缩（替代硬截断）

**现状**：`memory_service._observation()` 对 content 做 `truncate_text(..., 1800)` 硬截断；worker 巨量工具输出（nuclei/nmap 全量）信息损失严重。

**根因判定**：**能力升级**。项目已有 `application/context/context_compaction_service.py` 与 `transcript_hygiene_service.py`，安全模式未接入（对比文档 2.5）。

**目标**：长工具输出走**语义摘要**而非截断（对照 pentagi csum "工具结果 >16KB 自动摘要"）。

**改造点**：
- 在 `_apply_worker_output()` 落库前，对超过阈值（建议 16KB，对齐 pentagi `MaxBPBytes`）的 stdout 调用 `context_compaction_service` 语义摘要，保留关键结构（端口/服务/CVE/URL），再进 observation 与 campaign_state。
- 复用既有服务，**不新造压缩器**（选型有据）。

**验收**：喂一份 >16KB 的 nuclei 输出，确认落库的是"保留了 CVE/URL 的摘要"而非头部截断。

---

### S5 · 悬空能力落地 + 工具动态装配

**现状**：`FAMILY_EXPLOIT`/`FAMILY_TRAFFIC_ANALYSIS` 的 runner key 已声明，但 [command_profiles.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/security/command_profiles.py) 无对应 profile；`SECURITY_EXPLOIT_CODER` 角色无执行落点；不能运行时装工具。

**根因判定**：**能力悬空**（声明未实现）。

**目标**（分档，受授权与合规约束）：
1. 二期：为 traffic_analysis 补只读 profile（如 `tcpdump` 限时抓包）、为 exploit 补**受控**低风险 profile（如 `searchsploit` 已有、`msfconsole -x` 的只读信息模块），全部 `requires_approval=true`。
2. 三期：`Installer` 能力——新增 `security_runner_tool_bootstrap` 配置，允许在容器内按需 `apt-get install`/`pip install` 缺失工具（对照 pentagi Installer Agent），失败回退并完整回传错误。

**验收**：新增 profile 能通过 `SecurityCommandProfileRegistry.build_command()` 正确渲染；未授权时被审批门拦下。

---

### S6 · 授权与安全硬门（贯穿所有子系统）

**现状**：已有 `execution_safety_policy.py`/`risk_policy.py`/审批，但对比文档指出缺"目标 allowlist 强校验硬门"。

**根因判定**：**架构缺失**（缺越界即拒的硬门）。

**目标**：对齐性能模式的 `performance_target_allowlist` 思路，安全模式所有执行前**强制校验目标在 allowlist 内**。

**改造点**：
- `core/config.py` 新增 `security_target_allowlist: str = ""`（逗号分隔，空表示不限制但记警告）。
- 在 `_dispatch_batch()` 派发前、`execute()` 执行前双重校验目标域名/IP 属于 allowlist；越界→拒绝 + 结构化日志 + 不执行（安全红线）。
- 自由命令通道（S1-3）、exploit profile（S5）额外要求 interrupt/resume 审批。

**验收**：目标不在 allowlist 时任务被拒且留痕；在 allowlist 内正常执行。

---

## 3. 汇总：新增/变更清单

**配置项（`core/config.py`，`security_runner_*` 段扩展）**

| 配置 | 默认 | 用途 |
| --- | --- | --- |
| `security_runner_allow_free_command` | `false` | 是否允许受控自由命令（S1-3） |
| `security_target_allowlist` | `""` | 目标 allowlist 硬门（S6） |
| `security_runner_detach_poll_interval_seconds` | `3.0` | detach 轮询间隔（S1-1） |
| `security_runner_output_summary_threshold_bytes` | `16384` | 输出摘要阈值（S4） |
| `security_runner_tool_bootstrap` | `false` | 是否允许运行时装工具（S5，三期） |

**数据契约（`campaign_state.py` / `execution_environment_service.py`）**
- `SecurityCommandExecutionResult`：+`job_id` / `detached` / `is_running`
- `SecurityTask`：+`refine_origin` / `reflect_attempts`
- ObservationRecord metadata：+`profile_key` / `produced_finding`

> 所有新增字段均带默认值，**向后兼容**；改动 `SecurityCommandExecutionResult` 后需全局搜索其构造点核对（契约同步纪律 2.2）。

---

## 4. 分期实施路线（含验收门）

| 期 | 子系统 | 交付 | 验收门 |
| --- | --- | --- | --- |
| **一期（地基）** | S2 Reflector/Mentor、S4 摘要、S6 allowlist 硬门 | 低风险、纯编排/安全增强，不碰执行内核 | 现有 `tests/` 全绿 + 新增 3 个单测（reflect/摘要/allowlist） |
| **二期（执行力）** | S1 detach+文件通道、S2 Refiner 重规划、S3 记忆召回 | 执行内核与调度闭环增强 | detach/文件/召回各 1 集成测试；同目标二次跑能复用 |
| **三期（专业渗透，需授权）** | S1 自由命令、S5 exploit/traffic profile + Installer | 受控高风险能力 | 全程审批门 + allowlist 双校验通过；合规评审 |

**每期完成的 Definition of Done**（QA-Agent 第四章）：
- [ ] `python -c "from src.main import app"` 导入自检通过（PYTHONPATH 设为 `Agent_Server`）；
- [ ] `pytest tests` 相关用例实际运行且通过（贴真实输出）；
- [ ] 改 `SecurityCommandExecutionResult` 后所有构造点已核对；
- [ ] 新核心路径补了结构化日志（状态转换/执行/审批/降级）；
- [ ] 无调试残留、无未声明 TODO。

---

## 5. 铁律对齐 Checklist（落地时逐条自检）

- [ ] **先查证**：每个改造点先读对应源码 + pentagi 对应文件，方案里写「pentagi 位置 → 本项目对齐方式」。
- [ ] **根因非补丁**：detach/召回/allowlist 都作为**新子系统/新字段**，不在 `docker exec` 外套 if。
- [ ] **错误可见**：detach 轮询、自由命令、装工具失败都完整回传 stdout/stderr。
- [ ] **失败升级**：Mentor 在连续失败时强制换路线，不无限重试。
- [ ] **附加物是参考**：记忆召回、skill 提示用参考性措辞 + 逃生条款，不门禁内置能力。
- [ ] **安全红线**：allowlist 硬门 + 高危审批 + 路径穿越校验 + 密钥/payload 脱敏日志。
- [ ] **契约同步**：新增字段后全局搜索构造/消费点核对。
- [ ] **诚实验证**：每期跑测试贴真实结果，跑不了的明确告知需本地执行的命令。

---

## 6. 关键文件索引

**本项目改造落点**
- 执行环境：[execution_environment_service.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/security/execution_environment_service.py)、[command_profiles.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/security/command_profiles.py)、[execution_monitor.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/application/security/execution_monitor.py)
- 调度：[subagent_coordinator.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/subagent_coordinator.py)、[subtask_refiner.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/subtask_refiner.py)、[task_pool.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/task_pool.py)
- 记忆：[memory_service.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/memory_service.py)
- 规划：[recon_planner.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/recon_planner.py)、[vulnerability_planner.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/vulnerability_planner.py)
- 状态/契约：[campaign_state.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/campaign_state.py)、[contracts.py](file:///g:/Code_Warehouse/Ai_Test_Agent/Enterprise_AI_QA_Agent/Agent_Server/src/modes/security_testing_mode/contracts.py)
- 配置：`core/config.py`（`security_runner_*` / 新增 `security_target_allowlist`）

**PentAGI 对齐信源**
- 执行：`项目借鉴/pentagi/backend/pkg/tools/terminal.go`、`backend/docs/docker.md`
- 调度/agent：`backend/docs/flow_execution.md`、`backend/docs/controller.md`
- 记忆：`backend/pkg/graphiti/client.go`、`backend/pkg/tools/graphiti_search.go`、`backend/docs/database.md`
- 压缩：`backend/docs/chain_summary.md`

---

_本文为设计规格，非代码变更。任何一项落地前，先按第 5 节 Checklist 查证 pentagi 对应实现与本项目源码现状，再动手；完成后按第 4 节 DoD 实测验证并如实汇报。_

---

## 7. 2026-08-01 实施与验证记录

### 7.1 已落地

- **S1 执行环境**：Docker runner 支持 detached 执行与轮询、宿主机/容器双向文件通道、显式容器清理、路径穿越校验、localhost 到 `host.docker.internal` 重写；受控自由命令默认关闭，开启后仍要求服务端审批标记、显式目标、可执行文件白名单和破坏性模式拦截。
- **S2 调度闭环**：每批任务 settle 后执行 Refiner，可追加、删除、降级后续任务并受 `MAX_CAMPAIGN_TASKS=50` 限制；无结构化 runner 输出触发有界 Reflector；连续失败触发 Mentor 的 `APPROACH CHANGE REQUIRED` 换路线提示。真实/自动化场景证明新发现端口可生成并执行后续服务探测任务。
- **S3 记忆召回**：规划前按服务端目标指纹跨会话召回成功 profile；写入 metadata 包含 `mode_key`、`profile_key`、`surface_type`、`produced_finding`；低风险请求不会因历史结果引入中高风险 profile，召回结果仅作参考。
- **S4 输出压缩**：超过阈值的 runner 输出走结构感知、信号保留压缩，保留 URL、CVE、端口、服务、状态和错误，不再简单截取头部。
- **S5 悬空能力**：新增 `tcpdump_timed_capture`、`searchsploit_exploit_lookup`、`msf_module_info`、`free_command` profile；traffic/exploit runner 均可见但具体 profile 仍在参数级审批门重新判定；Docker backend 支持固定映射的工具 bootstrap，默认关闭。
- **S6 安全硬门**：目标 allowlist 在任务派发与执行环境两层校验；子会话只继承服务端持久化的可信授权与资源范围；自由命令和需审批 profile 只接受 `resume_after_approval()` 注入的 `_server_approval_granted`，不信任用户传入的 `approved` 等字段。
- **中断传播**：父会话 interrupt 会取消 Coordinator 持有的协程、把运行中子会话标记为 interrupted、停止新 Worker 派发，并把安全专用运行时快照保存为 `interrupted`。
- **可观测性**：阶段转换、checkpoint、执行策略、批次选择、Refiner、记忆召回、Worker 派发/收口、Runner 启停、审批和中断均有可检索结构化日志。
- **模型路由**：显式指定且已启用、并被 Agent 支持的模型优先于全局默认模型；安全主控和各 Worker 均支持 `qwen3.6-plus`。

### 7.2 明确保留的非目标与限制

- 无持久 PTY、SSH 连接池、反连端口编排、完整 C2、后渗透和横向移动自动化；这些仍属于本文第 0 节明确排除的范围。
- bootstrap 是服务端固定工具/包映射，不是 PentAGI 的通用 Installer Agent；默认关闭，尚未做真实 `apt-get` 端到端验证。
- 输出压缩是确定性的信号保留压缩，不是 LLM 语义摘要或完整 ChainAST/csum 实现。
- 记忆闭环使用平台现有 pgvector 检索；尚未增加 Graphiti 风格的独立安全情景图谱。
- 专用安全运行时的 interrupted 快照不可恢复为原 Campaign 继续执行；当前语义是终止并保留证据，而不是暂停/续跑。

### 7.3 自动化验证

使用 `E:\PyThon\Anaconda_PyThon\envs\Python3.11\python.exe` 执行安全模式定向测试：

```text
75 passed, 1 skipped in 5.81s
```

其中覆盖 detached/file round-trip、目标 allowlist、输出压缩、Refiner/Reflector/Mentor、同指纹记忆召回、可信授权继承、服务端审批标记、Coordinator Worker 取消、interrupted 快照、低/高风险审批策略、URL 句末标点归一化与显式模型选择优先级。

完整后端测试结果：

```text
284 passed, 1 skipped, 2 failed in 9.78s
```

两个失败均来自 `test_compatibility_runner_service.py` 在 Windows 深目录下写测试产物时超过路径长度，抛出 `FileNotFoundError`；失败调用链位于兼容性 Runner，与本次安全模式变更无关。`python -m compileall -q src` 与 `git diff --check` 均通过。

### 7.4 后端会话 API + Qwen + Kali 端到端证据

所有场景均通过 `/api/v1/sessions` 与 `/api/v1/sessions/{id}/messages` 发起，父会话和 3 个 worker 均实际使用 `qwen3.6-plus`，执行策略为 `subagent_session`。

| 目标 | 父会话 | 结果 | 真实证据 |
| --- | --- | --- | --- |
| `http://localhost:8089` 基线 | `03bf52df-e757-448f-a83a-d904291cc971` | 3/3 task 完成，4 个低风险发现 | HTTP 302、跳转 `/auth/login`、识别 XXL-JOB、响应头缺失；Kali 实际执行 `httpx`/`whatweb`/header probe |
| `http://localhost:8089` 同目标二次运行 | `8192f435-2584-4e0f-a6e4-2d76309ed22c` | 3/3 task 完成；规划前召回 3 个成功 profile | 日志记录 `recalled_profiles=['http_headers_probe','whatweb_fingerprint','httpx_probe']`，并按低风险约束重新执行 |
| `http://localhost:3000/` | `7f0ba1e2-26c7-45d0-87ef-147316ad6075` | 3/3 task 完成，4 个低风险发现 | HTTP 200、标题 `blog-web`、HTML5/module 脚本、响应头缺失；Kali 实际执行 3 个 profile |
| 运行中 interrupt | `cd5ba722-2580-4108-9730-56aafce51922` | 父会话和已启动子会话均 interrupted；Worker 数保持 1 | 其余 2 个 Worker 未派发；日志有 `security_coordinator_interrupted`，快照 stage 为 `interrupted` |
| 高风险审批恢复 | `de695926-6f34-477c-9d52-97144b31c906` | `waiting_approval` → API approve → completed | `exploit-workbench-runner/searchsploit_exploit_lookup` 生成 scope hash；批准后 Kali 中 `searchsploit` exit code 0 |

`localhost:3000` 原服务仅监听 Windows `::1`。验证时使用临时用户态 IPv4 到 IPv6 HTTP 代理，并保持 `Host: localhost:3000`；验证后已停止代理，端口恢复为仅 `::1:3000`。Qwen 仅在验证窗口临时设为活动模型，结束后已恢复原活动模型 `agnes-2.0-flash`。最终 `docker ps -a --filter name=qa-security-runner` 为空，所有测试 Runner 均已销毁。

### 7.5 验收审计

| 规格项 | 当前结论 | 证据 |
| --- | --- | --- |
| S1 detached/file/free command | 已实现本文范围内能力 | 自动化 round-trip、路径穿越、自由命令门控测试；真实 Runner 使用 Docker 并清理 |
| S2 Refiner/Reflector/Mentor | 已实现 | 动态 8443 服务任务测试、格式纠偏/换路线测试、批次日志 |
| S3 memory-first | 已实现 | 同指纹单测 + 会话 `8192f435-...` 的 PostgreSQL 跨会话召回日志 |
| S4 输出压缩 | 已实现确定性信号保留版本 | >16KB/CVE/URL/端口保留测试 |
| S5 traffic/exploit/bootstrap | profile 与门控已实现；通用 Installer 不在范围 | profile 渲染测试、真实 searchsploit 审批恢复；bootstrap 默认关闭 |
| S6 allowlist/授权硬门 | 已实现 | 双层 allowlist 测试、可信授权继承测试、服务端审批标记测试 |
| 多场景真实 API | 已验证 | 8089、8089 recall、3000、interrupt、approval 五个父会话 |
| 环境恢复 | 已完成 | Agnes 恢复活动；IPv4 代理停止；零 `qa-security-runner` 容器 |
