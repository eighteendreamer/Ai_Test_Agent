---
name: espresso-android-testing
description: 根据 Android 项目现有测试配置规划 Espresso 原生 UI 交互、matcher、同步、IdlingResource、权限和行为断言。用于 Android 原生界面或 instrumented test 变更。
---

# Espresso Android 测试

先读取 Gradle、Android 插件、测试 runner、依赖和现有 instrumented tests；不要假设候选版本或依赖存在。

## 工作流程

1. 从 Activity/Fragment/Compose 边界和用户任务定义场景。
2. 使用稳定 view id、文本和语义 matcher，避免坐标或视图层级位置。
3. 依赖 Espresso 同步；应用外异步资源需要真实且可释放的 IdlingResource。
4. 明确 intent、权限、旋转、进后台、进程恢复和测试数据前置。
5. 断言用户可观察结果和跨组件效果，不只检查 view 存在。
6. 保存设备、构建、测试输出、截图和失败栈。

## 执行边界

当前无 Android 构建/设备 Runner 时只做规划与代码审查，不声称 instrumented test 已运行。

## 输出

列出 Gradle 任务依据、设备矩阵、场景、同步点和缺失条件。
