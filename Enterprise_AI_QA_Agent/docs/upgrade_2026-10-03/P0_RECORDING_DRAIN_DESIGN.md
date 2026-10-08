# 录制结束确认设计与依据

2026-10-08，研究基线33f15e99。上一轮已实际复现：HTML输入存在，浏览器seq=6，PG只有5条，无最后fill；stop仍completed。原失败工件p0-recording-embedded-scenarios/tail-input保留。

## 源码与采用做法

- recorder.js用WeakMap保存500ms输入定时器，不能枚举；SetEnabled(false)先禁用，回调随后不emit。改为仅保存未结束输入的Map，结束时冲刷并移除；暂停前也冲刷已接受输入。异步binding返回须跟踪成功/拒绝，失败内容保持pending，不把调用函数当交付成功。
- Electron部分批次原HTTP200可能只接受部分事件，当前不检查accepted/duplicates；后端bridge接受只代表进入队列。结束回执必须在浏览器完成、客户端每一批accepted+duplicates等于发送数量后提交，后端另核对实际PG。不重写原事件。
- RecorderSessionService.control同步占位finalizing后_consume_loop仅允许active，造成尾部丢弃。finalizing期间接收已采事件，直到驱动finish_capture回执；用同一EventChannel排空标记结束流，消费循环正常退出并冲刷buffer，不能用cancel代替排空。
- PG是权威：对照驱动实际发布seq和浏览器末尾水位，检查丢弃、解析失败、入库重试耗尽，再写图投影及completed。失败不写成功图；PG提交未知保留既有事件，以failed/未确认记录，不自动重开旧会话。
- 三个生产驱动共用finish_capture契约；不支持的外部驱动明确失败，禁止默认假确认。embedded发送带随机request_id的finish_capture指令，capture-finished路由只接受当前请求、registered会话；重复原回执可查，换内容/错请求拒绝。
- 结束超时使用既有MySQLApplicationConfigStore的recording.finish_settings，项目/全局读取并在创建会话时冻结修订到PG metadata；生产lifespan初始化和注入，不新增独立数据库。

既有状态、表和事件格式不变；新增回执字段/路由同步Electron、Pythonschema、驱动、生产启动及测试。停止超时不能回到active；finalizing/failed历史保留。ready未启动会话也确认浏览器后关闭。

## 验收与边界

先用上一轮真实HTML/原Electron/HTTP/MySQL/PG/RustFS/Memgraph重新验正常及立即停止；额外检查不足二十条、延迟HTTP、错误/重复回执、终态、DB异常。JS测试覆盖多个输入、敏感脱敏、异步binding和失败缓冲；真实CDP驱动输入后结束须原事件到达并停止。

当前iframe及多窗口汇总缺少稳定录制范围身份；不能凭单个top水位宣称全部完成。首个协议在存在未确认frame/多top时明确失败并保留范围缺口，随后继续补各frame结束与多窗口身份，不能把这批当作完整录制验收。人工DeepSeek、动作响应关联、清洗/完整知识及AI使用、全P0—P5继续未完成。

研究参考原base.py、playwright_common.py、embedded_bridge.py、recorder_session_service.py、recordings.py、recording_store.py、recorder-window.mjs及原真实失败。本地《Agent轨迹与协议鲁棒性》要求同时验证轨迹、权限、幂等、超时和重试，因此不以返回completed或现有数量对账代替端到端确认。无新增依赖。

## 实际结果

原Electron首次两个场景均失败（22.87秒）：最后fill已保存且PG水位一致，但受控窗口录到了额外真实点击，固定“四次”断言不成立。原binding/HTTP/PG证据均保留；不删除额外事件，改成验证至少四次自动操作，且全部binding点击及完整字段都入库，额外数量单独记录。额外点击的来源未证实，不称为人工DeepSeek验收。

原后端首轮142通过、1失败（17.88秒），因旧测试主动发布暂停期间seq=2后丢弃，却期待completed。新契约明确检查出该缺口，测试现在验证failed、missing_seqs=[2]且不固化图。这不是取消缺口核验以通过旧断言。暂停前输入与网络迟到的边界仍须后续修复/实测。

随后两个原Electron场景2通过（21.30秒）；新增协议/真实CDP及原十三模块152通过（14.27秒）；JS26通过（1.50秒）。最终同一版本追加真实HTTP503回执场景，与全部后端合跑155通过、0跳过（31.59秒）；有一条既有Starlette弃用警告。原Electron两个成功场景保持最后输入/全部binding原字段；第三场景MySQL配置一秒后真实HTTP503、结束failed/finish_timeout，不重开终态。真实CDP实际HTML立即输入后finish得到完整fill且流正常结束。

编译及node语法检查实际退出0，但不替代上述功能测试。没有运行完整Vue App、真实managed结束、iframe/多窗口结束、模型使用或人工DeepSeek；无生产历史迁移。配置产品编辑入口未接，本批通过既有MySQL配置服务保存/读取及PG会话冻结，后续补运维/产品入口。

追加三实际Electron场景3通过26.39秒，保存创建后MySQL当前策略实际读回，PG仍保留原冻结策略。追加ASGI回执路由及原路由回归27通过1.06秒，确认错误request_id为409且记录拒绝，正确回执200。全部0跳过；与合跑重复的测试不再累计。
