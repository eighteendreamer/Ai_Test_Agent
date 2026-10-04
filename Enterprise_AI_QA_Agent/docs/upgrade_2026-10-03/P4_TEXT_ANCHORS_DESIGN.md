# 实际读取原文的引用锚点：影响协议v5

研究基线1d42f7ce，2026-10-05。完整P0—P5未完成。

## 查证依据 → 采用做法

`DocumentImpactBatches.read_range/read_segment`已经返回固定原文、字符范围及解析块位置，`read_difference`的raw/section/table区间也保存原文和位置；`DocumentDifferenceRange`验证范围长度等于原文字符长度。normalized坐标不是原文位置，不作为引用锚点。

`DocumentImpactResponseProtocol`从实际user消息的primary/additional_fixed_reads累计已读范围，再核验原文引用哈希/切片、用例完整版本/字面JSON值。`impact_identity_bindings`已有S/P/C/D/R短引用，原wire先保存再绑定，不能模糊修复身份。采用同一职责划分：模型选择已实际提供的文本锚点T1等，后端从冻结消息中的位置/原文构造规范引用，再走原事实核验。

上一轮真实SSE两次以length/8192结束、正文0；有大量reasoning片段。源码仍要求模型计算start_char/end_char并输出原文。职责调整有依据，但没有因果实验能证明字符计算是全部截断的根因；新的v5必须独立实测，不把减少字段当成功证据。

## 新契约与兼容

新提交impact-call-v5，响应impact-response-v3。发现候选document_citations为T引用列表，不要求模型重复quote/偏移/UUID/hash；用例阶段继续R引用及完整用例字段判断。规范结果仍DocumentImpactDecision/impact-response-v1，结果API/前端字段及coordinator-v1不变。S/P/C/D/R、补读及完整JSON/说明保留。

每个user消息附fixed_text_evidence：按历史消息、当前primary及additional读取顺序建立稳定T目录。文本范围本身和与该范围相交的解析块产生锚点；raw/section/table实际差异范围也可产生锚点。没有读到的原文不预登记；normalized、空范围和用例文本不冒充文档原文。相同来源/范围复用同一T，续读追加不重编号，两个版本相同文字仍有不同来源身份。目录同时提供实际quote便于AI选择，完整primary/其他资料不缩减。字节容量由原调用估算及策略约束，超限拒绝、不截断。

核验时从全部原消息再生成各回合目录，目录篡改/未知T拒绝；规范引用的位置、quote及来源hash由系统还原，继续校验固定原文与已读范围。同范围不同原文拒绝，不做模糊字符串匹配；锚点不代表语义正确，所有候选待评审/部署未知。

旧v2/v3/v4工厂、请求全文/哈希、核验和终态保留。v4专项明确请求v4；v5专项使用当前新工厂。新增的是内部wire版本及派生目录，不新增可编辑配置或SQL列；既有MySQL策略、PG固定任务与RustFS对象继续复用。不实施未经验证的正式流式或reasoning参数，也不重发旧unknown。

## 验证

先用已保存真实文件/失败输入检查T覆盖与还原、重复引用及目录篡改、normalized和未读排除、续读稳定、False/0/null/最后步骤及v4原请求重建。再运行MD/DOCX/PDF×原生/LangChain实际HTTP/MySQL/PG/RustFS自动补读/规则传递/终态，以及独立生产API/Worker。最后真实默认自动工作流使用新任务，不预填响应、不改源模型或忽略核验，检查全范围、规则及完整用例实际判断；失败仍保留。

## 实际结果与后续边界

离线32通过/12跳过4.10秒；开启实际存储后7模块132通过/0跳过441.18秒。追加局部相交范围与同文字不同来源两个真实固定输入检查，13通过/6跳过3.70秒；6个live已在132中通过，13与前批重叠。独立生产API/解析/比较/影响CLI及实际存储/HTTP，MD正常场景1通过/0跳过59.26秒，6次物理消息逐一等于冻结请求且均v5。没有杀Worker，不能称中断恢复测试。本轮公开规范结果/前端契约、SQL表/列和依赖未变。

真实默认模型新任务1失败/0跳过608.01秒，Handler601.219秒。10逻辑调用/13物理POST，8份完整响应均通过绑定核验（4补读、4结束）；4 analyzed、2 unknown、5 unprocessed，三个完整8步用例实际判断0。最终partial、semantic pending_review、deployment unknown。10规则候选包含modified3/removed3/added4，来自重复证据的建议，不是10个稳定需求。实际候选含7→9天、删除优惠码、新增倒序导出；不能将发现候选当成完整三方语义通过。

完整旧文126字符、新文139字符，却有2原文与6差异发现单元；当前discovered_rules按完整候选body精确hash分组，不能合并不同说法/引用的同类业务候选。首请求20780字节、response协议5330字节，并只给一个原文版本；Skill三方读取与当前发现单元的指令需要阶段一致性研究。统计见p4-text-anchors-request-sizing.json。该静态观察与实际重复候选支持重新划推理任务，不证明全部超时的原因；下一步保留证据清单，另冻结任务及覆盖映射，先合法规则再全部完整用例，不能复制答案给各证据单元冒充完成。

源模型、SDK60秒、单调用120秒、输出8192未改；600秒仅隔离项目MySQL。原unknown/终态不重发，原件、wire、核验、对象及失败XML/log分别保留在p4-default-model-text-v5-first及p4-text-anchors-*。未接正式stream/effort，没有生产历史迁移或前端/浏览器重测。完整目标仍未完成，详见同步PLAN3.5.12/6.8及IMPLEMENTATION。
