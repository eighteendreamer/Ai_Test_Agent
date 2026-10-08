# 原录制窗口的实际控制与事件传输研究

研究日期：2026-10-08；基线：05f7403d。先研究并复现，随后才修改生产代码。

目标链路仍为人工操作 → 操作及响应信息 → 清洗/遗漏检查 → PostgreSQL事件 → 知识投影 → AI读取/使用。当前步骤核验这条链路的入口，不把单独的浏览器驱动通过当作产品可用。

## 调用依据与待复现的问题

1. `EmbeddedDriver._enqueue_command` 保存 `{action, payload, recording_id}`，原 `GET /recordings/{id}/commands` 原样返回。Python路由/驱动测试也断言 `action`。Electron `applyCommand` 却只读取 `kind || command`，源码显示两端未对齐；需真实进程验证。
2. Electron `flushEventBuffer` 在非force调用且不足20条时返回；2秒定时器也调用非force，少量操作可能一直没有提交。不能以定时器存在证明定时传输。
3. Electron导航指令不等待 `loadURL` 完成；控制指令与新文档初始化可能交错。是否实际发生另查证，不能在没复现时扩大修复。
4. 后端stop先禁用再取消消费循环；JS debounce输入、Electron未发送批次及驱动队列没有结束水位确认。完整性检查只有已落库数量/内部seq缺口，不能检出尾部全丢。该结束契约需独立复现与跨端设计。
5. `ProjectPlanningSources.query(recording)` 当前只传图投影及指标，未传PG完整事件值；操作响应扫描也未在每次DOM变更后关联。后续必须验证AI实际读取，不能用图节点数量代替。

这首先是跨端契约及实际传输验证缺失，修复应放在原消息消费和原定时flush调用处，不再建立第二套录制器或关键词门卫。新配置仍只能在MySQL，业务/任务/版本记录在PG，已有Memgraph作为可重建投影。

## 采用的验证

用UUID隔离的正式项目、真实MySQL/PG/RustFS和原Memgraph提供者；启动原录制API路由，通过真实HTTP由原 `RecorderSessionService` 创建embedded会话；启动已安装Electron，直接导入原 `recorder-window.mjs` 创建原WebContentsView，并让原指令轮询、binding和batch实现运行。测试输入为实际保存的受控HTML文件，操作通过Chromium输入协议产生真实浏览器事件，不用手造RecorderEvent。保存原HTTP命令/批次、CDP binding、页面状态、截图和PG/图投影读回。

测试的控制条页面用最小静态夹具，仅测试录制窗口模块与API，不称作完整Vue App。自动点击也不称作人工DeepSeek验收。人工DeepSeek、结束排空、动态响应、知识原文与模型使用仍须继续。

Electron获取原窗口及WebContents采用[官方webContents文档](https://www.electronjs.org/docs/latest/api/web-contents/)，原模块的注入/截图继续使用既有debugger调用。直接CDP输入探针没有产生页面点击，因此改用项目已有的Playwright `connect_over_cdp` 对同一个原Electron WebContentsView执行实际点击/输入，并验证页面点击计数变化。不替换原录制模块、binding或批量传输。也查阅[Chromium Input协议定义](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Input.pdl)、[Electron ESM加载时序](https://www.electronjs.org/docs/latest/tutorial/esm)，核对已安装 `electron/electron.d.ts` 和Python实际方法签名；没有新增依赖。

## 结果

实际确认并在原模块修复两处：

- 原HTTP返回 `{action:"set_capture_enabled",payload:{enabled:true}}`；后端active，页面enabled=false，Electron两次记录 `unknown command ignored: undefined`。`applyCommand`现在优先读取action，保留旧别名。
- 修正指令后，用实际Playwright点击使页面计数变为2，原binding产生两个click和一个page_scan，六秒后PG为空。原两秒定时器现在以force提交部分批次，二十条即时发送门槛保持。

没有改动后端状态机、JS采集、事件结构、权限或存储配置。没有把未经复现的导航时序改成新机制。

| 原验证文件前缀 | 实际结果与边界 |
|---|---|
| p0-recording-embedded-first | 1失败49.37秒；探针入口顶层await ready阻止就绪，换为异步启动回调；不是录制缺陷证明 |
| p0-recording-embedded-start-reproduction | 1失败11.02秒；原指令字段缺陷得到实际证明 |
| p0-recording-embedded-batch-reproduction / native-reproduction | 各1失败10.72/10.94秒；直接CDP输入未产生页面点击，不能证明事件传输缺陷 |
| p0-recording-embedded-cdp-reproduction | 1失败46.49秒；新增CLI开关改变argv位置，探针错把入口源码当JSON；配置路径按明确文件名读取 |
| p0-recording-embedded-cdp-fixed-launch | 1失败11.76秒；实际点击与binding已确认，部分批次六秒未入库，证明定时发送缺陷 |
| p0-recording-embedded-fixed / visible-capture | 各1失败34.85/40.04秒；隐藏Electron view导致截图/绘制等待，探针改为正常渲染窗口；不当作生产录制功能缺陷 |
| p0-recording-embedded-verified | 1失败12.16秒；功能断言已过，探针清理glob误选screenshot-storage.json，显式保留失败并修正数字编号匹配 |
| p0-recording-embedded-final | 1通过11.08秒；原窗口/HTTP/PG/图投影/项目只读知识入口及RustFS截图链路通过 |
| p0-recording-embedded-scenarios | 1通过、1失败18.65秒；settled通过，tail-input实际漏录仍失败 |
| p0-recording-embedded-regression | 143通过13.32秒；包括原CDP真实Chromium、PG和Memgraph专项，0跳过；有1条既有Starlette弃用警告 |
| p0-recording-embedded-web | 23通过1.74秒；原recorder.js测试，不能代替Electron功能场景 |

正常场景按原API start→pause→resume→stop，实际四次有效点击入库，暂停两次点击不入库；同seq重复HTTP投递去重，四次真实操作保留。原close指令到达并关闭窗口。录制PG事件、图投影、规划只读入口及跨项目拒绝真实验证；原截图上传到RustFS，下载原字节、SHA及PNG内容已核对。测试实际配置服务使用隔离MySQL；业务使用隔离PG，原图为投影。没有真实模型调用，也没有人工DeepSeek验收。

## 下一步必须修复的根因

tail-input保存了真实HTML、页面实际输入值、停止前seq=6及原HTTP/PG状态；停止后PG只有5条，fill不存在，却返回completed且reconciled=true/degraded=false。已发生的尾部事件与尚未结束的debounce都不能通过内部seq连续性排除遗漏。

这不是在完整性结果上加一个空输入特判能解决的问题。需要先确定原三驱动共有的结束协议：停止采集前冲刷已接受的输入；客户端确认binding/批次排空和最后水位；后端接受关闭前的已采尾部，等待实际PG提交；再以水位、丢弃计数和实际保存核对是否完成。embedded下发指令仅入队不能当成已执行确认；超时、浏览器关闭和PG结果未知需保留原证据并显示未确认。现有completed历史不原地改写。本批**未实现这个协议**，活跃live回归的tail-input失败继续保留。

响应元素/DOM与动作的关联、截断/遗漏说明、知识完整原文和AI实际使用仍需随后完成。整个P0—P5目标未完成；不会把上述两个入口修复当作录制闭环。

清理夹具时曾因未经参数化的LIKE在既有SQLAlchemy/psycopg适配器报错（only %s/%b/%t allowed），改为参数化查询。清理只核对本批原录制/项目身份、UUID表名、表内唯一归属后删除测试自有表和图；原截图读回保存在本地证据中，已清除测试对象。原数据工件和失败结果保留。清理记录见p0-recording-embedded-cleanup.json，不作为生产迁移或业务数据清理能力。

交付审核首次比较binding与PG时在page字段失败。原因是审核脚本读取UTF-8 binding工件未指定编码，Windows默认解码改变中文；PG读取已显式使用UTF-8。对照两份原文件的Unicode字符确认一致后，审核脚本统一显式UTF-8读取，未改生产存储或原工件。该审核失败不能当作生产乱码的证据；真实tail-input漏录失败不受此修正影响。
