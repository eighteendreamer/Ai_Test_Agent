# AI 流式传输浏览器探针

这个本地探针用固定文本替代外部模型，经过正式 `SessionService` 分片回调、正式会话 SSE 路由、Vite API 代理和前端 Pinia Store/`ChatTimeline`。它不访问模型供应商、扫描器或其他外部服务。

1. 在 `Agent_Server` 目录启动只绑定环回地址的测试后端：

   ```powershell
   uv run --no-sync python -m scripts.assistant_stream_browser_probe --port 13032
   ```

2. 在 `agent_web` 目录启动前端开发服务器，并将 API 代理指向上一步：

   ```powershell
   $env:VITE_API_PROXY_TARGET = 'http://127.0.0.1:13032'
   npm run dev:app
   ```

3. 在浏览器打开 `http://127.0.0.1:5175/e2e/assistant-stream-probe.html`。页面先加载真实会话，再连接真实 SSE；后端逐片发送确定性回答并持久化最终 transcript。成功时页面状态会显示“通过”，回答应恰好出现一次。

4. 按 `Ctrl+C` 分别停止两个本地进程。
