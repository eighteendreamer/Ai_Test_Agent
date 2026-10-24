# 2026-10-24 当前升级验收进度

本记录依据当前工作树、MySQL 默认模型配置、隔离验收和真实外部请求更新。完整升级目标仍未完成；上一日记录见 `CURRENT_ACCEPTANCE_20261023.md`。

## 默认模型切换为 DeepSeek

用户在 MySQL 配置库中切换了激活模型（只读核对 `llm_model_config`，未写入）：

- `step-5-preview`（id=21）：`is_active=0`，更新于 2026-10-24 11:37:00。
- `deepseek-reasoner`（id=17）：`is_active=1`，provider=deepseek，transport=`openai_chat_completions`，base_url=`https://api.deepseek.com`，更新于 2026-10-24 11:38:15。
- `task_execution` 应用的默认模型解析为 `deepseek-reasoner`（`get_default_active()` 取首个 `is_active=1` 记录）。

## 默认模型最小真实探针

使用应用自身 `OpenAIChatClient`（`max_retries=0`，真实 MySQL 配置，密钥未落日志）对 `deepseek-reasoner` 执行四项最小脱敏请求，全部通过：

1. 非流式：返回正常，`reasoning_tokens=30`，密钥与订阅可用（Step 时代的 `no active step plan subscription` 阻塞不再存在）。
2. 流式：delta 正常接收，`finish_reason=stop`，usage 完整。
3. 非流式+工具：模型正确返回 `get_order(order_id="o-123")` 工具调用，`finish_reason=tool_calls`。
4. 流式+工具：流中工具调用解析正确，`stream_terminal_received=true`。

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

## 环境清理

- Redis DB 13 复核后仅余常驻键（`qa:fencing:agent`、`qa:quota:limits`、`qa:tasks:case_planning`）；本轮测试产生的 2 个会话事件缓存键已精确删除。
- 无 `qa-execution-cli-*` 容器残留；隔离 PostgreSQL/MySQL/RustFS 对象由夹具清理。
