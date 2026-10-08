# 操作后DOM观察研究与实施设计

2026-10-08，研究始于0d046514，实施核对基线091e4200，完整P0—P5范围保持。

## 依据与真实反例

读原recorder.js emit/扫描/MutationObserver/暂停/结束/离开，三驱动注入、Session创建、MySQL结束策略、PG事件、图固化/知识查询与默认规划，原公开脚本/Electron缓存。当前MutationObserver只增加总数，emit将此前累计值写到本次动作；扫描只在开启等入口安排且只top。既不能证明本次操作后的响应，也不能捕获随后动态新增元素。

受控HTML等待初始scan，再点击root，50ms后真正新增section/button/p，观察可见响应后停止。原CDP/headed managed经原API/MySQL/PG/RustFS/Memgraph均失败，2失败15.67秒；DOM确实变化，完整知识却无新增按钮。原HTML、response-dom观察、事件/HTTP/PG/截图和错误保存p0-recording-response-reproduction，不能以成功录制水位替代响应验收。

官方[MutationObserver.observe](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver/observe)、[MutationRecord](https://developer.mozilla.org/en-US/docs/Web/API/MutationRecord)提供实际变更目标和增删节点；[takeRecords](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver/takeRecords)用于结束前读取已检测但尚未回调的变更。沿现有observer、buildTarget/敏感字段、事件流/局部seq/PG及知识入口实现，不增加另一套录制。

## 采用做法与范围

新MySQL配置recording.capture_settings，创建会话时冻结到PG metadata；新会话使用固定响应策略，旧会话无策略继续原协议。时间、节点/文本/动作范围上限均由配置模型给出；不从浏览器用户输入覆盖。Python通过原Driver设置配置再注入，Electron脚本取得该录制的固定设置，不能沿旧全局脚本缓存混用不同项目策略。

新增page_response辅助事件，保存当前document_id与在本观察窗口内的action_local_seqs、实际变更数量、有限变更元素/原定位链、扫描范围与截断原因。关联只表明temporal_after_actions，不证明业务因果；多个重叠操作保留候选集合，不强行认定最后一个操作。无变更、达到最长观察时间、暂停/停止/文档离开都给出实际截止状态。样本不等于完整DOM或网络请求/响应；跨文档导航响应需要独立后续验证。

保留原操作事件，历史dom_mutation_count不能当作当前操作后的证据。响应记录按局部seq进入原传输/PG/图与完整知识。节点采集复用既有定位/敏感输入机制，不导出原DOM HTML、输入密码值或网络认证头；大变化保存明确截断，不称完整。

暂停/停止/离开先冲刷已接受输入，takeRecords并保存已观察到的响应，再关闭采集并给出原局部水位；不等待任意未来业务请求。真实缓存恢复沿原身份/消息去重。辅助事件不进入回放动作，也不自动证明断言通过；既有用例/历史保持。所有消费方和测试同步核对。

## 验收

真实三驱动动态DOM、延迟/连续操作、无变化、子frame、节点/文本截断、暂停/最后响应/离开及缓存回返；PG原件/公开API/知识逐字段、作用范围与项目隔离。MySQL修订冻结、不同会话设置隔离、旧策略兼容，以及response不被当作可回放操作。人工DeepSeek/默认AI实际使用、完整清洗/报告/App/Docker和其余升级仍分别验收，不缩小总目标。

## 实施中追加查证

输入事件500ms防抖会晚于页面同步响应；脚本反例实际失败，输出无response，不能把click修复当作所有操作已验。采用接受input即开观察窗口、等待最终fill身份关联；settled遇到尚未结算输入延后，固定max_wait排空已接受输入。change后原Map仍存同一记录会在结束时再发fill，结算即删除，结束先取出并清空已接受输入再发出，避免重入重复。旧防抖和终态恢复测试保持。

敏感父节点脱敏不足以保护其删除/新增子节点；实际脚本反例保存了合成敏感文本。采集时按变更目标及节点祖先继承脱敏状态，窗口结束再次核对，父文本跳过敏感/script/style子树。测试值仅为合成哨兵，不含真实密码。未承诺识别任意业务正文中的秘密。

原断言建议把dom_mutation_count当作操作后效果，源码明确它读取emit之前累积数。新草稿不再从该旧计数生成响应断言；page_response需固定协议、实际变化、元素及同文档操作身份匹配，才给low评审建议。历史草稿与运行不重写。

首次原Electron调用pause发生httpx.ReadError并在收尾超时，原日志/状态保存；未获根因证据。增加服务端请求起止/状态诊断后重测1通过17.74秒，扩大回归独立记录，不能把重测通过称为连接错误已修复。

本次最终23模块285通过、1默认模型专项跳过、274.89秒；JS46通过、前端typecheck/build通过。46真实场景/44完成查询逐字段核对，15源/测试文件hash在运行前冻结、运行后相同。具体命令、XML和限制见IMPLEMENTATION.md及p0-recording-response-validation.json；完整升级仍未完成。

最终审计追加：嵌套响应target的默认in_iframe=false不适用于子文档，按真实frame身份补正，原脚本及初始hash保留。重跑285通过1跳过262.22秒，JS46通过。生产冷启动1失败22.79秒，FK初始化顺序缺口在录制配置之前发生，后续必须修复。
