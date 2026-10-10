# 项目知识查询遗漏录制节点：调研、复现与产品验收

本轮基线 `adf51172`。按用户决定继续模型之外的模块；不调用待实名认证的默认 Step。以下是现有录制知识查询的根因修复，不代表完整 P0—P5 或人工 DeepSeek 业务验收完成。

## 依据来源 → 采用做法

| 当前代码和参考依据 | 本轮结论与做法 |
|---|---|
| `Agent_Server/src/application/exploration/recording_graph_store.py` 的 finalize、录制子图读取；`src/schemas/recording.py` 的事件和脱敏序列化 | PG 录制/事件是业务依据；现有 Memgraph 保存 Recording、Action、Page、Element 与 HAS_STEP/ON_PAGE/TARGETS。复用既有投影，不新增另一套场景库，不改历史事件 |
| `src/application/knowledge/knowledge_graph_service.py` 的身份解析、项目摘要和 get_graph | 原查询仅读 Page/Element/Entity，却读取全部项目关系；录制和动作端点缺失。原正式项目还强制要求旧 graph_scope_key，与录制正式 project_id 写入不一致 |
| `src/api/routes/knowledge.py`、`src/schemas/knowledge.py` 与 `agent_web/src/views/KnowledgeView.vue`、`src/types.ts` | 沿现有 API、详情和筛选扩展两类节点及计数；正式项目没有旧映射时仅按 project_id 读取。已有明确旧映射的兼容查询保留；不使用默认 scope 或项目名替代正式身份 |
| `docs/reference_ai_test_agent_2026-10-02/REPORT.md` 5.3（119—127 行）、实施建议（171 行） | 场景完成度、步骤/别名维护、业务断言及失败定位是独立产品能力。代码中内部 alias 为元素 ID 清洗重映射，不等于用户可编辑业务别名；内部回放器不能直接加 HTTP 入口绕开 Agent/权限/租约 |
| `src/application/projects/project_overview_service.py` 与知识 get_generation_context | 项目概览仍以旧 scope 为条件；生成上下文仍为有界三类节点摘要。本轮不把知识页面修复冒充概览/全部生成链路修复；完整录制查询已有专属事件/完整性出口 |

本轮依据均命中现有代码与本地参考报告，没有引入依赖、配置、数据库表、执行入口或新模式。配置仍 MySQL，业务录制/事件仍 PG，Memgraph 仍是现有可重建投影。

## 实际失败样本

新增 `test_recording_knowledge_product.py`，在真实独立生产 main、隔离 MySQL/PG/Redis 和现有 Memgraph 中创建两个正式项目。新 Chromium 通过生产 CDP 录制 API 在本轮 HTML 上真实填入长 Unicode 字符串并连续点击两次；页面计数确为 2。不是导入旧 JSON、模拟知识 API 或仅测试语法。

- `before`：1 失败，17.28 秒。8 条事件已经落 PG，但未绑定旧 scope 的正式项目知识请求返回 409。
- `before2`：2 失败，28.25 秒。无旧 scope 项目返回明确的 graph_scope_key 错误；有登记 scope 项目能读取知识，但缺少 1 个 Recording 和 8 个 Action，关系端点悬空。原 HTTP、PG、录制子图与差异清单均保留。

根因是两个查询契约错位：正式身份被旧映射阻断；项目知识的节点集合与关系集合不一致。无需重写录制、创建新图谱或增加兜底默认项目。

## 实施与兼容

知识身份解析允许正式项目无旧 scope；该分支仅精确 project_id 查询。项目列表/摘要和全图增加 Recording、Action，返回项目名称、recording_count、action_count；空项目仍不进入知识项目下拉。已有旧映射、旧三类节点、历史接口字段继续兼容。

录制节点使用既有 name，动作使用 action_type 摘要和完整事件元数据。Action 读取复用 RecorderEvent 和原脱敏序列化；历史密码输入只提供长度，不原地改写历史图载荷。损坏事件返回不可用元数据并按节点身份记录 warning，不打印载荷。实际图读取有结构化项目/节点/关系计数日志。

前后端 Schema/DTO、知识筛选、项目标签/计数、三语言文案同步。计数名称是“动作与观察”：Action 还包括 scroll/page_scan/page_response 等技术观察，不把 8 条事件宣称为 8 个业务操作。没有增加步骤编辑、删除事件、自动启用正式用例或直接回放功能。

## 实际验证结果

| 批次 | 结果与范围 |
|---|---|
| verified | 2 通过，86.42 秒：新实际录制、项目知识完整构建 App、主进程重启和项目隔离 |
| final | 2 通过，81.30 秒：新增动作详情的完整长值和 HAS_STEP 所属录制检查、正式项目画布标签；旧证据不覆盖 |
| restored（最终） | **2 通过，85.99 秒，0 跳过**：同时有/无旧 scope，录制与动作筛选、详情、项目摘要、完整 App 重载、API 重启、PG 原事件逐行不变及清理核验 |
| regression | 9 模块 **55 通过、2 门控跳过，4.16 秒**。含本轮 5 个从真实录制派生的身份、完整事件、历史密码与损坏载荷回归；两项既有 live draft 开关未启用，不能算通过 |
| consumers | 冒烟项目隔离、生成管线、冒烟执行边界 **9 通过，7.28 秒**；是既有合同回归，不是外部模型验收 |
| frontend | 既有 KnowledgeView 项目切换 **4 通过，1 文件，2.29 秒**；新增筛选/详情由实际构建 App 验证 |
| build / compile | 最终两项目各实际运行 npm build，包含 vue-tsc --noEmit；均退出 0。最终 compileall 退出 0，仅附加检查 |

最终两项目各保存 8 条事件，图为 1 Page、2 Element、1 Recording、8 Action、24 条关系，遗漏录制节点和悬空边均为 0。另一空项目请求 404；重启后知识 JSON 相同，页面重载后可再次看到录制；PG 原事件行在读取和重启前后完全一致。页面 JS 错误与非预期 API 错误均 0。

主动停 API 期间原会话轮询产生 500，按明确中断窗口单列保留；不宣称网络从未失败。恢复后的页面再次从真实 API 读取。测试仅清理本轮新项目 ID 的图节点，两批均从 12/0 降到 0/0，并保留清理计数；不重置共享 Memgraph。隔离存储和独立进程由既有生产验收夹具按所有权回收，stdout/stderr、PG、Docker、网络、截图和 HTML 原件保存。

## 分类与下一步

- **已有实现且本轮已验**：真实 CDP 录制入 PG、现有图投影、正式项目知识查询/筛选/详情、重启读取、项目隔离。密码及损坏事件由真实样本派生的合同测试验证，未伪称真实历史密码数据迁移。
- **已有实现，仍待完整验收**：默认 AI 使用完整知识并完成业务断言；人工 DeepSeek 录制；复杂 DOM/网络/跨文档/Shadow DOM 关联与清洗遗漏；正式生产历史投影核对。
- **仍需补实现或另行查证**：业务场景管理、步骤整理、业务元素别名、失败定位和回放报告；无旧 scope 项目的概览仍可能显示零；项目列表现有前 200 项及逐项目查询未做容量验收；通用生成摘要仍未包含完整录制事件。上述问题不能因本轮页面能读知识而勾选完成。

默认 Agent 的项目选取/排序/必要准备/执行/正式回执继续待账号处理；API 续期、性能容量/AI 流式、安全其他扫描器故障、需求业务质量和生产历史迁移继续未完成。

## 项目概览与生成上下文的后续修正

继续沿现有代码查证后发现，`ProjectOverviewService` 原先只有 `graph_scope_key` 时才调用知识服务；所以本轮知识页已能读取的正式项目，在项目概览中仍会显示零。`ProjectTestCaseContextProvider` 也只在旧 scope 存在时注入知识上下文，导致录制知识不能进入后续生成上下文。

修正保持同一身份规则：知识服务已绑定项目服务时传正式 `project_id`，没有绑定项目服务时才使用旧 scope；没有任何身份时明确记录不可用。概览新增录制和动作计数，生成上下文的有界节点集合加入 Recording/Action，但只传节点身份、标签和动作类型，不把事件原始值或凭据塞进生成上下文。原三类节点和旧项目兼容。

合同回归 `p5-recording-formal-context-contract3` 为 **19 passed in 3.83s**，覆盖旧 scope 只读、正式无 scope 概览、正式项目生成上下文、知识投影和事件脱敏。真实录制产品最终批次 `p5-recording-overview-live3` **2 passed in 102.74s**，将项目概览 API 和完整 App 的“图谱节点”计数加入验收；live2 中因测试把实际 12 写成 11 的失败原件保留。这仍不等于默认模型已实际生成或执行用例。

随后补实测真实知识服务的有界生成上下文，并补核心路径日志及旧前端类型兼容。交付批次 `p5-recording-overview-final` **2 passed in 94.80s，0 skipped**：两个新真实录制项目均读到 1 Recording/8 Action 上下文节点，完整 Unicode 输入原值未进入摘要。完整事件仍由既有专属录制查询提供，这个有界摘要仅供发现来源，不能代替完整执行依据；按种类限额及总额采样仍可能遗漏其他节点，未声称全量上下文。

7 个受影响模块最终 `p5-recording-overview-regression-final` **27 passed in 4.11s**；空图不能制造知识来源引用，警告可见。前端项目切换 4 通过，实际 build/类型检查及最终 compileall 退出 0。原19/26项回归及两个合同夹具错误批次保留；夹具里旧scope模拟对象与正式ID模拟对象分别验证，未放宽生产身份边界。默认AI仍未调用，场景/步骤/业务别名编辑本轮只调研，尚未实现。

## 可复现命令

在 Agent_Server 目录设置 `RUN_LIVE_RECORDING_KNOWLEDGE_PRODUCT=1` 和一个尚不存在的 `RECORDING_KNOWLEDGE_PRODUCT_EVIDENCE=../docs/upgrade_2026-10-03/<新批次目录>`，运行：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_recording_knowledge_product.py -q --tb=short --junitxml=../docs/upgrade_2026-10-03/<新批次>.xml
```

既有 production API、CDP、数据库和构建 App 必须实际启动；未设置开关会跳过，不能计作真实通过。最终本轮前缀 `p5-recording-knowledge-restored`；旧批次不重跑覆盖。核对脚本 `p5-recording-knowledge-audit.py` 只核验保存的原件、测试结果及历史哈希，不替代功能测试。
