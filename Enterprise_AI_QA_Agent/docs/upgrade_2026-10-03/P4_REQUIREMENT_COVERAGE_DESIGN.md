# 固定需求版本的用例覆盖确认：实施前研究

研究基线c2db4a4，重新读取用户原目标、RequirementService/Store/Schema/Matrix、版本评审、增量生成元数据/固定收据与本地版本化评测/治理课程。完整P0—P5不缩减。

## 依据 → 做法

- 现有需求版本case_links只允许原影响seed.cases；后生成的更新/新建/重试草稿不在范围中。保留这个历史契约，不修改需求正文/hash，不让生成元数据自动变成覆盖。新增独立不可变覆盖确认记录，绑定明确requirement/version/hash和验证点，保存完整选定关联集合；移除关联通过新记录，不删旧记录。
- Case版本内容不变，评审状态在case record里。覆盖确认不自动启用用例，也不把设计关联当作执行通过。允许当前项目明确固定版本，保存确认时的真实评审/启用状态，历史缺少状态标为unavailable，不伪造approved或新增“必须启用才准关联”的门禁。产品验收会先按现有流程评审用例，再确认覆盖。
- 关联可以显式引用生成task；此时必须用真实PG完成收据校验case/version/hash、原group/scope/module/response和版本生成元数据，不仅相信元数据。普通手工/导入用例仍可明确关联；不按模式过滤，不限制只能覆盖原生成需求，其他项目一律拒绝。
- 复用现有项目事务锁和请求回查。新PG覆盖表同事务写入，request_id绑定文档/需求版本/完整关联和预期上次覆盖ID；新请求检查active项目和上次记录，未知回执先原请求回查，重放不读取当前配置/模型/对象/用例状态。没有新配置，因此没有新增MySQL键；业务记录全部PG。
- 矩阵默认读该固定需求版本的最新覆盖确认，提供明确coverage_id及initial选择，展示当前与原版本关联、旧需求版本关联和确认记录。只追加记录，历史明确选择不变；旧请求无覆盖记录时保留原设计矩阵。API/Schema/前端DTO/客户端及正式消费者同步。

## 验证范围

实际MD/DOCX/PDF、解析/比较/影响及固定生成，更新/新建/重试完整JSON经过现有版本评审后明确映射验证点；真实公共API/PG完成关联，原需求版本及生成范围保持，未映射点保持未覆盖，执行not_assessed/部署unknown。覆盖记录修订、撤销/initial/指定历史、后续需求变化待复核、原请求/提交回执丢失、并发、分页、越项目、移除需求、重复验证点、生成收据/元数据替换及case版本篡改均实际核对。

正式生产API/独立Worker与正式App分阶段验收；后端/API通过不能冒充覆盖确认页面完成。完整生成消息/原件/对象、SQL记录/hash与每个结果的原始证据保留，失败原文件不重写。

## 当前实现与契约

POST `requirements/{requirement_id}/versions/{version_id}/coverage-reviews`提交完整关联集合、note、request_id与expected_coverage_id；每条关联明确case_version_id、content_hash、point_keys和可选generation_task_id。GET同路径分页历史，`requirement-coverage-reviews/by-request`按原编号恢复。matrix的coverage_id默认选择本需求版本最新记录，initial选择需求版本最初关联，UUID选择明确历史确认。需求版本及原影响评审不新增字段、不重算历史hash。

新PG表为文档前缀加`_requirement_coverage`，复用需求服务初始化、项目事务锁和已有错误返回。按预期覆盖ID追加修订，空集合表示撤销关联。case内容哈希算法从原_build_version抽出共享函数，保留旧canonical shape；从创建及读取双方验证完整JSON/来源/说明/生成字段，SQL身份及哈希列也需相同。生成Object/Receipt的原Schema移入独立文件并从原位置重导出，避免requirements与generation scope循环导入，不改变存量收据和组hash。

生成来源核对原completed task、seed/group/retry、result_saved收据、完整case/version/content_hash及共同scope/module/response元数据；重试沿用原生产seed校验。原生成provenance同时完整保留，其中request_sha256、原验证点及来源需求描述生成依据，不宣称仅凭收据重新读取了RustFS原请求或证明每个点已经覆盖。是否覆盖本次点仍是明确确认决定。无task的人工/导入关联仍校验正式项目及完整版本，不能伪造生成来源。

每个关联冻结真实version_reviews与active_version_id，缺失历史评审状态用unavailable，不回填approved、不新增必须启用的拦截。读取和请求重放不再访问当前用例/对象/模型/配置。旧需求的关联依据以所选版本创建时间或明确覆盖确认时间截断，之后对旧版本的确认不回写已选择历史矩阵；后续需求使用此前有效关联提示needs_review，不移植成新需求覆盖。

前端DTO/客户端与旧矩阵消费者兼容；正式覆盖确认编辑、原请求恢复和历史选择页面尚待接入。后端与生产进程结果详见完整方案6.24和IMPLEMENTATION.md；配置继续MySQL，覆盖元数据仅PG，原件/范围/响应沿用RustFS，没有新配置键或依赖。
