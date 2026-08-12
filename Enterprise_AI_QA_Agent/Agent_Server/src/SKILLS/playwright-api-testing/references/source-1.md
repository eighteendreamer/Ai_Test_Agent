---
name: Playwright API Testing
description: 使用 Playwright APIRequestContext 对 REST 和 GraphQL 端点进行 API 测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [api]
frameworks: [playwright]
info: vip.hctestedu.com
languages: [typescript]
domains: [api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Playwright API 测试技能

你是一位专注于使用 Playwright 内置 `APIRequestContext` 进行 API 测试的 QA 自动化专家。当用户要求你编写、审查或调试 Playwright API 测试时,请遵循以下详细说明。

## 核心原则

1. **Playwright 原生 API 测试** -- 使用 `APIRequestContext` 而不是外部 HTTP 库。
2. **类型安全** -- 为所有请求/响应载荷定义接口。
3. **隔离** -- 每个测试管理自己的数据生命周期(创建、验证、清理)。
4. **全面验证** -- 检查状态码、头、响应体结构和时序。
5. **可重用抽象** -- 为每个服务域构建 API 客户端类。

## 项目结构

```
tests/
  api/
    auth/
      auth-api.spec.ts
    users/
      users-api.spec.ts
      users-crud.spec.ts
    products/
      products-api.spec.ts
  fixtures/
    api.fixture.ts
    auth-api.fixture.ts
  models/
    user.model.ts
    product.model.ts
    api-response.model.ts
  clients/
    base-api-client.ts
    users-api-client.ts
    products-api-client.ts
  utils/
    api-helpers.ts
    schema-validator.ts
playwright.config.ts
```

## 配置

```typescript
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/api',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ['html'],
    ['json', { outputFile: 'test-results/api-results.json' }],
  ],
  use: {
    baseURL: process.env.API_BASE_URL || 'http://localhost:3000/api',
    extraHTTPHeaders: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
    },
  },
});
```

## 响应模型

为所有 API 载荷定义 TypeScript 接口:

```typescript
// models/user.model.ts
export interface User {
  id: string;
  email: string;
  name: string;
  role: 'admin' | 'user' | 'viewer';
  createdAt: string;
  updatedAt: string;
}

export interface CreateUserRequest {
  email: string;
  name: string;
  password: string;
  role?: 'admin' | 'user' | 'viewer';
}

export interface UpdateUserRequest {
  name?: string;
  role?: 'admin' | 'user' | 'viewer';
}

export interface UserListResponse {
  data: User[];
  total: number;
  page: number;
  pageSize: number;
}

export interface ApiError {
  statusCode: number;
  message: string;
  error: string;
  details?: Record<string, string[]>;
}
```

## 基础 API 客户端

```typescript
// clients/base-api-client.ts
import { APIRequestContext, APIResponse } from '@playwright/test';

export class BaseApiClient {
  protected readonly request: APIRequestContext;
  protected readonly basePath: string;

  constructor(request: APIRequestContext, basePath: string) {
    this.request = request;
    this.basePath = basePath;
  }

  protected async get(path: string, params?: Record<string, string>): Promise<APIResponse> {
    const url = params
      ? `${this.basePath}${path}?${new URLSearchParams(params)}`
      : `${this.basePath}${path}`;
    return this.request.get(url);
  }

  protected async post(path: string, data: unknown): Promise<APIResponse> {
    return this.request.post(`${this.basePath}${path}`, { data });
  }

  protected async put(path: string, data: unknown): Promise<APIResponse> {
    return this.request.put(`${this.basePath}${path}`, { data });
  }

  protected async patch(path: string, data: unknown): Promise<APIResponse> {
    return this.request.patch(`${this.basePath}${path}`, { data });
  }

  protected async delete(path: string): Promise<APIResponse> {
    return this.request.delete(`${this.basePath}${path}`);
  }
}
```

### 特定领域的 API 客户端

```typescript
// clients/users-api-client.ts
import { APIRequestContext, APIResponse } from '@playwright/test';
import { BaseApiClient } from './base-api-client';
import { CreateUserRequest, UpdateUserRequest } from '../models/user.model';

export class UsersApiClient extends BaseApiClient {
  constructor(request: APIRequestContext) {
    super(request, '/users');
  }

  async list(page = 1, pageSize = 10): Promise<APIResponse> {
    return this.get('', { page: String(page), pageSize: String(pageSize) });
  }

  async getById(id: string): Promise<APIResponse> {
    return this.get(`/${id}`);
  }

  async create(user: CreateUserRequest): Promise<APIResponse> {
    return this.post('', user);
  }

  async update(id: string, data: UpdateUserRequest): Promise<APIResponse> {
    return this.patch(`/${id}`, data);
  }

  async remove(id: string): Promise<APIResponse> {
    return this.delete(`/${id}`);
  }

  async search(query: string): Promise<APIResponse> {
    return this.get('/search', { q: query });
  }
}
```

## 自定义 Fixtures

```typescript
// fixtures/api.fixture.ts
import { test as base } from '@playwright/test';
import { UsersApiClient } from '../clients/users-api-client';
import { ProductsApiClient } from '../clients/products-api-client';

type ApiFixtures = {
  usersApi: UsersApiClient;
  productsApi: ProductsApiClient;
  authToken: string;
};

export const test = base.extend<ApiFixtures>({
  usersApi: async ({ request }, use) => {
    await use(new UsersApiClient(request));
  },

  productsApi: async ({ request }, use) => {
    await use(new ProductsApiClient(request));
  },

  authToken: async ({ request }, use) => {
    const response = await request.post('/auth/login', {
      data: {
        email: 'admin@example.com',
        password: 'AdminPass123!',
      },
    });
    const body = await response.json();
    await use(body.token);
  },
});

export { expect } from '@playwright/test';
```

## 编写 API 测试

### CRUD 操作

```typescript
import { test, expect } from '../fixtures/api.fixture';
import { CreateUserRequest, User } from '../models/user.model';

test.describe('Users API - CRUD', () => {
  let createdUserId: string;

  const newUser: CreateUserRequest = {
    email: `test-${Date.now()}@example.com`,
    name: 'Test User',
    password: 'SecurePass123!',
    role: 'user',
  };

  test('POST /users - should create a new user', async ({ usersApi }) => {
    const response = await usersApi.create(newUser);

    expect(response.status()).toBe(201);

    const body: User = await response.json();
    expect(body.id).toBeTruthy();
    expect(body.email).toBe(newUser.email);
    expect(body.name).toBe(newUser.name);
    expect(body.role).toBe('user');
    expect(body.createdAt).toBeTruthy();

    createdUserId = body.id;
  });

  test('GET /users/:id - should retrieve the user', async ({ usersApi }) => {
    // 首先创建用户
    const createResponse = await usersApi.create({
      ...newUser,
      email: `get-test-${Date.now()}@example.com`,
    });
    const created: User = await createResponse.json();

    const response = await usersApi.getById(created.id);
    expect(response.status()).toBe(200);

    const body: User = await response.json();
    expect(body.id).toBe(created.id);
    expect(body.email).toBe(created.email);
  });

  test('PATCH /users/:id - should update the user', async ({ usersApi }) => {
    const createResponse = await usersApi.create({
      ...newUser,
      email: `update-test-${Date.now()}@example.com`,
    });
    const created: User = await createResponse.json();

    const response = await usersApi.update(created.id, { name: 'Updated Name' });
    expect(response.status()).toBe(200);

    const body: User = await response.json();
    expect(body.name).toBe('Updated Name');
  });

  test('DELETE /users/:id - should delete the user', async ({ usersApi }) => {
    const createResponse = await usersApi.create({
      ...newUser,
      email: `delete-test-${Date.now()}@example.com`,
    });
    const created: User = await createResponse.json();

    const deleteResponse = await usersApi.remove(created.id);
    expect(deleteResponse.status()).toBe(204);

    const getResponse = await usersApi.getById(created.id);
    expect(getResponse.status()).toBe(404);
  });
});
```

### 认证测试

```typescript
import { test, expect } from '@playwright/test';

test.describe('Authentication API', () => {
  test('should login with valid credentials', async ({ request }) => {
    const response = await request.post('/auth/login', {
      data: {
        email: 'admin@example.com',
        password: 'AdminPass123!',
      },
    });

    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.token).toBeTruthy();
    expect(body.expiresIn).toBeGreaterThan(0);
    expect(body.user.email).toBe('admin@example.com');
  });

  test('should reject invalid credentials', async ({ request }) => {
    const response = await request.post('/auth/login', {
      data: {
        email: 'admin@example.com',
        password: 'wrongpassword',
      },
    });

    expect(response.status()).toBe(401);
    const body = await response.json();
    expect(body.message).toBe('Invalid credentials');
  });

  test('should access protected endpoint with token', async ({ request }) => {
    // 首先登录
    const loginResponse = await request.post('/auth/login', {
      data: {
        email: 'admin@example.com',
        password: 'AdminPass123!',
      },
    });
    const { token } = await loginResponse.json();

    // 使用 token
    const response = await request.get('/users/me', {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });

    expect(response.status()).toBe(200);
    const user = await response.json();
    expect(user.email).toBe('admin@example.com');
  });

  test('should reject expired or invalid token', async ({ request }) => {
    const response = await request.get('/users/me', {
      headers: {
        Authorization: 'Bearer invalid.token.here',
      },
    });

    expect(response.status()).toBe(401);
  });
});
```

### 错误处理和验证

```typescript
test.describe('Users API - Validation', () => {
  test('should return 400 for missing required fields', async ({ request }) => {
    const response = await request.post('/users', {
      data: { name: 'No Email User' },
    });

    expect(response.status()).toBe(400);
    const body = await response.json();
    expect(body.details).toHaveProperty('email');
  });

  test('should return 400 for invalid email format', async ({ request }) => {
    const response = await request.post('/users', {
      data: {
        email: 'not-an-email',
        name: 'Bad Email User',
        password: 'SecurePass123!',
      },
    });

    expect(response.status()).toBe(400);
    const body = await response.json();
    expect(body.details.email).toContain('must be a valid email');
  });

  test('should return 409 for duplicate email', async ({ usersApi }) => {
    const email = `duplicate-${Date.now()}@example.com`;
    const userData = { email, name: 'First', password: 'Pass123!' };

    await usersApi.create(userData);
    const response = await usersApi.create(userData);

    expect(response.status()).toBe(409);
  });

  test('should return 404 for non-existent resource', async ({ usersApi }) => {
    const response = await usersApi.getById('non-existent-id');
    expect(response.status()).toBe(404);
  });
});
```

### 分页和过滤

```typescript
test.describe('Users API - Pagination', () => {
  test('should return paginated results', async ({ usersApi }) => {
    const response = await usersApi.list(1, 5);

    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.data.length).toBeLessThanOrEqual(5);
    expect(body.page).toBe(1);
    expect(body.pageSize).toBe(5);
    expect(body.total).toBeGreaterThanOrEqual(0);
  });

  test('should return correct page', async ({ usersApi }) => {
    const page1 = await (await usersApi.list(1, 2)).json();
    const page2 = await (await usersApi.list(2, 2)).json();

    const page1Ids = page1.data.map((u: { id: string }) => u.id);
    const page2Ids = page2.data.map((u: { id: string }) => u.id);
    const overlap = page1Ids.filter((id: string) => page2Ids.includes(id));
    expect(overlap).toHaveLength(0);
  });
});
```

### 响应头验证

```typescript
test('should return correct response headers', async ({ request }) => {
  const response = await request.get('/users');

  expect(response.headers()['content-type']).toContain('application/json');
  expect(response.headers()['x-request-id']).toBeTruthy();
  expect(response.headers()['cache-control']).toBeDefined();

  // 安全头
  expect(response.headers()['x-content-type-options']).toBe('nosniff');
  expect(response.headers()['x-frame-options']).toBe('DENY');
});
```

### 响应时间断言

```typescript
test('should respond within acceptable time', async ({ request }) => {
  const start = Date.now();
  const response = await request.get('/health');
  const duration = Date.now() - start;

  expect(response.status()).toBe(200);
  expect(duration).toBeLessThan(500); // 500ms 阈值
});
```

## 最佳实践

1. **使用唯一测试数据** -- 在邮件和名称中包含时间戳或 UUID 以避免冲突。
2. **测试后清理** -- 删除你创建的资源以保持测试环境干净。
3. **验证响应模式** -- 不仅检查值,还要检查响应的形状。
4. **测试快乐和悲伤路径** -- 始终测试错误情况和边界情况。
5. **使用环境变量** -- 永远不要硬编码 URL 或凭证。
6. **逻辑组织测试** -- 按资源或功能组织,而不是按 HTTP 方法。
7. **使用 fixtures 进行认证** -- 避免在每个测试中重复登录逻辑。
8. **检查响应时间** -- API 性能是正确性的一部分。
9. **测试幂等性** -- 验证重复的相同请求产生一致的结果。
10. **对你的 API 测试进行版本控制** -- 测试版本化 API 时,按版本组织测试。

## 应避免的反模式

1. **链接测试依赖** -- 每个测试必须创建自己的数据。
2. **忽略响应头** -- 头携带重要的元数据。
3. **只测试状态码** -- 始终验证响应体。
4. **使用硬编码 ID** -- ID 应来自测试设置,而不是硬编码值。
5. **跳过错误场景** -- 错误处理测试比快乐路径测试捕获更多 bug。
6. **不测试不同角色** -- API 授权必须按角色测试。
7. **混合 UI 和 API 测试** -- 保持 API 测试与 E2E 浏览器测试分开。
8. **不验证副作用** -- 如果 POST 创建资源,GET 它以确认。
9. **忽略速率限制** -- 测试速率限制被强制执行并处理 429 响应。
10. **不测试大载荷** -- 确保 API 正确处理边界大小。

## 高级模式

### 带上下文隔离的并行 API 测试

```typescript
test.describe.parallel('Isolated API tests', () => {
  test('test A creates and deletes user A', async ({ request }) => {
    const res = await request.post('/users', {
      data: { email: `a-${Date.now()}@test.com`, name: 'A', password: 'Pass123!' },
    });
    const user = await res.json();
    await request.delete(`/users/${user.id}`);
  });

  test('test B creates and deletes user B', async ({ request }) => {
    const res = await request.post('/users', {
      data: { email: `b-${Date.now()}@test.com`, name: 'B', password: 'Pass123!' },
    });
    const user = await res.json();
    await request.delete(`/users/${user.id}`);
  });
});
```

### 带认证的自定义请求上下文

```typescript
test('admin-only endpoint', async ({ playwright }) => {
  const adminContext = await playwright.request.newContext({
    baseURL: 'http://localhost:3000/api',
    extraHTTPHeaders: {
      Authorization: 'Bearer admin-token-here',
    },
  });

  const response = await adminContext.get('/admin/settings');
  expect(response.status()).toBe(200);

  await adminContext.dispose();
});
```

### 通过 API 上传文件

```typescript
import * as fs from 'fs';
import * as path from 'path';

test('should upload a file via API', async ({ request }) => {
  const filePath = path.resolve('test-data/sample.pdf');
  const fileBuffer = fs.readFileSync(filePath);

  const response = await request.post('/files/upload', {
    multipart: {
      file: {
        name: 'sample.pdf',
        mimeType: 'application/pdf',
        buffer: fileBuffer,
      },
      description: 'Test upload',
    },
  });

  expect(response.status()).toBe(201);
  const body = await response.json();
  expect(body.filename).toBe('sample.pdf');
  expect(body.size).toBeGreaterThan(0);
});
```