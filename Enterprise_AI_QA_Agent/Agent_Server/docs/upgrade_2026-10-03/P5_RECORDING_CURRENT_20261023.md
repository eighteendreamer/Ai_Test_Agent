# P5 UI 录制当前验收

日期：2026-10-23

## 已通过的真实场景

| 场景 | 命令 | 结果 |
|---|---|---|
| DeepSeek 公网页面人工操作、录制入库、质量诊断和 case draft | `tests/test_recording_deepseek_live.py` | 1 passed，28.56 秒 |
| DeepSeek CDP 事件流结束回执 | `tests/test_recorder_deepseek_live.py` | 1 passed，3.68 秒 |
| Shadow DOM 嵌套/动态根、iframe 响应及敏感祖先过滤 | `tests/test_recording_shadow_live.py` | 1 passed |
| Fetch/XHR 200、404、201 响应关联和查询参数/请求体脱敏 | `tests/test_recording_network_live.py` | 1 passed |
| 真实重复点击保留、质量投影和 case draft 不改原事件 | `tests/test_recording_quality_live.py` | 1 passed |
| 场景管理、元素别名、固定版本知识读取、来源哈希校验和规划检查点恢复 | `tests/test_scene_planning_live.py` | 4 passed，214.73 秒；真实 Chromium/CDP、MySQL、PostgreSQL、RustFS 和受控模型 |

证据目录：

- `p5-deepseek-recording-current-20261023-01/`
- `p5-deepseek-recorder-probe-current-20261023-01/`
- `p5-recording-shadow-current-20261023-01/`
- `p5-recording-network-current-20261023-01/`
- `p5-recording-quality-current-20261023-01/`
- `p5-scene-planning-current-20261023-rerun/`

实际核对项包括：PostgreSQL 事件序号、结束回执、RustFS/报告工件、录制质量、动作与响应关联、开放 Shadow DOM、跨 frame 记录、网络状态和敏感字段不泄露。测试夹具结束后隔离存储和临时 Redis 均清理。

场景规划专项还确认：场景版本和别名随固定来源读取，规划上下文使用保存的内容哈希与原始快照；篡改投影、原生内容、原生来源、缺失版本/哈希、错误哈希及跨项目来源均被拒绝或形成明确缺口。检查点确认丢失回执后不重复模型调用，终态任务重投不新增调用。该轮结束后没有 `qa-execution-cli-*` 容器残留。

## 结论边界

- 公网页面测试没有登录、提交账号消息或使用用户数据，只验证 DeepSeek 首页的低频人工动作。
- 相同动作的重复候选会被标记，真实重复点击不会被自动删除；这符合保留用户实际操作的约束。
- `business_capture_complete` 当前仍是 `unknown`。录制系统能证明已发布事件无序号缺口、无丢帧、响应关联已解析，不能凭这些证据证明任意页面业务事件都没有遗漏。
- Shadow DOM 证据明确标记 `closed_shadow_dom=unobserved`；开放根已验收，闭合根仍是能力边界。
- 场景管理、元素别名、固定来源知识读取和规划恢复已有受控真实验收；步骤整理的更广产品交互、默认 Step 实际引用知识、失败定位报告和完整产品闭环仍未完成默认 Step 验收。

