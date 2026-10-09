# 2026-10-09 性能原件持久化、冒烟与执行终态研究

基线cf703e05；上一目标轮提交测量契约和实际k6四场景，是进展。本轮继续完整P0—P5，默认Step按用户决定不调用。

## 现有依据与待复现事项

- PerfRunnerService._run_in_docker/_run_local在TemporaryDirectory内写脚本/读summary，返回后删除目录；execute只保留stdout末2000字符和解析指标，inline:summary.json并不指向可下载原件。完整stderr和JMeter报告资产没有进入生产工件链。
- ArtifactStorageService已有store_uploaded_bytes/read_object_uri、RustFS客户端及生命周期管理；AgentCaseReceiptService以原件hash+URI+大小记录证据，不需再造存储客户端。ToolRuntimeService持有共享服务，性能模式初始化却没有传它；store_output_artifacts只处理仍存在的文件path，无法找回已删除原件。
- ToolJobService将URI/元数据存PG，sessions/{id}/artifacts/{id}/download按所属会话读取RustFS。性能模式只投影三件inline报告，烟测失败没有原件工件，成功smoke_result也没有进入后续运行状态。
- _validate_smoke对无summary不检查、JSON异常pass后继续、非零exit且有summary继续，expect_extract仅在stdout找字符串；这不是证明响应提取成功。先复现缺件/损坏/真实非零退出，不盲改提取契约。
- runtime允许非completed但有raw_metrics进入报告分析；PerfReportBuilder缺少进程完整性判定。不能将“有请求样本”当作引擎完整成功，阈值违约的非零退出仍须保留实际指标和独立SLA，不能全部笼统丢弃。
- 本地课程性能测试-通用篇第655—686行要求逐级压测及实际错误率/RT；本批只做短时受控证据链，不称容量基线。测试复用实际DockerResourceManager、Redis租约/PG分配、隔离MySQL/PG/RustFS、独立API和受管http-echo，不扫描/压测DeepSeek。

先用当前缓存k6原命令及自定义summary/teardown产生真实缺件、损坏和异常退出，保存脚本、原ExecutionResult、原smoke/report与进程输出，再确定契约。候选方向为共享RustFS服务在临时目录删除前保存原件、PG工具输出保存hash/URI及缺口，失败和通过都投影可下载工件；存储失败明确不可当作证据完整，不自动重复压测。已存历史结果不改写，旧字段兼容读取。何种原件必需、失败如何保留部分证据、进程终态如何影响判定，须据实际复现结论后落实，不能先新增未经验证的门卫。

首次before验收1失败18.99秒，原因是测试漏传现有PerfTarget必填name，尚未运行k6，不能当作生产故障。实际缓存k6 run --help给出summary-mode/summary-export，没有旧no-summary旗标；测试修订为现有summary-export在本测试容器指向/dev/null，取得真实exit0但无汇总文件。损坏场景用k6官方handleSummary生产真实非JSON文件；teardown故意抛错误，保留引擎实际响应和退出码。不调用外网、不修改系统/dev/null或宿主文件。

## 实际复现后采用的实现

reproduction：1失败28.06秒，四种实际smoke全为true，预期true/false/false/false。缺件与损坏实际exit0；teardown实际非零且原汇总有完成样本，load状态failed却报告pass，原件保留。这是结果契约和采集生命周期缺失，不是增加一个异常if即可修完。

采用现有共享ArtifactStorageService，在临时目录离开前上传原脚本、完整stdout/stderr、summary及引擎输出文件，按分块计算SHA-256/大小；最后上传含所属正式项目/会话/运行/尝试及实际退出码/容器身份的清单。继续由ToolJobService把URI/校验值和业务结果存PG、沿原会话工件API下载，不增加表或另一存储客户端。部分上传失败保存已成功引用和明确缺口，不重跑压测；报告不能因此pass。共享服务由已有生产ToolRuntimeService传入性能模式和runner，不由每次runner私建客户端。

ExecutionResult/PerfRun/SmokeResult增加evidence对象，原运行字段保留。正常、失败、烟测工件都投影；成功烟测保存在模式状态和后续run，不因继续执行丢失。空/损坏汇总、缺失失败率、非法数值、非零退出、超时明确不通过，不能吞错。仍保留既有>50%烟测失败策略及提取逻辑，未证实stdout字符串能证明响应提取，后续单列。

独立SLA真实违约继续fail；有合法样本但进程异常、未知终态或新证据不完整，结论inconclusive；阈值违约产生的非零退出仍保留测量和违规。旧已存报告不重写，新增字段默认空兼容读取。local超时保留TimeoutExpired实际已捕获stdout/stderr和临时文件，不能空置后声称完整。原下载API对声明hash/大小的新增工件核对原字节，实际篡改才拒绝；无校验元数据的旧工件维持原读取。验收要包含真实Docker、真实本地子进程/超时、实际RustFS/PG/生产API重启、正常与失败下载和项目隔离；受控工具执行不等于默认Agent或完整App验收。


## 最终验证与限制

contract-first七模块77通过12.78秒；最终扩大19模块123通过21.42秒。verified1失败51.43秒是比较持久output_payload和运行时增加download_urls/download_markdown的展示投影，依据ToolRuntimeService._record_output逐字段修正并独立校验真实链接。verified-final1失败52.11秒是测试路径/download不存在，sessions.py实际路由/content，按现有契约修正，不能借此新增路由。download-verified1通过55.24秒；delivery1通过51.26秒额外保存24份原下载字节，前序原件不覆盖。

实际隔离MySQL/PG/RustFS/Redis、独立生产API启动/重启、受管http-echo和缓存k6。正常smoke/load通过且保留双执行原件，缺件/坏JSON/107立即停止；failed load虽有正常SLA仍inconclusive。本地真实子进程超时保留已输出stdout/stderr。真实第一份RustFS上传后注入第二次失败，只执行一次引擎，保留成功原件且inconclusive；不声称实际存储宕机。PG终态重放不增加引擎。重启后24份原件下载，哈希/大小/归属一致；跨项目404，同大小篡改409，恢复相同；8个测试完整容器ID均已不存在。

原四场景测量回归1通过35.91秒，PG保持pass/pass/inconclusive/fail。compileall退出0。未改前端，未重新构建或验收完整App；工具测试从guard阶段开始，不调用Agent/真实模型。真实JMeter文件树及进程、stdout代替响应提取的缺口、完整性能模式/容量/流式阶段指标、安全引擎仍未验证。实际命令PLAN6.89/IMPLEMENTATION.md，独立新审计保留旧审计155/153/413/200/408/151件字节，生产历史/配置未改。
