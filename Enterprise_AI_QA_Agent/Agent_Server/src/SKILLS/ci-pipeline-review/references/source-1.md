---
name: CI Pipeline Optimizer
description: CI/CD 流水线优化，测试并行化，缓存策略和构建时间优化
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [code-quality]
info: vip.hctestedu.com
frameworks: []
languages: [typescript, yaml]
domains: [devops]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# CI 流水线优化

您是一位专注于 CI/CD 流水线优化的 DevOps 工程师。当用户要求您优化 CI/CD 流水线时，请遵循这些详细说明。

## 核心原则

1. **快速反馈循环** -- 流水线应该尽可能快地提供反馈。
2. **并行执行** -- 利用并行化来加速测试和构建。
3. **智能缓存** -- 高效利用缓存减少构建时间。
4. **按需执行** -- 只运行必要的步骤，避免浪费。
5. **可靠性** -- 流水线应该稳定可靠，避免 flaky 构建。

## 优化策略

### 1. 缓存优化

```yaml
# GitHub Actions - 依赖缓存
- name: Cache node_modules
  uses: actions/cache@v4
  with:
    path: |
      node_modules
      .npm
    key: ${{ runner.os }}-node-${{ hashFiles('**/package-lock.json') }}
    restore-keys: |
      ${{ runner.os }}-node-

# 或使用 npm cache
- name: Cache npm
  uses: actions/cache@v4
  with:
    path: ~/.npm
    key: ${{ runner.os }}-npm-${{ hashFiles('**/package-lock.json') }}
    restore-keys: |
      ${{ runner.os }}-npm-
```

```yaml
# Gradle 缓存
- name: Cache Gradle packages
  uses: actions/cache@v4
  with:
    path: |
      ~/.gradle/caches
      ~/.gradle/wrapper
    key: ${{ runner.os }}-gradle-${{ hashFiles('**/*.gradle*', '**/gradle-wrapper.properties') }}
    restore-keys: |
      ${{ runner.os }}-gradle-
```

### 2. 测试并行化

```yaml
# GitHub Actions - 使用测试分割
- name: Run tests in parallel
  run: |
    npx playwright test --shard=${{ matrix.shard }}/${{ matrix.total }}
  matrix:
    shard: [1, 2, 3, 4]
    total: [4]

# Jest 测试分割
- name: Run Jest tests
  run: npx jest --maxWorkers=4 --ci

# TestNG parallel execution
<suite name="Parallel Tests" parallel="methods" thread-count="4">
```

### 3. 跳过不必要的步骤

```yaml
# 仅在文件更改时运行某些步骤
- name: Run unit tests
  if: needs-changes.outputs.changes == 'true'
  run: npm run test:unit

- name: Check for file changes
  id: needs-changes
  uses: dorny/paths-filter@v2
  with:
    filters: |
      src:
        - 'src/**'
      tests:
        - 'tests/**'
```

### 4. Docker 层缓存

```dockerfile
# Dockerfile
FROM node:20-alpine AS builder

# 先复制 package 文件以利用 Docker 层缓存
COPY package*.json ./
RUN npm ci --only=production

COPY . .
RUN npm run build

# 生产镜像
FROM node:20-alpine
WORKDIR /app
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules
CMD ["node", "dist/index.js"]
```

```yaml
# GitHub Actions - Docker 层缓存
- name: Build Docker image
  uses: docker/build-push-action@v5
  with:
    context: .
    push: false
    tags: myapp:${{ github.sha }}
    cache-from: type=registry,ref=myapp:buildcache
    cache-to: type=registry,ref=myapp:buildcache,mode=max
```

## 流水线结构优化

### 多阶段流水线

```yaml
# .github/workflows/ci.yml
name: CI Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

env:
  NODE_VERSION: '20'

jobs:
  # 快速反馈阶段 - 应该在 2-3 分钟内完成
  lint-and-typecheck:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: ESLint
        run: npm run lint

      - name: Type check
        run: npm run typecheck

  # 单元测试 - 应该并行运行
  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run unit tests
        run: npm run test:unit -- --coverage

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: ./coverage/lcov.info

  # 集成测试
  integration-tests:
    name: Integration Tests
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
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run integration tests
        run: npm run test:integration
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/testdb

  # E2E 测试
  e2e-tests:
    name: E2E Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright browsers
        run: npx playwright install --with-deps chromium

      - name: Build application
        run: npm run build

      - name: Run E2E tests
        run: npx playwright test

  # 依赖安全检查
  security-audit:
    name: Security Audit
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Security audit
        run: npm audit --audit-level=high

  # 构建和推送
  build-and-push:
    name: Build and Push
    runs-on: ubuntu-latest
    needs: [lint-and-typecheck, unit-tests, integration-tests]
    if: github.ref == 'refs/heads/main'

    steps:
      - uses: actions/checkout@v4

      - name: Build Docker image
        run: docker build -t myapp:${{ github.sha }} .

      - name: Push to registry
        run: |
          echo "${{ secrets.DOCKER_TOKEN }}" | docker login -u ${{ secrets.DOCKER_USER }} --password-stdin
          docker push myapp:${{ github.sha }}
```

## 矩阵构建策略

```yaml
jobs:
  test-matrix:
    name: Test on ${{ matrix.browser }}/${{ matrix.os }}
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
        browser: [chromium, firefox, webkit]
        node: [18, 20]

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ matrix.node }}

      - name: Install dependencies
        run: npm ci

      - name: Install Playwright
        run: npx playwright install --with-deps ${{ matrix.browser }}

      - name: Run tests
        run: npx playwright test --project=${{ matrix.browser }}
```

## 条件执行

```yaml
jobs:
  docs:
    name: Deploy Documentation
    runs-on: ubuntu-latest
    # 仅在文档更改时运行
    if: contains(github.event.head_commit.message, '[docs]') || contains(github.event.head_commit.message, '[skip ci]') == false && needs.changes.outputs.docs == 'true'

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Deploy docs
        run: npm run deploy:docs

# 或使用 paths-filter
- name: Check for docs changes
  id: changes
  uses: dorny/paths-filter@v2
  with:
    filters: |
      docs:
        - 'docs/**'
        - '**.md'
```

## 缓存策略详解

### npm 依赖缓存

```yaml
- name: Cache npm dependencies
  uses: actions/cache@v4
  with:
    path: |
      ~/.npm
      node_modules
    key: ${{ runner.os }}-node-${{ hashFiles('**/package-lock.json') }}
    restore-keys: |
      ${{ runner.os }}-node-
```

### Docker 缓存

```yaml
# 使用 BuildKit
- name: Build with Docker BuildKit
  uses: docker/setup-buildx-action@v3

- name: Build and push
  uses: docker/build-push-action@v5
  with:
    context: .
    push: ${{ github.ref == 'refs/heads/main' }}
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

### pip/ Python 缓存

```yaml
- name: Cache pip packages
  uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: ${{ runner.os }}-pip-${{ hashFiles('**/requirements.txt') }}
    restore-keys: |
      ${{ runner.os }}-pip-
```

## 测试优化

### Vitest 优化

```typescript
// vitest.config.ts
export default defineConfig({
  test: {
    // 使用 worker 线程并行运行测试
    threads: true,
    // 在文件更改时使用 watch 模式
    watch: !process.env.CI,
    // 设置测试超时
    testTimeout: 10000,
    // 启用 UI reporter 在 CI 之外
    reporters: process.env.CI ? ['dot'] : ['html', 'dot'],
  },
});
```

### Jest 优化

```javascript
// jest.config.js
module.exports = {
  // 并行执行
  maxWorkers: '50%',
  // 缓存
  cache: true,
  cacheDirectory: '<rootDir>/.jest-cache',
  // 预编译
  preambles: ['<rootDir>/jest.preamble.js'],
  // 只运行相关测试
  changedSince: process.env.CI ? undefined : 'main',
};
```

### Playwright 优化

```typescript
// playwright.config.ts
export default defineConfig({
  testDir: './tests',
  fullyParallel: true,  // 完全并行
  retries: process.env.CI ? 2 : 0,  // CI 中重试
  workers: process.env.CI ? 4 : undefined,  // CI 中限制 workers
  timeout: 30000,
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
});
```

## 监控和反馈

### 构建时间追踪

```yaml
- name: Build time tracker
  run: |
    echo "Build started at $(date)"
    # ... 执行构建步骤 ...
    echo "Build completed at $(date)"

- name: Report build time
  run: |
    echo "## Build Metrics" >> $GITHUB_STEP_SUMMARY
    echo "- Duration: $((SECONDS / 60)) minutes" >> $GITHUB_STEP_SUMMARY
```

### 缓存命中率

```yaml
- name: Check cache hit rate
  run: |
    echo "Cache Analysis:"
    echo "- npm cache: ${{ steps.cache-npm.outputs.cache-hit || 'miss' }}"
    echo "- node_modules cache: ${{ steps.cache-modules.outputs.cache-hit || 'miss' }}"
```

## 最佳实践

1. **分离快速和慢速测试** -- 快速测试优先，快速反馈。
2. **使用适当的缓存** -- 依赖缓存可以显著减少构建时间。
3. **并行化一切** -- 利用多核 CPU 并行运行独立任务。
4. **按需执行** -- 使用条件逻辑跳过不必要的步骤。
5. **监控构建时间** -- 追踪构建时间趋势，及时发现回归。
6. **使用 matrix 构建** -- 同时测试多个配置。
7. **保持流水线简洁** -- 避免不必要的复杂性。
8. **自动化一切** -- 减少手动干预和错误。

## 应避免的反模式

1. **顺序运行所有测试** -- 使用并行化加速。
2. **每次都从头构建** -- 有效利用缓存。
3. **不使用 artifacts 传递数据** -- 使用 artifacts 在 job 之间共享数据。
4. **大单体 job** -- 拆分成小的并行 job。
5. **硬编码凭证** -- 使用 secrets 管理敏感信息。
6. **忽略缓存失效** -- 确保证券正确失效。
7. **过度使用条件语句** -- 保持流水线可读性。
8. **没有错误处理** -- 添加适当的错误处理和重试逻辑。