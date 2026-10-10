# 场景知识进入现有Agent工具

基线cc2d74f5。目标仍为完整P0—P5；默认模型验收按用户决定暂缓。

## 已查依据 → 本批做法

- scene_store/scene_service已有实际PG不可变版本、完整录制快照、排序/别名/草稿转换；上批product-integrity证明原事件全列不变。新读接口读取同一资产，不新建知识副本或写图谱。
- ProjectPlanningSources目前只支持文档和原录制；规划Prompt/Schema和持久seed有历史等价检查。不能把场景伪装recording或让旧任务改读新版本。规划来源接入仍是独立未完成项。
- registry/tools.py的knowledge-rag已是safe查询工具，多个Agent可调用；ToolRuntimeService._run_knowledge_rag原只检索记忆及本地MD，没有场景PG读取。新增scene_search/scene_read两个只读操作，旧query路径保持。
- ui_automation/runtime带录制审批、探索和浏览器操作；不把只读资产查询放进此执行入口，也不加另一套浏览器执行方式。
- 现有attachment-reader读取SessionStore.get_session；本批以持久会话project_id核定项目，明确不接受模型选择其他项目。项目归档可读历史。scene_read必须指定版本和内容hash；同一场景当前指针变更不改变已读版本。
- project_store/case_store已有参数化ILIKE检索及limit+1/offset分页；场景目录复用同类SQL，默认排除归档场景。目录提供名称/状态/版本/hash，只读正文才返回完整内容、录制快照和别名target/page。
- 参考REPORT5.3与本地Agent轨迹与协议鲁棒性课程要求原始工具轨迹、权限、版本和重复执行可查；查询沿现有ToolJob真实PG输出保存，终态重投读取原Job，不重新选择新版本。organized不证明执行通过。

## 验收计划及边界

先用新的实际HTML/Chromium/CDP、production main与隔离MySQL/PG/Redis创建录制和场景，复现现有knowledge-rag返回空MD而不返回场景。再验证目录/分页/名称、具体版本/hash/别名/完整Unicode动作与响应、跨项目、归档读取、错误可见、原始事件与版本不变，以及API重启后原ToolJob回读和终态重投。原场景产品流程继续实际运行；相关回归与编译为补充。

本批不调用默认模型，不宣称AI正确使用知识或测试通过；规划目录、自动关联来源、录制清洗/遗漏/DeepSeek/失败定位、正式Agent完整执行和生产迁移仍需完成。无新依赖、配置或业务表。配置继续MySQL，业务版本/ToolJob为PG，已有大工件沿RustFS。

## 实际结果

旧实现实际失败42.07秒：新录制及场景V2已保存，原knowledge-rag返回Markdown而不返回scene_knowledge，证明缺场景工具读取。首次修复验证发生测试导入错误；两类失败分别保存，不能将原产品缺口解释成测试接线问题。

最终complete 1通过64.97秒：生产main负责创建录制/场景和完整built App，测试进程的真实ToolRuntime连接同一PG，经注册StructuredTool→权限治理→原ToolJob读取。完整content/source逐项相等；别名target/page来自原动作；跨项目读取拒绝。API重启、头版本变更及项目归档后，重建服务仍读原V2；两次终态重投复用原Job和输出。未调用模型/Agent完整图，不宣称生产主进程发起工具调用。此前final 1通过80.26秒为直接Runtime读取范围，保留原件。

查证已安装langchain_core.tools.base._parse_input：dict Schema直接返回输入，不自动应用top_k默认；注册治理仍要求非空query，实测使用“读取固定场景”。不为此修改通用工具验证。目录分页实现沿既有SQL，完整多页/容量、场景归档筛选未专项证明。规划来源自动纳入、默认AI使用/断言、人工DeepSeek、清洗遗漏、失败定位报告仍需完成。
