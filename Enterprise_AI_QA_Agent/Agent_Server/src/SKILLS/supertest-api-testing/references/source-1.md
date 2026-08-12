---
name: SuperTest API Testing
description: Node.js API 测试的 HTTP 断言库，支持 Express、Koa 和 Fastify
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [api, integration]
info: vip.hctestedu.com
frameworks: [jest]
languages: [typescript, javascript]
domains: [api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# SuperTest API 测试

这个技能使 AI 代理使用 SuperTest 为 Express/Koa/Fastify 兼容的 Node HTTP 应用编写集成测试：将 app 对象直接传递给 `request()`，这样不会绑定端口，链式调用 `.expect()` 进行状态/头检查，并使用 Jest 匹配器断言响应体。当 Node 项目暴露 Express `app`、用户要求测试 REST 端点而不启动服务器、或 `supertest` 已在 `devDependencies` 中时，触发此技能。

## 核心原则

1. **测试 app 对象，而不是运行的服务器。** `request(app)` 为每个请求绑定到临时端口并拆除——无需 `app.listen()`，无端口冲突，CI 中无孤立服务器。
2. **将 `app` 与监听器分开导出。** 最重要的使能因素：`app.ts` 导出 Express app，`server.ts` 调用 `listen()`。测试只导入 `app.ts`。
3. **始终 `await`（或返回）请求链。** SuperTest 调用是一个 thenable；忘记 `await` 意味着测试在请求发出之前就通过了。
4. **`.expect(status)` 用于传输层，Jest 匹配器用于负载。** 状态码和 content-type 属于链式调用；响应体形状属于 `expect(res.body).toMatchObject(...)`，这样失败差异可读。
5. **真实数据库或无——不要半模拟。** 要么对临时数据库运行集成测试（Testcontainers、SQLite 内存中），要么完全模拟数据层。模拟五个查询中的两个会产生欺骗性的测试。
6. **每个测试拥有自己的数据。** 在测试内部（或 `beforeEach` 中）创建测试所需的记录，并使清理具有幂等性。顺序依赖的测试套件在一个 sprint 内就会腐坏。

## 设置

```bash
npm install --save-dev supertest @types/supertest jest ts-jest @types/jest
```

使一切可测试的 app/server 分离：

```typescript
// src/app.ts
import express from 'express';
import { usersRouter } from './routes/users';

export function createApp(): express.Express {
  const app = express();
  app.use(express.json());
  app.use('/api/users', usersRouter);
  app.get('/health', (_req, res) => res.json({ status: 'ok' }));
  return app;
}
```

```typescript
// src/server.ts — 唯一监听的文件；测试从不导入
import { createApp } from './app';

const port = Number(process.env.PORT ?? 3000);
createApp().listen(port, () => console.log(`listening on :${port}`));
```

第一个测试：

```typescript
// src/app.test.ts
import request from 'supertest';
import { createApp } from './app';

const app = createApp();

describe('GET /health', () => {
  it('响应 200 并返回 status ok', async () => {
    const res = await request(app)
      .get('/health')
      .expect('Content-Type', /json/)
      .expect(200);

    expect(res.body).toEqual({ status: 'ok' });
  });
});
```

## 模式

### 带 body 断言的 CRUD 往返

```typescript
import request from 'supertest';
import { createApp } from './app';
import { resetDb } from '../test/helpers/db';

const app = createApp();

beforeEach(async () => {
  await resetDb();
});

describe('POST /api/users', () => {
  it('创建用户并返回 201 及持久化的记录', async () => {
    const res = await request(app)
      .post('/api/users')
      .send({ email: 'mira@example.com', name: 'Mira' })
      .expect(201);

    expect(res.body).toMatchObject({
      email: 'mira@example.com',
      name: 'Mira',
    });
    expect(res.body.id).toEqual(expect.any(String));

    // 往返：创建的资源可以被检索
    const fetched = await request(app).get(`/api/users/${res.body.id}`).expect(200);
    expect(fetched.body.email).toBe('mira@example.com');
  });

  it('用 400 和字段级错误拒绝无效邮箱', async () => {
    const res = await request(app)
      .post('/api/users')
      .send({ email: 'not-an-email', name: 'Mira' })
      .expect(400);

    expect(res.body.errors).toContainEqual(
      expect.objectContaining({ field: 'email' }),
    );
  });
});
```

### 认证请求

```typescript
// test/helpers/auth.ts — 每个套件登录一次，重用 token
import request from 'supertest';
import type { Express } from 'express';

export async function getAuthToken(app: Express): Promise<string> {
  const res = await request(app)
    .post('/api/auth/login')
    .send({ email: 'admin@example.com', password: 'test-password-123' })
    .expect(200);
  return res.body.token as string;
}
```

```typescript
import request from 'supertest';
import { createApp } from './app';
import { getAuthToken } from '../test/helpers/auth';

const app = createApp();
let token: string;

beforeAll(async () => {
  token = await getAuthToken(app);
});

describe('DELETE /api/users/:id', () => {
  it('无 token 返回 401', async () => {
    await request(app).delete('/api/users/u_123').expect(401);
  });

  it('使用有效 bearer token 删除', async () => {
    await request(app)
      .delete('/api/users/u_123')
      .set('Authorization', `Bearer ${token}`)
      .expect(204);
  });
});
```

### 查询参数、文件上传和自定义断言

```typescript
// 通过 .query() 传递查询字符串——绝不手动拼接
const res = await request(app)
  .get('/api/users')
  .query({ page: 2, limit: 10, sort: 'createdAt' })
  .expect(200);
expect(res.body.items).toHaveLength(10);
expect(res.body.page).toBe(2);

// 多部分上传
await request(app)
  .post('/api/avatars')
  .set('Authorization', `Bearer ${token}`)
  .attach('avatar', 'test/fixtures/avatar.png')
  .field('alt', 'profile picture')
  .expect(201);

// .expect() 的函数形式用于响应级不变量
await request(app)
  .get('/api/users')
  .expect(200)
  .expect((response) => {
    if (response.body.items.some((u: { password?: string }) => u.password)) {
      throw new Error('password leaked in list endpoint');
    }
  });
```

### Cookie 和会话

```typescript
// 使用 agent 在请求之间持久化 cookie
const agent = request.agent(app);

await agent
  .post('/api/auth/login')
  .send({ email: 'admin@example.com', password: 'test-password-123' })
  .expect(200);

// agent 自动携带会话 cookie
await agent.get('/api/me').expect(200);
```

## 最佳实践

- 按行为和状态命名测试：`'returns 409 when email already exists'`，而不是 `'test create user 2'`。
- 覆盖框架不会处理的负面路径：格式错误的 JSON body、缺失认证、错误的 content-type、超大负载、无效格式的 ID（404 vs 400 用于无效格式）。
- 对共享 DB 串行运行集成测试（`jest --runInBand`）或为每个 worker 提供自己的 schema；一个可变 DB 上的并行 worker 产生海森堡 bug。
- 保持 `Content-Type` 断言为正则表达式（`/json/`）——服务器会附加 `; charset=utf-8`。
- 添加一个 `jest.setup.ts`，在未处理的承诺拒绝时使测试失败；否则 SuperTest 链会悄悄吞没它们。
- 对于 Fastify，在将 `app.server` 传递给 `request()` 之前调用 `await app.ready()`。

## 反模式

- **`app.listen()` 在测试设置中。** 跨 Jest worker 的端口冲突，失败时的孤立服务器。`request(app)` 存在的原因正是让你永远不需要 listen。
- **忘记链式调用上的 `await`。** 测试在请求进行中时退出绿色。启用 `@typescript-eslint/no-floating-promises` 使其成为 lint 错误。
- **使用 `toEqual` 断言包含时间戳和 ID 的整个 body。** 对生成字段使用 `toMatchObject` 加上 `expect.any(String)`；每次 schema 添加都会破坏全 body 相等。
- **一个 mega-test 练习登录、创建、更新和删除。** 当它在第 14 步失败时你调试所有 14 步。按行为拆分，通过 helpers 共享设置。
- **通过原始 SQL 播种同时通过 HTTP 测试。** 你的种子绕过验证和哈希；通过 API 或与应用使用相同的 repository 层创建测试数据。
- **测试第三方中间件**（body-parser 限制、cors echo）——如必须，在一个测试中固定你的配置，但不要重新测试 Express 本身。

## 何时触发此技能

- `supertest` 在 `devDependencies` 中，或用户要求测试 Express/Koa/NestJS HTTP 端点。
- API 没有集成测试，用户希望在不使用部署或绑定端口的情况下获得覆盖。
- 审查使用 `app.listen`、缺失 `await` 或共享可变测试数据的失败或 flaky API 测试。
- 用户询问如何在 Node 中测试受 auth 保护的路由、文件上传或 cookie 会话。
- 设置 app/server 分离，使现有 Express 代码库可测试。
