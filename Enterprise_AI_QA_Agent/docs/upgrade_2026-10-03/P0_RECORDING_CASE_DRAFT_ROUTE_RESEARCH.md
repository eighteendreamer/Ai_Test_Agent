# 录制到用例草稿入口、只读恢复与会话范围调研

## 实际依据与根因

`RecordingCaseDraftService` 已由 `main.py` 注入，读取已完成录制的 PG 事件并调用既有 `TestCaseService.create_draft`。原 `recordings` 路由、录制时间线没有调用入口；`RecordingReplayExecutor` 仍是内部执行器，不能把它算作已有回放产品闭环。

接入入口后，真实录制复现再次提交返回 409：第一次已写入的用例触发既有 `(project_id, case_key)` 唯一约束。HTTP 返回丢失时，前端无法辨别“未保存”与“已保存但未收到”。根因是缺少原转换结果的读取能力，不能通过再生成、换编号或忽略唯一约束解决。

时间线原先只查全局最近 20 条录制。实际 PG 插入 25 条其他会话记录后，当前会话录制不在返回页内；前端再过滤无法找回。过滤必须在数据库 LIMIT 前完成。

## 采用做法与对应代码

- `case_store.py`：沿既有项目/编号唯一约束增加精确 `get_case_by_key`，内存与 PG 同步；PG 参数化查询，不新增表。
- `recording_case_draft_service.py`：读取原 V1，核验正式项目、完整 recording_id、规则来源、source_refs 和原内容哈希；保留原生成编号和版本语义。短编号碰撞或内容损坏明确拒绝，不读取最新版本代替原转换版本，不重写旧哈希。
- 创建先读原结果；只有确定的 `DuplicateCaseKeyError` 才读取并验证并发赢家。其他数据库/网络异常原样暴露，不能猜测保存成功或自动重建。
- `recording_store.py`、`recorder_session_service.py`：增加可选 session_id，与 project_id 一起在查询、计数和分页前过滤；无 session_id 的历史调用保持。
- `RecordingTimelinePanel.vue`：终态录制可生成草稿；打开时和重试前 GET 原结果，GET 失败不 POST；按 recording_id 保存请求状态。会话切换拒绝旧请求迟到结果，复用 `TestCaseContentDialog` 显示原版本、评审和启用入口。
- 规则转换不读取生成模板，不调用模型。输入仍按已有统一脱敏规则保存；动作保留 `data.event`，观察保留 `test_data.recording_observations`，截断或缺失观察不能证明业务通过。

## HTTP 契约

```text
POST /api/v1/recordings/{recording_id}/case-draft?project_id=<optional>
201 {recording_id, step_count, case: TestCaseRecord, version: TestCaseVersionRecord}
GET  /api/v1/recordings/{recording_id}/case-draft?project_id=<optional>
200 同上，或 null（尚无转换结果，GET 不写入）
GET  /api/v1/recordings?session_id=<optional>&project_id=<optional>&limit=20
200 {items, count}，过滤在分页前
```

项目参数只核对录制所属项目，不改变写入归属。服务未注入 503；录制不存在 404；未固化、无动作、项目不一致或原转换身份/哈希不一致 409。首次生成仍要求 active 项目；已保存原件在 archived 项目可读。POST 重放沿新接口的 201 契约返回原结果，不另生成版本；返回 case 元数据可以随评审变化，返回 version 始终是原 V1。

## 真实失败与修正记录

- `p0-recording-draft-route-verified`：实际转换已持久化，测试仍把 Pydantic 响应当字典，末尾 TypeError；修正夹具后 `verified2` 1 passed in 5.40s。它们使用 ASGITransport，不是物理网络。
- `p0-recording-draft-route-recovery-before`：再次 POST 实际 409 duplicate case key，证明恢复能力缺失；保留日志、XML、原结果。
- `p0-recording-draft-route-recovery-verified`：修复后真实存储、ASGI 并发/丢回包验证 1 passed in 5.97s。
- `p0-recording-draft-route-delivery`：测试在应用处理请求后才加 middleware，Starlette 拒绝；这属于夹具生命周期错误，未修改生产实现绕过。修正后 `delivery2` 1 passed in 6.10s。
- 早期五个 XML 因相对路径位于 `Agent_Server/docs`，本轮逐字节复制到项目 docs，原文件保留。

## 最终实际验证

| 验证 | 结果及证明 |
|---|---|
| 全部 recording/recorder + replay/assertion/orchestration/case lifecycle/JSON template 回归 | 221 passed、72 skipped，17.10 秒；72 项是门控的外部环境场景，未计为通过；1 条既有 Starlette/AnyIO 警告 |
| 真实 Chromium、隔离 MySQL/PG/RustFS/Memgraph、Uvicorn 本地 TCP HTTP | `p0-recording-draft-route-http-verified`，1 passed、2 deselected，6.41 秒；实际 3 并发 POST 中 2 个 201、1 个故意提交后 503，GET/POST 重放返回同一原件；PG 1 case/1 version；重建服务、归档读取、评审/启用、规划完整事件和项目隔离通过 |
| 实际分页故障 | 全局 top20 不含当前录制；HTTP 按会话/项目过滤返回当前唯一记录；其他项目返回 0 |
| 转换证据 | 长 Unicode 输入完整保存，密码原值不进入草稿，2 次真实点击保留不同 seq；8 个 PG 事件/step_count=8/integrity 非 degraded |
| 前端 API、时间线、既有用例对话框单元回归 | 3 文件 18 passed，1.98 秒；10 项新测试覆盖恢复/迟到结果/错误与编码，8 项原有用例作者界面测试 |
| 前端构建 | `npm run build`，vue-tsc 与 Vite 退出 0；3288 modules、7.86 秒 |
| compileall | 本批 7 个生产 Python 文件、4 个测试退出 0，仅作附加检查 |

实际 HTTP 使用生产路由与服务、测试专属 FastAPI 装配及同进程 Uvicorn socket。它不是生产 main 独立进程重启、正式环境迁移或完整 Vue App 联调。服务重建证明 PG 读回，不称为进程中断验收。已有 RustFS 支撑录制环境，本轮未声称重新证明大工件下载/校验。

## 存储、兼容与未完成

MySQL 配置、PostgreSQL 业务/版本、RustFS 原件/大证据、Memgraph 可重建投影职责保持。无新增表、迁移、配置键、依赖或模式；旧用例版本和录制原事件不改写。

已实现：录制草稿的 API/前端入口、原 V1 只读恢复、并发唯一保存、精确会话过滤。尚未验收：完整 App 按钮到正式主进程、人工 DeepSeek 登录业务、默认 AI 读取知识并执行断言。仍需补实现/核验：回放报告 API/持久化、场景/步骤整理、元素别名、清洗干扰/遗漏、跨文档与 Shadow DOM 全关联、失败定位。默认 Step 因实名认证 403，按用户决定暂缓，不改模型配置或调用该账号。
