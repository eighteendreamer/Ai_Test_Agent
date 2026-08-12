---
name: espresso-android-testing
description: 根据 Android 项目现有测试配置规划 Espresso 原生 UI 交互、matcher、同步、IdlingResource、权限和行为断言。用于 Android 原生界面或 instrumented test 变更。
---

# Espresso Android 测试

先读取 Gradle、Android 插件、测试 runner、依赖和现有 instrumented tests；不要假设候选版本或依赖存在。View/Compose matcher 和同步细节按需读取 `references/source-1.md`。

## 输入契约

确认 Activity/Fragment/Compose 边界、Gradle 任务、设备/API 级别、权限、intent、数据和构建 variant。

## 工作流程

1. 从 Activity/Fragment/Compose 边界和用户任务定义场景。
2. 使用稳定 view id、文本和语义 matcher，避免坐标或视图层级位置。
3. 依赖 Espresso 同步；应用外异步资源需要真实且可释放的 IdlingResource。
4. 明确 intent、权限、旋转、进后台、进程恢复和测试数据前置。
5. 断言用户可观察结果和跨组件效果，不只检查 view 存在。
6. 保存设备、构建、测试输出、截图和失败栈。

## 执行门禁

1. 使用稳定 view id/语义 matcher，禁止坐标和层级位置断言。
2. 依赖 Espresso 同步；应用外异步资源必须使用可释放的 IdlingResource。
3. 覆盖权限、旋转、后台/进程恢复和用户可观察效果；测试数据需隔离清理。
4. 记录 Gradle 命令、设备/API、构建、日志、截图和退出码。

## 输出

输出任务/设备矩阵、matcher/同步风险、数据策略、命令和缺失条件；无 Android Runner 时只规划。

## 执行边界

当前无 Android 构建/设备 Runner 时只做规划与代码审查，不声称 instrumented test 已运行。

## 输出

列出 Gradle 任务依据、设备矩阵、场景、同步点和缺失条件。
