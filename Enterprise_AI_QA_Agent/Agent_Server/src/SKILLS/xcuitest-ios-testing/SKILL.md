---
name: xcuitest-ios-testing
description: 根据 Xcode 项目实际 scheme、test plan 和目标设备规划 XCUITest 启动参数、可访问标识、等待、系统权限和行为断言。用于 iOS 原生 UI 或测试代码变更。
---

# XCUITest iOS 测试

先读取 Xcode 工程、scheme、test plan、最低系统版本和现有 UI tests；不要假设候选版本或设备可用。XCUITest predicate 和 xcresult 细节按需读取 `references/source-1.md`。

## 输入契约

确认 scheme/test plan、配置、模拟器/真机、最低系统、应用标识、launch 参数、权限、账号和清理策略。

## 工作流程

1. 将应用构建、scheme、配置、模拟器/真机和系统版本绑定到测试证据。
2. 使用稳定 accessibility identifier、label 和 element type，避免坐标操作。
3. 用 predicate/expectation 等待可观察状态，设置合理超时并保留失败上下文。
4. 通过 launch arguments/environment 建立独立状态，清理 keychain、账号和持久化数据。
5. 覆盖系统权限、深链、旋转、后台恢复、网络失败和导航返回。
6. 保存 xcresult、控制台、截图、视频、设备和构建版本。

## 执行门禁

1. 使用 accessibility identifier/label/type，禁止坐标操作。
2. 通过 predicate/expectation 等待可观察状态，记录超时和上下文。
3. 覆盖权限、深链、旋转、后台恢复、网络失败和返回；隔离 keychain/持久化数据。
4. 失败关联 scheme、设备/OS、构建、步骤、xcresult、日志、截图/视频和退出码。

## 输出

输出 scheme/test plan 依据、设备矩阵、场景、等待/状态策略和缺失能力；无 macOS/Xcode Runner 时只规划。

## 执行边界

当前无 Xcode/macOS Runner 或设备 Provider 时只做规划与代码审查，不声称 UI test 已执行。

## 输出

列出 scheme/test plan 依据、设备矩阵、场景、等待点和缺失条件。
