# Electron 原生文档范围研究与实施

2026-10-08，源码基线a6f43697。延续完整P0—P5，不把本专项定义为完整录制验收。

## 先查证与复现

原recorder-window.mjs只在主webContents注册__qaRecordEmit，bindingCalled忽略executionContextId/sessionId；控制和finish仅在默认主context执行。导航的新文档enabled=false；iframe来源靠top桥，弹窗没有宿主通道。此前Python两驱动的范围实现不代表Electron可用。

原Electron、实际HTTP/API/MySQL/PG/RustFS/Memgraph与保存HTML四反例：同源导航、跨来源导航、跨站嵌套iframe、弹窗。4失败、5排除、31.12秒，见p0-electron-scopes-reproduction.xml/log及原件目录。导航/弹窗存在实际操作但PG没有对应事件；iframe结束不能确认。没有修改生产实现后重新命名失败为通过。

接口依据：本机electron.d.ts的Debugger消息包含sessionId，sendCommand第三参数可定向session；did-create-window提供实际子BrowserWindow。官方[Debugger](https://www.electronjs.org/docs/latest/api/debugger)、[webContents](https://www.electronjs.org/docs/latest/api/web-contents)、[Runtime协议原文](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/js_protocol.json)；本机版本及实际CDP仍须验证。查阅本地Agent轨迹与协议鲁棒性课程，按身份、完整轨迹与终态证据核验。API用法从这些信源核对，不把CDP addBinding当成Playwright返回Promise的binding。

## 实施设计

复用recorder.js的recording-scopes-v2。CDP原生binding外包一层Promise回执，按实际executionContextId/sessionId回复登记；每文档局部seq在Electron映射现有会话全局seq，原PG事件唯一键和消费通道不改。保留来源page/frame及原生frame树路径；重投不新分配seq，内容改写或来源错配明确拒绝。

主webContents及其did-create-window子窗口归属会话；不扫描全部webContents借其他窗口。OOPIF沿Target.setAutoAttach的原生session接入；工作线程不伪装frame。所有当前默认context及未来文档注入，导航继承采集状态。暂停/结束在全部当前文档执行finish，连同已离开文档的边界及事件范围一起核验；缺失不能完成。

新增范围协调模块负责原生context与文档，不在旧窗口文件堆多份状态分支。原窗口仍负责HTTP攒批/下行/截图。扩展原公开finish DTO允许v2 scopes，旧v1仍可读；所有消费方及真实契约测试同步。没有新配置、依赖或业务表，超时继续使用MySQL创建时冻结设置，事件/回执入PG，截图入RustFS。

## 验收边界

先重跑四反例，再跑原五Electron场景及Python/v1/服务/PG合同。逐文档确认实际PG字段、原绑定业务字段、连续全局seq、局部水位、最后输入、原知识与RustFS截图。保留所有失败。自动操作不计人工DeepSeek；图摘要不计完整步骤供AI。关闭/移除、BFCache、注入早期窗口、更多故障、完整App和知识/AI全链路仍分别验收。

## 实际结果与设计修正

等待初始登记及汇总所有target树后3通过/1失败；原初始化open与窗口首次加载重复导航，导致OOPIF会话销毁。客户端明确首条相同初始化导航已加载，后续同URL导航仍执行。四反例4通过35.22秒。

动态新增frame的操作虽然入库，其父路径却为[]，扩大回归168通过/1失败/8跳过。Page.frameAttached/frameNavigated补原生父链，不能仅依赖各OOPIF自己的根树；嵌套及暂停两项2通过20.41秒。实际已采事件通过binding重投并变换JSON键顺序不新分配seq；内容改写向原context返回拒绝，结束failed，身份专项2通过19.87秒。

首个十八模块192通过/1失败，Windows测试文件替换被读取句柄拒绝；这是控制器共享可变文件协议的缺陷，改为编号不可变命令文件，不以重试隐藏。完整重跑193通过0跳过149.32秒，JS按Vitest正确入口28通过1.30秒。原node --test错误和所有失败保留。公开finish DTO已允许v2 scopes，旧v1合同实际回归；没有前端发送方、Vue变更、新表/依赖/配置或生产迁移。

十二Electron原HTTP/实际存储场景、两Python驱动原模块都实际运行；十完成/两预期失败，原始业务字段、来源/水位、项目知识和RustFS截图核对。仍需关闭/移除/BFCache/并行attach、更多网络恢复，完整响应/清洗/知识原文供AI、完整App/人工DeepSeek及全部P0—P5；目标不缩为本专项。
