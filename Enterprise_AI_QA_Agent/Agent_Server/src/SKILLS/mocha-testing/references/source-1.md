---
name: Mocha Testing
description: Node.js JavaScript 测试框架，支持 TDD 和 BDD 风格的单元和集成测试
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit, integration]
frameworks: [mocha]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Mocha 测试

您是一位专注于 Mocha 测试框架的 QA 工程师。当用户要求您编写、审查或调试 Mocha 测试时，请遵循这些详细说明。

## 核心原则

1. **BDD/TDD 双模式** -- 支持行为驱动开发和测试驱动开发风格。
2. **异步测试支持** -- 原生支持 Promise 和 async/await。
3. **灵活的断言** -- 可搭配 Chai、Expect.js 等断言库。
4. **可扩展性** -- 支持钩子函数和自定义报告。
5. **测试隔离** -- 每个测试文件在独立进程中运行。

## 项目结构

```
project/
├── src/
│   ├── calculator.js
│   └── user.js
├── test/
│   ├── unit/
│   │   ├── calculator.test.js
│   │   └── user.test.js
│   ├── integration/
│   │   └── api.test.js
│   ├── helpers/
│   │   └── setup.js
│   └── mocha.opts
├── package.json
└── .mocharc.json
```

## 安装和配置

### 安装

```bash
npm install --save-dev mocha chai @types/mocha
```

### package.json 配置

```json
{
  "scripts": {
    "test": "mocha",
    "test:watch": "mocha --watch",
    "test:coverage": "nyc npm run test"
  },
  "mocha": {
    "spec": "test/**/*.test.js",
    "timeout": 5000,
    "slow": 100,
    "exit": true
  }
}
```

### .mocharc.json 配置

```json
{
  "spec": ["test/**/*.test.js"],
  "timeout": 5000,
  "slow": 100,
  "exit": true,
  "require": ["ts-node/register", "should"],
  "bail": true,
  "reporter": "spec",
  "extensions": ["js", "ts"],
  "ignore": ["test/**/*.skip.js"]
}
```

## BDD 风格测试

```javascript
// test/unit/calculator.test.js
const { describe, it, before, beforeEach, after, afterEach } = require('mocha');
const assert = require('assert');
const Calculator = require('../../src/calculator');

describe('Calculator', () => {
  let calculator;

  // 在所有测试前执行一次
  before(() => {
    console.log('Calculator tests starting...');
  });

  // 每个测试前创建新实例
  beforeEach(() => {
    calculator = new Calculator();
  });

  describe('add()', () => {
    it('should return sum of two positive numbers', () => {
      assert.strictEqual(calculator.add(2, 3), 5);
    });

    it('should handle negative numbers', () => {
      assert.strictEqual(calculator.add(-2, 3), 1);
    });

    it('should handle zero', () => {
      assert.strictEqual(calculator.add(5, 0), 5);
    });
  });

  describe('divide()', () => {
    it('should divide two numbers correctly', () => {
      assert.strictEqual(calculator.divide(10, 2), 5);
    });

    it('should throw error when dividing by zero', () => {
      assert.throws(() => {
        calculator.divide(10, 0);
      }, /Division by zero/);
    });
  });
});
```

## TDD 风格测试

```javascript
// test/unit/calculator.tdd.test.js
const { suite, test, setup, teardown } = require('mocha');
const assert = require('assert');
const Calculator = require('../../src/calculator');

suite('Calculator (TDD)', () => {
  let calculator;

  setup(() => {
    calculator = new Calculator();
  });

  suite('#add()', () => {
    test('should return sum of two positive numbers', () => {
      assert.strictEqual(calculator.add(2, 3), 5);
    });

    test('should handle negative numbers', () => {
      assert.strictEqual(calculator.add(-2, 3), 1);
    });
  });

  suite('#divide()', () => {
    test('should divide two numbers correctly', () => {
      assert.strictEqual(calculator.divide(10, 2), 5);
    });

    test('should throw error when dividing by zero', () => {
      test('', () => {
        assert.throws(() => {
          calculator.divide(10, 0);
        }, /Division by zero/);
      });
    });
  });
});
```

## 异步测试

### Callback 风格

```javascript
describe('Async Operations', () => {
  it('should fetch user data', (done) => {
    fetchUser(123, (error, user) => {
      if (error) {
        done(error);
        return;
      }
      assert.strictEqual(user.id, 123);
      done();
    });
  });
});
```

### Promise 风格

```javascript
describe('Async Operations', () => {
  it('should fetch user data', () => {
    return fetchUser(123).then(user => {
      assert.strictEqual(user.id, 123);
    });
  });
});
```

### async/await 风格

```javascript
describe('Async Operations', () => {
  it('should fetch user data', async () => {
    const user = await fetchUser(123);
    assert.strictEqual(user.id, 123);
  });

  it('should handle API errors', async () => {
    try {
      await fetchUser(999);
      assert.fail('Should have thrown error');
    } catch (error) {
      assert.strictEqual(error.message, 'User not found');
    }
  });
});
```

## 钩子函数

```javascript
describe('Hooks Demo', () => {
  // 在所有测试前执行一次
  before(() => {
    // 设置测试环境
  });

  // 在所有测试后执行一次
  after(() => {
    // 清理测试环境
  });

  // 每个测试前执行
  beforeEach(() => {
    // 重置测试数据
  });

  // 每个测试后执行
  afterEach(() => {
    // 清理测试产生的副作用
  });

  it('test 1', () => { /* ... */ });
  it('test 2', () => { /* ... */ });
});
```

## 动态测试

```javascript
describe('Dynamic Tests', () => {
  // 从数据生成测试
  const testCases = [
    { input: [2, 3], expected: 5 },
    { input: [0, 0], expected: 0 },
    { input: [-1, 1], expected: 0 },
    { input: [100, 200], expected: 300 },
  ];

  testCases.forEach(({ input, expected }) => {
    it(`should add ${input[0]} + ${input[1]} = ${expected}`, () => {
      const calculator = new Calculator();
      assert.strictEqual(calculator.add(input[0], input[1]), expected);
    });
  });
});
```

## 跳过和.only

```javascript
describe('Skipping Tests', () => {
  // 跳过整个测试套件
  describe.skip('skipped suite', () => {
    it('will not run', () => {
      assert.strictEqual(1, 2);
    });
  });

  // 跳过单个测试
  it('this test will be skipped', () => {
    // 不会执行
  }).skip();

  // 或者使用 this.skip()
  it('another skipped test', function() {
    this.skip();
  });
});

describe('Focused Tests', () => {
  // 只运行这个测试（其他测试被忽略）
  it.only('this test will run', () => {
    assert.strictEqual(1, 1);
  });

  it('this test will be ignored', () => {
    assert.strictEqual(1, 2);
  });
});
```

## 断言库集成

### Chai 断言

```javascript
const { expect, should } = require('chai');

// 使用 expect
describe('Chai Assertions', () => {
  it('should use expect syntax', () => {
    expect(1 + 1).to.equal(2);
    expect('hello').to.have.lengthOf(5);
    expect({ name: 'John' }).to.have.property('name');
    expect([1, 2, 3]).to.include(2);
  });

  it('should handle null checks', () => {
    expect(null).to.be.null;
    expect(undefined).to.be.undefined;
  });

  it('should handle async errors', async () => {
    await expect(Promise.reject(new Error('failed'))).to.be.rejectedWith('failed');
  });
});

// 使用 should
describe('Chai Should Syntax', () => {
  beforeEach(() => {
    should();
  });

  it('should use should syntax', () => {
    const user = { name: 'John', age: 30 };
    user.name.should.equal('John');
    user.age.should.be.above(18);
  });
});
```

## 集成测试

```javascript
// test/integration/api.test.js
const { describe, it, before, beforeEach, after } = require('mocha');
const request = require('supertest');
const { app } = require('../../src/app');
const { db } = require('../../src/db');

describe('User API Integration Tests', () => {
  // 测试前清空并填充数据库
  before(async () => {
    await db.connect();
    await db.seed({ users: [] });
  });

  after(async () => {
    await db.disconnect();
  });

  describe('GET /api/users', () => {
    it('should return empty array when no users', async () => {
      const response = await request(app)
        .get('/api/users')
        .expect(200);

      expect(response.body).to.deep.equal([]);
    });

    it('should return users after creation', async () => {
      // 创建用户
      await request(app)
        .post('/api/users')
        .send({ name: 'John', email: 'john@example.com' })
        .expect(201);

      // 获取用户列表
      const response = await request(app)
        .get('/api/users')
        .expect(200);

      expect(response.body).to.have.lengthOf(1);
      expect(response.body[0].name).to.equal('John');
    });
  });

  describe('POST /api/users', () => {
    it('should create a new user', async () => {
      const response = await request(app)
        .post('/api/users')
        .send({
          name: 'Jane',
          email: 'jane@example.com',
          password: 'SecurePass123!'
        })
        .expect(201);

      expect(response.body).to.have.property('id');
      expect(response.body.name).to.equal('Jane');
      expect(response.body).to.not.have.property('password');
    });

    it('should reject invalid email', async () => {
      const response = await request(app)
        .post('/api/users')
        .send({
          name: 'John',
          email: 'invalid-email'
        })
        .expect(400);

      expect(response.body).to.have.property('error');
    });
  });
});
```

## 测试数据管理

```javascript
// test/helpers/test-data.js
const faker = require('faker');

class TestDataFactory {
  static createUser(overrides = {}) {
    return {
      id: faker.datatype.uuid(),
      name: faker.name.findName(),
      email: faker.internet.email(),
      age: faker.datatype.number({ min: 18, max: 100 }),
      ...overrides,
    };
  }

  static createUsers(count = 5) {
    return Array.from({ length: count }, () => this.createUser());
  }

  static createProduct(overrides = {}) {
    return {
      id: faker.datatype.uuid(),
      name: faker.commerce.productName(),
      price: parseFloat(faker.commerce.price()),
      category: faker.commerce.department(),
      ...overrides,
    };
  }
}

module.exports = TestDataFactory;
```

```javascript
// test/unit/user.test.js
const TestDataFactory = require('../helpers/test-data');

describe('User Model', () => {
  it('should create user with factory', () => {
    const user = TestDataFactory.createUser();
    expect(user.name).to.be.a('string');
    expect(user.email).to.include('@');
  });

  it('should override factory defaults', () => {
    const user = TestDataFactory.createUser({ name: 'Custom Name' });
    expect(user.name).to.equal('Custom Name');
  });
});
```

## CI/CD 集成

```yaml
name: Mocha Tests
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

      - name: Run tests
        run: npm run test

      - name: Run with coverage
        run: npm run test:coverage

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          files: ./coverage/lcov.info
```

## 最佳实践

1. **描述性测试名称** -- 测试名称应该清晰描述预期行为。
2. **AAA 模式** -- Arrange（准备）、Act（执行）、Assert（断言）。
3. **单一职责** -- 每个测试只验证一个行为。
4. **测试隔离** -- 避免测试之间的依赖。
5. **适当的超时** -- 设置合理的测试超时。
6. **清理副作用** -- 在 afterEach 中清理测试数据。
7. **使用 beforeEach** -- 每个测试前重置状态。
8. **异步测试正确处理** -- 使用 async/await 或正确处理 Promise。

## 应避免的反模式

1. **过度使用 beforeAll** -- 可能导致测试状态污染。
2. **测试实现细节** -- 应该测试行为而非实现。
3. **断言过于具体** -- 避免脆弱的断言。
4. **忽略异步测试** -- 确保异步代码正确测试。
5. **测试间共享状态** -- 导致 flaky 测试。
6. **过长的测试** -- 拆分成多个小测试。
7. **不使用描述性名称** -- 测试名称应该自解释。
8. **忽略错误处理测试** -- 测试成功路径也要测试错误路径。