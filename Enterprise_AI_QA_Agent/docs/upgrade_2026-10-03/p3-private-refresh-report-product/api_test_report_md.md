# API 测试报告

**项目**: Cookie report fixture
**目标**: 接口验证
**生成时间**: 2026-10-18T04:01:24.222812+00:00
**Campaign ID**: `cd66eb27-eed2-4f95-811e-a21251571d09`

## 📊 执行摘要

| 指标 | 值 |
|------|-----|
| 总任务数 | 3 |
| 通过 | 2 |
| 失败 | 1 |
| 跳过 | 0 |
| 断言通过 | 2 |
| 断言失败 | 1 |
| 总耗时 | 71ms |

## 🔍 发现问题

- ❌ GET /api/profile: Expect status 201

## 📋 任务详情

### ✅ POST /api/login

- **状态**: completed
- **响应码**: 204
- **耗时**: 43ms
- **断言**: 1 通过 / 0 失败

### ✅ POST /api/refresh

- **状态**: completed
- **响应码**: 204
- **耗时**: 14ms
- **断言**: 1 通过 / 0 失败

### ❌ GET /api/profile

- **状态**: failed
- **响应码**: 200
- **耗时**: 14ms
- **断言**: 0 通过 / 1 失败
- **错误**: Assertions failed: status_code=201

**证据片段:**
```json
{
  "response_status": 200,
  "response_body_preview": "{\"ok\": true, \"opaque_echo\": \"[REDACTED]\"}",
  "error": "Assertions failed: status_code=201",
  "failed_assertions": [
    {
      "actual": 200,
      "description": "Expect status 201",
      "expected": 201,
      "kind": "status_code",
      "name": "status_code=201",
      "passed": false
    }
  ]
}
```
