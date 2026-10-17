# API 凭据有效期与负向认证测试研究

基线 `4fd85832`。本批处理显式有效期的保存、前置可用性与恢复；不将 401 一律解释为应该刷新，不自动重发写操作。

## 依据与方案纠正

- `CredentialSession.expires_at` 已存在；`CredentialManager.create_from_login_response` 和用户输入入口却始终写空值，`has_valid_session` 只看凭据内容是否存在。`runtime._check_preconditions` 已消费这一查询，修复应落在凭据管理层。
- 原方案明确保留“刻意未登录或使用无效数据”的负向条件。本批最初未验证的初稿在 `build_request_headers/build_request_cookies` 中删去过期凭据，会把“过期 Token”偷换为“没有 Token”；另有时间单位/时区猜测及继承有效期错误。因此已撤回初稿，差异保留在 `p3-api-token-expiry-rejected-draft.diff`，不能当作最终实现或已通过功能。
- 本地课程 `项目借鉴/llm-testing-course/接口测试-实战篇.html` 1.4—1.5 要求以明确响应字段传递认证状态。现有两个协调器、Worker runner 和加密凭据引用沿同一 CredentialManager，复用已有链路，不新增模型/执行框架。
- [RFC 6749 §5.1](https://www.rfc-editor.org/rfc/rfc6749.html#section-5.1) 定义 `expires_in` 为访问令牌的秒数寿命，[附录 A.14](https://www.rfc-editor.org/rfc/rfc6749.html#appendix-A.14) 为数字语法。只读取顶层显式秒数；不解码 JWT 猜有效期，不把 refresh_token 的期限当作 access token 的期限，不添加推测的 expiresAt 别名或秒/毫秒猜测。

## 确认的缺口及最终做法

修复前 `p3-api-token-expiry-before.xml` **11失败，2.77秒**：两项实际 HTTP 测试的有效期/用户输入断言失败，一项继承有效期失败，另八项无效 TTL 的可见性断言失败。不能将这些统称 11 个独立业务故障，原输出保留。

新登录/刷新确实返回 Token 时，将非负整数 `expires_in` 转成一次性的 UTC ISO 绝对时间，接受十进制整数字符串；布尔、小数、非数字、无穷和溢出保留“未知”并记录安全警告。锚点为本地处理响应时间，不是服务端签名发行时间或跨机器时钟校准结果。未返回 Token 的 Cookie 旋转保留原 Token 的旧期限，不能续命；返回新 Token 但缺 TTL 时新期限保持未知，不能继承旧 Token 的期限。

明确用户输入的 `expires_at` 保留原文，前置判断只解释带时区 ISO。旧快照的空值保持旧行为；不明确格式不猜测、不原地改写，保留可检索警告。恢复只读取固定绝对时间，不再次计算 TTL。已有加密 PG JSON、身份和哈希机制不变，无新字段/表/配置/依赖。

`has_valid_session` 不将已知过期会话当作普通前置可用；实际 Runtime 会进入原认证输入状态。本批不在低层删请求头、改变用例断言或自动恢复认证；正式任务明确要求 401 时仍发送原过期 Token。日志仅有凭据引用、字段/类型和布尔事实，不记录 Token 或原始无效值。

## 实际验证

- 原专项修复后 `p3-api-token-expiry-after.xml` **11通过，2.45秒**。
- 扩展功能 `p3-api-token-expiry-functional.xml` **17通过，6.91秒**：实际本地 HTTP 的有效/失效 Token；两协调器通过原 Worker runner 的受控启动完成登录→预期401；max_retries=2 下每链路只有两次 HTTP，终态重投额外 HTTP0；固定 JSON 恢复仍过期；普通前置进入 awaiting_auth_input；Cookie 继承、新 Token 独立期限及模糊时间格式不误判。无效 TTL/继承分支是代码层测试，不冒充所有分支均经过 HTTP。
- 启用 `RUN_LIVE_API_TOKEN_EXPIRY_STORAGE=1` 的 `p3-api-token-expiry-storage.xml` **1通过、17排除，7.07秒**：实际隔离 MySQL/PG/RustFS 环境，HTTP 登录取得有效期；现有加密 PG 保存与新服务回读逐字段一致，回读没有 HTTP，明确新建负向请求返回401；同身份再次保存及旧空期限 payload 无哈希冲突，密文无原 Token。本项没有独立 API/真实 Worker 进程中断，不要求或证明额外对象原件产生。
- 最终十三模块 `p3-api-token-expiry-regression.xml` **116通过、8门控跳过，43.10秒**，含有效期、Cookie、多账户来源、HTTP认证、Worker认证、执行恢复、正文、模式隔离、派发、结果读取、未知写入、文档依赖与独立进程恢复。门控存储/生产测试未开启，不计为通过；独立存储结果单独记录，不累加重复用例。`p3-api-token-expiry-compile.json` 记录实际 compileall 退出0，属于附加检查。没有前端修改，未重跑前端构建；外部 AI0。

## 未完成边界

本批仅完成有效期事实与前置可用性查询，不声称已完成自动 401 续期、刷新契约发现、安全重放或 Agent 完整认证规划。访问令牌提取仍有既有的含 token 字段名启发式；任意自定义 Token/TTL 字段映射及 refresh_token/id_token 角色辨别需继续查证。候选发现阶段的通用负向意图识别、本地时钟偏差/网络延迟校准、多账户生产故障恢复仍未验收。

默认 AI 项目选取→排序→补全→执行、准备生命周期、unknown 只读查证、运行中修订、UI 完整知识使用与失败报告、持续容量/流式指标、需求业务评审及生产历史迁移等完整 P0—P5 范围不缩减。Step 已认证，外部 AI 验收按此前安排继续暂缓。
