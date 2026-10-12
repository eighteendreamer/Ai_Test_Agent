# API 后台 Worker 的刷新凭据传播研究

基线15d8687b，完整P0—P5保持active。先重读目标附件、已有P2_API_AUTH_RESEARCH.md和接口测试课程6.2；该课程要求登录凭据实际调用后续受保护接口，不能只看登录200。

## 代码依据与待复现事实

- `runtime.py` 在有正式会话/协调服务时走ApiSubagentCoordinator，其他路径走ApiTestCoordinator；两个入口都保留现有Agent/工具。
- 直接协调器 `_propagate_auth_ref(auth_ref, source_task_id)` 已按来源更新认证任务及明确依赖任务；现有真实登录→刷新→受保护HTTP专项证明该算法可用。
- 后台协调器另有旧实现，仅更新没有auth_ref的任务。登录传播旧引用后，刷新生成新session不会替换已赋值的下游；还会给无依赖的未执行任务广播凭据。
- Worker输出带有正式task_result及credential_session，父协调器已有恢复/解析能力；不需要增加模型适配器、刷新URL猜测或凭据配置字段。
- 原DependencyPlanner/CapabilityMapper已识别显式刷新任务。自动过期/401重试、Cookie续期、并发刷新去重是独立未完成范围，不把本次传播修复当作全部实现。

## 验证及采用条件

先让同一组真实本地HTTP登录、显式刷新、受保护及无认证接口分别走直接执行和后台协调/生产api-test-runner。控制派发器只替代后台模型/进程启动，生产协调、Worker任务序列化、工具消费、实际HTTP和会话结果读取均执行；明确不冒充默认模型或独立Worker验收。导出只保存状态、路径、凭据是否符合及引用身份，不保存生成的访问凭据。

若确认差异，抽出直接路径已验证的传播函数供两协调器复用，并从实际完成Worker任务传入来源task_id，避免两份策略继续漂移。共享函数沿原日志设施，只记录来源/目标任务及依赖匹配，不记录凭据。无新表、依赖或配置，MySQL/PG/RustFS边界保持。

## 实际结果与研究修正〔2026-10-12〕

上面的无新表判断仅适用于传播函数修复，不能用于完整Worker凭据传输。最初受控派发器直接调用生产runner，绕过了ToolRuntime的脱敏持久化边界；进一步验证推翻了“恢复serialized credential_session即可完成真实链路”的判断。

- `p2-api-worker-auth-before`：2失败、2通过3.52秒。直接协调器正确；Worker刷新后profile仍使用旧引用，无依赖public还收到旧凭据。
- 复用直接路径算法后，受控Worker和直接执行四组真实HTTP场景均通过；已完成任务重投没有新增HTTP。这不是独立Worker或默认AI验收。
- `p2-api-worker-auth-safety-final`：九模块175通过、1门控跳过28.27秒，含真实文档依赖HTTP/PG/RustFS。此前50通过1跳过及48通过2跳过与本批重叠，不相加。
- 正式`ToolRuntimeService.execute`、PG ToolJob和PG子会话专项，`p2-api-worker-auth-storage-captured`：登录200、刷新401、profile跳过。Authorization已脱敏，但父协调器恢复脱敏后的对象，不能实际鉴权；`credential_session.token`仍有值，证明具体凭据容器存在脱敏遗漏。原件只保存布尔事实、状态和随机身份，没有输出凭据值。
- 将具体`credential_session`容器加入现有脱敏名单后，`p2-api-worker-auth-storage-redacted`仍1失败6.97秒：[200,401,null]不等于[200,200,200]。容器已脱敏，token不再出现在该容器的公开投影；后续无可用凭据。不能据此声称真实链路通过，也不扩大为“所有token字段都已安全”。普通`usage.token`保留。

真实存储夹具的基础设施失败、ProjectStore外键初始化遗漏及观察字段错误分别保留日志；这些不是生产鉴权通过或生产缺陷的证明。容器重启后恢复原PG数据目录`/var/lib/postgresql/data`和既有RustFS启动脚本，已实际连接QA_Agent并运行存储测试；没有更改数据库配置或重置历史库。

## 内部凭据引用的后续实施依据与边界

这是尚需补实现的内部传输缺口，不能靠关闭脱敏、从泄漏token还原Authorization或让模型再次登录修复。

| 已核对的源码事实 | 下一步采用方向与待验收条件 |
|---|---|
| `CoordinatorRuntimeService.dispatch`为子会话设置父session/turn、dispatch身份，并继承正式`project_id`；工具参数中的项目/来源不具备同等可信度 | 从实际SessionStore关系确定授权范围；不能让模型提交任意父身份或凭据引用即可解密。需验证跨项目、跨会话、无有效派发记录拒绝 |
| `_build_worker_spec`把整个credential_session写进Worker prompt；父协调器从公开tool message恢复它 | 新派发只携带不透明引用；可信executor在业务HTTP前解析引用。原会话/Job公开记录保持脱敏，父协调器不能把公开投影当作私密传输 |
| `ChannelCredentialCodec`已经使用cryptography/Fernet，加密内容为字符串字典，当前环境`available=True`；未打印密钥 | 可复用既有加密机制存PG业务记录；可编辑密钥配置延续现有方式。必须验证缺密钥/错误密钥、密文落库、旧版本读取与不覆盖历史，不新增模型专用能力 |
| PG业务存储已有派生表名、标识符校验、参数化SQL、asyncio.to_thread惯用法；没有现成API凭据vault | 需要有范围约束的内部业务存储及生命周期，不能声称已实现；实施前明确初始化/迁移/销毁和历史兼容，禁止把凭据写进MySQL业务表、Prompt或RustFS公开报告 |
| ToolJob终态复用已有原子领取与持久输出机制；`ApiTaskExecutor`已有unknown语义 | 新服务重建后能解析原引用；终态Job重投不发送HTTP；密钥/引用不可用明确失败。不能因此增加盲目401写重试或重置已完成任务 |

上述是已调查事实支持的实施方向，还不是已通过的最终存储设计。真实独立Worker、默认AI、更复杂Cookie/并发刷新、自动过期和401续期仍未完成。共享传播算法仍保留直接路径“未来auth任务可更新”的语义，本批没有验证任意多认证流隔离，不宣称已解决该范围。
