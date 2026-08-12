---
name: GraphQL Testing
description: GraphQL API 测试，包括查询、变更、订阅和变体测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [api]
frameworks: []
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# GraphQL 测试

您是一位专注于 GraphQL API 测试的 QA 工程师。当用户要求您编写、审查或调试 GraphQL 测试时，请遵循这些详细说明。

## 核心原则

1. **查询测试** -- 测试 GraphQL 查询的响应结构和数据。
2. **变更测试** -- 测试数据修改操作。
3. **订阅测试** -- 测试实时数据推送。
4. **变体测试** -- 测试查询变体（fragment、alias 等）。
5. **性能测试** -- 测试查询性能和复杂度。

## GraphQL 基础

### 术语解释

- **Query** -- 读取数据的请求
- **Mutation** -- 修改数据的请求
- **Subscription** -- 实时数据订阅
- **Fragment** -- 可重用的字段集
- **Alias** -- 查询字段别名
- **Variable** -- 动态查询参数

## 项目结构

```
graphql-tests/
├── src/
│   ├── queries/
│   │   ├── user.queries.ts
│   │   └── product.queries.ts
│   ├── mutations/
│   │   └── user.mutations.ts
│   ├── fragments/
│   │   └── common.fragment.ts
│   └── utils/
│       ├── graphql-client.ts
│       └── test-data.ts
├── tests/
│   ├── queries/
│   ├── mutations/
│   ├── subscriptions/
│   └── integration/
├── schemas/
│   └── schema.graphql
├── package.json
└── tsconfig.json
```

## GraphQL 客户端设置

```typescript
// src/utils/graphql-client.ts
import { GraphQLClient, gql } from 'graphql-request';

export interface GraphQLConfig {
  endpoint: string;
  headers?: Record<string, string>;
}

export class GraphQLTestClient {
  private client: GraphQLClient;
  private authToken?: string;

  constructor(config: GraphQLConfig) {
    this.client = new GraphQLClient(config.endpoint, {
      headers: config.headers,
    });
  }

  setAuthToken(token: string): void {
    this.authToken = token;
    this.client = new GraphQLClient(this.client.getEndpoint(), {
      headers: {
        ...this.client.getHeaders(),
        Authorization: `Bearer ${token}`,
      },
    });
  }

  async query<T>(query: string, variables?: Record<string, any>): Promise<T> {
    return this.client.request<T>(gql`${query}`, variables);
  }

  async mutation<T>(mutation: string, variables?: Record<string, any>): Promise<T> {
    return this.client.request<T>(gql`${mutation}`, variables);
  }
}

// 创建测试客户端
export const createClient = (): GraphQLTestClient => {
  return new GraphQLTestClient({
    endpoint: process.env.GRAPHQL_ENDPOINT || 'http://localhost:4000/graphql',
    headers: {
      'Content-Type': 'application/json',
    },
  });
};
```

## 查询测试

### 基本查询

```typescript
// tests/queries/user-queries.test.ts
import { test, expect } from '@jest/globals';
import { createClient } from '../../src/utils/graphql-client';

const client = createClient();

describe('User Queries', () => {
  test('should fetch all users', async () => {
    const query = `
      query GetUsers {
        users {
          id
          email
          name
        }
      }
    `;

    const response = await client.query<{ users: Array<{ id: string; email: string; name: string }> }>(query);

    expect(response.users).toBeDefined();
    expect(Array.isArray(response.users)).toBe(true);
    expect(response.users.length).toBeGreaterThan(0);

    // 验证用户结构
    const user = response.users[0];
    expect(user).toHaveProperty('id');
    expect(user).toHaveProperty('email');
    expect(user).toHaveProperty('name');
  });

  test('should fetch user by ID', async () => {
    const query = `
      query GetUser($id: ID!) {
        user(id: $id) {
          id
          email
          name
          createdAt
        }
      }
    `;

    // 先创建一个用户获取 ID
    const usersResponse = await client.query<{ users: Array<{ id: string }> }>(`
      query { users { id } }
    `);
    const userId = usersResponse.users[0].id;

    const response = await client.query<{ user: any }>(query, { id: userId });

    expect(response.user).toBeDefined();
    expect(response.user.id).toBe(userId);
    expect(response.user.email).toBeDefined();
  });

  test('should fetch users with pagination', async () => {
    const query = `
      query GetUsers($first: Int!, $after: String) {
        users(first: $first, after: $after) {
          edges {
            node {
              id
              email
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    `;

    const response = await client.query<{ users: any }>(query, {
      first: 5,
    });

    expect(response.users.edges).toHaveLength(5);
    expect(response.users.pageInfo).toBeDefined();
    expect(response.users.pageInfo.hasNextPage).toBeDefined();
  });

  test('should filter users by criteria', async () => {
    const query = `
      query GetUsersByRole($role: Role!) {
        users(role: $role) {
          id
          email
          role
        }
      }
    `;

    const response = await client.query<{ users: any[] }>(query, {
      role: 'ADMIN',
    });

    expect(response.users).toBeDefined();
    response.users.forEach(user => {
      expect(user.role).toBe('ADMIN');
    });
  });
});
```

### 嵌套查询

```typescript
describe('Nested Queries', () => {
  test('should fetch user with orders', async () => {
    const query = `
      query GetUserWithOrders($id: ID!) {
        user(id: $id) {
          id
          name
          orders {
            id
            total
            status
            items {
              productId
              quantity
            }
          }
        }
      }
    `;

    const response = await client.query<{ user: any }>(query, { id: 'user-123' });

    expect(response.user).toBeDefined();
    expect(response.user.orders).toBeDefined();
    expect(Array.isArray(response.user.orders)).toBe(true);

    if (response.user.orders.length > 0) {
      const order = response.user.orders[0];
      expect(order).toHaveProperty('id');
      expect(order).toHaveProperty('items');
      expect(Array.isArray(order.items)).toBe(true);
    }
  });

  test('should fetch orders with product details', async () => {
    const query = `
      query GetOrdersWithProducts {
        orders {
          id
          items {
            product {
              id
              name
              price
              category {
                name
              }
            }
            quantity
          }
        }
      }
    `;

    const response = await client.query<{ orders: any[] }>(query);

    expect(response.orders).toBeDefined();

    for (const order of response.orders) {
      for (const item of order.items) {
        expect(item.product).toBeDefined();
        expect(item.product.name).toBeDefined();
        expect(item.product.category).toBeDefined();
      }
    }
  });
});
```

## 变更测试

### 创建数据

```typescript
// tests/mutations/user-mutations.test.ts
import { test, expect } from '@jest/globals';
import { createClient } from '../../src/utils/graphql-client';

const client = createClient();

describe('User Mutations', () => {
  test('should create a new user', async () => {
    const mutation = `
      mutation CreateUser($input: CreateUserInput!) {
        createUser(input: $input) {
          id
          email
          name
          role
        }
      }
    `;

    const variables = {
      input: {
        email: `test-${Date.now()}@example.com`,
        name: 'Test User',
        password: 'SecurePass123!',
        role: 'USER',
      },
    };

    const response = await client.mutation<{ createUser: any }>(mutation, variables);

    expect(response.createUser).toBeDefined();
    expect(response.createUser.id).toBeDefined();
    expect(response.createUser.email).toBe(variables.input.email);
    expect(response.createUser.name).toBe(variables.input.name);
    expect(response.createUser.role).toBe('USER');
  });

  test('should update user information', async () => {
    // 先创建用户
    const createMutation = `
      mutation CreateUser($input: CreateUserInput!) {
        createUser(input: $input) {
          id
        }
      }
    `;

    const newUser = await client.mutation<{ createUser: { id: string } }>(createMutation, {
      input: {
        email: `update-test-${Date.now()}@example.com`,
        name: 'Original Name',
        password: 'SecurePass123!',
      },
    });

    const userId = newUser.createUser.id;

    // 更新用户
    const updateMutation = `
      mutation UpdateUser($id: ID!, $input: UpdateUserInput!) {
        updateUser(id: $id, input: $input) {
          id
          name
          email
        }
      }
    `;

    const updateResponse = await client.mutation<{ updateUser: any }>(updateMutation, {
      id: userId,
      input: {
        name: 'Updated Name',
      },
    });

    expect(updateResponse.updateUser.name).toBe('Updated Name');
    expect(updateResponse.updateUser.email).toBe(`update-test-${Date.now()}@example.com`);
  });

  test('should delete a user', async () => {
    // 先创建用户
    const createMutation = `
      mutation CreateUser($input: CreateUserInput!) {
        createUser(input: $input) {
          id
        }
      }
    `;

    const newUser = await client.mutation<{ createUser: { id: string } }>(createMutation, {
      input: {
        email: `delete-test-${Date.now()}@example.com`,
        name: 'Delete Me',
        password: 'SecurePass123!',
      },
    });

    const userId = newUser.createUser.id;

    // 删除用户
    const deleteMutation = `
      mutation DeleteUser($id: ID!) {
        deleteUser(id: $id) {
          success
        }
      }
    `;

    const deleteResponse = await client.mutation<{ deleteUser: { success: boolean } }>(deleteMutation, {
      id: userId,
    });

    expect(deleteResponse.deleteUser.success).toBe(true);

    // 验证用户已被删除
    const query = `
      query GetUser($id: ID!) {
        user(id: $id) {
          id
        }
      }
    `;

    const userResponse = await client.query<{ user: any }>(query, { id: userId });
    expect(userResponse.user).toBeNull();
  });
});
```

### 变更验证测试

```typescript
describe('Mutation Validation', () => {
  test('should reject duplicate email', async () => {
    const email = `duplicate-${Date.now()}@example.com`;

    const mutation = `
      mutation CreateUser($input: CreateUserInput!) {
        createUser(input: $input) {
          id
        }
      }
    `;

    // 创建第一个用户
    await client.mutation(mutation, {
      input: {
        email,
        name: 'User 1',
        password: 'SecurePass123!',
      },
    });

    // 尝试创建相同邮箱的用户
    const response = await client.mutation(mutation, {
      input: {
        email,
        name: 'User 2',
        password: 'SecurePass123!',
      },
    }).catch(err => err.response);

    expect(response.errors).toBeDefined();
    expect(response.errors[0].message).toContain('email');
  });

  test('should validate required fields', async () => {
    const mutation = `
      mutation CreateUser($input: CreateUserInput!) {
        createUser(input: $input) {
          id
        }
      }
    `;

    const response = await client.mutation(mutation, {
      input: {
        email: 'test@example.com',
        // 缺少必填字段 name
      },
    }).catch(err => err.response);

    expect(response.errors).toBeDefined();
  });

  test('should validate email format', async () => {
    const mutation = `
      mutation CreateUser($input: CreateUserInput!) {
        createUser(input: $input) {
          id
        }
      }
    `;

    const response = await client.mutation(mutation, {
      input: {
        email: 'not-an-email',
        name: 'Test User',
        password: 'SecurePass123!',
      },
    }).catch(err => err.response);

    expect(response.errors).toBeDefined();
    expect(response.errors[0].message).toContain('email');
  });
});
```

## 认证测试

```typescript
describe('Authentication', () => {
  test('should login and get token', async () => {
    const mutation = `
      mutation Login($email: String!, $password: String!) {
        login(email: $email, password: $password) {
          token
          user {
            id
            email
          }
        }
      }
    `;

    // 先创建用户
    const createMutation = `
      mutation CreateUser($input: CreateUserInput!) {
        createUser(input: $input) {
          id
        }
      }
    `;

    const email = `auth-test-${Date.now()}@example.com`;
    await client.mutation(createMutation, {
      input: {
        email,
        name: 'Auth Test',
        password: 'SecurePass123!',
      },
    });

    // 登录
    const response = await client.mutation<{ login: { token: string; user: any } }>(mutation, {
      email,
      password: 'SecurePass123!',
    });

    expect(response.login.token).toBeDefined();
    expect(response.login.user.email).toBe(email);
  });

  test('should reject invalid credentials', async () => {
    const mutation = `
      mutation Login($email: String!, $password: String!) {
        login(email: $email, password: $password) {
          token
        }
      }
    `;

    const response = await client.mutation(mutation, {
      email: 'nonexistent@example.com',
      password: 'wrongpassword',
    }).catch(err => err.response);

    expect(response.errors).toBeDefined();
    expect(response.errors[0].message).toContain('invalid');
  });

  test('should access protected fields with token', async () => {
    const mutation = `
      mutation Login($email: String!, $password: String!) {
        login(email: $email, password: $password) {
          token
        }
      }
    `;

    const email = `protected-${Date.now()}@example.com`;
    const loginResponse = await client.mutation<{ login: { token: string } }>(mutation, {
      email,
      password: 'SecurePass123!',
    });

    // 使用 token 创建新客户端
    const authenticatedClient = createClient();
    authenticatedClient.setAuthToken(loginResponse.login.token);

    // 访问受保护字段
    const query = `
      query GetMe {
        me {
          id
          email
          role
        }
      }
    `;

    const response = await authenticatedClient.query<{ me: any }>(query);

    expect(response.me).toBeDefined();
    expect(response.me.email).toBe(email);
  });
});
```

## Fragment 和 Alias 测试

```typescript
describe('GraphQL Features', () => {
  test('should use fragments for reusable fields', async () => {
    const query = `
      fragment UserFields on User {
        id
        email
        name
        createdAt
      }

      query GetUsers {
        users {
          ...UserFields
        }
      }
    `;

    const response = await client.query<{ users: any[] }>(query);

    expect(response.users).toBeDefined();
    response.users.forEach(user => {
      expect(user).toHaveProperty('id');
      expect(user).toHaveProperty('email');
      expect(user).toHaveProperty('name');
      expect(user).toHaveProperty('createdAt');
    });
  });

  test('should use aliases for multiple queries', async () => {
    const query = `
      query GetUsersByRole {
        admins: users(role: ADMIN) {
          id
          name
        }
        regularUsers: users(role: USER) {
          id
          name
        }
      }
    `;

    const response = await client.query<{
      admins: any[];
      regularUsers: any[];
    }>(query);

    expect(response.admins).toBeDefined();
    expect(response.regularUsers).toBeDefined();
  });

  test('should use variables in queries', async () => {
    const query = `
      query GetUser($id: ID!, $includeOrders: Boolean!) {
        user(id: $id) {
          id
          name
          orders @include(if: $includeOrders) {
            id
          }
        }
      }
    `;

    // 带订单
    const withOrders = await client.query<{ user: any }>(query, {
      id: 'user-123',
      includeOrders: true,
    });
    expect(withOrders.user.orders).toBeDefined();

    // 不带订单
    const withoutOrders = await client.query<{ user: any }>(query, {
      id: 'user-123',
      includeOrders: false,
    });
    expect(withoutOrders.user.orders).toBeUndefined();
  });
});
```

## 性能测试

```typescript
describe('Performance Tests', () => {
  test('should respond within acceptable time', async () => {
    const query = `
      query GetUsers {
        users {
          id
          email
          name
        }
      }
    `;

    const start = Date.now();
    await client.query(query);
    const duration = Date.now() - start;

    expect(duration).toBeLessThan(1000);  // 1秒内响应
  });

  test('should handle large result sets efficiently', async () => {
    const query = `
      query GetUsers($first: Int!) {
        users(first: $first) {
          edges {
            node {
              id
            }
          }
        }
      }
    `;

    const start = Date.now();
    const response = await client.query<{ users: any }>(query, { first: 1000 });
    const duration = Date.now() - start;

    expect(response.users.edges).toHaveLength(1000);
    expect(duration).toBeLessThan(5000);  // 5秒内响应
  });

  test('should limit query complexity', async () => {
    // 深度嵌套查询
    const complexQuery = `
      query GetDeepNestedData {
        users(first: 10) {
          orders(first: 10) {
            items(first: 10) {
              product {
                reviews(first: 10) {
                  author {
                    orders(first: 10) {
                      items(first: 5) {
                        product {
                          category {
                            products(first: 5) {
                              id
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    `;

    const response = await client.query(complexQuery).catch(err => err.response);

    // 应该被拒绝或限制复杂度
    if (response.errors) {
      expect(response.errors[0].message).toContain('complexity');
    }
  });
});
```

## CI/CD 集成

```yaml
name: GraphQL Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Start GraphQL server
        run: npm run start:graphql &
        timeout-minutes: 2

      - name: Wait for server
        run: npx wait-on http://localhost:4000/graphql

      - name: Run GraphQL tests
        run: npm run test:graphql

      - name: Run schema validation
        run: npm run validate:schema

      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: graphql-test-results
          path: coverage/
```

## 最佳实践

1. **测试查询和变更** -- 确保读写操作都正确。
2. **验证响应结构** -- 检查返回的数据结构。
3. **测试错误处理** -- 验证错误情况。
4. **使用 Fragment** -- 提高查询复用性。
5. **参数化查询** -- 使用变量而非字符串拼接。
6. **测试认证** -- 验证权限控制。
7. **性能测试** -- 确保查询性能可接受。
8. **监控复杂度** -- 防止过度复杂的查询。

## 应避免的反模式

1. **不测试错误情况** -- 必须测试各种失败场景。
2. **硬编码 ID** -- 使用动态创建的测试数据。
3. **忽略认证测试** -- 验证权限控制。
4. **不测试嵌套查询** -- 确保深度查询工作正常。
5. **忽略性能** -- 查询可能很慢。
6. **不验证 schema** -- 确保 schema 变更正确。
7. **过度使用 alias** -- 影响可读性。
8. **不测试分页** -- 确保分页正常工作。