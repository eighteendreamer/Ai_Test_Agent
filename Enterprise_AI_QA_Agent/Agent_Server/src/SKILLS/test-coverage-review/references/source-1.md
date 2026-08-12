---
name: Test Coverage Gap Finder
description: 使用覆盖率分析、风险映射和变更追踪识别未测试代码路径、未覆盖分支和缺失测试场景
version: 1.0.0
author: Pramod
license: MIT
testingTypes: [code-quality, unit]
frameworks: [jest, vitest, pytest]
info: vip.hctestedu.com
languages: [typescript, javascript, python, java]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt, gemini-cli, amp]
---

# 测试覆盖率漏洞发现器

测试覆盖率分析识别代码库的哪些部分被测试套件执行，更重要的是，哪些部分没有被执行。原始覆盖率百分比在没有上下文的情况下具有误导性：80% 的语句覆盖率可能意味着最关键的错误处理路径完全没有测试，而简单的 getter 被充分覆盖。本技能指导 AI 编码代理进行超出百分比的全面覆盖率漏洞分析，识别高风险未测试代码、强制新变更的覆盖率，以及生成可操作的测试建议。

## 核心原则

1. **覆盖率是诊断工具，不是目标**：高覆盖率不保证测试质量。一个没有有意义的断言就执行每一行的测试提供零缺陷检测能力的覆盖率。使用覆盖率发现缺失的内容，而不是作为质量的证明。

2. **分支覆盖率优于语句覆盖率**：语句覆盖率计算一行是否执行；分支覆盖率计算每个条件判断的真假路径是否都执行。一个有早期返回的函数可以有 100% 语句覆盖率但只有 50% 分支覆盖率，如果只测试了一条路径的话。

3. **风险加权覆盖率**：并非所有代码都携带相同风险。支付处理、认证和数据验证应该达到 100% 覆盖率。配置常量和简单的数据传输对象不需要。优先考虑业务风险方面的覆盖率漏洞。

4. **变更追踪覆盖率是不可妥协的**：追踪新代码和修改代码的覆盖率确保每个变更都附带测试。遗留代码覆盖率漏洞是继承的，但新漏洞是可以预防的。

5. **死代码不是覆盖率漏洞**：在生产中从未达到的代码不是需要测试的未测试路径；它是需要删除的死代码。在编写测试之前区分未测试的活代码和真正无法到达的代码。

6. **覆盖率趋势比快照更重要**：70% 覆盖率且在改进中的代码库比 85% 覆盖率但在下降中的更健康。随时间追踪覆盖率以在问题变得严重之前检测侵蚀。

7. **排除不属于的内容**：生成的代码、供应商库、类型定义和配置文件会膨胀或压低覆盖率数字而不提供信号。排除它们以保持覆盖率指标有意义。

## 项目结构

```
project-root/
├── src/
│   ├── controllers/
│   │   ├── user.controller.ts
│   │   └── order.controller.ts
│   ├── services/
│   │   ├── payment.service.ts
│   │   └── notification.service.ts
│   ├── utils/
│   │   ├── validators.ts
│   │   └── formatters.ts
│   └── types/
│       └── index.ts
├── tests/
│   ├── unit/
│   │   ├── payment.test.ts
│   │   └── validators.test.ts
│   └── integration/
│       └── order-flow.test.ts
├── coverage/
│   ├── lcov.info
│   ├── coverage-summary.json
│   └── html/
│       └── index.html
├── scripts/
│   ├── coverage-gap-analysis.ts
│   ├── change-coverage.ts
│   ├── risk-coverage-map.ts
│   └── coverage-trend.ts
├── .nycrc.json
├── jest.config.ts
├── vitest.config.ts
└── coverage.config.ts
```

## Istanbul/V8 覆盖率分析

### 配置 Jest 覆盖率

```typescript
// jest.config.ts
import type { Config } from 'jest';

const config: Config = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  collectCoverage: true,
  coverageProvider: 'v8', // V8 对 Node.js 更快更准确

  // 覆盖率收集目标
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',           // 排除类型定义
    '!src/**/index.ts',          // 排除 barrel 文件
    '!src/types/**',             // 排除仅类型的文件
    '!src/**/*.stories.{ts,tsx}', // 排除 Storybook 故事
    '!src/**/mocks/**',          // 排除测试 mock
    '!src/generated/**',         // 排除生成的代码
  ],

  // 覆盖率输出格式
  coverageReporters: [
    'text',           // 控制台摘要
    'text-summary',   // 简要控制台摘要
    'lcov',           // 用于 CI 工具（SonarQube、Codecov）
    'json-summary',   // 机器可读摘要
    'json',           // 详细的每文件数据
    'html',           // 交互式 HTML 报告
    'clover',         // Clover XML 格式
  ],

  // 覆盖率阈值
  coverageThreshold: {
    global: {
      branches: 80,
      functions: 85,
      lines: 85,
      statements: 85,
    },
    // 关键路径的每目录阈值
    './src/services/payment*.ts': {
      branches: 95,
      functions: 100,
      lines: 95,
      statements: 95,
    },
    './src/utils/validators.ts': {
      branches: 100,
      functions: 100,
      lines: 100,
      statements: 100,
    },
  },

  coverageDirectory: 'coverage',
};

export default config;
```

### 配置 Vitest 覆盖率

```typescript
// vitest.config.ts
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    coverage: {
      provider: 'v8',
      enabled: true,

      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.d.ts',
        'src/types/**',
        'src/**/*.test.{ts,tsx}',
        'src/**/*.spec.{ts,tsx}',
        'src/generated/**',
        'src/**/index.ts',
        'node_modules/**',
      ],

      // 报告格式
      reporter: ['text', 'json-summary', 'lcov', 'html'],
      reportsDirectory: './coverage',

      // 阈值
      thresholds: {
        branches: 80,
        functions: 85,
        lines: 85,
        statements: 85,
      },

      // 如果阈值未满足则使测试运行失败
      thresholdAutoUpdate: false,

      // 在控制台输出中显示未覆盖的行
      all: true, // 包含零覆盖率的文件
    },
  },
});
```

### 配置 pytest 覆盖率

```ini
# pytest.ini 或 pyproject.toml [tool.pytest.ini_options]
[pytest]
addopts =
    --cov=src
    --cov-report=term-missing
    --cov-report=html:coverage/html
    --cov-report=json:coverage/coverage.json
    --cov-report=lcov:coverage/lcov.info
    --cov-branch
    --cov-fail-under=80
```

```python
# pyproject.toml
[tool.coverage.run]
source = ["src"]
branch = true
omit = [
    "src/types/*",
    "src/generated/*",
    "src/**/test_*.py",
    "src/**/__init__.py",
]

[tool.coverage.report]
fail_under = 80
show_missing = true
skip_covered = false
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    "@overload",
]

[tool.coverage.html]
directory = "coverage/html"
```

## 分支 vs 语句 vs 函数覆盖率

理解覆盖率类型之间的差异对于准确的漏洞分析至关重要。

```typescript
// src/services/payment.service.ts
export class PaymentService {
  async processPayment(amount: number, method: string): Promise<PaymentResult> {
    // 语句：这是第 1 行
    if (amount <= 0) {
      // 分支 A (true)：金额无效
      throw new PaymentError('Invalid amount');
    }
    // 分支 A (false)：金额有效 - 向下执行

    // 语句：这是第 2 行
    if (method === 'credit_card') {
      // 分支 B (true)：信用卡路径
      return this.processCreditCard(amount);
    } else if (method === 'paypal') {
      // 分支 C (true)：PayPal 路径
      return this.processPayPal(amount);
    } else {
      // 分支 D (默认)：不支持的方法
      throw new PaymentError(`Unsupported payment method: ${method}`);
    }
  }
}

// 测试：只测试信用卡快乐路径
describe('PaymentService', () => {
  it('processes credit card payment', async () => {
    const service = new PaymentService();
    const result = await service.processPayment(100, 'credit_card');
    expect(result.status).toBe('success');
  });
});

// 覆盖率分析：
// 语句覆盖率：~60%（第 1-2 行执行，但 PayPal 和错误路径未执行）
// 分支覆盖率：~33%（只有分支 A-false 和分支 B-true）
// 函数覆盖率：~33%（processPayment 被调用，但 processPayPal 没有）
// 漏洞：负数金额、PayPal 路径、不支持的方法路径
```

### 全面漏洞分析脚本

```typescript
// scripts/coverage-gap-analysis.ts
import * as fs from 'fs';
import * as path from 'path';

interface CoverageEntry {
  path: string;
  statementMap: Record<string, { start: Location; end: Location }>;
  s: Record<string, number>;       // 语句命中计数
  branchMap: Record<string, { type: string; loc: Location; locations: Location[] }>;
  b: Record<string, number[]>;     // 每个分支的分支命中计数
  fnMap: Record<string, { name: string; loc: Location; decl: Location }>;
  f: Record<string, number>;       // 函数命中计数
}

interface Location {
  line: number;
  column: number;
}

interface CoverageGap {
  file: string;
  type: 'statement' | 'branch' | 'function';
  location: { startLine: number; endLine: number };
  description: string;
  riskLevel: 'critical' | 'high' | 'medium' | 'low';
  suggestion: string;
}

function analyzeCoverageGaps(coverageJsonPath: string): CoverageGap[] {
  const coverageData: Record<string, CoverageEntry> = JSON.parse(
    fs.readFileSync(coverageJsonPath, 'utf-8')
  );

  const gaps: CoverageGap[] = [];

  for (const [filePath, entry] of Object.entries(coverageData)) {
    const relativePath = path.relative(process.cwd(), filePath);

    // 找到未覆盖的语句
    for (const [stmtId, hitCount] of Object.entries(entry.s)) {
      if (hitCount === 0) {
        const loc = entry.statementMap[stmtId];
        gaps.push({
          file: relativePath,
          type: 'statement',
          location: { startLine: loc.start.line, endLine: loc.end.line },
          description: `Uncovered statement at line ${loc.start.line}`,
          riskLevel: assessRisk(relativePath, loc.start.line),
          suggestion: `Add a test that exercises the code path at line ${loc.start.line}`,
        });
      }
    }

    // 找到未覆盖的分支
    for (const [branchId, hitCounts] of Object.entries(entry.b)) {
      const branchInfo = entry.branchMap[branchId];
      hitCounts.forEach((count, index) => {
        if (count === 0) {
          const loc = branchInfo.locations[index] || branchInfo.loc;
          const branchType = index === 0 ? 'true' : 'false';
          gaps.push({
            file: relativePath,
            type: 'branch',
            location: { startLine: loc.line, endLine: loc.line },
            description: `Uncovered ${branchType} branch of ${branchInfo.type} at line ${branchInfo.loc.line}`,
            riskLevel: assessRisk(relativePath, loc.line),
            suggestion: `Add a test for the ${branchType} path of the ${branchInfo.type} conditional at line ${branchInfo.loc.line}`,
          });
        }
      });
    }

    // 找到未覆盖的函数
    for (const [fnId, hitCount] of Object.entries(entry.f)) {
      if (hitCount === 0) {
        const fnInfo = entry.fnMap[fnId];
        gaps.push({
          file: relativePath,
          type: 'function',
          location: { startLine: fnInfo.loc.start.line, endLine: fnInfo.loc.end.line },
          description: `Uncovered function "${fnInfo.name}" at line ${fnInfo.loc.start.line}`,
          riskLevel: assessRisk(relativePath, fnInfo.loc.start.line),
          suggestion: `Add tests for the "${fnInfo.name}" function covering its main paths`,
        });
      }
    }
  }

  return gaps.sort((a, b) => {
    const riskOrder = { critical: 0, high: 1, medium: 2, low: 3 };
    return riskOrder[a.riskLevel] - riskOrder[b.riskLevel];
  });
}

function assessRisk(filePath: string, line: number): 'critical' | 'high' | 'medium' | 'low' {
  // 关键：支付、认证、安全
  if (/payment|billing|charge|refund/i.test(filePath)) return 'critical';
  if (/auth|login|session|token|password/i.test(filePath)) return 'critical';
  if (/security|encrypt|decrypt|hash/i.test(filePath)) return 'critical';

  // 高：数据验证、API 控制器
  if (/valid|sanitiz|controller|handler/i.test(filePath)) return 'high';
  if (/service/i.test(filePath)) return 'high';

  // 中：工具、辅助函数
  if (/util|helper|format/i.test(filePath)) return 'medium';

  // 低：配置、常量、类型
  if (/config|constant|type|interface/i.test(filePath)) return 'low';

  return 'medium';
}

// 运行分析
const gaps = analyzeCoverageGaps('coverage/coverage-final.json');

console.log(`\nCoverage Gap Analysis Report`);
console.log(`${'='.repeat(60)}`);
console.log(`Total gaps found: ${gaps.length}`);
console.log(`  Critical: ${gaps.filter((g) => g.riskLevel === 'critical').length}`);
console.log(`  High: ${gaps.filter((g) => g.riskLevel === 'high').length}`);
console.log(`  Medium: ${gaps.filter((g) => g.riskLevel === 'medium').length}`);
console.log(`  Low: ${gaps.filter((g) => g.riskLevel === 'low').length}`);

console.log(`\nTop Priority Gaps:`);
gaps.slice(0, 20).forEach((gap, i) => {
  console.log(`  ${i + 1}. [${gap.riskLevel.toUpperCase()}] ${gap.file}:${gap.location.startLine}`);
  console.log(`     ${gap.description}`);
  console.log(`     Suggestion: ${gap.suggestion}`);
});

fs.writeFileSync('coverage/gap-analysis.json', JSON.stringify(gaps, null, 2));
```

## 基于变更的覆盖率

基于变更的覆盖率追踪新添加或修改的代码是否被测试覆盖。这是最可操作的覆盖率执行形式，因为它在不要求对遗留代码进行追溯测试的情况下防止新漏洞。

```typescript
// scripts/change-coverage.ts
import { execSync } from 'child_process';
import * as fs from 'fs';

interface ChangedLine {
  file: string;
  line: number;
  type: 'added' | 'modified';
}

interface ChangeCoverageResult {
  totalChangedLines: number;
  coveredLines: number;
  uncoveredLines: ChangedLine[];
  coveragePercentage: number;
}

function getChangedLines(baseBranch: string = 'main'): ChangedLine[] {
  const diffOutput = execSync(`git diff ${baseBranch}...HEAD --unified=0 --diff-filter=AM`, {
    encoding: 'utf-8',
  });

  const changedLines: ChangedLine[] = [];
  let currentFile = '';

  for (const line of diffOutput.split('\n')) {
    // 匹配文件头
    const fileMatch = line.match(/^\+\+\+ b\/(.+)$/);
    if (fileMatch) {
      currentFile = fileMatch[1];
      continue;
    }

    // 匹配块头：@@ -oldStart,oldCount +newStart,newCount @@
    const hunkMatch = line.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/);
    if (hunkMatch) {
      const startLine = parseInt(hunkMatch[1], 10);
      const lineCount = parseInt(hunkMatch[2] || '1', 10);

      // 只追踪源文件，不是测试
      if (
        currentFile.match(/\.(ts|tsx|js|jsx|py|java)$/) &&
        !currentFile.match(/\.(test|spec|__test__|_test)\./i) &&
        !currentFile.includes('__mocks__')
      ) {
        for (let i = 0; i < lineCount; i++) {
          changedLines.push({
            file: currentFile,
            line: startLine + i,
            type: 'added',
          });
        }
      }
    }
  }

  return changedLines;
}

function checkChangeCoverage(baseBranch: string = 'main'): ChangeCoverageResult {
  const changedLines = getChangedLines(baseBranch);

  if (changedLines.length === 0) {
    console.log('No source file changes detected.');
    return { totalChangedLines: 0, coveredLines: 0, uncoveredLines: [], coveragePercentage: 100 };
  }

  // 加载覆盖率数据
  const coverageData = JSON.parse(
    fs.readFileSync('coverage/coverage-final.json', 'utf-8')
  );

  const uncoveredLines: ChangedLine[] = [];
  let coveredCount = 0;

  for (const change of changedLines) {
    const absolutePath = `${process.cwd()}/${change.file}`;
    const fileCoverage = coverageData[absolutePath];

    if (!fileCoverage) {
      // 文件完全没有覆盖率数据
      uncoveredLines.push(change);
      continue;
    }

    // 检查这一特定行是否被覆盖
    let lineCovered = false;
    for (const [stmtId, stmtLoc] of Object.entries(fileCoverage.statementMap)) {
      const loc = stmtLoc as any;
      if (change.line >= loc.start.line && change.line <= loc.end.line) {
        if (fileCoverage.s[stmtId] > 0) {
          lineCovered = true;
          break;
        }
      }
    }

    if (lineCovered) {
      coveredCount++;
    } else {
      uncoveredLines.push(change);
    }
  }

  const result: ChangeCoverageResult = {
    totalChangedLines: changedLines.length,
    coveredLines: coveredCount,
    uncoveredLines,
    coveragePercentage:
      changedLines.length > 0 ? (coveredCount / changedLines.length) * 100 : 100,
  };

  return result;
}

// 运行基于变更的覆盖率检查
const result = checkChangeCoverage(process.argv[2] || 'main');

console.log('\nChange-Based Coverage Report');
console.log('='.repeat(50));
console.log(`Changed lines: ${result.totalChangedLines}`);
console.log(`Covered: ${result.coveredLines}`);
console.log(`Uncovered: ${result.uncoveredLines.length}`);
console.log(`Coverage: ${result.coveragePercentage.toFixed(1)}%`);

if (result.uncoveredLines.length > 0) {
  console.log('\nUncovered changed lines:');
  const byFile = new Map<string, number[]>();
  for (const line of result.uncoveredLines) {
    if (!byFile.has(line.file)) byFile.set(line.file, []);
    byFile.get(line.file)!.push(line.line);
  }
  for (const [file, lines] of byFile) {
    console.log(`  ${file}: lines ${lines.join(', ')}`);
  }
}

// 强制最低变更覆盖率
const MIN_CHANGE_COVERAGE = 90;
if (result.coveragePercentage < MIN_CHANGE_COVERAGE) {
  console.error(
    `\nFAILED: Change coverage ${result.coveragePercentage.toFixed(1)}% is below minimum ${MIN_CHANGE_COVERAGE}%`
  );
  process.exit(1);
} else {
  console.log(`\nPASSED: Change coverage meets minimum threshold of ${MIN_CHANGE_COVERAGE}%`);
}
```

### 用于变更覆盖率的 GitHub Actions 集成

```yaml
# .github/workflows/coverage-check.yml
name: Coverage Check
on:
  pull_request:
    branches: [main]

jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0   # 用于 git diff 的完整历史
      - uses: actions/setup-node@v4
        with:
          node-version: 20
      - run: npm ci
      - run: npm test -- --coverage
      - name: Check change-based coverage
        run: npx ts-node scripts/change-coverage.ts origin/main

      - name: Comment coverage on PR
        uses: actions/github-script@v7
        if: always()
        with:
          script: |
            const fs = require('fs');
            const summary = JSON.parse(fs.readFileSync('coverage/coverage-summary.json', 'utf-8'));
            const total = summary.total;

            const body = `## Coverage Report
            | Metric | Coverage | Threshold |
            |--------|----------|-----------|
            | Statements | ${total.statements.pct}% | 85% |
            | Branches | ${total.branches.pct}% | 80% |
            | Functions | ${total.functions.pct}% | 85% |
            | Lines | ${total.lines.pct}% | 85% |`;

            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body,
            });
```

## 每模块覆盖率追踪

```typescript
// scripts/module-coverage.ts
import * as fs from 'fs';
import * as path from 'path';

interface ModuleCoverage {
  module: string;
  statements: { total: number; covered: number; percentage: number };
  branches: { total: number; covered: number; percentage: number };
  functions: { total: number; covered: number; percentage: number };
  files: number;
  risk: string;
}

function analyzeModuleCoverage(): ModuleCoverage[] {
  const summaryData = JSON.parse(
    fs.readFileSync('coverage/coverage-summary.json', 'utf-8')
  );

  const modules = new Map<string, ModuleCoverage>();

  for (const [filePath, data] of Object.entries(summaryData)) {
    if (filePath === 'total') continue;

    const relativePath = path.relative(process.cwd(), filePath);
    const parts = relativePath.split(path.sep);

    // 从路径提取模块（例如，src/services -> services）
    const moduleName = parts.length >= 2 ? `${parts[0]}/${parts[1]}` : parts[0];

    if (!modules.has(moduleName)) {
      modules.set(moduleName, {
        module: moduleName,
        statements: { total: 0, covered: 0, percentage: 0 },
        branches: { total: 0, covered: 0, percentage: 0 },
        functions: { total: 0, covered: 0, percentage: 0 },
        files: 0,
        risk: '',
      });
    }

    const mod = modules.get(moduleName)!;
    const fileData = data as any;

    mod.statements.total += fileData.statements.total;
    mod.statements.covered += fileData.statements.covered;
    mod.branches.total += fileData.branches.total;
    mod.branches.covered += fileData.branches.covered;
    mod.functions.total += fileData.functions.total;
    mod.functions.covered += fileData.functions.covered;
    mod.files++;
  }

  // 计算百分比并分配风险
  for (const mod of modules.values()) {
    mod.statements.percentage = safeDivide(mod.statements.covered, mod.statements.total);
    mod.branches.percentage = safeDivide(mod.branches.covered, mod.branches.total);
    mod.functions.percentage = safeDivide(mod.functions.covered, mod.functions.total);

    const avgCoverage =
      (mod.statements.percentage + mod.branches.percentage + mod.functions.percentage) / 3;

    if (avgCoverage < 50) mod.risk = 'CRITICAL';
    else if (avgCoverage < 70) mod.risk = 'HIGH';
    else if (avgCoverage < 85) mod.risk = 'MEDIUM';
    else mod.risk = 'LOW';
  }

  return [...modules.values()].sort(
    (a, b) => a.branches.percentage - b.branches.percentage
  );
}

function safeDivide(numerator: number, denominator: number): number {
  return denominator === 0 ? 100 : Math.round((numerator / denominator) * 10000) / 100;
}

const modules = analyzeModuleCoverage();

console.log('\nModule Coverage Report');
console.log('='.repeat(80));
console.log(
  `${'Module'.padEnd(30)} ${'Stmts'.padStart(8)} ${'Branch'.padStart(8)} ${'Funcs'.padStart(8)} ${'Risk'.padStart(10)}`
);
console.log('-'.repeat(80));

for (const mod of modules) {
  console.log(
    `${mod.module.padEnd(30)} ${(mod.statements.percentage + '%').padStart(8)} ${(mod.branches.percentage + '%').padStart(8)} ${(mod.functions.percentage + '%').padStart(8)} ${mod.risk.padStart(10)}`
  );
}
```

## 死代码 vs 未测试代码

```typescript
// scripts/dead-code-detector.ts
import * as fs from 'fs';
import { execSync } from 'child_process';

interface DeadCodeCandidate {
  file: string;
  functionName: string;
  line: number;
  reason: 'no-references' | 'no-exports' | 'unreachable-branch';
  confidence: 'high' | 'medium' | 'low';
}

/**
 * 区分死代码（应该被删除）和
 * 未测试代码（需要测试）。使用覆盖率
 * 数据和静态分析的组合。
 */
function detectDeadCode(): DeadCodeCandidate[] {
  const coverageData = JSON.parse(
    fs.readFileSync('coverage/coverage-final.json', 'utf-8')
  );

  const candidates: DeadCodeCandidate[] = [];

  for (const [filePath, entry] of Object.entries(coverageData)) {
    const fileEntry = entry as any;
    const relativePath = filePath.replace(process.cwd() + '/', '');

    // 检查每个未覆盖的函数
    for (const [fnId, hitCount] of Object.entries(fileEntry.f)) {
      if ((hitCount as number) > 0) continue;

      const fnInfo = fileEntry.fnMap[fnId];
      const fnName = fnInfo.name || 'anonymous';

      // 检查函数是否在代码库中的任何地方被引用
      try {
        const grepResult = execSync(
          `grep -rn "${fnName}" src/ --include="*.ts" --include="*.tsx" -l 2>/dev/null || true`,
          { encoding: 'utf-8' }
        ).trim();

        const references = grepResult
          .split('\n')
          .filter((line) => line && !line.includes('.test.') && !line.includes('.spec.'));

        if (references.length <= 1) {
          // 只定义，从未在其他地方引用
          candidates.push({
            file: relativePath,
            functionName: fnName,
            line: fnInfo.loc.start.line,
            reason: 'no-references',
            confidence: 'high',
          });
        }
      } catch {
        // grep 失败，跳过
      }
    }
  }

  return candidates;
}

const deadCode = detectDeadCode();
console.log(`\nDead Code Candidates: ${deadCode.length}`);
deadCode.forEach((dc) => {
  console.log(`  [${dc.confidence}] ${dc.file}:${dc.line} - ${dc.functionName} (${dc.reason})`);
});
```

## 覆盖率趋势分析

```typescript
// scripts/coverage-trend.ts
import * as fs from 'fs';

interface CoverageSnapshot {
  date: string;
  commit: string;
  branch: string;
  statements: number;
  branches: number;
  functions: number;
  lines: number;
  totalFiles: number;
  totalStatements: number;
}

const TREND_FILE = 'coverage/trend-history.json';

function recordSnapshot(): void {
  const summary = JSON.parse(
    fs.readFileSync('coverage/coverage-summary.json', 'utf-8')
  );

  const { execSync } = require('child_process');
  const commit = execSync('git rev-parse --short HEAD', { encoding: 'utf-8' }).trim();
  const branch = execSync('git rev-parse --abbrev-ref HEAD', { encoding: 'utf-8' }).trim();

  const snapshot: CoverageSnapshot = {
    date: new Date().toISOString(),
    commit,
    branch,
    statements: summary.total.statements.pct,
    branches: summary.total.branches.pct,
    functions: summary.total.functions.pct,
    lines: summary.total.lines.pct,
    totalFiles: Object.keys(summary).length - 1, // 排除 'total'
    totalStatements: summary.total.statements.total,
  };

  // 加载现有历史
  let history: CoverageSnapshot[] = [];
  if (fs.existsSync(TREND_FILE)) {
    history = JSON.parse(fs.readFileSync(TREND_FILE, 'utf-8'));
  }

  history.push(snapshot);
  fs.writeFileSync(TREND_FILE, JSON.stringify(history, null, 2));

  // 分析趋势
  if (history.length >= 2) {
    const previous = history[history.length - 2];
    const current = snapshot;

    console.log('\nCoverage Trend');
    console.log('='.repeat(50));
    console.log(`Statements: ${current.statements}% (${delta(current.statements, previous.statements)})`);
    console.log(`Branches:   ${current.branches}% (${delta(current.branches, previous.branches)})`);
    console.log(`Functions:  ${current.functions}% (${delta(current.functions, previous.functions)})`);
    console.log(`Lines:      ${current.lines}% (${delta(current.lines, previous.lines)})`);

    // 在覆盖率下降时警告
    if (current.branches < previous.branches) {
      console.warn(`\nWARNING: Branch coverage decreased from ${previous.branches}% to ${current.branches}%`);
    }
  }
}

function delta(current: number, previous: number): string {
  const diff = current - previous;
  if (diff > 0) return `+${diff.toFixed(1)}%`;
  if (diff < 0) return `${diff.toFixed(1)}%`;
  return 'no change';
}

recordSnapshot();
```

## 覆盖率驱动的测试生成建议

```typescript
// scripts/suggest-tests.ts
import * as fs from 'fs';

interface TestSuggestion {
  file: string;
  functionName: string;
  line: number;
  uncoveredBranches: string[];
  suggestedTestCases: string[];
  priority: number;
}

function generateTestSuggestions(): TestSuggestion[] {
  const coverageData = JSON.parse(
    fs.readFileSync('coverage/coverage-final.json', 'utf-8')
  );
  const suggestions: TestSuggestion[] = [];

  for (const [filePath, entry] of Object.entries(coverageData)) {
    const fileEntry = entry as any;
    const relativePath = filePath.replace(process.cwd() + '/', '');
    const sourceCode = fs.readFileSync(filePath, 'utf-8').split('\n');

    // 分析未覆盖的分支
    for (const [branchId, hitCounts] of Object.entries(fileEntry.b)) {
      const counts = hitCounts as number[];
      const branchInfo = fileEntry.branchMap[branchId];
      const uncoveredIndices = counts
        .map((count, idx) => (count === 0 ? idx : -1))
        .filter((idx) => idx >= 0);

      if (uncoveredIndices.length === 0) continue;

      // 读取分支周围的源代码以理解上下文
      const branchLine = branchInfo.loc.start.line - 1;
      const contextLines = sourceCode.slice(
        Math.max(0, branchLine - 2),
        Math.min(sourceCode.length, branchLine + 3)
      );
      const context = contextLines.join('\n');

      const suggestedCases: string[] = [];

      // 从分支类型和上下文推断测试用例
      if (branchInfo.type === 'if') {
        if (context.includes('null') || context.includes('undefined')) {
          suggestedCases.push('Test with null/undefined input');
        }
        if (context.includes('.length') || context.includes('Array.isArray')) {
          suggestedCases.push('Test with empty array');
          suggestedCases.push('Test with populated array');
        }
        if (context.includes('> 0') || context.includes('< 0') || context.includes('=== 0')) {
          suggestedCases.push('Test with zero value');
          suggestedCases.push('Test with negative value');
          suggestedCases.push('Test with positive value');
        }
        if (context.includes('throw') || context.includes('Error')) {
          suggestedCases.push('Test error throwing condition');
        }
      }

      if (suggestedCases.length === 0) {
        uncoveredIndices.forEach((idx) => {
          suggestedCases.push(`Test the ${idx === 0 ? 'true' : 'false'} branch at line ${branchInfo.loc.start.line}`);
        });
      }

      suggestions.push({
        file: relativePath,
        functionName: findContainingFunction(fileEntry.fnMap, branchInfo.loc.start.line),
        line: branchInfo.loc.start.line,
        uncoveredBranches: uncoveredIndices.map((idx) => `Branch ${idx}`),
        suggestedTestCases: suggestedCases,
        priority: assessTestPriority(relativePath),
      });
    }
  }

  return suggestions.sort((a, b) => b.priority - a.priority);
}

function findContainingFunction(fnMap: Record<string, any>, line: number): string {
  for (const fn of Object.values(fnMap)) {
    if (line >= fn.loc.start.line && line <= fn.loc.end.line) {
      return fn.name || 'anonymous';
    }
  }
  return 'unknown';
}

function assessTestPriority(filePath: string): number {
  if (/payment|auth|security/i.test(filePath)) return 10;
  if (/service|controller/i.test(filePath)) return 7;
  if (/validator|sanitiz/i.test(filePath)) return 8;
  if (/util|helper/i.test(filePath)) return 5;
  return 3;
}

const suggestions = generateTestSuggestions();
console.log('\nTest Generation Suggestions');
console.log('='.repeat(60));
suggestions.slice(0, 15).forEach((s, i) => {
  console.log(`\n${i + 1}. ${s.file} - ${s.functionName}() [line ${s.line}]`);
  console.log(`   Uncovered: ${s.uncoveredBranches.join(', ')}`);
  s.suggestedTestCases.forEach((tc) => console.log(`   -> ${tc}`));
});
```

## 配置

### 排除生成的和供应商代码

```json
// .nycrc.json（Node.js 项目的 Istanbul 配置）
{
  "all": true,
  "check-coverage": true,
  "branches": 80,
  "functions": 85,
  "lines": 85,
  "statements": 85,
  "include": ["src/**/*.ts"],
  "exclude": [
    "src/**/*.d.ts",
    "src/**/*.test.ts",
    "src/**/*.spec.ts",
    "src/generated/**",
    "src/types/**",
    "src/**/__mocks__/**",
    "src/**/test-utils/**",
    "src/**/*.stories.tsx",
    "src/migrations/**",
    "src/seeds/**",
    "node_modules/**"
  ],
  "reporter": ["text", "lcov", "json-summary", "html"],
  "report-dir": "coverage"
}
```

### Package.json 脚本

```json
{
  "scripts": {
    "test": "vitest run",
    "test:coverage": "vitest run --coverage",
    "coverage:gaps": "ts-node scripts/coverage-gap-analysis.ts",
    "coverage:changes": "ts-node scripts/change-coverage.ts",
    "coverage:modules": "ts-node scripts/module-coverage.ts",
    "coverage:trend": "ts-node scripts/coverage-trend.ts",
    "coverage:suggest": "ts-node scripts/suggest-tests.ts",
    "coverage:dead-code": "ts-node scripts/dead-code-detector.ts",
    "coverage:report": "npm run test:coverage && npm run coverage:gaps && npm run coverage:modules"
  }
}
```

## 最佳实践

1. **在 CI 中强制基于变更的覆盖率。** 要求新代码和修改代码至少达到 90% 覆盖率。这可以防止覆盖率侵蚀，而不要求对遗留代码进行追溯覆盖率。

2. **为关键代码设置每模块阈值。** 支付、认证和数据验证模块应该有更高的覆盖率要求（95%+），而不是工具或配置模块。

3. **使用分支覆盖率作为主要指标。** 语句覆盖率太粗。一个有四个 if 语句的函数可以显示 75% 语句覆盖率，同时只测试了 16 条可能路径中的一条。

4. **在拉取请求中审查覆盖率报告。** 自动将覆盖率摘要和变更覆盖率发布为 PR 注释，以便审核者在批准前看到漏洞。

5. **随时间追踪覆盖率趋势。** 每次合并到 main 时记录覆盖率快照。当覆盖率比测量之间下降超过 1% 时发出警报。

6. **排除不应测试的文件。** 类型定义、barrel 导出、生成代码和迁移文件应从覆盖率收集中排除，以防止指标扭曲。

7. **在编写测试之前识别死代码。** 在覆盖率漏洞分析之前运行死代码检测。不要在为应该删除的代码编写测试上浪费精力。

8. **使用 all 标志包含零覆盖率的文件。** 默认情况下，大多数覆盖率工具只报告在测试期间导入的文件。`all` 标志确保具有零测试导入的文件以 0% 覆盖率出现。

9. **合并多种测试类型的覆盖率。** 合并来自单元测试、集成测试和 E2E 测试的覆盖率报告以获得完整画面。E2E 测试覆盖的行不需要冗余单元测试。

10. **专注于未覆盖的错误路径。** 错误处理代码（catch 块、错误响应、验证失败）经常未测试，但这是最常隐藏 bug 的地方。优先覆盖错误路径。

11. **设置现实的初始阈值并逐步提高。** 如果当前覆盖率是 60%，不要立即将阈值设置为 85%。从 60% 开始，防止回归，并随着覆盖率提高提高阈值。

12. **使覆盖率报告对整个团队可访问。** 托管 HTML 覆盖率报告，以便所有团队成员可以浏览。覆盖率漏洞是团队责任，而不仅仅是作者的责任。

## 应避免的反模式

1. **追逐 100% 覆盖率作为虚荣指标。** 实现 100% 覆盖率通常需要测试琐碎的代码（getter、常量、类型守卫），同时提供递减的回报。专注于风险加权的覆盖率。

2. **编写无断言的测试来提高覆盖率。** 调用函数而不断言结果的测试提高覆盖率数字而不捕获 bug。每个测试必须断言有意义的行为。

3. **使用覆盖率 pragma 隐藏漏洞。** `/* istanbul ignore next */` pragma 对于真正无法到达的代码有合法用途，但使用它来压制可测试代码上的警告是玩弄指标。

4. **只测量语句覆盖率。** 语句覆盖率错过未测试的分支，特别是在有早期返回、三元运算符和短路评估的代码中。始终测量分支覆盖率。

5. **将覆盖率作为测试设计的替代品。** 高覆盖率与糟糕的断言比中等覆盖率与深思熟虑的断言捕获更少的 bug。覆盖率指导在哪里测试；测试设计决定测试什么。

6. **忽略错误处理代码的覆盖率。** 错误路径通常测试最少但 bug 最多。如果 try-catch 块的 catch 分支显示 0% 覆盖率，那个错误处理从未被验证。

7. **对所有代码应用统一阈值。** 配置文件和类型定义不应该与业务逻辑有相同的阈值。使用每目录或每文件阈值来反映实际风险。

## 调试技巧

1. **使用 HTML 覆盖率报告进行可视化漏洞识别。** 交互式 HTML 报告将覆盖的行显示为绿色，未覆盖的行显示为红色。这是识别文件中特定漏洞的最快方法。

2. **检查测试是否实际导入了源文件。** 如果文件显示 0% 覆盖率，验证至少有一个测试导入它。除非设置了 `all` 标志，否则覆盖率工具只能跟踪在测试执行期间加载的文件。

3. **验证覆盖率提供者匹配运行时。** V8 覆盖率最适合 Node.js 应用程序。Istanbul 更适合用 Babel 转译的浏览器目标代码。不匹配的提供者产生不准确的报告。

4. **检查条件语句中未覆盖的 else 分支。** 最常见的覆盖率漏洞是隐式的 else 分支。一个没有 else 的 `if (condition) { ... }` 有两个分支，但通常只测试 true 分支。

5. **检查阈值配置优先级。** 每文件阈值覆盖全局阈值。如果文件尽管看起来充分测试但持续失败覆盖率，检查每文件阈值是否设置得比全局高。

6. **检查 coverage-final.json 获取详细数据。** JSON 覆盖率报告包含每个语句、分支和函数的精确命中计数。当 HTML 报告不足时，使用此数据进行编程分析。

7. **在开发期间以 watch 模式运行覆盖率。** 使用 `vitest --coverage --watch` 或 `jest --coverage --watchAll` 可以在编写测试时实时查看覆盖率变化。

8. **合并并行测试运行的覆盖率。** 如果测试在并行分片中运行，每个分片产生部分覆盖率。使用 `istanbul-merge` 或 `nyc merge` 在分析漏洞之前合并报告。

9. **验证 source maps 是正确的。** 在测试转译代码时，错误的 source maps 导致覆盖率归因于错误的行。确保构建工具为覆盖率报告生成准确的 source maps。
