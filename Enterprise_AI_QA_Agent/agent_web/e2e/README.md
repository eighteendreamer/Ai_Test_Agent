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

P1-82 的后端回归在 `Agent_Server/tests/test_assistant_stream_persistence.py`。它把上述固定回答拆成字符级 SDK 分片，注入每次 delta 写入 5ms 延迟，验证合并后正文及模型/工具链一致。后端首片持久化后才返回回调，后续按 40ms、16384 UTF-8 字节或 256 分片合并成一条正式 delta；SSE 的 event ID 对应持久化批次，客户端按 delta 内容顺序拼接，不依赖供应商原始分片边界。生产中普通提交、手动续跑和审批续跑均等待 writer 结束再进入最终正文与完成事件的原子提交。

在 `Agent_Server` 目录显式开启真实 PostgreSQL 回归（其中一个用例同时连接项目配置的真实 Redis）：

```powershell
$env:RUN_LIVE_POSTGRES_TESTS = '1'
uv run --no-sync python -m pytest -q -s tests/test_assistant_stream_persistence.py tests/test_session_event_delivery.py tests/test_assistant_stream_browser_probe.py
```

覆盖合并、首片、UTF-8 边界、有界反压、闲置模型的定时刷新、写库失败取消执行、重复取消清理、租约失效拒绝旧尾部、SSE 游标回放和 PG/Redis 事实一致。单个上游超大分片保持原文，可能超过字节窗口；进程被强杀时未提交的内存尾部仍可能丢失，历史事件保留期尚未调整。测试不使用真实模型凭据或外部知识服务，也不能据此宣称真实供应商吞吐或崩溃零丢失。

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
