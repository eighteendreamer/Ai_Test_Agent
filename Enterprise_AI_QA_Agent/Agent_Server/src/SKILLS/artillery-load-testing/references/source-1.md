---
name: Artillery Load Testing
description: 使用 Artillery 进行负载测试，支持 HTTP、WebSocket、Socket.io 等协议
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [performance, load]
frameworks: [artillery]
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [api, web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Artillery 负载测试

您是一位专注于使用 Artillery 进行负载测试的 QA 自动化工程师。当用户要求您编写、审查或调试 Artillery 负载测试时，请遵循这些详细说明。

## 核心原则

1. **真实场景模拟** -- 使用真实的用户行为模式，而不仅仅是命中端点的循环。
2. **渐进式负载** -- 从低并发开始，逐步增加以找到断点。
3. **全面的指标收集** -- 收集延迟、吞吐量、错误率等指标。
4. **CI/CD 集成** -- 在每次 PR 中运行性能回归测试。
5. **详细报告** -- 生成可操作的报告来识别性能瓶颈。

## Artillery 简介

Artillery 是一个现代的负载测试工具，支持：
- HTTP、HTTPS、WebSocket、Socket.io、AWS Kinesis 等协议
- 使用 YAML 或 JavaScript 定义测试场景
- 内置指标收集和报告
- 支持在本地、CI 或云端运行

## 项目结构

```
load-tests/
├── scenarios/
│   ├── api.yml           # API 场景
│   ├── websocket.yml     # WebSocket 场景
│   └── checkout.yml      # 结账流程场景
├── scripts/
│   ├── run-load-test.ts  # 自定义测试脚本
│   └── generate-report.ts
├── artillery.yml         # 主配置
├── artillery.config.ts   # TypeScript 配置
└── reports/              # 测试报告输出目录
```

## 快速开始

### 安装 Artillery

```bash
npm install -g artillery
# 或使用 npx
npx artillery --version
```

### 基本 HTTP 测试

```yaml
# artillery.yml
config:
  target: "https://api.example.com"
  phases:
    - duration: 60
      arrivalRate: 10
      name: "Warm up"
    - duration: 120
      arrivalRate: 50
      name: "Sustained load"
    - duration: 60
      arrivalRate: 100
      name: "Stress test"

scenarios:
  - name: "GET /users"
    request:
      method: GET
      url: "/api/users"

  - name: "POST /login"
    request:
      method: POST
      url: "/api/auth/login"
      json:
        email: "test@example.com"
        password: "password123"
```

运行测试：

```bash
artillery run artillery.yml
artillery run artillery.yml --output report.json
artillery run artillery.yml --insecure  # 跳过 TLS 验证
```

## 场景设计

### 典型 Web 应用场景

```yaml
scenarios:
  - name: "User browsing flow"
    weight: 70  # 70% 的虚拟用户执行此场景
    flow:
      - get:
          url: "/"
      - think: 2  # 2 秒思考时间
      - get:
          url: "/products"
      - think: 1
      - get:
          url: "/products/1"

  - name: "User login and purchase"
    weight: 20
    flow:
      - post:
          url: "/api/auth/login"
          json:
            email: "user@example.com"
            password: "password123"
          capture:
            - json: "$.token"
              as: "authToken"
      - get:
          url: "/api/cart"
          headers:
            Authorization: "Bearer {{ authToken }}"
      - post:
          url: "/api/checkout"
          json:
            items:
              - productId: "prod_123"
                quantity: 2
```

### 认证流程

```yaml
scenarios:
  - name: "Authenticated API calls"
    flow:
      - post:
          url: "/api/auth/login"
          json:
            email: "{{ $email }}"
            password: "{{ $password }}"
          capture:
            - json: "$.access_token"
              as: "accessToken"

      - get:
          url: "/api/users/me"
          headers:
            Authorization: "Bearer {{ accessToken }}"

      - get:
          url: "/api/orders"
          headers:
            Authorization: "Bearer {{ accessToken }}"
```

## 高级配置

### 带有循环的复杂场景

```yaml
config:
  target: "https://api.example.com"
  processor: "./scenarios/processors.js"  # 自定义处理器
  variables:
    - users: ["user1@example.com", "user2@example.com", "user3@example.com"]

scenarios:
  - name: "Process orders for multiple users"
    count:
      - 10  # 10 个虚拟用户
    flow:
      - loop:
          - post:
              url: "/api/orders"
              json:
                userId: "{{ users[$loopCount % users.length] }}"
                items:
                  - productId: "prod_{{ $randomInt(1, 100) }}"
                    quantity: "{{ $randomInt(1, 5) }}"
          - get:
              url: "/api/orders/latest"
        count: 5  # 每个虚拟用户循环 5 次
```

### 自定义处理器

```javascript
// scenarios/processors.js
const { v4: uuidv4 } = require('uuid');

module.exports = {
  // 生成唯一订单 ID
  generateOrderId: (requestParams, context, ee, next) => {
    context.vars.orderId = `ORD-${uuidv4().slice(0, 8).toUpperCase()}`;
    return next();
  },

  // 随机选择产品
  selectRandomProduct: (requestParams, context, ee, next) => {
    const products = ['prod_001', 'prod_002', 'prod_003', 'prod_004', 'prod_005'];
    context.vars.selectedProduct = products[Math.floor(Math.random() * products.length)];
    return next();
  },

  // 添加随机延迟
  randomThinkTime: (requestParams, context, ee, next) => {
    const delay = Math.floor(Math.random() * 3000) + 1000; // 1-4 秒
    setTimeout(() => next(), delay);
  }
};
```

```yaml
scenarios:
  - name: "E-commerce checkout"
    flow:
      - function: "selectRandomProduct"
      - post:
          url: "/api/orders"
          json:
            orderId: "{{ orderId }}"
            productId: "{{ selectedProduct }}"
          capture:
            - json: "$.orderId"
              as: "createdOrderId"
      - function: "randomThinkTime"
      - get:
          url: "/api/orders/{{ createdOrderId }}"
```

## WebSocket 测试

### 基本 WebSocket 连接

```yaml
config:
  target: "wss://api.example.com"
  phases:
    - duration: 30
      arrivalRate: 5
      name: "WebSocket load"

scenarios:
  - name: "Real-time notifications"
    engine: "socketio"
    flow:
      - emit:
          channel: "subscribe"
          data:
            userId: "{{ $randomNumber(1000, 9999) }}"
            channels: ["notifications", "updates"]
      - think: 5
      - capture:
          - json: "$.data"
            as: "notificationData"
      - disconnect:
```

### Socket.io 事件测试

```yaml
scenarios:
  - name: "Chat messaging"
    engine: "socketio"
    flow:
      - emit:
          channel: "join_room"
          data:
            room: "room_{{ $randomNumber(1, 100) }}"
      - think: 1
      - emit:
          channel: "send_message"
          data:
            message: "Hello from load test!"
            timestamp: "{{ $timestamp }}"
      - waitForEvent:
          channel: "message_received"
          timeout: 5000
      - think: 2
      - emit:
          channel: "leave_room"
```

## 性能监控集成

### Prometheus 指标

```yaml
config:
  plugins:
    metrics-by-endpoint:
      enabled: true
    expect:
      enabled: true

  phases:
    - duration: 60
      arrivalRate: 20

scenarios:
  - name: "API with assertions"
    flow:
      - get:
          url: "/api/health"
          expect:
            statusCode: 200
            contentType: "application/json"
      - get:
          url: "/api/users"
          expect:
            statusCode: 200
```

## 报告和分析

### 生成 HTML 报告

```bash
artillery report --output report.json
```

### 自定义报告脚本

```javascript
// scripts/generate-report.js
const fs = require('fs');

function analyzeReport(reportPath) {
  const report = JSON.parse(fs.readFileSync(reportPath, 'utf-8'));

  const summary = {
    totalRequests: report.counters?.requests || 0,
    totalErrors: report.counters?.errors || 0,
    errorRate: 0,
    avgLatency: 0,
    p95Latency: 0,
    p99Latency: 0
  };

  if (summary.totalRequests > 0) {
    summary.errorRate = (summary.totalErrors / summary.totalRequests) * 100;
  }

  // 分析延迟百分位数
  const latencies = report.summaries?.latency || {};
  summary.avgLatency = latencies.mean || 0;
  summary.p95Latency = latencies['95th percentile'] || 0;
  summary.p99Latency = latencies['99th percentile'] || 0;

  console.log('\n=== Load Test Analysis ===');
  console.log(`Total Requests: ${summary.totalRequests}`);
  console.log(`Errors: ${summary.totalErrors} (${summary.errorRate.toFixed(2)}%)`);
  console.log(`Avg Latency: ${summary.avgLatency.toFixed(2)}ms`);
  console.log(`P95 Latency: ${summary.p95Latency.toFixed(2)}ms`);
  console.log(`P99 Latency: ${summary.p99Latency.toFixed(2)}ms`);

  return summary;
}

analyzeReport('./artillery-report.json');
```

## CI/CD 集成

### GitHub Actions

```yaml
name: Load Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  load-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Run Load Test
        run: |
          npm install -g artillery
          artillery run load-tests/artillery.yml \
            --output reports/artillery-report.json

      - name: Analyze Report
        run: node scripts/generate-report.js

      - name: Upload Report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: load-test-report
          path: reports/

      - name: Performance Regression Check
        run: |
          node -e "
            const report = require('./reports/artillery-report.json');
            const p99 = report.summaries.latency['99th percentile'];
            if (p99 > 500) {
              console.error('P99 latency exceeds threshold:', p99);
              process.exit(1);
            }
            console.log('Performance check passed');
          "
```

## 最佳实践

1. **从基线开始** -- 首先建立性能基线，然后测量回归。
2. **渐进式负载** -- 使用渐进式相位来模拟真实用户流量模式。
3. **监控资源使用** -- 除了应用指标，还要监控 CPU、内存、 网络。
4. **使用真实数据** -- 使用真实的数据分布和用户行为模式。
5. **分离测试环境** -- 确保负载测试不会影响生产环境。
6. **预热期** -- 包括预热期让 JIT 编译和连接池预热。
7. **思考时间** -- 添加真实的思考时间来模拟真实用户行为。
8. **分析失败** -- 不仅要检查错误率，还要分析错误类型和模式。

## 应避免的反模式

1. **只测试单个端点** -- 真实用户会浏览多个页面和功能。
2. **立即所有虚拟用户** -- 这不能准确模拟真实流量。
3. **忽略思考时间** -- 没有思考时间会导致过度激进的负载。
4. **不监控应用指标** -- 负载测试期间必须监控应用健康。
5. **测试环境与生产差异大** -- 确保测试环境尽可能接近生产。
6. **不测试错误处理** -- 故意触发错误场景来测试错误处理。
7. **只关注响应时间** -- 还要关注错误率、吞吐量和资源使用。