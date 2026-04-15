# Enterprise AI QA Agent Server

一个面向企业级 Agent 测试平台的后端框架骨架，目标是用 `FastAPI + LangGraph` 复刻类似 Claude Code 的核心组织方式，但先聚焦在可扩展框架，而不是具体能力实现。

## 当前包含

- 会话管理：创建会话、读取会话、发送消息
- Agent 注册中心：先以内置占位模块呈现，后续可扩展
- Tool 注册中心：维护工具能力声明
- LangGraph 编排：`router -> planner -> executor -> responder`
- SSE 事件流：前端可以实时看到节点执行状态
- 内存态存储：方便先把 UI 和编排跑起来

## 目录结构

```text
src/
  api/            FastAPI 路由层
  application/    用例服务层
  core/           配置
  domain/         领域模型
  graph/          LangGraph 状态图与节点
  registry/       Agent/Tool 注册中心
  runtime/        存储与流式事件
  schemas/        Pydantic 输入输出
```

## 启动

```bash
cd Agent_Server
uvicorn src.main:app --reload --port 8000
```

## 后续接入建议

1. 在 `src/registry/agents.py` 注册真实 Agent 模块元数据。
2. 在 `src/graph/nodes/executor.py` 接入具体 Agent 运行器或工具调用器。
3. 若需要持久化，把 `src/runtime/store.py` 替换为 Redis / PostgreSQL / MongoDB 实现。
4. 若需要真正的多 Agent 协同，可把 `planner` 与 `executor` 拆成更细的子图。
