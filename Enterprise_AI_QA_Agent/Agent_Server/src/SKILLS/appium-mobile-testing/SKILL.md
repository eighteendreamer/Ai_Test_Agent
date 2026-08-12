---
name: appium-mobile-testing
description: 根据真实 Appium 项目、设备 Provider 和应用构建规划 iOS/Android 跨平台自动化的 capabilities、定位、同步、状态清理和证据采集。用于移动端模式或兼容性规划。
---

# Appium 移动端测试

先确认 Appium server、client、driver、平台版本、设备/模拟器、应用包和签名均真实可用；不得从候选资料推断执行环境。

## 工作流程

1. 定义平台、设备、系统版本、方向、语言和网络条件矩阵。
2. 核对应用标识、启动 activity/bundle、权限、深链和账号前置条件。
3. 优先使用 accessibility id 或稳定原生标识，避免坐标和脆弱层级 XPath。
4. 等待可观察状态或 driver 条件，不以固定 sleep 解决同步问题。
5. 每个用例声明安装/重置策略、数据清理、后台/前台和失败恢复。
6. 采集设备日志、截图、页面源、视频和构建版本，关联到具体设备与步骤。

## 执行边界

当前没有已注册 Appium Runner/Provider 时，只做矩阵规划、代码审查和脚本建议；不得调用通用 shell 冒充移动端执行。

## 输出

报告所需 Provider、capability、场景、断言、清理和缺失执行条件。
