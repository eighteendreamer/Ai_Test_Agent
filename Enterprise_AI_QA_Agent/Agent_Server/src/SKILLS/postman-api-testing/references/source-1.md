---
name: Postman API Testing
description: 使用 Postman 和 Newman 进行 API 测试，支持集合、环境和监控
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [api]
info: vip.hctestedu.com
frameworks: []
languages: [javascript]
domains: [api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Postman API 测试

您是一位专注于 Postman API 测试的 QA 工程师。当用户要求您使用 Postman 进行 API 测试时，请遵循这些详细说明。

## 核心原则

1. **集合管理** -- 使用集合组织 API 测试。
2. **环境隔离** -- 使用环境变量管理不同环境。
3. **自动化执行** -- 使用 Newman 在 CI/CD 中运行测试。
4. **监控和报告** -- 设置定期监控并生成报告。
5. **团队协作** -- 使用 Postman 团队工作区共享集合。

## Postman 集合结构

```
My API Collection
├── Folder: Users
│   ├── GET /users - List users
│   ├── POST /users - Create user
│   ├── GET /users/:id - Get user
│   ├── PUT /users/:id - Update user
│   └── DELETE /users/:id - Delete user
├── Folder: Authentication
│   ├── POST /auth/login - Login
│   ├── POST /auth/register - Register
│   └── POST /auth/refresh - Refresh token
└── Folder: Orders
    ├── GET /orders - List orders
    ├── POST /orders - Create order
    └── GET /orders/:id - Get order
```

## 测试脚本

### 基础请求测试

```javascript
// GET /users - List users
pm.test("Status code is 200", () => {
    pm.response.to.have.status(200);
});

pm.test("Response has users array", () => {
    const response = pm.response.json();
    pm.expect(response).to.have.property('data');
    pm.expect(response.data).to.be.an('array');
});

pm.test("User has required properties", () => {
    const response = pm.response.json();
    if (response.data.length > 0) {
        const user = response.data[0];
        pm.expect(user).to.have.property('id');
        pm.expect(user).to.have.property('email');
        pm.expect(user).to.have.property('name');
    }
});
```

### POST 请求测试

```javascript
// POST /users - Create user
const userData = {
    name: "Test User",
    email: `test${Date.now()}@example.com`,
    password: "SecurePass123!"
};

pm.request.body = {
    mode: 'raw',
    raw: JSON.stringify(userData),
    options: {
        raw: {
            language: 'json'
        }
    }
};

pm.test("Status code is 201", () => {
    pm.response.to.have.status(201);
});

pm.test("Response has created user data", () => {
    const response = pm.response.json();
    pm.expect(response).to.have.property('id');
    pm.expect(response.email).to.eql(userData.email);
    pm.expect(response.name).to.eql(userData.name);
    pm.expect(response).to.not.have.property('password');
});

pm.test("Content-Type is JSON", () => {
    pm.expect(pm.response.headers.get('Content-Type')).to.include('application/json');
});
```

### 认证测试

```javascript
// POST /auth/login
const loginData = {
    email: pm.variables.get("test_email"),
    password: pm.variables.get("test_password")
};

pm.request.body = {
    mode: 'raw',
    raw: JSON.stringify(loginData)
};

pm.test("Login successful", () => {
    pm.response.to.have.status(200);
});

pm.test("Response has token", () => {
    const response = pm.response.json();
    pm.expect(response).to.have.property('token');
    pm.expect(response.token).to.be.a('string');
});

// 保存 token 到环境变量
if (pm.response.code === 200) {
    const response = pm.response.json();
    pm.collectionVariables.set("auth_token", response.token);
}
```

### 链式请求测试

```javascript
// 在集合的第一个请求（登录）中设置变量
// Tests tab:
if (pm.response.code === 200) {
    var jsonData = pm.response.json();
    pm.collectionVariables.set("token", jsonData.token);
    pm.collectionVariables.set("user_id", jsonData.user.id);
}

// 在后续请求中使用 {{token}}
// Headers:
Authorization: Bearer {{token}}
```

## Pre-request 脚本

### 动态数据生成

```javascript
// 生成随机邮箱
const randomEmail = `test_${Date.now()}_${Math.random().toString(36).substring(7)}@example.com`;
pm.variables.set("random_email", randomEmail);

// 生成 UUID
const uuid = require('uuid');
pm.variables.set("uuid", uuid.v4());

// 动态日期
const tomorrow = new Date();
tomorrow.setDate(tomorrow.getDate() + 1);
pm.variables.set("tomorrow_date", tomorrow.toISOString());
```

### 请求签名

```javascript
// HMAC 签名示例
const crypto = require('crypto-js');

const timestamp = Math.floor(Date.now() / 1000);
const secret = pm.variables.get("api_secret");

const payload = `${timestamp}.${pm.request.body.raw}`;
const signature = crypto.HmacSHA256(payload, secret).toString();

pm.request.headers.add({
    key: 'X-Signature',
    value: signature
});

pm.request.headers.add({
    key: 'X-Timestamp',
    value: timestamp.toString()
});
```

## 集合运行器

### 使用 Newman 运行

```bash
# 安装 Newman
npm install -g newman

# 安装 HTML 报告
npm install -g newman-reporter-htmlextra

# 运行集合
newman run "collection.json" -e "environment.json"

# 运行带报告
newman run "collection.json" \
    -e "environment.json" \
    -r html,cli,json \
    --reporter-html-template "./node_modules/newman-reporter-htmlextra/templates/default-dark.hbs" \
    --reporter-html-export report.html

# 运行特定文件夹
newman run "collection.json" \
    --folder "Users" \
    -e "environment.json"

# 迭代运行次数
newman run "collection.json" \
    -e "environment.json" \
    -n 10
```

### 环境配置

```json
{
    "id": "api-test-env",
    "name": "API Test Environment",
    "values": [
        {
            "key": "base_url",
            "value": "https://api.example.com",
            "enabled": true
        },
        {
            "key": "test_email",
            "value": "test@example.com",
            "enabled": true
        },
        {
            "key": "test_password",
            "value": "TestPass123!",
            "enabled": true
        },
        {
            "key": "auth_token",
            "value": "",
            "enabled": true
        }
    ]
}
```

## CI/CD 集成

### GitHub Actions

```yaml
name: API Tests with Postman
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  postman-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Node
        uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install Newman
        run: npm install -g newman newman-reporter-htmlextra

      - name: Start API Server
        run: npm run start:api &
        timeout-minutes: 2

      - name: Wait for server
        run: npx wait-on http://localhost:3000

      - name: Run API Tests
        run: |
          newman run api-tests/collection.json \
            -e api-tests/environments/test.postman_environment.json \
            -r html,cli,json \
            --reporter-html-export reports/api-test-report.html \
            --reporter-json-export reports/api-test-report.json

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: postman-test-results
          path: |
            reports/api-test-report.html
            reports/api-test-report.json

      - name: Check for failures
        run: |
          if [ -f reports/api-test-report.json ]; then
            FAILURES=$(cat reports/api-test-report.json | jq '.run.stats.failures')
            if [ "$FAILURES" -gt 0 ]; then
              echo "API Tests Failed: $FAILURES failures"
              exit 1
            fi
          fi
```

## 监控设置

### Postman Monitor 配置

```javascript
// 用于监控的测试脚本
// 和普通测试类似，但需要考虑：

// 1. 设置合理的超时
pm.request.timeout = 10000;

// 2. 验证健康状态
pm.test("API Health Check", () => {
    pm.response.to.have.status(200);
});

// 3. 基本的响应验证
pm.test("Response has data", () => {
    const response = pm.response.json();
    pm.expect(response).to.have.property('data');
});

// 4. 性能验证
pm.test("Response time is acceptable", () => {
    pm.expect(pm.response.responseTime).to.be.below(2000);
});
```

## 最佳实践

1. **组织良好的集合** -- 使用文件夹和命名约定。
2. **使用环境变量** -- 区分开发和生产环境。
3. **链式请求** -- 使用变量在请求之间传递数据。
4. **全面的测试** -- 验证状态码、响应结构和数据。
5. **清理数据** -- 在测试后清理创建的测试数据。
6. **文档化集合** -- 添加描述和文档说明用途。
7. **版本控制** -- 将集合和环境文件纳入版本控制。
8. **定期运行** -- 设置监控和 CI 集成。

## 应避免的反模式

1. **硬编码值** -- 使用变量和环境。
2. **不测试负面情况** -- 测试错误情况同样重要。
3. **忽略性能** -- 包含响应时间断言。
4. **过长的测试** -- 拆分成小的独立测试。
5. **不清理状态** -- 测试之间保持干净状态。
6. **忽略认证** -- 确保认证请求被正确处理。
7. **不验证响应** -- 添加有意义的断言。
8. **忽略错误消息** -- 验证错误响应的格式和内容。