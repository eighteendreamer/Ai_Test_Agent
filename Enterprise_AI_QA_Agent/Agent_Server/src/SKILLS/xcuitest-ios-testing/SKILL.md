---
name: xcuitest-ios-testing
description: 根据 Xcode 项目实际 scheme、test plan 和目标设备规划 XCUITest 启动参数、可访问标识、等待、系统权限和行为断言。用于 iOS 原生 UI 或测试代码变更。
---

# XCUITest iOS 测试

先读取 Xcode 工程、scheme、test plan、最低系统版本和现有 UI tests；不要假设候选版本或设备可用。

## 工作流程

1. 将应用构建、scheme、配置、模拟器/真机和系统版本绑定到测试证据。
2. 使用稳定 accessibility identifier、label 和 element type，避免坐标操作。
3. 用 predicate/expectation 等待可观察状态，设置合理超时并保留失败上下文。
4. 通过 launch arguments/environment 建立独立状态，清理 keychain、账号和持久化数据。
5. 覆盖系统权限、深链、旋转、后台恢复、网络失败和导航返回。
6. 保存 xcresult、控制台、截图、视频、设备和构建版本。

## 执行边界

当前无 Xcode/macOS Runner 或设备 Provider 时只做规划与代码审查，不声称 UI test 已执行。

## 输出

列出 scheme/test plan 依据、设备矩阵、场景、等待点和缺失条件。
