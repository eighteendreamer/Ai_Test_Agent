# 默认 UI 正式结果的当前证据与验收修正

日期：2026-10-18。代码基线 `e66f5396`。完整 P0—P5 未完成。

## 先确认已有实现

`P5_DEFAULT_UI_RESEARCH.md` 的“下一步实现结构化观察”已过期：`P5_UI_OBSERVATION_RESEARCH.md`、`python_playwright_cli.py` 与 `mcp_runtime_service.py` 已实现持续 command eval 的 typed result，保留原 stdout 和 transcript。运行 `RUN_LIVE_BROWSER_TESTS=1 python -m pytest tests/test_browser_evaluation_observation.py::test_persistent_eval_exposes_exact_typed_value_and_unchanged_original`，真实 Chromium **1通过、11.64秒**。覆盖0、false、null、同名字符串、空字符串、对象/数组、元素引用及同会话，不重新实现这项能力。

默认模型配置原样复制到隔离MySQL，业务、固定用例、ToolJob、Run及资源账本使用隔离PostgreSQL，原件RustFS、队列Redis。生产API、规划/执行/资源清理CLI及真实Chromium实际运行。每次录制点击一次、新执行页点击一次，不改变DOM或用录制实际值冒充执行结果。没有生产代码或供应商协议变更。

## 三次完整产品运行

| 原件目录 | pytest结果 | 正向正式结果 | 负向正式结果 | 事实与局限 |
|---|---|---|---|---|
| `p5-default-ui-scene-live-current/` | 1通过/1失败，248.36秒 | passed，终态重投新增业务/模型请求均0 | failed | 负向在测试要求页面ID必须直接出现在Run投影时失败，未到终态重投 |
| `p5-default-ui-scene-live-current2/` | 2失败，285.48秒 | passed | failed | 测试错误要求每个观察都含page_id；实际合法观察还包含动作状态、不含页面身份的初始值，模型也会使用pageId；两者未到终态重投 |
| `p5-default-ui-scene-live-current3/` | 1通过/1失败，411.97秒 | passed，终态重投新增业务/模型请求均0 | error | 真实回执前附未加围栏说明，纠正响应length，业务实际只点击一次；不得改判failed |

页面身份应沿正式receipt的observation.call_id → evidence_refs中的ToolJob → 持久output_payload的结构化页面值验证。Run的case_output是用例所需输出投影，不要求复制完整观察。对每条观察核对Job引用、run_id、run_item_id和真实字面量路径；已观察页面身份必须全部属于新执行页，不能含录制页。这里只识别已捕获eval表达式实际输出的page_id/pageId，不改变生产字段，不修正模型路径。动作状态与不含页面ID的合法值可作为其他观察，至少一条已引用Job保留本次执行页身份。

`test_default_ui_evidence_replay.py` 使用五份真实成功/业务失败原件的最小公开投影，保存原文件SHA256，可离线重放；同时篡改为录制页、其他Run、缺Job引用和错误包装路径，均必须拒绝。另保留第三批negative的公开原回执及不完整纠正错误，原文前附说明仍按现有解析规则拒绝。**10通过**，不是十次外部模型验收。扩展回归 `test_receipt_stream_response.py`、`test_agent_receipt_transport.py`、`test_browser_evaluation_observation.py` 和新回放测试 **33通过、3门控跳过、29.50秒**，跳过不计通过。

审计 `p5-ui-evidence-current-audit.json` 对照15个原件来源哈希，以及6场景的25份浏览器原件下载哈希/大小，原失败XML保留。正向当前验收成立；负向有实际failed证据，但本批默认配置未取得稳定的完整通过和终态重投验收。

## 纠正失败与模型字段配置

第三批negative的RustFS纠正request/response已实际回读，SHA256和大小与PG公开checkpoint一致；response只导出text/tool_calls/response_summary，不导出私有推理。见该目录的`correction-*-public.json`及`correction-recovery-verification.json`。原始响应确认8,192 completion tokens、text为空、finish_reason=length、completion_status=truncated。

原执行模型先正确报告实际1与预期2不等，随后返回未加围栏的说明加JSON；现有parse_response拒绝该格式，触发一次只读纠正。字符串数组索引`"0"`由literal_value显式支持，不是本次错误；不应因此更改路径校验。

原MySQL模型没有request_profile，纠正的可选json_schema被映射为prompt_fallback/format_not_declared。现有request_mapping明确支持在单模型profile声明json_object时降级为该模型的JSON模式，并单独映射effort字段；Anthropic仍用Messages。以既有live17/live18 profile做无HTTP对照，结果为response_format=json_object、reasoning_effort=low、max_tokens=8192。默认无profile不加这些字段。不能从默认json_mode能力布尔值推断所有供应商都接受同一参数，也不能因此全局改配置。

UI验收夹具增加可选`QA_DEFAULT_UI_REQUEST_PROFILE`，只更改隔离MySQL副本并保存两套公开参数，最终核对全局配置未变；不改变期限、最大输出、业务步骤、校验或默认profile。本候选真实对照独立保存，不能冒充未修改配置的验收成功。

对照实际结果：`p5-default-ui-scene-profile-current.xml` **1通过、123.96秒**，仅negative参数。固定场景→真实默认Step规划→新页面初始0→真实点击一次→读到1→与2比较，正式结果failed。终态重投新增业务HTTP0、模型HTTP0。SDK创建/物理HTTP均9次，各请求仍max_tokens=8192、reasoning_effort=low；一份规划请求有response_format=json_object，其余Agent请求没有该字段。原件5份通过API/RustFS及下载哈希核验；隔离MySQL profile与原全局profile分别保存，最终全局参数恒等断言通过。

**该成功没有进入receipt_correction**，不能声称json_object已解决纠正输出截断，也不能把耗时差异归因于单个字段。它证明现有单模型配置映射可用于真实UI执行和终态幂等，不需要统一供应商协议。默认配置的第三批negative仍保持error；纠正阶段真实质量及复杂场景仍需推进。

## 仍需继续

默认配置负向UI及纠正质量、复杂UI与默认Agent完整项目选择/补全、多候选排序、准备及未知结果生命周期、计划修订、完整App和其余P0—P5范围仍未完成。不能用本批离线重放覆盖外部模型失败，不能用准备/工具成功证明业务断言通过。
