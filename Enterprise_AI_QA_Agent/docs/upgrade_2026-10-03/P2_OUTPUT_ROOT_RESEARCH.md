# P2 工具输出根路径修复与真实复验

2026-10-19。本次修复依据是持久化 ToolJob 与默认模型的真实失败；P2 整体仍未通过验收。

## 已确认的根因与改动

`p2-default-single-staged-v2-live2-20261019/PG-tool-jobs.json` 显示创建与删除分别返回 HTTP 201/200，但固定效果声明使用 `returned.task_result` 开头的路径，捕获结果为 `object_path_missing`。`planning_tool_contracts.capture_tool_operations` 把返回结构写在 `output.returned` 内，而 `api_testing_mode.runtime._execute_dispatched_task` 实际返回根级 `task_result`；效果捕获器直接从持久化输出根解析路径。

修正新建 operation snapshot 的 `output` 为真实返回根结构；失败变体说明放在旁支 `known_validation_failure`，避免误读为实际输出字段。分阶段任务提示明确输入与原始输出的路径根。旧 seed、规划、回执和失败结果不重写，捕获器不猜测或自动改路径。`ok` 仍说明为 `task_result.status == completed`，不固定为成功。

## 实际验证

- `uv run --project . python -m pytest -q tests/test_planning_tool_operations.py tests/test_planning_item_operation_selection.py tests/test_planning_operation_prefetch.py`：20 passed，20 skipped；跳过项要求隔离存储环境。
- `uv run --project . python -m pytest -q tests/test_case_effect_relations.py tests/test_planning_effect_tasks.py`：25 passed，18 skipped；跳过项要求隔离存储环境。
- Anthropic 专项：`uv run --project . python -m pytest -q tests/test_model_sdk_fault_http.py tests/test_model_clients.py -k anthropic`：15 passed，64 deselected。没有修改 Anthropic 代码；原 SDK `messages.create`、`/v1/messages` 和流事件协议保留，`await_provider_io` 只归一化网络异常。

## 新的默认模型真实失败

证据目录 `p2-default-single-staged-v2-live3-20261019/`。隔离 MySQL/PG/Redis/RustFS，使用真实默认 Step，单个正式创建/清理用例、分阶段任务 v3、证据引用 v2、16K 隔离输出上限、600 秒规划时限。pytest 1 failed，511.39 秒；没有修改全局模型设置。

规划成功，效果声明输出路径不再带 `returned`。实际业务请求仅一次 POST 和一次 DELETE，返回 201/200；两个效果均 `captured`、checks 通过，known_object_count=1、covered_object_count=1、uncovered=[]，目标订单已清理。

最终仍为 `blocked`：模型随后调用 `api-test-runner`，只有 `_case_action.operation_key=receipt`，缺少 `worker_action/task`，实际进入 Campaign 项目选择，结果 `partial`。该原操作被效果捕获器判断 `selection_unknown`，导致覆盖仍包含 unknown。之后同 operation_key 携带不同输入的调用被现有幂等机制拒绝，未增加业务 HTTP。该问题尚未修复；不是审批未通过，也不能通过忽略 unknown 或强制 passed 消除。

仍待验证：正确回执收尾、整个正式 Run 通过、默认模型下终态重投无额外业务请求，以及多候选检索/排序、必要依赖与运行内准备等其他 P2 场景。单个用例规划成功不能代表 P2 或 P0–P5 完成。
