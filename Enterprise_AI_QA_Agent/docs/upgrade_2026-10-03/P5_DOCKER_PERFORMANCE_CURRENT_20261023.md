# P5 Docker 性能真实验收（2026-10-23）

## 结论

现有 Docker 性能工具已完成一次真实的生产 API/Worker 边界验收，但不代表 Agent 自主规划或所有性能场景已经完成。测试没有调用模型，使用 MySQL 配置、隔离 PostgreSQL、RustFS 和临时 Redis；性能引擎实际运行本机已有的 k6 镜像，目标使用本机已有的 `hashicorp/http-echo:1.0.0`。

测试结果为 `1 passed`。四次真实 k6 运行分别覆盖正常、smoke、缺失 p99 指标和 SLA 阈值违规：

| 场景 | 实际结果 | 报告判定 |
| --- | --- | --- |
| default | 40 个样本，P99 0.765857 ms，退出码 0 | `pass` |
| smoke | 3 个样本，P99 0.620177 ms，退出码 0 | `pass` |
| missing | 没有 P99，退出码 0 | `inconclusive` |
| violating | P99 0.631451 ms，阈值 0.001 ms，退出码 99 | `fail` |

缺失指标没有被当成通过；阈值失败保留 k6 的 stdout/stderr、退出码和报告判定。四条指标记录写入 PostgreSQL，RustFS 原始工件的大小和 SHA-256 与元数据一致。测试结束后目标容器和四个性能执行容器均执行删除，并通过 `docker ps --all --filter id=...` 确认不存在；当前没有 `qa-execution-cli-*`、`qa-perf-*` 残留容器。

## 依据与范围

- 实现和现有测试：`Agent_Server/tests/test_performance_docker_measurement.py`、`K6EngineAdapter`、`PerfRunnerService`、`PerfResultParser`、`PerfReportBuilder`。
- 执行命令（`Agent_Server`）：

  ```powershell
  $env:RUN_LIVE_DOCKER_PERF_MEASUREMENT='1'
  $env:QA_DOCKER_PERF_MEASUREMENT_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p5-docker-performance-current-20261023-01'
  ..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_performance_docker_measurement.py -q --tb=short --junitxml=G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p5-docker-performance-current-20261023-01.xml
  ```

- 原始证据：`Agent_Server/docs/upgrade_2026-10-03/p5-docker-performance-current-20261023-01/`。

本轮只证明现有性能工具的调用、退出码、指标缺失处理、阈值判定、原始工件和清理闭环。Agent 调用工具、真实业务目标、多级加压、TTFT/Token 吞吐、性能报告完整产品页面和安全工具仍需单独验收。

## Step 当前状态

当前 MySQL 配置仍为 `step-5-preview / openai_chat_completions`，账号密钥存在且已被供应商识别。最小真实请求发往当前 Step Plan 地址时返回 HTTP 400，类型为 `request_params_invalid`，供应商消息为 `you have no active step plan subscription`；同一密钥切换到标准 `https://api.stepfun.com/v1` 后返回 HTTP 402 `quota_exceeded`。因此 Step 当前停在“真实 SDK/HTTP 已发出，但供应商未提供可用推理”的阶段，尚未进入普通文档生成结果、用例选取排序补全或正式 Run。

这不是认证失败，也没有证据支持把 Anthropic Messages 改成 OpenAI Responses。当前代码仍按 transport 分派：Anthropic 使用 Messages，OpenAI Responses 使用独立 `/responses` 客户端，Step 使用 OpenAI Chat Completions；需等 Step 订阅或额度恢复后再做真实模型质量验收。
