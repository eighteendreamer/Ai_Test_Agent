# 2026-10-24 当前升级验收进度

本记录依据当前工作树、MySQL 默认模型配置、隔离验收和真实外部请求更新。完整升级目标仍未完成；上一日记录见 `CURRENT_ACCEPTANCE_20261023.md`。

## 默认模型两轮切换

用户当天在 MySQL 配置库中两次切换激活模型（只读核对 `llm_model_config`，验收未写入）：

- 11:37–11:38：`step-5-preview`（id=21）停用，`deepseek-reasoner`（id=17）激活，`task_execution` 默认解析为 `deepseek-reasoner`。
- 12:24:59：id=17 行原地改为 `deepseek-flash`（provider=deepseek，transport=`openai_chat_completions`，base_url=`https://api.deepseek.com`），当前默认为 `deepseek-flash`。

两组模型各做过应用自身 `OpenAIChatClient` 的最小真实探针（非流式、流式、工具调用、流式+工具调用），均通过：密钥与订阅可用，Step 时代的 `no active step plan subscription` 阻塞不再存在；`deepseek-reasoner` 返回 `reasoning_content`，`deepseek-flash` 工具调用与流式 delta 正常。验收测试自带的全局默认配置身份守卫在 12:24:59 切换窗口如实触发过一次失败（`p2-default-full-deepseek-current-20261024-01`），属于守卫正确工作，不是产品缺陷。

## 上传内容类型检测挂死缺陷（本轮发现并修复）

### 发现过程

运行默认模型 P2 闭环验收时，生产 API 在 `POST /registry/api-docs/upload` 上无错误日志失联（两次失败：连接重置与 240 秒读超时），复现于 `p2-default-multi-case-deepseek-current-20261024-01/02/03/`。py-spy 抓取挂住进程的主线程栈，定位到 `upload_security_service._detect_content_type` 在**事件循环线程**上同步 `import magic`，卡死在 `magic/compat.py` 模块级 `Magic(_open(MAGIC_MIME))` 的原生 `magic_open()` 调用。

### 根因

python-magic 0.4.27 的加载器会接受 PATH 上的 `msys-magic-1.dll`（本机命中 Git for Windows 自带的 `E:\Git\Git\usr\bin\msys-magic-1.dll`，MSYS2 版 libmagic 对原生 CPython 不兼容），加载后 `magic_open()` 挂死或段错误。原实现的 `import magic` 位于同步调用与 `except Exception` 内：异常可被兜底，**挂死不能**；且发生在事件循环线程上，一次上传即冻结整个 API。2026-10-23 及之前在 PowerShell 下运行时 PATH 不含 Git 的 `usr\bin`（注册表 PATH 仅 `E:\Git\Git\cmd`），`import magic` 快速失败走签名回退，故未暴露；Git Bash 会话注入 `usr\bin` 后必现。

### 修复

`src/application/security/upload_security_service.py`：

- `_detect_content_type` 改为异步：libmagic 检测放入守护线程并有界（`_MAGIC_DETECTION_TIMEOUT_SECONDS=10.0`），事件循环不再被阻塞；超时/导入失败记入结构化日志（`upload_magic_detection_disabled` / `upload_magic_detection_unavailable`），不再静默吞错。
- 检测结果按进程缓存：超时或 `ImportError` 后标记不可用，后续上传直接走既有字节签名回退，不再重复挂起。
- 守护线程保证即使原生调用挂死也不阻塞解释器退出。

### 修复验证

- 触发环境下端到端调用 `secure_store_upload`：首次 10.51 秒超时后回退成功（`decision=allow`），第二次 0.03 秒，进程正常退出，RustFS 临时/正式对象均删除。
- 既有套件无回归：`tests/test_api_doc_original_assets.py tests/test_api_docs_origin_security.py` **16 passed, 12 skipped**；`tests/test_case_import_service.py` **8 passed, 6 skipped**。
- 新增回归 `test_content_type_detection_falls_back_when_libmagic_hangs`（模拟挂死 magic 模块）：断言 0.1 秒内回退、状态缓存后二次检测瞬时完成。

## 默认 DeepSeek 模型 P2 多用例依赖闭环验收通过

`RUN_LIVE_DEFAULT_COVERAGE_PRODUCTION=1`、`QA_DEFAULT_CASE_SHAPE=multi-case-dependency`、隔离 Redis DB 13 下执行 `tests/test_default_coverage_production.py::test_actual_default_model_plans_and_executes_complete_case`：

```text
1 passed in 98.94s
```

证据：`p2-default-multi-case-deepseek-current-20261024-04/` 与同名 JUnit XML。

- 默认模型身份：全程 `deepseek-reasoner` / `openai_chat_completions`，`execution_model_role=default`，`global_default_unchanged=true`（真实 MySQL 配置未被测试改写）。
- 规划（真实默认模型一次流式调用）：正确将"创建临时订单并记录实际ID"补入为前置依赖（`kind=dependency`），"查询订单详情"为目标（`depends_on=[create-order]`），`order_id` 经 `input_bindings` 引用前置实际输出；无关的"商品排序"用例未被选取；`unresolved=[]`，规划理由引用正式用例版本证据。
- 业务执行（真实受控 HTTP 目标）：`POST /orders` 创建、`GET /orders/{实际ID}` 查询、`DELETE /orders/{实际ID}` 清理各一次；`actual_created_ids` 与实际请求一致，`remaining_orders={}`（清理完成）。
- 执行条目两个均为 `passed`；流式 SDK 观测记录了 chunk 计数、首块耗时与 `reasoning_content` 事件。
- 本轮仅关闭"按用例执行"的多用例依赖闭环：默认模型的更广场景质量、未知副作用只读查证、其他测试模式与完整产品流程仍按总表继续推进。

## P2 其余两种形态默认模型验收通过（deepseek-flash）

同一测试在另外两种 `QA_DEFAULT_CASE_SHAPE` 下以当前默认 `deepseek-flash` 通过：

| 形态 | 结果 | 证据 |
|---|---|---|
| full | `1 passed in 140.55s` | `p2-default-full-deepseek-current-20261024-02/` 及同名 XML |
| single-create-cleanup | `1 passed in 77.94s` | `p2-default-single-create-cleanup-deepseek-current-20261024-02/` 及同名 XML |

- full：规划出 2 个准备步骤（按需求文档创建两条订单并保存各自 `order.id`）、1 个目标（依赖两个创建项并绑定 `actual_order_id`）、2 个清理步骤（DELETE 两条订单，理由引用需求文档对 DELETE 的约定）；真实业务 HTTP 共 5 次（2×POST、1×GET、2×DELETE），`remaining_orders={}`，`unresolved=[]`，Run 条目 passed。
- single-create-cleanup：规划仅 1 个 target（创建订单、用真实返回 `order.id` 清理并核对状态码）；业务 HTTP 为 POST+DELETE 各一次，`remaining_orders={}`，Run 条目 passed。
- 两次运行 `global_default_unchanged=true`；每轮证据的 `verification.json` 记录了默认模型身份与全部业务请求。

### 共享测试流的验收基础设施边界（本轮确认）

前两次 full/single 启动失败（规划任务停留 `queued`、规划 Worker exit 0），根因是测试 Redis DB 13 的共享流 `qa:tasks:case_planning` 中残留此前失败运行的投递：测试使用的规划 Worker CLI 为单批消费（`--once`），首批读到指向已销毁隔离数据库的旧条目时按 `ValueError(规划投递没有对应的项目持久任务)` 结束，本轮自己的条目排在后面未被消费。删除该流键后重跑即通过。生产 Worker 是常驻循环（`run()`），失败投递走转移/死信后继续消费，不受此影响；故这属于验收基础设施边界而非产品缺陷。按既有约定，本轮各次运行前精确清理 DB 13 的 `qa:tasks:*` 与 `qa:session:*:events` 测试键，运行后复核仅余常驻键（`qa:fencing:agent`、`qa:quota:limits`）。

## Agent API 默认模型完整流程验收通过（deepseek-flash）

`RUN_LIVE_API_AGENT_WORKER=1`、`RUN_LIVE_API_AGENT_WORKER_DEFAULT=1`、隔离 Redis DB 13 下执行 `tests/test_api_agent_worker_live.py::test_real_agent_worker_private_auth_and_terminal_redelivery` 的 `default-model` 两个变体：

| 变体 | 结果 | 证据 |
|---|---|---|
| default-model-same-process-child | `1 passed in 82.77s` | `p3-api-agent-worker-default-deepseek-current-20261024-01/configured-default-in-process/` |
| default-model-durable-child-process | `1 passed in 99.52s` | 同目录 `configured-default-durable/` |

默认模型身份：`deepseek-flash` / `openai_chat_completions`（复制到隔离配置表，真实配置只读）。断言与证据覆盖：

- 真实默认模型驱动完整 API Agent 链路：持久化任务提交 → 独立 Worker 领取 → 模型工具调用 → 业务 HTTP。持久变体记录 `physical_model_http=10` 次真实模型 HTTP（同进程变体同样 >0）。
- 业务目标（受控登录/刷新/详情三接口）最终 `[200,200,200]`；依赖传值与鉴权续期按固定 campaign 执行；父任务 `completed`。
- 报告 ToolJob 恰 1 份且 `completed=3, failed=0`；持久子进程场景 3 个子会话全部属于本项目。
- 登录与刷新凭据加密存储（2 条），且不出现在公开输出、会话记录、ToolJob 或进程日志中。
- API 重启后任务历史保留；终态重放 completed 任务 0 次新增业务 HTTP、0 次新增模型 HTTP。
- 场景中的中断/审批等待/Worker 重启边界由测试按参数实际驱动并通过。

这关闭了总表中"Agent API 测试——完整默认 Agent 流程仍未验收"的缺口；剩余缺口为更完整的断网/进程故障组合与产品页面报告的默认模型路径对账。

## 环境清理

- Redis DB 13 复核后仅余常驻键（`qa:fencing:agent`、`qa:quota:limits`、`qa:tasks:case_planning`）；本轮测试产生的 2 个会话事件缓存键已精确删除。
- 无 `qa-execution-cli-*` 容器残留；隔离 PostgreSQL/MySQL/RustFS 对象由夹具清理。
