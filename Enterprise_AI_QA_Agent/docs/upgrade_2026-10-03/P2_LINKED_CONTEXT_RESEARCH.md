# 规划关联来源：固定导航投影与真实 Step 复验

基线 `a6922e56`。本批处理已复现的上下文职责混合；完整默认 AI 执行目标仍未完成。

## 代码依据与采用做法

- `planning_workflow.py` 原来把 `checkpoint.linked_sources.model_dump()` 同时用于恢复记录和模型输入。`DocumentContentBasis` 含完整版本 snapshot、asset、上传检查、对象地址和解析快照；已读原文还在 evidence 中。两者各有职责，不能删除恢复依据，也无需重复作为推理正文。
- `case_planning_task.py` 已将模型参数、Prompt、工具与协议冻结在 PostgreSQL seed，并通过 wrap serializer 保持旧记录原样。新导航采用相同方式：新增可空 `linked_source_context_protocol`，旧 seed 缺该字段时仍发原完整结构，旧 Prompt 及检查点不重写。
- `planning_sources` / 关联读取已按固定项目、版本、原件、解析快照读取 evidence；新投影只取该已保存 basis 的身份。没有重新读当前文档，正文不截断、不改写。
- `planning_service.py` 的异步提交和同步 create 同时采用新协议：已有 MySQL 策略启用关联预读且使用阶段 Prompt 时，新任务冻结 `case-planning-linked-navigation-v1`，Prompt 版本追加 `+linked-navigation-v1`。没有新增可编辑配置开关、MySQL列或前端选择；协议是任务的固定行为身份，配置仍沿已有 MySQL 策略。

投影保存 source 原引用、case/evidence 关联、进度、读取状态和错误。文档 basis 保留项目、role、文档/版本/asset 身份与哈希、解析快照身份与哈希、预期内容哈希、警告。原 source.metadata 不删，场景 basis 原样保留。因此这不是所有元数据的压缩，也不保证任意解析来源体积均相同。完整 basis 仍保存在 PG 检查点及 round 记录，完整用例/原文/工具契约仍送入模型。

两阶段 Prompt 明确导航与正文的关系、`has_more` 和 authored/current role 的区别；错误/未读取不能当已读。`case_planning_linked_context_projected` 日志只记录协议、项目/计划、数量与前后字符数，不输出内容。

## 实际验证

新增合同取真实旧失败 PG 快照及真实场景快照验证：旧 seed/checkpoint 精确序列化、旧来源原结构、文档身份与错误保留、原证据不修改、场景不变、未知协议拒绝。最初 `before` 为缺实现的 ImportError，只是合同缺口证据；职责混合复现依据是前批真实 HTTP 请求，不将该 collection 错误称业务故障。

- 首次合同：36通过、21门控跳过，4.70秒。
- 开启实际 MySQL/PG/RustFS 的关联读取、引用、场景、规划任务回归：67通过、3格式门控跳过，57.30秒。实际包含分页、重复关联、双用例、项目隔离、错版本/asset/hash，以及写入已提交但回执丢失后的恢复；终态重投不新增模型调用。
- 补强新投影和 PG 原完整 basis 的断言，再开启实际格式解析：24通过、0跳过，23.14秒。新建 Markdown、DOCX、文本 PDF 原件，实际解析任务进程、V2上传/重解析后仍按当时快照读取。该批含上述14关联变体；不能把多批重叠项相加当不同功能数量。
- 三个门控跳过已由后一批补验，不掩盖第一批记录。受控模型只验证请求、状态和恢复，不能证明默认 AI 业务推理质量。

最终变更文件 `compileall -q` 实际exit0；受影响契约全局引用与两种提交调用方已核对，`git diff --check` exit0。高置信度敏感值扫描102份本批文件无命中，先前60份原件和三个受保护工作区文件SHA保持。没有前端消费内部seed的新字段，未改前端、未运行前端构建；本批没有数据库schema迁移。

## 默认 Step 真实结果

新建隔离项目及正式八步 JSON 用例，通过生产 API/规划 CLI，使用真实默认 Step 以及前批显式隔离 JSON/low profile，原180秒和8192 Token上限不变。未更改全局 MySQL 模型配置。新 seed/两轮实际 HTTP 请求均携带冻结的新导航协议。

`p2-linked-context-default` **1失败，197.07秒**。两次物理 HTTP200，未发现自动重复发送：

- logic 60.620秒、stop、正文1820字符。
- effects 109.902秒、length、completion_tokens=8192、正文0；仅存推理字段计数25886字符，未存私有内容。
- 保存 failed/incomplete_response，Run 未创建，业务 HTTP0，未执行副作用或伪造通过。

真实关联导航从前批4724字符变为982；完整 evidence 体积仍8292字符。effects 总输入16853字符、Prompt7785字符，但逻辑结果并非前批相同，不能直接将总输入差异当受控因果实验。投影优化已验证有效，**不能称截断根因已修复**。

## 后续需要设计，不能直接加循环

同类截断已在三个新任务出现，停止继续微调相同请求参数或扩大预算。`planning_stages.py` 的 effects 同时绑定产生/清理关系、证据身份、输出路径/选择器、文档预期和数据传递，`planning_workflow.py` 还附完整工具契约与短引用目录。下一步先复核各字段哪些必须由 AI 决策、哪些已有确定性绑定与事务校验；设计可冻结、可恢复的声明任务拆分，保持完整业务原文、固定逻辑计划、原总轮次/期限及终态恢复语义。尚未实施新的声明拆分，不能报告完成。

本批旧任务兼容证明限精确 seed/checkpoint/Prompt 与上下文序列化；没有重新执行历史失败默认模型任务。多候选项目选取、默认 Agent 执行与正式回执、其他模式、完整 App、容量与其他供应商质量仍未通过，完整 P0—P5继续 active。
