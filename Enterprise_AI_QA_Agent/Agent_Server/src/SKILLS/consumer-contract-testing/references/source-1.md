---
name: Contract Test Generator
description: 使用 Pact 进行消费者驱动的契约测试，支持 HTTP、WebSocket 等协议
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [contract, integration]
frameworks: [pact]
languages: [typescript, javascript, java]
info: vip.hctestedu.com
domains: [api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 契约测试生成器

您是一位专注于契约测试的 QA 工程师。当用户要求您编写、审查或调试契约测试时，请遵循这些详细说明。

## 核心原则

1. **消费者驱动** -- 契约由消费者定义，而非服务提供方。
2. **独立验证** -- 每个服务独立验证其契约。
3. **快速反馈** -- 契约测试应该快速运行，提供即时反馈。
4. **版本控制** -- 契约变更需要版本控制和平滑迁移。
5. **CI/CD 集成** -- 在每次更改时自动运行契约测试。

## 什么是契约测试

契约测试是一种集成测试方法，确保服务之间的 API 通信符合预期。它解决了以下问题：
- 微服务之间的集成测试困难
- 服务团队之间的协调成本高
- 回归问题难以早期发现

## 项目结构

```
contract-tests/
├── consumer/
│   ├── tests/
│   │   └── user-service.pact.spec.ts
│   ├── interactions/
│   └── pact/
├── provider/
│   ├── tests/
│   │   └── user-service.pact.spec.ts
│   └── src/
├── contracts/
│   └── user-service.json
├── pact-broker/
│   └── docker-compose.yml
└── package.json
```

## 消费者端测试

### 安装

```bash
npm install --save-dev @pact-foundation/pact @pact-foundation/pact-core
```

### 编写消费者契约测试

```typescript
// consumer/tests/user-service.pact.spec.ts
import { PactV3, PactV3Options } from '@pact-foundation/pact';
import { describe, it, expect } from 'vitest';

const config: PactV3Options = {
  dir: './pacts',
  consumer: 'web-frontend',
  provider: 'user-service',
  logLevel: 'info',
};

const pact = new PactV3(config);

describe('User Service Consumer', () => {
  describe('GET /users/:id', () => {
    it('should return user details', async () => {
      await pact.addInteraction({
        states: [{ description: 'user with ID 123 exists' }],
        uponReceiving: 'a request for user 123',
        withRequest: {
          method: 'GET',
          path: '/users/123',
          headers: { Accept: 'application/json' },
        },
        willRespondWith: {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
          body: {
            id: 123,
            name: 'Test User',
            email: 'test@example.com',
          },
        },
      });

      const response = await fetch('http://localhost:8080/users/123', {
        headers: { Accept: 'application/json' },
      });

      expect(response.status).toBe(200);
      const user = await response.json();
      expect(user).toMatchObject({
        id: 123,
        name: 'Test User',
        email: 'test@example.com',
      });
    });
  });

  describe('POST /users', () => {
    it('should create a new user', async () => {
      await pact.addInteraction({
        states: [{ description: 'no user with email test@example.com exists' }],
        uponReceiving: 'a request to create a user',
        withRequest: {
          method: 'POST',
          path: '/users',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'application/json',
          },
          body: {
            name: 'New User',
            email: 'new@example.com',
            password: 'SecurePass123!',
          },
        },
        willRespondWith: {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
          body: {
            id: 124,
            name: 'New User',
            email: 'new@example.com',
          },
        },
      });

      const response = await fetch('http://localhost:8080/users', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          name: 'New User',
          email: 'new@example.com',
          password: 'SecurePass123!',
        }),
      });

      expect(response.status).toBe(201);
      const user = await response.json();
      expect(user).toMatchObject({
        id: 124,
        name: 'New User',
        email: 'new@example.com',
      });
    });
  });
});
```

## 提供商端测试

### Java (JVM)

```java
// provider/src/test/java/com/example/UserServiceProviderTest.java
package com.example;

import au.com.dius.pact.provider.junit5.HttpTestTarget;
import au.com.dius.pact.provider.junit5.PactVerificationContext;
import au.com.dius.pact.provider.junit5.PactVerificationInvocationProvider;
import au.com.dius.pact.provider.junitsupport.Consumer;
import au.com.dius.pact.provider.junitsupport.Provider;
import au.com.dius.pact.provider.junitsupport.loader.PactBroker;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.TestTemplate;
import org.junit.jupiter.api.extension.ExtendWith;

import static io.restassured.RestAssured.given;

@Provider("user-service")
@Consumer("web-frontend")
@PactBroker(host = "pact-broker", port = 8080)
public class UserServiceProviderTest {

    @BeforeEach
    void setup(PactVerificationContext context) {
        context.setTarget(new HttpTestTarget("localhost", 8080));
    }

    @TestTemplate
    @ExtendWith(PactVerificationInvocationProvider.class)
    void verifyPact(PactVerificationContext context) {
        context.verifyInteraction();
    }

    @State("user with ID 123 exists")
    void userWithId123Exists() {
        // 设置测试数据
        userRepository.save(User.builder()
            .id(123L)
            .name("Test User")
            .email("test@example.com")
            .build());
    }

    @State("no user with email test@example.com exists")
    void noUserWithEmailExists() {
        userRepository.deleteByEmail("test@example.com");
    }
}
```

### Node.js (Provider)

```typescript
// provider/tests/user-service.pact.spec.ts
import { Verifier } from '@pact-foundation/pact';
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { app } from '../../src/app';
import { startServer, stopServer } from '../../src/server';

describe('User Service Provider', () => {
  let server;

  beforeAll(async () => {
    server = await startServer(8080);
  });

  afterAll(async () => {
    await stopServer(server);
  });

  it('should validate the consumer contract', async () => {
    const verifier = new Verifier({
      provider: 'user-service',
      providerBaseUrl: 'http://localhost:8080',
      pactBrokerUrl: 'http://pact-broker:8080',
      publishVerificationResult: process.env.CI === 'true',
      providerVersion: '1.0.0',
      stateHandlers: {
        'user with ID 123 exists': async () => {
          await userRepository.create({
            id: 123,
            name: 'Test User',
            email: 'test@example.com'
          });
        },
        'no user with email test@example.com exists': async () => {
          await userRepository.deleteByEmail('test@example.com');
        }
      }
    });

    const result = await verifier.verifyProvider();
    expect(result).toBe(true);
  });
});
```

## WebSocket 契约测试

```typescript
describe('Real-time Notification Service', () => {
  it('should send notification to subscribed user', async () => {
    await pact.addInteraction({
      states: [{ description: 'user 123 is subscribed to notifications' }],
      uponReceiving: 'a notification for user 123',
      withRequest: {
        method: 'GET',
        path: '/ws/notifications',
        headers: {
          Authorization: 'Bearer token123',
        },
      },
      // WebSocket 是一个双向协议，这里定义的是初始连接和订阅
      willRespondWith: {
        status: 200,
        body: {
          type: 'SUBSCRIBED',
          channel: 'notifications:123',
        },
      },
    });

    // 在实际测试中，你可能需要使用 WebSocket 客户端
    const ws = new WebSocket('ws://localhost:8080/ws/notifications', {
      headers: { Authorization: 'Bearer token123' }
    });

    // 验证 WebSocket 连接
    await new Promise<void>((resolve, reject) => {
      ws.on('message', (data) => {
        const message = JSON.parse(data.toString());
        if (message.type === 'SUBSCRIBED') {
          ws.close();
          resolve();
        }
      });
      ws.on('error', reject);
    });
  });
});
```

## Pact Broker 集成

### 发布契约

```yaml
# GitHub Actions - 发布契约
name: Publish Consumer Contract
on:
  push:
    branches: [main]

jobs:
  publish-contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Run consumer tests
        run: npm run test:consumer

      - name: Publish to Pact Broker
        run: |
          npx pact-broker publish ./pacts \
            --consumer-app-version=${{ github.sha }} \
            --branch=${{ github.ref_name }} \
            --broker-base-url=${{ secrets.PACT_BROKER_URL }} \
            --broker-token=${{ secrets.PACT_BROKER_TOKEN }}
```

### 验证提供商契约

```yaml
name: Verify Provider Contract
on:
  push:
    branches: [main]

jobs:
  verify-contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Verify against Pact Broker
        run: |
          npx pact-provider-verifier \
            --provider-base-url=http://localhost:8080 \
            --pact-broker-url=${{ secrets.PACT_BROKER_URL }} \
            --pact-broker-token=${{ secrets.PACT_BROKER_TOKEN }} \
            --provider-app-version=${{ github.sha }} \
            --publish-verification-results
```

## 运行时提供商验证

```typescript
// 启用 can-i-deploy 检查
import { canDeploy } from '@pact-foundation/pact';

async function checkCanDeploy() {
  const result = await canDeploy({
    providerAppVersion: '1.0.0',
    pactBrokerUrl: 'http://pact-broker:8080',
    pactBrokerToken: process.env.PACT_BROKER_TOKEN,
    provider: 'user-service',
    consumer: 'web-frontend',
  });

  if (!result) {
    throw new Error('Cannot deploy - contract not satisfied');
  }
}
```

## 最佳实践

1. **消费者驱动** -- 让消费者定义他们需要的 API 契约。
2. **小而专注的契约** -- 避免创建过大的契约。
3. **明确的字段匹配** -- 使用正则表达式或类型匹配。
4. **版本控制契约** -- 契约变更需要版本控制。
5. **使用 Pact Broker** -- 集中管理契约和版本。
6. **自动化 can-i-deploy** -- 在部署前检查契约兼容性。
7. **状态处理** -- 提供商需要正确处理测试状态。
8. **文档化契约** -- 使用 Pact Broker 的 API 文档功能。

## 应避免的反模式

1. **在消费者测试中使用真实提供商** -- 违反独立测试原则。
2. **过度指定字段** -- 允许可选字段的灵活性。
3. **忽略版本控制** -- 契约变更可能导致破坏性兼容。
4. **不使用 Broker** -- 没有中央存储难以协调。
5. **跳过状态处理** -- 缺少状态会导致测试不稳定。
6. **测试实现细节** -- 关注行为而非实现。
7. **创建过大的契约** -- 小而专注的契约更易维护。
8. **不验证提供商** -- 消费者测试不能替代提供商验证。