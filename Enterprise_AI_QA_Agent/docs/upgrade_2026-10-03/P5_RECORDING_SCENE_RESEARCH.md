# 录制场景、步骤整理与业务别名

基线5276887c。原目标继续保留；本批不调用默认模型。

## 依据 → 决策

- RecordingStore只追加PG事件；recording_case_draft_service用完整脱敏event构造步骤，保留观察并进入原评审链。保留原件，场景编辑另存PG资产和不可变版本，不改录制事件、已有正式用例或图谱。
- 本地参考REPORT第5.3及webui.py:2538—2618提供场景/步骤/别名产品方向；action_schema.py保存结构化步骤。参考别名原地更新不适合本项目冻结要求，只借鉴交互，版本随本项目治理。
- 本地case_store的项目唯一键、append_version/行锁及已存在的恢复入口是实现模板。场景创建有项目内请求身份；编辑使用父版本比较与行锁，同请求回读原版本，冲突409；GET可恢复未知提交，不盲重传。
- 原录制转换和步骤协议用于生成初始资产；整理请求只能选择原动作seq、调整顺序/说明/预期/别名。完整event和观察仍在初始快照保留；丢弃动作显式列为excluded_sequences，不按文本相同去重。别名引用原事件target，不用别名静默覆盖selector。
- 正式project_id每次核验。归档项目可读历史、不可新写。业务内容、版本、草稿属于PG，配置继续MySQL；现有Memgraph只为录制可重建投影，不作为场景版本的权威。新增PG表名沿DatabaseConfig约定，无新依赖。
- 新场景转草稿固定具体版本和hash，复用TestCaseService.create_draft及原review/activate/suite流程；不加浏览器执行入口，不改变Agent权限、租约和模式。

## 实施与验收范围

先使用新HTML、真实Chromium/CDP、独立production main和隔离PG/MySQL/Redis保存新的录制，再调用场景入口留存缺失证据。随后实现版本化场景服务/API和知识页项目入口。实际App验证创建、步骤编辑/调整、业务别名、历史浏览、重启回读、提交幂等/冲突、项目隔离、转草稿和原事件不变。合同回归与编译只作补充。

本批仅管理已完成录制衍生场景；录制前的场景规划、跨录制组合、可审查selector修复、默认Agent知识使用、完整清洗/遗漏、失败定位和报告继续独立推进。organized只表示用户整理，不表示已执行通过。原件删除仍是显式操作，已有版本有完整快照不跟随删除而改写。

## 实际结果与纠正

before1失败34.94秒：新HTML/真实录制已经入库，管理POST404。实现后api-first因误修改SQLAlchemy只读RowMapping失败32.55秒，事务未提交，改为构造新映射。product-first在实际API治理检查后因猜测不存在getProject而build失败47.37秒，按当前getProjectOverview契约纠正。product-verified进入完整App后隐式select标签包含别名选项，定位失败93.62秒；明确aria-label修复。新增测试的Node文件URL/类型环境及把原草稿误声明正式版本也造成collection/build失败，改用项目已有JSON fixture导入与TestCaseVersionCreateRequest，先类型检查再真实场景。所有原始失败独立留存。

未知响应与已确认拒绝必须区分；真实并发409样本派生的界面检查最初3通过1失败，确认拒绝误锁编辑器。修复后前端13通过；按原CasePlanDialog/TestCaseContentDialog的sessionStorage方式仅保留请求身份，不保留业务输入。刷新只查结果，恢复编辑缺正文时不自动/重构重发。

product-delivery1通过70.42秒；最后投影审查发现别名只有name/seq，原现场验证1失败0.23秒。正式草稿增加完整target/page、场景说明和别名使用说明，最终product-complete1通过62.00秒，0跳过。真实production API/built App、实际新Chromium/CDP与隔离PG/MySQL/Redis/Memgraph两场景六不可变版本；POST已提交后主动丢响应，刷新GET回读同资产、总数保持2；原事件/固化指标不变、父版本竞争201/409、跨项目404、归档项目写409/读200，原草稿固定且可读。

最终九模块72通过2门控跳过7.49秒（其中新增来源/完整性/引用/投影检查）；前端13通过2.32秒，实际build含类型检查通过，最终编译退出0。故意API重启窗口Vite会话轮询有ECONNRESET/ECONNREFUSED，不伪称网络全程正常；页面脚本错误0。

交付审计发现原PG payload时间带Z而API时间不带时区：RecordingStore._row_to_event采用索引TIMESTAMPTZ列，并由storage_utils.ensure_utc_datetime按既有契约转成naive UTC，不是原件被场景整理改写。增加整理前PG整行快照，最终product-integrity独立重跑1通过72.94秒，0跳过；8条原始事件所有列和完整payload在整理及重启后逐行相等。审计另核对payload、索引列、API三种时间表示的同一UTC时刻；不将格式归一假冒字节相同，不改既有时间契约。原complete及全部失败工件仍保留。

没有真实模型、用例启用/执行、录制前场景生成、跨录制拼接、selector修复、全量清洗与遗漏、默认Agent查询场景、业务失败定位报告、场景归档版本专项/生产历史或容量验收。配置仍MySQL，两个新业务表PG；无需新依赖、工具、执行模式或额外模型平台。三份方案及历史证据保护由本批audit核对。
