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

## 前端分片处理性能

P1-83 的固定输入在 `assistant-stream-performance.ts`：100 条历史消息，依次输入 100、500、1000 个分片，正文包含代码和公式。它复用 SSE 解码后的回调、Pinia Store 和正式 ChatTimeline；每次更新等待 Vue DOM 提交，最后检查正文完全一致。记录首片 DOM 可见时间、总处理时间、字符处理吞吐、逐片更新 P50/P95/P99 与错误数。生成时使用转义的纯文本，首片立即显示，后续按 40ms 合并；终态刷新待处理文本并恢复 Markdown、代码高亮、公式及复制按钮。

可在前端开发服务器的页面控制台执行：

```javascript
await (await import('/e2e/assistant-stream-performance.ts')).benchmarkAssistantStream()
```

自动对比使用 `measure-assistant-stream.py`。在独立临时项目复制当前前端及探针，将 `src/stores/session.ts` 和 `src/components/chat/AssistantMarkdown.vue` 替换为基线提交版本，在 5176 启动基线 Vite；当前版本使用 5175。基线 Vite 必须配置独立的 `cacheDir: "./.vite-baseline"`，尤其是复用 node_modules 时，避免两个实例互相覆盖优化缓存。无需启动测试后端；脚本用空白页面加载模块和正式样式，不会触发模型或工具请求。

在 `Agent_Server` 目录运行（使用项目已有 Playwright/Chromium）：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
uv run --no-sync python ../agent_web/e2e/measure-assistant-stream.py --baseline-url http://127.0.0.1:5176 --baseline-commit 01946e2 --output ../agent_web/e2e/results/P1-83_stream_performance_2026-10-01.json
```

每侧重复三次，输出所有原始指标及总耗时中位数；已有结果文件不会被覆盖，再次执行应使用新文件名。该测试测量突发分片输入下的 JavaScript/Vue DOM 处理，包含最终格式化，不测网络、模型 Token 吞吐、真实 TTFT 或屏幕绘制延迟。快速样本受浏览器计时精度影响，P50/P95 显示 0 不代表没有 CPU 开销。真实 SSE 路由与模型工具执行由上面的传输探针单独验证。
