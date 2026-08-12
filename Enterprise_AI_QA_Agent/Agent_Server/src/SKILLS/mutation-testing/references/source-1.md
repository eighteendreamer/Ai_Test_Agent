---
name: Mutation Testing
description: 使用 Stryker 进行变异测试，通过注入代码缺陷验证测试质量
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit, code-quality]
frameworks: [stryker]
info: vip.hctestedu.com
languages: [typescript, javascript, java]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# 变异测试

您是一位专注于变异测试的 QA 工程师。当用户要求您设置、编写或调试变异测试时，请遵循这些详细说明。

## 核心原则

1. **测试质量验证** -- 变异测试验证测试套件的有效性。
2. **缺陷注入** -- 故意注入代码缺陷（变异）来测试测试。
3. **杀灭变异体** -- 如果测试能检测到变异，说明测试有效。
4. **覆盖率补充** -- 传统覆盖率无法衡量测试质量，变异测试可以。
5. **持续改进** -- 根据变异测试结果改进测试套件。

## 什么是变异测试

变异测试通过以下步骤验证测试质量：
1. 对源代码进行微小修改（创建"变异体"）
2. 运行测试套件
3. 如果测试失败（检测到变异），说明测试有效
4. 如果测试通过（未能检测到变异），说明测试有漏洞

## 项目结构

```
project/
├── src/
│   ├── calculator.ts
│   └── string-utils.ts
├── tests/
│   ├── calculator.test.ts
│   └── string-utils.test.ts
├── stryker.conf.json
├── package.json
└── reports/
    └── mutation
```

## 安装和配置

### 安装

```bash
npm install --save-dev @stryker-mutator/core @stryker-mutator/jest-runner
# 或使用其他测试运行器
npm install --save-dev @stryker-mutator/vitest-runner
npm install --save-dev @stryker-mutator/mocha-runner
```

### TypeScript 配置

```json
// tsconfig.json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs",
    "strict": true,
    "esModuleInterop": true,
    "outDir": "./dist"
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
```

### Stryker 配置

```json
// stryker.conf.json
{
  "$schema": "./node_modules/@stryker-mutator/core/schema/stryker-schema.json",
  "testRunner": "jest",
  "reporters": ["html", "text", "clear-text"],
  "packageManager": "npm",
  "jest": {
    "projectType": "typescript",
    "configFile": "jest.config.js"
  },
  "mutate": [
    "src/**/*.ts",
    "!src/**/*.d.ts",
    "!src/**/__tests__/**"
  ],
  "threshold": {
    "high": 80,
    "low": 60,
    "break": 50
  },
  "timeout": 30000,
  "maxConcurrentTestRunners": 4
}
```

## 基本用法

### 运行变异测试

```bash
# 运行所有变异测试
npx stryker run

# 运行并生成 HTML 报告
npx stryker run --reporters html

# 运行并显示详细输出
npx stryker run --reporters clear-text

# 只运行特定文件的变异测试
npx stryker run --mutate src/calculator.ts
```

### Jest 配置

```javascript
// jest.config.js
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/tests'],
  testMatch: ['**/*.test.ts'],
  collectCoverageFrom: [
    'src/**/*.ts',
    '!src/**/*.d.ts',
  ],
};
```

## 变异算子

### 1. 条件突变

```typescript
// 原始代码
function isAdult(age: number): boolean {
  return age >= 18;
}

// 可能的变异
// age >= 18  -> age > 18
// age >= 18  -> age <= 18
// age >= 18  -> age < 18
```

### 2. 边界突变

```typescript
// 原始代码
if (index < array.length) { ... }

// 可能的变异
// index < array.length -> index <= array.length
// index < array.length -> index >= array.length
```

### 3. 逻辑突变

```typescript
// 原始代码
if (isValid && hasPermission) { ... }

// 可能的变异
// isValid && hasPermission -> isValid || hasPermission
// isValid && hasPermission -> !isValid && hasPermission
// isValid && hasPermission -> isValid && !hasPermission
```

### 4. 算数突变

```typescript
// 原始代码
const total = price * quantity;

// 可能的变异
// price * quantity -> price + quantity
// price * quantity -> price - quantity
// price * quantity -> price / quantity
```

### 5. 返回值突变

```typescript
// 原始代码
function getStatus() { return 'active'; }

// 可能的变异
// return 'active' -> return 'inactive'
// return 'active' -> return null
```

## 解释变异测试结果

### 突变体状态

- **Survived（存活）** -- 测试未能检测到代码变更，测试有漏洞
- **Killed（被杀灭）** -- 测试成功检测到代码变更，测试有效
- **Timeout（超时）** -- 测试运行超时
- **No Coverage（无覆盖）** -- 代码没有被任何测试覆盖

### 解读报告

```
Mutation Testing Results:
======================

Total Mutants: 150
Killed: 130 (86.7%)
Survived: 15 (10.0%)
Timeout: 3 (2.0%)
No Coverage: 2 (1.3%)

Mutation Score: 86.7%
Status: PASS (Above 80% threshold)

Survived Mutants:
-----------------
1. src/calculator.ts:15 - Changed > to >= (Survived)
   - isAdult(17) still returns false

2. src/string-utils.ts:23 - Changed + to - (Survived)
   - Concatenation still produces valid result
```

## 测试改进

### 发现漏洞时的修复

```typescript
// 假设变异测试发现这个测试有漏洞
describe('isAdult', () => {
  it('should return true for age 18', () => {
    const result = isAdult(18);
    // 修复：添加边界值测试
    expect(result).toBe(true);
  });

  it('should return false for age 17', () => {
    const result = isAdult(17);
    expect(result).toBe(false);
  });

  // 修复后添加
  it('should return false for age 16', () => {
    expect(isAdult(16)).toBe(false);
  });
});
```

### 改进测试质量

```typescript
// 原始测试（弱）
describe('StringUtils', () => {
  it('should truncate string', () => {
    expect(truncate('Hello World', 5)).toBe('Hello...');
  });
});

// 改进后的测试（强）
describe('StringUtils', () => {
  it('should truncate string at specified length', () => {
    expect(truncate('Hello World', 5)).toBe('Hello...');
    expect(truncate('Hi', 5)).toBe('Hi');  // 不需要截断
  });

  it('should handle empty string', () => {
    expect(truncate('', 5)).toBe('');
  });

  it('should handle exact length string', () => {
    expect(truncate('Hello', 5)).toBe('Hello');
  });

  it('should handle length of zero', () => {
    expect(truncate('Hello', 0)).toBe('...');
  });
});
```

## CI/CD 集成

```yaml
name: Mutation Tests
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  mutation-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'

      - name: Install dependencies
        run: npm ci

      - name: Run mutation tests
        run: npx stryker run

      - name: Upload mutation report
        uses: actions/upload-artifact@v4
        with:
          name: mutation-report
          path: reports/mutation/**/*.html

      - name: Check mutation score
        run: |
          SCORE=$(cat reports/mutation/score.json | jq '.mutationScore')
          echo "Mutation Score: $SCORE%"
          if (( $(echo "$SCORE < 80" | bc -l) )); then
            echo "Mutation score below threshold!"
            exit 1
          fi
```

## Java 配置

```xml
<!-- pom.xml -->
<dependencies>
    <dependency>
        <groupId>org.junit.jupiter</groupId>
        <artifactId>junit-jupiter</artifactId>
        <version>5.10.1</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.assertj</groupId>
        <artifactId>assertj-core</artifactId>
        <version>3.24.2</version>
        <scope>test</scope>
    </dependency>
</dependencies>

<build>
    <plugins>
        <plugin>
            <groupId>org.pitest</groupId>
            <artifactId>pitest-maven</artifactId>
            <version>1.15.0</version>
        </plugin>
    </plugins>
</build>
```

```xml
<!-- pitest 配置 -->
<plugin>
    <groupId>org.pitest</groupId>
    <artifactId>pitest-maven</artifactId>
    <configuration>
        <targetClasses>
            <param>com.example.myapp.*</param>
        </targetClasses>
        <targetTests>
            <param>com.example.myapp.*Test</param>
        </targetTests>
        <mutationOperators>
            <mutationOperator>RETURN_VALS</mutationOperator>
            <mutationOperator>NEGATE_CONDITIONALS</mutationOperator>
            <mutationOperator>REMOVE_CONDITIONALS</mutationOperator>
        </mutationOperators>
    </configuration>
</plugin>
```

## 最佳实践

1. **从关键模块开始** -- 先对核心业务逻辑进行变异测试。
2. **设置合理阈值** -- 80% 是常见的最低标准。
3. **关注 Survived 变异体** -- 它们暴露了测试漏洞。
4. **定期运行** -- 在 CI 中集成变异测试。
5. **增量测试** -- 优先测试新代码和修改过的代码。
6. **平衡速度和覆盖率** -- 使用 maxConcurrentTestRunners 优化。
7. **排除不需要测试的代码** -- 如配置、常量等。
8. **理解变异算子** -- 选择适合项目的算子。

## 应避免的反模式

1. **跳过 Survived 变异体** -- 它们是测试漏洞的信号。
2. **过低的阈值** -- 低于 60% 的分数意味着测试无效。
3. **变异整个代码库** -- 从关键模块开始。
4. **忽略超时** -- 超时可能表明性能问题。
5. **不更新测试** -- 发现漏洞后应该修复测试。
6. **过度关注分数** -- 质量比数字更重要。
7. **忽略变异体上下文** -- 理解为什么变异体存活。
8. **测试过于具体** -- 过于具体的测试容易被绕过。