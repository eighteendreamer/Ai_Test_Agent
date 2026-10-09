# 2026-10-09 模板与导入完整产品验收

基线1904fe9f，保留当前回执修改。本步不调用真实模型，遵循用户“先继续其他模块，账号处理后再验收”。

依据 → 采用做法：

- ProjectsView.vue 的项目资源入口、TestCaseTemplateEditor.vue → 在构建后的实际App创建项目，加载默认模板、保存项目指导/示例、载入默认内容并核对MySQL修订。模板只生成用例，不进入执行流程。
- CaseImportDialog.vue、case_imports.py、import_parser.py/import_store.py → 真实xlsx行布局、分表及JSON原件上传，核对RustFS原件、预览排序、错误行与编号冲突、冻结批次与重复提交不重复创建。
- TestCaseContentDialog.vue、case_management.py、case_store.py → 实际页面查看完整JSON，评审/启用后编辑新草稿，旧启用版本保持；false/0/null及自定义结构不丢失。
- test_document_full_product.product_browser 与 test_execution_worker_cli.isolated_runtime → 复用实际构建App、独立API及隔离MySQL/PG/RustFS/Redis进程；保存请求状态、浏览器错误、截图、原件、数据库与进程输出，并在失败时保存诊断。
- test_case_import_service._xlsx → 沿已有真实文件样本，分表步骤按2、1录入，验证排序结果1、2。已有test_case_import_browser为临时组件宿主，本步结果不能复用它充当完整App验收。

本步覆盖手工模板/导入与版本治理。文档AI生成、真实模型执行、生产历史迁移和全部P0—P5仍需分别验收。

首轮真实App已经上传/下载原件、保存模板；精确label定位select失败，ARIA原件显示控件正常。改用实际combobox后，行/分表/JSON及错误预览均通过，测试误读不存在的total失败；核对TestCasePage只有items/limit/offset/has_more后修正。第三轮激活断言在请求响应之前读取，最终PG及HTTP200证明启用完成，改为等待原activate响应及界面确认状态。第四轮已走到跨项目但测试仍在详情页寻找目录新建按钮；沿实际侧栏路由回目录。第五轮重启后精确返回按钮名称漏图标字符，保存ARIA显示按钮含图标，统一使用已有侧栏导航。以上均为新测试错误，未修改生产代码或历史失败。错误Excel增加同文件一条有效候选，验证逐行错误确实阻止整批提交，而非仅因为零候选。

第六轮业务/跨项目/重启全部断言已通过，最后“所有API响应无错误”误包含主动停API期间的健康/会话/资源轮询500。实际Vite stderr为ECONNREFUSED/ECONNRESET；沿test_document_full_product已有重启窗口划分保存原失败和计划停机响应，恢复后错误仍必须为0。不把这些500当作导入失败，也不抹除网络原件。

最终delivery：1通过/0跳过，54.82秒。实际构建App、Chromium、独立API及其重启，隔离MySQL/PG/RustFS和Redis容器；模板/原件/预览/提交/重放/错误行/冲突/评审/启用/新版本/跨项目及重启读回均通过。PG最终3条用例4份不可变版本；原JSON未知字段及false/0/null保持。未执行AI生成或真实模型，不改生产代码，不代表P1文档生成或全部升级完成。
