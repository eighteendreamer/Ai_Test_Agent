# UI 录制网络响应关联研究

基线：`b75e6b14`。本步先核对现有 `recorder.js`、`RecordingCaptureSettings`、录制 PG 事件和 assertion consumer，再按浏览器 API 实际语义设计；默认 AI 仍按用户决定暂缓。

## 事实与边界

- 原实现只有 `MutationObserver` 的 DOM 观察，`page_response` 的关联是 `temporal_after_actions`，没有 fetch/XHR 状态；网络响应即使驱动 DOM 变化，也没有网络证据。
- 浏览器 `fetch()` 在 Response 可用时 resolve；HTTP 404/500 不会 reject，必须读取 `status`/`ok`。XHR 的 `loadend` 是收口点。依据：[MDN Window.fetch](https://developer.mozilla.org/en-US/docs/Web/API/Window/fetch)、[MDN XMLHttpRequestEventTarget.load event](https://developer.mozilla.org/en-US/docs/Web/API/XMLHttpRequestEventTarget/load_event)。
- 不记录请求头、请求体、响应体或完整 URL 查询串；它们可能包含 token、Cookie、业务隐私。网络证据只保留 method、same/cross-origin、pathname、status、ok、outcome 和 duration。
- 未在操作响应窗口中开始的 fetch/XHR 不归因到该操作；同一窗口仍可能包含多个操作，所以该关联是 instrumented completion + action local sequence，不是业务因果或服务端事务证明。未覆盖 beacon、WebSocket、Service Worker 内部请求、导航和 closed 页面上下文。

## 实现

`recorder.js` 在 scoped response capture 启用时包装当前文档的 fetch 与 XHR：操作捕获阶段建立 response window；网络完成时向该 window 写入脱敏元数据；DOM settle 或 max_wait 收口时一并发出 `page_response`。fetch 的 HTTP 错误保留为 `outcome=response, ok=false`，网络异常、abort、timeout 单独标记。重复事件使用完成保护，避免 XHR error/loadend 双重计数。协议沿用 `recording-dom-response-v1`，新增 `network_correlation=instrumented_fetch_xhr_completion`、`network_observation_status` 和 `network` 字段，不新增表或配置。

## 实际复现与验证

- 修复前使用真实 Chromium、生产录制 API、隔离 MySQL/PostgreSQL/Redis/Memgraph 和本地 HTTP 页面：一次点击触发 fetch 200、fetch 404、XHR 201，PG 已有 click 和 DOM `page_response`，但 response 没有 `network` 字段；测试以 `KeyError: network` 失败。原件见 `p5-recording-network-before2/`。
- 修复后同一夹具 **1通过16.43秒**：三个请求均与 click 的 local sequence 关联，状态分别 200/true、404/false、201/true；查询串 `redaction-probe` 和响应体 `response-body-probe` 均未进入网络证据。
- Shadow DOM、录制策略、范围、质量回归 **17通过、40跳过、31.86秒**；Node 语法检查通过。

本步补齐的是可审计的 fetch/XHR 网络元数据和操作窗口关联，不宣称完成服务端业务因果、网络全覆盖、selector 修复、失败定位报告或默认 AI 读取执行。
