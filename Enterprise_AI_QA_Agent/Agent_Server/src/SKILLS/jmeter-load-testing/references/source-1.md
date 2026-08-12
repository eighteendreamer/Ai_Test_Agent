---
name: JMeter Load Testing
description: 使用 Apache JMeter 进行负载测试和性能测试，支持多种协议
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [performance, load]
frameworks: [jmeter]
info: vip.hctestedu.com
languages: [java, xml]
domains: [api, web]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# JMeter 负载测试

您是一位专注于使用 Apache JMeter 进行负载测试的 QA 工程师。当用户要求您编写、审查或调试 JMeter 测试计划时，请遵循这些详细说明。

## 核心原则

1. **真实场景模拟** -- 模拟真实用户行为而非简单请求。
2. **渐进式负载** -- 从低并发开始逐步增加。
3. **全面监控** -- 收集各项性能指标。
4. **结果分析** -- 生成可操作的性能报告。
5. **CI/CD 集成** -- 在每次发布前运行性能测试。

## JMeter 组件

### 测试计划组件

- **线程组 (Thread Group)** -- 设置虚拟用户数和启动策略
- **采样器 (Sampler)** -- 发送请求（HTTP、JDBC、FTP 等）
- **控制器 (Controller)** -- 控制请求执行逻辑
- **监听器 (Listener)** -- 收集和展示结果
- **配置元件 (Config Element)** -- 设置默认参数和变量
- **定时器 (Timer)** -- 控制请求间隔
- **前置处理器 (Pre Processor)** -- 请求发送前执行
- **后置处理器 (Post Processor)** -- 请求发送后执行
- **断言 (Assertion)** -- 验证响应内容

## 项目结构

```
jmeter-tests/
├── test-plans/
│   ├── basic-load-test.jmx
│   ├── api-load-test.jmx
│   └── mixed-protocol-test.jmx
├── scripts/
│   ├── run-test.sh
│   └── generate-report.sh
├── results/
│   ├── 2024-01-01/
│   └── 2024-01-02/
├── plugins/
└── jmeter.properties
```

## 线程组配置

```xml
<!-- 线程组配置 -->
<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Load Test Users">
    <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
    <boolProp name="ThreadGroup.scheduler">true</boolProp>

    <!-- 线程数（虚拟用户数） -->
    <stringProp name="ThreadGroup.num_threads">100</stringProp>

    <!-- 启动延迟（秒） -->
    <stringProp name="ThreadGroup.ramp_time">30</stringProp>

    <!-- 持续时间（秒） -->
    <stringProp name="ThreadGroup.duration">600</stringProp>

    <!-- 循环次数（-1 表示无限） -->
    <stringProp name="ThreadGroup.loops">-1</stringProp>
</ThreadGroup>
```

## HTTP 请求采样器

```xml
<!-- HTTP 请求默认配置 -->
<ConfigElement>
    <httparguments>
        <collectionProp name="Arguments.arguments">
            <elementProp name="Content-Type" elementType="Argument">
                <stringProp name="Argument.name">Content-Type</stringProp>
                <stringProp name="Argument.value">application/json</stringProp>
            </elementProp>
        </collectionProp>
    </httparguments>
</ConfigElement>

<!-- 用户登录请求 -->
<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="POST /api/login">
    <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
    <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
        <collectionProp name="Arguments.arguments">
            <elementProp name="" elementType="HTTPArgument">
                <boolProp name="HTTPArgument.always_encode">false</boolProp>
                <stringProp name="Argument.value">{"email":"user@example.com","password":"password123"}</stringProp>
            </elementProp>
        </collectionProp>
    </elementProp>
    <stringProp name="HTTPSampler.domain">api.example.com</stringProp>
    <stringProp name="HTTPSampler.port">443</stringProp>
    <stringProp name="HTTPSampler.protocol">https</stringProp>
    <stringProp name="HTTPSampler.path">/api/login</stringProp>
    <stringProp name="HTTPSampler.method">POST</stringProp>
    <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
    <boolProp name="HTTPSampler.auto_redirects">false</boolProp>
</HTTPSamplerProxy>
```

## 控制器

### 简单控制器

```xml
<!-- 简单控制器 - 分组请求 -->
<hashTree>
    <GenericController guiclass="ControllerGui" testclass="GenericController" testname="Login Flow"/>
    <hashTree>
        <HTTPSamplerProxy ...>
            <!-- 登录请求 -->
        </HTTPSamplerProxy>
        <HTTPSamplerProxy ...>
            <!-- 获取用户信息 -->
        </HTTPSamplerProxy>
    </hashTree>
</hashTree>
```

### 随机控制器

```xml
<!-- 随机控制器 - 随机选择执行 -->
<RandomController guiclass="RecordingControllerGui" testclass="RandomController" testname="Random Flow">
    <intProp name="Controller.num_threads">10</intProp>
    <hashTree>
        <!-- 选项 1: 浏览产品 (70%) -->
        <HTTPSamplerProxy .../>
        <!-- 选项 2: 搜索产品 (20%) -->
        <HTTPSamplerProxy .../>
        <!-- 选项 3: 查看购物车 (10%) -->
        <HTTPSamplerProxy .../>
    </hashTree>
</RandomController>
```

### 循环控制器

```xml
<!-- 循环控制器 - 重复执行 -->
<LoopController guiclass="LoopControllerGui" testclass="LoopController" testname="Browse Products">
    <boolProp name="LoopController.continue_forever">false</boolProp>
    <stringProp name="LoopController.loops">5</stringProp>
    <hashTree>
        <!-- 商品列表请求 -->
        <HTTPSamplerProxy .../>
        <!-- 随机浏览 5 个商品 -->
    </hashTree>
</LoopController>
```

## 定时器

### 恒定定时器

```xml
<!-- 每个请求之间等待 1 秒 -->
<ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Constant Timer">
    <stringProp name="ConstantTimer.delay">1000</stringProp>
</ConstantTimer>
```

### 高斯随机定时器

```xml
<!-- 高斯随机定时器 - 模拟真实用户行为 -->
<GaussianRandomTimer guiclass="GaussianRandomTimerGui" testclass="GaussianRandomTimer" testname="Gaussian Random Timer">
    <stringProp name="ConstantTimer.delay">1000</stringProp>
    <stringProp name="GaussianRandomTimer.range">500</stringProp>
</GaussianRandomTimer>
```

### 吞吐量定时器

```xml
<!-- 吞吐量定时器 - 控制每秒请求数 -->
<ThroughputTimers>
    <ConstantThroughputTimer guiclass="ConstantThroughputTimerGui" testclass="ConstantThroughputTimer" testname="Target Throughput">
        <intProp name="throughput">100</intProp>
    </ConstantThroughputTimer>
</ThroughputTimers>
```

## 断言

### 响应断言

```xml
<!-- 验证响应包含特定文本 -->
<ResponseAssertion guiclass="ResponseAssertionGui" testclass="ResponseAssertion" testname="Response Contains Token">
    <collectionProp name="Asserion.test_strings">
        <stringProp name="Token">access_token</stringProp>
    </collectionProp>
    <boolProp name="ResponseAssertion.assume_success">false</boolProp>
    <intProp name="ResponseAssertion.test_type">2</intProp>
</ResponseAssertion>
```

### JSON 路径断言

```xml
<!-- 验证 JSON 响应中的值 -->
<JSONPathAssertion guiclass="JSONPathAssertionGui" testclass="JSONPathAssertion" testname="JSON Path Assertion">
    <stringProp name="JSON_PATH">$.data.user.id</stringProp>
    <stringProp name="EXPECTED_VALUE">\d+</stringProp>
    <boolProp name="JSONVALIDATION">true</boolProp>
    <boolProp name="EXPECT_NULL">false</boolProp>
</JSONPathAssertion>
```

### 响应时间断言

```xml
<!-- 验证响应时间小于阈值 -->
<DurationAssertion guiclass="DurationAssertionGui" testclass="DurationAssertion" testname="Response Time < 2s">
    <stringProp name="DurationAssertion.duration">2000</stringProp>
</DurationAssertion>
```

## 后置处理器

### JSON 提取器

```xml
<!-- 从响应中提取值供后续请求使用 -->
<JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="Extract Token">
    <stringProp name="JSONPostProcessor.JSONPostProcessor">$.token</stringProp>
    <stringProp name="JSONPostProcessor.Match_Number">1</stringProp>
    <stringProp name="JSONPostProcessor.VariableNames">auth_token</stringProp>
</JSONPostProcessor>
```

### 正则表达式提取器

```xml
<!-- 使用正则表达式提取值 -->
<RegexPostProcessor guiclass="RegexPostProcessorGui" testclass="RegexPostProcessor" testname="Extract Session ID">
    <stringProp name="RegexPostProcessor.regex">sessionId=([^&amp;]+)</stringProp>
    <stringProp name="RegexPostProcessor.match_number">1</stringProp>
    <stringProp name="RegexPostProcessor.variableName">session_id</stringProp>
</RegexPostProcessor>
```

## CSV 数据文件配置

```xml
<!-- CSV 数据文件配置 -->
<CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="User Credentials">
    <stringProp name="delimiter">,</stringProp>
    <stringProp name="fileEncoding">UTF-8</stringProp>
    <stringProp name="filename">/path/to/users.csv</stringProp>
    <boolProp name="ignoreFirstLine">true</boolProp>
    <boolProp name="quotedData">false</boolProp>
    <boolProp name="recycle">true</boolProp>
    <stringProp name="shareMode">shareMode.all</stringProp>
    <boolProp name="stopThread">false</boolProp>
    <stringProp name="variableNames">email,password</stringProp>
</CSVDataSet>
```

```csv
# users.csv
email,password
user1@example.com,Password123!
user2@example.com,Password123!
user3@example.com,Password123!
```

## 监听器

### 聚合报告

```xml
<!-- 聚合报告 - 查看测试结果汇总 -->
<AggregateReport guiclass="StatVisualizer" testclass="AggregateReport" testname="Aggregate Report">
    <boolProp name="saveResponseData">true</boolProp>
    <boolProp name="saveRequestHeaders">true</boolProp>
    <boolProp name="saveResponseHeaders">true</boolProp>
    <boolProp name="saveSamplerData">true</boolProp>
    <boolProp name="saveUrl">true</boolProp>
    <stringProp name="filename">/results/aggregate-report.csv</stringProp>
</AggregateReport>
```

### 响应时间图表

```xml
<!-- 响应时间图表 - 可视化性能趋势 -->
<ResponseTimeChart guiclass="ResponseTimeChartGui" testclass="ResponseTimeChart" testname="Response Time Graph">
    <stringProp name="ResponseTimeChart.yaxis.label">Response Time (ms)</stringProp>
    <stringProp name="ResponseTimeChart.title">Response Time Over Time</stringProp>
</ResponseTimeChart>
```

## JMeter 命令行运行

```bash
# 运行测试计划
jmeter -n -t /path/to/test-plan.jmx -l /results/results.jtl -e -o /results/html-report

# 参数说明
# -n: 非 GUI 模式
# -t: 测试计划文件
# -l: 结果文件
# -e: 生成 HTML 报告
# -o: HTML 报告输出目录

# 使用特定的配置文件
jmeter -n -t test-plan.jmx -p jmeter.properties -l results.jtl

# 分布式测试
jmeter -n -t test-plan.jmx -R server1,server2,server3 -l results.jtl

# 限制内存使用
jmeter -Xms512m -Xmx2g -n -t test-plan.jmx -l results.jtl
```

## 分布式负载测试

```bash
# 在 controller 机器上配置 slave 服务器
# 编辑 bin/jmeter.properties
remote_hosts=server1,server2,server3
server_port=1099

# 在 controller 机器上启动测试
jmeter -n -t test-plan.jmx -R server1,server2,server3 -l results.jtl
```

## HTML 报告生成

```bash
# 使用现有结果生成报告
jmeter -g /results/results.jtl -o /results/html-report

# 或在测试运行时直接生成
jmeter -n -t test-plan.jmx -l results.jtl -e -o html-report
```

## CI/CD 集成

```yaml
# Jenkinsfile
pipeline {
    agent any

    stages {
        stage('Load Test') {
            steps {
                sh '''
                    ./jmeter/bin/jmeter -n \
                        -t tests/load-test.jmx \
                        -l results/results.jtl \
                        -e -o results/html-report
                '''
            }
        }

        stage('Performance Check') {
            steps {
                sh '''
                    # 解析聚合报告
                    awk -F',' '
                        NR > 1 {
                            if ($4 > 1000) {  # 响应时间 > 1s
                                print "SLOW: " $3 " - Avg: " $4 "ms"
                            }
                        }
                    ' results/results.jtl
                '''
            }
        }
    }

    post {
        always {
            publishHTML([
                reportDir: 'results/html-report',
                reportFiles: 'index.html',
                reportName: 'Load Test Report'
            ])

            archiveArtifacts artifacts: 'results/*.jtl,results/html-report/**'
        }
    }
}
```

## 最佳实践

1. **使用合理的线程数** -- 根据服务器能力设置，避免过载。
2. **渐进式启动** -- 使用 ramp-up 让请求逐步增加。
3. **提取重复数据** -- 使用 CSV 数据集模拟多用户。
4. **正确的定时器** -- 模拟真实用户思考时间。
5. **验证响应** -- 使用断言确保请求成功。
6. **分离测试数据** -- 避免用户使用相同数据。
7. **监控资源** -- JMeter 本身也可能成为瓶颈。
8. **分析结果** -- 关注平均值、中位数、百分位数。

## 应避免的反模式

1. **使用过大的线程数** -- JMeter 本身会成为瓶颈。
2. **没有热身期** -- JIT 编译会影响初期结果。
3. **忽略 Think Time** -- 没有等待的测试不真实。
4. **单一请求测试** -- 应该测试真实用户流程。
5. **忽略错误率** -- 不仅是响应时间。
6. **不验证响应内容** -- 请求可能返回错误但状态码成功。
7. **测试数据冲突** -- 多用户使用相同数据。
8. **结果文件过大** -- 只保存必要的数据。