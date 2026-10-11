# 持续浏览器会话的结构化观察与正式回执契约

本批起点为 `0907c39f`。完整 P0—P5 目标保持 active；不是重新设计测试模式或替换模型。先读完整用户方案、既有代码与上批真实失败，再实施。

## 依据与采用方式

- `src/application/runtime/python_playwright_cli.py` 的 `_execute eval` 直接得到 Playwright 的真实值，但 `_format_value` 把字符串原样输出、其他类型 JSON 化，因此 `"false"` 与 `false`、`"0"` 与 `0` 的 stdout 无法区分。不能靠再解析文本还原原类型。
- `src/application/context/mcp_runtime_service.py` 的持续 command 会话按固定运行身份复用；evaluate_js 则打开独立目标页并关闭，不能代替操作后同一会话的观察。旧 command 只暴露 stdout/stderr，evaluate_js 已有 result 形式。
- 沿现有 Playwright eval、命令成功返回及 transcript：在格式化之前保留 result，原 stdout、命令、退出码和工件保持；不再调用一次浏览器，不通过 DOM 赋值满足预期，不把 JSON.stringify 返回的字符串猜成对象。
- `browser_commands.py` 使用既有 shlex 分词；完整 command 字符串必须正确引用表达式，args 数组则表达式保持单独一个元素。不要拼接后续参数，因为 eval 还支持真实元素引用参数。
- `agent_case_contract.py` 明确回执路径从持久输出根开始；`literal_evidence.py` 严格区分存在的 null 和字段缺失。未增加自动去除 output 前缀或猜测证据路径的机制。

## 真实复现与验证

1. 使用上批 live3 实际 stdout 中 count/page_id 创建原样对象，在真实 Chromium 执行。首次夹具按错误空白格式筛选原件导致 StopIteration（before）；修正为原件实际格式后，明确复现缺少 result（reproduced）。两个失败工件均保留。
2. 同步 native CLI、MCP 返回、工具输出描述与 UI cases Prompt。真实 Chromium 验证对象、数组、0、false、null、空字符串、JSON 字符串、同会话值、元素引用及退出；transcript 与 stdout/result 核对，错误包装根继续拒绝。真实浏览器等首批31通过19.04秒。
3. 生命周期受控夹具曾用无参数 eval 充当任意占位命令。eval 现在走真实求值路径，该夹具改为其既有受控 `_execute` 的 noop；初次回归2失败87通过4跳过，修正后89通过4跳过23.38秒。最终扩大117通过4门控跳过49.70秒，含实际 Chromium。各批重叠不相加。
4. 默认 Step live4：1失败119.96秒。读取固定场景、新页面初始0、真实点击一次均发生；第一次结构化初始值正确。后续将带空格的表达式放入未引用 command，shlex 按合法命令规则将后半段当 locator，原预算耗尽，正式error。没有更换模型、扩预算或自动拼接修复。
5. 补齐公开工具/Prompt 的 args 单元素格式及持续/独立会话区别后，live5：1失败1通过462.86秒。两项都读取固定场景、打开新页面初始0、只点击一次且同会话最终1。负项期望2，正式failed，终态重投新增业务/模型请求均0；正项因equals同时引用动作结果和计数而被现有校验拒绝，非流式只读纠正3次HTTP超时，正式error，不能改判。
6. 核对 `schemas/case_execution.py`：原 after validator 已要求非semantic只能一个观察值，但 JSON Schema 只导出maxItems=20，Prompt只说用确定性操作，没有公开该条件。同步既有字段description与公共回执说明，原验证条件不变；普通Prompt版本v4→v5，场景Prompt v5→v6，旧请求/检查点/响应原件不改写。不放宽成自动选择最后一个值，也不把供应商超时藏进默认通过。
7. 新契约默认live6正项：1通过、1排除187.73秒。真实固定知识/别名、新页面初始0、点击一次后1、正式passed；終态重投新增业务/模型请求均0。live5负项和live6正项是两批验收，原其他失败保持。最终十九模块214通过19门控跳过79.32秒，含实际Chromium；初次历史Prompt完全恒等测试仍期待旧指令，1失败212通过19跳过82.92秒，明确只允许新增比较条件说明后复跑通过。compileall实际退出0，没有把门控跳过计作通过。

运行沿既有默认配置复制到隔离 MySQL，项目、冻结版本、任务、结果、ToolJob 与资源账本在隔离 PG，队列 Redis，原件 RustFS。生产 API、规划/执行/清理 CLI 实际启动；不扩大资源配额或手工改锁。API 下载与 RustFS 字节一致，持续命令的 transcript stdout 与持久 ToolJob 对账。

供应商私有推理只记录计数；请求无授权头，纠正响应仅导出公共字段。旧失败结果与上批原件保持不变。没有新增表、依赖、全局模型设置、前端变更或完整 App 验收声明。

## 尚未完成

简单默认UI正式断言已按上述范围验收，复杂场景/多候选补全/其他模式/完整App仍未验。只读纠正非流式3次HTTP超时仍需解决；既有generation_pipeline.invoke_frozen在新冻结任务选择stream_handler，ModelRuntimeService保留多传输及能力映射，是下一步依据。纠正阶段须冻结实际策略、保留旧意图/响应与未知状态，不能静默重发或把某供应商写成唯一模型。准备/unknown/计划修订、selector修复/失败定位/复杂响应因果、API自动续期/Cookie/并发刷新、持续容量/AI流式指标、安全故障、全HTTP恢复/历史迁移以及两份旧Worker原件仍保持未完成。
