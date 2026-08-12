---
name: angular-component-testing
description: 根据 Angular 项目现有 TestBed 和测试栈规划、编写或审查组件、模板、依赖注入、异步变化检测和可访问行为测试。用于 Angular 组件或服务变更。
---

# Angular 组件测试

先读取 Angular 版本、项目 builder、测试配置和邻近 spec；不要假设 Jasmine、Karma、Jest 或其他 runner 的组合。

## 工作流程

1. 从输入、输出、模板语义、用户交互和服务边界定义可观察行为。
2. 以最小 TestBed 配置声明真实依赖，只替换外部或昂贵边界。
3. 明确触发变化检测和异步稳定条件，避免固定延时。
4. 用用户可访问的文本、role、label 和状态验证模板，不依赖脆弱 DOM 层级。
5. 覆盖加载、空、成功、错误、权限和销毁清理路径。
6. 使用项目现有测试命令运行相关 spec 并保留证据。

## 审查重点

- provider 替身是否与真实注入契约一致。
- 订阅、timer 和 fixture 是否在结束时清理。
- 测试是否只调用组件私有方法而绕过模板行为。

## 输出边界

未识别实际 Angular 测试栈时先报告缺失信息，不生成不可运行配置。

## TestBed 与行为矩阵

确认 Angular/CLI、builder、Jasmine/Karma/Jest、zone 配置和现有 spec。以输入、模板语义、用户交互、服务边界和可访问结果建立加载、空、成功、错误、权限和销毁场景；只替换外部或昂贵依赖。

## 异步与参考

明确变化检测、Promise/observable、fakeAsync/tick 或项目既有等待方式；禁止固定延时。检查 provider 替身契约、订阅/timer/fixture 清理。需要 TestBed、DI、异步、模板查询和 runner 差异时读取 `references/source-1.md`。
