# 固定文档原文作为效果预期的依据

基线786b5b88。先读取原默认任务full-case.json、requirements.md、PG-planning.json及四条物理请求，并核对ProjectPlanningSources、原声明编译、分阶段Schema/Prompt/恢复、固定Agent上下文与PG对象证据。

原八步用例只保存GET和200等业务要求；DELETE仅存在项目文档原文。compile_relations目前以自身content或prepare.instructions解析expected_path_tokens，不能用该文档中的DELETE作为操作选择条件。该缺口独立于本次180秒超时；不能说超时已证明由此造成。实际第二逻辑回合106.085秒、第三技术查询56.697秒，绑定尚未返回。

已有做法→采用方式：ProjectPlanningSources已保存项目所属文档版本、完整读取范围、content_hash、原件地址及解析位置→引用该固定PlanningEvidence，不重新读取当前文档。literal_evidence.literal_value和typed SHA→继续按字面量路径与类型验证。原多声明意图/原ToolJob可靠返回→保存编译出的具体预期及原文证明，仍不编译业务操作。Agent轨迹与协议鲁棒性/评测工程化课程→原失败、固定版本、轨迹、unknown及恢复都保留。

新能力采用独立effect_schema=3项/声明类型和可选effect_expected_source_protocol=case-effect-document-expected-v1；仍使用已验v2范围、证据、覆盖及阶段服务。配置MySQL，新Prompt v6、关系和文档引用固定PG，原件RustFS。缺协议不接受新项/引用，旧v1/v2Schema、序列化和Prompt生成保持。

预期允许原自身路径或文档引用，二者只能选一。引用指定已读且该项引用的document evidence_id、完整content_hash、payload字面量路径、唯一原文摘录excerpt、摘录内唯一literal及text/json解码。结构化值可直接引用路径；文本仅精确定位与标准JSON标量解码，不让AI改写原文，不凭方法或关键词推断角色。保留文档身份、版本、内容hash、路径、实际字符/行位置和typed预期hash。JSON false/0/null不混淆；缺来源、hash不符、摘录歧义或无法解码明确给原校验反馈。

消费方范围：新Schema/政策及省略None序列化、阶段Prompt/选择/合并/恢复、规划校验、范围接受项版本、固定上下文/原声明意图、前端类型/开关/声明展示、真实原件/HTTP/PG/Result测试。PG JSONB和覆盖结果形状沿原服务，不新增表列/依赖。只允许当前项目已冻结文档；不是给模型直接填无依据常量。

验收使用原真实默认用例和原需求内容，不增加证据契约字段；在真实存储上传原件，固定文档后改当前版本/配置，原Run继续使用旧引用。实际创建两条、查询、只清一条/全部清理，核对原Job、证明、剩余对象和Result；原默认失败回放证明旧协议仍拒绝。先验受控真实链路，阶段预读/耗时优化另行查证，未改机制前不原样重复默认任务。共享消费完成、完整App与整个P0—P5继续未完成。


## 实施与结果

新Schema/源编译/阶段选择和恢复/上下文/前端已接入。26项最终后端、补日志后3项真实文档专项、旧61项回归、42项前端与类型/构建通过。原默认用例不加字段，实际创建查询清理及V2变更后保持V1来源已验；v6阶段丢回执/当前策略变化后的PG恢复已验。初回放2失败及前端1失败均是夹具输入/身份错误，原失败保留，未弱化产品。未改变阶段耗时机制，未重跑默认模型；完整范围与下一步见PLAN6.60。
