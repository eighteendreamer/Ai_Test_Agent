# 录制知识完整事件读取研究

2026-10-08，基线5711b37e。完整P0—P5升级目标继续保留。

## 当前事实与采用做法

`RecordingGraphStore._prepare_graph`将PG事件经现有密码脱敏写入Action.payload_json，完整定位链、输入值、page_effect和原生范围都在该载荷中。元素指纹合并不删Action流水。但`_get_recording_subgraph_sync`只查询类型/序号/页面摘要，`ProjectPlanningSources.query(kind=recording)`将这个摘要作为规划证据。步骤完整入库不等于完整内容交给AI，这是读取契约缺失。

采用现有payload_json和RecorderEvent契约，扩展原子图查询：每个Action返回脱敏完整event，仍保留原nodes/edges/摘要字段。沿原project_id/recording_id读取，不新增存储层或配置。每步使用自身event.target，不能用合并后的Element替代原定位链。PG保持原件权威，Memgraph仍为可重建投影。

输出`event_payloads`携带协议、完整/部分、期望/可读数量及诊断。按Recording.step_count、Action.seq/type与载荷身份核对。缺失/坏JSON/载荷身份冲突保留摘要和明确警告，不伪造事件，不默默过滤后声称完整；日志不打印原载荷。旧记录可显示部分知识，不原地重写PG/历史计划/证据。每次新查询的完整内容沿既有PlanningEvidence hash保存，旧已冻结证据不变化。

既有写入密码脱敏再用于读取兜底；不扩大对未知业务值的改写。此项不证明所有外部输入或DOM已脱敏。现有page_effect仅描述已采到的信息，mutation_count在emit时读取，不能冒充动作后DOM已关联。本批先补完整读取及明确覆盖边界，后续仍要实际验证响应采集/清洗、AI使用、人工DeepSeek和完整App。

## 验证范围

先在原两Python驱动/Electron真实页面、原HTTP/PG/RustFS/Memgraph中断言知识事件与PG脱敏原事件全字段一致，保存失败与成功工件。旧录制原件用作离线回归；长值、False/0/null、未知嵌套、密码、坏载荷/缺失Action和跨项目另验。规划请求实际包含完整event，不以查询成功或模型响应代替真实AI操作验证。

依据：原recording_graph_store.py、planning_sources.py、planning_workflow.py、recordings.py和既有真实存储夹具；本地《Agent轨迹与协议鲁棒性》关于轨迹/状态及《线上流量闭环与评测集治理》关于上下文补齐、脱敏、去重。无新依赖。

## 实际结果

最初选错测试节点，未运行功能，原log/XML保留。正确选择原CDP/Electron跨来源嵌套场景，2失败18.38秒：完整PG事件与知识摘要不一致，每步event缺失。原件分别保存reproduction-python/electron及counterexamples.log/XML。

修复后离线四模块67通过4跳过6.24秒。受控模型契约测试从错误目录启动产生收集错误，未运行；正确目录首轮1失败11通过4.50秒，录制store替身缺目录list_sessions；补齐后12通过3.44秒。完整事件进入真实规划ModelInvocationRequest，固定计划读取不追随投影修改；这是受控模型测试，不是默认AI操作。新默认Prompt记录case-planning-v5，不更改已冻结Prompt。

最终20模块244通过、0失败、1跳过、219.94秒，一条既有Starlette弃用警告。唯一跳过是未开启默认模型专项，未作AI业务通过结论。15个真实Electron及20个Python API/存储场景，33个完成项公开HTTP/规划完整载荷与PG逐字段一致，2项预期失败保持失败。35份原RustFS截图下载核对。缺失/坏载荷、长值、unknown嵌套、False/0/null和密码、源范围/身份、历史冻结均有专项。

无前端实现改动/新配置/表/依赖/迁移，py_compile通过仅附加；本轮没有重跑前端JS/build。全部失败及成功原件、三份计划、5893个历史原件当前/HEAD字节及5源文件核对见p0-recording-knowledge-validation.json，命令见IMPLEMENTATION.md。默认AI实际使用、响应采集/清洗、人工DeepSeek、完整App和其余P0—P5范围仍未完成。
