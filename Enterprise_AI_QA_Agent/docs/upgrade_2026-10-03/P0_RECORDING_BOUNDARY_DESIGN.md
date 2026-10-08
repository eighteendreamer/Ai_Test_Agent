# 暂停交付与终态迟到写入研究

2026-10-08，基线bd972911。完整目标仍为人工操作→捕获响应→清洗/遗漏→PG→AI，不能以本批边界通过代替录制验收。

## 已复现事实

使用上一轮原Electron/HTTP/实际MySQL、PG、RustFS、Memgraph及真实HTML夹具。输入“暂停前最后输入”后立即pause，原binding发送seq=3，原消费循环在paused丢弃，六秒后PG没有fill。移除正常completed会话的bridge，重放原已保存事件批次，API返回200/duplicates=6且updated_at改变。原结果p0-recording-boundary-reproduction，2失败51.12秒，工件不改写。

暂停失败夹具退出时原窗口触发stop，客户端已结束无法回执，清理期间还保存了finish_timeout；不是额外业务通过，不计为新录制缺陷。真实暂停漏录依据是原binding、paused丢弃日志及PG读回。

## 根因和采用做法

1. control在await前占位paused，_consume_loop只接受active/finalizing；SetEnabled(false)冲刷输入，但两秒客户端批次没有暂停回执。复用上一轮浏览器finish和批次交付协议，增加pause_capture快照操作，保留队列可继续发布；事件队列FIFO checkpoint由原消费循环冲刷PG后确认，再将PG状态写paused。暂停进行中接收已采尾部，但并发控制拒绝；取消HTTP等待保留原暂停任务。失败/超时明确failed，不回滚成仍在录制。
2. stop和pause共用PG水位核验及失败处理；回执带operation，错请求/错操作/改写回执拒绝。暂停回执追加到原PG finalize_metrics并在stop结果保留；不新建第二套录制器/状态机，不新增业务表。
3. 未知bridge回落到PostgresRecordingStore.append_events，原方法无状态条件，重复投递也更新会话。项目postgres_document_operation_store已使用FOR UPDATE锁定业务行；在原事件追加事务同样锁定会话，明确missing/terminal时拒绝，随后才计算COUNT、插入及更新。原update_status在同一会话行UPDATE，自然与追加锁串行；同时解决并发COUNT产生step_count错账。不能只在路由先GET再写导致竞态。
4. 返回类型使用明确RecordingEventWriteRejected，未知bridge路由映射missing为404/terminal为409；原活动bridge与流式消费继续原路径。finished/failed/discarded历史不补写、不重置；保留原事件及失败记录。

沿用MySQL recording.finish_settings及创建时PG冻结参数，不新增超时配置到源码。三生产驱动共用pause契约；CDP与managed通过既有Playwright基类。范围身份/iframe/多窗口仍待实施，当前未确认范围不能判完成。

## 验收

先重跑两个真实反例，再跑正常/立即stop/HTTP503场景；补真实CDP暂停后继续、PG并发追加/终态写入、消费checkpoint与并发控制、取消等待、超时、回执错误。读取暂停回执、全部原binding及PG事件、最终图/知识及历史前后完整快照，不能只检查状态字符串。配置和历史原字节核对继续。人工DeepSeek、完整App、动作响应关联、清洗/完整知识/AI、Docker及其余P0—P5仍未完成。

## 实际结果

原反例修复后2通过19.53秒；原十四模块153通过13.62秒；新增边界协议六项6通过6.73秒。补错operation与暂停超时后，最终十五模块161通过0跳过18.90秒。原Electron五场景5通过0跳过38.36秒；四成功/一预期503失败，真实暂停输入与移除bridge重投均已核对。全部XML/log及输入原件保留，详见PLAN 6.73和IMPLEMENTATION.md。

实际PG隔离表：八路并发追加计数为8；持有原会话行锁时另一路追加在pg_stat_activity等待Lock，随后提交completed/failed/discarded，追加醒来分别拒绝。终态重复批次、新seq与缺失会话经原ASGI路由分别409/409/404；全部会话字段、事件前后相等。storage测试复用既有真实事件字节并派生seq，明确不称为八次原生浏览器操作。实际CDP在HTML填入暂停前与继续后两值，原消费通道保存两fill及pause/stop回执。

没有新表/依赖/配置；沿用MySQL冻结策略和PG原字段。BrowserDriver/两实现的异步流类型同步声明CaptureCheckpoint，原服务先处理检查点再解析业务事件。Electron实际五场景加载operation更新；recorder.js未改，本批没有重跑历史26项JS。py_compile/node --check仅附加检查，完整Vue构建/managed实测/人工DeepSeek与模型调用未做。
