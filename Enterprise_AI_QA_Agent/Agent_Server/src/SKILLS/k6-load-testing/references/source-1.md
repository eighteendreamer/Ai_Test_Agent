---
name: k6 Performance Testing
description: 使用 k6 进行现代负载测试,包含阈值、场景和自定义指标
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [performance, load]
frameworks: [k6]
info: vip.hctestedu.com
languages: [javascript]
domains: [api, web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# k6 性能测试技能

你是一位专注于 k6 负载测试的性能工程专家。当用户要求你编写、审查或调试 k6 性能测试时,请遵循以下详细说明。

## 核心原则

1. **测试真实场景** -- 根据实际用户行为模式建模测试。
2. **定义明确的阈值** -- 每个测试必须预先定义通过/失败标准。
3. **逐步预热** -- 永远不要立即用满负载冲击系统。
4. **广泛使用检查** -- 即使在负载下也要验证响应。
5. **监控和关联** -- 将 k6 指标与服务器端监控结合。

## 项目结构

```
k6/
  scripts/
    smoke-test.js
    load-test.js
    stress-test.js
    spike-test.js
    soak-test.js
  scenarios/
    api-scenarios.js
    user-flows.js
  utils/
    helpers.js
    auth.js
    data-generators.js
  data/
    users.csv
    payloads.json
  thresholds/
    default-thresholds.js
  config/
    environments.js
  results/
    .gitkeep
```

## 基本负载测试脚本

```javascript
import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

// 自定义指标
const errorRate = new Rate('errors');
const loginDuration = new Trend('login_duration');
const requestCount = new Counter('total_requests');

export const options = {
  stages: [
    { duration: '2m', target: 10 },   // 预热到 10 用户
    { duration: '5m', target: 10 },   // 保持 10 用户
    { duration: '2m', target: 50 },   // 预热到 50 用户
    { duration: '5m', target: 50 },   // 保持 50 用户
    { duration: '2m', target: 0 },    // 预热下降
  ],
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],  // 95th 百分位 < 500ms
    http_req_failed: ['rate<0.01'],                     // 错误率 < 1%
    errors: ['rate<0.05'],                              // 自定义错误率 < 5%
    login_duration: ['p(95)<800'],                      // 登录 95th < 800ms
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:3000';

export default function () {
  group('Homepage', () => {
    const response = http.get(`${BASE_URL}/`);

    check(response, {
      'homepage status is 200': (r) => r.status === 200,
      'homepage loads in < 2s': (r) => r.timings.duration < 2000,
      'homepage has correct title': (r) => r.body.includes('<title>'),
    });

    errorRate.add(response.status !== 200);
    requestCount.add(1);
  });

  sleep(1);

  group('Login', () => {
    const startTime = Date.now();

    const loginResponse = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
      email: 'user@example.com',
      password: 'SecurePass123!',
    }), {
      headers: { 'Content-Type': 'application/json' },
    });

    loginDuration.add(Date.now() - startTime);

    check(loginResponse, {
      'login status is 200': (r) => r.status === 200,
      'login returns token': (r) => JSON.parse(r.body).token !== undefined,
    });

    errorRate.add(loginResponse.status !== 200);
    requestCount.add(1);
  });

  sleep(Math.random() * 3 + 1); // 1-4 秒之间的随机思考时间
}
```

## 测试类型

### 冒烟测试

```javascript
export const options = {
  vus: 1,
  duration: '1m',
  thresholds: {
    http_req_duration: ['p(99)<1500'],
    http_req_failed: ['rate<0.01'],
  },
};

// 验证系统在最小负载下工作
export default function () {
  const response = http.get(`${BASE_URL}/api/health`);
  check(response, {
    'status is 200': (r) => r.status === 200,
  });
  sleep(1);
}
```

### 负载测试

```javascript
export const options = {
  stages: [
    { duration: '5m', target: 100 },   // 预热
    { duration: '10m', target: 100 },   // 稳态
    { duration: '5m', target: 0 },      // 预热下降
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
  },
};
```

### 压力测试

```javascript
export const options = {
  stages: [
    { duration: '2m', target: 100 },
    { duration: '5m', target: 100 },
    { duration: '2m', target: 200 },
    { duration: '5m', target: 200 },
    { duration: '2m', target: 300 },
    { duration: '5m', target: 300 },
    { duration: '2m', target: 400 },
    { duration: '5m', target: 400 },
    { duration: '10m', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    http_req_failed: ['rate<0.05'],
  },
};
```

### 峰值测试

```javascript
export const options = {
  stages: [
    { duration: '1m', target: 10 },     // 正常负载
    { duration: '10s', target: 500 },    // 峰值!
    { duration: '3m', target: 500 },     // 保持峰值
    { duration: '10s', target: 10 },     // 恢复
    { duration: '3m', target: 10 },      // 观察恢复
    { duration: '1m', target: 0 },       // 预热下降
  ],
};
```

### 浸泡测试

```javascript
export const options = {
  stages: [
    { duration: '5m', target: 50 },     // 预热
    { duration: '4h', target: 50 },     // 持续负载 4 小时
    { duration: '5m', target: 0 },      // 预热下降
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
  },
};
```

## 场景(高级配置)

```javascript
export const options = {
  scenarios: {
    browse_products: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 50 },
        { duration: '5m', target: 50 },
        { duration: '2m', target: 0 },
      ],
      gracefulRampDown: '30s',
      exec: 'browseProducts',
    },
    checkout_flow: {
      executor: 'constant-arrival-rate',
      rate: 10,          // 每 timeUnit 10 次迭代
      timeUnit: '1s',
      duration: '5m',
      preAllocatedVUs: 20,
      maxVUs: 50,
      exec: 'checkoutFlow',
    },
    api_health_check: {
      executor: 'constant-vus',
      vus: 5,
      duration: '10m',
      exec: 'healthCheck',
    },
  },
  thresholds: {
    'http_req_duration{scenario:browse_products}': ['p(95)<300'],
    'http_req_duration{scenario:checkout_flow}': ['p(95)<800'],
    'http_req_duration{scenario:api_health_check}': ['p(95)<100'],
  },
};

export function browseProducts() {
  http.get(`${BASE_URL}/api/products`);
  sleep(2);
}

export function checkoutFlow() {
  // 完整结账流程
  const cart = http.post(`${BASE_URL}/api/cart`, JSON.stringify({
    productId: 'prod-001',
    quantity: 1,
  }), { headers: { 'Content-Type': 'application/json' } });

  check(cart, { 'cart created': (r) => r.status === 201 });

  const checkout = http.post(`${BASE_URL}/api/checkout`, JSON.stringify({
    cartId: JSON.parse(cart.body).id,
  }), { headers: { 'Content-Type': 'application/json' } });

  check(checkout, { 'checkout success': (r) => r.status === 200 });
  sleep(1);
}

export function healthCheck() {
  http.get(`${BASE_URL}/api/health`);
  sleep(1);
}
```

## 认证模式

```javascript
import http from 'k6/http';
import { check } from 'k6';

// setup 函数在测试开始前运行一次
export function setup() {
  const loginResponse = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
    email: 'load-test@example.com',
    password: 'SecurePass123!',
  }), {
    headers: { 'Content-Type': 'application/json' },
  });

  const body = JSON.parse(loginResponse.body);
  return { token: body.token };
}

export default function (data) {
  const params = {
    headers: {
      Authorization: `Bearer ${data.token}`,
      'Content-Type': 'application/json',
    },
  };

  const response = http.get(`${BASE_URL}/api/users/me`, params);
  check(response, {
    'authenticated request succeeds': (r) => r.status === 200,
  });
}
```

## 数据驱动测试

### 使用 CSV 数据

```javascript
import { SharedArray } from 'k6/data';
import papaparse from 'https://jslib.k6.io/papaparse/5.1.1/index.js';
import { open } from 'k6';

const csvData = new SharedArray('users', function () {
  return papaparse.parse(open('./data/users.csv'), { header: true }).data;
});

export default function () {
  const user = csvData[Math.floor(Math.random() * csvData.length)];

  const response = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({
    email: user.email,
    password: user.password,
  }), {
    headers: { 'Content-Type': 'application/json' },
  });

  check(response, {
    'login successful': (r) => r.status === 200,
  });
}
```

### 使用 JSON 载荷

```javascript
import { SharedArray } from 'k6/data';
import { open } from 'k6';

const products = new SharedArray('products', function () {
  return JSON.parse(open('./data/payloads.json'));
});

export default function () {
  const product = products[__VU % products.length];

  const response = http.post(`${BASE_URL}/api/products`, JSON.stringify(product), {
    headers: { 'Content-Type': 'application/json' },
  });

  check(response, {
    'product created': (r) => r.status === 201,
  });
}
```

## 自定义指标

```javascript
import { Trend, Rate, Counter, Gauge } from 'k6/metrics';

// Trend -- 追踪 min、max、avg、百分位数
const apiCallDuration = new Trend('api_call_duration');

// Rate -- 追踪非零值的百分比
const failureRate = new Rate('failure_rate');

// Counter -- 追踪累积计数
const totalRequests = new Counter('total_requests');

// Gauge -- 追踪最后一个值
const activeUsers = new Gauge('active_users');

export default function () {
  const start = Date.now();
  const response = http.get(`${BASE_URL}/api/products`);
  const duration = Date.now() - start;

  apiCallDuration.add(duration);
  failureRate.add(response.status !== 200);
  totalRequests.add(1);
  activeUsers.add(__VU);
}
```

## 最佳实践

1. **始终定义阈值** -- 没有通过/失败标准的测试只是观察。
2. **使用真实的思考时间** -- 在请求之间添加 `sleep()` 以模拟真实用户。
3. **逐步预热** -- 从低开始,增加负载以识别破坏点。
4. **参数化一切** -- 使用环境变量处理 URL、凭证和目标。
5. **使用 `group()` 组织逻辑部分** -- 分组出现在结果中并帮助分析。
6. **广泛使用 `check()`** -- 检查验证负载下的正确性。
7. **对大数据集使用 `SharedArray`** -- 它减少 VU 之间的内存使用。
8. **标记请求** -- 使用标签在分析中过滤指标。
9. **首先运行冒烟测试** -- 在大规模运行之前验证脚本工作。
10. **将结果保存到文件** -- 使用 `--out json=results.json` 进行事后分析。

## 应避免的反模式

1. **无阈值** -- 没有阈值,你无法确定测试是通过还是失败。
2. **无思考时间** -- 没有 `sleep()` 的请求创建不真实的负载模式。
3. **从单一位置测试** -- 使用分布式执行以获得真实的地理分布。
4. **忽略预热** -- 即时满负载不符合真实流量模式。
5. **硬编码 URL** -- 使用环境变量和配置文件。
6. **不验证响应** -- 快速的 500 错误不是成功的请求。
7. **忘记 `setup()`/`teardown()`** -- 使用生命周期钩子进行测试数据管理。
8. **默认函数中的大文件上传** -- 在默认函数外部使用 `open()`。
9. **不与服务器指标关联** -- k6 结果 alone 不能说明全部故事。
10. **未经批准对生产环境运行性能测试** -- 始终与运维团队协调。

## 运行 k6 测试

```bash
# 基本运行
k6 run scripts/load-test.js

# 带环境变量
k6 run -e BASE_URL=https://staging.example.com scripts/load-test.js

# 输出到 JSON
k6 run --out json=results/output.json scripts/load-test.js

# 云输出 (k6 Cloud)
k6 cloud scripts/load-test.js

# InfluxDB 输出
k6 run --out influxdb=http://localhost:8086/k6 scripts/load-test.js

# 覆盖 VU 和持续时间
k6 run --vus 50 --duration 5m scripts/smoke-test.js
```

## 结果分析

测试运行后,分析这些关键指标:

- **http_req_duration** -- 响应时间分布(p50、p90、p95、p99)
- **http_req_failed** -- 失败请求的百分比
- **http_reqs** -- 总请求率(每秒请求数)
- **vus** -- 活动虚拟用户数
- **iterations** -- 完整测试迭代的次数
- **checks** -- 检查断言的通过/失败比率
- **data_received** / **data_sent** -- 网络吞吐量

寻找这些模式:
- 响应时间随 VU 增加而增加 = 容量限制
- 在特定 VU 计数时错误率激增 = 破坏点
- 浸泡测试期间内存逐渐增加 = 内存泄漏
- 响应时间 plateau 然后突然激增 = 线程池耗尽