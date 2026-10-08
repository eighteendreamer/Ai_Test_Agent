# 文档、frame与弹窗身份研究

2026-10-08，基线d03ab1e5。完整P0—P5继续；本项按原BrowserDriver/事件消费/PG/知识链路实施，不另建录制执行器。

## 已复现与信源

实际Chromium CDP、两本地HTTP来源及保存HTML，四场景4失败6.88秒：同源导航、跨来源导航后的按钮没有事件；已有跨来源iframe及其嵌套frame没有安装脚本；弹窗有脚本但enabled=false，且finish拒绝多page。原states/events/receipt/XML/log保留在p0-recording-scopes-reproduction。不是JS单测模拟，尚未接PG，不能称完整录制产品验收。

根因：add_init_script仅覆盖之后创建/导航的frame，当前注入只evaluate每个page的main frame；控制只evaluate主page。新文档初始化enabled=false。top统一seq依赖按来源sessionStorage，跨来源和不同窗口不能保证会话唯一，nested postMessage也只监听top。

查证：本机playwright async_api/_generated.py的BrowserContext.expose_binding返回Promise并提供原生page/frame；add_init_script覆盖新page和frame；Page.opener判断真实弹窗归属；Frame.frame_element返回父文档中的元素。官方依据：[binding/init](https://playwright.dev/python/docs/api/class-browsercontext)、[opener](https://playwright.dev/python/docs/api/class-page#page-opener)、[frame element](https://playwright.dev/python/docs/api/class-frame#frame-frame-element)。本机接口已读，不能套用新版Disposable清理方式。本地Agent轨迹与协议鲁棒性课程要求验证轨迹、身份、终止及恢复，不能只看最终状态。

## 采用做法

新增版本化recording-scopes-v2，由宿主登记原生page/frame与每次文档身份；每文档局部seq从0开始，宿主按收到顺序分配现有会话全局seq，同document/local_seq重试沿用原幂等身份而不新分配seq。原件携带局部身份，PG既有page JSON保存recording_scope；不改旧事件/唯一键、不依赖页面sessionStorage保证跨来源身份。

JS保留v1模式供现有Electron及旧输入；Python原两驱动先显式使用v2：全部当前frame注入，新document向原binding登记并取得当前capture状态。仅所选根page及原生opener链归属它的弹窗进入本录制；同context无关标签不采集。子frame直接调用原生binding，不借postMessage补发。frame_path记录实际父frame元素id/name/src/顺序，不伪造定位。

暂停/结束枚举归属page的所有frame，逐文档确认；pagehide冲刷已接受输入并发送离开文档水位，已离开的文档也必须有收到的结束水位与完整局部事件。每文档缺口、未知离开/关闭、未登记范围、native来源不符及队列丢弃均显式保留；顶层回执不能替代子frame。宿主全局水位继续走原消费检查点/PG验证，另核对每文档局部范围。回执与page recording_scope落原PG字段；无新业务表/依赖/可编辑配置，超时沿用MySQL创建时冻结的recording.finish_settings。

这属于原多范围采集架构缺失，不用固定等待、单独开启frame后忽略身份或重写seq补丁。先实际验证Python共用链路，随后接原Electron的native execution context/popup采集；Electron未接前保持v1范围不足明确failed，不虚报三驱动已全部支持。完整App/人工DeepSeek、响应/清洗/知识/AI、Docker及其他P0—P5继续。

## 必须验证

重跑四个真实失败输入，补重复投递/篡改、跨来源回返、关闭/移除frame、暂停后新scope保持关闭、无关标签隔离、最后输入及旧v1回归。再接实际MySQL/PG/RustFS/图与正式项目知识读取；实际范围身份与原局部值必须可对账。未获得pagehide/缺水位不能归为成功。测试和生产能力分别记录，所有失败保留。

## 实际实施结果与仍待实施范围

首轮修改后2通过/2失败5.21秒：嵌套frame和popup已采齐，导航后点击也恢复，但旧document的pagehide binding没有到宿主，完成水位不确认。保留该失败后改为beforeunload更早送达边界，pagehide仍补发；取消导航时当前文档的后续finish重新确认，不以旧边界代替当前范围。没有离开水位或范围不齐依然未确认。

四实际场景及旧驱动合同第二轮32通过/2失败5.61秒，失败是原合同在open后、inject前调用toggle，根page未登记owned集合；补充toggle的根page归属，没有删合同或弱化断言。原十五模块回归161通过21.05秒。两驱动API/实际MySQL、PG、Memgraph及项目知识初轮12通过27.39秒；补原截图路由上传及RustFS读回后12通过31.45秒。派生真实原件的六项身份/局部缺口/PG来源错配0.50秒；暂停后新frame两项10.65秒通过。

第一次十八模块XML186通过0跳过78.66秒，但审计发现存储测试遗漏query的limit、kind使用目录复数，TypeError被保存而未重抛；因此此前绿色结果不证明知识成功或异常后的字段断言。十个finish-error原件保留。修正为kind=recording/limit=1并重抛所有结束验证异常后，完整重跑186通过0跳过86.92秒，十个新知识工件存在且无finish-error。JS最终28通过1.60秒；原26项兼容也先复验3.63秒。actual managed使用原运行时headless=False，不以替身代替受管浏览器。完整事件与PG字段、十份RustFS截图字节、逐doc/local_seq/page/frame/frame_path及MySQL冻结策略、原知识读取已检查。原知识查询仍主要返回图摘要/回执，不称完整操作原文已供AI执行。

未改公共路由/DTO/数据库结构；新协议是Python驱动内部回执，存到原泛型finalize_metrics，原Electron公共capture-finished仍v1。未重跑Vue全构建，py_compile/node --check仅附加。Electron native execution context/popup协议仍待接；关闭/移除frame、回返/BFCache和重复/并行attach、更多网络故障待验，不能把当前十个场景当全生命周期。完整App/人工DeepSeek/响应/清洗/完整知识/AI、Docker及整个P0—P5未完成。
