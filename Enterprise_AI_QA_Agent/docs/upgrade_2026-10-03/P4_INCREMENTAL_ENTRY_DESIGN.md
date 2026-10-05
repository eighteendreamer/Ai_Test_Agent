# 增量生成正式入口与独立 Worker 研究

研究基线80dc609b。用户目标原附件、当前完整方案及工作区已重新读取。本阶段接通已有持久模块服务的公开API、启动初始化、独立CLI及前端DTO/客户端；随后继续页面和失败显式新任务重试，完整P0—P5不缩减。

## 现有实现 → 接入方式

- document_impacts路由用UUID、项目级设置、文档级任务/按原请求查询和call_document_operation统一404/409/503。生成沿用同一边界：项目级MySQL策略，文档级提交、分页组目录、固定组/模块状态、原范围与原响应只读。提交返回202表示已持久接受，completed只表示草稿或停用候选已保存，不表示评审/测试通过。新路由不能接受客户端传入seed、租约或草稿提交条目。
- main在项目/文档/用例/解析/比较/影响/正式需求初始化后挂app.state服务。增量依赖这些已存在服务；API与独立Worker共用一个build_generation_runtime组装函数，避免两侧捕获不同来源或配置。新增MySQL键和两张PG表由正式启动初始化；关闭派发仍初始化只读服务，原提交先查再返回，新提交已有stream=None拒绝行为。
- document_operation_worker_runtime已检查登记stream，管理SQLAlchemy/对象/Redis资源及Outbox依赖；DocumentOperationTaskHandler已管理领取/心跳/取消/终态重投。生成复用生命周期，新增qa:tasks:case_generation登记流及CLI；只读初始化解析/比较/影响/需求来源，不能在恢复时重新捕获当前资料。CLI复用现有--once/--worker-id/--stream惯例，不自动修改全局调度设置。
- test_case_service.initialize只初始化case store，模板要单独初始化；container已有test_case_store、test_case_generator、template及model/Skill服务。新组装使用这些正式组件，不引用测试fixtures，也不复制模型适配器。
- requirements.types/api展示固定需求版本、显式关联和编码的原请求。生成DTO同步原模块身份/策略/组/检查点/收据，客户端传固定requirement/version与明确case version、新建范围；读取组报告保持全部模块状态。大范围/原响应保留完整JSON，不以预览或摘要替代。新公开Schema使用typed receipt表达版本引用和停用目标，不改旧冻结组/范围hash。
- 本地Agent轨迹/协议和评测工程化课程强调可追溯版本、阶段身份、异常/恢复；采用这些原则，内部或受控HTTP通过不代替真实默认模型业务判断。

## 实测要求

先用真实MD/DOCX/PDF和既有需求接受fixture测试新路由的设置MySQL落库、原请求、项目/文档隔离、分页、范围/响应、完整草稿及旧启用指针。再用生产FastAPI进程、独立解析/比较/影响/生成CLI、实际MySQL/PG/Redis/RustFS及HTTP验证正式提交→Outbox→领取→完整响应→草稿/收据，终态重投、API重启/关闭派发后的固定历史读取。权限沿既有Bearer中间件，不增加替代授权体系。

前端DTO/客户端同步契约并测试路径编码、冻结请求和原请求恢复；运行既有类型检查/构建。页面尚未接入时明确说明，不能把客户端测试当完整App验收。生产fixture新增表/对象必须纳入独占范围的对账与清理；保留旧证据和初次失败，不因新接口改变旧call/组/hash。

失败模块的新任务重试另须研究原固定范围/父失败身份、未知发送与新授权、并发父版本冲突及原总期限的语义，不重置终态，不把新请求捕获当前依据冒充旧任务恢复。正式运行/环境、人工录制、Docker和其余P0—P5仍按完整方案继续。

## 实际验证结果

公开API/内部任务19通过178.72秒；公共停用候选另1通过13.75秒。三格式独立正式流程3通过253.67秒、每场景影响2+生成2物理POST；既有生产兼容1通过77.80秒。前端契约8通过，类型检查/构建10.58秒通过。详细命令、启动别名错误、原固定范围/草稿/终态及关闭派发恢复见PLAN6.18和IMPLEMENTATION。生成页面、失败显式新任务、外部默认模型业务判断及完整P0—P5未完成。
