"""
UI 界面探索服务（独立于 browser-use）

基于 Claude Code 的 Agent 架构复刻：
  - Agent 循环：System Prompt → LLM 调用 → 工具执行 → 结果返回 → 迭代
  - 工具集：导航、点击、输入、滚动、截图、读取页面、查找元素、JS执行等
  - 提示词：行为引导 + 任务定义 + 输出格式约束
  - Hooks：前置校验 + 后置清理

接入接口：POST /api/knowledge/explore-page
"""
