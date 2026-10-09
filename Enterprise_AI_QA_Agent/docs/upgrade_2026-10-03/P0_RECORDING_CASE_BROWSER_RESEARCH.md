# 录制草稿真实组件验收依据

## 依据与范围

7.03 已补 API/时间线入口并验证实际 HTTP、并发唯一和原 V1 恢复。本轮只验证新入口的生产 Vue 组件，不扩展生产功能。

`tests/test_case_import_browser.py` 是项目已有的 Vite 独立宿主页、Uvicorn socket、Chromium、隔离配置/业务/原件存储模板；`RecordingTimelinePanel.vue` 读取真实 Pinia 会话和后台录制；`TestCaseContentDialog.vue` 加载完整固定版本、兼容 `content_json=None` 的旧结构、保存请求身份并经原评审/启用 API 操作。沿这些实现创建 `test_recording_case_draft_browser.py`，不模拟前端 API 或对话框。

录制事件来自已提交 7.03 的 `p0-recording-draft-route-http-verified/pg-events.json`，重新载入唯一隔离 PG 表，录制/项目/会话身份为本次测试所有。原浏览器采集、停止完整性及脱敏已经有直接证据；此处不声称重新人工采集。MySQL、PG、RustFS 分工保持，Memgraph 对象不投影本次夹具，没有新增表结构、配置、依赖或模式。

## 失败与最终验证

1. `p0-recording-case-browser-before`：实际浏览器完成转换/丢回包恢复/JSON显示，测试错误把对话框原文与 `content_json=None` 比较。对照组件已有兼容投影修正测试，不修改生产保存结构或哈希。
2. `p0-recording-case-browser-verified`：测试点击启用后，按钮在请求中已 disabled，但 PG 尚未启用；断言过早。改为等待实际 POST `/activate` HTTP200，再等待页面“当前版本：已确认 · 当前启用”。不添加生产延迟或重试。
3. `p0-recording-case-browser-verified2`：实际 **1 passed in 6.00s**。compileall 退出0，仅附加检查。

实际证明：

- 正式生产时间线按钮第一次发送 POST，保存后故意回503，页面显示后端具体错误。
- 再点击先 GET，找回原草稿；整批转换 POST 只有1次，不另创建。
- 真实用例对话框读取 case/versions；JSON 原文与原 V1 完整兼容投影一致，长 Unicode 输入无截断。
- 真实点击评审/启用，HTTP200 与 PG 的 active_version_id、评审事件及原版本身份一致。
- 浏览器刷新后重新 GET、展示同一用例和版本；转换按钮已禁用，不再 POST。
- PG仅1条用例/1版本，page_errors=[]。截图 `dialog.png`/`timeline.png` 与全部请求 `server-http.json` 保存；临时宿主页、Vite子进程、Chromium、Uvicorn和本次隔离存储完成回收。

没有修改生产源文件；不重复已通过的221后端/18前端/build。旧失败原件保留，不改写为通过。

## 仍未完成

此验收是生产组件和生产服务在独立宿主页/测试专属 FastAPI 装配中的真实联调；完整生产 App/main 独立进程、人工 DeepSeek 登录业务、默认 AI 使用知识/正式断言未验收。回放报告 API/持久化、场景步骤/元素别名/清洗干扰与遗漏/失败定位继续待实现或核验。默认 Step 因403实名认证按用户决定暂缓。
