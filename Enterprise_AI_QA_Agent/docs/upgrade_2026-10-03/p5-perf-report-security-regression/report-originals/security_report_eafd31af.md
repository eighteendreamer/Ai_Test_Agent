# 安全测试报告 - http://127.0.0.1 (url)

**报告名称**：安全测试报告 - http://127.0.0.1 (url)
**生成日期**：2026-10-09
**生成时间**：22:57:02
**测试目标**：http://127.0.0.1 (url)
**执行耗时**：0 秒

## 执行摘要

本次安全测试完成 2 个任务，产生 4 个发现。 有 1 个任务执行失败，因此覆盖范围可能不完整。

## 测试结果

- 任务总数：3
- 已完成：2
- 失败：1
- 跳过：0
- 发现资产：0
- 发现服务：0
- 执行记录：3
- 证据产物：12
- 漏洞假设：0
- 验证尝试：0
- Security Bug：0
- 复测仍存在：0
- 复测已关闭：0

## 风险等级概览

| 风险等级 | 数量 |
|---|---:|
| 严重 | 0 |
| 高危 | 0 |
| 中危 | 0 |
| 低危 | 4 |
| 信息 | 0 |

## Agent 执行情况

执行策略：local_worker_fallback

| Agent | 任务 | 动作 | 执行模式 | 执行内容 |
|---|---|---|---|---|
| security-web-verifier | missing | completed | local_worker_fallback | Executed http_headers_probe with exit code 0; parsed 4 finding(s). |
| security-web-verifier | complete | completed | local_worker_fallback | Executed http_headers_probe with exit code 0; parsed 0 finding(s). |
| security-web-verifier | unreachable | failed | local_worker_fallback | Executed http_headers_probe with exit code 1; parsed 0 finding(s). |

## 发现、风险与错误

### 1. 缺少 X-Frame-Options 响应头

- **风险等级**：低危
- **类别**：missing_control
- **受影响目标**：http://host.docker.internal:58005/missing
- **验证等级**：confirmed
- **CVSS**: 0.0
- **CVSS Vector**: `CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:N/I:N/A:N`
- **CWE**: CWE-693, CWE-1021
- **OWASP**: OWASP-A05:2021-Security-Misconfiguration
- **置信度**：confirmed
- **是否验证可利用**：否

**描述**

HTTP 响应中缺少安全响应头: x-frame-options

**证据**

```text
HTTP 200 response from http://host.docker.internal:58005/missing omitted x-frame-options.
```

**复现方式**

1. Send an authorized GET request to http://host.docker.internal:58005/missing.
2. Confirm HTTP 200 does not include the x-frame-options response header.

**修复建议**

在服务器配置中添加 x-frame-options 响应头

### 2. 缺少 X-Content-Type-Options 响应头

- **风险等级**：低危
- **类别**：missing_control
- **受影响目标**：http://host.docker.internal:58005/missing
- **验证等级**：confirmed
- **CVSS**: 0.0
- **CVSS Vector**: `CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:N/I:N/A:N`
- **CWE**: CWE-693
- **OWASP**: OWASP-A05:2021-Security-Misconfiguration
- **置信度**：confirmed
- **是否验证可利用**：否

**描述**

HTTP 响应中缺少安全响应头: x-content-type-options

**证据**

```text
HTTP 200 response from http://host.docker.internal:58005/missing omitted x-content-type-options.
```

**复现方式**

1. Send an authorized GET request to http://host.docker.internal:58005/missing.
2. Confirm HTTP 200 does not include the x-content-type-options response header.

**修复建议**

在服务器配置中添加 x-content-type-options 响应头

### 3. 缺少 HSTS 响应头

- **风险等级**：低危
- **类别**：missing_control
- **受影响目标**：http://host.docker.internal:58005/missing
- **验证等级**：confirmed
- **CVSS**: 0.0
- **CVSS Vector**: `CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:N/I:N/A:N`
- **CWE**: CWE-693
- **OWASP**: OWASP-A05:2021-Security-Misconfiguration
- **置信度**：confirmed
- **是否验证可利用**：否

**描述**

HTTP 响应中缺少安全响应头: strict-transport-security

**证据**

```text
HTTP 200 response from http://host.docker.internal:58005/missing omitted strict-transport-security.
```

**复现方式**

1. Send an authorized GET request to http://host.docker.internal:58005/missing.
2. Confirm HTTP 200 does not include the strict-transport-security response header.

**修复建议**

在服务器配置中添加 strict-transport-security 响应头

### 4. 缺少 Content-Security-Policy 响应头

- **风险等级**：低危
- **类别**：missing_control
- **受影响目标**：http://host.docker.internal:58005/missing
- **验证等级**：confirmed
- **CVSS**: 0.0
- **CVSS Vector**: `CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:N/I:N/A:N`
- **CWE**: CWE-693
- **OWASP**: OWASP-A05:2021-Security-Misconfiguration
- **置信度**：confirmed
- **是否验证可利用**：否

**描述**

HTTP 响应中缺少安全响应头: content-security-policy

**证据**

```text
HTTP 200 response from http://host.docker.internal:58005/missing omitted content-security-policy.
```

**复现方式**

1. Send an authorized GET request to http://host.docker.internal:58005/missing.
2. Confirm HTTP 200 does not include the content-security-policy response header.

**修复建议**

在服务器配置中添加 content-security-policy 响应头

## 攻击链验证

本次运行没有证据支持的漏洞假设，因此未执行验证尝试。

## Exploit Coder 与影响验证

本次运行未请求或未执行 P2 Exploit Coder 工作区。

## 安全情报与假设来源

本次运行未摄取结构化安全情报。

## P3 回连 Broker 与安全图谱

本次运行未请求 P3 loopback callback lease。

## P4 工具装配审计

本次运行未请求 P4 临时工具装配。

## 持久隔离攻击会话

本次运行未请求 P1 持久隔离攻击会话。

## 可复现 Security Bug

本次运行没有满足证据与复现质量门的 Security Bug。

## 修复建议

1. 在服务器配置中添加 x-frame-options 响应头
2. 在服务器配置中添加 x-content-type-options 响应头
3. 在服务器配置中添加 strict-transport-security 响应头
4. 在服务器配置中添加 content-security-policy 响应头

## 测试限制

- 环境限制（environment_limited）：1 个任务未能从隔离执行环境获得目标响应；未根据空响应生成漏洞发现。
- 本次运行产生 Finding，但未形成漏洞假设；攻击链验证覆盖不完整。

---
由安全测试模式生成。