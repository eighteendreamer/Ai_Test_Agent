# API 测试报告

**项目**: Cookie report fixture
**目标**: 接口验证
**生成时间**: 2026-10-18T03:40:37.426480+00:00
**Campaign ID**: `b32e4176-b396-4f13-8e76-5e3ae815dfdc`

## 📊 执行摘要

| 指标 | 值 |
|------|-----|
| 总任务数 | 3 |
| 通过 | 2 |
| 失败 | 1 |
| 跳过 | 0 |
| 断言通过 | 2 |
| 断言失败 | 1 |
| 总耗时 | 82ms |

## 🔍 发现问题

- ❌ GET /api/profile: Expect status 201

## 📋 任务详情

### ✅ POST /api/login

- **状态**: completed
- **响应码**: 204
- **耗时**: 49ms
- **断言**: 1 通过 / 0 失败

### ✅ POST /api/refresh

- **状态**: completed
- **响应码**: 204
- **耗时**: 15ms
- **断言**: 1 通过 / 0 失败

### ❌ GET /api/profile

- **状态**: failed
- **响应码**: 200
- **耗时**: 17ms
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
      "kind": "status_code",
      "name": "status_code=201",
      "actual": 200,
      "passed": false,
      "expected": 201,
      "description": "Expect status 201"
    }
  ]
}
```
