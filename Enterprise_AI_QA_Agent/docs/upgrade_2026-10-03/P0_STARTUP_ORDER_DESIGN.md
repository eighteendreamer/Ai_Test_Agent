# 生产冷启动依赖顺序研究

2026-10-08，实施基线7497b469；原失败在4e35de24。完整P0—P5目标保持。

## 已复现事实

实际独立生产API/解析Worker专项在新建隔离PG数据库中失败，退出码3，1失败22.79秒。原stderr保留于p0-recording-response-production/document-api-first.stderr。main.py约418先调用ToolJobService.initialize，PostgresCaseActionAdmission.initialize随后创建消费表；其run_item_id外键引用PostgresTestRunStore._item_table，但原运行服务到约556才初始化运行表。项目/用例/套件链尚未完成，不是录制策略格式问题。

查证ToolJobService.initialize同时负责原工具表、动作关联表和恢复陈旧任务；TestRunStore.initialize仅执行已有幂等DDL，TestRunService.initialize随后负责DDL及租约/审批/取消恢复。运行表还引用项目、套件、用例及用例版本，故不能简单提前到最初工具服务位置创建。

## 采用做法

保留工具服务构造，等项目/用例/套件已初始化后，先用现有TestRunStore.initialize创建运行及条目表，再初始化原工具服务及依赖外键，然后继续原TestRunService.initialize恢复流程。服务下游、原Schema、外键、任务/租约恢复和资源清理不变。已有DDL均CREATE TABLE IF NOT EXISTS，后续运行服务初始化仍可幂等执行；不增加“忽略缺表”分支或创建占位表，不改变公共接口，不增加依赖。

## 验证范围

使用原真实失败的独立生产API/独立DOCX解析Worker测试：新数据库启动、实际MySQL采集策略读取、PG运行表/消费表/真实外键检查、DOCX上传和解析、API重启、原终态任务重投及历史读取。原失败保留；实际命令与结果另记。还运行原生命周期清理/运行恢复相关回归。这个专项证明生产启动和原解析链兼容，不代替人工DeepSeek、正式App录制或默认AI业务操作验收。

## 实际结果

main.py只调整原初始化顺序，并记录lifespan_run_schema_initialized、lifespan_tool_jobs_initialized。保留原运行服务恢复及原外键，无新配置键、表、依赖、公共契约或历史改写。

生产DOCX专项首次在API已成功启动、外键及MySQL配置已核对后，因新增证据保存直接序列化SQLAlchemy RowMapping失败：1失败23.17秒，p0-startup-order-production-first原日志/XML保留。读取项目SQLAlchemyCursor及既有测试的dict(row)转换后修正测试保存；没有改变生产查询或放宽验证。新数据库重跑1通过60.07秒。空库两张表不存在由测试事先只读核对；生产API建成两个已验证外键，recording.capture_settings来自MySQL。重启后这些事实保持。实际DOCX原字节、RustFS读回、独立解析Worker、原配置冻结、终态重投不再解析均通过；解析不调用模型。

独立执行Worker专项1通过75.67秒：完整生产API多次重启，保留原审批/attempt/已创建对象；实际POST→GET→DELETE，五个受控模型阶段HTTP调用，零残留，终态重投无新增调用。它证明原受控执行链，不等于默认自主模型已通过。

扩大回归首轮24失败、54通过、2跳过80.62秒：23项为本次命令遗漏QA_CONSUMPTION_PHASE_DIR或QA_CONSUMPTION_EXTERNAL_DIR；1项旧审批恢复测试替身没有当前_start_item_sync先读条目/运行的能力。原输出保留。补齐证据目录及两个真实SQL池开关后，生命周期/依赖/消费/清理65通过、0失败、0跳过157.97秒。旧测试仅补原条目查询和既有运行读取，审批attempt和首次时间断言保持；该模块15通过2.59秒。运行存储生产代码没有为该替身新增分支。

实际命令与证据目录见IMPLEMENTATION.md。冷启动修复已有实际验证；录制AI使用/清洗/人工DeepSeek/App、Docker和其余P0—P5仍未完成。
