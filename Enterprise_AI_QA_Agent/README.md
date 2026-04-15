# Enterprise AI QA Agent

这个目录是新的目标项目，分为两个子工程：

- `Agent_Server`: FastAPI + LangGraph 后端框架
- `agent_web`: Vue 3 + Vite 前端工作台
- `docs/HARNESS_ENGINEERING_开发规范.md`: 后续项目开发必须遵循的 Harness Engineering 规范

## 设计映射

参考 `claude_code_ui_Agent` 的方式，这里先把项目拆成三层骨架：

1. `registry`
   管理 Agent 和 Tool 的元数据注册，后续接不同测试模块时只需扩展这里。
2. `graph`
   用 LangGraph 组织执行链路，当前先实现 `router / planner / executor / responder` 四段。
3. `ui + api`
   前端工作台用 Vue 还原你提供的原型结构，后端提供会话、流式事件、注册中心接口。

## 当前适合做什么

- 继续接 Playwright / Selenium Agent
- 接入页面知识库与 RAG
- 扩展任务池、报告中心、统一配置中心
- 把内存态 store 替换成数据库或缓存
