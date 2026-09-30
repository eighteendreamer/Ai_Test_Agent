# AI 流式传输浏览器探针

这个本地探针用确定性模型替身产生两轮响应：先请求已注册的 `knowledge-rag` 工具，再依据工具结果生成回答。正式 `InputOrchestratorService`、LangGraph 图节点、权限门、`ToolRuntimeService` 派发、`ToolJobService` 生命周期、`SessionService` 分片回调、正式 SSE 路由、Vite API 代理和前端 Pinia Store/`ChatTimeline` 都会参与执行；只有模型供应商和知识库提供方由本地替身隔离，因此不会访问外部服务。

1. 在 `Agent_Server` 目录启动只绑定环回地址的测试后端：

   ```powershell
   uv run --no-sync python -m scripts.assistant_stream_browser_probe --port 13032
   ```

2. 在 `agent_web` 目录启动前端开发服务器，并将 API 代理指向上一步：

   ```powershell
   $env:VITE_API_PROXY_TARGET = 'http://127.0.0.1:13032'
   npm run dev:app
   ```

3. 在浏览器打开 `http://127.0.0.1:5175/e2e/assistant-stream-probe.html`。页面先加载真实会话，再连接真实 SSE。通过条件包括：模型完成两轮调用、工具任务完成且结果进入第二轮模型请求、浏览器收到的分片拼接后与持久化 transcript 完全一致、目标助手消息只出现一次。

4. 按 `Ctrl+C` 分别停止两个本地进程。

后端自动回归为 `Agent_Server/tests/test_assistant_stream_browser_probe.py`。这条链路验证的是确定性模型和知识工具替身下的应用执行及浏览器传输，不代表真实模型供应商或真实知识库服务已通过端到端验证。
