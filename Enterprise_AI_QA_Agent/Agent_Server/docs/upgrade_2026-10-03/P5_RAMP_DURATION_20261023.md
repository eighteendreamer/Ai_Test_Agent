# 默认性能阶梯丢失时长的复现、修复与真实验证

## 根因与依据

当前 `WorkloadModeler.from_api_docs()` 和 k6 开环/闭环默认分支各自使用三次 `duration // 3` 生成阶梯。实际要求2秒时得到三个0秒，要求5秒时只有3秒。相同算法的重复实现是本次根因，修复集中到一个默认阶梯生成函数，未增加新的执行状态机或模式。

官方 k6 文档明确阶段时长共同组成负载时间，开环阶段 target 是迭代到达率，闭环阶段 target 是 VU 数，不能混用：[Ramping arrival rate](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-arrival-rate/)、[Ramping VUs](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/ramping-vus/)。本地课程 `项目借鉴/llm-testing-course/性能测试-AI专项篇.html` 对传统 HTTP 与流式 AI 指标的区分也用于限定本次验收范围：普通 HTTP 指标不能当作 TTFT 或 Token 吞吐。

修复前实际使用本机已有 k6 镜像检查并运行生成脚本，证据位于 `p5-ramp-duration-before-20261023-01/`：

- `inspect --execution-requirements` 确认2秒被编译为 `[0s, 0s, 0s]`，5秒被编译为 `[1s, 1s, 1s]`；两个默认分支均如此。
- 两个2秒脚本实际 `run` 均退出码0，但 stderr 包含 `No script iterations fully finished` 和摘要生成错误。这证明不能用退出码0代替采样和结果验证。
- 复现只在无网络的短时 k6 容器中执行，没有请求外部目标、模型或创建 Redis 容器。

## 修改

- `src/modes/performance_testing_mode/workload_modeler.py` 增加共用 `build_default_ramp_stages()`。以毫秒分配三段，最后一段保留余数，总和等于用户要求时长；正数秒不会生成零时长。既有目标顺序保留，显式阶梯不重新生成。默认总时长非正数时明确报错。
- `src/application/performance/k6_engine_adapter.py` 的开环和闭环默认分支复用该函数，API 文档建模也复用，消除三份整除算法。增加既有 logging 风格的 `performance_default_ramp_built`，记录总时长、目标和三段毫秒值。
- 新增 `tests/test_performance_ramp_duration.py`，并将两个短阶梯加入既有生产 Runner/指标持久化验收。没有改配置、数据库结构、测试模式或模型协议；显式阶梯和既有保存脚本保持原样。

例如2秒生成 `[666ms, 666ms, 668ms]`，5秒生成 `[1666ms, 1666ms, 1668ms]`，300秒仍为三个100秒。

## 验证

首轮组合为 **35 passed、3 failed**。三个失败均在5秒场景的测试读取器：k6 将1666ms规范化输出为1.666s，测试正则只接受整数，尚未进入该场景的实际负载执行。按实际输出修正测试读取器后，执行以下组合：

```powershell
$env:RUN_LIVE_PERF_RAMP='1'
$env:QA_PERF_RAMP_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p5-ramp-duration-current-20261023-02'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_performance_ramp_duration.py tests/test_performance_k6_adapter.py tests/test_performance_measurement_contract.py tests/test_performance_coordinator.py tests/test_performance_report_builder.py tests/test_performance_runner.py tests/test_performance_mode_skills.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p5-ramp-duration-current-20261023-02.xml
```

结果：**64 passed in 88.85s**。其中6个真实 Docker/k6 场景为开环默认、闭环默认、API文档建模各执行2秒和5秒，核对引擎实际阶段时间之和、目标服务器记录、样本数量、错误率、吞吐、P50/P95/P99及报告。服务器实际 GET 数与引擎 samples 相等且大于0，报告通过。另覆盖1/2/3/5/10/60/300秒、非正数和显式阶梯不被替换。

随后在完整生产 API/真实隔离存储环境执行正式 Runner：

```powershell
$env:RUN_LIVE_DOCKER_PERF_MEASUREMENT='1'
$env:QA_ISOLATED_RUNTIME_REDIS_URL='redis://127.0.0.1:6379/13'
$env:QA_DOCKER_PERF_MEASUREMENT_DIR='G:\Code_Warehouse\Ai_Test_Agent\Enterprise_AI_QA_Agent\Agent_Server\docs\upgrade_2026-10-03\p5-ramp-production-current-20261023-01'
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_performance_docker_measurement.py -q --tb=short --junitxml=docs/upgrade_2026-10-03/p5-ramp-production-current-20261023-01.xml
```

结果：**1 passed in 66.24s**，同一验收包含6次 Runner 执行：默认恒定负载、冒烟、开环短阶梯、闭环短阶梯、缺失指标和阈值违规。已有镜像实际为 `k6 v2.2.0`，ID `sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec`。

两次新增短阶梯分别得到5和27个样本，P99分别约3.361ms、0.937ms，报告为pass；缺失P99仍为inconclusive，低阈值违规仍为fail。六次指标与报告结论进入实际 PostgreSQL，最新有效基线选择闭环短阶梯。每次Runner保存5份RustFS证据，原始摘要、stdout/stderr和报告保留在证据目录。本轮改动的四个Python文件编译通过，`git diff --check` 通过；环境未安装ruff，未执行Python lint。未修改前端，未重跑前端构建。

## 资源与边界

所有独立k6测试容器使用随机专名并随执行删除；生产Runner管理的7个容器（6次运行及1个受控目标）经完整ID独立查询均不存在，30份RustFS证据实际读取均返回NoSuchKey。MySQL/PostgreSQL测试存储由既有隔离夹具清理；本项未额外宣称逐表独立核对。

Redis复用已有服务的DB13，测试后存在7条cleanup_pending元数据。先核对其session和完整容器ID全属于本次且容器已不存在，再精确清理本轮事件、清理队列、配额缓存及元数据，最终为0键；未使用FLUSHDB。本项没有运行Cleanup Worker回执链，不能据此宣称清理回执验收完成。独立核对记录见生产证据目录中的 `cleanup-verification.json`。没有创建 `qa-execution-cli-*` 容器。

本轮关闭默认阶梯时长实现缺陷及其引擎/生产Runner验收，不代表Agent自主分级加压、每级负载报告、默认Step质量、TTFT或Token吞吐验收完成。默认Step仍保持原配置，认证状态为已完成，最近真实供应商阻塞为无可用Step Plan订阅。
