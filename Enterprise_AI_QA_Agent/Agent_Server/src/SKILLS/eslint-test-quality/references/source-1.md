---
name: ESLint Testing
description: ESLint 测试规则配置，测试专用规则和自定义规则的编写
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [code-quality, unit]
frameworks: [eslint]
languages: [typescript, javascript]
info: vip.hctestedu.com
domains: [web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# ESLint 测试规则

您是一位专注于测试 ESLint 规则配置的 QA 工程师。当用户要求您编写、审查或调试测试规则时，请遵循这些详细说明。

## 核心原则

1. **测试即文档** -- 测试用例清晰展示规则预期行为。
2. **全面覆盖** -- 测试应覆盖所有有效和无效场景。
3. **清晰的错误信息** -- 错误消息应该准确描述问题。
4. **可维护性** -- 规则变更应该易于追踪和管理。
5. **自动化验证** -- 在 CI/CD 中自动验证规则。

## 测试框架

### 使用 Mocha + Chai 测试 ESLint 规则

```bash
npm install --save-dev mocha chai @eslint/js typescript-eslint
```

### 项目结构

```
eslint-rules/
├── rules/
│   ├── no-todo-without-issue.js
│   ├── require-error-handling.js
│   └── no-console-in-production.js
├── tests/
│   ├── rules/
│   │   ├── no-todo-without-issue.test.js
│   │   ├── require-error-handling.test.js
│   │   └── no-console-in-production.test.js
│   └── fixtures/
│       ├── valid-code.js
│       └── invalid-code.js
├── lib/
│   └── tester.js
├── .eslintrc.js
├── package.json
└── mocha.opts
```

## RuleTester 基础

### 基本规则测试

```javascript
const { RuleTester } = require('eslint');
const rule = require('../rules/no-todo-without-issue');

const ruleTester = new RuleTester({
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
});

describe('no-todo-without-issue', () => {
  ruleTester.run('no-todo-without-issue', rule, {
    valid: [
      // 有效的代码 - 不应该有错误
      {
        code: '// TODO: Implement feature XY-123',
        filename: 'src/feature.js',
      },
      {
        code: '// FIXME: Fix race condition',
        filename: 'src/bug.js',
      },
      {
        code: '// HACK: Workaround for browser bug',
        filename: 'src/utils.js',
      },
    ],
    invalid: [
      // 无效的代码 - 应该报告错误
      {
        code: '// TODO: Implement feature',
        filename: 'src/feature.js',
        errors: [
          {
            messageId: 'missingIssue',
            data: { type: 'TODO', pattern: 'TODO' },
          },
        ],
      },
      {
        code: '// FIXME without issue number',
        filename: 'src/bug.js',
        errors: [
          {
            messageId: 'missingIssue',
            data: { type: 'FIXME', pattern: 'FIXME' },
          },
        ],
      },
    ],
  });
});
```

### 带上下文的规则

```javascript
// rules/require-error-handling.js
module.exports = {
  meta: {
    type: 'problem',
    docs: {
      description: 'Require error handling in async functions',
      category: 'Best Practices',
    },
    fixable: 'code',
    schema: [
      {
        type: 'object',
        properties: {
          allowEmptyCatch: { type: 'boolean' },
        },
        additionalProperties: false,
      },
    ],
  },
  create(context) {
    const options = context.options[0] || {};
    const allowEmptyCatch = options.allowEmptyCatch ?? false;

    return {
      TryStatement(node) {
        if (!node.handler) return;

        const catchClause = node.handler;
        const catchBody = catchClause.body;

        // 检查 catch 块是否为空
        if (catchBody.body.length === 0) {
          if (!allowEmptyCatch) {
            context.report({
              node: catchClause,
              messageId: 'unexpectedEmptyCatch',
            });
          }
          return;
        }

        // 检查是否记录错误
        const hasConsoleError = catchBody.body.some(
          (stmt) =>
            stmt.type === 'ExpressionStatement' &&
            stmt.expression.type === 'CallExpression' &&
            stmt.expression.callee.object?.name === 'console' &&
            stmt.expression.callee.property.name === 'error'
        );

        const hasLoggerError = catchBody.body.some(
          (stmt) =>
            stmt.type === 'ExpressionStatement' &&
            stmt.expression.type === 'CallExpression' &&
            (stmt.expression.callee.object?.name === 'logger' ||
             stmt.expression.callee.object?.name === 'log') &&
            stmt.expression.callee.property.name === 'error'
        );

        const hasThrow = catchBody.body.some(
          (stmt) => stmt.type === 'ThrowStatement'
        );

        if (!hasConsoleError && !hasLoggerError && !hasThrow) {
          context.report({
            node: catchClause,
            messageId: 'missingErrorHandling',
          });
        }
      },
    };
  },
};
```

### 测试带选项的规则

```javascript
const { RuleTester } = require('eslint');
const rule = require('../rules/require-error-handling');

const ruleTester = new RuleTester({
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
});

describe('require-error-handling', () => {
  ruleTester.run('require-error-handling', rule, {
    valid: [
      // 默认选项 - 不允许空 catch
      {
        code: `
          try {
            await doSomething();
          } catch (e) {
            console.error(e);
          }
        `,
      },
      // 允许空 catch
      {
        code: `
          try {
            await doSomething();
          } catch (e) {
            // ignore
          }
        `,
        options: [{ allowEmptyCatch: true }],
      },
      // 使用 logger
      {
        code: `
          try {
            await doSomething();
          } catch (e) {
            logger.error(e);
          }
        `,
      },
      // 重新抛出错误
      {
        code: `
          try {
            await doSomething();
          } catch (e) {
            throw new CustomError(e);
          }
        `,
      },
    ],
    invalid: [
      // 空 catch（默认不允许）
      {
        code: `
          try {
            await doSomething();
          } catch (e) {}
        `,
        errors: [{ messageId: 'missingErrorHandling' }],
      },
      // 空 catch（明确禁用）
      {
        code: `
          try {
            await doSomething();
          } catch (e) {}
        `,
        options: [{ allowEmptyCatch: false }],
        errors: [{ messageId: 'unexpectedEmptyCatch' }],
      },
      // 没有错误处理
      {
        code: `
          try {
            await doSomething();
          } catch (e) {
            const foo = 'bar';
          }
        `,
        errors: [{ messageId: 'missingErrorHandling' }],
      },
    ],
  });
});
```

## Fixtures 测试

### 使用代码片段测试

```javascript
// tests/fixtures/valid-scenarios.js
const { RuleTester } = require('eslint');
const rule = require('../rules/no-console-in-production');

const ruleTester = new RuleTester({
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
});

// 从文件加载代码片段
const fs = require('fs');
const path = require('path');

const validFixtures = fs.readdirSync('tests/fixtures/valid')
  .filter(f => f.endsWith('.js'))
  .map(f => ({
    code: fs.readFileSync(path.join('tests/fixtures/valid', f), 'utf8'),
    filename: `src/${f}`,
  }));

const invalidFixtures = fs.readdirSync('tests/fixtures/invalid')
  .filter(f => f.endsWith('.js'))
  .map(f => ({
    code: fs.readFileSync(path.join('tests/fixtures/invalid', f), 'utf8'),
    filename: `src/${f}`,
    errors: [{ messageId: 'unexpectedConsole' }],
  }));

describe('no-console-in-production', () => {
  ruleTester.run('no-console-in-production', rule, {
    valid: [
      ...validFixtures,
      // 开发环境允许 console
      {
        code: 'console.log("debug");',
        filename: 'src/dev.js',
      },
    ],
    invalid: [
      ...invalidFixtures,
      // 生产环境不允许 console
      {
        code: 'console.log("debug");',
        filename: 'src/prod.js',
        errors: [{ messageId: 'unexpectedConsole' }],
      },
    ],
  });
});
```

## AST 测试

### 验证 AST 结构

```javascript
const { RuleTester } = require('eslint');
const rule = require('../rules/no-naked-pointers');

const ruleTester = new RuleTester({
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
});

describe('no-naked-pointers', () => {
  ruleTester.run('no-naked-pointers', rule, {
    valid: [
      // 安全的指针操作
      {
        code: `
          const user = getUser();
          if (user != null) {
            console.log(user.name);
          }
        `,
      },
      {
        code: `
          const users = [];
          for (const user of users) {
            processUser(user);
          }
        `,
      },
    ],
    invalid: [
      {
        code: 'console.log(user.name);',
        errors: [
          {
            messageId: 'nakedPointer',
            data: { name: 'user' },
          },
        ],
      },
    ],
  });

  // 额外的 AST 验证
  it('should detect naked pointer in nested property access', () => {
    const { RuleTester } = require('eslint');
    const rule = require('../rules/no-naked-pointers');

    const tester = new RuleTester({ parserOptions: { ecmaVersion: 2022 } });

    tester.run('no-naked-pointers', rule, {
      valid: [],
      invalid: [
        {
          code: 'return obj.nested.value;',
          errors: [{ messageId: 'nakedPointer' }],
        },
      ],
    });
  });
});
```

## 集成测试

### 测试整个规则集

```javascript
// tests/integration/eslint-rules.test.js
const { ESLint } = require('eslint');

describe('ESLint Rules Integration', () => {
  let eslint;

  beforeAll(async () => {
    eslint = new ESLint({
      useEslintrc: true,
      overrideConfigFile: '.eslintrc.test.js',
    });
  });

  it('should pass valid code', async () => {
    const results = await eslint.lintFiles(['tests/fixtures/valid/*.js']);

    const errors = results.filter(r => r.errorCount > 0);
    expect(errors).toHaveLength(0);
  });

  it('should fail invalid code', async () => {
    const results = await eslint.lintFiles(['tests/fixtures/invalid/*.js']);

    const hasErrors = results.some(r => r.errorCount > 0);
    expect(hasErrors).toBe(true);
  });

  it('should report correct line numbers', async () => {
    const results = await eslint.lintText(`
      function foo() {
        TODO: fix this
      }
    `);

    expect(results[0].messages).toHaveLength(1);
    expect(results[0].messages[0].line).toBe(2);
  });
});
```

## 快照测试

### 使用 jest-snapshot

```javascript
// tests/rules/snapshot.test.js
const { RuleTester } = require('eslint');
const rule = require('../rules/comprehensive-coverage');

const ruleTester = new RuleTester({
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
});

describe('comprehensive-coverage rule snapshots', () => {
  it('should match snapshot for typical scenarios', () => {
    const results = ruleTester.run('comprehensive-coverage', rule, {
      valid: [
        { code: 'const x = 1;' },
      ],
      invalid: [
        {
          code: 'async function test() { return 1; }',
          errors: 1,
        },
      ],
    });

    expect(results).toMatchSnapshot();
  });
});
```

## CI/CD 集成

```yaml
name: ESLint Rules Tests
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

      - name: Run unit tests for rules
        run: npm run test:rules

      - name: Run integration tests
        run: npm run test:integration

      - name: Check rule documentation
        run: npm run docs:check

      - name: Upload coverage
        uses: codecov/codecov-action@v4
```

## 最佳实践

1. **每个规则单独测试文件** -- 便于追踪和管理。
2. **使用 messageId** -- 便于测试和国际化。
3. **测试有效和无效场景** -- 确保规则行为正确。
4. **使用真实代码片段** -- 测试真实使用场景。
5. **文档化规则选项** -- 在测试中展示所有选项。
6. **性能测试** -- 确保规则不会显著影响 ESLint 性能。
7. **可访问性测试** -- 确保错误消息清晰易懂。
8. **回归测试** -- 确保规则变更不破坏现有行为。

## 应避免的反模式

1. **硬编码行号** -- 使用相对位置而非绝对行号。
2. **只测试有效代码** -- 必须同时测试无效代码。
3. **忽略选项测试** -- 所有选项都需要测试。
4. **复杂测试设置** -- 保持测试简单和清晰。
5. **不测试边界情况** -- 测试 null、undefined 等边界情况。
6. **不清理测试状态** -- 每个测试应该独立。
7. **忽略性能** -- 避免 O(n²) 或更差的复杂度。
8. **不更新快照** -- 规则变更时及时更新快照。