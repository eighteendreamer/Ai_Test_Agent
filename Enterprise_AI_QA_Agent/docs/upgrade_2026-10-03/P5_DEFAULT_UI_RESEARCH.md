# 默认 UI 场景知识与正式用例执行调研

基线：41c24ed6。范围：现有 UI Agent 读取固定场景并真实操作、形成正式结果；不把受控模型的场景回执专项当默认模型通过。

## 依据与缺口

- `src/application/recorder/scene_knowledge.py`、`tests/test_scene_knowledge_live.py`：场景读工具已有实际项目隔离、版本/哈希核对、步骤/响应/别名读取；此前使用 coordinator 的受控调用。
- `src/modes/ui_automation_mode/manifest.py`、`tools.py`、`src/registry/agents.py`、`skills.py`：UI 模式、Agent 和 Playwright E2E Skill 均未登记 knowledge-rag。`CapabilityResolver` 与 `ToolExposurePolicy` 逐层过滤；工具本身实现不等于 UI 模型可用。
- `src/application/prompting/prompt_assembly_service.py:273`：UI 的自由测试与按用例测试均使用探索入口 Prompt，要求 always call ui-automation-runner first；`ui_automation_mode/runtime.py` 的 test_execution 尚不支持。相邻 API Agent 已按 execution_basis 选不同 Prompt，是可复用实现依据。
- `src/application/test_runs/agent_case_contract.py`、`schemas/scene_receipt.py`：普通正式回执可依据完整用例 JSON；场景预期来源的 schema2 是另一个可选协议，不是场景读取的必要前提。此次先验证固定场景知识查询和用例自身预期，不启用整个高级效果规划协议来掩盖简单 UI 路径的问题。
- `tests/test_recording_agent_production.py`：已有真实 CDP 人为操作路径、服务端授权来源继承与生产 Worker；旧默认模型未正式通过。沿用记录/审批/终态幂等方式；新的验收直接复制当前默认配置到隔离 MySQL，通过现有 SDK 探针，不使用改写响应的模型或保存供应商私有推理。
- 本地课程 `Agent轨迹与协议鲁棒性.html` 结果与轨迹共同核验：知识读取不等于操作、工具成功不等于断言通过。

## 采用做法

先用实际 registry→capability→exposure 和实际 Prompt 构造复现。确认后同步补 UI 模式/Agent/工具/Skill 的知识工具登记，权限与项目隔离继续使用既有服务。沿 API 用例 Prompt 对应方式，为 UI 的 cases 分支提供固定用例执行指导；自由入口不改，不增加模式或供应商特判。

真实验收使用独占本地 HTTP 页面，CDP 录制物理点击并建立场景版本/别名。正式 JSON 保存精确场景身份，要求默认模型读取固定场景、用浏览器实际操作、引用实际 ToolJob 结果与用例预期。服务端请求与新页面身份用于区分录制和执行，终态重投必须没有新增点击或模型请求。失败保存原证据；不提高原模型预算或把不合法回执改判通过。

## 实际结果与根因分层

1. 实际登记/过滤与 Prompt 复现为2失败1通过；补齐登记、按 cases 选 Prompt 后7通过。首个测试夹具遗漏固定身份、误用结果字段，失败原件仍保存，不把它们当生产缺陷。
2. `p5-default-ui-live1`：1失败128.63秒。真实 Step 已读取固定场景并真实点击，但 `command="open",args=[URL]` 被原解析器丢弃动词。原件重放专项再次明确复现。命令统一支持分开的动词/参数和原完整 args；导航授权和执行继续使用同一解析器。真实 Chromium生命周期等30通过21.36秒；最终分开命令的实际图权限/浏览器/ToolJob专项17通过7.01秒。该夹具最初未运行独立清理 Worker，关闭的浏览器仍占配额等待清理；后续启动生产清理 CLI，没有扩大配额或手工清 Redis。
3. `p5-default-ui-live2`：正/负两项测试失败，合计697.79秒。两项业务都真实点击一次，正式结果均error。正项公开终态有完整JSON回执，前置说明导致解析失败；额外非流式只读纠正3次HTTP尝试超时。测试还错误要求上传后本地文件保留；真实 storage.artifact_keep_local_copy 可以为false，已改为API与RustFS字节对账。补录原件并清理仅本次项目的对象，历史失败结果没有改判。
4. 回执帧解析沿现有planning_service._parse的fenced JSON实现，识别唯一完整显式块；多个块、截断、工具请求继续拒绝，所有类型及项目/运行/实际ToolJob校验保留，不自动更正字段或证据路径。真实公开回复的回归先复现失败；初次扩大测试暴露本轮日志变量名引用错误，已修正，61通过17门控跳过30.21秒。最终扩大159通过18门控跳过53.53秒，各批重叠不能相加。
5. `p5-default-ui-live3`：1失败330.55秒。实际默认配置/规划与执行CLI、生产API和四存储；固定场景/别名→新页面→初始0→真实点击一次→当前1，4份命令原件经API与RustFS下载一致，stdout与ToolJob一致。JSON块已识别，但候选使用不存在的`["output","stdout"]`，并把整个JSON文本与标量做equals；校验拒绝是正确行为，不能摘掉output或伪造路径。后续纠正无可靠响应，保存http_error。尚未到达终态重投断言，不声称默认UI正式通过或零重投实测。

前三次实际默认任务与首次尝试原件、公共PG投影和SDK计数保存。私有供应商推理只记录字段计数，不导出内容；纠正响应只导出公共text/tool_calls/response_summary。第一批浏览器原件补录时已无本地副本，只能声明RustFS原件下载和新SHA，不能声称与本地逐字核对。第二批脚本`p5-default-ui-evidence.py`验证指定失败运行的纠正对象SHA/size，并只清理其确切拥有的对象；第三批下载现场核对API、RustFS与持久stdout。

## 下一步仍需实现或验收

实际知识查询已打通；默认UI正式断言尚未通过。下一步核对`browser-control command eval`只有stdout而`evaluate_js`已有结构化result的差别，以及工具消息包装与ToolJob输出根的说明；依据现有解析能力提供可引用的结构化观察，保留原stdout，不自动修正模型错误路径。再执行明确初始条件的正/负用例、终态重投和产品流程。只读纠正非流式HTTP超时已复现，传输策略需要按固定模型能力单独调研，不能再次同参数盲跑或增加原预算。

本批无schema、新依赖、全局模型配置或前端实现变动；没有声称新完整App、多供应商外网、场景预期schema2、生产授权配置或完整P0—P5通过。
