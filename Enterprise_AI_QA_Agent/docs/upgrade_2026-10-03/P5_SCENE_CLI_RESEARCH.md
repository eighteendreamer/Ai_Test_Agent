# 独立规划 CLI 的场景查询进程恢复

基线1776cdd9。已有场景查询、固定读取、SQL检查点和Worker接线；上一批测试进程中的提交确认丢失不证明独立CLI进程故障。该项属于已有实现待验收，先验证，不预设生产代码缺陷。外部模型按用户决定暂缓。

## 依据与采用方式

- src/cli/run_case_planning_worker.py实际创建planning_worker_runtime、RedisTaskWorker和PlanningTaskHandler。测试通过runpy启动该原模块，只在测试子进程观察真实SQL提交及固定读取，不替换模型/查询结果/租约或Handler。
- planning_task_handler.py的persist直接调用PostgresPlanningTaskStore.save_checkpoint；并行心跳每lease_seconds/3续约。planning_workflow.py先保存pending_decision，再读取并保存query_index。故可在query_index=0/1提交返回后阻塞原执行协程，父进程核对PG行后kill该确切PID。
- postgres_planning_task_store.py的claim只在PG clock_timestamp判断租约过期后领取，保留首次deadline_at，seed与固定版本不重建。默认task_outbox_lease_seconds=30，沿用不缩短、不手工expire、不修改数据库时钟。
- test_case_planning_tasks.py已有实际MySQL模型配置、localhost HTTP/OpenAI SDK及独立正式CLI方式；test_execution_worker_cli.py已有owned Processes、环境序列化、PG/MySQL/Redis/RustFS隔离和清理。复用它们，不启动外部默认模型。
- test_recording_scene_product.py已有实际HTML/CDP采集、main/API创建整理场景V2、App构建、API重启和场景头V4。复用回调在initial启动CLI、restart恢复；正式用例无场景引用，受控HTTP请求先导航再读精确版本，当前政策变化不能改变旧seed。
- Redis的默认回收等待120秒，与PG租约不同。隔离测试沿既有CLI样例使用1000ms传输回收以缩短无关等待；PG租约仍保留30秒并由服务器时间自然过期。该差异必须写入验收范围。
- 本地课程Agent轨迹与协议鲁棒性.html:1189要求异常注入和恢复测试；项目AGENTS规则另要求终态不可重领。终态再次向真实Redis投递后运行正式CLI，核对模型HTTP和固定读取次数、attempt/结果均不增加。

## 验收设计

logic/effects × pending-read/read-ack，实际独立进程、控制HTTP、真实MySQL/PG/Redis/main及新CDP录制/App。保存原HTTP上下文/响应、SQL seed/checkpoint/attempt/截止时间、提交标记、子进程stdout/stderr、原录制及场景快照。kill前确认子进程存活、标记与PG一致；kill后确认退出码非零。恢复只读取原V2，已提交读取不重复；最终E引用还原正式证据ID。原件和前批工件保留。

范围不包含外部AI质量、真实模型自主查询、UI执行与断言或操作副作用恢复；本批仅验证只读规划进程故障。无生产表、依赖、全局配置、模型接入策略修改。

## 实际结果

### 7.18 独立规划CLI的场景查询真实进程恢复〔2026-10-11〕

基线1776cdd9，依据见P5_SCENE_CLI_RESEARCH.md。该能力已有生产实现，本批补真实独立进程验收，未修改生产代码。沿原CLI、planning_worker_runtime、RedisTaskWorker、PlanningTaskHandler及模型适配器；受控localhost HTTP模型配置存于本轮独立MySQL表，真实PG固定任务与场景、Redis投递、main/CDP新录制和已构建App。

首轮1失败24.60秒：测试夹具给只读派发流属性赋值，Worker未启动；改为登记流并核对派生属性。第二轮1失败24.80秒：真实HTTP和SQL检查点已到达，但虚拟环境启动器PID与Worker PID不同，原单PID断言错误。沿项目既有taskkill /T /F回收确切owned进程树，保存启动器/Worker/父PID、退出码及tasklist缺席证据；不改变生产租约。修正后单项1通过65.49秒，全部原失败原件保留。

最终complete **4通过243.89秒，0跳过**：logic/effects×读取前/读取结果提交后kill。每例真实SQL提交返回后保存观察标记，父进程核对PG再杀进程树；实际Worker消失。PG原30秒租约由服务器时间自然到期，不手工expire，不改时钟；隔离Redis传输回收1000ms，区别于默认120000ms，不能将此批称为默认传输等待时长验收。API重启、场景头V4、当前MySQL政策关闭查询后，原seed、v13 Prompt、原场景V2及首次截止时间仍保留。第二attempt理由lease_expired；每项4次真实SDK/HTTP、完整场景读取1次。读取结果已经提交时恢复不重读；终态重投原Redis并运行正式CLI，追加模型HTTP/读取/attempt均0，原计划及证据整行保留。证据阶段E引用还原正式身份，not_executed不当作通过。

兼容性初查25通过1门控跳过8.62秒；启用独立真实存储复验 **26通过11.23秒，0跳过**，涵盖旧Schema/Prompt/投影/引用、旧文档来源及Worker生命周期，不累加两次数量。四次实际App构建/vue-tsc/Vite退出0、页面错误0；compileall退出0仅补语法。原录制和PG原事件整行不变。四组原HTTP正文/响应、SQL检查点/seed/attempt/截止时间、进程日志、原场景及页面原件保留。

范围是受控HTTP语义响应下的只读规划，不是外部默认AI自主查询/理解/UI执行/正式断言，也不是未知副作用或其他模式恢复。7.16/7.17历史记录中“独立CLI/真实进程kill待验”在本批范围内已补验，历史失败不重写。无生产表、依赖、默认模型、全局配置或旧项目回填；生产部署/历史迁移仍未验。原件、前序855工件、3个保护文件及三份方案同步见p5-scene-cli-validation.json。

当前未完成项继续按两类管理：

| 方向 | 已有实现、仍待验收 | 仍需补实现或查证实际缺口 |
|---|---|---|
| 模板、生成、导入与需求变更 | 默认/项目模板、Excel/JSON完整页面及普通模块生成已有专项；真实模型业务质量、更多容量/故障、业务评审与生产部署仍待验 | 根据新增实际失败定位补强；不把已验模板/页面重新列为未实现 |
| 按用例执行 | 真实AI项目选取、排序、补全、工具执行、断言、回执与结果完整闭环 | 完整准备状态、未知副作用只读查证、执行中计划修订及其他模式仍需逐项核验补齐 |
| UI录制与知识 | 场景编辑/步骤/别名/查询、固定规划与独立CLI恢复已有专项；人工DeepSeek和默认AI业务使用仍待验 | 清洗干扰及遗漏诊断、复杂网络/跨文档/Shadow DOM关联、场景预期技术效果声明、selector修复评审和失败定位报告 |
| Agent API | 依赖来源、响应提取、参数传值等专项已有；完整默认Agent与异常/并发/幂等组合待验 | 鉴权续期、未覆盖故障和结果呈现按实际缺口补齐 |
| 性能、安全 | Docker原输出、工件、解析及报告已有专项；完整Agent闭环、其他工具和故障待验 | 逐级容量、负载/环境可比性、AI流式阶段指标及其他安全发现证据仍需推进 |
| 模型适配、重复请求与交付 | 现有多传输/profile及终态映射已有专项，外部多供应商/推理/媒体质量待验 | 全阶段逻辑调用/SDK重试/物理HTTP故障追踪、生产历史迁移与投影核对、旧Worker汇总原件追踪 |

外部AI按用户决定暂缓；旧Step200/403均为历史，不推断当前账号状态。完整P0—P5目标继续active。

实际命令：Agent_Server内RUN_LIVE_SCENE_CLI=1、RECORDING_SCENE_EVIDENCE独立绝对first/verified/tree-verified/complete目录，运行`..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_scene_cli_live.py -q --junitxml=../docs/upgrade_2026-10-03/p5-scene-cli-complete.xml`。合同复验RUN_LIVE_CASE_PLANNING_TASKS=1，运行test_scene_query_contract.py/test_scene_planning_contract.py/test_planning_worker_runtime.py，输出contract-live。按白名单中文commit，不推送/PR。
