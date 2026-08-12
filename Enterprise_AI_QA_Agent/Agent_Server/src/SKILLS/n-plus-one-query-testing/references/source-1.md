---
name: N+1 Query Detector
description: 使用 APM 和查询分析检测 N+1 查询问题，支持 PostgreSQL、MySQL、MongoDB
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [performance, integration]
frameworks: []
languages: [typescript, javascript, java]
info: vip.hctestedu.com
domains: [api, database]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# N+1 查询检测器

您是一位专注于数据库性能测试的 QA 工程师。当用户要求您检测 N+1 查询问题时，请遵循这些详细说明。

## 核心原则

1. **识别 N+1 模式** -- 检测循环中的重复查询。
2. **量化影响** -- 测量 N+1 查询的性能开销。
3. **提供修复方案** -- 使用 eager loading、join 等方案。
4. **预防为主** -- 在代码审查和 CI 中检测 N+1。
5. **持续监控** -- 确保修复后不会重新引入。

## 什么是 N+1 查询问题

N+1 查询问题发生在：
1. 首先执行 1 次查询获取主对象列表
2. 然后对每个对象执行 N 次额外查询

例如：
```sql
-- 1 次查询获取所有用户
SELECT * FROM users;

-- N 次查询获取每个用户的订单
SELECT * FROM orders WHERE user_id = 1;
SELECT * FROM orders WHERE user_id = 2;
SELECT * FROM orders WHERE user_id = 3;
-- ... 重复 N 次
```

## 检测工具

### 1. APM 工具
- New Relic
- Datadog
- Scout APM
- Elastic APM

### 2. 数据库日志
- PostgreSQL: `log_min_duration_statement`
- MySQL: 慢查询日志
- MongoDB: Profiler

### 3. 测试工具
- Jest with query logging
- Playwright with backend monitoring
- 自定义查询计数器

## 测试策略

### 基于日志的分析

```typescript
// 测试脚本：分析查询日志
import * as fs from 'fs';

interface QueryLog {
  timestamp: string;
  duration: number;
  query: string;
  stackTrace?: string;
}

interface N1Analysis {
  totalQueries: number;
  uniqueTables: number;
  potentialN1Queries: QueryPattern[];
  recommendations: string[];
}

interface QueryPattern {
  pattern: string;
  count: number;
  table: string;
  samples: string[];
}

function analyzeQueryLog(logPath: string): N1Analysis {
  const logs = JSON.parse(fs.readFileSync(logPath, 'utf-8')) as QueryLog[];

  // 识别潜在 N+1 查询
  const patternCounts = new Map<string, number>();
  const patternQueries = new Map<string, string[]>();

  for (const entry of logs) {
    const normalized = normalizeQuery(entry.query);
    const count = patternCounts.get(normalized) || 0;
    patternCounts.set(normalized, count + 1);

    const samples = patternQueries.get(normalized) || [];
    if (samples.length < 5) samples.push(entry.query);
    patternQueries.set(normalized, samples);
  }

  // 找出重复执行的查询
  const potentialN1Queries: QueryPattern[] = [];
  for (const [pattern, count] of patternCounts) {
    if (count > 10) {  // 超过10次执行可能是 N+1
      const tableMatch = pattern.match(/FROM\s+(\w+)/i);
      potentialN1Queries.push({
        pattern,
        count,
        table: tableMatch ? tableMatch[1] : 'unknown',
        samples: patternQueries.get(pattern) || [],
      });
    }
  }

  return {
    totalQueries: logs.length,
    uniqueTables: new Set(potentialN1Queries.map(q => q.table)).size,
    potentialN1Queries: potentialN1Queries.sort((a, b) => b.count - a.count),
    recommendations: generateRecommendations(potentialN1Queries),
  };
}

function normalizeQuery(query: string): string {
  // 移除数值和字符串字面量，提取查询模式
  return query
    .replace(/\d+/g, '?')
    .replace(/'[^']*'/g, '?')
    .replace(/\s+/g, ' ')
    .trim()
    .toUpperCase();
}

function generateRecommendations(patterns: QueryPattern[]): string[] {
  const recommendations: string[] = [];

  for (const pattern of patterns) {
    if (pattern.count > 50) {
      recommendations.push(
        `High frequency query on ${pattern.table}: ${pattern.count} executions. ` +
        `Consider using JOIN or batch fetching.`
      );
    }
  }

  return recommendations;
}
```

### 使用 Jest 进行 N+1 测试

```typescript
// tests/performance/query-monitoring.test.ts
import { Pool } from 'pg';

describe('N+1 Query Detection', () => {
  let pool: Pool;
  let queryLog: Array<{ sql: string; duration: number }> = [];

  beforeAll(() => {
    pool = new Pool({
      connectionString: process.env.DATABASE_URL,
    });

    // 启用查询日志
    pool.on('query', (e) => {
      const start = Date.now();
      e.query.on('end', () => {
        queryLog.push({
          sql: e.query.text,
          duration: Date.now() - start,
        });
      });
    });
  });

  afterAll(async () => {
    await pool.end();
  });

  beforeEach(() => {
    queryLog = [];
  });

  it('should not execute N+1 queries when fetching users with orders', async () => {
    // 调用可能产生 N+1 的函数
    const users = await getUsersWithOrders();

    // 分析查询日志
    const ordersQueryCount = queryLog.filter(q =>
      q.sql.toUpperCase().includes('SELECT') &&
      q.sql.toUpperCase().includes('ORDERS')
    ).length;

    // 如果有 N+1，订单查询数量会等于用户数量
    const userCount = users.length;

    // 允许少量订单查询（使用 JOIN 的情况），但不应该等于用户数
    expect(ordersQueryCount).toBeLessThan(userCount);

    // 总体查询数应该合理
    expect(queryLog.length).toBeLessThan(10);
  });

  it('should use batch fetching instead of loop queries', async () => {
    const start = Date.now();
    const result = await getProductsWithCategories();
    const duration = Date.now() - start;

    // 验证性能：100个产品的查询应该在合理时间内完成
    expect(duration).toBeLessThan(5000);

    // 验证查询次数：不应该有大量重复查询
    const categoryQueries = queryLog.filter(q =>
      q.sql.toUpperCase().includes('CATEGORIES')
    ).length;

    expect(categoryQueries).toBeLessThan(5);
  });
});
```

### E2E 测试中的 N+1 检测

```typescript
// tests/e2e/n1-detection.test.ts
import { test, expect, request } from '@playwright/test';

test.describe('N+1 Query Detection', () => {
  let queryCount = 0;
  let queries: Array<{ url: string; duration: number }> = [];

  test.beforeEach(async ({ page }) => {
    queryCount = 0;
    queries = [];

    // 监听 API 请求
    page.on('response', async (response) => {
      if (response.url().includes('/api/')) {
        queries.push({
          url: response.url(),
          duration: response.request().timing()?.responseEnd || 0,
        });
      }
    });
  });

  test('user list endpoint should not have N+1 problem', async ({ request }) => {
    // 记录初始查询数
    const initialCount = queries.length;

    // 调用 API
    const response = await request.get('/api/users');
    expect(response.ok()).toBeTruthy();

    const users = await response.json();

    // 等待所有后续请求完成
    await page.waitForLoadState('networkidle');

    // 统计用户相关的查询
    const userRelatedQueries = queries.filter(q =>
      q.url.includes('/api/users')
    ).length;

    // N+1 检测：如果只调用了一次 users API 但获取了多个用户的数据
    // 说明可能有 N+1（需要在后端添加查询计数）
    if (users.length > 10) {
      // 验证响应时间合理
      const totalDuration = queries.reduce((sum, q) => sum + q.duration, 0);
      expect(totalDuration).toBeLessThan(3000);
    }
  });
});
```

## 修复方案

### 1. 使用 JOIN

```typescript
// 修复前：N+1 查询
async function getUsersWithOrders() {
  const users = await db.query('SELECT * FROM users');
  return users.map(async (user) => {
    const orders = await db.query('SELECT * FROM orders WHERE user_id = ?', [user.id]);
    return { ...user, orders };
  });
}

// 修复后：使用 JOIN
async function getUsersWithOrders() {
  const results = await db.query(`
    SELECT u.*, o.id as order_id, o.total as order_total, o.status
    FROM users u
    LEFT JOIN orders o ON u.id = o.user_id
    ORDER BY u.id, o.id
  `);

  // 按用户分组
  const usersMap = new Map();
  for (const row of results) {
    if (!usersMap.has(row.id)) {
      usersMap.set(row.id, {
        id: row.id,
        name: row.name,
        email: row.email,
        orders: [],
      });
    }
    if (row.order_id) {
      usersMap.get(row.id).orders.push({
        id: row.order_id,
        total: row.order_total,
        status: row.status,
      });
    }
  }

  return Array.from(usersMap.values());
}
```

### 2. 使用批量查询

```typescript
// 修复前：循环查询
async function getProductsWithCategories() {
  const products = await db.query('SELECT * FROM products');
  return products.map(async (product) => {
    const category = await db.query(
      'SELECT * FROM categories WHERE id = ?',
      [product.category_id]
    );
    return { ...product, category };
  });
}

// 修复后：批量查询
async function getProductsWithCategories() {
  const products = await db.query('SELECT * FROM products');

  // 获取所有相关的 category_id
  const categoryIds = [...new Set(products.map(p => p.category_id))];

  // 一次性查询所有 categories
  const categories = await db.query(
    'SELECT * FROM categories WHERE id IN (?)',
    [categoryIds]
  );

  // 创建 map 便于查找
  const categoryMap = new Map(categories.map(c => [c.id, c]));

  // 合并数据
  return products.map(product => ({
    ...product,
    category: categoryMap.get(product.category_id),
  }));
}
```

### 3. 使用 ORM 的 Eager Loading

```typescript
// Sequelize 示例
// 修复前
const users = await User.findAll();

// 修复后：使用 eager loading
const users = await User.findAll({
  include: [{
    model: Order,
    as: 'orders',
  }],
});

// 或者使用 nested eager loading
const users = await User.findAll({
  include: [{
    model: Order,
    as: 'orders',
    include: [{
      model: Product,
      as: 'items',
    }],
  }],
});
```

```typescript
// Prisma 示例
// 修复前
const users = await prisma.user.findMany();

// 修复后：使用 include
const users = await prisma.user.findMany({
  include: {
    orders: {
      include: {
        items: true,
      },
    },
  },
});
```

## CI/CD 集成

```yaml
name: N+1 Query Detection
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  n1-detection:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: testdb
        options: >-
          --health-cmd pg_isready
          --health-interval 10s

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'

      - name: Install dependencies
        run: npm ci

      - name: Enable query logging
        run: |
          docker exec ${{ job.services.postgres.outputs.id }} \
            psql -U test -d testdb -c \
            "ALTER DATABASE testdb SET log_min_duration_statement = 0;"

      - name: Run N+1 tests
        run: npm run test:n1-detection

      - name: Analyze query logs
        run: node scripts/analyze-n1.js

      - name: Upload report
        uses: actions/upload-artifact@v4
        with:
          name: n1-query-report
          path: reports/n1-*.json
```

## 监控仪表板

```typescript
// 简单的 N+1 监控指标
interface N1Metrics {
  endpoint: string;
  queryCount: number;
  queryPattern: string;
  estimatedN1Severity: 'none' | 'low' | 'medium' | 'high';
  lastDetected: string;
}

function calculateN1Severity(
  totalQueries: number,
  primaryQueryCount: number,
  expectedMaxQueries: number
): N1Metrics['estimatedN1Severity'] {
  const ratio = totalQueries / primaryQueryCount;

  if (ratio <= expectedMaxQueries) return 'none';
  if (ratio <= expectedMaxQueries * 2) return 'low';
  if (ratio <= expectedMaxQueries * 5) return 'medium';
  return 'high';
}
```

## 最佳实践

1. **启用查询日志** -- 在开发环境启用详细日志。
2. **分析慢查询** -- 关注执行时间长的查询。
3. **使用 JOIN** -- 替代循环查询。
4. **批量操作** -- 收集 ID 后一次性查询。
5. **Eager Loading** -- 使用 ORM 的预加载功能。
6. **添加测试** -- 创建 N+1 检测测试。
7. **定期审查** -- 代码审查时检查查询模式。
8. **性能测试** -- 在性能测试套件中包含 N+1 检测。

## 应避免的反模式

1. **在循环中执行查询** -- 这是 N+1 的主要原因。
2. **忽略查询数量** -- 只关注执行时间不够。
3. **过度使用 JOIN** -- 复杂的 JOIN 可能影响性能。
4. **不测量基准** -- 不知道修复前的性能基线。
5. **一次性加载太多数据** -- 可能导致内存问题。
6. **忽略缓存** -- 合理使用缓存减少查询。
7. **不使用查询分析工具** -- 手动难以发现 N+1。
8. **修复后不测试** -- 确保修复有效且不引入新问题。