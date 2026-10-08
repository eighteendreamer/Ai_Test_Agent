# 录制文档离开与原生命令生命周期研究

2026-10-08，基线 `77ab0e0`。完整 P0—P5 范围继续保留。

## 调研与实测依据

- 原 `recorder.js` 在 beforeunload/pagehide 清空已接受的输入并发送局部水位；Python 与 Electron 保存原生来源、局部/全局 seq，不依赖父页面代发子页面事件。
- 已读取 PlaywrightBindingDriverBase、RecordingDocumentScopes、ElectronRecordingScopes、公开结束回执及真实存储测试。查阅本地《Agent轨迹与协议鲁棒性》关于轨迹、半成功和重复执行的要求。
- [Playwright Page.close](https://playwright.dev/python/docs/api/class-page#page-close) 的默认关闭不运行 beforeunload。因此夹具使用真正的 window.close、iframe DOM 移除和历史返回，没有用合成事件代替文档离开。
- [MDN pagehide](https://developer.mozilla.org/en-US/docs/Web/API/Window/pagehide_event) 不保证所有离开都通知；[pageshow](https://developer.mozilla.org/en-US/docs/Web/API/Window/pageshow_event) 可记录 persisted，以区分重新加载和缓存恢复。
- 本机 `electron.d.ts:7612` / [Debugger](https://www.electronjs.org/docs/latest/api/debugger) 的 sendCommand 返回 Promise，带 sessionId；原生 context/target 销毁通知与命令返回是不同事件。

## 先复现，后定设计

两种 Python 驱动各执行关闭弹窗、移除嵌套 iframe、返回主页面：6 通过，27.25 秒。实际 MySQL/PG/RustFS/Memgraph，原件 HTML、生命周期 beacon、PG 原事件与逐文档回执保留在 `p0-recording-lifecycle-reproduction`。最后输入存在且 PG 水位核验通过。所有返回均 persisted=false，不能算 BFCache 通过。

原 Electron 三场景：2 通过、1 失败，63.04 秒。首次 frame-remove 过早检查 Playwright 的异步 OOPIF 移除。改为等待实际 frames 列表后仍失败，43.23 秒：正常停止 HTTP ReadTimeout，原后台最终 finish_timeout；不是丢失最后输入。

增加原生 Page.frame 轨迹和结束阶段日志后，原失败再现43.16秒；额外读取原浏览器 GetState 后再现43.01秒。证据指向原生命周期协调：

1. 末尾 fill 与两个 close 已到原 binding，局部序列完整。
2. 子 target 已发出 detached；根文档仍可读取，in_flight=0、pending=0。
3. 宿主 pending 中有3个原生回复命令，finish 等它们；对子 realm 的 Runtime.evaluate 不在 detach 时结束，而在进程退出时才返回 target closed。
4. 因此不能把源事件已接受、子 realm 已销毁和回复命令结束当成同一状态。增加超时或重发事件不能修复此错位。

## 采用做法

在现有原生 context 上登记未结束的 evaluate 命令。contextDestroyed、contextsCleared、targetDetached、webContents destroyed 统一沿原 depart 释放该 context 的等待，以明确的离开错误结束命令 Promise。原 native sendCommand 仍接收成功/失败，避免后续拒绝无人消费；每次命令结束移除等待引用。

binding 已接受事件/水位之后，失去回复 realm 只记日志，不增加缺失事件；没有收到的局部序列或 close 仍按原规则 unconfirmed。活跃 realm 的命令错误仍正常报告。finish 继续等待实际事件处理、读取存活文档水位并核对全部文档，禁止直接把离开当作 complete。

不新增超时配置、数据库表、依赖或另一套录制状态。MySQL 仍保存原结束策略，PG 保存原会话/事件/回执，RustFS 保存原件和截图。共享 GetState 加入 in_flight 诊断字段，不改变结束协议。

第一次生命周期修复后原三场景2通过/1失败29.24秒：iframe 移除已完成且原水位/最后输入完整；弹窗关闭的事件与close也全部保存、PG verified，但 transport_dropped=1。原 close 命令接受后回传遇到 target closed，发生时原 contextDestroyed 尚未通知，按map存活判定错误地计成事件丢失。设计补齐接受与回复职责：binding校验/接受错误仍进原错误通道；接受后的回复失败只记录日志，由实际事件范围和客户端pending/结束水位分别判断完整性。不是按报错字符串放行，不伪造缺失事件或水位。

## 缓存研究边界

本机 Playwright coreBundle 的默认参数包含 --disable-back-forward-cache。独立试验只过滤该参数，仍未恢复缓存：第一轮1失败6.73秒；第二轮增加实际 Page.backForwardCacheNotUsed 观测，1失败5.06秒，原生原因 BrowsingInstanceNotSwapped 与 BackForwardCacheDisabledForDelegate，原件均保留。

此证据证明当前夹具没有覆盖缓存恢复，不证明录制器的缓存恢复正确。不得通过派发假的 pageshow 把它算通过，也不凭代码猜测改恢复逻辑。后续需要换真实支持缓存的浏览器启动路径验证。

## 完整 Chromium 的实际缓存反例与采用做法

依照 [Playwright BrowserType](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch) 的 channel=chromium 使用新 headless 的完整 Chromium，并保留仅过滤 --disable-back-forward-cache。真实 pageshow.persisted=true 已出现。首次测试等待load超时37.58秒：缓存恢复没有新load；改为commit并等待实际pageshow后，13.24秒失败。原 states/events/lifecycle 保存：原文档身份保留、enabled=false、in_flight=1；返回后的root点击未进入录制，finish等待旧绑定Promise超时。

根因是文档激活与交付确认没有恢复协议。采用 `activate`：只对真实缓存恢复调用，携带原document_id，由原宿主核对原生归属、保留局部seq并返回当前采集开关；不能从缓存中的旧enabled推测是否应继续。Python沿原page/frame归属恢复current文档；Electron还核对实际原生realm uniqueId，不凭页面传入ID跨realm关联。

浏览器保存未确认投递对应的原消息。在缓存恢复/activate成功后，以原document_id与local_seq重投原字节，由原宿主去重；已在宿主接受的事件不增加全局seq，尚未接受的事件补入原局部范围。废弃冻结前的Promise等待，仅由重投后的确认控制finish；旧Promise晚到不能把消息再次放入pending。不是重新操作页面，也不是重发业务工具副作用。恢复时宿主已暂停则仍暂停，但离开前已接受的消息仍可送达。缺来源、激活失败或局部范围不完整继续失败。

## 验收

原三种驱动全部离开场景、旧导航/暂停/终态、PG 与公开协议一并回归；末尾事件对照原 binding 全字段、完整全局/局部 seq、实际截图读回及项目隔离。保留失败原件。真实缓存恢复、人工 DeepSeek、完整知识原文供 AI、响应清洗及其余完整升级仍未完成。

## 就绪提交与立即开始的实际竞态

首个完整十八模块208通过/1失败203.08秒：PG已保存ready，control仍判内存launching而返回409。读取原 `_await_ready`/`_update`/`control` 与公开get_session证实：数据库可见提交与内存状态赋值中间存在await窗口，查询与控制不一致。不是录制数据缺失，也不是应重试用户操作的依据。

用原PG提交成功后暂缓函数返回，在真实HTTP上重现：`pg_status=ready`、`runtime_status=launching`，start已返回409；1失败8.45秒。原件见p0-recording-lifecycle-ready-reproduction。采用项目已有 PythonPlaywrightCliRuntime 的会话级 asyncio.Lock 模式：每个录制runtime有一个迁移锁；PG状态更新与执行状态回填在锁内，control的迁移校验/占位也在同一锁内。驱动操作与长时间排空在锁外，保留既有并发占位、合法迁移表及PG终态保护。真正尚未就绪仍按原规则拒绝，不增加重试、sleep或另一个状态真源。

锁属于运行时协调，不是可编辑配置或新业务元数据；不新建配置键、表或迁移。验收原两驱动同一PG窗口、重复控制/暂停/停止、浏览器关闭、故障回滚及完整原模块。

## 最终实际结果与限制

上述“缓存研究边界”与“验收”中的未完成描述记录当时状态。后续真实完整Chromium独立缓存恢复1通过7.51秒；原API/PG活动和暂停缓存回返2通过13.19秒。原文档身份、原消息重投、当前暂停状态、点击与逐文档PG范围实际核对，原操作没有重放。Electron本批回返persisted=false，实际缓存恢复仍未验证，uniqueId协议约束仅单测。

真实PG就绪窗口反例1失败8.45秒保留；修复后两原Python驱动2通过13.16秒。最终18模块211通过、0失败/跳过、207.58秒，一条既有Starlette弃用警告。JS30项及原生协议6项共36通过、2.21秒；vue-tsc/Vite构建通过12.71秒。附加py_compile/node检查退出0。

15个实际Electron场景（13完成、2项预期失败）与20个Python API/存储场景保留原件，核对PG原事件、水位和35份RustFS截图。完整人工DeepSeek、响应清洗、知识原文供AI、完整App/场景/报告及其余P0—P5目标未完成。详见PLAN.md 4.1.4/6.76与p0-recording-lifecycle-validation.json；实际命令见IMPLEMENTATION.md。所有失败原件保留，未重跑原样自主Agent超时任务。
