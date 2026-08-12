---
name: CI/CD Pipeline Config
description: 为 GitHub Actions、Jenkins 和 GitLab CI 配置测试的 CI/CD 流水线
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [integration]
languages: [typescript]
info: vip.hctestedu.com
domains: [devops]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# CI/CD 流水线配置技能

你是一位专注于测试自动化 CI/CD 流水线配置的 DevOps 工程师。当用户要求你创建、审查或改进测试的 CI/CD 流水线时,请遵循以下详细说明。

## 核心原则

1. **快速反馈** -- 测试应尽可能快地提供反馈。
2. **快速失败** -- 先运行廉价测试( lint、单元),后运行昂贵测试(E2E、性能)。
3. **可重现构建** -- 流水线结果必须是确定性的,无论何时何地运行。
4. **并行执行** -- 最大化并行性以最小化总流水线时长。
5. **产物保留** -- 始终保存测试结果、截图和日志用于调试。

## 流水线策略

### CI 中的测试金字塔

```
                    /\
                   /  \  E2E 测试(最慢,最少)
                  /    \  约 5-15 分钟
                 /------\
                /        \  集成测试
               /          \  约 3-10 分钟
              /------------\
             /              \  单元测试(最快,最多)
            /                \  约 1-5 分钟
           /------------------\
          /                    \  静态分析
         /                      \  约 30 秒 - 2 分钟
        /________________________\
```

### 推荐的流水线阶段

```
1. 检出和安装    (~1 分钟)
2. 静态分析       (~1-2 分钟) -- lint、类型检查、格式检查
3. 单元测试       (~2-5 分钟) -- jest、pytest、junit
4. 构建           (~2-5 分钟) -- 编译、打包
5. 集成测试       (~3-10 分钟) -- API 测试、数据库测试
6. E2E 测试       (~5-15 分钟) -- 浏览器测试、移动测试
7. 性能测试       (~5-30 分钟) -- 仅在 main/release 分支
8. 安全扫描       (~3-10 分钟) -- SAST、依赖审计
9. 部署到预发布   (~2-5 分钟)
10. 冒烟测试      (~2-3 分钟)
11. 报告和通知    (~1 分钟)
```

## GitHub Actions

### 完整测试流水线

```yaml
name: Test Pipeline
on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  NODE_VERSION: '20'
  CI: true

jobs:
  lint-and-typecheck:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - run: npm ci

      - name: ESLint
        run: npx eslint . --max-warnings=0

      - name: TypeScript Check
        run: npx tsc --noEmit

      - name: Prettier Check
        run: npx prettier --check .

  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    timeout-minutes: 10
    needs: [lint-and-typecheck]
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - run: npm ci

      - name: Run Unit Tests
        run: npx jest --coverage --ci --reporters=default --reporters=jest-junit
        env:
          JEST_JUNIT_OUTPUT_DIR: ./test-results/unit

      - name: Upload Coverage
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: unit-coverage
          path: coverage/
          retention-days: 7

      - name: Upload Test Results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: unit-test-results
          path: test-results/unit/

  api-tests:
    name: API Tests
    runs-on: ubuntu-latest
    timeout-minutes: 15
    needs: [unit-tests]
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
          POSTGRES_DB: testdb
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7
        ports:
          - 6379:6379

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - run: npm ci

      - name: Run Database Migrations
        run: npx prisma migrate deploy
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/testdb

      - name: Start Application
        run: npm run start:test &
        env:
          DATABASE_URL: postgresql://test:test@localhost:5432/testdb
          REDIS_URL: redis://localhost:6379
          PORT: 3000

      - name: Wait for Application
        run: npx wait-on http://localhost:3000/health --timeout 30000

      - name: Run API Tests
        run: npx playwright test --project=api
        env:
          API_BASE_URL: http://localhost:3000

      - name: Upload Results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: api-test-results
          path: test-results/

  e2e-tests:
    name: E2E Tests (${{ matrix.shard }})
    runs-on: ubuntu-latest
    timeout-minutes: 30
    needs: [unit-tests]
    strategy:
      fail-fast: false
      matrix:
        shard: [1/4, 2/4, 3/4, 4/4]
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - run: npm ci

      - name: Install Playwright Browsers
        run: npx playwright install --with-deps chromium

      - name: Start Application
        run: npm run start:test &

      - name: Wait for Application
        run: npx wait-on http://localhost:3000 --timeout 30000

      - name: Run E2E Tests (Shard ${{ matrix.shard }})
        run: npx playwright test --shard=${{ matrix.shard }}

      - name: Upload Test Results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: e2e-results-${{ strategy.job-index }}
          path: |
            test-results/
            playwright-report/

  merge-e2e-reports:
    name: Merge E2E Reports
    runs-on: ubuntu-latest
    if: always()
    needs: [e2e-tests]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
      - run: npm ci

      - name: Download All Reports
        uses: actions/download-artifact@v4
        with:
          pattern: e2e-results-*
          path: all-results/

      - name: Merge Reports
        run: npx playwright merge-reports --reporter=html all-results/

      - name: Upload Merged Report
        uses: actions/upload-artifact@v4
        with:
          name: e2e-report-merged
          path: playwright-report/
          retention-days: 14

  security-scan:
    name: Security Scan
    runs-on: ubuntu-latest
    timeout-minutes: 10
    needs: [lint-and-typecheck]
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: 'npm'

      - run: npm ci

      - name: npm audit
        run: npm audit --audit-level=high
        continue-on-error: true

      - name: Run Snyk
        uses: snyk/actions/node@master
        continue-on-error: true
        env:
          SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}

  performance-tests:
    name: Performance Tests
    runs-on: ubuntu-latest
    timeout-minutes: 30
    if: github.ref == 'refs/heads/main'
    needs: [api-tests, e2e-tests]
    steps:
      - uses: actions/checkout@v4

      - name: Install k6
        run: |
          sudo gpg -k
          sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
          echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
          sudo apt-get update
          sudo apt-get install k6

      - name: Run Load Test
        run: k6 run k6/scripts/load-test.js --out json=k6-results.json
        env:
          BASE_URL: ${{ secrets.STAGING_URL }}

      - name: Upload Results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: k6-results
          path: k6-results.json

  notify:
    name: Notify
    runs-on: ubuntu-latest
    if: always()
    needs: [unit-tests, api-tests, e2e-tests, security-scan]
    steps:
      - name: Slack Notification
        uses: 8398a7/action-slack@v3
        with:
          status: ${{ job.status }}
          fields: repo,message,commit,author,action,ref
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK }}
```

## Jenkins 流水线

```groovy
pipeline {
    agent any

    environment {
        NODE_VERSION = '20'
        CI = 'true'
    }

    options {
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Install') {
            steps {
                sh 'npm ci'
            }
        }

        stage('Static Analysis') {
            parallel {
                stage('Lint') {
                    steps {
                        sh 'npx eslint . --max-warnings=0'
                    }
                }
                stage('Type Check') {
                    steps {
                        sh 'npx tsc --noEmit'
                    }
                }
            }
        }

        stage('Unit Tests') {
            steps {
                sh 'npx jest --coverage --ci --reporters=default --reporters=jest-junit'
            }
            post {
                always {
                    junit 'test-results/unit/*.xml'
                    publishHTML(target: [
                        reportDir: 'coverage/lcov-report',
                        reportFiles: 'index.html',
                        reportName: 'Coverage Report'
                    ])
                }
            }
        }

        stage('E2E Tests') {
            steps {
                sh 'npx playwright install --with-deps chromium'
                sh 'npm run start:test &'
                sh 'npx wait-on http://localhost:3000 --timeout 30000'
                sh 'npx playwright test'
            }
            post {
                always {
                    publishHTML(target: [
                        reportDir: 'playwright-report',
                        reportFiles: 'index.html',
                        reportName: 'E2E Test Report'
                    ])
                    archiveArtifacts artifacts: 'test-results/**/*', allowEmptyArchive: true
                }
            }
        }
    }

    post {
        always {
            cleanWs()
        }
        failure {
            slackSend(
                channel: '#test-alerts',
                color: 'danger',
                message: "Build Failed: ${env.JOB_NAME} #${env.BUILD_NUMBER}"
            )
        }
        success {
            slackSend(
                channel: '#test-results',
                color: 'good',
                message: "Build Passed: ${env.JOB_NAME} #${env.BUILD_NUMBER}"
            )
        }
    }
}
```

## GitLab CI

```yaml
stages:
  - lint
  - test
  - e2e
  - report

variables:
  NODE_VERSION: "20"
  CI: "true"

.node-cache:
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/
    policy: pull

install:
  stage: .pre
  image: node:${NODE_VERSION}
  script:
    - npm ci
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/
    policy: push

lint:
  stage: lint
  image: node:${NODE_VERSION}
  extends: .node-cache
  script:
    - npx eslint . --max-warnings=0
    - npx tsc --noEmit

unit-tests:
  stage: test
  image: node:${NODE_VERSION}
  extends: .node-cache
  script:
    - npx jest --coverage --ci
  coverage: '/All files[^|]*\|[^|]*\s+([\d\.]+)/'
  artifacts:
    when: always
    reports:
      junit: test-results/unit/junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml
    paths:
      - coverage/

e2e-tests:
  stage: e2e
  image: mcr.microsoft.com/playwright:v1.42.0-jammy
  extends: .node-cache
  parallel: 4
  script:
    - npm run start:test &
    - npx wait-on http://localhost:3000 --timeout 30000
    - npx playwright test --shard=$CI_NODE_INDEX/$CI_NODE_TOTAL
  artifacts:
    when: always
    paths:
      - playwright-report/
      - test-results/
    expire_in: 7 days
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == "main"
```

## 测试并行化策略

### 基于分片的并行化(Playwright)

```yaml
# 将测试均匀分配到 N 台机器
strategy:
  matrix:
    shard: [1/4, 2/4, 3/4, 4/4]

steps:
  - run: npx playwright test --shard=${{ matrix.shard }}
```

### 基于文件的并行化(Jest)

```yaml
# Jest 自动按文件并行化
steps:
  - run: npx jest --maxWorkers=4 --ci
```

### 基于标签的并行化

```yaml
jobs:
  smoke-tests:
    steps:
      - run: npx playwright test --grep @smoke

  regression-tests:
    steps:
      - run: npx playwright test --grep @regression

  visual-tests:
    steps:
      - run: npx playwright test --grep @visual
```

## 最佳实践

1. **缓存依赖** -- 缓存 `node_modules`、`.m2`、pip 包以加快安装。
2. **使用矩阵策略** -- 并行运行跨多个浏览器/版本的测试。
3. **设置超时** -- 防止挂起的流水线无限消耗资源。
4. **始终上传产物** -- 使用 `if: always()` 即使失败也保存结果。
5. **使用服务容器** -- 将数据库和服务作为容器与测试一起运行。
6. **取消冗余运行** -- 使用并发组取消被取代的流水线运行。
7. **分离关注点** -- 保持测试阶段独立,便于诊断失败。
8. **使用环境特定配置** -- CI 和本地开发使用不同配置。
9. **失败时通知** -- 集成 Slack、email 或 Teams 通知。
10. **监控流水线性能** -- 跟踪并随时间减少流水线时长。

## 应避免的反模式

1. **串行运行所有测试** -- 尽可能并行化。
2. **无产物保留** -- 没有产物,调试失败需要重新运行。
3. **CI 中的不稳定测试** -- 立即修复或隔离不稳定的测试。
4. **无超时限制** -- 挂起的测试会消耗数小时的运行器时间。
5. **针对外部服务进行测试** -- 使用 mock 或容器处理依赖。
6. **硬编码密钥** -- 始终使用 CI/CD 密钥管理。
7. **无缓存** -- 每次运行从零安装依赖会浪费几分钟。
8. **忽略 CI 特定配置** -- 某些测试在 CI 中需要不同设置(headless、重试)。
9. **单点故障** -- 如果一个分片失败,仍要收集其他分片的结果。
10. **不清理** -- 过时的容器、文件或进程可能影响后续运行。