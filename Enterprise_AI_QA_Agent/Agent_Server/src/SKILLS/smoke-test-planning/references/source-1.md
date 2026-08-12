---
name: Production Smoke Suite
description: 生产环境冒烟测试，验证关键功能在生产环境中正常工作
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [smoke]
info: vip.hctestedu.com
frameworks: []
languages: [typescript, javascript]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 生产环境冒烟测试

您是一位专注于生产环境冒烟测试的 QA 工程师。当用户要求您设置和运行生产环境冒烟测试时，请遵循这些详细说明。

## 核心原则

1. **关键路径测试** -- 测试生产环境中最关键的功能。
2. **快速执行** -- 冒烟测试应该快速完成。
3. **可靠性优先** -- 测试必须稳定可靠。
4. **最小化影响** -- 不应显著影响生产性能。
5. **即时反馈** -- 快速发现生产环境问题。

## 测试范围

### 关键功能
- 用户登录/登出
- 核心业务流程
- 关键 API 端点
- 数据库连接
- 缓存层
- 认证服务

### 健康检查
- 服务健康端点
- 依赖服务连接
- 队列/消息服务
- 存储服务

## 项目结构

```
smoke-tests/
├── src/
│   ├── tests/
│   │   ├── production/
│   │   │   ├── auth.spec.ts
│   │   │   ├── api.spec.ts
│   │   │   └── critical-flows.spec.ts
│   │   └── health/
│   │       └── health-check.spec.ts
│   ├── config/
│   │   └── prod.config.ts
│   └── utils/
│       ├── reporter.ts
│       └── alerting.ts
├── package.json
└── smoke.config.ts
```

## 配置

```typescript
// smoke.config.ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './src/tests',
  timeout: 30000,
  retries: 0,  // 生产环境不重试
  workers: 1,   // 串行执行避免并发问题

  use: {
    baseURL: process.env.PRODUCTION_URL || 'https://api.example.com',
    headless: true,
  },

  reporter: [
    ['html', { outputFolder: 'smoke-reports' }],
    ['json', { outputFile: 'smoke-reports/results.json' }],
  ],

  projects: [
    {
      name: 'production',
      use: {
        baseURL: 'https://api.example.com',
      },
    },
  ],
});
```

## 冒烟测试套件

### 健康检查测试

```typescript
// src/tests/health/health-check.spec.ts
import { test, expect, request } from '@playwright/test';

test.describe('Production Health Checks', () => {
  test('API health endpoint should respond', async () => {
    const response = await request.get('/api/health');

    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body.status).toBe('healthy');
  });

  test('Database connection should be healthy', async () => {
    const response = await request.get('/api/health/db');

    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body.database).toBe('connected');
  });

  test('Cache layer should be operational', async () => {
    const response = await request.get('/api/health/cache');

    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body.cache).toBe('operational');
  });

  test('Message queue should be available', async () => {
    const response = await request.get('/api/health/queue');

    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body.queue).toBe('available');
  });
});
```

### 认证测试

```typescript
// src/tests/production/auth.spec.ts
import { test, expect, request } from '@playwright/test';

test.describe('Production Authentication', () => {
  test('should login with valid credentials', async () => {
    const response = await request.post('/api/auth/login', {
      data: {
        email: process.env.TEST_USER_EMAIL,
        password: process.env.TEST_USER_PASSWORD,
      },
    });

    expect(response.ok()).toBeTruthy();
    expect(response.status()).toBe(200);

    const body = await response.json();
    expect(body).toHaveProperty('token');
    expect(body).toHaveProperty('user');
  });

  test('should reject invalid credentials', async () => {
    const response = await request.post('/api/auth/login', {
      data: {
        email: 'invalid@example.com',
        password: 'wrongpassword',
      },
    });

    expect(response.status()).toBe(401);
  });

  test('should maintain session', async () => {
    // 登录
    const loginResponse = await request.post('/api/auth/login', {
      data: {
        email: process.env.TEST_USER_EMAIL,
        password: process.env.TEST_USER_PASSWORD,
      },
    });

    const { token } = await loginResponse.json();

    // 使用 token 访问受保护的资源
    const meResponse = await request.get('/api/users/me', {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    expect(meResponse.ok()).toBeTruthy();
  });
});
```

### 关键 API 测试

```typescript
// src/tests/production/api.spec.ts
import { test, expect, request } from '@playwright/test';

test.describe('Production Critical APIs', () => {
  let authToken: string;

  test.beforeAll(async () => {
    // 获取认证 token
    const loginResponse = await request.post('/api/auth/login', {
      data: {
        email: process.env.TEST_USER_EMAIL,
        password: process.env.TEST_USER_PASSWORD,
      },
    });

    const body = await loginResponse.json();
    authToken = body.token;
  });

  test('should fetch user profile', async () => {
    const response = await request.get('/api/users/me', {
      headers: {
        Authorization: `Bearer ${authToken}`,
      },
    });

    expect(response.ok()).toBeTruthy();

    const user = await response.json();
    expect(user).toHaveProperty('id');
    expect(user).toHaveProperty('email');
  });

  test('should fetch products list', async () => {
    const response = await request.get('/api/products', {
      headers: {
        Authorization: `Bearer ${authToken}`,
      },
    });

    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    expect(body).toHaveProperty('products');
    expect(Array.isArray(body.products)).toBeTruthy();
  });

  test('should create order', async () => {
    const response = await request.post('/api/orders', {
      headers: {
        Authorization: `Bearer ${authToken}`,
        'Content-Type': 'application/json',
      },
      data: {
        items: [
          { productId: 'prod_123', quantity: 1 },
        ],
      },
    });

    expect(response.status()).toBe(201);

    const order = await response.json();
    expect(order).toHaveProperty('id');
    expect(order.status).toBe('pending');
  });

  test('should handle payment processing', async () => {
    // 先创建订单
    const orderResponse = await request.post('/api/orders', {
      headers: {
        Authorization: `Bearer ${authToken}`,
      },
      data: {
        items: [{ productId: 'prod_123', quantity: 1 }],
      },
    });

    const { id: orderId } = await orderResponse.json();

    // 处理支付
    const paymentResponse = await request.post(`/api/orders/${orderId}/pay`, {
      headers: {
        Authorization: `Bearer ${authToken}`,
      },
      data: {
        paymentMethod: 'card',
        paymentToken: 'tok_visa',
      },
    });

    expect(paymentResponse.ok()).toBeTruthy();

    const payment = await paymentResponse.json();
    expect(payment.status).toBe('completed');
  });
});
```

### 关键业务流程测试

```typescript
// src/tests/production/critical-flows.spec.ts
import { test, expect, request } from '@playwright/test';

test.describe('Production Critical User Flows', () => {
  let authToken: string;
  let testUserId: string;

  test.beforeAll(async () => {
    const loginResponse = await request.post('/api/auth/login', {
      data: {
        email: process.env.TEST_USER_EMAIL,
        password: process.env.TEST_USER_PASSWORD,
      },
    });

    const body = await loginResponse.json();
    authToken = body.token;
    testUserId = body.user.id;
  });

  test('complete purchase flow', async () => {
    // 1. 浏览产品
    const productsResponse = await request.get('/api/products', {
      headers: { Authorization: `Bearer ${authToken}` },
    });
    expect(productsResponse.ok()).toBeTruthy();

    const { products } = await productsResponse.json();
    const productId = products[0].id;

    // 2. 添加到购物车
    const cartResponse = await request.post('/api/cart/items', {
      headers: { Authorization: `Bearer ${authToken}` },
      data: { productId, quantity: 1 },
    });
    expect(cartResponse.status()).toBe(201);

    // 3. 创建订单
    const orderResponse = await request.post('/api/orders', {
      headers: { Authorization: `Bearer ${authToken}` },
      data: {
        items: [{ productId, quantity: 1 }],
        shippingAddress: {
          street: '123 Main St',
          city: 'Test City',
          zip: '12345',
        },
      },
    });
    expect(orderResponse.status()).toBe(201);

    const { id: orderId } = await orderResponse.json();

    // 4. 完成支付
    const paymentResponse = await request.post(`/api/orders/${orderId}/pay`, {
      headers: { Authorization: `Bearer ${authToken}` },
      data: { paymentMethod: 'card' },
    });
    expect(paymentResponse.status()).toBe(200);

    // 5. 验证订单状态
    const finalOrderResponse = await request.get(`/api/orders/${orderId}`, {
      headers: { Authorization: `Bearer ${authToken}` },
    });
    const finalOrder = await finalOrderResponse.json();
    expect(finalOrder.status).toBe('paid');
  });

  test('user profile update flow', async () => {
    // 1. 获取当前资料
    const profileResponse = await request.get('/api/users/me', {
      headers: { Authorization: `Bearer ${authToken}` },
    });
    expect(profileResponse.ok()).toBeTruthy();

    const originalProfile = await profileResponse.json();

    // 2. 更新资料
    const updateResponse = await request.patch('/api/users/me', {
      headers: { Authorization: `Bearer ${authToken}` },
      data: {
        name: `Updated Name ${Date.now()}`,
      },
    });
    expect(updateResponse.ok()).toBeTruthy();

    // 3. 验证更新
    const updatedProfile = await updateResponse.json();
    expect(updatedProfile.name).not.toBe(originalProfile.name);

    // 4. 恢复原始资料
    await request.patch('/api/users/me', {
      headers: { Authorization: `Bearer ${authToken}` },
      data: { name: originalProfile.name },
    });
  });
});
```

## 报告和告警

```typescript
// src/utils/reporter.ts
interface SmokeTestReport {
  timestamp: string;
  environment: string;
  totalTests: number;
  passedTests: number;
  failedTests: number;
  duration: number;
  results: TestResult[];
}

interface TestResult {
  name: string;
  status: 'passed' | 'failed';
  duration: number;
  error?: string;
}

export async function generateReport(results: TestResult[]): Promise<SmokeTestReport> {
  const passed = results.filter(r => r.status === 'passed').length;
  const failed = results.filter(r => r.status === 'failed').length;

  return {
    timestamp: new Date().toISOString(),
    environment: process.env.PRODUCTION_URL || 'unknown',
    totalTests: results.length,
    passedTests: passed,
    failedTests: failed,
    duration: results.reduce((sum, r) => sum + r.duration, 0),
    results,
  };
}

export async function sendAlert(report: SmokeTestReport): Promise<void> {
  if (report.failedTests > 0) {
    // 发送告警到 Slack/PagerDuty
    console.log(`ALERT: ${report.failedTests}/${report.totalTests} smoke tests failed!`);

    // 发送到 Slack
    await fetch(process.env.SLACK_WEBHOOK_URL!, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: `Production Smoke Tests Failed`,
        attachments: [{
          color: 'danger',
          fields: [
            { title: 'Failed', value: `${report.failedTests}`, short: true },
            { title: 'Passed', value: `${report.passedTests}`, short: true },
            { title: 'Duration', value: `${report.duration}ms`, short: true },
          ],
        }],
      }),
    });
  }
}
```

## CI/CD 集成

```yaml
name: Production Smoke Tests
on:
  schedule:
    - cron: '*/15 * * * *'  # 每 15 分钟运行一次
  workflow_dispatch:  # 也允许手动触发

jobs:
  smoke-tests:
    runs-on: ubuntu-latest
    environment: production  # 要求在 GitHub 配置环境 secrets
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright browsers
        run: npx playwright install --with-deps chromium

      - name: Run Smoke Tests
        env:
          PRODUCTION_URL: ${{ vars.PRODUCTION_URL }}
          TEST_USER_EMAIL: ${{ secrets.TEST_USER_EMAIL }}
          TEST_USER_PASSWORD: ${{ secrets.TEST_USER_PASSWORD }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
        run: npm run smoke-tests

      - name: Upload report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: smoke-test-report
          path: smoke-reports/

      - name: Notify on failure
        if: failure()
        run: |
          echo "Production smoke tests failed!"
          curl -X POST -H 'Content-type: application/json' \
            --data "{\"text\":\"Production Smoke Tests Failed\"}" \
            ${{ secrets.SLACK_WEBHOOK_URL }}
```

## 最佳实践

1. **只测试关键路径** -- 选择最重要且快速的测试。
2. **使用真实但隔离的数据** -- 避免影响真实用户数据。
3. **设置合理的超时** -- 生产环境可能有延迟。
4. **监控执行时间** -- 跟踪性能趋势。
5. **即时告警** -- 失败时立即通知。
6. **定期审查测试套件** -- 确保测试覆盖正确。
7. **记录测试结果** -- 用于趋势分析。
8. **最小化测试数量** -- 保持快速执行。

## 应避免的反模式

1. **测试太多用例** -- 冒烟测试应该快速。
2. **使用生产真实数据** -- 可能造成数据泄露。
3. **忽略失败重试** -- 确认是否是暂时性问题。
4. **过长的测试** -- 保持测试简短。
5. **不监控执行** -- 不知道测试是否在运行。
6. **忽略负面测试** -- 也要验证错误处理。
7. **不清理测试数据** -- 可能污染生产数据库。
8. **过频执行** -- 可能影响生产性能。