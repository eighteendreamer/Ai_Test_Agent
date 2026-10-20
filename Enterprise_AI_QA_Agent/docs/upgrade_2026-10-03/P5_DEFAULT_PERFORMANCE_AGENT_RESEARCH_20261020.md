# 默认 Agent 性能工具到报告的实际验收

本次依据现有性能模式、工具/审批、项目与会话接口，以及已完成的真实 k6 报告专项继续查证。没有改生产代码、权限规则、测试模式、供应商协议或全局配置，没有注入执行计划或已完成检查点。

## 原路径与缺口

- 原 `test_performance_evidence_live.py`、`test_performance_report_product.py` 从 guard/analyzed 检查点开始，证明真实引擎/解析/报告，不能代替默认 Agent 从用户消息发起测试。
- 新增 `test_default_performance_agent_live.py`：读取当前 MySQL 默认 Step 配置，原样复制模型调用参数到隔离 MySQL；使用独立生产 API、Agent Worker CLI、PG、Redis、RustFS和受管 http-echo 目标。用户要求固定 1 VU、closed、2 秒、P99<1000ms；负载上限只在隔离测试设置中收紧。
- `ExecutionSafetyPolicy._trusted_target_hosts` 读取服务端可信范围；`InputOrchestratorService` 从会话元数据获取该范围。`SessionService._new_session_record` 仅核验项目身份，不从项目 base_url 生成可信范围；公开会话接口禁止客户端写入该元数据。
- `SafetyIntentService._authorization_status` 对非 security_probe 返回 not_required，用户一句“目标已确认”也不能形成可信私有地址。因此项目地址登记和会话目标授权是两个当前并未接通的动作，不能通过修改提示或去掉策略拦截使测试通过。

## 实际批次

| 批次 | 真实结果 | 结论 |
|---|---|---|
| `p5-default-performance-agent-20261020` | 1 failed，78.16秒 | 模型选择引擎并提交探针/性能工具，但私有目标被拒；测试又查询不存在的指标表，掩盖此前拒绝。不是指标持久化故障。 |
| `p5-default-performance-agent-registered-20261020` | 1 failed，54.46秒 | 经公开项目 PATCH 登记 base_url 后仍被拒；证实地址登记不产生可信会话范围。保留原会话/任务/HTTP和测试源码。 |
| `p5-default-performance-agent-owned-20261020` | **1 passed，170.32秒** | 沿 `test_default_ui_scene_live.py` 的独占服务端目标前提，将本次自有目标写入隔离会话的 resource_scope。模型、计划和结果没有被替换；此批只证明已具备可信目标时的后续链路。 |

最后一批实际发生 4 次模型 HTTP、3 次现有审批，调用 perf-engine-select、http-probe-runner、performance-test-runner，随后调用 report-writer。真实 k6 冒烟与正式压测各一次；最终性能工具 report_ready/pass，PG 一条正式指标记录与工具输出完全一致。

取得 10,600 个样本，错误率 0，吞吐量 5299.4456/s，P50=0.140144ms、P95=0.18784905ms、P99=0.26069438ms。该数据仅代表本地 http-echo 及这次 1VU/2秒链路验收，不是业务系统容量基线。

性能工具的 13 份工件经真实 API 下载，其中两次引擎执行各有脚本、stdout、stderr、summary、manifest，另有 Markdown/HTML/JSON 报告；执行原件 SHA-256 与元数据一致。实际构建完整 Vue App（vue-tsc/Vite）并用 Chromium 打开报告中心，核验良好/pass、本次任务归属、JSON 下载与原指标相同；pageerror及HTTP错误为空，截图已目视复核。API 重启后重投原终态 Agent 任务，新增模型 HTTP、ToolJob及性能运行均为0。

全局默认模型参数读回未改变。补充使用 Docker CLI 对4个完整受管容器ID执行只读 inspect，均明确 no such object；不能以此替代容量租约回收或故障组合验收。补充核验脚本初次漏传 settings、之后大小写判断及工作目录错误均未计为通过，最终原生CLI记录保存在 `cleanup-verification.json`。没有运行生产数据库迁移，没有引入依赖。

## 尚未完成与下一步

- **已有实现且本批通过**：具备服务端可信目标时，当前默认 Step 从用户消息、原有审批、实际 k6 工具、指标/证据持久化，到报告页面、下载及终态重投的正常路径。
- **仍需补实现或明确产品入口**：普通性能会话如何通过现有项目/环境操作登记可信目标；当前公开项目 base_url 不足以完成这一步。不能把隔离数据库中的权限前提准备说成公开产品流程已经闭环，需先确定目标依据、更新/撤销与会话恢复的既有语义。
- **尚未验收**：默认 Agent 的性能负向/超时/进程中断和并发容量，AI流式指标，以及默认安全 Agent 完整路径。旧真实工具故障专项继续保留，但不替代这些默认模型验收。
- 默认多用例规划/P2、人工 DeepSeek 录制、业务变更评审/环境闭环和生产历史迁移等继续按总方案推进。

可复验命令（在 Agent_Server 运行，目录每次必须全新）：

```powershell
$env:RUN_LIVE_DEFAULT_PERFORMANCE_AGENT='1'
$env:QA_DEFAULT_PERFORMANCE_AGENT_DIR=Join-Path (Get-Location).Path '..\docs\upgrade_2026-10-03\p5-default-performance-agent-owned-NEW'
..\.venv\Scripts\python.exe -Xutf8 -m pytest tests/test_default_performance_agent_live.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/p5-default-performance-agent-owned-NEW.xml
```

完整本地工件在 `p5-default-performance-agent-owned-20261020/`，版本库仅纳入核验摘要与JUnit，不提交包含内部租约的原始PG快照或运行日志。配置继续 MySQL，业务/结果继续 PostgreSQL，完整原件继续 RustFS；P0—P5整体未完成。
